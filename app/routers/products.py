# app/routers/products.py
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Price, Product, Store
from app.scrapers import willys

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    offers = (
        db.query(Product)
        .join(Price)
        .filter(Price.is_offer == True)
        .order_by(Price.scraped_at.desc())
        .limit(20)
        .all()
    )
    return templates.TemplateResponse(
        "index.html", {"request": request, "offers": offers}
    )


@router.get("/sok", response_class=HTMLResponse)
async def search(request: Request, q: str = "", db: Session = Depends(get_db)):
    results = []
    error = ""

    if q:
        # Hämta alltid live från Willys och spara/uppdatera i DB
        try:
            store = _get_or_create_store(db, "Willys", "https://www.willys.se")
            raw = willys.search_products(q, size=60)
            for item in raw:
                if item["external_id"] and item["name"] and item["price"] > 0:
                    _upsert_product(db, item, store)
        except Exception as e:
            error = f"Kunde inte hämta från Willys: {e}"

        results = (
            db.query(Product)
            .filter(Product.name.ilike(f"%{q}%"))
            .limit(60)
            .all()
        )

    return templates.TemplateResponse(
        "search.html",
        {"request": request, "query": q, "results": results, "error": error},
    )


@router.post("/hamta-erbjudanden")
async def fetch_offers(db: Session = Depends(get_db)):
    store = _get_or_create_store(db, "Willys", "https://www.willys.se")
    count = 0
    for item in willys.get_all_offers():
        if item["external_id"] and item["name"] and item["price"] > 0:
            _upsert_product(db, item, store)
            count += 1
    return {"message": f"Hämtade {count} erbjudanden från Willys"}
