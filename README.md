# ComicNav

ComicNav is a small FastAPI service and browser-based panel editor for comic panel detection/segmentation and Readium Guided Navigation JSON.

## Included model profiles

- `moses-yolov12x` — `mosesb/best-comic-panel-detection`, bbox detection.
- `inkwell-yolov8n` — `cedarrapidsboy/inkwell-panel-models`, small bbox detector (`best.onnx`).
- `shadowb-yolo26s-seg` — Manga109-derived YOLO26s instance segmentation.
- `manga-yolo11m-seg` — YOLO11m manga page element segmentation.

Models are downloaded from Hugging Face on first use and cached in the Docker volume. They are **not baked into the image**.

> Review every model and training dataset license before production use. In particular, the Inkwell detector model card identifies its detector as AGPL-3.0 and the Manga-derived models have upstream dataset terms that need review.

## Start

```bash
cp .env.example .env
docker compose up --build -d
```

Open API docs:

```text
http://localhost:8000/docs
```

## List models

```bash
curl http://localhost:8000/v1/models
```

## Preload a model

```bash
curl -X POST http://localhost:8000/v1/models/moses-yolov12x/load
```

## Readium Guided Navigation

```bash
curl -X POST \
  'http://localhost:8000/v1/guided-navigation?model=shadowb-yolo26s-seg&geometry=auto&reading_direction=ltr' \
  -F 'file=@page_0003.jpg' \
  -o page_0003.guided.json
```

The response media type is:

```text
application/guided-navigation+json
```

Rectangular panels are serialized as:

```text
page_0003.jpg#xywh=percent:7.412,9.732,55.339,40.474
```

Irregular masks can be serialized as:

```text
page_0003.jpg#points=percent:34.314,71.061 58.49,71.267 58.629,82.441 ...
```

`geometry=auto` converts segmentation masks that are effectively axis-aligned rectangles to `xywh`, and preserves non-rectangular regions as `points`.

## Debug / benchmark endpoint

```bash
curl -X POST \
  'http://localhost:8000/v1/analyze?model=moses-yolov12x' \
  -F 'file=@page.jpg'
```

Returns confidence values, pixel geometry, normalized geometry, and computed reading order.

## Query parameters

### `/v1/analyze` and `/v1/guided-navigation`

- `model` — model name from `/v1/models`.
- `confidence` — override model confidence threshold.
- `reading_direction=ltr|rtl` — row/column reading heuristic.
- `geometry=auto|rectangle|polygon` — geometry conversion mode.
- `image_href` — Guided Navigation `imgref` base instead of uploaded filename.
- `self_href` — output document self link.

## Reading order

The initial build uses a geometry-based row grouping algorithm tuned for left-to-right European comics. It is intentionally isolated in `app/ordering.py`, so it can later be replaced with Inkwell's `panel-order-model.onnx` or another learned ordering model.

## Adding another model

Edit `models.yaml`:

```yaml
models:
  my-model:
    title: My Comic Segmentation Model
    repo_id: owner/repository
    filename: best.pt
    task: segment
    panel_classes: [frame]
    imgsz: 1024
    default_confidence: 0.25
    license: Check upstream
    notes: Optional notes
```

If the checkpoint is Ultralytics-compatible, no Python changes should be required.

## CPU/GPU

Default is CPU:

```env
DEVICE=cpu
```

For an NVIDIA-enabled Docker host, install/configure NVIDIA Container Toolkit and use a Compose GPU device reservation, then set for example:

```env
DEVICE=0
```

The supplied Compose file intentionally remains CPU-compatible by default.

## Built-in panel editor

The container now serves a browser-based QA/editor UI at:

```text
http://localhost:8000/editor
```

The editor uses the API from the same origin:

- `GET /v1/models` discovers configured models and their metadata.
- `POST /v1/analyze` runs the selected model.
- `geometry=auto` preserves segmentation polygons where useful and uses rectangles for box detectors / effectively rectangular masks.
- Detected geometry remains editable before export.
- **Detect page** runs the current page; **Detect all** runs every loaded page/CBZ page sequentially.
- **Replace existing panels** can be disabled to compare/append detections.
- Export produces per-page Readium Guided Navigation JSON with `xywh=percent:` and `points=percent:` fragments.

Because the editor is served by the same FastAPI container, no CORS configuration is required 