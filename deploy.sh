#!/usr/bin/env bash
# Deploy photos-viewer to the homelab host (Proxmox VE, Geekom A8) over SSH.
#
# Manual, on-purpose: SSH in, git pull, docker compose up -d --build. No CI/CD,
# no registry push, no self-hosted runner — see LOCAL.md for why.
#
# Setup:
#   cp deploy.conf.example deploy.conf
#   edit deploy.conf with your host/user/path
#
# Usage:
#   ./deploy.sh              # git pull + docker compose up -d --build
#   ./deploy.sh --no-build   # just recreate containers (picks up .env/compose changes)
#   ./deploy.sh --no-pull    # rebuild/restart without touching the git checkout
#   ./deploy.sh --dry-run    # print the remote command without running it
#   ./deploy.sh --host 192.168.1.20 --user root --path /opt/photos-viewer

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
CONF_FILE="$SCRIPT_DIR/deploy.conf"

# Defaults, overridable by deploy.conf and then by CLI flags.
DEPLOY_HOST=""
DEPLOY_USER=""
DEPLOY_PATH=""
DEPLOY_PORT="22"
HEALTH_URL=""

if [[ -f "$CONF_FILE" ]]; then
  # shellcheck source=deploy.conf.example
  source "$CONF_FILE"
fi

DO_PULL=1
DO_BUILD=1
DRY_RUN=0

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) DEPLOY_HOST="$2"; shift 2 ;;
    --user) DEPLOY_USER="$2"; shift 2 ;;
    --path) DEPLOY_PATH="$2"; shift 2 ;;
    --port) DEPLOY_PORT="$2"; shift 2 ;;
    --no-pull) DO_PULL=0; shift ;;
    --no-build) DO_BUILD=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$DEPLOY_HOST" || -z "$DEPLOY_USER" || -z "$DEPLOY_PATH" ]]; then
  echo "Missing host/user/path." >&2
  echo "Copy deploy.conf.example to deploy.conf and fill it in, or pass --host/--user/--path." >&2
  exit 1
fi

remote_cmd="set -euo pipefail; cd '$DEPLOY_PATH'"
if [[ "$DO_PULL" -eq 1 ]]; then
  remote_cmd+=" && git pull --ff-only"
fi
if [[ "$DO_BUILD" -eq 1 ]]; then
  remote_cmd+=" && docker compose up -d --build"
else
  remote_cmd+=" && docker compose up -d"
fi
# Drop dangling images left over from the rebuild; keeps the LXC's disk from
# creeping up over repeated deploys.
remote_cmd+=" && docker image prune -f"

echo "==> ${DEPLOY_USER}@${DEPLOY_HOST}:${DEPLOY_PORT} ${DEPLOY_PATH}"
echo "==> $remote_cmd"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "(dry run, not connecting)"
  exit 0
fi

ssh -p "$DEPLOY_PORT" "${DEPLOY_USER}@${DEPLOY_HOST}" "$remote_cmd"

if [[ -n "$HEALTH_URL" ]]; then
  echo "==> waiting for health check: $HEALTH_URL"
  for _ in $(seq 1 15); do
    if curl -fsS -o /dev/null "$HEALTH_URL"; then
      echo "==> healthy"
      exit 0
    fi
    sleep 2
  done
  echo "==> WARNING: health check never passed, check logs on the host" >&2
  exit 1
fi

echo "==> done"
