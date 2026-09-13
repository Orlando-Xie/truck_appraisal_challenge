"""Autoline scraper.

Autoline serves plain HTML with no bot protection, and every listing card
carries year, mileage, power, axle configuration, Euro class, suspension,
condition and price in both native currency and EUR. That makes it the right
backbone for the comparables corpus.

Two sources of truth are merged per page:

* the HTML cards (all 35 per page, full specification set)
* the embedded ``ld+json`` ItemList (a subset, but with full-size image URLs
  and an explicit itemCondition)

Usage::

    python -m scrape.autoline --pages 90 --category truck_tractors
    python -m scrape.autoline --all --pages 40
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
from datetime import datetime, timezone

import httpx
from selectolax.parser import HTMLParser

import config
from scrape import db

log = logging.getLogger("autoline")

BASE = "https://autoline.info"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

# Categories worth having in the corpus. `body_type` is the canonical label the
# pricing model uses; `weight` is how many pages to spend when scraping all.
CATEGORIES: dict[str, dict] = {
    # Heavy vehicles -- these drive the pricing model. Truck tractors are
    # Kamion's core segment so they get the most pages.
    "truck_tractors": {"path": "/-/truck-tractors--c42", "body_type": "tractor_unit", "weight": 10},
    "dump_trucks": {"path": "/-/dump-trucks--c36", "body_type": "tipper", "weight": 4},
    "box_trucks": {"path": "/-/box-trucks--c12", "body_type": "box", "weight": 3},
    "refrigerated_trucks": {"path": "/-/refrigerated-trucks--c7", "body_type": "refrigerated", "weight": 2},
    "car_transporters": {"path": "/-/car-transporters--c14", "body_type": "car_transporter", "weight": 1},
    "tow_trucks": {"path": "/-/tow-trucks--c17", "body_type": "tow", "weight": 1},
    "garbage_trucks": {"path": "/-/garbage-trucks--c342", "body_type": "garbage", "weight": 1},
    # Negative / adversarial classes. Not used for pricing, but real photos of
    # things that are not a heavy truck are exactly what the gates must reject,
    # and they make an honest adversarial test set.
    "motorcycles": {"path": "/-/motorcycles--c1982", "body_type": "_negative", "weight": 1},
    "cars": {"path": "/-/cars--c1169", "body_type": "_negative", "weight": 1},
    "semi_trailers": {"path": "/-/semi-trailers--c43", "body_type": "_negative", "weight": 1},
    "cargo_vans": {"path": "/-/cargo-vans--c78", "body_type": "_negative", "weight": 1},
}

NEGATIVE_CATEGORIES = {k for k, v in CATEGORIES.items() if v["body_type"] == "_negative"}

# Fallback conversion, only used when the page does not print a EUR figure.
FX_TO_EUR = {
    "EUR": 1.0,
    "USD": 0.86,
    "GBP": 1.17,
    "PLN": 0.235,
    "SEK": 0.088,
    "DKK": 0.134,
    "NOK": 0.086,
    "CZK": 0.040,
    "HUF": 0.0026,
    "RON": 0.20,
    "CHF": 1.06,
    "TRY": 0.021,
    "UAH": 0.021,
    "BGN": 0.511,
}

CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP", "₺": "TRY", "zł": "PLN"}

_AMOUNT_RE = re.compile(
    r"(?P<sym>[€$£₺])\s*(?P<v1>\d[\d\s.,]*)"
    r"|(?P<code>[A-Z]{3})\s*(?P<v2>\d[\d\s.,]*)"
)


def _to_number(raw: str) -> float | None:
    """Parse Autoline's thousands-separated figures."""
    cleaned = raw.replace("\xa0", " ").strip()
    cleaned = re.sub(r"[^\d.,]", "", cleaned)
    if not cleaned:
        return None
    # Autoline uses comma as the thousands separator and dot for decimals.
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        # A trailing 1-2 digit group is a decimal, otherwise it is thousands.
        cleaned = cleaned.replace(",", ".") if len(parts[-1]) <= 2 and len(parts) == 2 else cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_prices(text: str) -> list[tuple[str, float]]:
    """Extract every (currency, amount) pair printed in a price block."""
    out: list[tuple[str, float]] = []
    for m in _AMOUNT_RE.finditer(text):
        if m.group("sym"):
            cur = CURRENCY_SYMBOLS.get(m.group("sym"), "")
            val = _to_number(m.group("v1") or "")
        else:
            cur = m.group("code") or ""
            val = _to_number(m.group("v2") or "")
        if cur and val and val > 50:
            out.append((cur, val))
    return out


