from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


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
    timings_ms: dict[str, float]


class DraftPanel(BaseModel):
    points: list[list[float]] = Field(min_length=3)
    confidence: float | None = Field(default=None, ge=0, le=1)
    label: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)


class ProjectCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    reading_direction: Literal["ltr", "rtl"] = "ltr"


class ProjectPageDraft(BaseModel):
    id: UUID
    filename: str = Field(min_length=1, max_length=500)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    background_color: str | None = Field(default=None, max_length=32)
    is_wide_spread: bool = False
    panels: list[DraftPanel] = Field(default_factory=list)
    review_status: Literal["empty", "model", "customized", "validated"] = "empty"
    model_ids: list[str] = Field(default_factory=list, max_length=10)


class ProjectDraftUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    reading_direction: Literal["ltr", "rtl"] = "ltr"
    pages: list[ProjectPageDraft] = Field(default_factory=list)
