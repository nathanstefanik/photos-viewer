# Original-preserving image delivery

## Approved intent

Improve loading efficiency without changing Immich. Smaller derivatives are allowed
in the gallery only. Opening and downloading a photograph must use the unchanged
exported original JPEG. Display a photograph only once decoded, with no preview,
recompression, sharpening filter, resolution upgrade, or visible progressive reveal.
The browser may scale the original to fit the display. Unsupported originals produce
an honest error with the original download still available, never a substituted preview.

## Architecture

Keep the existing authenticated original/download proxy and disk cache. Both already
store and transmit upstream bytes without image processing. Reuse Immich thumbnails
for dense/comfortable grid modes and its larger preview for large mode; no new image
encoder or Immich configuration change is needed. Grid quality can be revisited with
real photographs after this first measurable improvement.

Extract original request/decode ownership into a small browser module. Use abortable
same-origin fetch, blob URLs, and decode before revealing the full-screen image.
Explicitly release obsolete requests and object URLs. Permit blob image sources in
CSP without relaxing script, connection, or other policies. Keep only the current
decoded original in application memory. Browser HTTP caching remains responsible
for subsequent visits. Do not add a service worker or offline store.

Start image loading from the selected asset immediately. Fetch missing metadata
independently; guard every asynchronous completion with the current open generation.
Closing and rapid navigation must invalidate both metadata and image completions.
Use decoded source dimensions for zoom. Preserve existing video behavior.

Load grid images near the viewport, with lower request priority than the selected
original. Suspend new grid image requests while the lightbox is open, cancel unfinished
grid loads where browser image APIs allow, and resume on close. Do not download
speculative originals: an unknown or poor connection should dedicate its bandwidth to
the user's selection. Reconsider bounded prefetch only with real network measurements.

## Error handling and invariants

- Authentication and album checks remain before server cache hits and 304 responses.
- Original and download bytes must equal upstream bytes on cache misses and hits.
- Original failure leaves the image hidden, displays an accessible error, and allows
  download/retry via reopening; do not silently show a derivative.
- A stale load, metadata response, or close must never display another photo or update
  the current photo's metadata/social panel.
- Aborts must release browser resources and server cache waiters. No partial original
  may become a valid cache entry.
- Avoid a duplicate original transfer just to decode or reveal the same photo.

## Validation and limits

Test request ordering, original-only URLs, decode-before-reveal, rapid navigation,
close, decode/network failures, metadata races, grid modes, and grid pause/resume.
Test the HTTP endpoints for byte equality and authorization on cold/warm caches.
Run Python lint/tests, JavaScript behavioral tests, and a local browser smoke test.
Record request counts and bytes using synthetic image fixtures; do not present them
as measurements of the user's library or as perceptual quality validation.

Cold, unseen originals still require their full byte count over the connection.
There is no honest fixed latency promise without representative JPEG sizes and an
actual deployed-network measurement. First-screen sharpness intentionally waits for
the original; gallery navigation should avoid competing image work.

## Sources consulted

- [Immich image settings](https://docs.immich.app/administration/system-settings/)
  describe separate thumbnail/preview derivatives and configurable encoding.
- [MDN responsive images](https://developer.mozilla.org/en-US/docs/Web/HTML/Guides/Responsive_images)
  explains display size and pixel-density considerations.
- [MDN Network Information API](https://developer.mozilla.org/en-US/docs/Web/API/Network_Information_API)
  documents limited support; this implementation does not depend on that API.
