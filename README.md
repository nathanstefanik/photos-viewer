# Photos viewer

Gated, read-only photo gallery for friends. Fork of
[Immich View-Only Web Interface](https://github.com/JimmyeJones/Immich-View-Only-Web-Interface).

| Host | Who | Role |
|------|-----|------|
| `photos.example.com` | friends | this app |
| `immich.example.com` | you | Immich admin |

Friends never talk to Immich. The browser only hits this app; Immich API calls (including CLIP smart search) happen server-side with a scoped API key.

```
friend → reverse proxy → viewer → Immich (LAN)
```

Host-specific domains, IPs, and deploy commands live in `LOCAL.md` (gitignored). Copy from this README’s placeholders when setting up a new machine.

## Security model

1. **Allowlisted proxy** — only read routes (assets, thumbs, people, search). No upload/delete/PATCH.
2. **Scoped Immich key** — `asset.read`, `asset.view`, `asset.download`, `person.read`, `server.about`.
3. **Access codes** — 6-char verbal codes (`ABC-DEF`) or `/t/ABCDEF` links → HttpOnly session cookie. Revoke per code.

Text search uses Immich’s existing `/api/search/smart` (CLIP). Filter-only queries use `/api/search/metadata`.

## Config

Copy `example.env` → `.env` (never commit secrets):

- `IMMICH_URL` — LAN Immich, e.g. `http://192.168.1.10:2283`
- `IMMICH_API_KEY` — scoped key from Immich
- `SESSION_SECRET` — `openssl rand -hex 32`
- `PUBLIC_BASE_URL` — public HTTPS origin, e.g. `https://photos.example.com`
- `CORS_ORIGINS` — usually the same origin as `PUBLIC_BASE_URL`
- `TRUST_X_FORWARDED_FOR` / `TRUSTED_PROXY_IPS` — only if a reverse proxy sits
  in front and you want the `/t/{code}` rate limit keyed by the real client
  IP. Both must be set together — `TRUSTED_PROXY_IPS` is the proxy's address
  (or CIDR range); without it, `X-Forwarded-For` is just a header anyone
  connecting directly can forge to dodge the rate limit. Leave both unset if
  you're not fronting this with a proxy.
- `MEDIA_CACHE_DIR` / `MEDIA_CACHE_MAX_BYTES` — on-disk cache for previews,
  originals, and person faces (default `/data/media-cache`, 8 GiB LRU cap).
  Authorization is still checked on every request; this only skips re-fetching
  the same bytes from Immich. Bytes are stored as-is, never recompressed.

## Photo quality and loading

Opening a photo and downloading it use the original file from Immich, byte for
byte. The viewer does not resize, recompress, sharpen, or strip the color profile
or metadata from those files. It waits until the original is downloaded and decoded
before revealing the photo, then scales it in the browser to fit the screen.
There is no preview-to-original quality transition. If a browser cannot decode the
original format, the viewer shows an error and keeps the original download available.

The gallery uses Immich's existing smaller thumbnails in Dense and Comfortable
modes, and its larger previews in Large mode. Only images near the viewport are
requested. Grid loading pauses while a photo is open, and unfinished original
requests are cancelled when navigating or closing. No speculative originals are
downloaded. Immich settings and the original/download endpoints are unchanged.

The existing private browser cache and on-disk server cache avoid repeat work.
A first visit to an uncached original still transfers the full file; speed on a
poor connection depends on the exported JPEG's size. These changes reduce competing
requests and unnecessary waiting, rather than reducing original quality.

For development checks, install the Python dependencies from
`backend/requirements-dev.txt` and run `pytest` and `ruff check .`. Run frontend
behavioral tests with Node 22 or newer: `node --test frontend/tests/*.test.cjs`.
The frontend remains static JavaScript without a bundler or runtime npm dependencies.

## Deploy

Build and run with Docker Compose on the host that can reach Immich:

```bash
docker compose up -d --build
```

App listens on `:8080` by default. Put a TLS-terminating reverse proxy in front for the public hostname.

Access codes, comments, reactions, and the media cache live in `./data/`
(bind-mounted to `/data` in the container). That directory survives image
rebuilds; do not delete it when redeploying.

If you previously used the named Docker volume and need to keep existing data:

```bash
mkdir -p ./data
docker run --rm -v photos-viewer_viewer-data:/from -v "$(pwd)/data:/to" alpine cp -a /from/. /to/
```

## Access codes

```bash
docker compose exec viewer python cli.py issue --label friends
docker compose exec viewer python cli.py list
docker compose exec viewer python cli.py revoke <id>
```

`issue` prints a short **code** (say out loud) and a **link**. Guests can also enter the code at `/gate`.

## Layout

```
backend/
  app/
    main.py           app wiring (lifespan, middleware, mounts)
    auth.py           session cookies + auth gate
    tokens.py         access-code store
    social_store.py   reactions/comments store
    memory_cache.py   in-memory TTL cache
    media_cache.py    on-disk Immich media cache
    scope.py          album-scope enforcement
    routers/          HTTP handlers (access, assets, people, search, social, activity)
  cli.py              issue / revoke / list access codes
  tests/
frontend/             static SPA (css/, js/, fonts/) — no bundler
Dockerfile            single image: API + frontend copied to static/
```

Upstream git remote: `upstream` → JimmyeJones/Immich-View-Only-Web-Interface.
