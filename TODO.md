# TODO

## Speech bubbles, text regions, and OCR

### Goal

Add optional comic-layout and OCR capabilities without changing the existing panel-detection or Readium Guided Navigation responses.

### Current state

- `inkwell-yolov8n` detects only the `panel` class; it cannot detect speech bubbles or text.
- `shadowb-yolo26s-seg` is already configured and can segment `frame`, `text`, and `balloon`, but ComicNav currently filters its results to `frame`.
- The ShadowB model was trained on Manga109-derived data. Evaluate it on the target comic styles before treating it as the default for Western/colour comics.

### Decisions to make first

- [ ] Define the target OCR languages and scripts: Latin only, Japanese, or both.
- [ ] Select and evaluate an OCR engine for the target languages, quality, CPU latency, image size, and license.
- [ ] Decide whether OCR text is returned only on request or stored/exported with an edited page.
- [ ] Assemble representative, licensed test pages and agree on success criteria for panels, balloons, text regions, and transcription accuracy.

### Phase 1 — Expose layout regions

- [ ] Add model configuration for semantic classes (`panel`, `text`, and `balloon`) instead of filtering every result through `panel_classes`.
- [ ] Introduce a `RawRegion`/API schema with `type`, confidence, bounding box, and optional polygon mask.
- [ ] Add a dedicated layout-analysis response or opt-in parameter that returns panels, balloons, and text regions. Keep `/v1/analyze` and Guided Navigation panel-only by default for backward compatibility.
- [ ] Preserve high-resolution masks for text and balloon regions where available.
- [ ] Add tests that verify the ShadowB labels `frame`, `text`, and `balloon` are classified and serialized correctly.

### Phase 2 — Associate regions

- [ ] Associate each text region with the containing/most-overlapping balloon.
- [ ] Associate balloons and text regions with their containing panel where possible; leave ambiguous regions unassigned rather than guessing.
- [ ] Define deterministic ordering for text regions within a balloon, including left-to-right and right-to-left/vertical reading modes.
- [ ] Return association IDs so consumers can reconstruct `panel → balloon → text` relationships.

### Phase 3 — OCR

- [ ] Add an OCR provider interface so the recognition engine can be swapped without changing API output.
- [ ] Crop and mask detected `text` regions before recognition; do not OCR the entire balloon unless no text region is available.
- [ ] Add an explicit OCR endpoint or `ocr=true` option, with language/script selection and per-region OCR confidence.
- [ ] Return the transcription, source region geometry, language, OCR confidence, and associated balloon/panel IDs.
- [ ] Preinstall all OCR dependencies in the Docker image and cache model assets in a writable mounted directory. Do not rely on runtime `pip install`.
- [ ] Limit concurrent OCR work and request size to keep CPU-only Dokploy deployments responsive.

### Phase 4 — Editor and export

- [ ] Display/edit balloon and text-region overlays separately from panel overlays.
- [ ] Let users correct OCR text and associations before export.
- [ ] Decide an export format for text metadata; keep the existing Readium Guided Navigation output unchanged unless a compatible extension is defined.

### Validation and deployment

- [ ] Benchmark panel/layout/OCR quality and latency on the deployment CPU and representative pages.
- [ ] Add regression tests for no-panel pages, overlapping balloons, narration boxes, irregular bubbles, vertical Japanese text, and low-resolution scans.
- [ ] Document model/data licenses, OCR engine license, memory requirements, and Dokploy rebuild/redeploy steps.
