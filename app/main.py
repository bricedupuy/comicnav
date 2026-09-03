from __future__ import annotations

import io
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Literal

import numpy as np
from fastapi import FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from PIL import Image, UnidentifiedImageError

from .config import load_model_specs, settings
from .database import close_database, init_database
from .detectors import ModelManager
from .geometry import (
    bbox_percent,
    is_effectively_axis_aligned_rectangle,
    polygon_percent,
    simplify_polygon,
)
from .ordering import OrderablePanel, reading_order
from .readium import guided_document
from .projects import router as project_router
from .schemas import AnalyzeResponse

# Uvicorn configures this logger at INFO level, so the timing records are
# visible in container logs without requiring a separate logging setup.
logger = logging.getLogger("uvicorn.error")

@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_database()
    try:
        yield
    finally:
        await close_database()


app = FastAPI(
    title="ComicNav API",
    version="0.1.0",
    description="Comic panel detection/segmentation with Readium Guided Navigation output.",
    lifespan=lifespan,
)

specs = load_model_specs()
manager = ModelManager(specs)
app.include_router(project_router)


def _validate_model(name: str) -> None:
    if name not in specs:
        raise HTTPException(status_code=404, detail=f"Unknown model '{name}'")


async def _load_image(file: UploadFile, timings_ms: dict[str, float]) -> tuple[Image.Image, int]:
    # Do not rely exclusively on multipart Content-Type. Images extracted from
    # CBZ/ZIP archives in browsers can legitimately arrive as
    # application/octet-stream even though their bytes are valid JPEG/PNG/WebP.
    read_started = perf_counter()
    raw = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    timings_ms["upload_read"] = round((perf_counter() - read_started) * 1000, 1)
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image is too large")

    try:
        decode_started = perf_counter()
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Invalid image") from exc

    timings_ms["image_decode"] = round((perf_counter() - decode_started) * 1000, 1)

    # Validate the decoded format instead of trusting the browser-supplied MIME.
    # Pillow reports JPEG, PNG or WEBP after inspecting the actual file bytes.
    if (image.format or "").upper() not in {"JPEG", "PNG", "WEBP"}:
        raise HTTPException(status_code=415, detail="Only JPEG, PNG and WebP are accepted")

    if image.width * image.height > settings.max_image_pixels:
        raise HTTPException(status_code=413, detail="Image pixel dimensions exceed limit")

    convert_started = perf_counter()
    image = image.convert("RGB")
    timings_ms["image_convert"] = round((perf_counter() - convert_started) * 1000, 1)
    return image, len(raw)


def _analyze(
    image: Image.Image,
    model_name: str,
    confidence: float | None,
    reading_direction: str,
    geometry_mode: Literal["auto", "rectangle", "polygon"],
    timings_ms: dict[str, float] | None = None,
) -> dict:
    raw = manager.detect(model_name, image, confidence, timings_ms)
    postprocess_started = perf_counter()
    panels: list[dict] = []

    for idx, p in enumerate(raw):
        geom_type = "rectangle"
        poly_pixels = None
        poly_pct = None

        if p.polygon is not None and geometry_mode != "rectangle":
            simplified = simplify_polygon(p.polygon, settings.polygon_epsilon)
            should_polygon = geometry_mode == "polygon" or not is_effectively_axis_aligned_rectangle(
                simplified, p.bbox, settings.rectangle_fill_threshold
            )
            if should_polygon and len(simplified) >= 3:
                geom_type = "polygon"
                poly_pixels = [[round(float(x), 2), round(float(y), 2)] for x, y in simplified]
                poly_pct = polygon_percent(simplified, image.width, image.height, settings.percent_decimals)

        panels.append(
            {
                "_source_index": idx,
                "confidence": round(p.confidence, 5),
                "label": p.label,
                "geometry": {
                    "type": geom_type,
                    "bbox_pixels": [round(v, 2) for v in p.bbox],
                    "polygon_pixels": poly_pixels,
                    "bbox_percent": bbox_percent(p.bbox, image.width, image.height, settings.percent_decimals),
                    "polygon_percent": poly_pct,
                },
            }
        )

    orderables = [
        OrderablePanel(
            index=i,
            x=float(p["geometry"]["bbox_pixels"][0]),
            y=float(p["geometry"]["bbox_pixels"][1]),
            w=float(p["geometry"]["bbox_pixels"][2]),
            h=float(p["geometry"]["bbox_pixels"][3]),
        )
        for i, p in enumerate(panels)
    ]
    order = reading_order(orderables, direction=reading_direction)
    ordered = [panels[i] for i in order]
    for n, panel in enumerate(ordered, start=1):
        panel.pop("_source_index", None)
        panel["order"] = n

    if timings_ms is not None:
        timings_ms["geometry_order"] = round((perf_counter() - postprocess_started) * 1000, 1)

    return {
        "model": model_name,
        "image": {"width": image.width, "height": image.height},
        "panels": ordered,
    }


