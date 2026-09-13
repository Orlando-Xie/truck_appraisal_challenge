"""Comparable listing retrieval.

The comparables serve two purposes: they are shown to the user so the price is
checkable against real adverts, and their spread is a second, model-independent
read on the market level for that specification.

Retrieval progressively relaxes its constraints so a rare specification still
returns something honest, and every comp records why it was considered a match.
"""

from __future__ import annotations

import json
import math
from datetime import datetime

from scrape import db, normalize
from vision.schemas import Comparable

CURRENT_YEAR = datetime.now().year

# Markets whose price level is most relevant to a Turkish buyer. Central and
# Eastern Europe is where most Turkish used-tractor imports come from.
REGION_PREFERENCE = {"turkiye": 1.0, "eu_east": 0.9, "eu_west": 0.75, "other": 0.5, "north_america": 0.35, "asia": 0.35}


def _region(country: str | None) -> str:
    from pricing.features import region_for

    return region_for(country)


def similarity(spec: dict, row: dict) -> tuple[float, list[str]]:
    """Score how comparable `row` is to `spec`, with human-readable reasons."""
    reasons: list[str] = []
    score = 0.0

    spec_make = normalize.canonical_make(spec.get("make"))
    row_make = normalize.canonical_make(row.get("make"))
    if spec_make and row_make == spec_make:
        score += 3.0
        reasons.append(f"same make ({row_make})")
    elif spec_make:
        return 0.0, []  # a different manufacturer is not a comparable

    spec_family = spec.get("family_canon") or normalize.canonical_family(
        spec_make, spec.get("model_family"), spec.get("model_variant")
    )
    row_family = normalize.canonical_family(row_make, row.get("model_family"), row.get("model_variant"), row.get("title"))
    if spec_family and row_family == spec_family:
        score += 2.5
        reasons.append(f"same model family ({row_family})")

    spec_year = spec.get("year")
    row_year = row.get("year")
    if spec_year and row_year:
        dy = abs(int(spec_year) - int(row_year))
        score += max(0.0, 2.5 - 0.5 * dy)
        if dy == 0:
            reasons.append(f"same model year ({row_year})")
        elif dy <= 2:
            reasons.append(f"within {dy} year{'s' if dy > 1 else ''} ({row_year})")

    spec_km = spec.get("km")
    row_km = row.get("km")
    if spec_km and row_km and spec_km > 0 and row_km > 0:
        ratio = abs(math.log(row_km / spec_km))
        score += max(0.0, 2.0 - 2.5 * ratio)
        if ratio < 0.18:
            reasons.append(f"similar mileage ({int(row_km):,} km)".replace(",", " "))

    spec_power = spec.get("power_hp") or normalize.power_from_designation(
        f"{spec.get('model_family') or ''} {spec.get('model_variant') or ''}"
    )
    row_power = row.get("power_hp") or normalize.power_from_designation(
        f"{row.get('model_family') or ''} {row.get('model_variant') or ''}"
    )
    if spec_power and row_power:
        dp = abs(spec_power - row_power)
        score += max(0.0, 1.2 - dp / 100.0)
        if dp <= 30:
            reasons.append(f"comparable power ({row_power} hp)")

    spec_body = normalize.normalize_body_type(spec.get("body_type"))
    row_body = normalize.normalize_body_type(row.get("body_type"))
    if spec_body and row_body == spec_body:
        score += 1.0
    elif spec_body and row_body != spec_body:
        score -= 1.5

    spec_axle = normalize.normalize_axle(spec.get("axle_config"))
    row_axle = normalize.normalize_axle(row.get("axle_config"))
    if spec_axle and row_axle == spec_axle:
        score += 0.8
        reasons.append(f"same axle configuration ({row_axle})")

    region = _region(row.get("country"))
    score += 1.5 * REGION_PREFERENCE.get(region, 0.5)
    if region == "turkiye":
        reasons.append("listed in Turkiye")

    # Damaged or parts-only vehicles are poor comparables for a running truck.
    cond = normalize.normalize_condition(row.get("condition_flag"))
    if cond in ("crashed", "parts"):
        score -= 2.0
        reasons.append(f"note: advertised as {cond}")

    return score, reasons


