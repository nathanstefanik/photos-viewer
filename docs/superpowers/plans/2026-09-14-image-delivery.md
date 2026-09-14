# Original-Preserving Image Delivery Implementation Plan

> **For agentic workers:** Use subagent-driven-development or executing-plans to implement this plan task-by-task. Track steps below.

**Goal:** Reduce competing image work while displaying and downloading the photographer's unchanged originals.

**Architecture:** Keep the existing byte-preserving proxy/cache. Give original loading a cancellable decode-before-reveal lifecycle, and reserve existing smaller derivatives for a viewport-aware gallery.

**Tech Stack:** Vanilla JavaScript, FastAPI/httpx, SQLite/disk cache, Node built-in test runner, pytest/Ruff.

**Spec:** `docs/superpowers/specs/2026-09-14-image-delivery-design.md`

## Global Constraints

- Change photos-viewer only; leave Immich unchanged.
- Opening and downloading photographs use unchanged originals; derivatives are gallery-only (existing video posters remain).
- No visible preview-to-original transition, server image transformation, or sharpening filter.
- Authorization precedes server cache access, including conditional responses.
- Cancel obsolete work; do not introduce speculative original downloads.
- Preserve video playback, original downloads, zoom, pan, and keyboard/swipe navigation.
- Commit subjects use uppercase prefixes without colons. Push the feature branch after verification; do not merge or deploy.

## Task 1: Original-only lightbox lifecycle

**Files:** Create `frontend/js/original-image.js`, `frontend/tests/original-image.test.cjs`, `frontend/tests/lightbox.test.cjs`; modify `frontend/js/lightbox.js`, `frontend/index.html`, `backend/app/security_headers.py`, `backend/tests/test_security_headers.py`, and lightbox error styling if needed.

**Interfaces:** `OriginalImage.load(url)` returns a promise for a decoded image/object URL owned by the loader; `OriginalImage.clear()` aborts pending fetches and releases object URLs. The implementation may use a class to give each viewer an owner. Lightbox owns the instance and uses its media generation as the authoritative selected-photo identity. Gallery reads existing `State.lightboxAssetId` and needs no loader internals.

- [ ] Write Node tests using `node:test`, `assert/strict`, and `vm` to execute the actual unbundled modules with controlled fetch/Image/DOM boundaries. Catch derivative requests, early reveal, stale metadata, stale decode, and failure/close cleanup.

```js
// Contract examples for the behavioral harness:
assert.equal(image.hidden, true); // while original decode is pending
assert.deepEqual(requestedImages, ['/api/assets/photo-a/original']);
// Resolve metadata for A after opening B: B remains current.
assert.equal(lightbox.currentAsset.id, 'photo-b');
```

- [ ] Run `node --test frontend/tests/*.test.cjs`; verify new behavior fails against existing code.
- [ ] Implement abortable original fetch and decode, reveal once, release on navigation/close. Set the image hidden and remove its old source before awaiting. Handle errors visibly without substituting a preview. Fetch metadata concurrently and guard it before applying. Base the final zoom ceiling on decoded original dimensions, preserving existing fit/pan/gesture behavior. Remove the preview swap/prefetch machinery.

```js
const generation = ++this.mediaGeneration;
// Begin display synchronously from the selected asset, before metadata awaits.
// Before every async UI mutation:
if (generation !== this.mediaGeneration || this.currentAsset?.id !== asset.id) return;
```

- [ ] Add `blob:` only to CSP `img-src`; verify all other restrictions remain intact.
- [ ] Run focused Node tests and Python security tests, then inspect the diff for unnecessary line-ending changes.
- [ ] Commit `PERF Display unchanged originals without competing preview loads`.

## Task 2: Smaller gallery derivatives and viewport scheduling

**Files:** Modify `frontend/js/gallery.js`; create `frontend/tests/gallery.test.cjs`; update `.github/workflows/ci.yml` to run the Node behavioral suite.

