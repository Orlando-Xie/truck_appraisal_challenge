import type { PriceRange, Severity } from "./types";

const trFormatter = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 });
const enFormatter = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });

export function money(value: number, currency: string): string {
  const n = currency === "TRY" ? trFormatter.format(value) : enFormatter.format(value);
  return currency === "TRY" ? `${n} TL` : `€${n}`;
}

export function compactMoney(value: number, currency: string): string {
  if (currency === "TRY") {
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 1 : 2)}M TL`;
    return `${trFormatter.format(Math.round(value / 1000))}K TL`;
  }
  if (value >= 1000) return `€${enFormatter.format(Math.round(value / 1000))}K`;
  return `€${enFormatter.format(value)}`;
}

export function km(value: number | null | undefined): string {
  if (!value) return "—";
  return `${enFormatter.format(value)} km`;
}

export function rangeSpread(range: PriceRange): string {
  if (!range.mid) return "";
  return `±${Math.round(((range.high - range.low) / 2 / range.mid) * 100)}%`;
}

export const SEVERITY_STYLE: Record<Severity, { label: string; chip: string; dot: string }> = {
  none: {
    label: "Looks fine",
    chip: "bg-accent-500/12 text-accent-300 border-accent-500/25",
    dot: "bg-accent-400",
  },
  minor: {
    label: "Minor",
    chip: "bg-yellow-500/12 text-yellow-200 border-yellow-500/25",
    dot: "bg-yellow-400",
  },
  moderate: {
    label: "Moderate",
    chip: "bg-orange-500/12 text-orange-200 border-orange-500/25",
    dot: "bg-orange-400",
  },
  severe: {
    label: "Severe",
    chip: "bg-red-500/14 text-red-200 border-red-500/30",
    dot: "bg-red-400",
  },
  not_observable: {
    label: "Can't see it",
    chip: "bg-ink-700/60 text-ink-300 border-ink-600",
    dot: "bg-ink-400",
  },
};

export function confidenceStyle(label: string): string {
  switch (label) {
    case "High":
      return "bg-accent-500/15 text-accent-300 border-accent-500/30";
    case "Moderate":
      return "bg-yellow-500/15 text-yellow-200 border-yellow-500/30";
    case "Low":
      return "bg-orange-500/15 text-orange-200 border-orange-500/30";
    default:
      return "bg-red-500/15 text-red-200 border-red-500/30";
  }
}

export const VIEW_LABELS: Record<string, string> = {
  front: "Front",
  front_three_quarter: "Front 3/4",
  side: "Side profile",
  rear_three_quarter: "Rear 3/4",
  rear: "Rear",
  interior_cab: "Cab interior",
  dashboard_odometer: "Dashboard",
  engine_bay: "Engine bay",
  tire_wheel: "Tyre",
  chassis_underside: "Chassis",
  fifth_wheel: "Fifth wheel",
  document_or_plate: "Document",
  detail_other: "Detail",
  not_applicable: "—",
};

export const SUBJECT_LABELS: Record<string, string> = {
  truck_tractor: "Tractor unit",
  rigid_truck: "Rigid truck",
  construction_truck: "Construction truck",
  bus_coach: "Bus",
  van_light_commercial: "Van",
  trailer_semitrailer: "Trailer",
  construction_machine: "Machine",
  agricultural_tractor: "Farm tractor",
  pickup: "Pickup",
  passenger_car: "Car",
  motorcycle: "Motorcycle",
  truck_part_or_detail: "Truck detail",
  other_vehicle: "Other vehicle",
  not_a_vehicle: "Not a vehicle",
  indeterminate: "Unclear",
};

export function titleCase(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
