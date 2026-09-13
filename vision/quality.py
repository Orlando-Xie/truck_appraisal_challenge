"""Local image quality scoring.

Runs before any model call, for two reasons: it is free, and it means the
"these photos are not good enough" verdict rests on a measurement rather than on
the model's opinion of its own eyesight.
"""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image, ImageFile, ImageOps

import config
from vision.schemas import ImageQuality

ImageFile.LOAD_TRUNCATED_IMAGES = True


def load_rgb(raw: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(raw))
    img.load()
    # Phone photos carry orientation in EXIF; without this a portrait shot is
    # analysed sideways.
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def downscale_for_model(raw: bytes, max_edge: int | None = None) -> bytes:
    """Re-encode an upload to something cheap to send and fast to process."""
    max_edge = max_edge or config.MAX_IMAGE_EDGE
    img = load_rgb(raw)
    w, h = img.size
    if max(w, h) > max_edge:
        scale = max_edge / max(w, h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True)
    return buf.getvalue()


def assess(filename: str, raw: bytes) -> ImageQuality:
    """Measure sharpness, exposure and resolution."""
    q = ImageQuality(filename=filename)
    try:
        img = load_rgb(raw)
    except Exception as exc:
        q.usable = False
        q.notes.append(f"could not be decoded as an image ({type(exc).__name__})")
        return q

    q.width, q.height = img.size
    q.megapixels = round(q.width * q.height / 1e6, 3)

    arr = np.asarray(img)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    # Blur is scale dependent, so measure on a normalised height.
    target_h = 720
    if gray.shape[0] > target_h:
        scale = target_h / gray.shape[0]
        gray_norm = cv2.resize(gray, (max(1, int(gray.shape[1] * scale)), target_h), interpolation=cv2.INTER_AREA)
    else:
        gray_norm = gray

    q.blur_score = round(float(cv2.Laplacian(gray_norm, cv2.CV_64F).var()), 2)
    q.brightness = round(float(gray.mean()), 2)
    q.contrast = round(float(gray.std()), 2)

    q.too_blurry = q.blur_score < config.BLUR_THRESHOLD
    q.too_dark = q.brightness < config.DARK_THRESHOLD
    q.too_bright = q.brightness > config.BRIGHT_THRESHOLD
    q.too_small = q.megapixels < config.MIN_MEGAPIXELS

    if q.too_blurry:
        q.notes.append(f"out of focus or motion blurred (sharpness {q.blur_score:.0f}, need {config.BLUR_THRESHOLD:.0f})")
    if q.too_dark:
        q.notes.append(f"too dark to assess (mean brightness {q.brightness:.0f}/255)")
    if q.too_bright:
        q.notes.append(f"blown out (mean brightness {q.brightness:.0f}/255)")
    if q.too_small:
        q.notes.append(f"too low resolution ({q.width}x{q.height})")
    if q.contrast < 18:
        q.notes.append("very flat contrast, likely fog, glare or a dirty lens")

    # A photo is unusable only if it fails badly. Mildly soft or dim photos are
    # still worth showing the model; they just lower confidence.
    q.usable = not (q.too_small or q.too_dark or q.too_bright or q.blur_score < config.BLUR_THRESHOLD * 0.45)
    return q
