"""Central configuration. Everything tunable lives here or in deductions.yaml."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
IMAGE_DIR = DATA_DIR / "images"
UPLOAD_DIR = DATA_DIR / "uploads"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "listings.db"
MODEL_DIR = ROOT / "pricing" / "artifacts"
DEDUCTIONS_PATH = ROOT / "pricing" / "deductions.yaml"
SAMPLES_DIR = DATA_DIR / "samples"

for _d in (DATA_DIR, IMAGE_DIR, UPLOAD_DIR, CACHE_DIR, MODEL_DIR, SAMPLES_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Vision provider
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or ""

# Preference order. The client probes these against the live model list at
# startup and uses the first that is actually available on the key, so a model
# being renamed or retired does not take the demo down.
GEMINI_FLASH_CANDIDATES = [
    m.strip()
    for m in os.getenv(
        "GEMINI_FLASH_MODELS",
        # Prefer current Flash ids first. gemini-2.5-flash is still listed
        # but returns 404 for new keys ("no longer available to new users").
        "gemini-3.6-flash,gemini-3.5-flash,gemini-3.8-flash,gemini-3.7-flash,"
        "gemini-3-flash-preview,gemini-flash-latest,"
        "gemini-2.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-2.5-flash-lite",
    ).split(",")
    if m.strip()
]
GEMINI_PRO_CANDIDATES = [
    m.strip()
    for m in os.getenv(
        "GEMINI_PRO_MODELS",
        "gemini-3.6-flash,gemini-3.5-flash,gemini-2.5-flash",
    ).split(",")
    if m.strip()
]
OPENAI_MODEL_CANDIDATES = [
    m.strip() for m in os.getenv("OPENAI_MODELS", "gpt-4o-mini,gpt-4o").split(",") if m.strip()
]

VISION_PROVIDER = os.getenv("VISION_PROVIDER", "auto")  # auto | gemini | openai | stub
# Pro is several times the Flash price and is off by default. Identification and
# the condition rubric both run on Flash; set GEMINI_USE_PRO=1 to spend more.
GEMINI_USE_PRO = os.getenv("GEMINI_USE_PRO", "0") == "1"

# ---------------------------------------------------------------------------
# Runtime behaviour
# ---------------------------------------------------------------------------

STAGE_TIMEOUT_S = float(os.getenv("STAGE_TIMEOUT_S", "75"))
PER_CALL_TIMEOUT_S = float(os.getenv("PER_CALL_TIMEOUT_S", "60"))
MAX_IMAGES = int(os.getenv("MAX_IMAGES", "12"))
# 1024px is enough to read a grille badge; 1280px just burns tokens.
MAX_IMAGE_EDGE = int(os.getenv("MAX_IMAGE_EDGE", "1024"))
# Identify + condition only need a covering set of views, not every upload.
MAX_VISION_IMAGES = int(os.getenv("MAX_VISION_IMAGES", "6"))
ENABLE_RESULT_CACHE = os.getenv("ENABLE_RESULT_CACHE", "1") != "0"

# ---------------------------------------------------------------------------
# Image quality thresholds (Stage 0, computed locally)
# ---------------------------------------------------------------------------

BLUR_THRESHOLD = float(os.getenv("BLUR_THRESHOLD", "45"))
DARK_THRESHOLD = float(os.getenv("DARK_THRESHOLD", "42"))
BRIGHT_THRESHOLD = float(os.getenv("BRIGHT_THRESHOLD", "225"))
MIN_MEGAPIXELS = float(os.getenv("MIN_MEGAPIXELS", "0.08"))

# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

MIN_SUBJECT_CONFIDENCE = 0.45
MIN_MODEL_CONFIDENCE = 0.55
MIN_MAKE_CONFIDENCE = 0.50
PHASH_MATCH_DISTANCE = 8

# ---------------------------------------------------------------------------
# Market / currency
# ---------------------------------------------------------------------------

# Turkish heavy trucks trade above EU levels because of import duty, OTV/KDV
# and a structurally tighter supply of clean used tractors. This multiplier is
# fitted from Turkish listings by pricing/calibrate_turkiye.py; the value here
# is the fallback if calibration has not been run.
DEFAULT_TURKIYE_MULTIPLIER = float(os.getenv("TURKIYE_MULTIPLIER", "1.30"))
DEFAULT_EUR_TRY = float(os.getenv("EUR_TRY", "47.5"))
CALIBRATION_PATH = MODEL_DIR / "turkiye_calibration.json"


@lru_cache(maxsize=1)
def rubric() -> dict:
    """The condition rubric and deduction table."""
    with open(DEDUCTIONS_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=1)
def rubric_items() -> list[dict]:
    return rubric()["items"]


def rubric_item(item_id: str) -> dict | None:
    for item in rubric_items():
        if item["id"] == item_id:
            return item
    return None
