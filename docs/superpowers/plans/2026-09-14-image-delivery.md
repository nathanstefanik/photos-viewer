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

- [x] Write Node tests using `node:test`, `assert/strict`, and `vm` to execute the actual unbundled modules with controlled fetch/Image/DOM boundaries. Catch derivative requests, early reveal, stale metadata, stale decode, and failure/close cleanup.

```js
// Contract examples for the behavioral harness:
assert.equal(image.hidden, true); // while original decode is pending
assert.deepEqual(requestedImages, ['/api/assets/photo-a/original']);
// Resolve metadata for A after opening B: B remains current.
assert.equal(lightbox.currentAsset.id, 'photo-b');
```

- [x] Run `node --test frontend/tests/*.test.cjs`; verify new behavior fails against existing code.
- [x] Implement abortable original fetch and decode, reveal once, release on navigation/close. Set the image hidden and remove its old source before awaiting. Handle errors visibly without substituting a preview. Fetch metadata concurrently and guard it before applying. Base the final zoom ceiling on decoded original dimensions, preserving existing fit/pan/gesture behavior. Remove the preview swap/prefetch machinery.

```js
const generation = ++this.mediaGeneration;
// Begin display synchronously from the selected asset, before metadata awaits.
// Before every async UI mutation:
if (generation !== this.mediaGeneration || this.currentAsset?.id !== asset.id) return;
```

- [x] Add `blob:` only to CSP `img-src`; verify all other restrictions remain intact.
- [x] Run focused Node tests and Python security tests, then inspect the diff for unnecessary line-ending changes.
- [x] Commit `PERF Display unchanged originals without competing preview loads` (`09957ba`).

## Task 2: Smaller gallery derivatives and viewport scheduling

**Files:** Modify `frontend/js/gallery.js`; create `frontend/tests/gallery.test.cjs`; update `.github/workflows/ci.yml` to run the Node behavioral suite.

**Interfaces:** Reuse `API.getThumbnailUrl(id, size)`, `State.lightboxAssetId`, `Gallery.createGalleryItem`, and the existing `data-view` setting. Add focused gallery-owned observation/cleanup methods as needed. `size` remains either `thumbnail` or `preview`.

- [x] Write behavioral tests catching large previews in dense/comfortable mode, original requests from the grid, offscreen image requests, loads continuing to start while a photo is open, and missing resume/cleanup on close or rerender.

```js
assert.equal(tileImage.src, ''); // before it enters the loading margin
// After intersection in comfortable/dense mode:
assert.equal(tileImage.src, '/api/assets/photo-a/thumbnail?size=thumbnail');
// Large mode chooses the existing preview derivative, never original.
```

- [x] Run focused tests and observe failures before implementing.
- [x] Use a separate image IntersectionObserver with a small loading margin (200px). Assign image sources only near the viewport. Set async decoding and low fetch priority. Use thumbnail in dense/comfortable mode, preview in large mode. Update visible sources when view mode changes. Disconnect observers and remove references on rerender.
- [x] Pause new grid image loads while `State.lightboxAssetId` is set; remove unfinished sources where possible, then reobserve/resume on close. Do not interrupt already rendered tiles. Preserve infinite-scroll and its pagination behavior.
- [x] Add Node LTS setup and `node --test frontend/tests/*.test.cjs` to CI without introducing a frontend bundler or runtime dependency.
- [x] Run the full Node suite and commit `PERF Load smaller gallery images only when needed` (`34d71f8`).

## Task 3: Byte fidelity, browser verification, and release notes

**Files:** Create `backend/tests/test_asset_media.py`; extend `backend/tests/test_media_cache.py` only for uncovered cancellation behavior; modify `README.md` and this plan with validation evidence.

**Interfaces:** Exercise existing FastAPI thumbnail/original/download endpoints using dependency overrides for the real cache, token, and mock upstream transport. No encoder or transformed original API is introduced.

- [x] Verify upstream bytes equal `/original` and `/download` response bodies on first request and cache hits; original fetched upstream once. Test denial with populated cache and matching ETag. Confirm partial/aborted streams are never committed and subsequent requests can retry. Tests protecting already-correct backend behavior are characterization tests, not claimed TDD changes.

```python
assert original_response.content == jpeg_bytes
assert download_response.content == jpeg_bytes
assert upstream_original_requests == 1
```

- [x] Run `python -m pytest`, `ruff check .`, `node --test frontend/tests/*.test.cjs`, and JavaScript syntax checks. Result: 96 Python tests pass (2 third-party deprecation warnings), Ruff clean, 18 Node tests pass, all `frontend/js/*.js` pass `node --check`.
- [x] Launch a local fixture-backed browser page using real frontend files. Exercise grid/open/next/close, delayed originals and metadata, failed originals, large-mode switching, and downloads. Check original-only requests in the lightbox and decoded reveal. Record request/byte differences without claiming deployed speed gains.
- [x] Document gallery/original behavior and the unavoidable full-file transfer on cold loads. Confirm no Immich settings changed and original routes remain byte-preserving.
- [x] Commit `TEST Verify original fidelity and image loading behavior` (and focused fixes if review finds issues).
- [x] Complete task and whole-branch reviews, resolve substantive findings, verify clean status, and push `t3code/fast-high-quality-image-loading` to origin with upstream tracking. Review found one density-switch issue (a reloaded tile kept its `loaded` flag, so opening the lightbox would not cancel it) and it was fixed before the verification commit. Pushed; no merge or deploy.

## Progress and decisions

- Planning updated from the conversation: 1440p/full-screen derivatives and progressive upgrades are explicitly superseded by unchanged-original display.
- Prefetch is deferred: without deployed measurements, avoiding speculative original bytes better serves the poor-connection requirement.
- Existing isolated worktree and feature branch verified. Baseline: 88 Python tests pass.
- Backend characterization tests were implemented before the frontend phases because they are independent: 7 new checks pass, including byte equality with a progressive JPEG carrying ICC/EXIF/comment data and cancellation cleanup. Commit `9178804`.
- Task 1 commit `09957ba`; Task 2 commit `34d71f8`.
- Local smoke test used the real `frontend/index.html` and scripts served over HTTP with fixture `backend/tests/fixtures/exported.jpg` and a stub `/api`; only the test harness page was synthetic and it was never committed. Observations, with a 19,351-byte fixture standing in for a real JPEG:
  - Large grid asked for `size=preview` and loaded only the 3 of 9 tiles in view; dense mode switched visible tiles to `size=thumbnail` and prepared offscreen tiles without fetching them.
  - Opening a photo with a delayed original kept the image hidden and requested only `/original` (19,351 bytes logged once); the photo appeared from a `blob:` URL after decode with no preview request or flash.
  - Two rapid `next` clicks left the photo hidden and revealed only the third selection; the stale second selection never appeared.
  - Scrolling with the lightbox open started 0 grid requests; closing resumed with exactly the 3 newly visible tiles, and all 9 tiles completed.
  - A 415 original left the image hidden, showed the accessible error, and kept Download enabled; a successful `/download` transferred the same 19,351 bytes.
- Smoke numbers use a synthetic fixture and local server, so they demonstrate request behavior and byte accounting, not deployed latency or perceptual quality. The unavoidable full original transfer on a cold cache still governs poor-connection latency until a real deployment measurement exists.
