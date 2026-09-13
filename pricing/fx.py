"""Currency and the Türkiye market bridge.

The price model is fitted on a mostly European corpus in EUR, because that is
where the data volume is. Turkish asking prices sit above European ones for the
same truck: import duty, ÖTV and KDV, plus a structurally tighter supply of
clean used tractor units.

Rather than hide that, the pipeline reports it as two explicit factors:

    price_TRY = price_EUR * turkiye_multiplier * EURTRY

Both are surfaced in the result so a user can see exactly what was assumed. The
multiplier is fitted by `pricing/calibrate_turkiye.py` from Turkish listings; the
value in config is only the fallback.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import config

log = logging.getLogger(__name__)


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
    }


def save_calibration(multiplier: float, eur_try: float, n_listings: int, notes: str, source: str = "arabam") -> None:
    payload = {
        "turkiye_multiplier": round(multiplier, 4),
        "eur_try": round(eur_try, 4),
        "n_listings": n_listings,
        "source": source,
        "notes": notes,
        "fitted_at": datetime.now(timezone.utc).isoformat(),
    }
    config.CALIBRATION_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("saved calibration: %s", payload)


def eur_to_try(amount_eur: float, calibration: dict | None = None) -> float:
    cal = calibration or load_calibration()
    return amount_eur * cal["turkiye_multiplier"] * cal["eur_try"]
