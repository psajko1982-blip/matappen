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


ENHETER = {"dl", "ml", "cl", "l", "kg", "g", "msk", "tsk", 
           "st", "krm", "liter", "nypa", "bit", "skiva", "förp"}

STOPPORD = {"stor", "stora", "liten", "små", "hackad", "hackade",
            "riven", "rivet", "skivad", "fryst", "färsk", "färska",
            "ca", "till", "med", "och", "av", "à", "á", "finhackad",
            "grovhackad", "pressad", "pressade", "mald", "delad",
            "rimmat", "rimmad", "kokt", "kokte", "rökt", "tärnad"}

# Synonymer – mappar ingrediens → sökterm
SYNONYMER = {
    "ägg":          "ägg",
    "mjölk":        "mjölk",
    "smör":         "smör",
    "potatis":      "potatis",
    "lök":          "lök",
    "vitlök":       "vitlök",
    "vetemjöl":     "vetemjöl",
    "socker":       "socker",
    "salt":         "salt",
    "lingon":       "lingon",
    "fläsk":        "fläsk",
    "kyckling":     "kycklingfilé",
    "nötfärs":      "nötfärs",
}

def _clean_ingredient_name(name: str) -> str:
    # Ta bort parenteser
    name = re.sub(r'\s*\([^)]*\)', '', name)
    # Ta bort allt efter komma
    name = name.split(',')[0]
    # Ta bort siffror (t.ex. "1.5", "3")
    name = re.sub(r'\b\d+[\d.,]*\b', '', name)
    
    # Filtrera bort enheter och stoppord
    words = [w for w in name.lower().split() 
             if w not in ENHETER and w not in STOPPORD]
    
    return " ".join(words).strip()


def _get_search_term(clean_name: str) -> str:
    """Kolla synonymer – returnera bästa söktermen."""
    words = clean_name.lower().split()
    # Kolla om något ord finns i synonymlistan
    for word in words:
        if word in SYNONYMER:
            return SYNONYMER[word]
    return clean_name


def _match_products(db: Session, ingredients: list[Ingredient]) -> dict[int, list[Product]]:
    matches = {}
    for ing in ingredients:
        clean = _clean_ingredient_name(ing.name)
        if not clean:
            continue
            
        search = _get_search_term(clean)
        words = search.split()

        # Försök 1: hela söktermen
        products = (
            db.query(Product)
            .filter(Product.name.ilike(f"%{search}%"))
            .limit(3)
            .all()
        )

        # Försök 2: första ordet
        if not products:
            products = (
                db.query(Product)
                .filter(Product.name.ilike(f"%{words[0]}%"))
                .limit(3)
                .all()
            )

        # Försök 3: sista ordet
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

        # Visa produkten aven om den saknar pris
        if prods and best is None:
            best = prods[0]

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
