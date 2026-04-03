# app/routers/recipes.py
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
import re
import unicodedata

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Ingredient, Price, Product, Recipe, Store
from app.scrapers import ica, tasteline, willys

router = APIRouter(prefix="/recept")
templates = Jinja2Templates(directory="app/templates")


# ------------------------------------------------------------------ matching

ENHETER = {
    "dl", "ml", "cl", "l", "kg", "g", "msk", "tsk", "st", "krm",
    "liter", "nypa", "bit", "skiva", "förp", "port", "burk", "påse",
    "ask", "paket", "klyfta", "klyftorna", "knippe", "näve",
}

STOPPORD = {
    "stor", "stora", "liten", "litet", "små", "smått",
    "hackad", "hackade", "riven", "rivet", "rivna",
    "skivad", "skivade", "skivat",
    "fryst", "frysta", "färsk", "färska", "färskt",
    "kokt", "kokta", "kokte", "rökt", "rökta",
    "rimmat", "rimmad", "tärnad", "tärnade",
    "finhackad", "grovhackad", "pressad", "pressade",
    "mald", "malda", "delad", "delade",
    "ca", "till", "med", "och", "av", "à", "á", "i", "ur",
    "efter", "smak", "valfritt", "gärna",
    "eller", "torkad", "torkade",
}

