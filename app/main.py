# app/main.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routers import products, recipes

app = FastAPI(title="Matappen", description="Jämför matpriser och planera inköp")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(products.router)
app.include_router(recipes.router)


@app.on_event("startup")
def on_startup():
    init_db()
