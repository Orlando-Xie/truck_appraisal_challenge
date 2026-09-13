"""Feature engineering for the hedonic price model.

The same code path builds training rows from the scraped corpus and inference
rows from what the vision stage saw, so a spec inferred from photos lands in
exactly the same feature space as a real listing.
"""

from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd

from scrape import db, normalize

CURRENT_YEAR = datetime.now().year

CATEGORICAL = ["make_canon", "family_canon", "body_canon", "axle_canon", "condition_canon", "region"]
NUMERIC = ["age", "log_km", "power_hp_filled", "euro_filled", "has_power", "has_euro", "has_km"]
FEATURES = CATEGORICAL + NUMERIC
TARGET = "log_price_eur"

# Tree models with native categorical support cap cardinality at 255, and a model
# family seen twice carries no signal anyway. Rare values collapse to "other",
# which also gives inference a well-defined bucket for a family the corpus has
# never seen.
MAX_CATEGORIES = 240
MIN_CATEGORY_COUNT = 4
OTHER = "other"

# Sanity bounds. Anything outside these is a data error or a vehicle so unusual
# it would only add noise.
PRICE_MIN_EUR = 1500.0
PRICE_MAX_EUR = 400_000.0
YEAR_MIN = 1990
KM_MIN = 1_000
KM_MAX = 3_000_000

# Listing countries grouped into markets that price similarly. Country-level
# one-hot would be too sparse for the smaller markets.
REGION_BY_COUNTRY = {
    # Western / Northern Europe: the deepest and most expensive used market.
    "Germany": "eu_west", "Netherlands": "eu_west", "Belgium": "eu_west", "France": "eu_west",
    "Austria": "eu_west", "Switzerland": "eu_west", "Luxembourg": "eu_west", "Denmark": "eu_west",
    "Sweden": "eu_west", "Norway": "eu_west", "Finland": "eu_west", "Ireland": "eu_west",
    "United Kingdom": "eu_west", "Italy": "eu_west", "Spain": "eu_west", "Portugal": "eu_west",
    # Central / Eastern Europe: the main source of exports into Turkiye.
    "Poland": "eu_east", "Czechia": "eu_east", "Czech Republic": "eu_east", "Slovakia": "eu_east",
    "Hungary": "eu_east", "Romania": "eu_east", "Bulgaria": "eu_east", "Slovenia": "eu_east",
    "Croatia": "eu_east", "Lithuania": "eu_east", "Latvia": "eu_east", "Estonia": "eu_east",
    "Serbia": "eu_east", "Greece": "eu_east", "Bosnia and Herzegovina": "eu_east",
    "North Macedonia": "eu_east", "Albania": "eu_east", "Montenegro": "eu_east",
    "Ukraine": "eu_east", "Belarus": "eu_east", "Moldova": "eu_east",
    "Turkey": "turkiye", "Turkiye": "turkiye", "Türkiye": "turkiye",
    "USA": "north_america", "United States": "north_america", "Canada": "north_america",
    "Mexico": "north_america",
    "China": "asia", "India": "asia", "Japan": "asia", "South Korea": "asia",
    "United Arab Emirates": "middle_east", "Saudi Arabia": "middle_east",
}


def region_for(country: str | None) -> str:
    if not country:
        return "other"
    return REGION_BY_COUNTRY.get(country.strip(), "other")


def load_corpus(exclude_holdout: bool = True, include_negatives: bool = False) -> pd.DataFrame:
    """Load priced listings and attach canonical columns."""
    conn = db.connect()
    sql = (
        "SELECT listing_id, source, category, url, title, make, model_family, model_variant, "
        "body_type, year, km, power_hp, euro_class, axle_config, suspension, fuel, "
        "condition_flag, country, city, seller, seller_verified, price_eur, price_native, "
        "price_currency, photo_count, image_urls, is_holdout "
        "FROM listings WHERE price_eur IS NOT NULL AND price_eur > 0"
    )
    if exclude_holdout:
        sql += " AND is_holdout = 0"
    if not include_negatives:
        sql += " AND body_type != '_negative'"
    df = pd.DataFrame([dict(r) for r in conn.execute(sql)])
    conn.close()
    if df.empty:
        return df
    return add_canonical_columns(df)