# Mappar ingrediensnamn → söktermen som ger bäst träff i Willys-DB
SYNONYMER: dict[str, str] = {
    # Kött & fågel
    "kyckling":         "kyckling",
    "kycklingfilé":     "kyckling",
    "kycklingfiléer":   "kyckling",
    "kycklingfilé(er)": "kyckling",
    "kycklingbröst":    "kyckling",
    "kycklinglår":      "kycklinglår",
    "kycklingklubbor":  "kycklingklubbor",
    "nötfärs":          "nötfärs",
    "köttfärs":         "köttfärs",
    "fläskfilé":        "fläskfilé",
    "fläskkotlett":     "fläskkotlett",
    "fläsk":            "fläsk",
    "lammfärs":         "lammfärs",
    "lamm":             "lamm",
    "skinka":           "skinka",
    "bacon":            "bacon",
    "korv":             "korv",
    "falukorv":         "falukorv",
    "prosciutto":       "prosciutto",
    # Fisk & skaldjur
    "lax":              "lax",
    "laxfilé":          "lax",
    "torsk":            "torsk",
    "räkor":            "räkor",
    "tonfisk":          "tonfisk",
    # Mejeri & ägg
    "ägg":              "ägg",
    "mjölk":            "mjölk",
    "smör":             "smör",
    "grädde":           "grädde",
    "vispgrädde":       "vispgrädde",
    "matlagningsgrädde": "matlagningsgrädde",
    "gräddfil":         "gräddfil",
    "crème fraîche":    "creme fraiche",
    "creme fraiche":    "creme fraiche",
    "yoghurt":          "yoghurt",
    "kvarg":            "kvarg",
    "ost":              "ost",
    "parmesan":         "parmesan",
    "mozzarella":       "mozzarella",
    "fetaost":          "fetaost",
    "cream cheese":     "cream cheese",
    # Grönsaker
    "potatis":          "potatis",
    "sötpotatis":       "sötpotatis",
    "lök":              "lök",
    "gullök":           "lök",
    "rödlök":           "rödlök",
    "purjolök":         "purjolök",
    "salladslök":       "salladslök",
    "vitlök":           "vitlök",
    "tomat":            "tomat",
    "tomater":          "tomat",
    "körsbärstomater":  "körsbärstomater",
    "paprika":          "paprika",
    "morot":            "morot",
    "morötter":         "morot",
    "broccoli":         "broccoli",
    "blomkål":          "blomkål",
    "spenat":           "spenat",
    "zucchini":         "zucchini",
    "aubergine":        "aubergine",
    "champinjoner":     "champinjoner",
    "svamp":            "svamp",
    "selleri":          "selleri",
    "gurka":            "gurka",
    "sallad":           "sallad",
    "rucola":           "rucola",
    "vitkål":           "vitkål",
    "rödkål":           "rödkål",
    "kål":              "kål",
    "majs":             "majs",
    "ärtor":            "ärtor",
    "bönor":            "bönor",
    "kidneybönor":      "kidneybönor",
    "kikärtor":         "kikärtor",
    "linser":           "linser",
    "avokado":          "avokado",
    "lime":             "lime",
    "citron":           "citron",
    # Skafferi
    "vetemjöl":         "vetemjöl",
    "socker":           "socker",
    "salt":             "salt",
    "olivolja":         "olivolja",
    "rapsolja":         "rapsolja",
    "olja":             "olja",
    "pasta":            "pasta",
    "spaghetti":        "spaghetti",
    "penne":            "penne",
    "ris":              "ris",
    "nudlar":           "nudlar",
    "buljong":          "buljong",
    "hönsbuljong":      "buljong",
    "köttbuljong":      "buljong",
    "grönsakbuljong":   "buljong",
    "krossade tomater": "krossade tomater",
    "passerade tomater":"krossade tomater",
    "tomatpuré":        "tomatpuré",
    "tomatsås":         "tomatsås",
    "kokosmjölk":       "kokosmjölk",
    "sojasås":          "sojasås",
    "fisksås":          "fisksås",
    "fiskssås":         "fisksås",
    "currypasta":        "currypasta",
    "röd currypasta":   "currypasta",
    "grön currypasta":  "currypasta",
    "risnudlar":         "risnudlar",
    "wokgrönsaker":     "wokgrönsaker",
    "wokgronsaker":      "wokgrönsaker",
    "senap":            "senap",
    "majonnäs":         "majonnäs",
    "ketchup":          "ketchup",
    "honung":           "honung",
    "vinäger":          "vinäger",
    "balsamvinäger":    "balsamvinäger",
    # Kryddor
    "peppar":           "peppar",
    "svartpeppar":      "peppar",
    "paprikapulver":    "paprikapulver",
    "oregano":          "oregano",
    "basilika":         "basilika",
    "timjan":           "timjan",
    "rosmarin":         "rosmarin",
    "kanel":            "kanel",
    "curry":            "curry",
    "ingefära":         "ingefära",
    "spiskummin":       "spiskummin",
    "kummin":           "kummin",
    "koriander":        "koriander",
    "chili":            "chili",
    "chilisås":         "chilisås",
    "persilja":         "persilja",
    "dill":             "dill",
    "lingon":           "lingon",
}

# Svenska sammansatta ord — prova prefixet om hela ordet ej matchar
_COMPOUND_SUFFIXES = [
    "filé", "bröst", "lår", "kött", "färs", "kotlett",
    "sås", "puré", "mjölk", "olja", "pulver", "lök",
    "buljong", "spad", "sky",
]


def _clean_ingredient_name(name: str) -> str:
    """Rensa ingrediensnamnet från mängder, enheter och stoppord."""
    name = unicodedata.normalize("NFKC", name)
    name = name.replace(" ", " ")
    name = re.sub(r'\s*\([^)]*\)', '', name)   # ta bort parenteser
    name = name.replace("(er)", " ").replace("er)", " ")
    name = name.split(',')[0]                   # ta bort allt efter komma
    name = re.sub(r'\b\d+[\d.,/]*\b', '', name) # ta bort tal
    name = re.sub(r"[-/]", " ", name)
    name = re.sub(r"[^a-zA-ZåäöÉèêüÅÄÖ\s]", " ", name)
    words = [
        w for w in name.lower().split()
        if w not in ENHETER and w not in STOPPORD and len(w) > 1
    ]
    return " ".join(words).strip()


