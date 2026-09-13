"""Fit the Türkiye market multiplier.

The price model is trained in EUR on a mostly European corpus. Turkish asking
prices sit above that, for structural reasons (import duty, OTV, KDV, thinner
supply of clean used tractors). This script estimates a single multiplier:

    median( price_TRY / EURTRY / model_EUR )

so the conversion is one number a dealer can argue with, not a black box.

Sources, in preference order:

1. arabam.com listings already in SQLite (from `python -m scrape.arabam`)
2. `pricing/turkiye_manual.json`, a seed table of public asking prices

Usage::

    python -m pricing.calibrate_turkiye
"""

from __future__ import annotations

import json
import logging
import math
import sys
from pathlib import Path

import numpy as np

import config
from pricing import fx, predict
from scrape import db, normalize

log = logging.getLogger("calibrate")

MANUAL_PATH = Path(__file__).with_name("turkiye_manual.json")


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


def _ratios(rows: list[dict], eur_try: float) -> list[float]:
    ratios: list[float] = []
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
            ratios.append(ratio)
    return ratios


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout
    )
    eur_try = _eur_try()
    db_rows = _rows_from_db()
    manual_rows = _rows_from_manual()

    if db_rows:
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

    ratios = _ratios(rows, eur_try)
    if len(ratios) < 8:
        log.error("only %d usable ratios; need at least 8", len(ratios))
        return 1

    arr = np.array(ratios)
    multiplier = float(np.median(arr))
    notes = (
        f"Fitted as the median of (Turkish asking price / EURTRY / European-model midpoint) "
        f"on {len(ratios)} listings. Quartiles {float(np.quantile(arr, 0.25)):.2f}–"
        f"{float(np.quantile(arr, 0.75)):.2f}. Source: {source}."
    )
    fx.save_calibration(multiplier, eur_try, len(ratios), notes, source=source)
    log.info(
        "turkiye_multiplier=%.3f  eur_try=%.2f  n=%d  p25=%.2f p75=%.2f",
        multiplier,
        eur_try,
        len(ratios),
        float(np.quantile(arr, 0.25)),
        float(np.quantile(arr, 0.75)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
