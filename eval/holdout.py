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
            "FROM listings WHERE is_holdout = 1 AND price_eur >= 5000 "
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
                "make": row.get("make") or "",
                "model_family": row.get("model_family") or "",
                "body_type": row.get("body_type") or "",
                "has_km": bool(row.get("km")),
                "has_power": bool(row.get("power_hp")),
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


def slice_oracle(details: list[dict]) -> dict:
    """Break spec-oracle error down by the things that actually move the quote."""
    if not details:
        return {}

    def _group(key: str, empty: str = "unknown") -> dict:
        buckets: dict[str, list[float]] = {}
        for d in details:
            label = str(d.get(key) or empty)
            buckets.setdefault(label, []).append(float(d["ape"]))
        out = {}
        for label, apes in buckets.items():
            arr = np.array(apes)
            out[label] = {
                "n": int(len(arr)),
                "median_ape": float(np.median(arr)),
                "mean_ape": float(np.mean(arr)),
            }
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["median_ape"]))

    missing_km = [d["ape"] for d in details if not d.get("has_km")]
    has_km = [d["ape"] for d in details if d.get("has_km")]
    missing_power = [d["ape"] for d in details if not d.get("has_power")]
    has_power = [d["ape"] for d in details if d.get("has_power")]
    tail = sorted(details, key=lambda d: -d["ape"])[:8]
    return {
        "by_make": _group("make"),
        "by_family": {k: v for k, v in _group("model_family").items() if v["n"] >= 2},
        "by_body": _group("body_type", empty="tractor_unit"),
        "missing_km": {
            "n": len(missing_km),
            "median_ape": float(np.median(missing_km)) if missing_km else None,
            "with_km_median_ape": float(np.median(has_km)) if has_km else None,
        },
        "missing_power": {
            "n": len(missing_power),
            "median_ape": float(np.median(missing_power)) if missing_power else None,
            "with_power_median_ape": float(np.median(has_power)) if has_power else None,
        },
        "worst": [
            {
                "listing_id": d["listing_id"],
                "ape": d["ape"],
                "true_eur": d["true_eur"],
                "pred_mid": d["pred_mid"],
                "make": d.get("make"),
                "model_family": d.get("model_family"),
                "body_type": d.get("body_type"),
            }
            for d in tail
        ],
    }


def _rows_with_photos(rows: list[dict], limit: int) -> list[dict]:
    picked = []
    for row in rows:
        if len(_photos_for(row["listing_id"])) >= 2:
            picked.append(row)
        if len(picked) >= limit:
            break
    return picked


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
                "true_make": row.get("make") or "",
                "true_family": row.get("model_family") or "",
                "true_year": row.get("year"),
                "true_km": row.get("km"),
                "pred_year": (result.pricing_basis.spec_used.get("year") if result.pricing_basis else None),
                "pred_km": (result.pricing_basis.spec_used.get("km") if result.pricing_basis else None),
                "pred_year_high": result.identification.generation_year_high if result.identification else None,
                "make_match": (
                    (result.identification.make or "").lower() in (row.get("make") or "").lower()
                    or (row.get("make") or "").lower() in (result.identification.make or "").lower()
                    if result.identification and result.identification.make
                    else False
                ),
            }
        )
        log.info("%s  true=%s  pred=%s  ape=%.1f%%", row["listing_id"], true, round(mid), 100 * ape)

    if not apes:
        return {}, details
    summary = _summarise(np.array(apes), np.array(insides), np.array(widths), np.array(prices), np.array(mids))
    id_rows = [d for d in details if d.get("identified_as")]
    if id_rows:
        summary["make_match_rate"] = float(np.mean([1.0 if d.get("make_match") else 0.0 for d in id_rows]))
        year_err = [
            abs(int(d["pred_year"]) - int(d["true_year"]))
            for d in id_rows
            if d.get("pred_year") and d.get("true_year")
        ]
        if year_err:
            summary["median_year_abs_error"] = float(sorted(year_err)[len(year_err) // 2])
        km_err = [
            abs(float(d["pred_km"]) - float(d["true_km"])) / max(float(d["true_km"]), 1.0)
            for d in id_rows
            if d.get("pred_km") and d.get("true_km")
        ]
        if km_err:
            summary["median_km_ape"] = float(sorted(km_err)[len(km_err) // 2])
    return summary, details


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
        slices = payload.get("oracle_slices") or {}
        worst = slices.get("worst") or []
        if worst:
            lines += ["### Spec-oracle error slices", ""]
            by_body = slices.get("by_body") or {}
            if by_body:
                lines.append("By body type:")
                for body, stats in list(by_body.items())[:8]:
                    lines.append(
                        f"- `{body}`: n={stats['n']}, median APE {100 * stats['median_ape']:.0f}%"
                    )
                lines.append("")
            by_make = slices.get("by_make") or {}
            if by_make:
                lines.append("By make:")
                for make, stats in list(by_make.items())[:8]:
                    lines.append(
                        f"- `{make}`: n={stats['n']}, median APE {100 * stats['median_ape']:.0f}%"
                    )
                lines.append("")
            lines.append("Worst misses:")
            for d in worst[:6]:
                lines.append(
                    f"- {d['listing_id']} {d.get('make')} {d.get('model_family')} "
                    f"true EUR {d['true_eur']:.0f} pred {d['pred_mid']} "
                    f"APE {100 * d['ape']:.0f}%"
                )
            lines.append("")
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
        if photos.get("make_match_rate") is not None:
            lines.append(f"- Make match rate: **{100 * photos['make_match_rate']:.0f}%**")
        if photos.get("median_year_abs_error") is not None:
            lines.append(f"- Median |year error|: **{photos['median_year_abs_error']:.0f} years**")
        if photos.get("median_km_ape") is not None:
            lines.append(f"- Median mileage APE (when both present): **{100 * photos['median_km_ape']:.0f}%**")
        lines.append("")
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
    ap.add_argument("--photos-limit", type=int, default=25, help="cap for the photos-only run (Gemini cost)")
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
    slices = slice_oracle(oracle_details)
    log.info("oracle slices: %s", json.dumps({k: slices.get(k) for k in ("by_body", "missing_km", "missing_power")}))

    if args.photos:
        photo_rows = _rows_with_photos(rows, args.photos_limit)
        log.info("photos-only on %d listings with local images", len(photo_rows))
        photos_metrics, photos_details = asyncio.run(photos_only(photo_rows, args.photos_limit))
        log.info("photos-only: %s", json.dumps(photos_metrics))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spec_oracle": oracle,
        "photos_only": photos_metrics,
        "recalibration_factor": factor,
        "n_holdout": len(rows),
        "oracle_slices": slices,
        "oracle_sample": oracle_details[:12],
        "photos_sample": photos_details[:12],
    }
    METRICS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_report(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
