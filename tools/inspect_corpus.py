"""Print a health report for the scraped corpus."""

from __future__ import annotations

import json

from scrape import db


def main() -> int:
    conn = db.connect()
    print("=== sample rows ===")
    cols = (
        "make, model_family, model_variant, year, km, power_hp, euro_class, "
        "axle_config, condition_flag, country, price_native, price_currency, "
        "price_eur, photo_count"
    )
    for r in conn.execute(f"SELECT {cols} FROM listings WHERE price_eur > 0 LIMIT 10"):
        print("  ", dict(r))

    print("\n=== field coverage ===")
    total = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    checks = {
        "total": "1=1",
        "make": "make IS NOT NULL AND make != ''",
        "model_family": "model_family IS NOT NULL AND model_family != ''",
        "year": "year > 1980",
        "km": "km > 0",
        "power_hp": "power_hp > 0",
        "euro_class": "euro_class != ''",
        "axle_config": "axle_config != ''",
        "condition_flag": "condition_flag != ''",
        "price_eur": "price_eur > 0",
        "image_urls": "json_array_length(image_urls) > 0",
        "trainable": "price_eur > 0 AND year > 1980 AND km > 0 AND make != ''",
    }
    for label, pred in checks.items():
        n = conn.execute(f"SELECT COUNT(*) FROM listings WHERE {pred}").fetchone()[0]
        pct = (100.0 * n / total) if total else 0
        print(f"  {label:15s} {n:6d}  {pct:5.1f}%")

    print("\n=== currencies ===")
    for r in conn.execute(
        "SELECT price_currency c, COUNT(*) n FROM listings GROUP BY c ORDER BY n DESC"
    ):
        print(f"  {str(r['c']):6s} {r['n']}")

    print("\n=== top makes ===")
    for r in conn.execute(
        "SELECT make, COUNT(*) n, CAST(AVG(price_eur) AS INT) avg_eur FROM listings "
        "WHERE price_eur > 0 GROUP BY make ORDER BY n DESC LIMIT 15"
    ):
        print(f"  {r['make']:18s} {r['n']:5d}  avg EUR {r['avg_eur']}")

    print("\n=== categories ===")
    for r in conn.execute("SELECT category, COUNT(*) n FROM listings GROUP BY category ORDER BY n DESC"):
        print(f"  {r['category']:22s} {r['n']}")

    print("\n=== images on disk ===")
    row = conn.execute(
        "SELECT COUNT(*) n, COUNT(DISTINCT listing_id) l FROM listing_images WHERE local_path IS NOT NULL"
    ).fetchone()
    print(f"  {row['n']} files across {row['l']} listings")

    print("\n=== summary ===")
    print(json.dumps(db.stats(conn)["sources"], ensure_ascii=False))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
