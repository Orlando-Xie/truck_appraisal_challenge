"""Reconcile what the photos show with what the seller typed.

Seller-typed details are treated as an unverified claim throughout. Where a claim
and the photos disagree, both are reported and, when the disagreement is
material, both are priced. Silently trusting typed input would defeat the point
of appraising from photos.
"""

from __future__ import annotations

from datetime import datetime

import config
from scrape import db, normalize
from vision.schemas import (
    ConditionReport,
    Contradiction,
    Identification,
    SellerClaims,
    TriagedImage,
)

CURRENT_YEAR = datetime.now().year


def detect_contradictions(
    ident: Identification | None,
    condition: ConditionReport | None,
    claims: SellerClaims | None,
) -> list[Contradiction]:
    out: list[Contradiction] = []
    if ident is None:
        return out

    # Anything the identification step itself flagged as inconsistent.
    for note in ident.contradicting_evidence:
        if note.strip():
            out.append(
                Contradiction(
                    field="identification",
                    claimed="",
                    observed=note.strip(),
                    detail=note.strip(),
                    severity="note",
                )
            )

    if claims:
        if claims.year and ident.generation_year_low and ident.generation_year_high:
            lo, hi = ident.generation_year_low, ident.generation_year_high
            if not (lo - 1 <= claims.year <= hi + 1):
                gen = ident.generation or f"{ident.make} {ident.model_family}".strip()
                out.append(
                    Contradiction(
                        field="year",
                        claimed=str(claims.year),
                        observed=f"{lo}-{hi}",
                        detail=(
                            f"The seller says {claims.year}, but the visual generation cues point to "
                            f"{gen}, which was built {lo}-{hi}. "
                            + (ident.year_evidence or "")
                        ).strip(),
                        severity="warning",
                    )
                )

        if claims.make and ident.make and ident.make_confidence >= config.MIN_MAKE_CONFIDENCE:
            if normalize.canonical_make(claims.make) != normalize.canonical_make(ident.make):
                out.append(
                    Contradiction(
                        field="make",
                        claimed=claims.make,
                        observed=ident.make,
                        detail=(
                            f"The seller says {claims.make}, but the badging and body shape read as "
                            f"{ident.make}."
                        ),
                        severity="warning",
                    )
                )

    # Odometer versus independently estimated wear.
    if condition is not None:
        mileage = mileage_check(condition, claims)
        if mileage:
            out.append(mileage)
    return out


def mileage_check(condition: ConditionReport, claims: SellerClaims | None) -> Contradiction | None:
    """Compare the odometer against wear estimated independently of it.

    Odometer rollback is a real problem in this market, and interior wear is the
    cheapest available cross-check.
    """
    cfg = config.rubric()["mileage_check"]
    threshold = float(cfg["discrepancy_flag_fraction"])
    wear = condition.wear

    stated = None
    source = ""
    if wear.odometer_visible and wear.odometer_reading_km > 0:
        stated = wear.odometer_reading_km
        source = "the odometer in the photos"
    elif claims and claims.km:
        stated = claims.km
        source = "the mileage the seller typed"

    if not stated or wear.wear_implied_km_low <= 0 or wear.wear_implied_km_high <= 0:
        return None
    if wear.wear_confidence < 0.35:
        return None

    implied_low = min(wear.wear_implied_km_low, wear.wear_implied_km_high)
    if implied_low <= stated * (1 + threshold):
        return None

    excess = implied_low / stated - 1
    evidence = "; ".join(wear.wear_evidence[:4]) or "general interior and cab wear"
    return Contradiction(
        field="km",
        claimed=f"{stated:,} km".replace(",", " "),
        observed=f"{implied_low:,}-{wear.wear_implied_km_high:,} km".replace(",", " "),
        detail=(
            f"Wear on this truck looks like at least {implied_low:,} km".replace(",", " ")
            + f", which is {excess:.0%} above {source}. Based on: {evidence}. "
            "That gap is worth checking against service records and the tachograph before buying."
        ),
        severity="alert",
    )


def resolve_year(ident: Identification | None, claims: SellerClaims | None) -> tuple[int | None, str]:
    """Pick the year to price against, and say where it came from."""
    if claims and claims.year and 1980 < claims.year <= CURRENT_YEAR + 1:
        if ident and ident.generation_year_low and ident.generation_year_high:
            if ident.generation_year_low - 1 <= claims.year <= ident.generation_year_high + 1:
                return claims.year, "the seller's stated year, consistent with the visual generation"
        else:
            return claims.year, "the seller's stated year (no visual generation cue to check it against)"

    if ident and ident.generation_year_low and ident.generation_year_high:
        lo, hi = ident.generation_year_low, ident.generation_year_high
        typical = _corpus_median_year(ident.make, ident.model_family, lo, hi)
        if typical and lo <= typical <= hi:
            return typical, (
                f"the typical build year for this generation in the comparable corpus "
                f"({lo}-{hi}), not a single year read from a plate"
            )
        mid = int((lo + hi) / 2)
        return mid, (
            f"an estimated year inside the {ident.generation or 'identified'} generation "
            f"({lo}-{hi}); the exact build year was not visible"
        )

    if claims and claims.year:
        return claims.year, "the seller's stated year, which could not be checked from the photos"
    return None, "no year could be established"


