"""Structured schemas for every stage of the appraisal pipeline.

The vision model is only ever asked perceptual questions and is constrained to
these shapes. It never emits a price -- pricing is done by the statistical model
in `pricing/`.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Stage 0 -- local image quality (computed with OpenCV, no model involved)
# ---------------------------------------------------------------------------


class ImageQuality(BaseModel):
    filename: str
    width: int = 0
    height: int = 0
    megapixels: float = 0.0
    blur_score: float = Field(0.0, description="Variance of the Laplacian; higher is sharper")
    brightness: float = Field(0.0, description="Mean luminance, 0-255")
    contrast: float = Field(0.0, description="Std-dev of luminance")
    too_blurry: bool = False
    too_dark: bool = False
    too_bright: bool = False
    too_small: bool = False
    usable: bool = True
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage 0 -- per-image triage (what is this a photo of?)
# ---------------------------------------------------------------------------


class SubjectClass(str, Enum):
    """What the photo is actually of. Gate 0 keys off this."""

    truck_tractor = "truck_tractor"
    rigid_truck = "rigid_truck"
    construction_truck = "construction_truck"
    bus_coach = "bus_coach"
    van_light_commercial = "van_light_commercial"
    trailer_semitrailer = "trailer_semitrailer"
    construction_machine = "construction_machine"
    agricultural_tractor = "agricultural_tractor"
    pickup = "pickup"
    passenger_car = "passenger_car"
    motorcycle = "motorcycle"
    truck_part_or_detail = "truck_part_or_detail"
    other_vehicle = "other_vehicle"
    not_a_vehicle = "not_a_vehicle"
    indeterminate = "indeterminate"


HEAVY_VEHICLE_CLASSES = {
    SubjectClass.truck_tractor,
    SubjectClass.rigid_truck,
    SubjectClass.construction_truck,
}

# A detail shot (tire, dash, engine) counts as supporting evidence but cannot on
# its own establish that there is a truck for sale.
SUPPORTING_CLASSES = {SubjectClass.truck_part_or_detail}


class ViewType(str, Enum):
    front = "front"
    front_three_quarter = "front_three_quarter"
    side = "side"
    rear_three_quarter = "rear_three_quarter"
    rear = "rear"
    interior_cab = "interior_cab"
    dashboard_odometer = "dashboard_odometer"
    engine_bay = "engine_bay"
    tire_wheel = "tire_wheel"
    chassis_underside = "chassis_underside"
    fifth_wheel = "fifth_wheel"
    document_or_plate = "document_or_plate"
    detail_other = "detail_other"
    not_applicable = "not_applicable"


class ImageTriage(BaseModel):
    """One of these per uploaded image."""

    subject: SubjectClass = SubjectClass.indeterminate
    subject_confidence: float = Field(0.0, ge=0.0, le=1.0)
    view: ViewType = ViewType.detail_other
    view_confidence: float = Field(0.0, ge=0.0, le=1.0)
    subject_description: str = Field(
        "",
        description="One short sentence describing literally what is in the frame.",
    )
    vehicle_fingerprint: str = Field(
        "",
        description=(
            "Distinguishing marks that let this vehicle be matched across photos: "
            "colour, livery, visible plate, damage, accessories. Empty if no vehicle."
        ),
    )
    visible_plate_text: str = Field("", description="Registration plate text if legible, else empty.")
    is_listing_screenshot: bool = Field(
        False, description="True if this is a screenshot of a web listing rather than a direct photo."
    )
    obstructions: list[str] = Field(
        default_factory=list,
        description="Things blocking assessment: mud, snow, night, rain, glare, cropped, people.",
    )
    usable_for_appraisal: bool = True


class NamedTriage(ImageTriage):
    """Triage result that names which photo it belongs to, for batched calls."""

    filename: str = ""


class BatchTriage(BaseModel):
    images: list[NamedTriage] = Field(default_factory=list)


class TriagedImage(BaseModel):
    filename: str
    quality: ImageQuality
    triage: ImageTriage


# ---------------------------------------------------------------------------
# Stage 1 -- identification
# ---------------------------------------------------------------------------


class AltHypothesis(BaseModel):
    description: str = ""
    probability: float = Field(0.0, ge=0.0, le=1.0)


class Identification(BaseModel):
    make: str = ""
    make_confidence: float = Field(0.0, ge=0.0, le=1.0)
    model_family: str = Field("", description='Series name, e.g. "Actros", "TGX", "XF", "FH", "Cargo".')
    model_confidence: float = Field(0.0, ge=0.0, le=1.0)
    model_variant: str = Field("", description='Numeric designation if legible, e.g. "1845", "18.480", "R450".')
    generation: str = Field("", description='Generation label, e.g. "Actros MP4".')
    generation_year_low: int = Field(0, description="Earliest plausible model year given visual generation cues. 0 if unknown.")
    generation_year_high: int = Field(0, description="Latest plausible model year. 0 if unknown.")
    year_evidence: str = Field("", description="Which visual cues pin the generation: headlight shape, grille, badge, mirror style.")
    cab_type: str = Field("", description='e.g. "high sleeper", "day cab", "BigSpace", "Globetrotter".')
    axle_configuration: str = Field("", description='e.g. "4x2", "6x2", "6x4". Empty if not countable from photos.')
    estimated_power_hp: int = Field(0, description="Only if a badge or model number implies it. 0 otherwise.")
    euro_class: str = Field("", description='e.g. "Euro 6". Only if badged or strongly implied by generation.')
    body_type: str = Field("", description='"tractor_unit", "box", "curtainside", "tipper", "tanker", "flatbed", "mixer", "other".')
    color: str = ""
    identifying_evidence: list[str] = Field(
        default_factory=list, description="Specific visual cues supporting this identification."
    )
    contradicting_evidence: list[str] = Field(
        default_factory=list, description="Anything in the photos inconsistent with this identification."
    )
    alternative_hypotheses: list[AltHypothesis] = Field(default_factory=list)
    same_vehicle_in_all_photos: bool = True
    distinct_vehicle_count: int = Field(1, description="How many different vehicles appear across the photo set.")


# ---------------------------------------------------------------------------
# Stage 2 -- condition
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    none = "none"
    minor = "minor"
    moderate = "moderate"
    severe = "severe"
    not_observable = "not_observable"


SEVERITY_RANK = {
    Severity.not_observable: -1,
    Severity.none: 0,
    Severity.minor: 1,
    Severity.moderate: 2,
    Severity.severe: 3,
}


class BBox(BaseModel):
    """Normalised 0-1 box so the UI can crop the exact evidence region."""

    filename: str = ""
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0


class ConditionFinding(BaseModel):
    item_id: str = Field("", description="Must be one of the rubric item ids supplied in the prompt.")
    severity: Severity = Severity.not_observable
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    observation: str = Field("", description="What is literally visible. Do not speculate beyond the pixels.")
    evidence: list[BBox] = Field(default_factory=list)


class TireAssessment(BaseModel):
    tires_assessable: bool = False
    tires_visible_count: int = 0
    tread_remaining_pct: int = Field(-1, description="Best estimate 0-100, or -1 if not assessable.")
    uneven_wear: bool = False
    mismatched_sizes_or_brands: bool = False
    sidewall_damage_or_cracking: bool = False
    retread_detected: bool = False
    notes: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class WearEstimate(BaseModel):
    """Used for the odometer-versus-wear cross-check."""

    odometer_visible: bool = False
    odometer_reading_km: int = Field(0, description="Digits read from the cluster, 0 if not visible.")
    odometer_confidence: float = Field(0.0, ge=0.0, le=1.0)
    wear_implied_km_low: int = Field(0, description="Lower bound of mileage implied by interior/exterior wear alone.")
    wear_implied_km_high: int = Field(0, description="Upper bound of mileage implied by wear alone.")
    wear_evidence: list[str] = Field(
        default_factory=list,
        description="Steering wheel polish, seat bolster collapse, pedal rubber, gear knob, door sill, step wear.",
    )
    wear_confidence: float = Field(0.0, ge=0.0, le=1.0)


class ConditionReport(BaseModel):
    findings: list[ConditionFinding] = Field(default_factory=list)
    tires: TireAssessment = Field(default_factory=TireAssessment)
    wear: WearEstimate = Field(default_factory=WearEstimate)
    overall_impression: str = Field("", description="Two sentences maximum, strictly grounded in what was seen.")
    roadworthy_concerns: list[str] = Field(
        default_factory=list, description="Anything a buyer should treat as a safety or legality question."
    )
    not_observable: list[str] = Field(
        default_factory=list, description="Rubric areas that the photo set simply does not cover."
    )


# ---------------------------------------------------------------------------
# Stage 3/4/5 -- pricing and the final result
# ---------------------------------------------------------------------------


class SellerClaims(BaseModel):
    """Optional typed input. Treated as a claim to be checked, never as truth."""

    year: int | None = None
    make: str | None = None
    model: str | None = None
    km: int | None = None
    asking_price_try: float | None = None


class PriceRange(BaseModel):
    low: float
    mid: float
    high: float
    currency: str


class Comparable(BaseModel):
    listing_id: str
    source: str
    title: str
    make: str = ""
    model_family: str = ""
    year: int | None = None
    km: int | None = None
    price_eur: float | None = None
    country: str = ""
    url: str = ""
    image_path: str = ""
    similarity: float = 0.0
    match_reasons: list[str] = Field(default_factory=list)


class DeductionLine(BaseModel):
    item_id: str
    label: str
    severity: str
    amount_eur: float
    rationale: str


class MissingView(BaseModel):
    view: str
    why_it_matters: str
    widening_pct: float


class Contradiction(BaseModel):
    field: str
    claimed: str
    observed: str
    detail: str
    severity: str = "warning"


class DuplicatePhotoMatch(BaseModel):
    uploaded_filename: str
    listing_id: str
    source: str
    url: str = ""
    title: str = ""
    price_eur: float | None = None
    hamming_distance: int = 0


class Refusal(BaseModel):
    code: str
    headline: str
    detail: str
    what_to_send: list[str] = Field(default_factory=list)


class PricingBasis(BaseModel):
    """Everything needed to audit the number."""

    model_used: str = ""
    spec_used: dict = Field(default_factory=dict)
    market_baseline_eur: PriceRange | None = None
    total_deductions_eur: float = 0.0
    deductions: list[DeductionLine] = Field(default_factory=list)
    turkiye_multiplier: float = 1.0
    eur_try_rate: float = 0.0
    comparable_count: int = 0
    interval_widening_pct: float = 0.0
    widening_reasons: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ScenarioPrice(BaseModel):
    """When a contradiction cannot be resolved, both readings get priced."""

    label: str
    assumption: str
    price_try: PriceRange
    price_eur: PriceRange


class AppraisalResult(BaseModel):
    status: str = Field("ok", description='"ok", "partial" or "refused"')
    refusal: Refusal | None = None

    confidence: float = Field(0.0, ge=0.0, le=1.0)
    confidence_label: str = ""

    price_try: PriceRange | None = None
    price_eur: PriceRange | None = None
    scenarios: list[ScenarioPrice] = Field(default_factory=list)

    identification: Identification | None = None
    condition: ConditionReport | None = None
    images: list[TriagedImage] = Field(default_factory=list)

    comparables: list[Comparable] = Field(default_factory=list)
    pricing_basis: PricingBasis | None = None

    contradictions: list[Contradiction] = Field(default_factory=list)
    duplicate_photos: list[DuplicatePhotoMatch] = Field(default_factory=list)
    missing_views: list[MissingView] = Field(default_factory=list)
    blind_spots: list[str] = Field(default_factory=list)
    request_photos: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    seller_claims: SellerClaims | None = None
    elapsed_seconds: float = 0.0
    cache_hit: bool = False