def add_canonical_columns(df: pd.DataFrame) -> pd.DataFrame:
    records = [normalize.normalize_listing(row) for row in df.to_dict("records")]
    return pd.DataFrame(records)


def build_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Apply sanity filters and derive model features."""
    if df.empty:
        return df
    out = df.copy()

    out = out[(out["price_eur"] >= PRICE_MIN_EUR) & (out["price_eur"] <= PRICE_MAX_EUR)]
    out = out[out["year"].notna() & (out["year"] >= YEAR_MIN) & (out["year"] <= CURRENT_YEAR + 1)]
    out = out[out["km"].notna() & (out["km"] >= KM_MIN) & (out["km"] <= KM_MAX)]
    out = out[out["make_canon"].astype(str).str.len() > 0]

    out = derive_features(out)
    out[TARGET] = np.log(out["price_eur"].astype(float))
    return out.reset_index(drop=True)


def derive_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["age"] = (CURRENT_YEAR - out["year"].astype(float)).clip(lower=0, upper=45)
    km = out["km"].astype(float).clip(lower=KM_MIN, upper=KM_MAX)
    out["log_km"] = np.log(km)
    out["has_km"] = out["km"].notna().astype(int)

    power = pd.to_numeric(out.get("power_hp_canon"), errors="coerce")
    out["has_power"] = power.notna().astype(int)
    # Median-fill keeps the row usable; the has_* flag lets the model learn that
    # a missing spec is itself informative.
    out["power_hp_filled"] = power.fillna(power.median() if power.notna().any() else 430.0)

    euro = pd.to_numeric(out.get("euro_canon"), errors="coerce")
    out["has_euro"] = euro.notna().astype(int)
    out["euro_filled"] = euro.fillna(0)

    out["region"] = out["country"].apply(region_for) if "country" in out else "other"

    for col in CATEGORICAL:
        if col not in out:
            out[col] = "unknown"
        out[col] = out[col].fillna("").astype(str).replace("", "unknown")
    return out


def as_model_matrix(df: pd.DataFrame, categories: dict[str, list[str]] | None = None) -> pd.DataFrame:
    """Return the feature matrix with stable categorical dtypes.

    Passing `categories` from the trained artifact guarantees inference sees the
    same category ordering as training, which matters for tree-based models with
    native categorical support.
    """
    X = df[FEATURES].copy()
    for col in CATEGORICAL:
        if categories and col in categories:
            vocab = categories[col]
            values = X[col].astype(str).where(X[col].astype(str).isin(vocab), OTHER)
            X[col] = pd.Categorical(values, categories=vocab)
        else:
            X[col] = X[col].astype("category")
    for col in NUMERIC:
        X[col] = pd.to_numeric(X[col], errors="coerce").astype(float)
    return X


def category_map(df: pd.DataFrame) -> dict[str, list[str]]:
    """Build the categorical vocabulary, collapsing the long tail into "other"."""
    vocab: dict[str, list[str]] = {}
    for col in CATEGORICAL:
        counts = df[col].astype(str).value_counts()
        keep = [v for v in counts[counts >= MIN_CATEGORY_COUNT].index.tolist()[:MAX_CATEGORIES]]
        if OTHER not in keep:
            keep.append(OTHER)
        vocab[col] = sorted(set(keep))
    return vocab


def spec_to_row(spec: dict) -> pd.DataFrame:
    """Turn an inferred vehicle spec into a single-row frame ready for the model."""
    make = normalize.canonical_make(spec.get("make"))
    family = spec.get("family_canon") or normalize.canonical_family(
        make, spec.get("model_family"), spec.get("model_variant")
    )
    power = spec.get("power_hp") or normalize.power_from_designation(
        f"{spec.get('model_family') or ''} {spec.get('model_variant') or ''}"
    )
    row = {
        "make_canon": make or "unknown",
        "family_canon": family or "unknown",
        "body_canon": normalize.normalize_body_type(spec.get("body_type")) or "other",
        "axle_canon": normalize.normalize_axle(spec.get("axle_config")) or "unknown",
        "condition_canon": spec.get("condition") or "used",
        "country": spec.get("country") or "Turkey",
        "year": spec.get("year"),
        "km": spec.get("km"),
        "power_hp_canon": power,
        "euro_canon": normalize.euro_number(spec.get("euro_class")),
        "price_eur": np.nan,
    }
    df = pd.DataFrame([row])
    return derive_features(df)
