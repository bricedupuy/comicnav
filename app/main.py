from __future__ import annotations

import io
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from PIL import Image, UnidentifiedImageError

from .config import load_model_specs, settings
from .detectors import ModelManager
from .geometry import (
    bbox_percent,
    is_effectively_axis_aligned_rectangle,
    polygon_percent,
    simplify_polygon,
)
from .ordering import OrderablePanel, reading_order
from .readium import guided_document

app = FastAPI(
    title="ComicNav API",
    version="0.1.0",
    description="Comic panel detection/segmentation with Readium Guided Navigation output.",
)

specs = load_model_specs()
manager = ModelManager(specs)


def _validate_model(name: str) -> None:
    if name not in specs:
        raise HTTPException(status_code=404, detail=f"Unknown model '{name}'")


async def _load_image(file: UploadFile) -> Image.Image:
    allowed = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=415, detail="Only JPEG, PNG and WebP are accepted")

    raw = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image is too large")

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Invalid image") from exc

    if image.width * image.height > settings.max_image_pixels:
        raise HTTPException(status_code=413, detail="Image pixel dimensions exceed limit")
    return image.convert("RGB")


def _analyze(
    image: Image.Image,
    model_name: str,
    confidence: float | None,
    reading_direction: str,
    geometry_mode: Literal["auto", "rectangle", "polygon"],
) -> dict:
    raw = manager.detect(model_name, image, confidence)
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

    return {
        "model": model_name,
        "image": {"width": image.width, "height": image.height},
        "panels": ordered,
    }


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


@app.post("/v1/analyze")
async def analyze(
    file: UploadFile = File(...),
    model: str = Query(default=settings.default_model),
    confidence: float | None = Query(default=None, ge=0.01, le=0.99),
    reading_direction: Literal["ltr", "rtl"] = Query(default="ltr"),
    geometry: Literal["auto", "rectangle", "polygon"] = Query(default="auto"),
) -> dict:
    _validate_model(model)
    image = await _load_image(file)
    return _analyze(image, model, confidence, reading_direction, geometry)


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
    image = await _load_image(file)
    analysis = _analyze(image, model, confidence, reading_direction, geometry)

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
