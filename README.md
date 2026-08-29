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
- `MEDIA_CACHE_DIR` / `MEDIA_CACHE_MAX_BYTES` — on-disk cache for thumbnails,
  previews, and person faces (default `/data/media-cache`, 2 GiB LRU cap).
  Authorization is still checked on every request; this only skips re-fetching
  the same bytes from Immich.

## Deploy

Build and run with Docker Compose on the host that can reach Immich:

```bash
docker compose up -d --build
```

App listens on `:8080` by default. Put a TLS-terminating reverse proxy in front for the public hostname.

Access codes, comments, reactions, and the thumbnail cache live in `./data/`
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
backend/     FastAPI proxy, auth, token CLI
frontend/    static SPA (Immich-styled timeline)
Dockerfile   single image: API + static
```

Upstream git remote: `upstream` → JimmyeJones/Immich-View-Only-Web-Interface.
