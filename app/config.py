from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import yaml
from pydantic import BaseModel, Field


def _default_database_url() -> str:
    """Build the Compose PostgreSQL URL without requiring URL-safe secrets."""
    user = quote(os.getenv("POSTGRES_USER", "comicnav"), safe="")
    password = quote(os.getenv("POSTGRES_PASSWORD", "comicnav"), safe="")
    database = quote(os.getenv("POSTGRES_DB", "comicnav"), safe="")
    return f"postgresql+asyncpg://{user}:{password}@postgres:5432/{database}"


class ModelSpec(BaseModel):
    title: str
    repo_id: str
    filename: str
    task: Literal["detect", "segment"]
    panel_classes: list[str] = Field(default_factory=lambda: ["panel", "frame"])
    imgsz: int = 640
    default_confidence: float = 0.25
    license: str = "Unknown"
    notes: str = ""


class Settings(BaseModel):
    model_cache_dir: Path = Path(os.getenv("MODEL_CACHE_DIR", "/models"))
    model_config_path: Path = Path(os.getenv("MODEL_CONFIG", "/app/models.yaml"))
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "20"))
    max_image_pixels: int = int(os.getenv("MAX_IMAGE_PIXELS", "60000000"))
    default_model: str = os.getenv("DEFAULT_MODEL", "moses-yolov12x")
    device: str = os.getenv("DEVICE", "cpu")
    polygon_epsilon: float = float(os.getenv("POLYGON_EPSILON", "0.008"))
    rectangle_fill_threshold: float = float(os.getenv("RECTANGLE_FILL_THRESHOLD", "0.965"))
    percent_decimals: int = int(os.getenv("PERCENT_DECIMALS", "3"))
    database_url: str = os.getenv("DATABASE_URL") or _default_database_url()
    media_dir: Path = Path(os.getenv("MEDIA_DIR", "/data/media"))
    gcd_username: str = os.getenv("GCD_USERNAME", "")
    gcd_password: str = os.getenv("GCD_PASSWORD", "")


settings = Settings()


def load_model_specs() -> dict[str, ModelSpec]:
    with settings.model_config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return {name: ModelSpec(**spec) for name, spec in raw.get("models", {}).items()}