**Interfaces:** Reuse `API.getThumbnailUrl(id, size)`, `State.lightboxAssetId`, `Gallery.createGalleryItem`, and the existing `data-view` setting. Add focused gallery-owned observation/cleanup methods as needed. `size` remains either `thumbnail` or `preview`.

- [ ] Write behavioral tests catching large previews in dense/comfortable mode, original requests from the grid, offscreen image requests, loads continuing to start while a photo is open, and missing resume/cleanup on close or rerender.

```js
assert.equal(tileImage.src, ''); // before it enters the loading margin
// After intersection in comfortable/dense mode:
assert.equal(tileImage.src, '/api/assets/photo-a/thumbnail?size=thumbnail');
// Large mode chooses the existing preview derivative, never original.
```

- [ ] Run focused tests and observe failures before implementing.
- [ ] Use a separate image IntersectionObserver with a small loading margin (200px). Assign image sources only near the viewport. Set async decoding and low fetch priority. Use thumbnail in dense/comfortable mode, preview in large mode. Update visible sources when view mode changes. Disconnect observers and remove references on rerender.
- [ ] Pause new grid image loads while `State.lightboxAssetId` is set; remove unfinished sources where possible, then reobserve/resume on close. Do not interrupt already rendered tiles. Preserve infinite-scroll and its pagination behavior.
- [ ] Add Node LTS setup and `node --test frontend/tests/*.test.cjs` to CI without introducing a frontend bundler or runtime dependency.
- [ ] Run the full Node suite and commit `PERF Load smaller gallery images only when needed`.

## Task 3: Byte fidelity, browser verification, and release notes

**Files:** Create `backend/tests/test_asset_media.py`; extend `backend/tests/test_media_cache.py` only for uncovered cancellation behavior; modify `README.md` and this plan with validation evidence.

**Interfaces:** Exercise existing FastAPI thumbnail/original/download endpoints using dependency overrides for the real cache, token, and mock upstream transport. No encoder or transformed original API is introduced.

- [ ] Verify upstream bytes equal `/original` and `/download` response bodies on first request and cache hits; original fetched upstream once. Test denial with populated cache and matching ETag. Confirm partial/aborted streams are never committed and subsequent requests can retry. Tests protecting already-correct backend behavior are characterization tests, not claimed TDD changes.

```python
assert original_response.content == jpeg_bytes
assert download_response.content == jpeg_bytes
assert upstream_original_requests == 1
```

- [ ] Run `python -m pytest`, `ruff check .`, `node --test frontend/tests/*.test.cjs`, and JavaScript syntax checks. Existing baseline: 88 Python tests pass; two third-party deprecation warnings are present.
- [ ] Launch a local fixture-backed browser page using real frontend files. Exercise grid/open/next/close, delayed originals and metadata, failed originals, large-mode switching, and downloads. Check original-only requests in the lightbox and decoded reveal. Record request/byte differences without claiming deployed speed gains.
- [ ] Document gallery/original behavior and the unavoidable full-file transfer on cold loads. Confirm no Immich settings changed and original routes remain byte-preserving.
- [ ] Commit `TEST Verify original fidelity and image loading behavior` (and focused fixes if review finds issues).
- [ ] Complete task and whole-branch reviews, resolve substantive findings, verify clean status, and push `t3code/fast-high-quality-image-loading` to origin with upstream tracking.

## Progress and decisions

- Planning updated from the conversation: 1440p/full-screen derivatives and progressive upgrades are explicitly superseded by unchanged-original display.
- Prefetch is deferred: without deployed measurements, avoiding speculative original bytes better serves the poor-connection requirement.
- Existing isolated worktree and feature branch verified. Baseline: 88 Python tests pass.
- Backend characterization tests were implemented before the frontend phases because they are independent: 7 new checks pass, including byte equality with a progressive JPEG carrying ICC/EXIF/comment data and cancellation cleanup. Commit `9178804`.
