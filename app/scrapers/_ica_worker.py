#!/usr/bin/env python
"""ICA Playwright worker — körs som separat subprocess av ica.py.

Kommunikation:
  stdin  → JSON-rader: {"query": "...", "size": 30}
  stdout → JSON-rader: [{...produkt...}, ...]  (en rad per query)
  stderr → loggar (visas i uvicorn-terminalen)
"""
import asyncio
import json
import logging
import os
import re
import sys

# På Railway (Linux): sätt browser-sökväg INNAN playwright importeras.
# /app/.playwright är innanför app-katalogen som bevaras i Docker-imagen.
if sys.platform != "win32":
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/app/.playwright")

# KRITISKT: måste sättas INNAN asyncio.run() på Windows/Python 3.14.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="ICA worker: %(message)s")
logger = logging.getLogger(__name__)

ICA_STORE_ZIP = os.getenv("ICA_STORE_ZIP", "17141")
ICA_STORE_ID  = os.getenv("ICA_STORE_ID", "")


async def _init_store(page) -> str:
    from playwright.async_api import TimeoutError as PWTimeout

    await page.goto("https://handla.ica.se/?chooseStore=true",
                    wait_until="networkidle", timeout=30_000)
    try:
        await page.click('button:has-text("Godkänn kakor")', timeout=5_000)
        await page.wait_for_timeout(1_000)
    except PWTimeout:
        pass

    await page.evaluate(f"""() => {{
        const i = document.querySelector('input[name=zipcode]');
        i.value = '{ICA_STORE_ZIP}';
        i.dispatchEvent(new Event('input', {{bubbles: true}}));
        i.dispatchEvent(new KeyboardEvent('keydown', {{key:'Enter', keyCode:13, bubbles:true}}));
    }}""")
    await page.wait_for_timeout(3_000)

    await page.evaluate(
        "() => document.querySelector('[data-automation-id=store-selector-view-home-delivery]')?.click()"
    )
    await page.wait_for_timeout(3_000)

    if ICA_STORE_ID:
        clicked = await page.evaluate(f"""() => {{
            const btn = document.querySelector('[data-testid="store-selector-select-store_{ICA_STORE_ID}"]');
            if (btn) {{ btn.click(); return true; }}
            return false;
        }}""")
    else:
        clicked = False

    if not clicked:
        await page.evaluate("""() => {
            const btns = [...document.querySelectorAll('button')]
                .filter(b => b.textContent.includes('lj butik'));
            if (btns.length > 1) btns[1].click();
            else if (btns.length > 0) btns[0].click();
        }""")

    await page.wait_for_timeout(5_000)

    if "handlaprivatkund.ica.se" not in page.url:
        logger.error(f"hamnade inte på handlaprivatkund ({page.url})")
        return ""

    url = page.url.rstrip("/")
    logger.info(f"session klar — {url}")
    return url


async def _do_search(page, store_base_url: str, query: str, size: int) -> list[dict]:
    await page.goto(f"{store_base_url}/search?q={query}",
                    wait_until="networkidle", timeout=20_000)
    await page.wait_for_timeout(3_000)

    return await page.evaluate(f"""() => {{
        const cards = [...document.querySelectorAll('[class*=product-card]')];
        return cards.slice(0, {size}).map(card => {{
            const nameEl   = card.querySelector('h2, h3, [class*=name], [class*=title]');
            const link     = card.querySelector('a[href*="/products/"]');
            const imgEl    = card.querySelector('img');
            const unitEl   = card.querySelector('[class*=size-layout]');
            const priceEls = [...card.querySelectorAll('[class*=price]')];
            let price = 0, unit = unitEl?.textContent?.trim() || '';
            for (const el of priceEls) {{
                if (!price) {{
                    const m = el.textContent.trim().match(/(\\d+[,.]\\d+|\\d+)/);
                    if (m) price = parseFloat(m[1].replace(',', '.'));
                }}
            }}
            let external_id = '';
            if (link) {{
                const m = link.href.match(/\\/products\\/[^/]+\\/(\\d+)/);
                if (m) external_id = m[1];
            }}
            return {{
                external_id,
                name:      nameEl?.textContent?.trim() || '',
                price, unit,
                image_url: imgEl?.src || imgEl?.dataset?.src || '',
                brand: '',
            }};
        }}).filter(p => p.name && p.price > 0);
    }}""")


def _normalize(raw: dict) -> dict:
    unit_txt = raw.get("unit", "")
    m = re.match(r'^([^(]+)', unit_txt)
    return {
        "external_id":    raw.get("external_id", ""),
        "name":           raw.get("name", ""),
        "brand":          "",
        "unit":           m.group(1).strip() if m else unit_txt,
        "image_url":      raw.get("image_url", ""),
        "price":          raw.get("price", 0),
        "original_price": None,
        "is_offer":       False,
        "offer_label":    "",
        "store":          "ICA",
    }


async def main():
    from playwright.async_api import async_playwright

    logger.info("startar Playwright...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="sv-SE",
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()
        store_base_url = await _init_store(page)

        if not store_base_url:
            print(json.dumps([]), flush=True)  # signal: init misslyckades
            return

        # Signalera till föräldraprocessen att vi är redo
        print("READY", flush=True)

        loop = asyncio.get_event_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                raw = await _do_search(page, store_base_url, req["query"], req.get("size", 30))
                results = [_normalize(p) for p in raw]
                logger.info(f"{len(results)} produkter för '{req['query']}'")
                print(json.dumps(results, ensure_ascii=False), flush=True)
            except Exception as e:
                logger.error(f"sökfel: {e}")
                print(json.dumps([]), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
