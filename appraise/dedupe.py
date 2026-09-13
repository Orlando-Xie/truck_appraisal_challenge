"""Perceptual-hash matching of uploaded photos against the scraped corpus.

Two things fall out of this, both useful in a real marketplace:

* a seller who has lifted photos from someone else's advert is caught, and the
  original advert and its asking price can be shown;
* if the photos do come from a known advert, that advert's asking price is a
  genuine data point about this exact truck.

pHash is used rather than a cryptographic hash so that re-encoding, resizing and
light cropping still match.
"""

from __future__ import annotations

import logging

import imagehash

import config
from scrape import db
from vision.quality import load_rgb
from vision.schemas import DuplicatePhotoMatch

log = logging.getLogger(__name__)


def phash_of(raw: bytes) -> str | None:
    try:
        return str(imagehash.phash(load_rgb(raw)))
    except Exception:
        return None


def _hamming(a: str, b: str) -> int:
    try:
        return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)
    except Exception:
        return 999


def find_duplicates(images: list[tuple[str, bytes]], max_distance: int | None = None) -> list[DuplicatePhotoMatch]:
    """Match uploads against every hashed corpus image."""
    max_distance = max_distance if max_distance is not None else config.PHASH_MATCH_DISTANCE

    upload_hashes: list[tuple[str, str]] = []
    for filename, blob in images:
        h = phash_of(blob)
        if h:
            upload_hashes.append((filename, h))
    if not upload_hashes:
        return []

    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT i.listing_id, i.phash, l.source, l.url, l.title, l.price_eur "
            "FROM listing_images i JOIN listings l ON l.listing_id = i.listing_id "
            "WHERE i.phash IS NOT NULL"
        ).fetchall()
    except Exception as exc:
        log.warning("duplicate check skipped: %s", exc)
        return []
    finally:
        conn.close()

    matches: list[DuplicatePhotoMatch] = []
    seen_pairs: set[tuple[str, str]] = set()
    for filename, uh in upload_hashes:
        best: tuple[int, dict] | None = None
        for row in rows:
            d = _hamming(uh, row["phash"])
            if d <= max_distance and (best is None or d < best[0]):
                best = (d, dict(row))
        if best:
            d, row = best
            key = (filename, row["listing_id"])
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            matches.append(
                DuplicatePhotoMatch(
                    uploaded_filename=filename,
                    listing_id=row["listing_id"],
                    source=row["source"] or "",
                    url=row["url"] or "",
                    title=row["title"] or "",
                    price_eur=row["price_eur"],
                    hamming_distance=d,
                )
            )
    return matches
