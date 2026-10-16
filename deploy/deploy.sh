#!/usr/bin/env bash
# Deploy a tagged version of MockTrade on the server (V1.6, #289; docs/runbooks/release.md).
#
#   deploy/deploy.sh v1.0.0                              outside market hours
#   deploy/deploy.sh v1.0.1 --emergency "reason"         inside them: logged with who and why
#
# 1. the tag exists and the checkout has no local changes
# 2. not in market hours (09:00-15:45 India time on trading days), unless --emergency
# 3. a backup of all state, taken now (the way back if the new version changes data)
# 4. the running image is kept as mocktrade:previous, and the way back is written down
# 5. check out the tag, build it (stamped with its commit), start it
# 6. healthy, and /api/health says the tag's version; otherwise roll back at once
#
# Settings (for rehearsals): COMPOSE (default deploy/compose.yaml), ENV_FILE (deploy/.env),
# DEPLOY_LOG (default /var/log/mocktrade-deploy.log), SERVICES (default "app caddy").
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE=${COMPOSE:-deploy/compose.yaml}
DEPLOY_LOG=${DEPLOY_LOG:-/var/log/mocktrade-deploy.log}
SERVICES=${SERVICES:-app caddy}
dc() { docker compose -f "$COMPOSE" "$@"; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$DEPLOY_LOG"; }
who=${SUDO_USER:-${USER:-$(id -un)}}

tag=${1:?usage: deploy/deploy.sh <tag> [--emergency "reason"]}
emergency=""
if [ "${2:-}" = "--emergency" ]; then
  emergency=${3:?--emergency needs a reason}
fi

# 1. the tag, and a clean checkout
git fetch --tags --quiet 2>/dev/null || true
git rev-parse --verify --quiet "$tag^{commit}" >/dev/null || { echo "no such tag: $tag" >&2; exit 2; }
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "the checkout has local changes: commit or remove them first" >&2
  exit 2
fi

# 2. the market-hours window (on this server, whichever version is running)
ENV_FILE=${ENV_FILE:-deploy/.env}
holidays=$(sed -n 's/^MOCKTRADE_HOLIDAYS=//p' "$ENV_FILE" 2>/dev/null | tail -1)
if ! MOCKTRADE_HOLIDAYS="$holidays" deploy/window.sh; then
  if [ -z "$emergency" ]; then
    echo "refused: deploy outside market hours, or pass --emergency \"reason\"" >&2
    exit 3
  fi
  log "EMERGENCY deploy of $tag in market hours by $who: $emergency"
fi

# 3. a backup now, inside the running app
backup=$(dc exec -T app python -m mocktrade.tools.backup --to /data/backups --keep 14 | sed -n 's/^backup OK: \([^ ]*\).*/\1/p')
[ -n "$backup" ] || { echo "the backup failed: not deploying" >&2; exit 4; }

# 4. the way back
previous=$(git rev-parse HEAD)
docker image tag mocktrade:latest mocktrade:previous
printf 'ref=%s\nbackup=%s\ntag=%s\n' "$previous" "$backup" "$tag" >deploy/.previous
log "deploy $tag by $who: from $(git describe --tags --always "$previous"), backup $backup"

# 5. the new version
git -c advice.detachedHead=false checkout --quiet "$tag"
dc build --build-arg MOCKTRADE_BUILD="$(git rev-parse --short HEAD)" app
# shellcheck disable=SC2086  # SERVICES is a list
dc up -d $SERVICES

# 6. healthy, and the right version
want=${tag#v}
for _ in $(seq 1 60); do
  state=$(docker inspect -f '{{.State.Health.Status}}' "$(dc ps -q app)" 2>/dev/null || echo starting)
  [ "$state" = healthy ] && break
  sleep 2
done
got=$(dc exec -T app python -c "import json,urllib.request as u; print(json.load(u.urlopen('http://127.0.0.1:8000/api/health'))['version'])" 2>/dev/null || echo none)
if [ "$state" != healthy ] || [ "$got" != "$want" ]; then
  log "deploy $tag FAILED (health $state, version $got): rolling back"
  deploy/rollback.sh
  exit 5
fi
log "deploy $tag OK: version $got, healthy"
