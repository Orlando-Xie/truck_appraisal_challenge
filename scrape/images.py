"""Download listing photos, downscale them and record a perceptual hash.

Two tiers are used:

* the whole priced corpus gets a few small images, which is enough for the
  comparables thumbnails in the UI and for perceptual-hash matching (pHash
  downsamples to 32x32 internally, so a 400px source loses nothing);
* the evaluation holdout gets larger images, because the pipeline has to
  actually appraise from them.

Usage::

    python -m scrape.images --max-per-listing 3 --edge 400 --limit 5000
    python -m scrape.images --holdout-only --max-per-listing 6 --edge 1100
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import sys
from pathlib import Path

import httpx
import imagehash
from PIL import Image, ImageFile

import config
from scrape import db

ImageFile.LOAD_TRUNCATED_IMAGES = True

log = logging.getLogger("images")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://autoline.info/",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


def process_bytes(raw: bytes, dest: Path, max_edge: int) -> tuple[str, int, int] | None:
    """Downscale, save as JPEG and return (phash, width, height)."""
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return None
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")

    # Reject placeholder / spacer images.
    if img.width < 80 or img.height < 60:
        return None

    phash = str(imagehash.phash(img))

    w, h = img.size
    if max(w, h) > max_edge:
        scale = max_edge / max(w, h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=84, optimize=True)
    return phash, img.width, img.height


async def fetch_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    listing_id: str,
    idx: int,
    url: str,
    max_edge: int,
) -> tuple | None:
    dest = config.IMAGE_DIR / listing_id / f"{idx}.jpg"
    if dest.exists() and dest.stat().st_size > 1000:
        try:
            with Image.open(dest) as img:
                return (listing_id, idx, url, str(dest.relative_to(config.DATA_DIR)), str(imagehash.phash(img)), img.width, img.height)
        except Exception:
            pass
    async with sem:
        for attempt in range(3):
            try:
                resp = await client.get(url)
                if resp.status_code == 200 and resp.content:
                    break
                if resp.status_code in (404, 403, 410):
                    return None
            except Exception as exc:
                if attempt == 2:
                    log.debug("%s idx%d failed: %s", listing_id, idx, exc)
                    return None
            await asyncio.sleep(0.6 * (attempt + 1))
        else:
            return None

    result = await asyncio.to_thread(process_bytes, resp.content, dest, max_edge)
    if result is None:
        return None
    phash, w, h = result
    return (listing_id, idx, url, str(dest.relative_to(config.DATA_DIR)), phash, w, h)


async def run(
    max_per_listing: int, edge: int, limit: int, holdout_only: bool, concurrency: int
) -> None:
    conn = db.connect()
    where = "json_array_length(image_urls) > 0"
    if holdout_only:
        where += " AND is_holdout = 1"
    else:
        where += " AND price_eur > 0"
    rows = conn.execute(
        f"SELECT listing_id, image_urls FROM listings WHERE {where} "
        f"ORDER BY is_holdout DESC, listing_id LIMIT ?",
        (limit,),
    ).fetchall()
    log.info("%d listings to fetch images for (max %d each, edge %d)", len(rows), max_per_listing, edge)

    jobs: list[tuple[str, int, str]] = []
    for row in rows:
        try:
            urls = json.loads(row["image_urls"] or "[]")
        except json.JSONDecodeError:
            continue
        for i, url in enumerate(urls[:max_per_listing]):
            if isinstance(url, str) and url.startswith("http"):
                jobs.append((row["listing_id"], i, url))

    log.info("%d image jobs", len(jobs))
    sem = asyncio.Semaphore(concurrency)
    done = 0
    batch: list[tuple] = []
    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(headers=HEADERS, timeout=25, limits=limits, follow_redirects=True) as client:
        chunk = 300
        for start in range(0, len(jobs), chunk):
            tasks = [
                fetch_one(client, sem, lid, idx, url, edge)
                for lid, idx, url in jobs[start : start + chunk]
            ]
            for result in await asyncio.gather(*tasks, return_exceptions=True):
                if isinstance(result, tuple):
                    batch.append(result)
                done += 1
            db.upsert_images(conn, batch)
            batch = []
            log.info("%d/%d images processed", done, len(jobs))

    stats = db.stats(conn)
    log.info("images on disk: %d", stats["images"])
    conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Download listing photos")
    ap.add_argument("--max-per-listing", type=int, default=3)
    ap.add_argument("--edge", type=int, default=400)
    ap.add_argument("--limit", type=int, default=6000)
    ap.add_argument("--holdout-only", action="store_true")
    ap.add_argument("--concurrency", type=int, default=12)
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout
    )
    db.init()
    asyncio.run(
        run(args.max_per_listing, args.edge, args.limit, args.holdout_only, args.concurrency)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
