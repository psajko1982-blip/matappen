# app/scrapers/ica.py
"""Scraper för ICA Handla — Playwright-baserad DOM-extraktion."""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

# Konfiguration via miljövariabler
ICA_STORE_ZIP = os.getenv("ICA_STORE_ZIP", "17141")   # postnummer för butiken
ICA_STORE_ID  = os.getenv("ICA_STORE_ID", "")         # valfritt: välj specifik butik-ID

# Playwright-state (initieras en gång per process)
_playwright_obj = None
_browser        = None
_page           = None
_store_base_url: str = ""


def _init_session() -> bool:
    """Starta Playwright och navigera till ICA-butiken. Returnerar True vid lyckat."""
    global _playwright_obj, _browser, _page, _store_base_url

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        logger.error("playwright saknas — kör: pip install playwright && playwright install chromium")
        return False

    try:
        logger.info("ICA: startar Playwright och väljer butik...")
        _playwright_obj = sync_playwright().start()
        _browser = _playwright_obj.chromium.launch(headless=True)
        ctx = _browser.new_context(
            locale="sv-SE",
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        _page = ctx.new_page()

        # 1. Startsida + cookie-banner
        _page.goto("https://handla.ica.se/?chooseStore=true",
                   wait_until="networkidle", timeout=30_000)
        try:
            _page.click('button:has-text("Godkänn kakor")', timeout=5_000)
            _page.wait_for_timeout(1_000)
        except PWTimeout:
            pass

        # 2. Ange postnummer
        _page.evaluate(f"""() => {{
            const i = document.querySelector('input[name=zipcode]');
            i.value = '{ICA_STORE_ZIP}';
            i.dispatchEvent(new Event('input', {{bubbles: true}}));
            i.dispatchEvent(new KeyboardEvent('keydown', {{key:'Enter', keyCode:13, bubbles:true}}));
        }}""")
        _page.wait_for_timeout(3_000)

        # 3. Klicka fliken "Hemleverans"
        _page.evaluate(
            "() => document.querySelector('[data-automation-id=store-selector-view-home-delivery]')?.click()"
        )
        _page.wait_for_timeout(3_000)

        # 4. Välj butik — specifik via data-testid eller bara den första
        if ICA_STORE_ID:
            selector = f'[data-testid="store-selector-select-store_{ICA_STORE_ID}"]'
            clicked = _page.evaluate(f"""() => {{
                const btn = document.querySelector('{selector}');
                if (btn) {{ btn.click(); return true; }}
                return false;
            }}""")
        else:
            clicked = False

        if not clicked:
            # Klicka första butiksspecifika "Välj butik"-knappen (index 1 är den första butiken)
            _page.evaluate("""() => {
                const btns = [...document.querySelectorAll('button')]
                    .filter(b => b.textContent.includes('lj butik'));
                if (btns.length > 1) btns[1].click();
                else if (btns.length > 0) btns[0].click();
            }""")

        _page.wait_for_timeout(5_000)

        if "handlaprivatkund.ica.se" not in _page.url:
            logger.error(f"ICA: hamnade inte på handlaprivatkund ({_page.url})")
            return False

        _store_base_url = _page.url.rstrip("/")
        logger.info(f"ICA: session klar — butik {_store_base_url}")
        return True

    except Exception as e:
        logger.error(f"ICA Playwright-fel: {e}")
        return False


def _ensure_session() -> bool:
    """Återanvänder befintlig session eller skapar en ny."""
    if _page is not None and _store_base_url:
        return True
    return _init_session()


def search_products(query: str, size: int = 30) -> list[dict]:
    """Sök produkter på ICA och returnera normaliserade dicts."""
    if not _ensure_session():
        return []

    try:
        from playwright.sync_api import TimeoutError as PWTimeout
        search_url = f"{_store_base_url}/search?q={query}"
        _page.goto(search_url, wait_until="networkidle", timeout=20_000)
        _page.wait_for_timeout(3_000)

        products = _page.evaluate(f"""() => {{
            const cards = [...document.querySelectorAll('[class*=product-card]')];
            return cards.slice(0, {size}).map(card => {{
                const nameEl  = card.querySelector('h2, h3, [class*=name], [class*=title]');
                const link    = card.querySelector('a[href*="/products/"]');
                const imgEl   = card.querySelector('img');
                const unitEl  = card.querySelector('[class*=size-layout]');
                const priceEls = [...card.querySelectorAll('[class*=price]')];

                let price = 0;
                let unit  = unitEl?.textContent?.trim() || '';
                for (const el of priceEls) {{
                    const txt = el.textContent.trim();
                    if (!price) {{
                        const m = txt.match(/(\\d+[,.]\\d+|\\d+)/);
                        if (m) price = parseFloat(m[1].replace(',', '.'));
                    }}
                }}

                // Extrahera produkt-ID från URL: /products/slug/1234567
                let external_id = '';
                if (link) {{
                    const m = link.href.match(/\\/products\\/[^/]+\\/(\\d+)/);
                    if (m) external_id = m[1];
                }}

                return {{
                    external_id,
                    name:      nameEl?.textContent?.trim() || '',
                    price,
                    unit,
                    image_url: imgEl?.src || imgEl?.dataset?.src || '',
                    brand:     '',
                }};
            }}).filter(p => p.name && p.price > 0);
        }}""")

        logger.debug(f"ICA: {len(products)} produkter för '{query}'")
        return [_normalize(p) for p in products]

    except Exception as e:
        logger.error(f"ICA sökfel '{query}': {e}")
        return []


def _normalize(raw: dict) -> dict:
    """Lägg till fält som matchar DB-modellens format."""
    # Extrahera jämförelseenhet från t.ex. "1.5L (11,32 kr/l)"
    unit_txt = raw.get("unit", "")
    m = re.match(r'^([^(]+)', unit_txt)
    unit = m.group(1).strip() if m else unit_txt

    return {
        "external_id":    raw.get("external_id", ""),
        "name":           raw.get("name", ""),
        "brand":          raw.get("brand", ""),
        "unit":           unit,
        "image_url":      raw.get("image_url", ""),
        "price":          raw.get("price", 0),
        "original_price": None,
        "is_offer":       False,
        "offer_label":    "",
        "store":          "ICA",
    }
