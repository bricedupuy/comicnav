# ComicNav API plan

## 1. Product direction

ComicNav will evolve from a local panel-detection/editor tool into a guided-navigation platform for comics.

The product is a **content-addressed guided-navigation registry**, not a general-purpose CBZ distribution service:

- ingest private comic archives and derive a page manifest;
- match an archive to a known work and, critically, to the correct edition/layout;
- create, validate, version, and publish Readium Guided Navigation documents;
- let external readers find the best available guide for the pages they hold;
- later add carefully sourced metadata and licensed visual assets for series, works, editions, and issues.

Guided navigation must be attached to a **layout fingerprint**, never merely to a title or issue number. The same work can have standard, documented, collector, translated, cropped, re-encoded, or upscaled editions with different page counts and different page order.

```text
Work:       Largo Winch, T25, Si les dieux t'abandonnent
Layout:     Standard / Documentee / Huberty et Breyne / Slumberland
Rendition:  original digital / ePub extract / re-encode / resize / upscale
Archive:    one uploaded CBZ or CBR file
Guide:      versioned panels tied to a layout page manifest
```

## 2. Principles and non-goals

### Principles

- Prefer a reliable "related layout" response over a false exact match.
- A reader should get the best safe result available: exact guide, compatible guide, partial mapped guide, candidates to choose from, or a clear no-match result.
- Keep original archives and derived images private by default.
- Do not put archive bytes in PostgreSQL.
- Make every match explainable: record the evidence, score, page correspondence, and human overrides.
- Start as a modular monolith plus workers, not a microservice fleet.
- Keep panel coordinates normalized so a guide can work across compatible resolutions.

### Non-goals for the first public release

- Hosting or distributing copyrighted CBZ/CBR files to third parties.
- Treating filename or release-group text as canonical metadata.
- Automatically applying a guide to a related edition with unverified page changes.
- Building a complete TMDB-like metadata catalogue before matching and guide serving are reliable.

## 3. Current foundation

The current FastAPI service already provides a strong ingestion/editor foundation:

- model discovery and panel analysis (`/v1/models`, `/v1/analyze`);
- Readium Guided Navigation export with normalized rectangle and polygon coordinates;
- a browser editor with Inkwell default analysis, page review states, per-page history, preview modes, and CBZ extraction;
- timing instrumentation for model and image-processing work.

This remains the analysis and guided-navigation core. The public platform adds persistence, jobs, matching, authentication, and a versioned API around it.

## 4. Recommended architecture

```text
Reader SDK / editor / external client
        |
        v
Public gateway + Better Auth (Node/TypeScript)
        | validates session or API key, applies request limits
        v
FastAPI core API --------------------------------- PostgreSQL
        |                                                 |
        |                                                 +-- works, layouts, guides, manifests,
        |                                                     users/org references, jobs, usage
        |
        +-- Redis ------------------------------------ rate limits, queue, cache
        |
        +-- S3-compatible object storage ------------ private CBZ/CBR and page derivatives
        |
        +-- Python worker --------------------------- extraction, fingerprints, Inkwell,
                                                       matching, thumbnails, OCR later
```

### Why this shape

