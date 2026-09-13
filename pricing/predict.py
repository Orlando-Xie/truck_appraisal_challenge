"""Inference side of the pricing model.

The pipeline is:

1. ``baseline_range``  -- the market band for this specification (wide, because
   identically specified trucks are advertised at very different prices).
2. ``blend_with_comps`` -- nudge the band toward the median of the comparables
   actually retrieved, guarding against the model extrapolating.
3. ``widen``           -- widen the band for uncertainty about *what the truck is*
   (unidentified model, no odometer, missing views).
4. ``positioning.position`` -- locate the truck inside the band using the
   condition rubric, and set how tight the reported interval may be.
5. ``apply_deductions`` -- subtract the discrete repair bills.

The ordering matters. Uncertainty about identity widens the band; knowledge about
condition narrows and moves it. Conflating the two is how a system ends up
confidently pricing a truck it cannot identify.
"""

from __future__ import annotations

import logging
import math
from functools import lru_cache

import joblib
import numpy as np

import config
from pricing import comps as comps_mod
from pricing import features as F
from vision.schemas import PriceRange

log = logging.getLogger(__name__)


class ModelUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def load_artifact() -> dict:
    path = config.MODEL_DIR / "price_model.joblib"
    if not path.exists():
        raise ModelUnavailable(
            f"no trained price model at {path}; run `python -m pricing.train` after scraping"
        )
    return joblib.load(path)


def model_info() -> dict:
    try:
        art = load_artifact()
    except ModelUnavailable:
        return {"available": False}
    return {
        "available": True,
        "trained_at": art.get("trained_at", ""),
        "n_train": art.get("n_train", 0),
        "n_test": art.get("n_test", 0),
        "metrics_test": art.get("metrics_test", {}),
        "interval_factor": art.get("interval_factor", 1.0),
    }


def baseline_range(spec: dict) -> tuple[PriceRange, dict]:
    """Market baseline in EUR for this specification, before any condition work."""
    art = load_artifact()
    row = F.spec_to_row(spec)
    X = F.as_model_matrix(row, art["categories"])

    log_q10 = float(art["models"]["q10"].predict(X)[0])
    log_q50 = float(art["models"]["q50"].predict(X)[0])
    log_q90 = float(art["models"]["q90"].predict(X)[0])

    mid = math.exp(log_q50)
    lo = math.exp(log_q10)
    hi = math.exp(log_q90)

    # Quantile regressors on a log target are usually over-confident; this factor
    # was fitted on held-out data to hit ~80% coverage.
    factor = float(art.get("interval_factor", 1.0))
    lo = mid * (max(lo, 1.0) / mid) ** factor
    hi = mid * (max(hi, 1.0) / mid) ** factor

    lo, hi = min(lo, mid), max(hi, mid)
    diagnostics = {
        "raw_q10": round(math.exp(log_q10)),
        "raw_q50": round(mid),
        "raw_q90": round(math.exp(log_q90)),
        "interval_factor": factor,
        "features_used": {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in row[F.FEATURES].iloc[0].to_dict().items()},
    }
    return PriceRange(low=lo, mid=mid, high=hi, currency="EUR"), diagnostics


def blend_with_comps(base: PriceRange, comp_stats: dict, weight: float = 0.30) -> tuple[PriceRange, str | None]:
    """Pull the midpoint toward the median of the comparables actually retrieved.

    The model generalises across the whole corpus; the comparables are the few
    listings closest to this exact truck. Blending guards against the model
    extrapolating into a thin region of the feature space.
    """
    median = comp_stats.get("median_eur")
    n = comp_stats.get("n", 0)
    if not median or n < 3:
        return base, None

    tightness = float(comp_stats.get("median_similarity") or 0.0)
    # Raise the blend when at least five tight comps sit next to this spec.
    if n >= 5 and tightness >= 0.55:
        weight = max(weight, 0.45)

    # Trust the comparables more when there are more of them.
    w = weight * min(1.0, n / 8.0)
    blended_mid = (1 - w) * base.mid + w * float(median)
    shift = blended_mid / base.mid if base.mid else 1.0
    note = None
    if abs(shift - 1.0) > 0.06:
        direction = "up" if shift > 1 else "down"
        note = (
            f"Midpoint moved {direction} by {abs(shift - 1) * 100:.0f}% toward the median of the "
            f"{n} closest comparable listings (EUR {int(median):,})".replace(",", " ")
        )
    return (
        PriceRange(
            low=base.low * shift,
            mid=blended_mid,
            high=base.high * shift,
            currency="EUR",
        ),
        note,
    )


def widen(base: PriceRange, total_pct: float) -> PriceRange:
    """Widen an interval symmetrically about its midpoint."""
    if total_pct <= 0:
        return base
    f = total_pct / 100.0
    return PriceRange(
        low=max(0.0, base.mid - (base.mid - base.low) * (1 + f) - base.mid * f * 0.25),
        mid=base.mid,
        high=base.high + (base.high - base.mid) * f + base.mid * f * 0.25,
        currency=base.currency,
    )


def apply_deductions(base: PriceRange, total_deductions_eur: float) -> PriceRange:
    """Subtract condition deductions from the whole range.

    Deductions shift the range rather than only its midpoint: the repairs are
    needed whichever end of the market this truck sits at.
    """
    if total_deductions_eur <= 0:
        return base
    floor = base.mid * 0.25  # never write the vehicle down to nothing
    return PriceRange(
        low=max(floor * 0.8, base.low - total_deductions_eur),
        mid=max(floor, base.mid - total_deductions_eur),
        high=max(floor * 1.2, base.high - total_deductions_eur),
        currency=base.currency,
    )


def round_range(pr: PriceRange, currency: str, step: float | None = None) -> PriceRange:
    """Round to a granularity that does not imply false precision."""
    if step is None:
        step = 25_000.0 if currency == "TRY" else 500.0
        if pr.mid < (250_000 if currency == "TRY" else 8_000):
            step = 10_000.0 if currency == "TRY" else 250.0

    def r(v: float) -> float:
        return float(max(0.0, round(v / step) * step))

    return PriceRange(low=r(pr.low), mid=r(pr.mid), high=r(pr.high), currency=currency)


def to_try(pr: PriceRange, calibration: dict, spec: dict | None = None) -> PriceRange:
    from pricing import fx as fx_mod

    mult = fx_mod.multiplier_for(spec, calibration)
    rate = mult * calibration["eur_try"]
    return PriceRange(low=pr.low * rate, mid=pr.mid * rate, high=pr.high * rate, currency="TRY")
