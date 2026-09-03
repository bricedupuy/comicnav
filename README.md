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

The Compose stack now includes PostgreSQL and durable Docker volumes for both
project page media and the database. `POSTGRES_PASSWORD` is required: set it
to a long random secret in Dokploy (or in `.env` locally) before starting the
stack. The supplied `.env.example` documents the available settings.
`DATABASE_URL` is optional: leave it blank to use the local Compose database,
or set it to use a managed PostgreSQL instance.

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

Returns confidence values, pixel geometry, normalized geometry, computed reading order, and a `timings_ms` object. The same phase timings are written to the application logs, so Dokploy logs can be used to compare requests without storing images.

Example timing data:

```json
{
  "upload_read": 2.1,
  "image_decode": 148.5,
  "image_convert": 12.2,
  "model_download": 0.0,
  "model_initialize": 0.0,
  "model_input_prepare": 3.8,
  "inference": 842.7,
  "model_postprocess": 4.3,
  "geometry_order": 0.6,
  "total": 1011.4
}
```

`model_download` and `model_initialize` are non-zero only when a model is first loaded into the container process. A downloaded model can still need initialization after a container restart; a warm model reports both as `0.0`.

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

Because the editor is served by the same FastAPI container, no CORS configuration is required.

## Saved projects and guide versions

The editor's top bar has a **Projects** selector, **Save project**, and
**Publish guide** controls. A saved project keeps its original page images,
page dimensions/background/spread state, panel geometry, review status, and
model provenance in PostgreSQL. Re-opening it restores the editor state from
the server rather than from browser memory.

Publishing creates an immutable Readium guide-version document from the saved
draft. Creating another version never changes the previous one.

Use **Metadata** in the editor's top bar to review and edit project-level
metadata. When a CBZ contains `ComicInfo.xml`, the editor reads the standard
ComicInfo fields during import and pre-fills series/issue/title, creators,
publisher, language, edition-related fields, date, and page count. Each
imported field is marked `ComicInfo.xml`; changing a field marks only that
field as `Manual`. A declared page count that differs from the actual imported
page count is shown as a warning and saved with the project.

The same panel also holds non-ComicInfo **release / rendition** fields for
matching and review: free-text scene release group (for example `TONER` or
`NEO RIP-Club`), controlled release type (`Digital`, `Scan`, `Hybrid`, ePub
extract, Web rip, and Unknown/Other), flexible processing tags, edition label,
revision, and release notes. These are intentionally separate from publisher
metadata: they describe a particular file/release, are normally entered
manually, and remain soft matching evidence rather than canonical identity.

`ComicInfo.xml` is currently a client-side import hint. It is useful metadata
and a future matching signal, but it is not treated as canonical release data.
The later private archive worker will parse the same file server-side as part
of the durable archive manifest.

The initial private API surface is intentionally small and unauthenticated:

- `POST` / `GET /v1/projects` create and list projects.
- `GET` / `PUT /v1/projects/{project_id}` load and save an editor draft.
- `POST /v1/projects/{project_id}/pages` uploads one JPEG, PNG, or WebP page.
- `POST /v1/projects/{project_id}/guide-versions` publishes an immutable guide.
- `GET /v1/projects/{project_id}/guide-versions/{version}` fetches a published guide.

Page media currently uses the durable `/data` Docker volume behind a small
storage adapter. This is deliberately a private editor persistence layer, not
the future object-storage/archive pipeline described in `PLAN.md`. The next
platform increment replaces this adapter with private S3-compatible storage,
adds versioned migrations, and introduces user/organization isolation before
these write routes are exposed beyond a trusted deployment.
