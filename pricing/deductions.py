"""Turn condition findings into money.

Only the rubric items marked ``pricing_role: deduction`` are charged here: the
discrete bills a buyer pays on collection. The holistic "how well was it kept"
items are handled by `pricing/positioning.py`, which moves the truck within its
specification's market band instead. Keeping the two separate is what stops a
tired-looking cab from being charged twice.

Every line comes from `deductions.yaml`, so the arithmetic is auditable: a buyer
or a truck dealer can disagree with a specific number instead of with the system
as a whole. Tyres are costed from estimated tread rather than a severity band,
because a tyre set is one of the largest single items and tread is the one thing
a photo can genuinely support.
"""

from __future__ import annotations

import config
from pricing.positioning import deduction_item_ids
from vision.schemas import ConditionReport, DeductionLine, Severity, TireAssessment


def _scaling(body_type: str | None) -> float:
    table = config.rubric().get("class_scaling", {})
    return float(table.get(body_type or "tractor_unit", table.get("other", 1.0)))


def tire_deduction(tires: TireAssessment, axle_config: str | None, scale: float) -> tuple[float, str] | None:
    """Cost the tyres from estimated remaining tread."""
    cfg = config.rubric()["tire_costing"]
    if not tires.tires_assessable or tires.tread_remaining_pct is None or tires.tread_remaining_pct < 0:
        return None

    counts = cfg["tire_count_by_axle_config"]
    n_tires = int(counts.get(axle_config or "", counts.get("default", 6)))
    unit = float(cfg["fitted_cost_eur_per_tire"])
    replace_below = float(cfg["replace_below_pct"])

    tread = max(0.0, min(100.0, float(tires.tread_remaining_pct)))
    # Tread below the legal-ish threshold is fully consumed; above it, the
    # missing fraction of life is what the buyer has to fund.
    if tread <= replace_below:
        consumed = 1.0
    else:
        usable_span = 100.0 - replace_below
        consumed = (100.0 - tread) / usable_span

    amount = n_tires * unit * consumed * scale
    parts = [f"{tread:.0f}% tread remaining across {n_tires} tyres at EUR {unit:.0f} fitted"]

    penalties = cfg["penalties_eur"]
    if tires.uneven_wear:
        amount += penalties["uneven_wear"] * scale
        parts.append(f"uneven wear suggests alignment or suspension work (+EUR {penalties['uneven_wear']})")
    if tires.mismatched_sizes_or_brands:
        amount += penalties["mismatched"] * scale
        parts.append(f"mismatched tyres across an axle (+EUR {penalties['mismatched']})")
    if tires.sidewall_damage_or_cracking:
        amount += penalties["sidewall_damage"] * scale
        parts.append(f"sidewall damage or cracking (+EUR {penalties['sidewall_damage']})")
    if tires.retread_detected:
        amount += penalties["retread"] * scale
        parts.append(f"retreaded casings (+EUR {penalties['retread']})")

    if amount < 25:
        return None
    return amount, "; ".join(parts)


def compute(
    report: ConditionReport | None,
    baseline_eur: float,
    axle_config: str | None = None,
    body_type: str | None = None,
) -> tuple[list[DeductionLine], float, list[str]]:
    """Return (lines, total_eur, notes).

    Caps from the rubric are applied so that a photo-only assessment can never
    write off the vehicle. A truck with everything wrong is still worth money.
    """
    if report is None or baseline_eur <= 0:
        return [], 0.0, []

    rubric = config.rubric()
    caps = rubric["caps"]
    scale = _scaling(body_type)
    max_single = baseline_eur * float(caps["max_single_item_fraction_of_baseline"])
    max_total = baseline_eur * float(caps["max_total_fraction_of_baseline"])

    lines: list[DeductionLine] = []
    notes: list[str] = []
    chargeable = deduction_item_ids()

    for finding in report.findings:
        item = config.rubric_item(finding.item_id)
        if item is None:
            continue
        # Positioning items move the truck within its market band instead of
        # being billed, so they are skipped here.
        if finding.item_id not in chargeable:
            continue
        # Tyres are handled separately from the tyre assessment.
        if item.get("dynamic") == "tires":
            continue
        if finding.severity in (Severity.none, Severity.not_observable):
            continue
        amount = float(item["severity_eur"].get(finding.severity.value, 0)) * scale
        if amount <= 0:
            continue
        capped = min(amount, max_single)
        if capped < amount:
            notes.append(
                f"{item['label']} deduction capped at "
                f"{caps['max_single_item_fraction_of_baseline']:.0%} of the market baseline"
            )
        rationale = finding.observation.strip() or f"{finding.severity.value} {item['label'].lower()} observed"
        # A low-confidence finding is discounted rather than dropped, so weak
        # evidence still shows up but does not swing the number.
        conf = max(0.0, min(1.0, finding.confidence or 0.5))
        weight = 0.5 + 0.5 * conf
        lines.append(
            DeductionLine(
                item_id=finding.item_id,
                label=item["label"],
                severity=finding.severity.value,
                amount_eur=round(capped * weight, 2),
                rationale=rationale if conf >= 0.5 else f"{rationale} (low confidence, deduction reduced)",
            )
        )

    tire_item = config.rubric_item("tires")
    tire_result = tire_deduction(report.tires, axle_config, scale)
    if tire_result and tire_item:
        amount, rationale = tire_result
        lines.append(
            DeductionLine(
                item_id="tires",
                label=tire_item["label"],
                severity="measured",
                amount_eur=round(min(amount, max_single), 2),
                rationale=rationale,
            )
        )
    else:
        # No usable tyre evidence: fall back to the severity band if the rubric
        # finding said something, otherwise say nothing and widen the interval
        # elsewhere.
        for finding in report.findings:
            if finding.item_id == "tires" and finding.severity not in (Severity.none, Severity.not_observable):
                amount = float(tire_item["severity_eur"].get(finding.severity.value, 0)) * scale if tire_item else 0
                if amount > 0:
                    lines.append(
                        DeductionLine(
                            item_id="tires",
                            label=tire_item["label"] if tire_item else "Tyres",
                            severity=finding.severity.value,
                            amount_eur=round(min(amount, max_single), 2),
                            rationale=finding.observation or "tyre condition judged from a general view only",
                        )
                    )
                break

    total = sum(line.amount_eur for line in lines)
    if total > max_total:
        factor = max_total / total
        for line in lines:
            line.amount_eur = round(line.amount_eur * factor, 2)
        notes.append(
            f"Total deductions exceeded {caps['max_total_fraction_of_baseline']:.0%} of the market "
            f"baseline and were scaled back proportionally; on this much visible damage the truck "
            f"needs a physical inspection, not a photo appraisal."
        )
        total = max_total

    lines.sort(key=lambda line: -line.amount_eur)
    return lines, round(total, 2), notes
