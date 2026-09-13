export type Severity = "none" | "minor" | "moderate" | "severe" | "not_observable";

export interface PriceRange {
  low: number;
  mid: number;
  high: number;
  currency: string;
}

export interface BBox {
  filename: string;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface ConditionFinding {
  item_id: string;
  severity: Severity;
  confidence: number;
  observation: string;
  evidence: BBox[];
}

export interface TireAssessment {
  tires_assessable: boolean;
  tires_visible_count: number;
  tread_remaining_pct: number;
  uneven_wear: boolean;
  mismatched_sizes_or_brands: boolean;
  sidewall_damage_or_cracking: boolean;
  retread_detected: boolean;
  notes: string;
  confidence: number;
}

export interface WearEstimate {
  odometer_visible: boolean;
  odometer_reading_km: number;
  odometer_confidence: number;
  wear_implied_km_low: number;
  wear_implied_km_high: number;
  wear_evidence: string[];
  wear_confidence: number;
}

export interface ConditionReport {
  findings: ConditionFinding[];
  tires: TireAssessment;
  wear: WearEstimate;
  overall_impression: string;
  roadworthy_concerns: string[];
  not_observable: string[];
}

export interface AltHypothesis {
  description: string;
  probability: number;
}

export interface Identification {
  make: string;
  make_confidence: number;
  model_family: string;
  model_confidence: number;
  model_variant: string;
  generation: string;
  generation_year_low: number;
  generation_year_high: number;
  year_evidence: string;
  cab_type: string;
  axle_configuration: string;
  estimated_power_hp: number;
  euro_class: string;
  body_type: string;
  color: string;
  identifying_evidence: string[];
  contradicting_evidence: string[];
  alternative_hypotheses: AltHypothesis[];
  same_vehicle_in_all_photos: boolean;
  distinct_vehicle_count: number;
}

export interface ImageQuality {
  filename: string;
  width: number;
  height: number;
  megapixels: number;
  blur_score: number;
  brightness: number;
  contrast: number;
  too_blurry: boolean;
  too_dark: boolean;
  too_bright: boolean;
  too_small: boolean;
  usable: boolean;
  notes: string[];
}

export interface ImageTriage {
  subject: string;
  subject_confidence: number;
  view: string;
  view_confidence: number;
  subject_description: string;
  vehicle_fingerprint: string;
  visible_plate_text: string;
  is_listing_screenshot: boolean;
  obstructions: string[];
  usable_for_appraisal: boolean;
}

export interface TriagedImage {
  filename: string;
  quality: ImageQuality;
  triage: ImageTriage;
}

export interface Comparable {
  listing_id: string;
  source: string;
  title: string;
  make: string;
  model_family: string;
  year: number | null;
  km: number | null;
  price_eur: number | null;
  country: string;
  url: string;
  image_path: string;
  similarity: number;
  match_reasons: string[];
}

export interface DeductionLine {
  item_id: string;
  label: string;
  severity: string;
  amount_eur: number;
  rationale: string;
}

export interface MissingView {
  view: string;
  why_it_matters: string;
  widening_pct: number;
}

export interface Contradiction {
  field: string;
  claimed: string;
  observed: string;
  detail: string;
  severity: string;
}

export interface DuplicatePhotoMatch {
  uploaded_filename: string;
  listing_id: string;
  source: string;
  url: string;
  title: string;
  price_eur: number | null;
  hamming_distance: number;
}

export interface Refusal {
  code: string;
  headline: string;
  detail: string;
  what_to_send: string[];
}

export interface PricingBasis {
  model_used: string;
  spec_used: Record<string, unknown>;
  market_baseline_eur: PriceRange | null;
  total_deductions_eur: number;
  deductions: DeductionLine[];
  turkiye_multiplier: number;
  eur_try_rate: number;
  comparable_count: number;
  interval_widening_pct: number;
  widening_reasons: string[];
  notes: string[];
}

export interface ScenarioPrice {
  label: string;
  assumption: string;
  price_try: PriceRange;
  price_eur: PriceRange;
}

export interface AppraisalResult {
  status: "ok" | "partial" | "refused";
  refusal: Refusal | null;
  confidence: number;
  confidence_label: string;
  price_try: PriceRange | null;
  price_eur: PriceRange | null;
  scenarios: ScenarioPrice[];
  identification: Identification | null;
  condition: ConditionReport | null;
  images: TriagedImage[];
  comparables: Comparable[];
  pricing_basis: PricingBasis | null;
  contradictions: Contradiction[];
  duplicate_photos: DuplicatePhotoMatch[];
  missing_views: MissingView[];
  blind_spots: string[];
  request_photos: string[];
  warnings: string[];
  seller_claims: Record<string, unknown> | null;
  elapsed_seconds: number;
  cache_hit: boolean;
}

export interface RubricItem {
  id: string;
  label: string;
  role: string;
}

export interface PipelineInfo {
  vision: { provider: string; flash_model: string; pro_model: string };
  price_model: {
    available: boolean;
    trained_at?: string;
    n_train?: number;
    n_test?: number;
    metrics_test?: Record<string, number>;
    interval_factor?: number;
  };
  calibration: {
    turkiye_multiplier: number;
    eur_try: number;
    source: string;
    n_listings: number;
    notes: string;
  };
  rubric_items: RubricItem[];
  stages: { id: string; label: string }[];
  max_images: number;
}

export interface SampleCase {
  id: string;
  label: string;
  description: string;
  kind: string;
  files: string[];
  truth?: Record<string, unknown>;
  claims?: Record<string, unknown>;
}

export interface ProgressEvent {
  stage: string;
  message: string;
  data: Record<string, unknown>;
}
