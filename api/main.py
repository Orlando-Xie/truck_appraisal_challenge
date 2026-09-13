"""FastAPI application.

The appraisal endpoint streams stage-by-stage progress. That is partly a latency
trick and partly the point: a buyer trusting a number wants to watch the
reasoning happen rather than stare at a spinner and receive a verdict.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import config
from appraise import orchestrator
from vision.client import get_client
from vision.schemas import AppraisalResult, SellerClaims

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

app = FastAPI(title="Kamion truck appraisal", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic", "image/heif"}


@app.on_event("startup")
async def startup() -> None:
    # Probing the vision provider at startup surfaces a bad key immediately
    # rather than during a demo.
    try:
        await get_client().ensure_ready()
    except Exception as exc:
        log.warning("vision provider not ready: %s", exc)
    log.info("pipeline: %s", json.dumps(orchestrator.pipeline_info()["vision"]))


async def _read_uploads(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    images: list[tuple[str, bytes]] = []
    total = 0
    for f in files[: config.MAX_IMAGES]:
        blob = await f.read()
        if not blob:
            continue
        total += len(blob)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Upload too large; please send fewer or smaller photos.")
        ctype = (f.content_type or "").lower()
        if ctype and ctype not in ALLOWED_TYPES and not ctype.startswith("image/"):
            raise HTTPException(415, f"{f.filename} is not an image ({ctype}).")
        images.append((f.filename or f"photo_{len(images) + 1}.jpg", blob))
    if not images:
        raise HTTPException(400, "No photos received.")
    return images


def _claims(year, make, model, km, asking) -> SellerClaims | None:
    def num(v):
        if v in (None, "", "null"):
            return None
        try:
            return int(float(str(v).replace(" ", "").replace(",", "")))
        except (TypeError, ValueError):
            return None

    claims = SellerClaims(
        year=num(year),
        make=(make or None) or None,
        model=(model or None) or None,
        km=num(km),
        asking_price_try=num(asking),
    )
    return claims if any(claims.model_dump().values()) else None


@app.get("/api/info")
async def info() -> JSONResponse:
    await get_client().ensure_ready()
    data = orchestrator.pipeline_info()
    data["stages"] = [{"id": s, "label": label} for s, label in orchestrator.STAGES]
    data["max_images"] = config.MAX_IMAGES
    return JSONResponse(data)


@app.get("/api/samples")
async def samples() -> JSONResponse:
    """Seeded demo cases, so a demo never depends on finding a file."""
    path = config.SAMPLES_DIR / "index.json"
    if not path.exists():
        return JSONResponse({"samples": []})
    try:
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return JSONResponse({"samples": []})


@app.get("/api/asset/{kind}/{path:path}")
async def asset(kind: str, path: str) -> FileResponse:
    """Serve corpus thumbnails and sample photos.

    Paths are resolved and checked to stay inside the data directory.
    """
    roots = {"image": config.IMAGE_DIR, "sample": config.SAMPLES_DIR}
    root = roots.get(kind)
    if root is None:
        raise HTTPException(404, "unknown asset kind")
    target = (root / path).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(403, "path outside asset root")
    if not target.is_file():
        raise HTTPException(404, "not found")
    ctype, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=ctype or "application/octet-stream")


@app.post("/api/appraise")
async def appraise(
    files: list[UploadFile] = File(...),
    year: str | None = Form(None),
    make: str | None = Form(None),
    model: str | None = Form(None),
    km: str | None = Form(None),
    asking_price_try: str | None = Form(None),
    no_cache: str | None = Form(None),
) -> JSONResponse:
    images = await _read_uploads(files)
    result = await orchestrator.appraise(
        images, claims=_claims(year, make, model, km, asking_price_try), use_cache=not no_cache
    )
    return JSONResponse(json.loads(result.model_dump_json()))


@app.post("/api/appraise/stream")
async def appraise_stream(
    files: list[UploadFile] = File(...),
    year: str | None = Form(None),
    make: str | None = Form(None),
    model: str | None = Form(None),
    km: str | None = Form(None),
    asking_price_try: str | None = Form(None),
    no_cache: str | None = Form(None),
):
    images = await _read_uploads(files)
    claims = _claims(year, make, model, km, asking_price_try)
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def progress(stage: str, message: str, data: dict | None) -> None:
        await queue.put(
            json.dumps({"type": "progress", "stage": stage, "message": message, "data": data or {}})
        )

    async def run() -> None:
        try:
            result = await orchestrator.appraise(
                images, claims=claims, progress=progress, use_cache=not no_cache
            )
            await queue.put(json.dumps({"type": "result", "result": json.loads(result.model_dump_json())}))
        except Exception as exc:
            log.exception("appraisal failed")
            await queue.put(
                json.dumps(
                    {
                        "type": "error",
                        "message": f"The appraisal failed unexpectedly: {type(exc).__name__}: {exc}",
                    }
                )
            )
        finally:
            await queue.put(None)

    async def events():
        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield f"data: {item}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.post("/api/appraise/sample/{sample_id}")
async def appraise_sample(sample_id: str) -> JSONResponse:
    """Appraise a seeded sample by id, for one-click demos."""
    index_path = config.SAMPLES_DIR / "index.json"
    if not index_path.exists():
        raise HTTPException(404, "no samples installed")
    data = json.loads(index_path.read_text(encoding="utf-8"))
    sample = next((s for s in data.get("samples", []) if s["id"] == sample_id), None)
    if sample is None:
        raise HTTPException(404, "unknown sample")

    images: list[tuple[str, bytes]] = []
    for rel in sample["files"]:
        p = (config.SAMPLES_DIR / rel).resolve()
        try:
            p.relative_to(config.SAMPLES_DIR.resolve())
        except ValueError:
            continue
        if p.is_file():
            images.append((p.name, p.read_bytes()))
    if not images:
        raise HTTPException(404, "sample files missing")

    claims = None
    if sample.get("claims"):
        claims = SellerClaims(**sample["claims"])
    result = await orchestrator.appraise(images, claims=claims)
    return JSONResponse(json.loads(result.model_dump_json()))


# The built frontend is served from the same origin so there is nothing to
# configure at demo time.
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
else:

    @app.get("/")
    async def no_frontend() -> JSONResponse:
        return JSONResponse(
            {
                "message": "API is running. Build the frontend with `npm run build` in web/, "
                "or run `npm run dev` for the dev server.",
                "endpoints": ["/api/info", "/api/appraise", "/api/appraise/stream", "/api/samples"],
            }
        )
