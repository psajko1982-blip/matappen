# app/models.py
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.database import Base


class Store(Base):
    __tablename__ = "stores"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    url = Column(String(255))
    products = relationship("Product", back_populates="store")


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    external_id = Column(String(100))          # butikens egna produkt-ID
    name = Column(String(255), nullable=False)
    brand = Column(String(100))
    unit = Column(String(50))                  # t.ex "1 kg", "500 ml"
    image_url = Column(String(500))
    store_id = Column(Integer, ForeignKey("stores.id"))
    store = relationship("Store", back_populates="products")
    prices = relationship("Price", back_populates="product", order_by="Price.scraped_at.desc()")


class Price(Base):
    __tablename__ = "prices"
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    price = Column(Float, nullable=False)
    original_price = Column(Float)             # pris innan rabatt
    is_offer = Column(Boolean, default=False)
    offer_label = Column(String(100))          # t.ex "3 för 2" eller "Veckans erbjudande"
    scraped_at = Column(DateTime, default=datetime.utcnow)
    product = relationship("Product", back_populates="prices")


class Recipe(Base):
    __tablename__ = "recipes"
    id = Column(Integer, primary_key=True)
    external_id = Column(String(100), unique=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    source_url = Column(String(500))
    image_url = Column(String(500))
    youtube_url = Column(String(500))
    servings = Column(Integer, default=4)
    time_minutes = Column(Integer)
    ingredients = relationship("Ingredient", back_populates="recipe", cascade="all, delete-orphan")


class Ingredient(Base):
    __tablename__ = "ingredients"
    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, ForeignKey("recipes.id"), nullable=False)
    name = Column(String(255), nullable=False)
    amount = Column(Float)
    unit = Column(String(50))
    recipe = relationship("Recipe", back_populates="ingredients")