async def _timed_analysis(
    file: UploadFile,
    model: str,
    confidence: float | None,
    reading_direction: str,
    geometry: Literal["auto", "rectangle", "polygon"],
) -> dict:
    request_started = perf_counter()
    timings_ms: dict[str, float] = {}
    image, file_bytes = await _load_image(file, timings_ms)
    analysis = _analyze(image, model, confidence, reading_direction, geometry, timings_ms)
    timings_ms["total"] = round((perf_counter() - request_started) * 1000, 1)

    logger.info(
        "analysis_complete model=%s image=%sx%s bytes=%s panels=%s timings_ms=%s",
        model,
        image.width,
        image.height,
        file_bytes,
        len(analysis["panels"]),
        json.dumps(timings_ms, separators=(",", ":"), sort_keys=True),
    )
    analysis["timings_ms"] = timings_ms
    return analysis


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/editor")


@app.get("/editor", include_in_schema=False)
def editor() -> FileResponse:
    return FileResponse(Path(__file__).resolve().parent.parent / "web" / "editor.html", media_type="text/html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "loaded_models": sorted(manager.models.keys())}


@app.get("/v1/models")
def list_models() -> dict:
    return {
        "default": settings.default_model,
        "models": {
            name: {
                **spec.model_dump(),
                "loaded": name in manager.models,
            }
            for name, spec in specs.items()
        },
    }


@app.post("/v1/models/{model_name}/load")
def load_model(model_name: str) -> dict:
    _validate_model(model_name)
    manager.get(model_name)
    return {"model": model_name, "loaded": True}


@app.delete("/v1/models/{model_name}/load")
def unload_model(model_name: str) -> dict:
    _validate_model(model_name)
    return {"model": model_name, "unloaded": manager.unload(model_name)}


@app.post("/v1/analyze", response_model=AnalyzeResponse)
async def analyze(
    file: UploadFile = File(...),
    model: str = Query(default=settings.default_model),
    confidence: float | None = Query(default=None, ge=0.01, le=0.99),
    reading_direction: Literal["ltr", "rtl"] = Query(default="ltr"),
    geometry: Literal["auto", "rectangle", "polygon"] = Query(default="auto"),
) -> dict:
    _validate_model(model)
    return await _timed_analysis(file, model, confidence, reading_direction, geometry)


@app.post("/v1/guided-navigation")
async def create_guided_navigation(
    file: UploadFile = File(...),
    model: str = Query(default=settings.default_model),
    confidence: float | None = Query(default=None, ge=0.01, le=0.99),
    reading_direction: Literal["ltr", "rtl"] = Query(default="ltr"),
    geometry: Literal["auto", "rectangle", "polygon"] = Query(default="auto"),
    image_href: str | None = Query(default=None),
    self_href: str | None = Query(default=None),
) -> Response:
    _validate_model(model)
    analysis = await _timed_analysis(file, model, confidence, reading_direction, geometry)

    filename = file.filename or "page.jpg"
    resolved_image_href = image_href or filename
    stem = filename.rsplit(".", 1)[0]
    resolved_self_href = self_href or f"{stem}.guided.json"
    document = guided_document(resolved_image_href, resolved_self_href, analysis["panels"])

    import json

    return Response(
        content=json.dumps(document, ensure_ascii=False, separators=(",", ":")),
        media_type="application/guided-navigation+json",
    )
