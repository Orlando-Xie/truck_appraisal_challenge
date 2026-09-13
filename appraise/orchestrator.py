"""The appraisal pipeline.

Sequencing is chosen for both honesty and latency:

* free local checks first, so an unusable photo set is rejected without spending
  a single API call;
* per-image triage next, concurrently, because it gates everything else;
* identification and the condition rubric concurrently, because neither strictly
  needs the other and together they dominate wall-clock time;
* pricing last, and entirely deterministic.

Every stage is individually guarded: a stage that fails degrades the result and
records a warning rather than taking the whole appraisal down. That matters
because this has to survive a live demo on unseen photos.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Awaitable, Callable

import config
from appraise import dedupe, fusion, gates
from pricing import comps as comps_mod
from pricing import deductions as deductions_mod
from pricing import fx, positioning, predict
from vision import quality, stages
from vision.client import get_client
from vision.schemas import (
    AppraisalResult,
    ConditionReport,
    Contradiction,
    Identification,
    MissingView,
    PriceRange,
    PricingBasis,
    Refusal,
    ScenarioPrice,
    SellerClaims,
    Severity,
    TriagedImage,
)

log = logging.getLogger(__name__)

ProgressCB = Callable[[str, str, dict | None], Awaitable[None]] | None

STAGES = [
    ("quality", "Checking photo quality"),
    ("triage", "Working out what these photos show"),
    ("gates", "Deciding whether this can be appraised"),
    ("identify", "Identifying the vehicle"),
    ("condition", "Inspecting condition"),
    ("comps", "Finding comparable listings"),
    ("price", "Pricing"),
    ("done", "Done"),
]


async def _emit(cb: ProgressCB, stage: str, message: str, data: dict | None = None) -> None:
    if cb is not None:
        try:
            await cb(stage, message, data)
        except Exception:  # progress reporting must never break the pipeline
            log.debug("progress callback failed", exc_info=True)


def cache_key(images: list[tuple[str, bytes]], claims: SellerClaims | None) -> str:
    h = hashlib.sha256()
    for _, blob in images:
        h.update(hashlib.sha256(blob).digest())
    if claims:
        h.update(json.dumps(claims.model_dump(), sort_keys=True, default=str).encode())
    h.update(b"v5")  # bump to invalidate cached results after a pipeline change
    return h.hexdigest()[:32]


def load_cached(key: str) -> AppraisalResult | None:
    if not config.ENABLE_RESULT_CACHE:
        return None
    path = config.CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        result = AppraisalResult.model_validate_json(path.read_text(encoding="utf-8"))
        result.cache_hit = True
        return result
    except Exception:
        return None


def save_cached(key: str, result: AppraisalResult) -> None:
    if not config.ENABLE_RESULT_CACHE:
        return
    try:
        (config.CACHE_DIR / f"{key}.json").write_text(
            result.model_dump_json(indent=None), encoding="utf-8"
        )
    except Exception:
        log.debug("could not write result cache", exc_info=True)


def _refused(refusal: Refusal, images: list[TriagedImage], t0: float, claims: SellerClaims | None) -> AppraisalResult:
    return AppraisalResult(
        status="refused",
        refusal=refusal,
        confidence=0.0,
        confidence_label="Cannot appraise",
        images=images,
        request_photos=refusal.what_to_send,
        seller_claims=claims,
        elapsed_seconds=round(time.time() - t0, 2),
    )


def _confidence(
    ident_tier: str,
    evidence_ratio: float,
    n_comps: int,
    n_usable_images: int,
    widening_pct: float,
    has_alert: bool,
) -> tuple[float, str]:
    """A single headline confidence, built from the things that actually drive it."""
    score = 1.0
    score *= {"ok": 1.0, "year_unknown": 0.85, "variant_unknown": 0.78, "model_unknown": 0.55, "make_unknown": 0.35, "none": 0.2}.get(ident_tier, 0.6)
    score *= 0.55 + 0.45 * min(1.0, evidence_ratio / 0.85)
    score *= 0.6 + 0.4 * min(1.0, n_comps / 8.0)
    score *= 0.65 + 0.35 * min(1.0, n_usable_images / 5.0)
    score *= max(0.5, 1.0 - widening_pct / 120.0)
    if has_alert:
        score *= 0.8
    score = max(0.05, min(0.97, score))

    if score >= 0.75:
        label = "High"
    elif score >= 0.55:
        label = "Moderate"
    elif score >= 0.35:
        label = "Low"
    else:
        label = "Very low"
    return round(score, 3), label


def _compute_widening(
    ident_tier: str,
    missing: list[str],
    n_comps: int,
    n_images: int,
    km_estimated: bool,
    contradictions: list[Contradiction],
) -> tuple[float, list[str], list[MissingView]]:
    """How much to widen the market band, and why. Every reason is user-facing."""
    cfg = config.rubric()["widening"]
    total = 0.0
    reasons: list[str] = []
    missing_views: list[MissingView] = []

    view_names = []
    for view in missing:
        pct = float(cfg["missing_view_pct"].get(view, 0.0))
        if pct <= 0:
            continue
        total += pct
        why = cfg["missing_view_reason"].get(view, "")
        missing_views.append(MissingView(view=view, why_it_matters=why, widening_pct=pct))
        view_names.append(view.replace("_", " "))
    if view_names:
        reasons.append(
            f"{len(view_names)} inspection view{'s' if len(view_names) != 1 else ''} missing from the "
            f"photo set ({', '.join(view_names)})."
        )

    tier_map = {
        "make_unknown": ("no_model_identified_pct", "The manufacturer could not be established."),
        "model_unknown": ("no_model_identified_pct", "The model could not be established."),
        "variant_unknown": ("no_variant_identified_pct", "No power designation was legible."),
        "year_unknown": ("no_year_pinned_pct", "The build year could not be narrowed."),
    }
    if ident_tier in tier_map:
        key, reason = tier_map[ident_tier]
        total += float(cfg[key])
        reasons.append(reason)

    if km_estimated:
        # Already partly covered by the missing dashboard view, but an estimated
        # mileage is the single largest source of error in a photo-only appraisal.
        total += 6.0
        reasons.append("Mileage had to be estimated rather than read.")

    if n_comps < int(cfg["thin_comparables_threshold"]):
        total += float(cfg["thin_comparables_pct"])
        reasons.append(f"Only {n_comps} genuinely comparable listings were found.")

    if n_images < int(cfg["low_image_count_threshold"]):
        total += float(cfg["low_image_count_pct"])
        reasons.append(f"Only {n_images} usable photo{'s' if n_images != 1 else ''} were supplied.")

    if any(c.severity == "alert" for c in contradictions):
        total += float(cfg["unresolved_contradiction_pct"])
        reasons.append("An unresolved contradiction between the photos and the stated details.")

    total = min(total, float(cfg["max_total_widening_pct"]))
    return total, reasons, missing_views


async def appraise(
    images: list[tuple[str, bytes]],
    claims: SellerClaims | None = None,
    progress: ProgressCB = None,
    use_cache: bool = True,
) -> AppraisalResult:
    t0 = time.time()
    images = images[: config.MAX_IMAGES]

    if not images:
        return _refused(
            Refusal(
                code="no_images",
                headline="No photos were received",
                detail="Upload at least one photo of the truck.",
                what_to_send=gates.STANDARD_PHOTO_REQUEST,
            ),
            [],
            t0,
            claims,
        )

    key = cache_key(images, claims)
    if use_cache:
        cached = load_cached(key)
        if cached is not None:
            await _emit(progress, "done", "Loaded a previous result for these exact photos")
            return cached

    warnings: list[str] = []

    # ---- Stage 0a: local quality (free, no API calls) --------------------
    await _emit(progress, "quality", "Checking photo quality")
    downscaled: list[tuple[str, bytes]] = []
    qualities = []
    for filename, blob in images:
        try:
            small = await asyncio.to_thread(quality.downscale_for_model, blob)
        except Exception:
            small = blob
        downscaled.append((filename, small))
        qualities.append(await asyncio.to_thread(quality.assess, filename, blob))

    # ---- Stage 0b: per-image triage -------------------------------------
    await _emit(progress, "triage", "Working out what these photos show")
    try:
        triaged = await asyncio.wait_for(
            stages.triage_all(downscaled, qualities), timeout=config.STAGE_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        warnings.append("Photo triage timed out; falling back to local quality checks only.")
        triaged = [
            TriagedImage(filename=fn, quality=q, triage=ImageTriage())
            for (fn, _), q in zip(downscaled, qualities)
        ]

    await _emit(
        progress,
        "triage",
        "Photos classified",
        {
            "images": [
                {
                    "filename": im.filename,
                    "subject": im.triage.subject.value,
                    "view": im.triage.view.value,
                    "usable": im.quality.usable,
                    "blur_score": im.quality.blur_score,
                }
                for im in triaged
            ]
        },
    )

    # ---- Gates 1 and 2: refuse before spending more ---------------------
    await _emit(progress, "gates", "Deciding whether this can be appraised")
    refusal = gates.gate_quality(triaged)
    if refusal is None:
        refusal = gates.gate_subject(triaged)
    if refusal is not None:
        result = _refused(refusal, triaged, t0, claims)
        save_cached(key, result)
        await _emit(progress, "done", refusal.headline, {"refused": True})
        return result

    # Only usable photos of the vehicle go forward.
    usable = [im for im in triaged if im.quality.usable] or triaged
    model_images = stages.pick_stage_images(triaged, downscaled)

    # ---- Stages 1 and 2 concurrently ------------------------------------
    await _emit(progress, "identify", "Identifying the vehicle")
    hint_lines = []
    if claims:
        for label, value in (
            ("year", claims.year),
            ("make", claims.make),
            ("model", claims.model),
            ("mileage_km", claims.km),
        ):
            if value:
                hint_lines.append(f"- {label}: {value}")
    hints = "\n".join(hint_lines)

    async def _identify() -> Identification | None:
        try:
            return await asyncio.wait_for(
                stages.identify(model_images, hints), timeout=config.STAGE_TIMEOUT_S
            )
        except Exception as exc:
            log.warning("identification failed: %s", exc)
            warnings.append(f"Identification stage failed ({type(exc).__name__}).")
            return None

    async def _condition() -> ConditionReport | None:
        try:
            return await asyncio.wait_for(
                stages.assess_condition(model_images), timeout=config.STAGE_TIMEOUT_S
            )
        except Exception as exc:
            log.warning("condition assessment failed: %s", exc)
            warnings.append(f"Condition stage failed ({type(exc).__name__}).")
            return None

    async def _duplicates():
        try:
            return await asyncio.to_thread(dedupe.find_duplicates, model_images)
        except Exception as exc:
            log.debug("dedupe failed: %s", exc)
            return []

    ident, condition, duplicate_photos = await asyncio.gather(
        _identify(), _condition(), _duplicates()
    )

    if ident is not None:
        await _emit(
            progress,
            "identify",
            f"Identified as {ident.make} {ident.model_family} {ident.model_variant}".strip(),
            {"identification": ident.model_dump(mode="json")},
        )
    if condition is not None:
        flagged = sum(
            1 for f in condition.findings if f.severity in (Severity.minor, Severity.moderate, Severity.severe)
        )
        await _emit(progress, "condition", f"Condition inspected: {flagged} item(s) flagged")

    # ---- Gates 3 and 4 --------------------------------------------------
    multi = gates.gate_multiple_vehicles(triaged, ident)
    if multi:
        warnings.append(multi)
    ident_tier, ident_notes = gates.gate_identification(ident)

    # ---- Fusion ---------------------------------------------------------
    contradictions = fusion.detect_contradictions(ident, condition, claims)
    spec, provenance = fusion.build_spec(ident, condition, claims, triaged)

    # ---- Comparables ----------------------------------------------------
    await _emit(progress, "comps", "Finding comparable listings")
    try:
        comparables = await asyncio.to_thread(comps_mod.find_comparables, spec, 8)
    except Exception as exc:
        log.warning("comparables lookup failed: %s", exc)
        warnings.append("Comparable lookup failed.")
        comparables = []
    comp_stats = comps_mod.comp_price_stats(comparables)
    await _emit(progress, "comps", f"{len(comparables)} comparable listing(s) found")

    # ---- Pricing --------------------------------------------------------
    await _emit(progress, "price", "Pricing")
    calibration = fx.load_calibration()
    missing = gates.missing_views(triaged)
    widening_pct, widening_reasons, missing_views = _compute_widening(
        ident_tier,
        missing,
        len(comparables),
        len(usable),
        bool(provenance.get("km_estimated")),
        contradictions,
    )
    widening_reasons = ident_notes + widening_reasons

    cond_index, evidence_ratio, positioning_notes = positioning.condition_index(condition)

    try:
        band, diagnostics = await asyncio.to_thread(predict.baseline_range, spec)
    except predict.ModelUnavailable as exc:
        log.error("price model unavailable: %s", exc)
        result = _refused(
            Refusal(
                code="model_unavailable",
                headline="The price model is not trained yet",
                detail=str(exc),
                what_to_send=[],
            ),
            triaged,
            t0,
            claims,
        )
        return result
    except Exception as exc:
        log.exception("pricing failed")
        warnings.append(f"Pricing failed ({type(exc).__name__}).")
        result = AppraisalResult(
            status="partial",
            confidence=0.1,
            confidence_label="Very low",
            identification=ident,
            condition=condition,
            images=triaged,
            comparables=comparables,
            contradictions=contradictions,
            duplicate_photos=duplicate_photos,
            warnings=warnings + ["No price could be produced, but the condition report above stands."],
            seller_claims=claims,
            elapsed_seconds=round(time.time() - t0, 2),
        )
        return result

    band, comp_note = predict.blend_with_comps(band, comp_stats)
    notes: list[str] = []
    if comp_note:
        notes.append(comp_note)

    band = predict.widen(band, widening_pct)
    positioned, pos_diag = positioning.position(band, cond_index, evidence_ratio)

    ded_lines, ded_total, ded_notes = deductions_mod.compute(
        condition,
        baseline_eur=positioned.mid,
        axle_config=spec.get("axle_config"),
        body_type=spec.get("body_type"),
    )
    notes.extend(ded_notes)
    final_eur = predict.apply_deductions(positioned, ded_total)

    price_eur = predict.round_range(final_eur, "EUR")
    price_try = predict.round_range(predict.to_try(final_eur, calibration), "TRY")

    # Scenario pricing when mileage credibility is in doubt.
    scenarios: list[ScenarioPrice] = []
    km_alert = next((c for c in contradictions if c.field == "km" and c.severity == "alert"), None)
    if km_alert and claims and claims.km and config.rubric()["mileage_check"]["price_both_scenarios"]:
        try:
            alt_spec = dict(spec)
            alt_spec["km"] = claims.km
            alt_band, _ = await asyncio.to_thread(predict.baseline_range, alt_spec)
            alt_band, _ = predict.blend_with_comps(alt_band, comp_stats)
            alt_band = predict.widen(alt_band, widening_pct)
            alt_pos, _ = positioning.position(alt_band, cond_index, evidence_ratio)
            alt_final = predict.apply_deductions(alt_pos, ded_total)
            scenarios = [
                ScenarioPrice(
                    label="If the stated mileage is genuine",
                    assumption=f"Mileage is {claims.km:,} km as stated".replace(",", " "),
                    price_try=predict.round_range(predict.to_try(alt_final, calibration), "TRY"),
                    price_eur=predict.round_range(alt_final, "EUR"),
                ),
                ScenarioPrice(
                    label="If the wear is telling the truth",
                    assumption=f"Mileage is nearer {spec['km']:,} km, as the wear suggests".replace(",", " "),
                    price_try=price_try,
                    price_eur=price_eur,
                ),
            ]
        except Exception:
            log.debug("scenario pricing failed", exc_info=True)

    has_alert = any(c.severity == "alert" for c in contradictions)
    confidence, confidence_label = _confidence(
        ident_tier, evidence_ratio, len(comparables), len(usable), widening_pct, has_alert
    )

    blind_spots: list[str] = list(condition.not_observable) if condition else []
    for mv in missing_views:
        if mv.why_it_matters:
            blind_spots.append(mv.why_it_matters)

    request_photos: list[str] = []
    view_requests = {
        "dashboard_odometer": "The instrument cluster with the ignition on, so the odometer is readable.",
        "tire_wheel": "A close-up of one drive tyre showing the tread depth.",
        "side": "A full side profile of the truck.",
        "front_three_quarter": "A front three-quarter shot of the whole truck in daylight.",
        "interior_cab": "The cab interior, including the seat and steering wheel.",
        "engine_bay": "The engine bay with the cab tilted or the panel open.",
        "fifth_wheel": "The fifth wheel coupling plate.",
    }
    for view in missing:
        if view in view_requests:
            request_photos.append(view_requests[view])
    if ident_tier in ("make_unknown", "model_unknown"):
        request_photos.insert(0, "A straight-on photo of the grille badge and any model lettering on the cab.")

    basis = PricingBasis(
        model_used=f"gradient-boosted quantile regression on {predict.model_info().get('n_train', 0)} listings",
        spec_used={k: v for k, v in spec.items() if v not in (None, "")},
        market_baseline_eur=predict.round_range(band, "EUR"),
        total_deductions_eur=ded_total,
        deductions=ded_lines,
        turkiye_multiplier=calibration["turkiye_multiplier"],
        eur_try_rate=calibration["eur_try"],
        comparable_count=len(comparables),
        interval_widening_pct=round(widening_pct, 1),
        widening_reasons=widening_reasons,
        notes=notes
        + positioning_notes
        + [f"Year taken from {provenance['year']}.", f"Mileage taken from {provenance['km']}."]
        + ([calibration["notes"]] if calibration.get("notes") else [])
        + [
            f"Comparables span EUR {comp_stats['min_eur']:,.0f} to EUR {comp_stats['max_eur']:,.0f} "
            f"with a median of EUR {comp_stats['median_eur']:,.0f}.".replace(",", " ")
        ]
        if comp_stats
        else notes + positioning_notes,
    )
    basis.spec_used["positioning"] = pos_diag
    basis.spec_used["model_diagnostics"] = diagnostics

    status = "ok"
    if ident_tier in ("make_unknown", "none") or confidence < 0.3:
        status = "partial"

    result = AppraisalResult(
        status=status,
        confidence=confidence,
        confidence_label=confidence_label,
        price_try=price_try,
        price_eur=price_eur,
        scenarios=scenarios,
        identification=ident,
        condition=condition,
        images=triaged,
        comparables=comparables,
        pricing_basis=basis,
        contradictions=contradictions,
        duplicate_photos=duplicate_photos,
        missing_views=missing_views,
        blind_spots=blind_spots,
        request_photos=request_photos[:5],
        warnings=warnings,
        seller_claims=claims,
        elapsed_seconds=round(time.time() - t0, 2),
    )
    save_cached(key, result)
    await _emit(progress, "done", "Appraisal complete", {"confidence": confidence_label})
    return result


def pipeline_info() -> dict:
    client = get_client()
    return {
        "vision": client.describe(),
        "price_model": predict.model_info(),
        "calibration": fx.load_calibration(),
        "rubric_items": [
            {"id": i["id"], "label": i["label"], "role": i.get("pricing_role", "deduction")}
            for i in config.rubric_items()
        ],
    }
