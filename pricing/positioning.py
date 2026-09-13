"""Place a specific truck inside its specification's market price band.

The price model knows what a 2018 Actros 1845 with 700,000 km is advertised for,
and its honest answer is a wide band, because identically specified trucks sell
for very different money depending on how they were kept. The corpus cannot see
condition; the photos can. So the condition rubric is used to locate this truck
within that band rather than merely to shave money off a point estimate.

Two numbers come out of here:

* ``condition_index`` in 0..1 -- where this truck sits relative to a typical
  example of its specification. 0.5 is typical.
* ``evidence_ratio`` in 0..1 -- how much of the positioning rubric the photos
  actually supported, which sets how tight the reported interval is allowed to be.
"""

from __future__ import annotations

import config
from vision.schemas import ConditionReport, PriceRange, Severity

# How far each severity moves an item away from "typical". A single moderate
# problem should not read as a wreck, and a clean bill of health on everything
# should not read as a brand-new truck either.
SEVERITY_SCORE = {
    Severity.none: 1.0,
    Severity.minor: 0.62,
    Severity.moderate: 0.3,
    Severity.severe: 0.0,
}


def _positioning_items() -> dict[str, float]:
    weights = config.rubric()["positioning"]["weights"]
    return {
        item["id"]: float(weights.get(item["id"], 1.0))
        for item in config.rubric_items()
        if item.get("pricing_role") == "positioning"
    }


def deduction_item_ids() -> set[str]:
    return {
        item["id"]
        for item in config.rubric_items()
        if item.get("pricing_role", "deduction") == "deduction"
    }


def condition_index(report: ConditionReport | None) -> tuple[float, float, list[str]]:
    """Return (condition_index, evidence_ratio, explanation lines).

    Items the photos could not cover are excluded from the average rather than
    assumed good, so a truck photographed from one flattering angle does not
    score as immaculate.
    """
    items = _positioning_items()
    if report is None or not items:
        return 0.5, 0.0, ["No condition evidence, so the truck is priced as a typical example."]

    by_id = {f.item_id: f for f in report.findings}
    total_weight = 0.0
    weighted = 0.0
    observed: list[str] = []
    unobserved: list[str] = []

    for item_id, weight in items.items():
        finding = by_id.get(item_id)
        label = (config.rubric_item(item_id) or {}).get("label", item_id)
        if finding is None or finding.severity == Severity.not_observable:
            unobserved.append(label)
            continue
        score = SEVERITY_SCORE.get(finding.severity, 0.5)
        # A low-confidence reading is pulled toward "typical" rather than trusted.
        conf = max(0.0, min(1.0, finding.confidence or 0.5))
        score = 0.5 + (score - 0.5) * (0.55 + 0.45 * conf)
        weighted += score * weight
        total_weight += weight
        observed.append(f"{label}: {finding.severity.value}")

    if total_weight == 0:
        return 0.5, 0.0, [
            "None of the condition areas that drive market position were visible, so the truck "
            "is priced as a typical example of its specification."
        ]

    index = weighted / total_weight
    evidence_ratio = total_weight / sum(items.values())

    lines = []
    if index >= 0.72:
        lines.append(
            "Visible condition is better than typical for this specification, so the estimate sits "
            "in the upper part of the market band."
        )
    elif index <= 0.38:
        lines.append(
            "Visible condition is worse than typical for this specification, so the estimate sits "
            "in the lower part of the market band."
        )
    else:
        lines.append(
            "Visible condition is about typical for this specification, so the estimate sits near "
            "the middle of the market band."
        )
    if observed:
        lines.append("Positioned on: " + ", ".join(observed) + ".")
    if unobserved:
        lines.append(
            "Not visible, so excluded from positioning rather than assumed good: "
            + ", ".join(unobserved)
            + "."
        )
    return index, evidence_ratio, lines


def evidence_tier(evidence_ratio: float) -> str:
    tiers = config.rubric()["positioning"]["evidence_tiers"]
    if evidence_ratio >= tiers["full"]:
        return "full"
    if evidence_ratio >= tiers["good"]:
        return "good"
    if evidence_ratio >= tiers["partial"]:
        return "partial"
    return "poor"


def position(
    band: PriceRange, index: float, evidence_ratio: float
) -> tuple[PriceRange, dict]:
    """Locate the truck within `band` and set the reported interval width.

    `band` is the model's q10-q90 spread for the specification. The returned
    range is a sub-interval of it, centred according to condition and as wide as
    the evidence justifies.
    """
    cfg = config.rubric()["positioning"]
    strength = float(cfg["strength"])
    tier = evidence_tier(evidence_ratio)
    width_fraction = float(cfg["width_by_evidence"][tier])

    band_width = max(0.0, band.high - band.low)
    if band_width <= 0:
        return band, {"tier": tier, "width_fraction": width_fraction, "condition_index": index}

    # Condition maps 0..1 onto the band, damped by `strength` so that even a
    # pristine truck is not priced above what the specification actually fetches.
    centred = 0.5 + (index - 0.5) * strength
    centre = band.low + band_width * centred

    half = band_width * width_fraction / 2.0
    low = max(band.low * 0.92, centre - half)
    high = min(band.high * 1.08, centre + half)
    if high <= low:
        low, high = centre * 0.94, centre * 1.06

    diagnostics = {
        "tier": tier,
        "width_fraction": width_fraction,
        "condition_index": round(index, 3),
        "evidence_ratio": round(evidence_ratio, 3),
        "band_low_eur": round(band.low),
        "band_high_eur": round(band.high),
        "centred_at_pct_of_band": round(centred * 100, 1),
    }
    return PriceRange(low=low, mid=centre, high=high, currency=band.currency), diagnostics
