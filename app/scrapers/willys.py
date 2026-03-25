# app/scrapers/willys.py
"""Scraper för Willys.se — använder Willys interna JSON-API."""
from __future__ import annotations

import logging
import time
from typing import Generator

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.willys.se"
SEARCH_URL = f"{BASE_URL}/search"
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

# Kategorier med erbjudanden att skrapa
OFFER_CATEGORIES = [
    "mejeri-ost-och-agg",
    "kott-chark-och-fagel",
    "frukt-och-gront",
    "brod-och-kakor",
    "fryst",
    "fisk-och-skaldjur",
    "skafferi",
    "dryck",
]


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
    params = {"q": query, "page": page, "size": size}
    data = _get(SEARCH_URL, params)
    if not data:
        return []
    products = data.get("results") or []
    return [_normalize(p) for p in products if p]


def get_offers_from_category(category: str, page: int = 0, size: int = 60) -> list[dict]:
    """Hämta produkter med erbjudanden från en kategori."""
    url = f"{BASE_URL}/c/{category}"
    params = {"page": page, "size": size}
    data = _get(url, params)
    if not data:
        return []
    products = data.get("results") or []
    # Returnera bara produkter med aktiva erbjudanden
    return [
        _normalize(p, is_offer=True)
        for p in products
        if p and p.get("potentialPromotions")
    ]


def get_all_offers() -> Generator[dict, None, None]:
    """Hämta erbjudanden från alla kategorier."""
    for category in OFFER_CATEGORIES:
        page = 0
        while True:
            items = get_offers_from_category(category, page=page)
            if not items:
                break
            yield from items
            page += 1
            time.sleep(0.5)


def _normalize(raw: dict, is_offer: bool = False) -> dict:
    """Normalisera en råprodukt från Willys API."""
    price = float(raw.get("priceValue") or 0)

    # Kolla erbjudanden
    promos = raw.get("potentialPromotions") or []
    offer_label = ""
    original_price = None

    if promos:
        is_offer = True
        promo = promos[0]
        offer_label = promo.get("conditionLabel", "") or promo.get("redeemLimitLabel", "")
        promo_price = promo.get("price", {})
        if isinstance(promo_price, dict):
            promo_val = float(promo_price.get("value") or 0)
            if promo_val > 0 and promo_val < price:
                original_price = price
                price = promo_val

    savings = float(raw.get("savingsAmount") or 0)
    if savings > 0 and not original_price:
        original_price = price + savings

    image = raw.get("image") or raw.get("thumbnail") or {}
    image_url = ""
    if isinstance(image, dict):
        image_url = image.get("url", "")
    if image_url and not image_url.startswith("http"):
        image_url = BASE_URL + image_url

    return {
        "external_id": str(raw.get("code", "") or ""),
        "name": raw.get("name", ""),
        "brand": raw.get("manufacturer", ""),
        "unit": raw.get("displayVolume", "") or raw.get("comparePriceUnit", ""),
        "image_url": image_url,
        "price": price,
        "original_price": original_price,
        "is_offer": is_offer,
        "offer_label": offer_label,
        "store": "Willys",
    }
