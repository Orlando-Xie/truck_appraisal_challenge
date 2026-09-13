"""Train the hedonic quantile price model.

Three gradient-boosted quantile regressors (q10, q50, q90) are fitted on
log price. The interval therefore comes from the observed dispersion of the real
market at that specification, not from a language model being asked to produce a
range.

The q10/q90 pair is then calibrated on a held-out split so the stated interval
actually contains the asking price about 80 percent of the time. Without that
step quantile regressors on log targets are usually over-confident.

Usage::

    python -m pricing.train
    python -m pricing.train --holdout 120
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import train_test_split

import config
from pricing import features as F
from scrape import db

log = logging.getLogger("train")

QUANTILES = {"q10": 0.10, "q50": 0.50, "q90": 0.90}

MODEL_PARAMS = dict(
    max_iter=500,
    learning_rate=0.06,
    max_depth=None,
    max_leaf_nodes=31,
    min_samples_leaf=20,
    l2_regularization=1.0,
    early_stopping=True,
    n_iter_no_change=30,
    validation_fraction=0.12,
    random_state=42,
)


def mark_holdout(n: int) -> int:
    """Reserve listings with photos for the blind evaluation.

    The holdout is chosen from listings that have several images, because the
    evaluation must run the real pipeline on the photos alone.
    """
    conn = db.connect()
    conn.execute("UPDATE listings SET is_holdout = 0")
    conn.commit()
    rows = conn.execute(
        "SELECT listing_id FROM listings "
        "WHERE price_eur > 0 AND year > 1995 AND km > 1000 "
        "AND body_type != '_negative' "
        "AND json_array_length(image_urls) >= 4 "
        "AND make != '' "
        # Spread the holdout across the price range rather than taking whatever
        # the default ordering gives.
        "ORDER BY (price_eur * 1000) % 997, listing_id "
        "LIMIT ?",
        (n,),
    ).fetchall()
    ids = [r["listing_id"] for r in rows]
    conn.executemany("UPDATE listings SET is_holdout = 1 WHERE listing_id = ?", [(i,) for i in ids])
    conn.commit()
    conn.close()
    log.info("marked %d listings as evaluation holdout", len(ids))
    return len(ids)


def evaluate(models: dict, X: pd.DataFrame, y: np.ndarray, prices: np.ndarray) -> dict:
    pred_log = {k: m.predict(X) for k, m in models.items()}
    mid = np.exp(pred_log["q50"])
    lo = np.exp(pred_log["q10"])
    hi = np.exp(pred_log["q90"])

    ape = np.abs(mid - prices) / prices
    inside = (prices >= lo) & (prices <= hi)
    width = (hi - lo) / np.maximum(mid, 1)

    return {
        "n": int(len(prices)),
        "median_ape": float(np.median(ape)),
        "mean_ape": float(np.mean(ape)),
        "within_10pct": float(np.mean(ape <= 0.10)),
        "within_20pct": float(np.mean(ape <= 0.20)),
        "within_30pct": float(np.mean(ape <= 0.30)),
        "interval_coverage": float(np.mean(inside)),
        "median_interval_width_ratio": float(np.median(width)),
        "median_price_eur": float(np.median(prices)),
    }


def calibrate_interval(models: dict, X: pd.DataFrame, prices: np.ndarray, target: float = 0.80) -> float:
    """Find the multiplicative widening that hits the target coverage.

    Returns a factor applied symmetrically in log space to the q10/q90 pair.
    """
    mid = np.exp(models["q50"].predict(X))
    lo = np.exp(models["q10"].predict(X))
    hi = np.exp(models["q90"].predict(X))

    best_factor, best_gap = 1.0, 9.9
    for factor in np.arange(0.6, 3.01, 0.05):
        lo_f = mid * np.power(np.maximum(lo, 1) / np.maximum(mid, 1), factor)
        hi_f = mid * np.power(np.maximum(hi, 1) / np.maximum(mid, 1), factor)
        coverage = float(np.mean((prices >= lo_f) & (prices <= hi_f)))
        gap = abs(coverage - target)
        if gap < best_gap:
            best_factor, best_gap = float(factor), gap
    log.info("interval calibration factor %.2f (target coverage %.0f%%)", best_factor, target * 100)
    return best_factor


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Train the quantile price model")
    ap.add_argument("--holdout", type=int, default=120, help="listings to reserve for blind evaluation")
    ap.add_argument("--no-remark-holdout", action="store_true")
    ap.add_argument("--min-rows", type=int, default=400)
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout
    )

    if not args.no_remark_holdout:
        mark_holdout(args.holdout)

    raw = F.load_corpus(exclude_holdout=True)
    if raw.empty:
        log.error("corpus is empty; run the scraper first")
        return 1
    df = F.build_frame(raw)
    log.info("training rows after filtering: %d (from %d priced listings)", len(df), len(raw))
    if len(df) < args.min_rows:
        log.error("only %d usable rows, need at least %d", len(df), args.min_rows)
        return 1

    cats = F.category_map(df)
    X = F.as_model_matrix(df, cats)
    y = df[F.TARGET].to_numpy()
    prices = df["price_eur"].to_numpy(dtype=float)

    X_tr, X_te, y_tr, y_te, p_tr, p_te = train_test_split(X, y, prices, test_size=0.18, random_state=7)

    models = {}
    for name, q in QUANTILES.items():
        log.info("fitting %s ...", name)
        model = HistGradientBoostingRegressor(
            loss="quantile", quantile=q, categorical_features="from_dtype", **MODEL_PARAMS
        )
        model.fit(X_tr, y_tr)
        models[name] = model

    metrics_train = evaluate(models, X_tr, y_tr, p_tr)
    metrics_test = evaluate(models, X_te, y_te, p_te)
    log.info("train: %s", json.dumps(metrics_train, indent=None))
    log.info("test : %s", json.dumps(metrics_test, indent=None))

    factor = calibrate_interval(models, X_te, p_te, target=0.80)

    # Residual spread by segment, used later to widen the interval when the
    # vision stage could not pin the specification.
    resid = np.abs(np.exp(models["q50"].predict(X_te)) - p_te) / p_te
    residual_quantiles = {
        "p50": float(np.quantile(resid, 0.50)),
        "p80": float(np.quantile(resid, 0.80)),
        "p90": float(np.quantile(resid, 0.90)),
    }

    artifact = {
        "models": models,
        "categories": cats,
        "features": F.FEATURES,
        "categorical": F.CATEGORICAL,
        "numeric": F.NUMERIC,
        "interval_factor": factor,
        "residual_quantiles": residual_quantiles,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_train": int(len(X_tr)),
        "n_test": int(len(X_te)),
        "metrics_train": metrics_train,
        "metrics_test": metrics_test,
        "price_median_eur": float(np.median(prices)),
    }
    out_path = config.MODEL_DIR / "price_model.joblib"
    joblib.dump(artifact, out_path, compress=3)
    log.info("saved %s", out_path)

    # A readable copy for the README and the UI.
    summary = {
        k: v for k, v in artifact.items() if k not in ("models", "categories")
    }
    summary["n_categories"] = {k: len(v) for k, v in cats.items()}
    metrics_path = config.MODEL_DIR / "price_model_metrics.json"
    metrics_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("saved %s", metrics_path)

    # Recalibrated coverage for the record.
    mid = np.exp(models["q50"].predict(X_te))
    lo = np.exp(models["q10"].predict(X_te)) 
    hi = np.exp(models["q90"].predict(X_te))
    lo_f = mid * np.power(np.maximum(lo, 1) / np.maximum(mid, 1), factor)
    hi_f = mid * np.power(np.maximum(hi, 1) / np.maximum(mid, 1), factor)
    log.info(
        "calibrated test coverage: %.1f%% | median APE %.1f%% | median interval width %.0f%% of mid",
        100 * float(np.mean((p_te >= lo_f) & (p_te <= hi_f))),
        100 * metrics_test["median_ape"],
        100 * float(np.median((hi_f - lo_f) / mid)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
