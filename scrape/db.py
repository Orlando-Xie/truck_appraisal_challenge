"""SQLite storage for the scraped comparables corpus."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    listing_id       TEXT PRIMARY KEY,
    source           TEXT NOT NULL,
    category         TEXT,
    url              TEXT,
    title            TEXT,
    make             TEXT,
    model_family     TEXT,
    model_variant    TEXT,
    body_type        TEXT,
    year             INTEGER,
    km               INTEGER,
    power_hp         INTEGER,
    euro_class       TEXT,
    axle_config      TEXT,
    suspension       TEXT,
    fuel             TEXT,
    load_capacity_kg INTEGER,
    condition_flag   TEXT,
    country          TEXT,
    city             TEXT,
    seller           TEXT,
    seller_verified  INTEGER DEFAULT 0,
    price_native     REAL,
    price_currency   TEXT,
    price_eur        REAL,
    price_try        REAL,
    photo_count      INTEGER DEFAULT 0,
    image_urls       TEXT,
    raw_props        TEXT,
    is_holdout       INTEGER DEFAULT 0,
    detail_fetched   INTEGER DEFAULT 0,
    scraped_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_listings_make  ON listings(make);
CREATE INDEX IF NOT EXISTS idx_listings_spec  ON listings(make, model_family, year);
CREATE INDEX IF NOT EXISTS idx_listings_price ON listings(price_eur);
CREATE INDEX IF NOT EXISTS idx_listings_cat   ON listings(category);

CREATE TABLE IF NOT EXISTS listing_images (
    listing_id  TEXT NOT NULL,
    idx         INTEGER NOT NULL,
    url         TEXT,
    local_path  TEXT,
    phash       TEXT,
    width       INTEGER,
    height      INTEGER,
    PRIMARY KEY (listing_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_images_phash ON listing_images(phash);
CREATE INDEX IF NOT EXISTS idx_images_lid   ON listing_images(listing_id);

CREATE TABLE IF NOT EXISTS scrape_log (
    url        TEXT PRIMARY KEY,
    status     INTEGER,
    n_items    INTEGER,
    fetched_at TEXT
);
"""

LISTING_COLUMNS = [
    "listing_id",
    "source",
    "category",
    "url",
    "title",
    "make",
    "model_family",
    "model_variant",
    "body_type",
    "year",
    "km",
    "power_hp",
    "euro_class",
    "axle_config",
    "suspension",
    "fuel",
    "load_capacity_kg",
    "condition_flag",
    "country",
    "city",
    "seller",
    "seller_verified",
    "price_native",
    "price_currency",
    "price_eur",
    "price_try",
    "photo_count",
    "image_urls",
    "raw_props",
    "scraped_at",
]


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init(path: Path | None = None) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def upsert_listings(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    payload = []
    for row in rows:
        rec = {k: row.get(k) for k in LISTING_COLUMNS}
        for key in ("image_urls", "raw_props"):
            if isinstance(rec[key], (list, dict)):
                rec[key] = json.dumps(rec[key], ensure_ascii=False)
        payload.append(tuple(rec[k] for k in LISTING_COLUMNS))
    if not payload:
        return 0
    placeholders = ",".join("?" * len(LISTING_COLUMNS))
    cols = ",".join(LISTING_COLUMNS)
    # Existing rows are refreshed but the holdout flag and any fetched detail
    # state are preserved.
    updates = ",".join(f"{c}=excluded.{c}" for c in LISTING_COLUMNS if c != "listing_id")
    conn.executemany(
        f"INSERT INTO listings ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(listing_id) DO UPDATE SET {updates}",
        payload,
    )
    conn.commit()
    return len(payload)


def upsert_images(conn: sqlite3.Connection, rows: Iterable[tuple]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO listing_images (listing_id, idx, url, local_path, phash, width, height) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(listing_id, idx) DO UPDATE SET "
        "url=excluded.url, local_path=excluded.local_path, phash=excluded.phash, "
        "width=excluded.width, height=excluded.height",
        rows,
    )
    conn.commit()
    return len(rows)


def log_fetch(conn: sqlite3.Connection, url: str, status: int, n_items: int) -> None:
    from datetime import datetime, timezone

    conn.execute(
        "INSERT INTO scrape_log (url, status, n_items, fetched_at) VALUES (?,?,?,?) "
        "ON CONFLICT(url) DO UPDATE SET status=excluded.status, n_items=excluded.n_items, "
        "fetched_at=excluded.fetched_at",
        (url, status, n_items, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def stats(conn: sqlite3.Connection) -> dict:
    def one(sql: str, *args):
        cur = conn.execute(sql, args)
        row = cur.fetchone()
        return row[0] if row else 0

    return {
        "listings": one("SELECT COUNT(*) FROM listings"),
        "with_price": one("SELECT COUNT(*) FROM listings WHERE price_eur IS NOT NULL AND price_eur > 0"),
        "with_year_km": one(
            "SELECT COUNT(*) FROM listings WHERE price_eur > 0 AND year > 1980 AND km > 0"
        ),
        "images": one("SELECT COUNT(*) FROM listing_images WHERE local_path IS NOT NULL"),
        "sources": {
            r["source"]: r["n"]
            for r in conn.execute("SELECT source, COUNT(*) n FROM listings GROUP BY source")
        },
        "categories": {
            r["category"]: r["n"]
            for r in conn.execute(
                "SELECT category, COUNT(*) n FROM listings GROUP BY category ORDER BY n DESC"
            )
        },
    }