def resolve_price(text: str) -> tuple[float | None, str | None, float | None]:
    """Return (native_amount, native_currency, eur_amount).

    The page prints the asking price in the seller's currency and, when that is
    not EUR, an approximate EUR figure alongside. Preferring the site's own EUR
    conversion avoids baking a stale FX rate into the corpus.
    """
    pairs = parse_prices(text)
    if not pairs:
        return None, None, None
    native_cur, native_val = pairs[0]
    eur_val = None
    for cur, val in pairs:
        if cur == "EUR":
            eur_val = val
            break
    if eur_val is None:
        rate = FX_TO_EUR.get(native_cur)
        if rate:
            eur_val = native_val * rate
    return native_val, native_cur, eur_val


def _txt(node) -> str:
    return " ".join((node.text() or "").split()) if node is not None else ""


def _first(card, selector: str):
    found = card.css_first(selector)
    return found


def _prop(card, title: str) -> str:
    """Read a labelled property out of a card by its title attribute."""
    for sel in (
        f'.sl-main-props__item[title="{title}"] .value',
        f'.property[title="{title}"] .value',
        f'div[title="{title}"] .value',
        f'span[title="{title}"] .value',
    ):
        node = card.css_first(sel)
        if node is not None:
            return _txt(node)
    return ""


def _int_or_none(raw: str) -> int | None:
    digits = re.sub(r"[^\d]", "", raw or "")
    return int(digits) if digits else None


def big_image_url(url: str) -> str:
    """Thumbnails and full-size images differ only by this token."""
    return url.replace("_common--", "_big--").replace("_small--", "_big--")


def parse_ld_products(tree: HTMLParser) -> dict[str, dict]:
    """Map listing id -> the ld+json Product entry."""
    out: dict[str, dict] = {}
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text() or "{}")
        except json.JSONDecodeError:
            continue
        if data.get("@type") != "ItemList":
            continue
        for entry in data.get("itemListElement", []):
            item = entry.get("item") or {}
            url = item.get("url") or ""
            m = re.search(r"--(\d{15,})$", url)
            if m:
                out[m.group(1)] = item
    return out


