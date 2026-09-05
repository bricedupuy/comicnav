from __future__ import annotations

from typing import Literal
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


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


MetadataSource = Literal["comicinfo.xml", "filename", "manual", "derived", "gcd", "comicvine"]
ReleaseType = Literal["digital", "scan", "hybrid", "epub_extract", "web_rip", "unknown", "other"]


class MetadataRecord(BaseModel):
    provider: Literal["gcd"] = "gcd"
    external_id: int = Field(gt=0)
    source_url: str = Field(pattern=r"^https://www\.comics\.org/issue/[0-9]+/$")
    retrieved_at: datetime
    adapter_version: Literal["gcd-v1"] = "gcd-v1"
    license: Literal["CC BY-SA 4.0"] = "CC BY-SA 4.0"
    attribution: Literal["Grand Comics Database"] = "Grand Comics Database"
    raw: dict = Field(default_factory=dict)


class ComicVineMetadataRecord(BaseModel):
    provider: Literal["comicvine"]
    external_id: int = Field(gt=0)
    source_url: str = Field(pattern=r"^https://comicvine\.gamespot\.com/[^/?#]*/4000-[0-9]+/$")
    retrieved_at: datetime
    adapter_version: Literal["comicvine-v1"]
    license: Literal["Comic Vine API terms"]
    attribution: Literal["Comic Vine"]
    raw: dict = Field(default_factory=dict)


class MetadataFieldProvenance(BaseModel):
    record_id: str = Field(pattern=r"^(gcd|comicvine):issue:[0-9]+$")
    original_value: str | int


class ProjectMetadata(BaseModel):
    """Editable project facts plus lightweight provenance for each populated field."""

    series: str | None = Field(default=None, max_length=500)
    number: str | None = Field(default=None, max_length=100)
    volume: str | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, max_length=500)
    summary: str | None = Field(default=None, max_length=10_000)
    writer: str | None = Field(default=None, max_length=2_000)
    penciller: str | None = Field(default=None, max_length=2_000)
    inker: str | None = Field(default=None, max_length=2_000)
    colorist: str | None = Field(default=None, max_length=2_000)
    letterer: str | None = Field(default=None, max_length=2_000)
    cover_artist: str | None = Field(default=None, max_length=2_000)
    editor: str | None = Field(default=None, max_length=2_000)
    publisher: str | None = Field(default=None, max_length=500)
    imprint: str | None = Field(default=None, max_length=500)
    genre: str | None = Field(default=None, max_length=1_000)
    format: str | None = Field(default=None, max_length=500)
    language_iso: str | None = Field(default=None, max_length=32)
    year: int | None = Field(default=None, ge=0, le=9_999)
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)
    page_count: int | None = Field(default=None, ge=0, le=10_000)
    manga: str | None = Field(default=None, max_length=100)
    black_and_white: str | None = Field(default=None, max_length=100)
    story_arc: str | None = Field(default=None, max_length=500)
    series_group: str | None = Field(default=None, max_length=500)
    age_rating: str | None = Field(default=None, max_length=100)
    web: str | None = Field(default=None, max_length=2_000)
    release_group: str | None = Field(default=None, max_length=500)
    release_type: ReleaseType | None = None
    release_tags: list[str] = Field(default_factory=list, max_length=30)
    edition_label: str | None = Field(default=None, max_length=500)
    release_revision: str | None = Field(default=None, max_length=100)
    release_notes: str | None = Field(default=None, max_length=5_000)
    comicinfo_path: str | None = Field(default=None, max_length=500)
    sources: dict[str, MetadataSource] = Field(default_factory=dict, max_length=48)
    provider_records: dict[str, MetadataRecord | ComicVineMetadataRecord] = Field(default_factory=dict, max_length=8)
    field_provenance: dict[str, MetadataFieldProvenance] = Field(default_factory=dict, max_length=48)
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("release_tags")
    @classmethod
    def normalize_release_tags(cls, tags: list[str]) -> list[str]:
        normalized: list[str] = []
        for tag in tags:
            value = tag.strip()
            if not value:
                continue
            if len(value) > 100:
                raise ValueError("Release tags must be 100 characters or fewer")
            if value not in normalized:
                normalized.append(value)
        return normalized


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
    metadata: ProjectMetadata = Field(default_factory=ProjectMetadata)
    pages: list[ProjectPageDraft] = Field(default_factory=list)
