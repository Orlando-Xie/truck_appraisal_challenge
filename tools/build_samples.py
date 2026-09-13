"""Install one-click demo cases.

A live demo should never depend on finding a file. These cases are drawn from the
evaluation holdout (so the pipeline has genuinely never trained on them) plus real
photos of things that are not trucks, which is what the abstention gates exist
for.

Usage::

    python -m tools.build_samples
"""

from __future__ import annotations

import json
import shutil
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import config
from scrape import db


def _copy(listing_id: str, prefix: str, limit: int = 6) -> list[str]:
    conn = db.connect()
    rows = conn.execute(
        "SELECT local_path FROM listing_images WHERE listing_id = ? AND local_path IS NOT NULL "
        "ORDER BY idx LIMIT ?",
        (listing_id, limit),
    ).fetchall()
    conn.close()
    out: list[str] = []
    for i, row in enumerate(rows):
        src = config.DATA_DIR / row["local_path"]
        if not src.exists():
            continue
        name = f"{prefix}_{i + 1}.jpg"
        shutil.copyfile(src, config.SAMPLES_DIR / name)
        out.append(name)
    return out


def _pick(where: str, params: tuple = (), order: str = "l.price_eur DESC") -> dict | None:
    conn = db.connect()
    row = conn.execute(
        "SELECT l.* FROM listings l JOIN listing_images i ON i.listing_id = l.listing_id "
        f"WHERE {where} AND i.local_path IS NOT NULL "
        f"GROUP BY l.listing_id HAVING COUNT(*) >= 3 ORDER BY {order} LIMIT 1",
        params,
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _truth(row: dict) -> dict:
    return {
        "listing_id": row["listing_id"],
        "make": row["make"],
        "model": f"{row['model_family']} {row['model_variant']}".strip(),
        "year": row["year"],
        "km": row["km"],
        "asking_price_eur": row["price_eur"],
        "country": row["country"],
        "url": row["url"],
    }


def synth_night_blur() -> str:
    """A dark, out-of-focus frame: the classic useless upload."""
    img = Image.new("RGB", (1200, 800), (9, 10, 14))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((380, 300, 820, 560), radius=30, fill=(26, 25, 24))
    d.ellipse((410, 330, 470, 380), fill=(58, 54, 44))
    d.ellipse((730, 330, 790, 380), fill=(58, 54, 44))
    img = img.filter(ImageFilter.GaussianBlur(11))
    name = "adv_night_blur.jpg"
    img.save(config.SAMPLES_DIR / name, "JPEG", quality=80)
    return name


def synth_listing_screenshot() -> str:
    """A screenshot of an advert rather than a photo of the truck."""
    img = Image.new("RGB", (1200, 820), (247, 248, 250))
    d = ImageDraw.Draw(img)
    try:
        font_l = ImageFont.truetype("arialbd.ttf", 40)
        font_m = ImageFont.truetype("arial.ttf", 24)
        font_s = ImageFont.truetype("arial.ttf", 18)
    except OSError:
        font_l = font_m = font_s = ImageFont.load_default()

    d.rectangle((0, 0, 1200, 74), fill=(28, 36, 54))
    d.text((28, 22), "ikinci-el-kamyon.example.com", fill=(235, 238, 245), font=font_m)
    d.rectangle((40, 110, 700, 520), fill=(206, 212, 222), outline=(170, 178, 190), width=2)
    d.text((250, 300), "[ vehicle photo ]", fill=(120, 128, 142), font=font_m)
    d.text((740, 120), "2 450 000 TL", fill=(18, 24, 38), font=font_l)
    for i, line in enumerate(
        [
            "Mercedes-Benz Actros 1845",
            "2018 model",
            "742 000 km",
            "Euro 6 - Otomatik",
            "Istanbul / Tuzla",
            "Sahibinden satilik cekici",
        ]
    ):
        d.text((740, 200 + i * 40), line, fill=(58, 66, 82), font=font_s)
    d.rectangle((740, 470, 1060, 520), fill=(46, 130, 220))
    d.text((800, 485), "Satici ile iletisim", fill=(255, 255, 255), font=font_s)

    name = "adv_listing_screenshot.jpg"
    img.save(config.SAMPLES_DIR / name, "JPEG", quality=88)
    return name


def main() -> int:
    config.SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    for old in config.SAMPLES_DIR.glob("*"):
        if old.is_file():
            old.unlink()

    samples: list[dict] = []

    # 1. A well-photographed modern tractor unit from the holdout.
    row = _pick(
        "l.is_holdout = 1 AND l.category = 'truck_tractors' AND l.year >= 2017 "
        "AND l.price_eur > 25000 AND l.km > 100000"
    )
    if row:
        files = _copy(row["listing_id"], "clean_tractor")
        if files:
            samples.append(
                {
                    "id": "clean-tractor",
                    "label": "Modern tractor unit",
                    "description": f"{row['year']} {row['make']} {row['model_family']} from the blind holdout",
                    "kind": "truck",
                    "files": files,
                    "truth": _truth(row),
                }
            )

    # 2. An older, higher-mileage tractor: the harder end of the market.
    row = _pick(
        "l.is_holdout = 1 AND l.category = 'truck_tractors' AND l.year BETWEEN 2005 AND 2013 "
        "AND l.km > 600000 AND l.price_eur > 5000",
        order="l.km DESC",
    )
    if row:
        files = _copy(row["listing_id"], "old_tractor")
        if files:
            samples.append(
                {
                    "id": "high-mileage-tractor",
                    "label": "High-mileage older tractor",
                    "description": f"{row['year']} {row['make']} {row['model_family']}, {row['km']:,} km".replace(",", " "),
                    "kind": "truck",
                    "files": files,
                    "truth": _truth(row),
                }
            )

    # 3. A tipper, to show the body-type handling.
    row = _pick("l.is_holdout = 1 AND l.category = 'dump_trucks' AND l.price_eur > 15000")
    if row is None:
        row = _pick("l.category = 'dump_trucks' AND l.price_eur > 15000 AND l.year > 2010")
    if row:
        files = _copy(row["listing_id"], "tipper")
        if files:
            samples.append(
                {
                    "id": "tipper",
                    "label": "Tipper truck",
                    "description": f"{row['year']} {row['make']} {row['model_family']} tipper",
                    "kind": "truck",
                    "files": files,
                    "truth": _truth(row),
                }
            )

    # 4. A truck listing with typed details that the photos contradict.
    row = _pick(
        "l.is_holdout = 1 AND l.category = 'truck_tractors' AND l.year BETWEEN 2014 AND 2020 "
        "AND l.price_eur > 15000"
    )
    if row:
        files = _copy(row["listing_id"], "lying_seller")
        if files:
            samples.append(
                {
                    "id": "lying-seller",
                    "label": "Seller's details do not match",
                    "description": "Same truck, but the typed year and mileage are wrong on purpose",
                    "kind": "truck",
                    "files": files,
                    "truth": _truth(row),
                    # Deliberately optimistic claims, to show the cross-checks firing.
                    "claims": {
                        "year": min(2024, (row["year"] or 2018) + 6),
                        "make": row["make"],
                        "km": max(80_000, int((row["km"] or 700_000) * 0.35)),
                        "asking_price_try": None,
                    },
                }
            )

    # 5. A motorcycle: a real listing photo of something that is not a truck.
    row = _pick("l.category = 'motorcycles'")
    if row:
        files = _copy(row["listing_id"], "adv_motorcycle", limit=3)
        if files:
            samples.append(
                {
                    "id": "adversarial-motorcycle",
                    "label": "A motorcycle",
                    "description": "Should be refused, not priced",
                    "kind": "adversarial",
                    "files": files,
                    "truth": _truth(row),
                }
            )

    # 6. A trailer with no tractor unit: the subtle wrong-subject case.
    row = _pick("l.category = 'semi_trailers'")
    if row:
        files = _copy(row["listing_id"], "adv_trailer", limit=3)
        if files:
            samples.append(
                {
                    "id": "adversarial-trailer",
                    "label": "A trailer, no truck",
                    "description": "Priced on completely different criteria; should be refused",
                    "kind": "adversarial",
                    "files": files,
                    "truth": _truth(row),
                }
            )

    # 7 and 8. Synthetic failure modes.
    samples.append(
        {
            "id": "adversarial-night-blur",
            "label": "Dark, blurred frame",
            "description": "Fails the local quality check before any API call",
            "kind": "adversarial",
            "files": [synth_night_blur()],
        }
    )
    samples.append(
        {
            "id": "adversarial-screenshot",
            "label": "Screenshot of an advert",
            "description": "A screenshot is not an inspection; should be called out",
            "kind": "adversarial",
            "files": [synth_listing_screenshot()],
        }
    )

    (config.SAMPLES_DIR / "index.json").write_text(
        json.dumps({"samples": samples}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"installed {len(samples)} demo cases in {config.SAMPLES_DIR}")
    for s in samples:
        print(f"  {s['id']:28s} {len(s['files'])} photo(s)  {s['label']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