def _fetch_candidates(conn, spec: dict, relax: int) -> list[dict]:
    """Progressively wider SQL prefilters."""
    make = normalize.canonical_make(spec.get("make"))
    year = spec.get("year")
    body = normalize.normalize_body_type(spec.get("body_type"))

    clauses = ["price_eur > 0", "body_type != '_negative'", "year > 1990", "km > 1000"]
    params: list = []

    if make:
        # Canonical make is derived, so match on the stored make loosely.
        clauses.append("(make = ? COLLATE NOCASE OR make LIKE ?)")
        params += [make, f"{make.split('-')[0]}%"]

    if year:
        window = [3, 5, 8, 40][min(relax, 3)]
        clauses.append("year BETWEEN ? AND ?")
        params += [int(year) - window, int(year) + window]

    if body and body != "other" and relax < 2:
        clauses.append("body_type = ?")
        params.append(body)

    sql = (
        "SELECT listing_id, source, title, make, model_family, model_variant, body_type, "
        "year, km, power_hp, euro_class, axle_config, condition_flag, country, url, "
        "price_eur, image_urls FROM listings WHERE " + " AND ".join(clauses) + " LIMIT 4000"
    )
    return [dict(r) for r in conn.execute(sql, params)]


def _thumb_for(conn, listing_id: str) -> str:
    row = conn.execute(
        "SELECT local_path FROM listing_images WHERE listing_id = ? AND local_path IS NOT NULL "
        "ORDER BY idx LIMIT 1",
        (listing_id,),
    ).fetchone()
    return row["local_path"].replace("\\", "/") if row else ""


def _eur_for_european_band(comp: Comparable) -> float | None:
    """Comparable price in European-model EUR space.

    Arabam rows are stored as TRY/EURTRY (FX only). That figure already contains
    the Turkish market premium. Blending it into the European band and then
    converting with turkiye_multiplier would count the premium twice.
    """
    if not comp.price_eur:
        return None
    source = (comp.source or "").lower()
    from pricing.features import region_for

    if source == "arabam" or region_for(comp.country) == "turkiye":
        from pricing import fx as fx_mod

        mult = float((fx_mod.load_calibration() or {}).get("turkiye_multiplier") or 1.0)
        if mult > 0:
            return float(comp.price_eur) / mult
    return float(comp.price_eur)


def find_comparables(spec: dict, limit: int = 8, exclude_holdout: bool = False) -> list[Comparable]:
    try:
        conn = db.connect()
    except Exception:
        return []
    try:
        scored: list[tuple[float, list[str], dict]] = []
        for relax in range(4):
            rows = _fetch_candidates(conn, spec, relax)
            scored = []
            for row in rows:
                if exclude_holdout and row.get("is_holdout"):
                    continue
                s, reasons = similarity(spec, row)
                if s > 0:
                    scored.append((s, reasons, row))
            scored.sort(key=lambda t: -t[0])
            if len(scored) >= limit:
                break

        out: list[Comparable] = []
        best = scored[0][0] if scored else 1.0
        for s, reasons, row in scored[:limit]:
            out.append(
                Comparable(
                    listing_id=row["listing_id"],
                    source=row["source"],
                    title=row["title"] or "",
                    make=row["make"] or "",
                    model_family=row["model_family"] or "",
                    year=row["year"],
                    km=row["km"],
                    price_eur=row["price_eur"],
                    country=row["country"] or "",
                    url=row["url"] or "",
                    image_path=_thumb_for(conn, row["listing_id"]),
                    similarity=round(s / best, 3) if best else 0.0,
                    match_reasons=reasons[:4],
                )
            )
        return out
    except Exception:
        return []
    finally:
        conn.close()


def comp_price_stats(comps: list[Comparable]) -> dict:
    """A model-independent read on the market from the comparables themselves."""
    prices = sorted(p for p in (_eur_for_european_band(c) for c in comps) if p)
    if not prices:
        return {}
    n = len(prices)

    def q(p: float) -> float:
        if n == 1:
            return prices[0]
        idx = p * (n - 1)
        lo, hi = int(math.floor(idx)), int(math.ceil(idx))
        return prices[lo] + (prices[hi] - prices[lo]) * (idx - lo)

    sims = [c.similarity for c in comps if getattr(c, "similarity", None) is not None]
    return {
        "n": n,
        "median_similarity": round(float(sorted(sims)[len(sims) // 2]), 3) if sims else 0.0,
        "min_eur": prices[0],
        "p25_eur": round(q(0.25)),
        "median_eur": round(q(0.50)),
        "p75_eur": round(q(0.75)),
        "max_eur": prices[-1],
    }
