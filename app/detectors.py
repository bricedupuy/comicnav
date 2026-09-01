from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from time import perf_counter

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

    def _get_with_timings(self, name: str) -> tuple[YOLO, dict[str, float]]:
        if name not in self.specs:
            raise KeyError(name)

        model = self.models.get(name)
        if model is not None:
            return model, {"model_download": 0.0, "model_initialize": 0.0}

        with self._lock:
            model = self.models.get(name)
            if model is None:
                download_started = perf_counter()
                checkpoint = self._download(self.specs[name])
                download_ms = (perf_counter() - download_started) * 1000
                initialize_started = perf_counter()
                model = YOLO(str(checkpoint), task=self.specs[name].task)
                initialize_ms = (perf_counter() - initialize_started) * 1000
                self.models[name] = model
                return model, {
                    "model_download": round(download_ms, 1),
                    "model_initialize": round(initialize_ms, 1),
                }

        # Another request loaded this model while this request was waiting for
        # the load lock. It did not perform a model download or initialization.
        return model, {"model_download": 0.0, "model_initialize": 0.0}

    def get(self, name: str) -> YOLO:
        model, _ = self._get_with_timings(name)
        return model

    def unload(self, name: str) -> bool:
        with self._lock:
            existed = name in self.models
            self.models.pop(name, None)
            return existed

    def detect(
        self,
        name: str,
        image: Image.Image,
        confidence: float | None = None,
        timings_ms: dict[str, float] | None = None,
    ) -> list[RawPanel]:
        spec = self.specs[name]
        model, model_timings = self._get_with_timings(name)
        if timings_ms is not None:
            timings_ms.update(model_timings)
        conf = spec.default_confidence if confidence is None else confidence

        input_prepare_started = perf_counter()
        source = np.asarray(image.convert("RGB"))
        if timings_ms is not None:
            timings_ms["model_input_prepare"] = round((perf_counter() - input_prepare_started) * 1000, 1)

        inference_started = perf_counter()
        result = model.predict(
            source=source,
            conf=conf,
            imgsz=spec.imgsz,
            device=settings.device,
            verbose=False,
            retina_masks=(spec.task == "segment"),
        )[0]
        if timings_ms is not None:
            timings_ms["inference"] = round((perf_counter() - inference_started) * 1000, 1)

        result_processing_started = perf_counter()
        names = result.names
        panel_names = {n.lower() for n in spec.panel_classes}
        output: list[RawPanel] = []

        if result.boxes is None:
            if timings_ms is not None:
                timings_ms["model_postprocess"] = round((perf_counter() - result_processing_started) * 1000, 1)
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
        if timings_ms is not None:
            timings_ms["model_postprocess"] = round((perf_counter() - result_processing_started) * 1000, 1)
        return output