def _deaccent(s: str) -> str:
    """Bygg en enklare variant utan diakritiska tecken."""
    return (
        s.replace("å", "a").replace("ä", "a").replace("ö", "o")
        .replace("Å", "A").replace("Ä", "A").replace("Ö", "O")
        .replace("é", "e").replace("è", "e").replace("ê", "e")
        .replace("ü", "u")
    )


def _get_search_term(clean_name: str) -> str:
    """Slå upp synonymer — returnera bästa söktermen."""
    # Prova hela frasen först
    if clean_name in SYNONYMER:
        return SYNONYMER[clean_name]
    # Prova varje enskilt ord
    for word in clean_name.split():
        if word in SYNONYMER:
            return SYNONYMER[word]
    return clean_name



def _candidate_terms(term: str) -> list[str]:
    """Bygg söktermer med diakritik-variant och unika tokens."""
    terms = [term]
    deaccented = _deaccent(term)
    if deaccented != term:
        terms.append(deaccented)
    return list(dict.fromkeys([t for t in terms if t]))

def _compound_variants(word: str) -> list[str]:
    """Bryt upp sammansatta ord, t.ex. 'kycklingfilé' → ['kyckling', 'filé']."""
    for suffix in _COMPOUND_SUFFIXES:
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return [word[: -len(suffix)], suffix]
    return []


def _query_products(db: Session, term: str, limit: int = 3) -> list[Product]:
    return (
        db.query(Product)
        .filter(Product.name.ilike(f"%{term}%"))
        .limit(limit)
        .all()
    )


def _get_or_create_store(db: Session, name: str, url: str = "") -> Store:
    store = db.query(Store).filter_by(name=name).first()
    if not store:
        store = Store(name=name, url=url)
        db.add(store)
        db.commit()
        db.refresh(store)
    return store


def _upsert_product(db: Session, data: dict, store: Store) -> Product:
    product = (
        db.query(Product)
        .filter_by(external_id=data["external_id"], store_id=store.id)
        .first()
    )
    if not product:
        product = Product(
            external_id=data["external_id"],
            name=data["name"],
            brand=data.get("brand", ""),
            unit=data.get("unit", ""),
            image_url=data.get("image_url", ""),
            store_id=store.id,
        )
        db.add(product)
        db.commit()
        db.refresh(product)

    price = Price(
        product_id=product.id,
        price=data["price"],
        original_price=data.get("original_price"),
        is_offer=data.get("is_offer", False),
        offer_label=data.get("offer_label", ""),
        scraped_at=datetime.utcnow(),
    )
    db.add(price)
    db.commit()
    return product


def _maybe_fetch_products(db: Session, term: str) -> None:
    if not term or len(term) < 3:
        return
    try:
        store = _get_or_create_store(db, "Willys", "https://www.willys.se")
        for item in willys.search_products(term, size=30):
            if item["external_id"] and item["name"] and item["price"] > 0:
                _upsert_product(db, item, store)
    except Exception:
        pass
    try:
        # ICA använder Playwright (synkron) som inte kan köras direkt i asyncio-loopen.
        # Kör i separat tråd för att undvika "Sync API inside asyncio loop"-fel.
        with ThreadPoolExecutor(max_workers=1) as ex:
            ica_items = ex.submit(ica.search_products, term, 30).result(timeout=60)
        ica_store = _get_or_create_store(db, "ICA", "https://handlaprivatkund.ica.se")
        for item in ica_items:
            if item["external_id"] and item["name"] and item["price"] > 0:
                _upsert_product(db, item, ica_store)
    except FuturesTimeout:
        pass
    except Exception:
        pass


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
    """Matcha ingredienser mot produkter med progressiv fallback."""
    matches: dict[int, list[Product]] = {}

    for ing in ingredients:
        clean = _clean_ingredient_name(ing.name)
        if not clean:
            continue

        search = _get_search_term(clean)
        products: list[Product] = []

        # 1. Hela söktermen
        for term in _candidate_terms(search):
            products = _query_products(db, term)
            if products:
                break
        # 1b. Saknas i DB — hämta från Willys och testa igen
        if not products:
            _maybe_fetch_products(db, search)
            for term in _candidate_terms(search):
                products = _query_products(db, term)
                if products:
                    break

        # 2. Varje ord för sig (längsta ordet först — troligen mest specifikt)
        if not products:
            for word in sorted(search.split(), key=len, reverse=True):
                if len(word) > 2:
                    for term in _candidate_terms(word):
                        products = _query_products(db, term)
                        if products:
                            break
                if products:
                    break

        # 3. Sammansatta ordvarianter (t.ex. "kycklingfilé" → "kyckling")
        if not products:
            for variant in _compound_variants(search.split()[0] if search else ""):
                if len(variant) > 2:
                    products = _query_products(db, variant)
                    if products:
                        break

        if products:
            matches[ing.id] = products

    return matches


