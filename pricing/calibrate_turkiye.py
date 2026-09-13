"""Fit the Türkiye market multiplier.

The price model is trained in EUR on a mostly European corpus. Turkish asking
prices sit above that, for structural reasons (import duty, OTV, KDV, thinner
supply of clean used tractors). This script estimates a global multiplier:

    median( price_TRY / EURTRY / model_EUR )

and, when a slice has at least three seeds, a make x age-band multiplier so a
2022 BMC tipper is not priced with the same premium as a 2016 4x2 Actros.

Sources, in preference order:

1. validated Arabam metadata at `data_handoff/arabam_metadata/manifest.jsonl`
   (CSV fallback), using only `price_label_usable == true` rows
2. arabam.com listings already in SQLite (from `python -m scrape.arabam`)
3. `pricing/turkiye_manual.json`, a seed table of public asking prices

Usage::

    python -m pricing.calibrate_turkiye
"""

from __future__ import annotations

import csv
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

import config
from pricing import fx, predict
from scrape import db, normalize

log = logging.getLogger("calibrate")

MANUAL_PATH = Path(__file__).with_name("turkiye_manual.json")
ARABAM_MANIFEST_DIR = config.ROOT / "data_handoff" / "arabam_metadata"
ARABAM_MANIFEST_JSONL = ARABAM_MANIFEST_DIR / "manifest.jsonl"
ARABAM_MANIFEST_CSV = ARABAM_MANIFEST_DIR / "manifest.csv"
MIN_SEGMENT_N = 3


def _is_true(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() == "true"


def _as_number(value, kind):
    if value is None or value == "":
        return None
    try:
        return kind(value)
    except (TypeError, ValueError):
        try:
            return kind(float(value))
        except (TypeError, ValueError):
            return None


def _map_arabam_record(raw: dict) -> dict | None:
    """Map one validated Arabam listing onto the calibrator row schema.

    Missing km / axle / euro / power are left unset. Category is passed through
    as body_type so Çekici can canonicalise to tractor_unit without inventing
    a body for other categories.
    """
    if not _is_true(raw.get("price_label_usable")):
        return None
    price_try = _as_number(raw.get("price_try"), float)
    if price_try is None:
        return None
    year = _as_number(raw.get("year"), int)
    km = _as_number(raw.get("mileage_km"), int)
    return {
        "make": raw.get("make"),
        "model_family": raw.get("model"),
        "model_variant": raw.get("variant"),
        "year": year,
        "km": km,
        "price_try": price_try,
        "body_type": raw.get("category") or raw.get("body_type"),
        "source": raw.get("source") or "arabam",
        "listing_id": raw.get("listing_id"),
        "listing_url": raw.get("listing_url") or raw.get("canonical_url"),
        "listing_scope": raw.get("listing_scope"),
    }


def _load_arabam_manifest_records() -> list[dict]:
    if ARABAM_MANIFEST_JSONL.exists():
        records = []
        with ARABAM_MANIFEST_JSONL.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                records.append(json.loads(line))
        return records
    if ARABAM_MANIFEST_CSV.exists():
        with ARABAM_MANIFEST_CSV.open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    return []


def _rows_from_arabam_manifest() -> tuple[list[dict], dict]:
    records = _load_arabam_manifest_records()
    stats = {
        "manifest_rows": len(records),
        "usable": 0,
        "excluded_unusable_price": 0,
        "excluded_unusable_reasons": Counter(),
        "excluded_missing_price_try": 0,
    }
    rows: list[dict] = []
    for raw in records:
        if not _is_true(raw.get("price_label_usable")):
            stats["excluded_unusable_price"] += 1
            reason = raw.get("price_unusable_reason") or raw.get("listing_scope") or "price_label_usable!=true"
            stats["excluded_unusable_reasons"][str(reason)] += 1
            continue
        mapped = _map_arabam_record(raw)
        if mapped is None:
            stats["excluded_missing_price_try"] += 1
            continue
        stats["usable"] += 1
        rows.append(mapped)
    return rows, stats


def _rows_from_db() -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT make, model_family, model_variant, year, km, power_hp, euro_class, "
            "axle_config, body_type, price_try, price_native, price_currency "
            "FROM listings WHERE source = 'arabam' AND year > 1995 AND km > 1000 "
            "AND (price_try > 50000 OR (price_currency = 'TRY' AND price_native > 50000))"
        ).fetchall()
        out = []
        for r in rows:
            rec = dict(r)
            rec["price_try"] = rec["price_try"] or rec["price_native"]
            rec["body_type"] = rec["body_type"] or "tractor_unit"
            out.append(rec)
        return out
    finally:
        conn.close()


def _rows_from_manual() -> list[dict]:
    if not MANUAL_PATH.exists():
        return []
    data = json.loads(MANUAL_PATH.read_text(encoding="utf-8"))
    return list(data.get("listings") or [])


def _eur_try() -> float:
    if MANUAL_PATH.exists():
        data = json.loads(MANUAL_PATH.read_text(encoding="utf-8"))
        if data.get("eur_try"):
            return float(data["eur_try"])
    return config.DEFAULT_EUR_TRY


