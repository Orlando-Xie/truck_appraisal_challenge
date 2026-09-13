"""Currency and the Türkiye market bridge.

The price model is fitted on a mostly European corpus in EUR, because that is
where the data volume is. Turkish asking prices sit above European ones for the
same truck: import duty, OTV and KDV, plus a structurally tighter supply of
clean used tractor units.

Rather than hide that, the pipeline reports it as two explicit factors:

    price_TRY = price_EUR * turkiye_multiplier * EURTRY

The global multiplier is the fallback. When calibration has enough Turkish
seeds, `multiplier_for` picks a make x age-band factor instead of one 1.99x
for every truck.

Both are surfaced in the result so a user can see exactly what was assumed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import config

log = logging.getLogger(__name__)


def age_band(year: int | None) -> str:
    """Coarse age buckets used for segmented TRY multipliers."""
    try:
        y = int(year) if year else 0
    except (TypeError, ValueError):
        y = 0
    if y >= 2020:
        return "2020+"
    if y >= 2016:
        return "2016-2019"
    if y >= 2012:
        return "2012-2015"
    if y > 0:
        return "pre-2012"
    return "unknown"


def load_calibration() -> dict:
    """Read the fitted Türkiye multiplier and FX rate, if calibration has run."""
    if config.CALIBRATION_PATH.exists():
        try:
            data = json.loads(config.CALIBRATION_PATH.read_text(encoding="utf-8"))
            return {
                "turkiye_multiplier": float(data.get("turkiye_multiplier", config.DEFAULT_TURKIYE_MULTIPLIER)),
                "eur_try": float(data.get("eur_try", config.DEFAULT_EUR_TRY)),
                "source": data.get("source", "calibrated"),
                "n_listings": int(data.get("n_listings", 0)),
                "fitted_at": data.get("fitted_at", ""),
                "notes": data.get("notes", ""),
                "segments": data.get("segments") or {},
                "use_segments": bool(data.get("use_segments", False)),
            }
        except Exception as exc:  # pragma: no cover
            log.warning("could not read calibration (%s); using defaults", exc)
    return {
        "turkiye_multiplier": config.DEFAULT_TURKIYE_MULTIPLIER,
        "eur_try": config.DEFAULT_EUR_TRY,
        "source": "default",
        "n_listings": 0,
        "fitted_at": "",
        "notes": (
            "Turkish market calibration has not been run, so a default premium over European "
            "asking prices is being applied."
        ),
        "segments": {},
        "use_segments": False,
    }


def save_calibration(
    multiplier: float,
    eur_try: float,
    n_listings: int,
    notes: str,
    source: str = "arabam",
    segments: dict | None = None,
) -> None:
    payload = {
        "turkiye_multiplier": round(multiplier, 4),
        "eur_try": round(eur_try, 4),
        "n_listings": n_listings,
        "source": source,
        "notes": notes,
        "fitted_at": datetime.now(timezone.utc).isoformat(),
        "segments": segments or {},
    }
    config.CALIBRATION_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("saved calibration: %s", payload)


def multiplier_for(spec: dict | None, calibration: dict | None = None) -> float:
    """Segmented TRY premium: make|age-band, then make, then age-band, then global."""
    cal = calibration or load_calibration()
    global_m = float(cal.get("turkiye_multiplier") or config.DEFAULT_TURKIYE_MULTIPLIER)
    # Demo-safe: ignore sparse make×age segments unless explicitly enabled.
    if not cal.get("use_segments"):
        return global_m
    segments = cal.get("segments") or {}
    if not spec or not segments:
        return global_m

    make = str(spec.get("make") or spec.get("make_canon") or "").strip()
    band = age_band(spec.get("year"))
    for key in (f"{make}|{band}", make, band):
        row = segments.get(key)
        if not row:
            continue
        try:
            n = int(row.get("n") or 0)
            value = float(row.get("multiplier") or 0)
        except (TypeError, ValueError):
            continue
        if n >= 3 and 0.4 <= value <= 4.0:
            return value
    return global_m


def eur_to_try(amount_eur: float, calibration: dict | None = None, spec: dict | None = None) -> float:
    cal = calibration or load_calibration()
    return amount_eur * multiplier_for(spec, cal) * cal["eur_try"]
