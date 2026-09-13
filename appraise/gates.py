"""Abstention gates.

A system that confidently prices a photo of a motorcycle is worse than useless in
a marketplace, so refusing is a first-class output rather than an error path. Each
gate is a hard rule with a stated consequence, and each refusal says what was
actually seen and what to send instead.
"""

from __future__ import annotations

from collections import Counter

import config
from vision.schemas import (
    HEAVY_VEHICLE_CLASSES,
    SUPPORTING_CLASSES,
    Identification,
    Refusal,
    SubjectClass,
    TriagedImage,
    ViewType,
)

# Human-readable names for what the triage step may have found instead.
SUBJECT_LABELS = {
    SubjectClass.truck_tractor: "a heavy tractor unit",
    SubjectClass.rigid_truck: "a rigid truck",
    SubjectClass.construction_truck: "a construction truck",
    SubjectClass.bus_coach: "a bus or coach",
    SubjectClass.van_light_commercial: "a light commercial van",
    SubjectClass.trailer_semitrailer: "a trailer or semi-trailer with no tractor unit attached",
    SubjectClass.construction_machine: "a construction machine",
    SubjectClass.agricultural_tractor: "an agricultural tractor",
    SubjectClass.pickup: "a pickup",
    SubjectClass.passenger_car: "a passenger car",
    SubjectClass.motorcycle: "a motorcycle",
    SubjectClass.truck_part_or_detail: "a close-up of part of a truck",
    SubjectClass.other_vehicle: "some other kind of vehicle",
    SubjectClass.not_a_vehicle: "something that is not a vehicle",
    SubjectClass.indeterminate: "something it could not make out",
}

STANDARD_PHOTO_REQUEST = [
    "A front three-quarter shot of the whole truck, from about 3 metres back, in daylight.",
    "A full side profile so the axles and chassis are visible.",
    "A close-up of one drive tyre showing the tread.",
    "The instrument cluster with the ignition on, so the odometer is readable.",
    "The cab interior, including the seat and steering wheel.",
]


def gate_quality(images: list[TriagedImage]) -> Refusal | None:
    """Gate 1: the photo set is physically unusable."""
    if not images:
        return Refusal(
            code="no_images",
            headline="No photos were received",
            detail="Upload at least one photo of the truck.",
            what_to_send=STANDARD_PHOTO_REQUEST,
        )

    usable = [im for im in images if im.quality.usable]
    if usable:
        return None

    reasons = []
    for im in images:
        notes = ", ".join(im.quality.notes) or "unusable"
        reasons.append(f"{im.filename}: {notes}")
    return Refusal(
        code="unusable_photos",
        headline="These photos are not good enough to appraise from",
        detail=(
            "Every photo failed a basic quality check, so anything said about this truck would be "
            "guesswork. Specifically -- " + "; ".join(reasons) + "."
        ),
        what_to_send=STANDARD_PHOTO_REQUEST,
    )


def gate_subject(images: list[TriagedImage]) -> Refusal | None:
    """Gate 2: there is no heavy commercial vehicle in the photo set."""
    usable = [im for im in images if im.quality.usable]
    pool = usable or images

    confident = [
        im
        for im in pool
        if im.triage.subject in HEAVY_VEHICLE_CLASSES
        and im.triage.subject_confidence >= config.MIN_SUBJECT_CONFIDENCE
    ]
    if confident:
        return None

    supporting = [im for im in pool if im.triage.subject in SUPPORTING_CLASSES]
    counts = Counter(im.triage.subject for im in pool)
    dominant, _ = counts.most_common(1)[0] if counts else (SubjectClass.indeterminate, 0)
    seen = SUBJECT_LABELS.get(dominant, "something unexpected")
    descriptions = [im.triage.subject_description for im in pool if im.triage.subject_description][:3]

    # Detail shots only: a tyre close-up is real evidence, but it cannot establish
    # which truck is for sale.
    if supporting and len(supporting) == len(pool):
        return Refusal(
            code="details_only",
            headline="These are close-ups, not photos of a truck",
            detail=(
                "Every photo shows part of a vehicle rather than the vehicle itself, so there is "
                "nothing to identify or price. The close-ups are useful, but they need to come with "
                "shots of the whole truck."
            ),
            what_to_send=STANDARD_PHOTO_REQUEST[:2] + ["Then re-send the close-ups you already have."],
        )

    if dominant == SubjectClass.trailer_semitrailer:
        return Refusal(
            code="trailer_only",
            headline="This is a trailer, not a truck",
            detail=(
                "The photos show a trailer or semi-trailer with no tractor unit attached. This tool "
                "prices the truck itself; a trailer is valued on completely different criteria "
                "(axles, body type, floor and roof condition, brake type)."
            ),
            what_to_send=["Photos of the tractor unit, if that is what is for sale."],
        )

    if dominant == SubjectClass.indeterminate:
        return Refusal(
            code="unidentifiable",
            headline="Cannot tell what these photos show",
            detail=(
                "No vehicle could be made out in any of these photos"
                + (f". What was visible: {descriptions[0]}" if descriptions else "")
                + ". Rather than guess at a price, this needs usable photos."
            ),
            what_to_send=STANDARD_PHOTO_REQUEST,
        )

    detail = f"These photos show {seen}, not a heavy truck."
    if descriptions:
        detail += " What was seen: " + " ".join(descriptions[:2])
    detail += (
        " This tool only prices heavy commercial vehicles, and a price for anything else would be "
        "meaningless."
    )
    return Refusal(
        code="not_a_truck",
        headline=f"This is not a truck -- it looks like {seen}",
        detail=detail,
        what_to_send=["Photos of the truck you want appraised."],
    )