def _build_shopping_list(
    ingredients: list[Ingredient],
    matches: dict[int, list[Product]],
) -> list[dict]:
    """Bygg inköpslista med billigaste matchade produkten per ingrediens och butik."""
    items = []
    for ing in ingredients:
        prods = matches.get(ing.id, [])
        by_store: dict[str, dict] = {}

        for p in prods:
            if not p.prices:
                continue
            store_name = p.store.name if p.store else "Okänd"
            price = p.prices[0].price
            if store_name not in by_store or price < by_store[store_name]["price"]:
                by_store[store_name] = {"product": p, "price": price}

        # Billigast totalt
        best = None
        best_price = None
        for sd in by_store.values():
            if best_price is None or sd["price"] < best_price:
                best_price = sd["price"]
                best = sd["product"]

        items.append({
            "ingredient": ing,
            "product": best,
            "price": best_price,
            "all_matches": prods,
            "by_store": by_store,
        })
    return items


def _save_recipe(db: Session, data: dict) -> Recipe:
    """Spara recept + ingredienser i DB (uppdatera om det redan finns)."""
    recipe = db.query(Recipe).filter_by(external_id=data.get("external_id")).first()

    if recipe:
        recipe.name = data.get("name") or recipe.name
        recipe.description = data.get("description", "") or ""
        recipe.source_url = data.get("source_url", "") or ""
        recipe.image_url = data.get("image_url", "") or ""
        recipe.servings = data.get("servings") or recipe.servings
        recipe.time_minutes = data.get("time_minutes")

        # Ersatt ingredienslista helt för att undvika dubbletter
        db.query(Ingredient).filter_by(recipe_id=recipe.id).delete()
    else:
        recipe = Recipe(
            external_id=data.get("external_id", ""),
            name=data.get("name", ""),
            description=data.get("description", "") or "",
            source_url=data.get("source_url", "") or "",
            image_url=data.get("image_url", "") or "",
            servings=data.get("servings") or 4,
            time_minutes=data.get("time_minutes"),
        )
        db.add(recipe)
        db.flush()  # behövs för recipe.id innan ingredienser läggs till

    for ing in data.get("ingredients", []):
        name = (ing.get("name") or "").strip()
        if not name:
            continue
        db.add(
            Ingredient(
                recipe_id=recipe.id,
                name=name,
                amount=ing.get("amount"),
                unit=ing.get("unit", "") or "",
            )
        )

    db.commit()
    db.refresh(recipe)
    return recipe


# ------------------------------------------------------------------ routes

@router.get("", response_class=HTMLResponse)
async def recipes_index(request: Request, q: str = "", db: Session = Depends(get_db)):
    results = []
    error = ""

    if q:
        # Kolla DB först
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
    """Hämta ett recept från Tasteline-URL och spara i DB."""
    error = ""
    recipe = None
    shopping_list = []

    try:
        data = tasteline.get_recipe(url)
        if data:
            recipe = _save_recipe(db, data)
        else:
            error = "Kunde inte hämta receptet."
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











