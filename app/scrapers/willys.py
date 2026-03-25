# app/scrapers/willys.py
"""Scraper för Willys.se — använder Willys interna JSON-API."""
from __future__ import annotations

import logging
import time
from typing import Generator

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.willys.se"
SEARCH_URL = f"{BASE_URL}/search.json"
OFFERS_URL = f"{BASE_URL}/c/erbjudanden.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*",
    "Accept-Language": "sv-SE,sv;q=0.9",
    "Referer": "https://www.willys.se/",
}

# Willys butiks-ID för Stockholm (kan konfigureras)
DEFAULT_STORE_ID = "1631"


def _get(url: str, params: dict) -> dict | None:
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Willys GET fel {url}: {e}")
        return None


def search_products(query: str, page: int = 0, size: int = 30) -> list[dict]:
    """Sök produkter på Willys och returnera normaliserade dicts."""
    params = {
        "q": query,
        "page": page,
        "size": size,
        "store": DEFAULT_STORE_ID,
    }
    data = _get(SEARCH_URL, params)
    if not data:
        return []
    results = data.get("results", {})
    if isinstance(results, dict):
        products = results.get("products", {}).get("results", [])
    else:
        products = []
    return [_normalize(p) for p in products if p]


def get_offers(page: int = 0, size: int = 60) -> list[dict]:
    """Hämta veckans erbjudanden från Willys."""
    params = {
        "page": page,
        "size": size,
        "store": DEFAULT_STORE_ID,
        "type": "OFFER",
    }
    data = _get(OFFERS_URL, params)
    if not data:
        return []
    products = data.get("results", {}).get("products", {}).get("results", [])
    return [_normalize(p, is_offer=True) for p in products if p]


def get_all_offers() -> Generator[dict, None, None]:
    """Hämta alla sidor med erbjudanden."""
    page = 0
    while True:
        items = get_offers(page=page, size=60)
        if not items:
            break
        yield from items
        page += 1
        time.sleep(0.5)  # vara snäll mot servern


def _normalize(raw: dict, is_offer: bool = False) -> dict:
    """Normalisera en råprodukt från Willys API till ett enhetligt format."""
    price_value = raw.get("price", {})
    if isinstance(price_value, dict):
        price = float(price_value.get("value", 0) or 0)
        original = float(price_value.get("originalPrice", {}).get("value", 0) or 0)
    else:
        price = float(price_value or 0)
        original = 0.0

    promo = raw.get("potentialPromotions", [])
    offer_label = ""
    if promo and isinstance(promo, list):
        offer_label = promo[0].get("description", {}).get("sv", "") if promo else ""
        is_offer = is_offer or bool(promo)

    images = raw.get("images", []) or []
    image_url = ""
    if images:
        img = images[0]
        if isinstance(img, dict):
            image_url = img.get("url", "")
        elif isinstance(img, str):
            image_url = img
    if image_url and not image_url.startswith("http"):
        image_url = BASE_URL + image_url

    return {
        "external_id": str(raw.get("code", "") or ""),
        "name": raw.get("name", ""),
        "brand": raw.get("manufacturer", ""),
        "unit": raw.get("compareUnit", "") or raw.get("displayVolume", ""),
        "image_url": image_url,
        "price": price,
        "original_price": original if original > price else None,
        "is_offer": is_offer,
        "offer_label": offer_label,
        "store": "Willys",
    }
