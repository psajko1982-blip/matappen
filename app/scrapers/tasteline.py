# app/scrapers/tasteline.py
"""Scraper för Tasteline.com — hämtar recept via JSON-LD och HTML-parsing."""
from __future__ import annotations

import json
import logging
import re
import time

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.tasteline.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "sv-SE,sv;q=0.9",
}


def _get_html(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        logger.error(f"Tasteline GET fel {url}: {e}")
        return None


def _extract_jsonld(soup: BeautifulSoup, schema_type: str) -> dict | None:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            if isinstance(data, list):
                for item in data:
                    if item.get("@type") == schema_type:
                        return item
            elif data.get("@type") == schema_type:
                return data
        except Exception:
            continue
    return None


def search_recipes(query: str, max_results: int = 12) -> list[dict]:
    """Sök recept på Tasteline och returnera en lista med grundinfo."""
    slug = query.lower().replace(" ", "-").replace("å", "a").replace("ä", "a").replace("ö", "o")
    url = f"{BASE_URL}/recept/{slug}/"
    soup = _get_html(url)
    if not soup:
        return []

    # Försök hitta JSON-LD ItemList
    itemlist = _extract_jsonld(soup, "ItemList")
    recipes = []

    if itemlist:
        for item in itemlist.get("itemListElement", [])[:max_results]:
            recipe_url = item.get("url", "")
            name = item.get("name", "")
            if recipe_url and name:
                recipes.append({"name": name, "url": recipe_url, "image_url": ""})
        return recipes

    # Fallback: hämta receptkort från HTML
    for card in soup.select("article.recipe-card, .recipe-item, [data-recipe-id]")[:max_results]:
        link = card.find("a", href=True)
        title = card.find(["h2", "h3", "h4"])
        img = card.find("img")
        if link and title:
            recipes.append({
                "name": title.get_text(strip=True),
                "url": BASE_URL + link["href"] if link["href"].startswith("/") else link["href"],
                "image_url": img.get("src", "") if img else "",
            })

    return recipes


def get_recipe(url: str) -> dict | None:
    """
    Hämta ett recept från en Tasteline-URL.
    Returnerar ett normaliserat dict med namn, ingredienser, tid etc.
    """
    soup = _get_html(url)
    if not soup:
        return None

    ld = _extract_jsonld(soup, "Recipe")

    # Hämta ingredienser med mängder från HTML
    ingredients = _parse_ingredients(soup)

    if not ingredients and ld:
        # Fallback: använd JSON-LD ingredienslista (utan mängder)
        ingredients = [
            {"name": ing, "amount": None, "unit": ""}
            for ing in ld.get("recipeIngredient", [])
        ]

    if not ld and not ingredients:
        return None

    # Parsa tillagningstid
    time_minutes = None
    if ld:
        raw_time = ld.get("totalTime") or ld.get("cookTime") or ""
        m = re.search(r"PT(\d+)M", raw_time)
        if m:
            time_minutes = int(m.group(1))

    # Parsa portioner
    servings = 4
    if ld:
        yield_str = str(ld.get("recipeYield", "") or "")
        m = re.search(r"\d+", yield_str)
        if m:
            servings = int(m.group())

    return {
        "external_id": url.rstrip("/").split("/")[-1],
        "name": (ld or {}).get("name") or _fallback_title(soup),
        "description": (ld or {}).get("description", ""),
        "source_url": url,
        "image_url": (ld or {}).get("image", ""),
        "servings": servings,
        "time_minutes": time_minutes,
        "ingredients": ingredients,
    }


def _parse_ingredients(soup: BeautifulSoup) -> list[dict]:
    """Parsa ingredienser med mängder från HTML."""
    ingredients = []

    # Tasteline-specifika selektorer
    selectors = [
        "li.ingredient",
        ".ingredients li",
        ".recipe-ingredients li",
        "[class*='ingredient'] li",
        ".ingr li",
    ]

    items = []
    for sel in selectors:
        items = soup.select(sel)
        if items:
            break

    for item in items:
        text = item.get_text(separator=" ", strip=True)
        if not text or len(text) < 2:
            continue

        # Försök matcha "250 g smör" → amount=250, unit="g", name="smör"
        m = re.match(
            r"^([\d,./½¼¾]+)\s*"
            r"(msk|tsk|dl|cl|ml|l|kg|g|st|krm|nypa|burk|paket|förp|bit|skiva|skivor)?\s*(.+)$",
            text,
            re.IGNORECASE,
        )
        if m:
            raw_amount = m.group(1).replace(",", ".").replace("½", "0.5").replace("¼", "0.25")
            try:
                amount = float(raw_amount)
            except Exception:
                amount = None
            ingredients.append({
                "name": m.group(3).strip(),
                "amount": amount,
                "unit": (m.group(2) or "").lower(),
            })
        else:
            ingredients.append({"name": text, "amount": None, "unit": ""})

    return ingredients


def _fallback_title(soup: BeautifulSoup) -> str:
    tag = soup.find("h1")
    return tag.get_text(strip=True) if tag else "Okänt recept"


def get_youtube_search_url(recipe_name: str) -> str:
    """Returnera en YouTube-sök-URL för receptet."""
    query = recipe_name.replace(" ", "+")
    return f"https://www.youtube.com/results?search_query={query}+recept"
