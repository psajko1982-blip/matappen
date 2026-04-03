# app/scrapers/ica.py
"""Scraper för ICA Handla — använder handlaprivatkund.ica.se JSON-API."""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

# Butiks-ID kan sättas via miljövariabeln ICA_STORE_ID.
# Standard: ICA Maxi Linköping (1004493) — byt till närmaste butik.
ICA_STORE_ID = os.getenv("ICA_STORE_ID", "1004493")
BASE_URL = f"https://handlaprivatkund.ica.se/{ICA_STORE_ID}/api/v5/products/search"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*",
    "Accept-Language": "sv-SE,sv;q=0.9",
    "Referer": "https://handlaprivatkund.ica.se/",
}


def search_products(query: str, size: int = 30) -> list[dict]:
    """Sök produkter på ICA och returnera normaliserade dicts."""
    try:
        r = requests.get(
            BASE_URL,
            params={"term": query, "limit": size, "offset": 0},
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.error(f"ICA GET fel: {e}")
        return []

    # Testa flera möjliga nycklar beroende på API-version
    items = (
        data.get("items")
        or data.get("products")
        or data.get("results")
        or []
    )
    return [_normalize(p) for p in items if p]


def _normalize(raw: dict) -> dict:
    """Normalisera en råprodukt från ICA API."""
    # ICA v5 använder PascalCase-fält
    name = (
        raw.get("ProductName")
        or raw.get("name")
        or raw.get("title")
        or ""
    )
    price = float(
        raw.get("Price")
        or raw.get("price")
        or raw.get("CurrentPrice")
        or 0
    )
    external_id = str(
        raw.get("EanId")
        or raw.get("ArticleId")
        or raw.get("id")
        or raw.get("Gtin")
        or ""
    )
    unit = (
        raw.get("SizeOrQuantity")
        or raw.get("Unit")
        or raw.get("unit")
        or raw.get("CompareUnitText")
        or ""
    )
    image_url = (
        raw.get("ImageUrl")
        or raw.get("image_url")
        or raw.get("ThumbnailImageUrl")
        or ""
    )
    brand = (
        raw.get("BrandName")
        or raw.get("brand")
        or raw.get("Brand")
        or ""
    )

    original_price_raw = raw.get("OriginalPrice") or raw.get("RegularPrice")
    original_price = float(original_price_raw) if original_price_raw else None
    is_offer = bool(raw.get("IsOffer") or raw.get("Promotion") or original_price)
    offer_label = raw.get("OfferLabel") or raw.get("PromotionText") or ""

    return {
        "external_id": external_id,
        "name": name,
        "brand": brand,
        "unit": unit,
        "image_url": image_url,
        "price": price,
        "original_price": original_price,
        "is_offer": is_offer,
        "offer_label": offer_label,
        "store": "ICA",
    }
