"""Blind holdout evaluation.

Two numbers, kept separate on purpose:

* **spec-oracle** -- price the holdout from the listing's own year/make/km. This
  is the accuracy of the statistical model when identification is perfect, and
  it is the number that answers "does the pricing work?".
* **photos-only** -- run the full pipeline on the listing's photos with no typed
  details. This is the accuracy of the *system*, and it needs a real vision
  provider; the stub would just invent identities.

The interval width is then recalibrated so that about 80 percent of holdout
asking prices fall inside the reported range.

Usage::

    python -m eval.holdout
    python -m eval.holdout --photos   # only if a vision API key is configured
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import config
from appraise import orchestrator
from pricing import predict
from scrape import db
from vision.client import get_client
from vision.schemas import SellerClaims

log = logging.getLogger("eval")
REPORT_PATH = Path(__file__).with_name("report.md")
METRICS_PATH = config.MODEL_DIR / "holdout_metrics.json"


def _holdout_rows(limit: int) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT listing_id, make, model_family, model_variant, year, km, power_hp, "
            "euro_class, axle_config, body_type, price_eur, country, url "
            "FROM listings WHERE is_holdout = 1 AND price_eur > 0 "
            "ORDER BY listing_id LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _photos_for(listing_id: str, n: int = 5) -> list[tuple[str, bytes]]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT local_path FROM listing_images WHERE listing_id = ? AND local_path IS NOT NULL "
            "ORDER BY idx LIMIT ?",
            (listing_id, n),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for i, row in enumerate(rows):
        path = config.DATA_DIR / row["local_path"]
        if path.exists():
            out.append((f"photo_{i + 1}.jpg", path.read_bytes()))
    return out


def _summarise(apes: np.ndarray, insides: np.ndarray, widths: np.ndarray, prices: np.ndarray, mids: np.ndarray) -> dict:
    return {
        "n": int(len(apes)),
        "median_ape": float(np.median(apes)),
        "mean_ape": float(np.mean(apes)),
        "within_10pct": float(np.mean(apes <= 0.10)),
        "within_20pct": float(np.mean(apes <= 0.20)),
        "within_30pct": float(np.mean(apes <= 0.30)),
        "interval_coverage": float(np.mean(insides)),
        "median_interval_width_ratio": float(np.median(widths)),
        "median_true_eur": float(np.median(prices)),
        "median_pred_eur": float(np.median(mids)),
    }


def spec_oracle(rows: list[dict]) -> tuple[dict, list[dict]]:
    """Price each holdout listing from its own specification."""
    apes, insides, widths, prices, mids = [], [], [], [], []
    details = []
    for row in rows:
        spec = {
            "make": row["make"],
            "model_family": row["model_family"],
            "model_variant": row["model_variant"],
            "body_type": row["body_type"] or "tractor_unit",
            "axle_config": row["axle_config"] or "",
            "euro_class": row["euro_class"] or "",
            "power_hp": row["power_hp"],
            "year": row["year"],
            "km": row["km"],
            "condition": "used",
            "country": row["country"] or "Poland",
        }
        try:
            band, _ = predict.baseline_range(spec)
        except Exception as exc:
            log.debug("skip %s: %s", row["listing_id"], exc)
            continue
        true = float(row["price_eur"])
        ape = abs(band.mid - true) / true
        inside = band.low <= true <= band.high
        width = (band.high - band.low) / max(band.mid, 1)
        apes.append(ape)
        insides.append(inside)
        widths.append(width)
        prices.append(true)
        mids.append(band.mid)
        details.append(
            {
                "listing_id": row["listing_id"],
                "true_eur": true,
                "pred_mid": round(band.mid),
                "pred_low": round(band.low),
                "pred_high": round(band.high),
                "ape": round(ape, 4),
                "inside": bool(inside),
            }
        )
    if not apes:
        return {}, []
    return _summarise(np.array(apes), np.array(insides), np.array(widths), np.array(prices), np.array(mids)), details


def calibrate_from_residuals(details: list[dict], target: float = 0.80) -> float:
    """Find a multiplicative widening of the reported interval that hits coverage."""
    best, gap = 1.0, 9.9
    for factor in np.arange(0.6, 3.01, 0.05):
        hits = 0
        n = 0
        for d in details:
            mid = d["pred_mid"]
            if mid <= 0:
                continue
            lo = mid * (max(d["pred_low"], 1) / mid) ** factor
            hi = mid * (max(d["pred_high"], 1) / mid) ** factor
            n += 1
            if lo <= d["true_eur"] <= hi:
                hits += 1
        if n == 0:
            continue
        g = abs(hits / n - target)
        if g < gap:
            best, gap = float(factor), g
    return best


async def photos_only(rows: list[dict], limit: int) -> tuple[dict, list[dict]]:
    client = get_client()
    await client.ensure_ready()
    if client.is_stub:
        log.warning("vision provider is the stub; photos-only eval would be meaningless")
        return {}, []

    apes, insides, widths, prices, mids = [], [], [], [], []
    details = []
    used = 0
    for row in rows:
        if used >= limit:
            break
        photos = _photos_for(row["listing_id"])
        if len(photos) < 2:
            continue
        used += 1
        try:
            result = await orchestrator.appraise(photos, claims=None, use_cache=False)
        except Exception as exc:
            log.warning("%s failed: %s", row["listing_id"], exc)
            continue
        if result.status == "refused" or result.price_eur is None:
            details.append(
                {
                    "listing_id": row["listing_id"],
                    "true_eur": row["price_eur"],
                    "status": result.status,
                    "refusal": result.refusal.code if result.refusal else None,
                }
            )
            continue
        true = float(row["price_eur"])
        mid = result.price_eur.mid
        ape = abs(mid - true) / true
        inside = result.price_eur.low <= true <= result.price_eur.high
        width = (result.price_eur.high - result.price_eur.low) / max(mid, 1)
        apes.append(ape)
        insides.append(inside)
        widths.append(width)
        prices.append(true)
        mids.append(mid)
        details.append(
            {
                "listing_id": row["listing_id"],
                "true_eur": true,
                "pred_mid": round(mid),
                "pred_low": round(result.price_eur.low),
                "pred_high": round(result.price_eur.high),
                "ape": round(ape, 4),
                "inside": bool(inside),
                "identified_as": (
                    f"{result.identification.make} {result.identification.model_family}".strip()
                    if result.identification
                    else ""
                ),
            }
        )
        log.info("%s  true=%s  pred=%s  ape=%.1f%%", row["listing_id"], true, round(mid), 100 * ape)

    if not apes:
        return {}, details
    return _summarise(np.array(apes), np.array(insides), np.array(widths), np.array(prices), np.array(mids)), details


def write_report(payload: dict) -> None:
    oracle = payload.get("spec_oracle") or {}
    photos = payload.get("photos_only") or {}
    lines = [
        "# Holdout evaluation",
        "",
        f"Generated {payload.get('generated_at', '')}.",
        "",
        "The evaluation holdout was excluded from training before the model was fitted.",
        "Two numbers are reported, because they answer different questions.",
        "",
        "## Spec-oracle: does the pricing model work?",
        "",
        "Each holdout listing is priced from its own year, make, model, mileage, axle",
        "configuration and power -- as if identification were perfect. This is the",
        "accuracy of the statistical model.",
        "",
    ]
    if oracle:
        lines += [
            f"- Listings evaluated: **{oracle['n']}**",
            f"- Median absolute percentage error: **{100 * oracle['median_ape']:.1f}%**",
            f"- Within 20% of asking price: **{100 * oracle['within_20pct']:.0f}%**",
            f"- True asking price inside the reported interval: **{100 * oracle['interval_coverage']:.0f}%**",
            f"- Median interval width: **{100 * oracle['median_interval_width_ratio']:.0f}%** of the midpoint",
            f"- Recalibration factor for 80% coverage: **{payload.get('recalibration_factor', 1):.2f}**",
            "",
        ]
    else:
        lines += ["No spec-oracle results.", ""]

    lines += [
        "## Photos-only: does the whole system work?",
        "",
        "The pipeline sees the listing's photos and nothing else -- no year, make or",
        "mileage. This is the number that answers the challenge as written.",
        "",
    ]
    if photos:
        lines += [
            f"- Listings evaluated: **{photos['n']}**",
            f"- Median absolute percentage error: **{100 * photos['median_ape']:.1f}%**",
            f"- Within 20% of asking price: **{100 * photos['within_20pct']:.0f}%**",
            f"- True asking price inside the reported interval: **{100 * photos['interval_coverage']:.0f}%**",
            "",
        ]
    else:
        lines += [
            "Not run. Configure `GEMINI_API_KEY` and rerun with `--photos`.",
            "",
        ]

    lines += [
        "## What this does and does not mean",
        "",
        "Asking price is not transaction price, so a perfect model would still have residual",
        "error: two identical trucks are advertised at different numbers. The interval is",
        "calibrated against that residual, which is why it is wide, and why the condition",
        "photos are then used to place a specific truck inside it rather than to invent a",
        "tighter number from nowhere.",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    log.info("wrote %s", REPORT_PATH)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=130)
    ap.add_argument("--photos", action="store_true")
    ap.add_argument("--photos-limit", type=int, default=40)
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout
    )

    rows = _holdout_rows(args.limit)
    if not rows:
        log.error("no holdout listings; train the model first")
        return 1
    log.info("%d holdout listings", len(rows))

    oracle, oracle_details = spec_oracle(rows)
    log.info("spec-oracle: %s", json.dumps(oracle))
    factor = calibrate_from_residuals(oracle_details, target=0.80) if oracle_details else 1.0
    log.info("holdout recalibration factor %.2f", factor)

    photos_metrics: dict = {}
    photos_details: list = []
    if args.photos:
        photos_metrics, photos_details = asyncio.run(photos_only(rows, args.photos_limit))
        log.info("photos-only: %s", json.dumps(photos_metrics))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spec_oracle": oracle,
        "photos_only": photos_metrics,
        "recalibration_factor": factor,
        "n_holdout": len(rows),
        "oracle_sample": oracle_details[:12],
        "photos_sample": photos_details[:12],
    }
    METRICS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_report(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
