#!/usr/bin/env bash
# Deploy photos-viewer to a Proxmox LXC via the PVE host.
#
# Mac tree → tar → scp → pct push → extract → compose up.
# CT is not SSH-reachable from this Mac and has no git checkout.
# Leaves the CT's .env alone; never `docker compose down -v`.
#
# Usage: ./deploy.sh [--no-sync] [--no-build] [--dry-run]
#        ./deploy.sh --host pve --ctid 100 --path /opt/photos-viewer
# Real host/CTID live in deploy.conf (gitignored); see deploy.conf.example.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
CONF_FILE="$SCRIPT_DIR/deploy.conf"
TAR=/tmp/photos-viewer.tgz
SSH=(ssh -o BatchMode=yes)
SCP=(scp -o BatchMode=yes)

# Keep in sync with .gitignore (plus tests — no reason to ship them to the CT).
TAR_EXCLUDES=(
  .git
  .env
  ._*
  .DS_Store
  data
  LOCAL.md
  '*.local.md'
  deploy.conf
  .venv
  venv
  __pycache__
  '*.pyc'
  '*.db'
  backend/tests
)

# Placeholders only — override via deploy.conf (gitignored) or CLI flags.
PVE_HOST=pve
CTID=100
DEPLOY_PATH=/opt/photos-viewer

if [[ -f "$CONF_FILE" ]]; then
  # shellcheck source=deploy.conf.example
  source "$CONF_FILE"
fi

DO_SYNC=1
DO_BUILD=1
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage:
  ./deploy.sh              sync tree + docker compose up -d --build --force-recreate --wait
  ./deploy.sh --no-build   sync files, then compose up -d --wait (no image rebuild)
  ./deploy.sh --no-sync    rebuild/restart without copying this tree
  ./deploy.sh --dry-run    print commands without running them
  ./deploy.sh --host pve --ctid 100 --path /opt/photos-viewer
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) PVE_HOST="$2"; shift 2 ;;
    --ctid) CTID="$2"; shift 2 ;;
    --path) DEPLOY_PATH="$2"; shift 2 ;;
    --no-sync) DO_SYNC=0; shift ;;
    --no-build) DO_BUILD=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if [[ -z "$PVE_HOST" || -z "$CTID" || -z "$DEPLOY_PATH" ]]; then
  echo "Missing PVE_HOST/CTID/DEPLOY_PATH." >&2
  exit 1
fi

# Live gates hash with HMAC-SHA256(SESSION_SECRET, code). A plain-SHA tokens.py
# or a missing scope.py ships as a silent lockout / full-library leak.
for f in backend/app/tokens.py backend/app/scope.py backend/app/auth.py \
         backend/app/main.py backend/app/config.py backend/cli.py; do
  if [[ ! -f "$SCRIPT_DIR/$f" ]]; then
    echo "missing $f; refusing to deploy" >&2
    exit 1
  fi
done
if ! grep -q 'hmac.new(' "$SCRIPT_DIR/backend/app/tokens.py"; then
  echo "tokens.py is not HMAC; refusing to deploy (would invalidate existing codes)" >&2
  exit 1
fi

compose="docker compose up -d"
if [[ "$DO_BUILD" -eq 1 ]]; then
  compose+=" --build --force-recreate"
fi
# Rebuild + healthcheck start_period can exceed compose's 60s --wait default.
compose+=" --wait --wait-timeout 120"

ct_script="set -euo pipefail"
if [[ "$DO_SYNC" -eq 1 ]]; then
  ct_script+="
find '$DEPLOY_PATH' -name '._*' -delete 2>/dev/null || true
tar xzf '$TAR' -C '$DEPLOY_PATH'
find '$DEPLOY_PATH' -name '._*' -delete 2>/dev/null || true"
fi
# prune is &&-chained so a failed cd/compose cannot look like success.
ct_script+="
cd '$DEPLOY_PATH' && $compose && docker image prune -f"

echo "==> ${PVE_HOST}  CT ${CTID}  ${DEPLOY_PATH}"
if [[ "$DO_SYNC" -eq 1 ]]; then
  echo "==> tar + scp ${TAR} -> ${PVE_HOST}:${TAR} && pct push ${CTID}"
fi
echo "==> pct exec ${CTID} -- bash -s"
echo "$ct_script"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "(dry run, not connecting)"
  exit 0
fi

trap 'rm -f "$TAR"' EXIT

if [[ "$DO_SYNC" -eq 1 ]]; then
  exclude_args=()
  for pat in "${TAR_EXCLUDES[@]}"; do
    exclude_args+=(--exclude="$pat")
  done
  COPYFILE_DISABLE=1 tar czf "$TAR" "${exclude_args[@]}" -C "$SCRIPT_DIR" .
  "${SCP[@]}" "$TAR" "${PVE_HOST}:${TAR}"
fi

# Same bytes as the dry-run print: pve runs pct push, then feeds ct_script to
# bash -s inside the CT. Quoted <<'CT' so the CT does not re-expand the script.
"${SSH[@]}" "$PVE_HOST" bash -s <<EOF
set -euo pipefail
$(
  if [[ "$DO_SYNC" -eq 1 ]]; then
    printf "pct push '%s' '%s' '%s'\n" "$CTID" "$TAR" "$TAR"
  fi
)
pct exec '$CTID' -- bash -s <<'CT'
$ct_script
CT
EOF

echo "==> done"