- Keep FastAPI and Python for image decoding, ONNX/Inkwell, and matching work already present in the project.
- Use a very small Node/TypeScript gateway for Better Auth instead of rewriting the analysis pipeline in TypeScript.
- Run the core API and worker from the same Python package/image at first, with distinct processes and queue roles.
- Do not expose the FastAPI core directly to the Internet; only the gateway can call it. The gateway forwards a short-lived, signed internal principal context after authentication.
- Keep the public REST API versioned and OpenAPI-described. FastAPI supports endpoint-level OAuth-style scopes in its security dependencies. [FastAPI OAuth2 scopes](https://fastapi.tiangolo.com/advanced/security/oauth2-scopes/)

## 5. Chosen stack

| Concern | Choice | Notes |
|---|---|---|
| Core API and analysis | Python, FastAPI, Uvicorn | Retains existing detector/editor capabilities. |
| Public auth gateway | Node.js/TypeScript with Hono or Fastify + Better Auth | Small boundary service for browser sessions, API-key verification, and public rate-limit policy. |
| Authentication and organizations | Better Auth | Use organization and API-key plugins; keep application authorization decisions explicit. |
| Relational data | PostgreSQL 17+ | Primary source of truth for transactional, versioned, and relational data. |
| Fuzzy text candidates | PostgreSQL `pg_trgm` | Fast title/filename candidate searches; never final matching. [PostgreSQL `pg_trgm`](https://www.postgresql.org/docs/17/pgtrgm.html) |
| Queue, cache, distributed limits | Redis | Jobs, short-lived candidate caches, token buckets, and concurrency locks. |
| Archive and media storage | S3-compatible object storage | MinIO on Dokploy initially, or a managed S3-compatible provider later. [MinIO object storage](https://min.io/docs/minio/linux/index.html) |
| Image/matching primitives | Pillow/OpenCV in Python | Perceptual hashes and candidate-only visual verification. |
| Background jobs | Python worker using Redis-backed queue | Extraction, normalization, OCR later, Inkwell, manifesting, and match scoring must not block API requests. |
| Reader reference SDK | TypeScript package + browser demo | Lets web readers create manifests and apply mapped Readium guides. |
| Deployment | Docker Compose/Dokploy initially | Separate services: gateway, core API, worker, PostgreSQL, Redis, and object storage. |

Avoid Elasticsearch, GraphQL, Kubernetes, and separate services for every domain in the first release. PostgreSQL plus `pg_trgm` is enough for metadata candidate retrieval; image evidence decides the layout.

## 6. Authentication, authorization, and limits

### Better Auth

Better Auth is the selected identity platform.

- Browser/editor users use Better Auth sessions and supported login methods.
- Organizations own projects, archives, guides, and organization API keys.
- Roles begin as `owner`, `admin`, `editor`, `reviewer`, and `reader`.
- Better Auth's organization plugin provides member/role/permission primitives. [Organization plugin](https://better-auth.com/docs/plugins/organization)
- Use Better Auth's API-key plugin for key lifecycle, expiration, ownership, metadata, permissions, and key-level request limits. It supports organization-owned keys and Redis-backed secondary storage. [API-key plugin](https://better-auth.com/docs/plugins/api-key)

### API access rules

- The browser editor uses session authentication.
- Third-party readers use organization-owned opaque API keys, sent with `X-API-Key`.
- Keys are scoped, named, expirable, revocable, and shown only once when created.
- Suggested scopes: `guides:read`, `matches:resolve`, `archives:ingest`, `guides:write`, `metadata:write`, and `admin`.
- The gateway validates a key once, applies permissions and rate limits, then forwards only a signed internal principal (`key_id`, organization, scopes, plan) to FastAPI.
- Do **not** make FastAPI reimplement Better Auth's key hashing or query Better Auth tables directly. Put a narrow internal auth adapter/gateway boundary in front of it.

### Limits and abuse controls

Use several independent controls:

1. IP-based edge limits for unauthenticated/login traffic.
2. Per-key request limits enforced by Better Auth.
3. Redis token buckets for expensive endpoints such as archive ingest, candidate verification, and model work. Token buckets permit short bursts while holding an average limit. [Redis token bucket guidance](https://redis.io/docs/latest/develop/use-cases/rate-limiter/redis-py/)
4. Per-organization storage, byte-transfer, archive-size, and monthly processing quotas stored in PostgreSQL.
5. Queue concurrency limits so one organization cannot consume all image/ONNX workers.

Better Auth's own documentation warns against treating leaked API keys as browser sessions. Keep organization API keys as machine credentials rather than user impersonation sessions. [API-key advanced guidance](https://better-auth.com/docs/plugins/api-key/advanced)

## 7. Data model

Use UUID/ULID identifiers and immutable versions where content is published.

```text
organization
user (owned by Better Auth)
project

series
work                  # canonical comic / issue identity
layout                # an edition-specific ordered page set
layout_alias          # publisher, language, edition label, external IDs
archive               # a private uploaded CBZ/CBR instance
archive_page          # original page facts and fingerprints
page_manifest         # immutable ordered fingerprint set

guided_navigation     # logical guide for a layout
guide_version          # immutable published/draft/reviewed version
guide_page
guide_panel            # normalized geometry and reading order

asset                 # cover/background/logo; rights and source required
ingest_job
match_attempt
match_candidate
page_correspondence
usage_ledger
audit_event
```

Important relationships:

- A `work` can have many `layouts`.
- A `layout` can have multiple low-level renditions and uploaded archives.
- A guide version belongs to one layout manifest.
- One archive may be recognized as an exact rendition, compatible rendition, related layout, or unknown.
- `release group` and raw filename are import facts, not canonical identity.

## 8. Archive retention and object storage

Archive originals are required while matching, detection, and human panel validation are in progress. Store them in a private bucket, never in PostgreSQL.

For every page, persist:

- original ordinal and internal filename;
- decoded dimensions, aspect ratio, format, and byte size;
- archive SHA-256 and original-page SHA-256;
- compact perceptual/layout signatures;
- a private 320px matching derivative and a 640px editor/UI derivative;
- optional candidate-verification features calculated only when needed.

Retention states:

```text
incoming -> processing -> matched/review -> published or expired -> deleted
```

Keep hashes and manifests long-term. Make original and derivative retention configurable by organization and product policy. Public endpoints must return guide data and only media with an explicit right to distribute.

## 9. Matching design

### Filename is a soft signal

Normalize filename text and extract possible series, volume, issue, title, creators, publisher, year, language, edition label, resolution, source group, and archive format. Preserve both raw and parsed forms with a parser confidence.

Example variants such as `Edition Documentee`, `Huberty et Breyne`, and `Slumberland` are useful candidate clues, but must be confirmed by page evidence. Different release groups and filenames may represent the same layout; a similar filename may represent a materially different one.

### Layered evidence

| Evidence | Cost | Role |
|---|---:|---|
| Normalized filename, issue, language, publisher, year | Tiny | Find possible works/layouts. |
| Page count and aspect-ratio sequence | Tiny | Reject many incompatible editions quickly. |
| Archive and original-page SHA-256 | Tiny | Confirm exact duplicates. |
| Per-page perceptual hash + edge/layout hash | Tiny | Find resized or re-encoded page correspondences. |
| Anchor-page comparisons | Low | Compare cover plus several interior/final pages. |
| Page-sequence alignment with inserts/deletes | Low | Recognize related editions with added/removed pages. |
| ORB feature verification on shortlisted pairs | Moderate | Confirm artwork/layout when crops or resizes make hashes uncertain. |

OpenCV has pHash and related image-hash primitives. ORB uses multiscale keypoints and should be used as a candidate verifier, not an all-against-all database operation. [OpenCV image hashing](https://docs.opencv.org/4.12.0/javadoc/org/opencv/img_hash/Img_hash.html), [OpenCV ORB](https://docs.opencv.org/4.10.0/db/d95/classcv_1_1ORB.html)

### Match outcomes

```text
exact_layout
  Same ordered layout and evidence is above the strict threshold.
  Safe to serve the guide automatically.

compatible_rendition
  Same page sequence/artwork; resolution, encoding, or small non-layout changes differ.
  Safe to serve the guide automatically.

related_layout
  Clearly the same work but pages were inserted, removed, moved, or materially changed.
  Offer candidates and an optional partial page map. Never claim an exact guide.

ambiguous
  Several candidates are plausible. Return choices and request targeted verification.

unknown
  No trustworthy candidate. Do not serve a guide.
```

### Sequence alignment

The matcher aligns the incoming page-signature sequence against candidate layout sequences, allowing insertions and deletions. This detects a documented edition that contains extra material without incorrectly shifting all later panels.

Each correspondence stores:

```json
{
  "guide_page": 12,
  "reader_page": 14,
  "confidence": 0.992,
  "method": "perceptual_hash+dimensions",
  "transform": null
}
```

When a compatible rendition has a small crop/reframe, a candidate verification pass may return a normalized 3x3 transform. The reader transforms guide polygons before drawing them. It must never invent a mapping for a low-confidence page.

## 10. Public API shape

All public endpoints are versioned under `/v1`.

| Endpoint | Purpose |
|---|---|
| `POST /v1/manifests` | Create or register a page manifest without uploading an archive. |
| `POST /v1/matches` | Resolve a reader/archive manifest to exact, compatible, related, or candidate layouts. |
| `POST /v1/matches/{id}/verify` | Submit only requested low-resolution pages for ambiguous candidate verification. |
| `GET /v1/layouts/{layout_id}` | Read safe public layout metadata and available guide versions. |
| `GET /v1/layouts/{layout_id}/guides/{version}` | Return Readium Guided Navigation JSON. |
| `POST /v1/archives` | Start private direct-to-object-storage archive ingest. |
| `GET /v1/jobs/{job_id}` | Read ingest, analysis, and match progress. |
| `POST /v1/guides` | Create a draft guide version. |
| `POST /v1/guides/{id}/publish` | Publish a reviewed immutable guide version. |

`POST /v1/matches` should return useful alternatives, not only an error:

```json
{
  "status": "related_layout",
  "candidates": [
    {
      "layout_id": "layout_documentee_2025_fr",
      "label": "Largo Winch T25 - Edition Documentee (French, 2025)",
      "guide_version": "2026-03-01",
      "compatibility": {
        "score": 0.94,
        "mapped_pages": 51,
        "guide_pages": 56,
        "reader_pages": 58,
        "safe_for_partial_navigation": true
      },
      "page_map": [
        { "guide_page": 0, "reader_page": 0, "confidence": 0.998 },
        { "guide_page": 1, "reader_page": 1, "confidence": 0.996 }
      ],
      "unmatched_reader_pages": [12, 13, 57],
      "unmatched_guide_pages": [34, 35]
    }
  ]
}
```

## 11. State-of-the-art reader experience

### Reader behavior

1. Open the reader immediately; never block reading while matching.
2. Build a lightweight local manifest from pages already available to the reader.
3. Ask the API for a match in the background.
4. Enable guided navigation automatically only for `exact_layout` or `compatible_rendition`.
5. For `related_layout`, show a calm choice sheet that explains coverage and lets the reader:
   - use mapped navigation only on safe pages;
   - choose another candidate layout;
   - verify selected ambiguous pages;
   - continue without guided navigation.
6. Unmapped pages remain readable as normal pages. Do not show a misleading panel overlay.
7. Remember a user-confirmed layout choice for that local archive fingerprint.

### Proposed TypeScript reader SDK

The reference package, tentatively `@comicnav/reader-sdk`, owns local manifest creation, API calls, page-map application, and optional browser verification. It does **not** require uploading the CBZ for ordinary matching.

```ts
import {
  ComicNavClient,
  createPageManifest,
  applyMappedReadiumGuide,
} from "@comicnav/reader-sdk";

const client = new ComicNavClient({
  baseUrl: "https://api.example.com/v1",
  apiKey: "cnav_live_...",
});

// `readerPages` are the pages the host reader has already decoded.
// The SDK downsizes locally and sends dimensions + compact signatures first.
const manifest = await createPageManifest(readerPages, {
  matchingLongEdge: 256,
  includeFilenameHint: true,
});

const match = await client.match({
  filename: archive.filename,
  manifest,
});

const exact = match.candidates.find((candidate) =>
  ["exact_layout", "compatible_rendition"].includes(candidate.matchKind),
);

if (exact) {
  const guide = await client.getGuide(exact.layoutId, exact.guideVersion);
  reader.installGuidedNavigation(
    applyMappedReadiumGuide(guide, exact.pageMap),
  );
} else {
  const usable = match.candidates.filter(
    (candidate) => candidate.compatibility.safeForPartialNavigation,
  );

  const choice = await reader.showGuideChoices({
    title: "A similar edition has guided navigation",
    candidates: usable.map((candidate) => ({
      id: candidate.id,
      label: candidate.label,
      detail: `${candidate.compatibility.mappedPages}/${manifest.pages.length} pages align`,
      confidence: candidate.compatibility.score,
    })),
    actions: ["use-partial", "verify-pages", "continue-without-guide"],
  });

  if (choice.action === "use-partial") {
    const candidate = usable.find((item) => item.id === choice.candidateId)!;
    const guide = await client.getGuide(candidate.layoutId, candidate.guideVersion);

    // Only correspondence entries above the server's safe threshold are applied.
    // Unmatched local pages receive no panel overlay and remain normally readable.
    reader.installGuidedNavigation(
      applyMappedReadiumGuide(guide, candidate.pageMap),
    );
  }

  if (choice.action === "verify-pages") {
    const request = await client.getVerificationRequest(match.id, choice.candidateId);
    const verification = await createPageManifest(
      request.readerPageIndexes.map((index) => readerPages[index]),
      { matchingLongEdge: 640, includeVerificationThumbnail: true },
    );

    const resolved = await client.verifyMatch(match.id, {
      candidateId: choice.candidateId,
      pages: verification.pages,
    });

    if (resolved.compatibility.safeForPartialNavigation) {
      const guide = await client.getGuide(resolved.layoutId, resolved.guideVersion);
      reader.installGuidedNavigation(
        applyMappedReadiumGuide(guide, resolved.pageMap),
      );
    }
  }
}
```

`applyMappedReadiumGuide()` maps guide-page indexes to reader-page indexes and preserves the existing Readium `xywh=percent:` or `points=percent:` geometry. If a high-confidence page transform is supplied, it transforms polygon points before normalizing them for the reader page. This directly builds on the current editor's normalized guided-navigation export.

### Expected UX copy for a related edition

```text
We found a similar French 2025 edition.

51 of 58 pages line up with its guided navigation.
Five pages differ or are missing, so those pages will stay in normal reading mode.

[Use on matching pages] [Check a few pages] [Continue normally]
```

## 12. Implementation phases

### Phase 0 - decisions and test corpus

- Confirm tenancy model: personal use only first, or organizations from day one.
- Define archive/media retention and rights policy.
- Define whether public matching is key-only or has a small anonymous quota.
- Build a labelled regression corpus from known files, including the Largo Winch variants:
  - exact same archive;
  - same layout at a different resolution/encoding;
  - related edition with added pages;
  - different layout with a deceptively similar name.

### Phase 1 - persistence and guide versions

- Introduce PostgreSQL migrations and project/archive/layout/guide tables.
- Persist editor status, panel edits, model provenance, and immutable guide versions.
- Add user/project isolation before exposing public write endpoints.

**Initial vertical slice implemented (local/trusted deployment):** PostgreSQL
projects, persisted page images/drafts, page review/model provenance, and
immutable guide versions are now available through `/v1/projects`. Page images
are currently held on the persistent application volume behind a storage
adapter; this is a bridge to the private S3-compatible archive/media layer,
not the final archive-ingest design. The routes intentionally remain
unauthenticated until organization boundaries and the Better Auth gateway are
introduced.

### Phase 2 - private archive ingest

- Add object storage and signed uploads.
- Add worker jobs for extraction, derivatives, manifest fingerprints, Inkwell, and progress.
- Add retention state and deletion jobs.

### Phase 3 - matching and review

- Implement filename candidate retrieval, manifest comparison, page-sequence alignment, and match evidence.
- Add related-layout candidate UX to the editor.
- Add manual match/link/override workflows with audit records.

### Phase 4 - public read API and SDK

- Add Better Auth gateway, organization keys, scope enforcement, usage ledger, and rate limits.
- Publish `POST /v1/matches` and guide retrieval.
- Release the TypeScript SDK and a reference reader/demo.

### Phase 5 - metadata and media

- Add sourced series/work/layout metadata with provenance and moderation.
- Add only licensed/authorized covers, logos, and backgrounds.
- Keep metadata optional; it must not control guide safety.

## 13. Risks to handle deliberately

- Model and training-data licenses already noted in `README.md` must be reviewed before a production API.
- Archive ingestion and storing page derivatives require a clear privacy, copyright, and retention policy.
- CBR support requires a separate archive-reading path and security review; treat every uploaded archive as untrusted.
- Matching thresholds must be calibrated with labelled real-world editions. Never hard-code confidence values from a handful of files.
- A false exact match is worse than a useful partial/related response.

## 14. Immediate next deliverable

Replace the bootstrap `create_all` schema setup with versioned migrations, then
write the page-manifest and match-response schemas. Add the Largo Winch files
(or safe fingerprint fixtures) as the first matching regression suite. That
contract must be settled before public reader/matching routes, because it
defines guide safety, reader interoperability, and object-storage needs.
