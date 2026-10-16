#!/usr/bin/env bash
# Go back to the version that ran before the last deploy (V1.6, #289; docs/runbooks/release.md).
#
#   bash deploy/rollback.sh                 the previous image and code; the data as it is now
#   bash deploy/rollback.sh --restore-data  also the data as it was just before the deploy
#
# The plain rollback is enough when the new version changed no data. Use --restore-data when
# its release notes name a migration that can't run backwards. Note that this loses what
# happened since the deploy: the audit trail of those hours stays in the folder moved aside.
set -euo pipefail
# it checks out older code, which may not have this file: run from a copy (in the checkout)
if [ -z "${MOCKTRADE_ROLLBACK_COPY:-}" ]; then
  cd "$(dirname "$0")/.."
  copy=$(mktemp)
  cp "$0" "$copy"
  MOCKTRADE_ROLLBACK_COPY=1 exec bash "$copy" "$@"
fi
cd "$(git rev-parse --show-toplevel)"
COMPOSE=${COMPOSE:-deploy/compose.yaml}
DEPLOY_LOG=${DEPLOY_LOG:-/var/log/mocktrade-deploy.log}
SERVICES=${SERVICES:-app caddy}
dc() { docker compose -f "$COMPOSE" "$@"; }
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$DEPLOY_LOG"; }
who=${SUDO_USER:-${USER:-$(id -un)}}

[ -f deploy/.previous ] || { echo "nothing to roll back to (no deploy/.previous)" >&2; exit 2; }
ref=$(sed -n 's/^ref=//p' deploy/.previous)
backup=$(sed -n 's/^backup=//p' deploy/.previous)
from=$(sed -n 's/^tag=//p' deploy/.previous)

docker image inspect mocktrade:previous >/dev/null
git -c advice.detachedHead=false checkout --quiet "$ref"
docker image tag mocktrade:previous mocktrade:latest
if [ "${1:-}" = "--restore-data" ]; then
  dc stop app
  # the backup is inside the data volume: what is there now is moved into
  # /data/.before-restore-<time>/ (kept, never deleted), then the backup is copied in
  dc run --rm --no-deps -T app python -m mocktrade.tools.restore \
    --from "$backup" --to /data --replace
  log "rollback from $from by $who: data restored from $backup"
fi
# shellcheck disable=SC2086  # SERVICES is a list
dc up -d --no-build $SERVICES
for _ in $(seq 1 60); do
  state=$(docker inspect -f '{{.State.Health.Status}}' "$(dc ps -q app)" 2>/dev/null || echo starting)
  [ "$state" = healthy ] && break
  sleep 2
done
got=$(dc exec -T app python -c "import json,urllib.request as u; print(json.load(u.urlopen('http://127.0.0.1:8000/api/health'))['version'])" 2>/dev/null || echo none)
log "rollback from $from by $who: now $(git describe --tags --always "$ref"), version $got, $state"
[ "$state" = healthy ]