def _corpus_median_year(make: str | None, family: str | None, lo: int, hi: int) -> int | None:
    """Median advertised year for this family inside a generation window."""
    try:
        conn = db.connect()
    except Exception:
        return None
    try:
        def _query(use_family: bool) -> int | None:
            clauses = ["year > 1995", "price_eur > 0", "body_type != '_negative'", "year BETWEEN ? AND ?"]
            params: list = [int(lo), int(hi)]
            if make:
                clauses.append("make = ? COLLATE NOCASE")
                params.append(normalize.canonical_make(make))
            if use_family and family:
                clauses.append("model_family LIKE ? COLLATE NOCASE")
                params.append(f"%{str(family).strip()}%")
            rows = conn.execute(
                "SELECT year FROM listings WHERE " + " AND ".join(clauses) + " ORDER BY year",
                params,
            ).fetchall()
            years = [int(r["year"]) for r in rows if r["year"]]
            if len(years) < 8:
                return None
            return years[len(years) // 2]

        return _query(True) or _query(False)
    except Exception:
        return None
    finally:
        conn.close()


def _corpus_median_km(make: str | None, family: str | None, year: int | None) -> int | None:
    """Typical mileage for this kind of truck at this age, from the corpus."""
    try:
        conn = db.connect()
    except Exception:
        return None
    try:
        clauses = ["km > 1000", "price_eur > 0", "body_type != '_negative'"]
        params: list = []
        if make:
            clauses.append("make = ? COLLATE NOCASE")
            params.append(normalize.canonical_make(make))
        if year:
            clauses.append("year BETWEEN ? AND ?")
            params += [year - 2, year + 2]
        rows = conn.execute(
            "SELECT km FROM listings WHERE " + " AND ".join(clauses) + " ORDER BY km", params
        ).fetchall()
        kms = [r["km"] for r in rows if r["km"]]
        if len(kms) < 5:
            return None
        return int(kms[len(kms) // 2])
    except Exception:
        return None
    finally:
        conn.close()


def resolve_km(
    condition: ConditionReport | None,
    claims: SellerClaims | None,
    ident: Identification | None,
    year: int | None,
) -> tuple[int | None, str, bool]:
    """Pick the mileage to price against.

    Returns (km, provenance, is_estimated). Mileage moves the price more than
    anything except the model itself, so where the number came from is reported
    rather than buried.
    """
    if ident and ident.odometer_reading_km > 1000:
        return ident.odometer_reading_km, "the odometer digits read from a dashboard crop", False

    wear = condition.wear if condition else None

    if wear and wear.odometer_visible and wear.odometer_reading_km > 1000 and wear.odometer_confidence >= 0.45:
        odo = wear.odometer_reading_km
        # If wear contradicts the odometer badly, price the wear-implied figure
        # instead: the more pessimistic reading is the safer one for a buyer.
        cfg = config.rubric()["mileage_check"]
        if (
            wear.wear_implied_km_low > odo * (1 + float(cfg["discrepancy_flag_fraction"]))
            and wear.wear_confidence >= 0.35
        ):
            return (
                wear.wear_implied_km_low,
                f"wear-implied mileage, because the odometer reading of {odo:,} km".replace(",", " ")
                + " is not consistent with how worn this truck looks",
                True,
            )
        return odo, "the odometer read from the dashboard photo", False

    if claims and claims.km and claims.km > 1000:
        return claims.km, "the mileage the seller typed (not visible in any photo)", False

    if wear and wear.wear_implied_km_low > 0 and wear.wear_implied_km_high > 0 and wear.wear_confidence >= 0.3:
        mid = int((wear.wear_implied_km_low + wear.wear_implied_km_high) / 2)
        return mid, "mileage estimated from visible wear, since no odometer photo was supplied", True

    median = _corpus_median_km(ident.make if ident else None, ident.model_family if ident else None, year)
    if median:
        return (
            median,
            f"the typical mileage for a truck of this age in the comparables corpus "
            f"({median:,} km)".replace(",", " ")
            + ", because nothing in the photos shows the real figure",
            True,
        )
    return None, "no mileage could be established", True


def build_spec(
    ident: Identification | None,
    condition: ConditionReport | None,
    claims: SellerClaims | None,
    images: list[TriagedImage],
) -> tuple[dict, dict]:
    """Assemble the feature spec for the pricing model, with provenance."""
    year, year_src = resolve_year(ident, claims)
    km, km_src, km_estimated = resolve_km(condition, claims, ident, year)

    make = (ident.make if ident else None) or (claims.make if claims else None) or ""
    family_text = (ident.model_family if ident else None) or (claims.model if claims else None) or ""
    variant = ident.model_variant if ident else ""

    generation = ident.generation if ident else ""
    spec = {
        "make": make,
        "model_family": family_text,
        "model_variant": variant,
        "family_canon": normalize.canonical_family(make, family_text, variant),
        "generation": generation,
        "generation_canon": normalize.canonical_generation(make, family_text, year, generation),
        "body_type": (ident.body_type if ident else "") or "tractor_unit",
        "axle_config": (ident.axle_configuration if ident else "") or "",
        "euro_class": (ident.euro_class if ident else "") or "",
        "power_hp": (ident.estimated_power_hp if ident and ident.estimated_power_hp else None),
        "year": year,
        "km": km,
        "condition": "used",
        # Priced as a Turkish-market vehicle regardless of where comparables came from.
        "country": "Turkey",
    }
    year_estimated = bool(year) and (
        "typical build year" in year_src or "estimated year" in year_src or "midpoint" in year_src
    )
    provenance = {
        "year": year_src,
        "year_estimated": year_estimated,
        "km": km_src,
        "km_estimated": km_estimated,
        "make": "read from the photos" if ident and ident.make else "not established",
        "model": "read from the photos" if ident and ident.model_family else "not established",
    }
    return spec, provenance
