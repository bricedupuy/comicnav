from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import numpy as np
from huggingface_hub import hf_hub_download
from PIL import Image
from ultralytics import YOLO

from .config import ModelSpec, settings


@dataclass
class RawPanel:
    confidence: float
    label: str
    bbox: tuple[float, float, float, float]  # x, y, width, height
    polygon: np.ndarray | None = None


class ModelManager:
    def __init__(self, specs: dict[str, ModelSpec]):
        self.specs = specs
        self.models: dict[str, YOLO] = {}
        self._lock = Lock()

    def _download(self, spec: ModelSpec) -> Path:
        settings.model_cache_dir.mkdir(parents=True, exist_ok=True)
        path = hf_hub_download(
            repo_id=spec.repo_id,
            filename=spec.filename,
            cache_dir=str(settings.model_cache_dir / "huggingface"),
        )
        return Path(path)

    def get(self, name: str) -> YOLO:
        if name not in self.specs:
            raise KeyError(name)
        if name in self.models:
            return self.models[name]
        with self._lock:
            if name not in self.models:
                checkpoint = self._download(self.specs[name])
                self.models[name] = YOLO(str(checkpoint), task=self.specs[name].task)
        return self.models[name]

    def unload(self, name: str) -> bool:
        with self._lock:
            existed = name in self.models
            self.models.pop(name, None)
            return existed

    def detect(self, name: str, image: Image.Image, confidence: float | None = None) -> list[RawPanel]:
        spec = self.specs[name]
        model = self.get(name)
        conf = spec.default_confidence if confidence is None else confidence

        result = model.predict(
            source=np.asarray(image.convert("RGB")),
            conf=conf,
            imgsz=spec.imgsz,
            device=settings.device,
            verbose=False,
            retina_masks=(spec.task == "segment"),
        )[0]

        names = result.names
        panel_names = {n.lower() for n in spec.panel_classes}
        output: list[RawPanel] = []

        if result.boxes is None:
            return output

        boxes = result.boxes.xyxy.cpu().numpy()
        classes = result.boxes.cls.cpu().numpy().astype(int)
        confidences = result.boxes.conf.cpu().numpy()
        polygons = result.masks.xy if (spec.task == "segment" and result.masks is not None) else None

        for i, (xyxy, cls_id, score) in enumerate(zip(boxes, classes, confidences)):
            label = str(names[int(cls_id)])
            if label.lower() not in panel_names:
                continue
            x1, y1, x2, y2 = [float(v) for v in xyxy]
            poly = None
            if polygons is not None and i < len(polygons) and len(polygons[i]) >= 3:
                poly = np.asarray(polygons[i], dtype=np.float32)
            output.append(
                RawPanel(
                    confidence=float(score),
                    label=label,
                    bbox=(x1, y1, x2 - x1, y2 - y1),
                    polygon=poly,
                )
            )
        return output
