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

## Panel confidence filtering

In the **Panels** section, **Minimum confidence** temporarily hides panels below
the selected percentage from the current page's editor list and canvas. The cutoff
starts at 0% and is remembered separately per page for the editing session.
Panels at the cutoff or without a confidence score are kept.

**Remove … below …%** applies the removal to that page only, as one undoable edit,
and marks it for validation (or empty if no panels remain). The slider resets to 0%
after removal so Undo immediately shows restored panels. Until removal, all panels
remain in saved drafts, exports and reader Preview; moving the slider alone does
not change the comic. Use **Save project** to persist actual removals.

Checks: `node tests/test_panel_confidence.cjs`.

## Preview effects

In Preview, enable **Blur outside panel** to blur the area outside the focused
panel. It can be combined with Normal, B&W, Dark or Faded, in single-page and
two-page spread mode. The focused panel stays sharp, with the existing Feather
setting softening the boundary; full-page/spread overviews remain unaffected.
Blur is off by default. **Advanced → Blur strength** adjusts the radius from
0–1% of the page/spread's shorter side in 0.05% steps (default 0.4%, independent of filter opacity).

Preview effect checks: `node tests/test_preview_effects.cjs`.
For a browser pixel regression, serve the repository locally with
`python -m http.server 8765 --bind 127.0.0.1` and open
`http://127.0.0.1:8765/tests/test_preview_browser.html`. It checks both page
edges and the sharp focused panel using the real SVG renderer.

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

### Find metadata with GCD

Open **Metadata → Find metadata** and select **Grand Comics Database**. Enter the series
name and issue number, choose the **language**, with an optional **issue** year, then search. Language
is prefilled from the project's metadata (including ComicInfo.xml), or left blank
for all languages when unknown. Enter an ISO language code such as `fr`, `nl` or
`en`; regional codes such as `fr-BE` search the base language `fr`.
The search
is available with or without ComicInfo.xml; it opens automatically when no
ComicInfo.xml was imported. If a search finds nothing, shorten the series name
or remove the year (some GCD entries have no indexed date).

Candidate languages are shown before comparison. GCD does not currently expose
a language search parameter, so the server resolves and caches each distinct
series' language and filters each result page locally. Unknown-language records
are excluded when a language is selected. An empty filtered page may still have
matches on **Next results**; the UI preserves pagination and reports exclusions.
These extra series lookups can make an uncached search slower.

Choose **Compare fields** on a candidate. Review its language, publication
details and edition, then select which fields to use. Only empty fields are
preselected. **Use selected fields → Apply metadata → Save project** persists
the values. Cancel discards changes to the metadata form. Release group and
processing tags remain independent of GCD's publication metadata.

Imported fields retain the GCD issue ID/link, original imported value, retrieval
time, adapter version and attribution. One raw issue/series/publisher snapshot
is stored per referenced GCD issue in the existing metadata JSONB column (up to
eight referenced issues per project). Manual edits remove that field's active
GCD provenance. Saved source links remain visible in the metadata panel.

The server uses GCD's API with optional Basic Auth. In Dokploy, set both
`GCD_USERNAME` and `GCD_PASSWORD` to use a GCD account; leaving both blank uses
anonymous access. Credentials never reach the browser. Access may be limited
by GCD; the UI reports authentication errors, timeouts and rate limits.
Outbound requests are serialized per process with one-second spacing, a
bounded one-hour in-memory cache, and a cooldown after HTTP 429. Cache and
throttle state reset when the process restarts; a shared limiter is needed if
the app later runs multiple workers.

- `GET /v1/metadata/gcd/search?series=Largo%20Winch&number=25&language=fr` returns French candidates (omit `language` for all languages).
- `GET /v1/metadata/gcd/issues/{id}` returns normalized fields and provenance.

These are metadata suggestions; selecting one does not assert a matching page
layout or validate guided navigation. Metadata attribution is
[Grand Comics Database](https://www.comics.org/),
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
The adapter follows the [GCD API documentation](https://github.com/GrandComicsDatabase/gcd-django/wiki/API).

### Find metadata with Comic Vine

1. Obtain your own key from [Comic Vine's API page](https://comicvine.gamespot.com/api/).
2. Set `COMICVINE_API_KEY` in Dokploy's environment (or your untracked `.env`),
   then redeploy. The key is server-only: never enter it in project metadata,
   client code, or a committed file. No new database migration is required.
3. Open **Metadata → Find metadata → Source: Comic Vine**. Search by series and
   issue number, select **Show matching issues** on a series, then **Compare fields**
   on an issue. The optional year filters issue cover dates, not the series start year.
4. Select fields, **Use selected fields → Apply metadata → Save project**.
   Existing values remain unchecked; Cancel discards unapplied changes.

Comic Vine does not provide a verified language field in this integration.
Language filtering is disabled for this source, candidates are explicitly
language-unverified, and existing project language is never inferred or overwritten.
Check the edition manually; use GCD when language-filtered lookup is essential.
The connector imports series, number, title, publisher, cover-date components,
and supported creator credits; release/scan details remain independent.

Imported fields retain their Comic Vine ID, link, timestamp and normalized-value
snapshot. GCD and Comic Vine sources can coexist (eight records total per project).
Comic Vine records retain their own API-terms label, **not** GCD's CC BY-SA license.
Its [API terms](https://comicvine.gamespot.com/api/) restrict commercial use,
competing products and redistribution. Review permissions before serving these
values from the planned public Comics API; this connector does not grant that right.

Requests have bounded one-hour caching, one-second spacing, a conservative
180-request/resource/hour budget per process, and provider rate-limit cooldowns.
The key is redacted from HTTP request logs and is not stored in provenance.
Multiple workers or deployments sharing a key need a shared rate limiter.

- `GET /v1/metadata/comicvine/search?series=Largo%20Winch&number=25` lists series.
- Add `volume_id=<selected id>` to list matching issues; `page` paginates either stage.
- `GET /v1/metadata/comicvine/issues/{id}` returns fields and provenance.

Tests use synthetic provider responses; live Comic Vine access requires your key.

For local verification, install the app dependencies and `pytest`, then run
`python -B -m pytest -q`. The editor's JavaScript parser and metadata interaction
checks can be run separately with `node tests/test_editor_metadata.cjs`.

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