def gate_multiple_vehicles(images: list[TriagedImage], ident: Identification | None) -> str | None:
    """Gate 3: more than one physical vehicle in the set.

    A warning rather than a refusal: often the second vehicle is a parked truck in
    the background, but the user has to know which one was priced.
    """
    if ident and (not ident.same_vehicle_in_all_photos or ident.distinct_vehicle_count > 1):
        n = max(2, ident.distinct_vehicle_count)
        return (
            f"These photos appear to show {n} different vehicles. The appraisal covers the one that "
            f"appears in the most photos. If that is wrong, upload photos of a single truck."
        )

    fingerprints = {
        im.triage.vehicle_fingerprint.strip().lower()
        for im in images
        if im.triage.vehicle_fingerprint.strip()
        and im.triage.subject in HEAVY_VEHICLE_CLASSES
    }
    if len(fingerprints) > 2:
        return (
            "The trucks in these photos do not look like the same vehicle. Check that every photo "
            "is of the truck being sold."
        )
    return None


def gate_identification(ident: Identification | None) -> tuple[str, list[str]]:
    """Gate 4: how far the identification can be trusted.

    Returns (confidence tier, notes). The tier drives how much the interval is
    widened rather than whether an answer is given at all: a buyer is still helped
    by "some kind of Actros, somewhere in this range".
    """
    notes: list[str] = []
    if ident is None:
        return "none", ["The vehicle could not be identified at all."]

    if not ident.make or ident.make_confidence < config.MIN_MAKE_CONFIDENCE:
        notes.append(
            "The manufacturer could not be established from these photos, so the estimate is based "
            "on the general market for this class of truck rather than on this model."
        )
        return "make_unknown", notes

    if not ident.model_family or ident.model_confidence < config.MIN_MODEL_CONFIDENCE:
        notes.append(
            f"The make ({ident.make}) is clear but the model could not be pinned down, so the "
            "estimate spans the range of models this manufacturer sells in this class."
        )
        return "model_unknown", notes

    if not ident.model_variant:
        notes.append(
            f"The model ({ident.make} {ident.model_family}) is clear but no power designation was "
            "legible. Engine power moves the price, so the range covers the usual variants."
        )
        return "variant_unknown", notes

    if not (ident.generation_year_low and ident.generation_year_high):
        notes.append("The build year could not be narrowed from the visual generation cues.")
        return "year_unknown", notes

    return "ok", notes


def missing_views(images: list[TriagedImage]) -> list[str]:
    """Which required inspection views the photo set does not provide."""
    widening = config.rubric()["widening"]["missing_view_pct"]
    present = {
        im.triage.view
        for im in images
        if im.quality.usable and im.triage.view_confidence >= 0.35
    }
    # A front or rear three-quarter shot both establish the general exterior.
    equivalent = {
        ViewType.front_three_quarter: {ViewType.front_three_quarter, ViewType.front, ViewType.rear_three_quarter},
        ViewType.side: {ViewType.side},
        ViewType.tire_wheel: {ViewType.tire_wheel},
        ViewType.dashboard_odometer: {ViewType.dashboard_odometer},
        ViewType.interior_cab: {ViewType.interior_cab, ViewType.dashboard_odometer},
        ViewType.engine_bay: {ViewType.engine_bay},
        ViewType.fifth_wheel: {ViewType.fifth_wheel, ViewType.rear_three_quarter},
    }
    missing = []
    for view_name in widening:
        try:
            view = ViewType(view_name)
        except ValueError:
            continue
        accepted = equivalent.get(view, {view})
        if not (present & accepted):
            missing.append(view_name)
    return missing
