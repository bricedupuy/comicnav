from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Point(BaseModel):
    x: float
    y: float


class Geometry(BaseModel):
    type: Literal["rectangle", "polygon"]
    bbox_pixels: list[float]
    polygon_pixels: list[list[float]] | None = None
    bbox_percent: list[float]
    polygon_percent: list[list[float]] | None = None


class PanelResult(BaseModel):
    order: int
    confidence: float
    label: str
    geometry: Geometry


class AnalyzeResponse(BaseModel):
    model: str
    image: dict
    panels: list[PanelResult]