def parse_card(card, category: str, ld: dict[str, dict]) -> dict | None:
    listing_id = card.attributes.get("data-code") or ""
    if not listing_id:
        return None

    name = (card.attributes.get("data-name") or "").strip()
    make = (card.attributes.get("data-brand") or "").strip()

    link = _first(card, "a.sales-item-title-link")
    url = (link.attributes.get("href") if link is not None else "") or ""
    title = _txt(link) or name

    # Model family / variant: data-name is "<brand> <model...>", so removing the
    # brand prefix leaves the model designation.
    model_full = name
    if make and name.lower().startswith(make.lower()):
        model_full = name[len(make) :].strip()
    tokens = model_full.split()
    model_family = tokens[0] if tokens else ""
    model_variant = " ".join(tokens[1:]) if len(tokens) > 1 else ""

    price_node = _first(card, ".price")
    price_native, price_currency, price_eur = resolve_price(_txt(price_node)) if price_node is not None else (None, None, None)

    year = _int_or_none(_prop(card, "year"))
    km = _int_or_none(_prop(card, "mileage"))
    euro_class = _prop(card, "Euro")
    axle_config = _prop(card, "Axle configuration")
    suspension = _prop(card, "Suspension")
    fuel = _prop(card, "Fuel")
    load_cap = _int_or_none(_prop(card, "Load cap."))

    power_raw = _prop(card, "Power")
    power_hp = None
    if power_raw:
        m = re.search(r"(\d[\d,\.]*)\s*(?:HP|hp|л\.с\.)", power_raw)
        if m:
            power_hp = _int_or_none(m.group(1))

    condition_flag = _prop(card, "Condition") or _prop(card, "condition")

    location = _txt(_first(card, ".location-text"))
    country, city = "", ""
    if location:
        bits = [b.strip() for b in location.split(",")]
        country = bits[0]
        city = bits[1] if len(bits) > 1 else ""

    seller_node = _first(card, ".branding-company-name")
    seller = _txt(seller_node)
    seller_verified = 1 if card.css_first(".verified-company") is not None else 0

    photo_count = _int_or_none(_txt(_first(card, ".num .value"))) or 0

    # Images: prefer the ld+json full-size list, otherwise promote the card
    # thumbnails to full size.
    product = ld.get(listing_id) or {}
    image_urls: list[str] = [u for u in (product.get("image") or []) if isinstance(u, str)]
    if not image_urls:
        seen: set[str] = set()
        for img in card.css(".photo-img-wrapper img"):
            src = img.attributes.get("data-src") or img.attributes.get("src") or ""
            if src.startswith("data:") or not src:
                continue
            full = big_image_url(src)
            if full not in seen:
                seen.add(full)
                image_urls.append(full)

    if not condition_flag:
        item_condition = (product.get("itemCondition") or "").rsplit("/", 1)[-1]
        if item_condition == "NewCondition":
            condition_flag = "new"
        elif item_condition == "UsedCondition":
            condition_flag = "used"

    # A defect marker appears in the card as a badge rather than a property.
    card_text = _txt(card).lower()
    for marker in ("crashed", "with a defect", "damaged", "for spare parts"):
        if marker in card_text:
            condition_flag = marker
            break

    raw_props: dict[str, str] = {}
    for prop in card.css(".property[title]"):
        key = prop.attributes.get("title") or ""
        val = _txt(prop.css_first(".value"))
        if key and val:
            raw_props[key] = val
    for prop in card.css(".sl-main-props__item[title]"):
        key = prop.attributes.get("title") or ""
        val = _txt(prop.css_first(".value"))
        if key and val:
            raw_props[key] = val

    body_type = CATEGORIES.get(category, {}).get("body_type", "other")

    return {
        "listing_id": f"al-{listing_id}",
        "source": "autoline",
        "category": category,
        "url": url,
        "title": title,
        "make": make,
        "model_family": model_family,
        "model_variant": model_variant,
        "body_type": body_type,
        "year": year,
        "km": km,
        "power_hp": power_hp,
        "euro_class": euro_class,
        "axle_config": axle_config,
        "suspension": suspension,
        "fuel": fuel,
        "load_capacity_kg": load_cap,
        "condition_flag": condition_flag or "",
        "country": country,
        "city": city,
        "seller": seller,
        "seller_verified": seller_verified,
        "price_native": price_native,
        "price_currency": price_currency,
        "price_eur": price_eur,
        "price_try": None,
        "photo_count": photo_count,
        "image_urls": image_urls,
        "raw_props": raw_props,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def parse_page(html: str, category: str) -> list[dict]:
    tree = HTMLParser(html)
    ld = parse_ld_products(tree)
    rows = []
    for card in tree.css(".sl-item[data-code]"):
        try:
            row = parse_card(card, category, ld)
        except Exception as exc:  # a single malformed card must not kill the run
            log.debug("card parse failed: %s", exc)
            continue
        if row:
            rows.append(row)
    return rows


def scrape_category(
    client: httpx.Client,
    conn,
    category: str,
    pages: int,
    delay: float = 1.0,
) -> int:
    meta = CATEGORIES[category]
    total = 0
    empty_streak = 0
    for page in range(1, pages + 1):
        url = f"{BASE}{meta['path']}"
        if page > 1:
            url += f"?page={page}"
        try:
            resp = client.get(url)
        except Exception as exc:
            log.warning("%s page %d fetch error: %s", category, page, exc)
            time.sleep(delay * 3)
            continue
        if resp.status_code != 200:
            log.warning("%s page %d -> HTTP %d, stopping category", category, page, resp.status_code)
            db.log_fetch(conn, url, resp.status_code, 0)
            break
        rows = parse_page(resp.text, category)
        db.log_fetch(conn, url, resp.status_code, len(rows))
        if not rows:
            empty_streak += 1
            if empty_streak >= 2:
                log.info("%s: two empty pages, assuming end of results", category)
                break
        else:
            empty_streak = 0
            total += db.upsert_listings(conn, rows)
        priced = sum(1 for r in rows if r.get("price_eur"))
        log.info("%s p%d: %d cards (%d priced) | running total %d", category, page, len(rows), priced, total)
        time.sleep(delay + random.uniform(0, 0.4))
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scrape Autoline truck listings")
    ap.add_argument("--category", action="append", help="category key; repeatable")
    ap.add_argument("--all", action="store_true", help="scrape every configured category")
    ap.add_argument("--pages", type=int, default=20, help="pages per unit of category weight")
    ap.add_argument("--delay", type=float, default=0.8, help="seconds between requests")
    ap.add_argument("--list", action="store_true", help="list category keys and exit")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout
    )

    if args.list:
        for key, meta in CATEGORIES.items():
            print(f"{key:22s} weight={meta['weight']:2d} body={meta['body_type']:16s} {meta['path']}")
        return 0

    if args.all:
        targets = list(CATEGORIES)
    elif args.category:
        targets = args.category
    else:
        targets = ["truck_tractors"]

    unknown = [t for t in targets if t not in CATEGORIES]
    if unknown:
        print(f"unknown categories: {unknown}", file=sys.stderr)
        return 2

    db.init()
    conn = db.connect()
    grand = 0
    try:
        with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True, http2=False) as client:
            for cat in targets:
                weight = CATEGORIES[cat]["weight"]
                # Negative classes need only a handful of examples.
                pages = args.pages if cat not in NEGATIVE_CATEGORIES else min(2, args.pages)
                if args.all:
                    pages = max(1, int(args.pages * weight / 10)) if cat not in NEGATIVE_CATEGORIES else 2
                log.info("=== %s: up to %d pages ===", cat, pages)
                grand += scrape_category(client, conn, cat, pages, args.delay)
        s = db.stats(conn)
        log.info("DONE. upserted=%d | corpus=%s", grand, json.dumps(s, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
