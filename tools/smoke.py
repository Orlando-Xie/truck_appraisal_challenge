"""End-to-end smoke test.

Runs the full pipeline against real corpus photos and against deliberately bad
input, and prints what came back. Works with the stub vision provider, so the
plumbing, the gates and the pricing can all be verified without an API key.

Usage::

    python -m tools.smoke
    python -m tools.smoke --listing al-26063014463529862500
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import random
from pathlib import Path

from PIL import Image, ImageDraw

import config
from appraise import orchestrator
from scrape import db
from vision.schemas import SellerClaims


def corpus_photos(listing_id: str | None = None, n: int = 5) -> tuple[str, list[tuple[str, bytes]], dict]:
    conn = db.connect()
    if listing_id:
        row = conn.execute("SELECT * FROM listings WHERE listing_id = ?", (listing_id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT l.* FROM listings l JOIN listing_images i ON i.listing_id = l.listing_id "
            "WHERE l.price_eur > 0 AND l.year > 2005 AND l.km > 1000 AND i.local_path IS NOT NULL "
            "GROUP BY l.listing_id HAVING COUNT(*) >= 3 LIMIT 1"
        ).fetchone()
    if row is None:
        conn.close()
        return "", [], {}
    imgs = conn.execute(
        "SELECT local_path FROM listing_images WHERE listing_id = ? AND local_path IS NOT NULL "
        "ORDER BY idx LIMIT ?",
        (row["listing_id"], n),
    ).fetchall()
    conn.close()

    photos: list[tuple[str, bytes]] = []
    for i, im in enumerate(imgs):
        p = config.DATA_DIR / im["local_path"]
        if p.exists():
            photos.append((f"photo_{i + 1}.jpg", p.read_bytes()))
    return row["listing_id"], photos, dict(row)


def synthetic(kind: str) -> list[tuple[str, bytes]]:
    """Deterministic bad inputs for the gates."""
    if kind == "black":
        img = Image.new("RGB", (900, 600), (4, 4, 6))
        d = ImageDraw.Draw(img)
        d.ellipse((400, 280, 460, 330), fill=(22, 20, 18))
        name = "night_blur.jpg"
    elif kind == "tiny":
        img = Image.new("RGB", (60, 40), (120, 120, 120))
        name = "thumbnail.jpg"
    elif kind == "blurry":
        from PIL import ImageFilter

        img = Image.new("RGB", (900, 600), (110, 115, 120))
        d = ImageDraw.Draw(img)
        random.seed(3)
        for _ in range(40):
            x, y = random.randint(0, 880), random.randint(0, 580)
            d.rectangle((x, y, x + 40, y + 30), fill=(random.randint(60, 180),) * 3)
        img = img.filter(ImageFilter.GaussianBlur(14))
        name = "very_blurry.jpg"
    else:
        img = Image.new("RGB", (800, 600), (250, 250, 250))
        name = "blank.jpg"
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return [(name, buf.getvalue())]


def summarise(label: str, result) -> None:
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    print(f"status={result.status}  confidence={result.confidence} ({result.confidence_label})  "
          f"elapsed={result.elapsed_seconds}s")
    if result.refusal:
        print(f"REFUSED [{result.refusal.code}] {result.refusal.headline}")
        print(f"  {result.refusal.detail}")
        for w in result.refusal.what_to_send:
            print(f"   - ask for: {w}")
        return
    if result.price_try:
        p = result.price_try
        print(f"PRICE TRY  {p.low:,.0f} .. {p.mid:,.0f} .. {p.high:,.0f}".replace(",", " "))
    if result.price_eur:
        p = result.price_eur
        print(f"PRICE EUR  {p.low:,.0f} .. {p.mid:,.0f} .. {p.high:,.0f}".replace(",", " "))
    if result.identification:
        i = result.identification
        print(f"IDENT      {i.make} {i.model_family} {i.model_variant} | {i.generation} "
              f"({i.generation_year_low}-{i.generation_year_high}) | {i.axle_configuration} | "
              f"conf make={i.make_confidence} model={i.model_confidence}")
    if result.pricing_basis:
        b = result.pricing_basis
        if b.market_baseline_eur:
            print(f"BAND EUR   {b.market_baseline_eur.low:,.0f} .. {b.market_baseline_eur.high:,.0f}".replace(",", " "))
        print(f"DEDUCTIONS EUR {b.total_deductions_eur:,.0f} across {len(b.deductions)} line(s)".replace(",", " "))
        for line in b.deductions[:5]:
            print(f"   - {line.label} [{line.severity}] EUR {line.amount_eur:,.0f}: {line.rationale[:90]}".replace(",", " "))
        print(f"WIDENING   +{b.interval_widening_pct}%")
        for r in b.widening_reasons[:5]:
            print(f"   - {r}")
        for note in b.notes[:6]:
            print(f"   note: {note}")
    print(f"COMPS      {len(result.comparables)}")
    for c in result.comparables[:4]:
        print(f"   - {c.title[:52]:52s} {c.year} {str(c.km or '?'):>9} km  EUR {c.price_eur or 0:,.0f}  "
              f"sim={c.similarity}".replace(",", " "))
    for c in result.contradictions:
        print(f"CONTRADICTION [{c.severity}] {c.field}: claimed={c.claimed} observed={c.observed}")
        print(f"   {c.detail[:200]}")
    for d in result.duplicate_photos:
        print(f"DUPLICATE  {d.uploaded_filename} matches {d.listing_id} (distance {d.hamming_distance}) "
              f"EUR {d.price_eur or 0:,.0f}".replace(",", " "))
    if result.request_photos:
        print("ASKS FOR:")
        for r in result.request_photos:
            print(f"   - {r}")
    for w in result.warnings:
        print(f"WARNING    {w}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listing", help="specific corpus listing id to appraise")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO)

    print("pipeline info:")
    print(json.dumps(orchestrator.pipeline_info()["vision"], indent=2))
    print(json.dumps(orchestrator.pipeline_info()["price_model"], indent=2))

    lid, photos, row = corpus_photos(args.listing)
    if photos:
        result = await orchestrator.appraise(photos, use_cache=False)
        summarise(
            f"REAL CORPUS LISTING {lid}\n"
            f"truth: {row.get('make')} {row.get('model_family')} {row.get('model_variant')} "
            f"| {row.get('year')} | {row.get('km')} km | asking EUR {row.get('price_eur')}",
            result,
        )

        claims = SellerClaims(year=row.get("year"), make=row.get("make"), km=row.get("km"))
        result2 = await orchestrator.appraise(photos, claims=claims, use_cache=False)
        summarise(f"SAME LISTING, WITH SELLER-TYPED DETAILS", result2)
    else:
        print("\n!! no corpus photos on disk yet -- run `python -m scrape.images` first")

    for kind, label in [
        ("black", "ADVERSARIAL: night shot of nothing"),
        ("tiny", "ADVERSARIAL: 60x40 thumbnail"),
        ("blurry", "ADVERSARIAL: heavily blurred frame"),
    ]:
        result = await orchestrator.appraise(synthetic(kind), use_cache=False)
        summarise(label, result)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
