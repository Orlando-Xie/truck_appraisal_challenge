"""The three model-backed perceptual stages.

Prompt design follows one rule: the model is asked what it can see, never what
something is worth. Every stage is also told explicitly that "I cannot tell from
this photo" is a correct and expected answer, because the failure mode that
matters in a marketplace is confident nonsense, not an admission of ignorance.
"""

from __future__ import annotations

import asyncio
import logging

import config
from vision.client import VisionError, get_client
from vision.quality import crop_region
from vision.schemas import (
    BadgeRead,
    BatchTriage,
    ConditionReport,
    Identification,
    ImageQuality,
    ImageTriage,
    OdometerRead,
    SubjectClass,
    TriagedImage,
    ViewType,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stage 0: per-image triage
# ---------------------------------------------------------------------------

TRIAGE_PROMPT = """You are the intake step of a used commercial-vehicle appraisal system in Turkiye.

Look at this ONE photo and report only what is actually visible. Do not guess, do not
infer from context, and do not assume the photo shows a truck just because it was
uploaded to a truck appraisal tool. Sellers upload the wrong thing all the time.

Classify `subject` as what is genuinely in the frame:
- truck_tractor: a heavy road tractor unit designed to pull a semi-trailer (fifth wheel, no cargo body of its own)
- rigid_truck: a heavy truck with its own cargo body on the same chassis
- construction_truck: a heavy tipper, mixer or similar off-road-capable truck
- bus_coach, van_light_commercial, trailer_semitrailer (a trailer with NO tractor attached),
  construction_machine, agricultural_tractor, pickup, passenger_car, motorcycle
- truck_part_or_detail: a close-up of part of a truck where the whole vehicle is not visible
  (a tyre, a dashboard, an engine bay, a coupling). Use this rather than guessing the vehicle class.
- not_a_vehicle: anything else at all, including people, buildings, documents, screenshots of text, blank frames
- indeterminate: you genuinely cannot tell

Set `view` to which inspection view this photo provides. Use dashboard_odometer only if an
instrument cluster is visible, tire_wheel only for a close enough view to judge a tyre.

`vehicle_fingerprint` must capture what makes THIS vehicle identifiable across photos:
colour, livery or company name, visible plate, distinctive damage, accessories, wheel type.
Later steps use it to detect that two photos show different vehicles.

List anything in `obstructions` that blocks assessment: mud, snow, dirt, darkness, rain,
glare, heavy shadow, cropping, people or objects in front of the vehicle.

Set `usable_for_appraisal` false if this photo contributes nothing an appraiser could use.
Be honest. A confident answer about a photo you cannot read is worse than no answer."""


async def triage_image(filename: str, blob: bytes, quality: ImageQuality) -> TriagedImage:
    client = get_client()
    try:
        triage = await client.structured(
            prompt=TRIAGE_PROMPT,
            images=[(filename, blob)],
            response_model=ImageTriage,
            temperature=0.0,
            stub_hint={"quality": quality.model_dump()},
        )
    except (VisionError, Exception) as exc:  # noqa: BLE001 - one bad photo must not fail the run
        log.warning("triage failed for %s: %s", filename, exc)
        triage = ImageTriage(
            subject=SubjectClass.indeterminate,
            subject_confidence=0.0,
            view=ViewType.detail_other,
            subject_description=f"triage could not be completed ({type(exc).__name__})",
            usable_for_appraisal=False,
        )

    # A locally measured quality failure overrides an optimistic model answer.
    if not quality.usable:
        triage.usable_for_appraisal = False
        for note in quality.notes:
            if note not in triage.obstructions:
                triage.obstructions.append(note)

    return TriagedImage(filename=filename, quality=quality, triage=triage)


BATCH_TRIAGE_PROMPT = (
    TRIAGE_PROMPT
    + """

You are looking at several photos at once. Return one entry per image in the same
order, with `filename` matching the [image: ...] label. Judge each photo on its
own — do not let a truck in one frame make you assume the next frame is a truck."""
)


async def triage_all(images: list[tuple[str, bytes]], qualities: list[ImageQuality]) -> list[TriagedImage]:
    """Triage every photo.

    One batched Flash call is much cheaper than N separate ones. If the batch
    fails, fall back to per-image so a single malformed response cannot take
    the whole intake stage down.
    """
    if not images:
        return []
    if len(images) == 1:
        return [await triage_image(images[0][0], images[0][1], qualities[0])]

    client = get_client()
    try:
        batch = await client.structured(
            prompt=BATCH_TRIAGE_PROMPT,
            images=images,
            response_model=BatchTriage,
            temperature=0.0,
        )
        by_name = {item.filename: item for item in batch.images if item.filename}
        out: list[TriagedImage] = []
        for (fn, blob), q in zip(images, qualities):
            named = by_name.get(fn)
            if named is None and len(batch.images) == len(images):
                named = batch.images[len(out)]
            if named is None:
                out.append(await triage_image(fn, blob, q))
                continue
            triage = ImageTriage.model_validate(named.model_dump(exclude={"filename"}))
            if not q.usable:
                triage.usable_for_appraisal = False
                for note in q.notes:
                    if note not in triage.obstructions:
                        triage.obstructions.append(note)
            out.append(TriagedImage(filename=fn, quality=q, triage=triage))
        if out:
            return out
    except Exception as exc:
        log.warning("batched triage failed (%s); falling back to per-image", exc)

    results = await asyncio.gather(
        *(triage_image(fn, blob, q) for (fn, blob), q in zip(images, qualities)),
        return_exceptions=True,
    )
    out = []
    for (fn, _blob), q, res in zip(images, qualities, results):
        if isinstance(res, TriagedImage):
            out.append(res)
        else:
            out.append(
                TriagedImage(
                    filename=fn,
                    quality=q,
                    triage=ImageTriage(
                        subject=SubjectClass.indeterminate,
                        subject_description="triage errored",
                        usable_for_appraisal=False,
                    ),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Stage 1: identification
# ---------------------------------------------------------------------------

IDENTIFY_PROMPT = """You are identifying a used heavy commercial vehicle from photos, for an
appraisal in the Turkish second-hand truck market.

Identify the vehicle as precisely as the photos genuinely allow, and no more precisely
than that. Work from concrete visual evidence:
- badges, model numbers and lettering on the grille, doors or rear of the cab
- grille, headlamp and bumper shape, which pin the generation (for example Actros MP2 vs
  MP3 vs MP4 vs MP5, DAF XF105 vs XF106 vs XG, Volvo FH2 vs FH3 vs FH4 vs FH5,
  Scania R-series pre-facelift vs Streamline vs Next Generation, MAN TGA vs TGX)
- mirror and sun visor style, door handle style, wheel and hub design
- cab height and depth, which give the cab designation
- number of axles and which are driven, for the axle configuration
- Euro emission badges or AdBlue filler presence

`generation_year_low` and `generation_year_high` must bracket the years that generation
was actually built. Do not narrow to a single year unless a date is legibly visible.
Leave them 0 if you cannot pin the generation at all.

`model_variant` is the numeric designation only if you can actually read it or infer it
from a badge, for example "1845", "18.480", "R450", "XF480". Do not invent one; an
invented power rating moves the price by thousands.

Set `estimated_power_hp` only when a badge or model number implies it.

Set `same_vehicle_in_all_photos` false and `distinct_vehicle_count` above 1 if the photos
show more than one physical vehicle. Compare colour, plate, livery, damage and wheels.

Put every cue you used in `identifying_evidence`, phrased so a buyer could check it
themselves. Put anything that does not fit in `contradicting_evidence`.

If you are unsure between two candidates, give the more likely one and list the other in
`alternative_hypotheses` with an honest probability. Confidence values must be calibrated:
use below 0.5 when you are genuinely guessing."""


def pick_stage_images(
    triaged: list[TriagedImage],
    downscaled: list[tuple[str, bytes]],
    limit: int | None = None,
) -> list[tuple[str, bytes]]:
    """Keep a covering set of views so later stages do not resend every upload."""
    limit = limit or config.MAX_VISION_IMAGES
    by_name = {fn: blob for fn, blob in downscaled}
    usable = [im for im in triaged if im.quality.usable and im.filename in by_name] or [
        im for im in triaged if im.filename in by_name
    ]
    priority = [
        ViewType.front_three_quarter,
        ViewType.tire_wheel,
        ViewType.dashboard_odometer,
        ViewType.side,
        ViewType.front,
        ViewType.interior_cab,
        ViewType.rear_three_quarter,
        ViewType.engine_bay,
        ViewType.fifth_wheel,
    ]

    def rank(im: TriagedImage) -> tuple[int, float]:
        try:
            return (priority.index(im.triage.view), -im.triage.view_confidence)
        except ValueError:
            return (99, -im.triage.view_confidence)

    unique: list[TriagedImage] = []
    extras: list[TriagedImage] = []
    seen: set[ViewType] = set()
    for im in sorted(usable, key=rank):
        if im.triage.view not in seen:
            unique.append(im)
            seen.add(im.triage.view)
        else:
            extras.append(im)
    chosen = (unique + extras)[:limit]
    return [(im.filename, by_name[im.filename]) for im in chosen]



BADGE_PROMPT = """You are reading a tight crop of a truck grille, headlight or model badge.

Report only what is legible. Do not guess a generation you cannot support from this crop.
Leave numeric fields 0 and strings empty when the crop does not show them.
`generation_year_low` / `generation_year_high` must be the production window of the
visual generation (for example Actros MP4 is 2011-2018), not a single guessed year."""

ODOMETER_PROMPT = """You are reading a tight crop of a truck instrument cluster.

If odometer digits are legible, set digits_visible true and reading_km to the kilometre
figure (not miles, not hours). If you cannot read the digits, set digits_visible false
and reading_km 0. Do not invent a round number."""


def _blob_for(filename: str, downscaled: list[tuple[str, bytes]]) -> bytes | None:
    for fn, blob in downscaled:
        if fn == filename:
            return blob
    return None


def _first_view(triaged: list[TriagedImage], views: set[ViewType]) -> TriagedImage | None:
    ranked = [im for im in triaged if im.quality.usable and im.triage.view in views]
    ranked.sort(key=lambda im: -im.triage.view_confidence)
    return ranked[0] if ranked else None


async def refine_identification(
    ident: Identification,
    triaged: list[TriagedImage],
    downscaled: list[tuple[str, bytes]],
) -> Identification:
    """Second cheap Flash pass: grille crop for generation, dash crop for km."""
    client = get_client()
    front = _first_view(triaged, {ViewType.front_three_quarter, ViewType.front})
    dash = _first_view(triaged, {ViewType.dashboard_odometer})

    if front:
        blob = _blob_for(front.filename, downscaled)
        if blob:
            try:
                crop = crop_region(blob, (0.18, 0.12, 0.82, 0.58))
                badge = await client.structured(
                    prompt=BADGE_PROMPT,
                    images=[(f"badge_{front.filename}", crop)],
                    response_model=BadgeRead,
                    temperature=0.0,
                )
                ident = _merge_badge(ident, badge)
            except Exception as exc:  # noqa: BLE001
                log.warning("badge refine failed: %s", exc)

    if dash:
        blob = _blob_for(dash.filename, downscaled)
        if blob:
            try:
                crop = crop_region(blob, (0.18, 0.22, 0.82, 0.78))
                odo = await client.structured(
                    prompt=ODOMETER_PROMPT,
                    images=[(f"odo_{dash.filename}", crop)],
                    response_model=OdometerRead,
                    temperature=0.0,
                )
                ident = _merge_odometer(ident, odo)
            except Exception as exc:  # noqa: BLE001
                log.warning("odometer refine failed: %s", exc)
    return ident


def _merge_badge(ident: Identification, badge: BadgeRead) -> Identification:
    data = ident.model_dump()
    if badge.make and (not ident.make or ident.make_confidence < 0.7):
        data["make"] = badge.make
        data["make_confidence"] = max(ident.make_confidence, 0.72)
    if badge.model_family and (not ident.model_family or ident.model_confidence < 0.7):
        data["model_family"] = badge.model_family
        data["model_confidence"] = max(ident.model_confidence, 0.7)
    if badge.model_variant and not ident.model_variant:
        data["model_variant"] = badge.model_variant
    if badge.generation:
        data["generation"] = badge.generation
    if badge.generation_year_low and badge.generation_year_high:
        data["generation_year_low"] = badge.generation_year_low
        data["generation_year_high"] = badge.generation_year_high
    if badge.estimated_power_hp and not ident.estimated_power_hp:
        data["estimated_power_hp"] = badge.estimated_power_hp
    if badge.euro_class and not ident.euro_class:
        data["euro_class"] = badge.euro_class
    if badge.evidence:
        data["identifying_evidence"] = list(ident.identifying_evidence) + [f"badge crop: {badge.evidence}"]
    return Identification.model_validate(data)


def _merge_odometer(ident: Identification, odo: OdometerRead) -> Identification:
    if not odo.digits_visible or odo.reading_km < 1000 or odo.confidence < 0.4:
        return ident
    data = ident.model_dump()
    data["odometer_reading_km"] = odo.reading_km
    note = f"odometer crop reads {odo.reading_km:,} km".replace(",", " ")
    data["identifying_evidence"] = list(ident.identifying_evidence) + [note]
    return Identification.model_validate(data)


async def identify(images: list[tuple[str, bytes]], hints: str = "") -> Identification:
    client = get_client()
    prompt = IDENTIFY_PROMPT
    if hints:
        prompt += (
            "\n\nThe seller has typed the following details. Treat them as an unverified "
            "claim: confirm or contradict them from the photos, and do not let them "
            "override what you can see.\n" + hints
        )
    return await client.structured(
        prompt=prompt,
        images=images,
        response_model=Identification,
        use_pro=False,
        temperature=0.1,
    )


# ---------------------------------------------------------------------------
# Stage 2: condition rubric
# ---------------------------------------------------------------------------


def build_condition_prompt() -> str:
    """The rubric from deductions.yaml is injected verbatim.

    Keeping one checklist in one file means the money in the deduction table and
    the question put to the model can never drift apart.
    """
    lines = [
        "You are carrying out a photo-based condition inspection of a used heavy truck for a "
        "buyer who may travel hundreds of kilometres to view it. Your job is to tell them what "
        "is actually wrong with this truck, and to be equally clear about what the photos do "
        "not show.",
        "",
        "Work through this checklist. Return exactly one finding per item id, in order.",
        "",
    ]
    for item in config.rubric_items():
        look = " ".join((item.get("look_for") or "").split())
        lines.append(f"- {item['id']} ({item['label']}): {look}")
    lines += [
        "",
        "For each item set `severity` to one of:",
        "  none            - the item is clearly visible and clearly fine",
        "  minor           - cosmetic or early-stage, cheap to put right",
        "  moderate        - needs work, a buyer should budget for it",
        "  severe          - major expense, or a reason to walk away",
        "  not_observable  - the photos do not show this area well enough to judge",
        "",
        "not_observable is the correct and expected answer for anything the photo set does not "
        "cover. Never guess a severity to fill in a blank. Do not mark an item `none` just "
        "because you cannot see a problem: `none` means you can see the area and it is sound.",
        "",
        "`observation` must describe what is literally visible, citing the photo. Write "
        "\"rust blistering along the lower edge of the driver's door in photo 2\", not "
        "\"some corrosion present\". No hedging filler.",
        "",
        "For every finding worse than `none`, add at least one `evidence` box with the filename "
        "and normalised coordinates (x0, y0, x1, y1 between 0 and 1) around the thing you are "
        "describing, so the buyer can be shown the exact spot.",
        "",
        "Tyres: fill in `tires` only from tyres you can actually see. `tread_remaining_pct` is a "
        "percentage of usable tread left, or -1 if you cannot judge it. A full set of tyres on a "
        "tractor unit is a four-figure cost, so an honest -1 is far better than a guess.",
        "",
        "Mileage cross-check: fill in `wear`. If an instrument cluster is visible, read the "
        "odometer digits into `odometer_reading_km`. Separately, and WITHOUT looking at the "
        "odometer, estimate from wear alone what mileage this truck has covered: steering wheel "
        "polish and rim wear, seat bolster collapse, pedal rubber wear, gear selector wear, door "
        "sill and step scuffing, paint thinning on grab handles. Put that as a range in "
        "`wear_implied_km_low` and `wear_implied_km_high`. These two figures are compared later "
        "to check whether the odometer is credible, so estimate the wear independently and "
        "honestly rather than anchoring it to the odometer you just read.",
        "",
        "`roadworthy_concerns` is for anything a buyer should treat as a safety or legality "
        "question. `not_observable` at the top level lists the areas the photo set fails to "
        "cover at all.",
    ]
    return "\n".join(lines)


async def assess_condition(images: list[tuple[str, bytes]], identification: Identification | None = None) -> ConditionReport:
    client = get_client()
    prompt = build_condition_prompt()
    if identification and identification.make:
        prompt += (
            f"\n\nThe vehicle has been identified as a {identification.make} "
            f"{identification.model_family} {identification.model_variant}".rstrip()
            + f" ({identification.body_type or 'tractor_unit'}). Use that only to know where to "
            "look for known weak points on this model; do not let it change what you report seeing."
        )
    report = await client.structured(
        prompt=prompt,
        images=images,
        response_model=ConditionReport,
        use_pro=False,
        temperature=0.1,
    )
    return _clean_condition(report)


def _clean_condition(report: ConditionReport) -> ConditionReport:
    """Drop findings for unknown ids and de-duplicate, keeping the worst."""
    from vision.schemas import SEVERITY_RANK

    valid = {item["id"] for item in config.rubric_items()}
    best: dict[str, object] = {}
    for finding in report.findings:
        if finding.item_id not in valid:
            continue
        prior = best.get(finding.item_id)
        if prior is None or SEVERITY_RANK[finding.severity] > SEVERITY_RANK[prior.severity]:  # type: ignore[union-attr]
            best[finding.item_id] = finding
    report.findings = [best[k] for k in (i["id"] for i in config.rubric_items()) if k in best]  # type: ignore[misc]
    return report
