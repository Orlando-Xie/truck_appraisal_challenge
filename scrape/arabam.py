"""arabam.com scraper for Turkish price anchoring.

arabam.com sits behind Cloudflare, so plain HTTP returns an interstitial. A real
browser context gets through. This is deliberately time-boxed: the European
corpus provides the shape of depreciation, and this scraper only has to establish
the *level* of the Turkish market, which needs tens of listings rather than
thousands.

If it fails, `pricing/calibrate_turkiye.py` falls back to a manually collected
table, so the pipeline is never blocked on this.

Usage::

    python -m scrape.arabam --pages 8
    python -m scrape.arabam --pages 8 --headed     # watch it work / solve a challenge by hand
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone

from selectolax.parser import HTMLParser

import config
from scrape import db

log = logging.getLogger("arabam")

# Tractor units (çekici) are Kamion's core segment; kamyon covers rigids.
TARGETS = {
    "cekici": "https://www.arabam.com/ikinci-el/ticari-arac/cekici",
    "kamyon": "https://www.arabam.com/ikinci-el/ticari-arac/kamyon-kamyonet",
}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def parse_try_price(text: str) -> float | None:
    """Turkish prices use dots for thousands: '2.450.000 TL'."""
    if not text:
        return None
    m = re.search(r"([\d.]{4,})\s*(?:TL|₺)", text.replace("\xa0", " "))
    if not m:
        m = re.search(r"([\d.]{6,})", text)
    if not m:
        return None
    digits = m.group(1).replace(".", "")
    try:
        val = float(digits)
    except ValueError:
        return None
    return val if 50_000 <= val <= 100_000_000 else None


def parse_listings(html: str, category: str) -> list[dict]:
    """Parse an arabam.com results table.

    arabam renders results as table rows; the markup changes periodically, so
    parsing is intentionally forgiving and driven by the listing link plus the
    numeric cells around it rather than by brittle class names.
    """
    tree = HTMLParser(html)
    rows: list[dict] = []
    seen: set[str] = set()

    for anchor in tree.css("a[href*='/ilan/']"):
        href = anchor.attributes.get("href") or ""
        m = re.search(r"/ilan/[^/]*?(\d{6,})", href)
        if not m:
            continue
        listing_id = m.group(1)
        if listing_id in seen:
            continue

        # Walk up to the row so sibling cells (year, km, price) are in scope.
        container = anchor
        for _ in range(6):
            parent = container.parent
            if parent is None:
                break
            container = parent
            if container.tag in ("tr", "li") or "listing-list-item" in (container.attributes.get("class") or ""):
                break

        blob = " ".join((container.text() or "").split())
        title = " ".join((anchor.text() or "").split()) or (anchor.attributes.get("title") or "")
        if not title:
            continue

        price_try = parse_try_price(blob)

        year = None
        for ym in re.finditer(r"\b(19[89]\d|20[0-4]\d)\b", blob):
            candidate = int(ym.group(1))
            if 1985 <= candidate <= datetime.now().year + 1:
                year = candidate
                break

        km = None
        km_match = re.search(r"([\d.]{3,})\s*(?:km|KM|Km)", blob)
        if km_match:
            try:
                km_val = float(km_match.group(1).replace(".", ""))
                if 0 < km_val <= 3_000_000:
                    km = int(km_val)
            except ValueError:
                pass
        if km is None:
            # Some layouts show a bare thousands-separated mileage cell.
            for cand in re.finditer(r"\b(\d{1,3}(?:\.\d{3})+)\b", blob):
                val = float(cand.group(1).replace(".", ""))
                if 10_000 <= val <= 2_500_000 and (price_try is None or val != price_try):
                    km = int(val)
                    break

        make, model_family = "", ""
        tokens = title.split()
        if tokens:
            make = tokens[0]
            model_family = tokens[1] if len(tokens) > 1 else ""

        img_urls: list[str] = []
        for img in container.css("img"):
            src = img.attributes.get("data-src") or img.attributes.get("src") or ""
            if src.startswith("http") and "noimage" not in src.lower():
                img_urls.append(src)

        seen.add(listing_id)
        rows.append(
            {
                "listing_id": f"ab-{listing_id}",
                "source": "arabam",
                "category": category,
                "url": href if href.startswith("http") else f"https://www.arabam.com{href}",
                "title": title,
                "make": make,
                "model_family": model_family,
                "model_variant": " ".join(tokens[2:]) if len(tokens) > 2 else "",
                "body_type": "tractor_unit" if category == "cekici" else "other",
                "year": year,
                "km": km,
                "power_hp": None,
                "euro_class": "",
                "axle_config": "",
                "suspension": "",
                "fuel": "",
                "load_capacity_kg": None,
                "condition_flag": "used",
                "country": "Turkey",
                "city": "",
                "seller": "",
                "seller_verified": 0,
                "price_native": price_try,
                "price_currency": "TRY" if price_try else None,
                "price_eur": None,  # filled by calibration, not by a guessed rate
                "price_try": price_try,
                "photo_count": len(img_urls),
                "image_urls": img_urls[:4],
                "raw_props": {"row_text": blob[:400]},
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return rows


async def scrape(pages: int, headed: bool, targets: list[str], timeout_s: float) -> int:
    from playwright.async_api import async_playwright

    db.init()
    conn = db.connect()
    total = 0
    deadline = asyncio.get_event_loop().time() + timeout_s

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=not headed,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=UA,
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
            viewport={"width": 1440, "height": 900},
            extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"},
        )
        # Cheap stealth: hide the automation flag Cloudflare's JS checks first.
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "window.chrome = { runtime: {} };"
        )
        page = await context.new_page()

        try:
            for category in targets:
                base = TARGETS[category]
                for pno in range(1, pages + 1):
                    if asyncio.get_event_loop().time() > deadline:
                        log.warning("time box exhausted, stopping")
                        return total
                    url = base if pno == 1 else f"{base}?page={pno}"
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    except Exception as exc:
                        log.warning("%s page %d navigation failed: %s", category, pno, exc)
                        continue

                    # Wait for either real results or a visible challenge.
                    for _ in range(30):
                        html = await page.content()
                        if "/ilan/" in html and "security verification" not in html.lower():
                            break
                        if "verify you are human" in html.lower() or "checking your browser" in html.lower():
                            log.info("waiting on Cloudflare challenge ...")
                        await asyncio.sleep(1.0)

                    html = await page.content()
                    rows = parse_listings(html, category)
                    priced = [r for r in rows if r["price_try"]]
                    if not rows:
                        log.warning("%s page %d: no listings parsed (blocked or layout change)", category, pno)
                        snapshot = config.DATA_DIR / f"_arabam_{category}_{pno}.html"
                        snapshot.write_text(html, encoding="utf-8")
                        log.warning("saved page source to %s for inspection", snapshot)
                        break
                    total += db.upsert_listings(conn, rows)
                    log.info("%s p%d: %d listings (%d priced) | total %d", category, pno, len(rows), len(priced), total)
                    await asyncio.sleep(2.0)
        finally:
            await context.close()
            await browser.close()
            conn.close()
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scrape arabam.com for Turkish price anchoring")
    ap.add_argument("--pages", type=int, default=6)
    ap.add_argument("--headed", action="store_true", help="show the browser (useful if a challenge appears)")
    ap.add_argument("--category", action="append", choices=sorted(TARGETS), help="repeatable")
    ap.add_argument("--timebox", type=float, default=900.0, help="hard limit in seconds")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout
    )
    targets = args.category or ["cekici"]
    n = asyncio.run(scrape(args.pages, args.headed, targets, args.timebox))
    log.info("DONE: %d Turkish listings stored", n)
    if n == 0:
        log.warning(
            "No listings captured. Turkish price anchoring will fall back to the manual table in "
            "pricing/turkiye_manual.json."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