def _ratio_rows(rows: list[dict], eur_try: float) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        try:
            price_try = float(row["price_try"])
        except (KeyError, TypeError, ValueError):
            continue
        spec = {
            "make": row.get("make"),
            "model_family": row.get("model_family"),
            "model_variant": row.get("model_variant"),
            "family_canon": normalize.canonical_family(
                row.get("make"), row.get("model_family"), row.get("model_variant")
            ),
            "body_type": row.get("body_type") or "tractor_unit",
            "axle_config": row.get("axle_config") or "4x2",
            "euro_class": row.get("euro_class") or "",
            "power_hp": row.get("power_hp"),
            "year": row.get("year"),
            "km": row.get("km"),
            "condition": "used",
            "country": "Poland",  # European baseline, not the Turkish one
        }
        try:
            band, _ = predict.baseline_range(spec)
        except Exception as exc:
            log.debug("skip %s: %s", row.get("make"), exc)
            continue
        if band.mid <= 0:
            continue
        implied_eur = price_try / eur_try
        ratio = implied_eur / band.mid
        if 0.4 <= ratio <= 4.0:
            year = row.get("year")
            try:
                year_i = int(year) if year else None
            except (TypeError, ValueError):
                year_i = None
            make = normalize.canonical_make(row.get("make")) or str(row.get("make") or "unknown")
            out.append(
                {
                    "make": make,
                    "year": year_i,
                    "age_band": fx.age_band(year_i),
                    "body": spec["body_type"],
                    "ratio": ratio,
                }
            )
    return out


def _fit_segments(ratio_rows: list[dict]) -> dict:
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in ratio_rows:
        buckets[row["make"]].append(row["ratio"])
        buckets[row["age_band"]].append(row["ratio"])
        buckets[f"{row['make']}|{row['age_band']}"].append(row["ratio"])
    segments = {}
    for key, vals in buckets.items():
        if len(vals) < MIN_SEGMENT_N:
            continue
        arr = np.array(vals)
        segments[key] = {
            "n": int(len(arr)),
            "multiplier": round(float(np.median(arr)), 4),
            "p25": round(float(np.quantile(arr, 0.25)), 3),
            "p75": round(float(np.quantile(arr, 0.75)), 3),
        }
    return segments


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout
    )
    eur_try = _eur_try()
    manifest_rows, manifest_stats = _rows_from_arabam_manifest()
    db_rows = _rows_from_db() if not manifest_rows else []
    manual_rows = _rows_from_manual()

    if manifest_rows:
        source = "arabam_manifest"
        rows = manifest_rows
        log.info(
            "using %d/%d validated arabam listings (price_label_usable=true); "
            "excluded_unusable=%d reasons=%s missing_price_try=%d",
            manifest_stats["usable"],
            manifest_stats["manifest_rows"],
            manifest_stats["excluded_unusable_price"],
            dict(manifest_stats["excluded_unusable_reasons"]),
            manifest_stats["excluded_missing_price_try"],
        )
    elif db_rows:
        source = "arabam"
        rows = db_rows
        log.info("using %d arabam listings", len(rows))
    elif manual_rows:
        source = "manual_seed"
        rows = manual_rows
        log.info("no arabam listings in SQLite; using %d manual seed prices", len(rows))
    else:
        log.error("no Turkish prices available")
        return 1

    try:
        predict.load_artifact()
    except predict.ModelUnavailable as exc:
        log.error("%s", exc)
        return 1

    ratio_rows = _ratio_rows(rows, eur_try)
    dropped = len(rows) - len(ratio_rows)
    if dropped:
        log.info(
            "excluded %d mapped rows at scoring (model skip, non-positive midpoint, or ratio outside 0.4-4.0)",
            dropped,
        )
    if len(ratio_rows) < 8:
        log.error("only %d usable ratios; need at least 8", len(ratio_rows))
        return 1

    arr = np.array([r["ratio"] for r in ratio_rows])
    multiplier = float(np.median(arr))
    segments = _fit_segments(ratio_rows)
    notes = (
        f"Fitted as the median of (Turkish asking price / EURTRY / European-model midpoint) "
        f"on {len(ratio_rows)} listings. Quartiles {float(np.quantile(arr, 0.25)):.2f}-"
        f"{float(np.quantile(arr, 0.75)):.2f}. Source: {source}. "
        f"{len(segments)} make/age segments with at least {MIN_SEGMENT_N} seeds."
    )
    fx.save_calibration(multiplier, eur_try, len(ratio_rows), notes, source=source, segments=segments)
    log.info(
        "turkiye_multiplier=%.3f  eur_try=%.2f  n=%d  p25=%.2f p75=%.2f  segments=%d",
        multiplier,
        eur_try,
        len(ratio_rows),
        float(np.quantile(arr, 0.25)),
        float(np.quantile(arr, 0.75)),
        len(segments),
    )
    for key, row in sorted(segments.items(), key=lambda kv: -kv[1]["n"]):
        log.info("  segment %-28s n=%d  x%.3f", key, row["n"], row["multiplier"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
