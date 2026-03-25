# app/routers/recipes.py
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Ingredient, Price, Product, Recipe
from app.scrapers import tasteline

router = APIRouter(prefix="/recept")
templates = Jinja2Templates(directory="app/templates")


# ------------------------------------------------------------------ helpers

def _save_recipe(db: Session, data: dict) -> Recipe:
    recipe = db.query(Recipe).filter_by(external_id=data["external_id"]).first()
    if not recipe:
        recipe = Recipe(
            external_id=data["external_id"],
            name=data["name"],
            description=data.get("description", ""),
            source_url=data["source_url"],
            image_url=data.get("image_url", ""),
            servings=data.get("servings", 4),
            time_minutes=data.get("time_minutes"),
            youtube_url=tasteline.get_youtube_search_url(data["name"]),
        )
        db.add(recipe)
        db.flush()

        for ing in data.get("ingredients", []):
            db.add(Ingredient(
                recipe_id=recipe.id,
                name=ing["name"],
                amount=ing.get("amount"),
                unit=ing.get("unit", ""),
            ))
        db.commit()
        db.refresh(recipe)
    return recipe


def _clean_ingredient_name(name: str) -> str:
    """Rensa ingrediensnamn: ta bort parenteser, komma-tillägg och specialtecken."""
    # Ta bort parentetiskt innehåll: "(ar)", "(or)", "(10-15 beroende på storlek)" etc.
    name = re.sub(r'\s*\([^)]*\)', '', name)
    # Ta bort allt efter komma: ", pressade", ", vege" etc.
    name = name.split(',')[0]
    # Ta bort siffror och enheter i början som kan ha blivit kvar
    name = re.sub(r'^\d+[\d.,]*\s*(msk|tsk|dl|cl|ml|l|kg|g|st|krm)?\s*', '', name, flags=re.IGNORECASE)
    return name.strip()


def _match_products(db: Session, ingredients: list[Ingredient]) -> dict[int, list[Product]]:
    """Matcha ingredienser mot produkter i DB. Returnerar {ingredient_id: [products]}."""
    matches = {}
    for ing in ingredients:
        clean = _clean_ingredient_name(ing.name)
        words = clean.split()
        if not words:
            continue

        # Forsok 1: forsta 2 ord
        query = " ".join(words[:2])
        products = (
            db.query(Product)
            .filter(Product.name.ilike(f"%{query}%"))
            .limit(3)
            .all()
        )

        # Forsok 2: om inget traff, prova forsta ordet ensamt
        if not products:
            products = (
                db.query(Product)
                .filter(Product.name.ilike(f"%{words[0]}%"))
                .limit(3)
                .all()
            )

        # Forsok 3: om fortfarande inget och flera ord, prova sista ordet
        if not products and len(words) >= 2:
            products = (
                db.query(Product)
                .filter(Product.name.ilike(f"%{words[-1]}%"))
                .limit(3)
                .all()
            )

        if products:
            matches[ing.id] = products
    return matches


def _build_shopping_list(
    ingredients: list[Ingredient],
    matches: dict[int, list[Product]],
) -> list[dict]:
    """Bygg inkopslista med billigaste matchade produkten per ingrediens."""
    items = []
    for ing in ingredients:
        prods = matches.get(ing.id, [])
        best = None
        best_price = None

        for p in prods:
            if p.prices:
                latest = p.prices[0]
                if best_price is None or latest.price < best_price:
                    best_price = latest.price
                    best = p

        items.append({
            "ingredient": ing,
            "product": best,
            "price": best_price,
            "all_matches": prods,
        })
    return items


# ------------------------------------------------------------------ routes

@router.get("", response_class=HTMLResponse)
async def recipes_index(request: Request, q: str = "", db: Session = Depends(get_db)):
    results = []
    error = ""

    if q:
        db_results = db.query(Recipe).filter(Recipe.name.ilike(f"%{q}%")).limit(12).all()
        if db_results:
            results = [{"name": r.name, "url": f"/recept/{r.id}", "image_url": r.image_url, "db_id": r.id} for r in db_results]
        else:
            try:
                results = tasteline.search_recipes(q, max_results=12)
            except Exception as e:
                error = f"Kunde inte söka recept: {e}"

    return templates.TemplateResponse(
        "recipes.html",
        {"request": request, "query": q, "results": results, "error": error},
    )


@router.get("/hamta", response_class=HTMLResponse)
async def fetch_and_show(request: Request, url: str, db: Session = Depends(get_db)):
    """Hamta ett recept fran Tasteline-URL och spara i DB."""
    error = ""
    recipe = None
    shopping_list = []

    try:
        data = tasteline.get_recipe(url)
        if data:
            recipe = _save_recipe(db, data)
        else:
            error = "Kunde inte hamta receptet."
    except Exception as e:
        error = str(e)

    if recipe:
        matches = _match_products(db, recipe.ingredients)
        shopping_list = _build_shopping_list(recipe.ingredients, matches)

    return templates.TemplateResponse(
        "recipe_detail.html",
        {
            "request": request,
            "recipe": recipe,
            "shopping_list": shopping_list,
            "error": error,
            "youtube_url": tasteline.get_youtube_search_url(recipe.name) if recipe else "",
        },
    )


@router.get("/{recipe_id}", response_class=HTMLResponse)
async def recipe_detail(request: Request, recipe_id: int, db: Session = Depends(get_db)):
    recipe = db.query(Recipe).filter_by(id=recipe_id).first()
    if not recipe:
        return templates.TemplateResponse(
            "recipes.html",
            {"request": request, "query": "", "results": [], "error": "Receptet hittades inte."},
        )
    matches = _match_products(db, recipe.ingredients)
    shopping_list = _build_shopping_list(recipe.ingredients, matches)

    return templates.TemplateResponse(
        "recipe_detail.html",
        {
            "request": request,
            "recipe": recipe,
            "shopping_list": shopping_list,
            "error": "",
            "youtube_url": recipe.youtube_url or tasteline.get_youtube_search_url(recipe.name),
        },
    )
