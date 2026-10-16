#!/usr/bin/env bash
# Deploy-and-rollback rehearsal (V1.6, #289) with the real deploy/deploy.sh and rollback.sh, real
# Docker images and a real data volume, on this machine (a staging copy, never the server).
#
#   context/scripts/drill-deploy-rollback.sh <old-ref> <new-ref>
#     old-ref: what "production" runs first (e.g. origin/main)   new-ref: the release to deploy
#
# Needs Docker. It works in a throwaway clone with local-only tags (v0.0.1 = old, v<new
# version> = new, v9.9.9 = new but claiming a wrong version), and a copy of the compose file
# without Caddy (no ports opened) that builds on the host network. Steps, each PASS/FAIL:
#   1. "production" runs old, with a user in it
#   2. a deploy inside market hours is refused, and nothing changes
#   3. a deploy outside them: the new version runs, stamped with its commit; the data is kept
#   4. rollback: the old version runs again, with the data
#   5. a deploy whose version check fails rolls itself back
#   6. an emergency deploy in market hours goes ahead and is logged with its reason; then a
#      rollback with --restore-data brings back the data as it was before that deploy
# shellcheck disable=SC2016  # each check is quoted on purpose: `check` evaluates it later
set -uo pipefail
OLD=${1:?usage: drill-deploy-rollback.sh <old-ref> <new-ref>}
NEW=${2:?usage: drill-deploy-rollback.sh <old-ref> <new-ref>}
REPO=$(git rev-parse --show-toplevel)
WORK=$(mktemp -d)
export COMPOSE_PROJECT_NAME=mtrehearsal COMPOSE=deploy/rehearsal.compose.yaml SERVICES=app
export DEPLOY_LOG=$WORK/deploy.log
pass=0 fail=0
check() { if eval "$2"; then echo "PASS $1"; pass=$((pass + 1)); else echo "FAIL $1"; fail=$((fail + 1)); fi; }
dc() { docker compose -f "$COMPOSE" "$@"; }
deploy() { git show "v$version:deploy/deploy.sh" >"$WORK/deploy.sh" && bash "$WORK/deploy.sh" "$@"; }  # as the runbook says
health() { dc exec -T app python -c "import json,urllib.request as u; h=json.load(u.urlopen('http://127.0.0.1:8000/api/health')); print(h.get('version','(none)'), h.get('build','-'))" 2>/dev/null; }
users() { dc exec -T app python -c "import sqlite3; print(' '.join(r[0] for r in sqlite3.connect('/data/users.db').execute('select id from users order by id')))" 2>/dev/null; }
ist() { TZ=Asia/Kolkata date -d "$1" +%s; }
IN_HOURS=$(ist "2026-10-16 10:00")   # a Friday, market open
AFTER=$(ist "2026-10-16 18:00")

git clone -q "$REPO" "$WORK/repo" && cd "$WORK/repo" || exit 1
git -c advice.detachedHead=false checkout -q "$(cd "$REPO" && git rev-parse "$NEW")"
version=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' src/mocktrade/version.py)
git tag v0.0.1 "$(cd "$REPO" && git rev-parse "$OLD")"
git tag "v$version" HEAD
git tag v9.9.9 HEAD
# staging plumbing: no Caddy (no ports), builds on the host network; both untracked
python3 - <<'PY'
import re
s = open("deploy/compose.yaml").read()
s = s[: s.index("\n  caddy:")] + "\nvolumes:\n  data:\n"
s = s.replace("      dockerfile: Dockerfile\n", "      dockerfile: Dockerfile\n      network: host\n")
open("deploy/rehearsal.compose.yaml", "w").write(s)
PY
key=$(docker run --rm mocktrade:old-main python -m mocktrade.tools.users new-key | sed -n '1s/^[^=]*=//p')
printf 'MOCKTRADE_DOMAIN=localhost\nMOCKTRADE_SECRET_KEY=%s\nMOCKTRADE_PRICES=sim\nMOCKTRADE_HOLIDAYS=\n' "$key" >deploy/.env

echo "--- 1. production runs the old version"
git -c advice.detachedHead=false checkout -q v0.0.1
docker image tag mocktrade:old-main mocktrade:latest
dc up -d app >/dev/null 2>&1
for _ in $(seq 1 40); do [ "$(docker inspect -f '{{.State.Health.Status}}' "$(dc ps -q app)")" = healthy ] && break; sleep 2; done
printf 'a-long-password-1\na-long-password-1\n' |
  dc exec -T app python -m mocktrade.tools.users add-user ayush Ayush --roles admin,trader >/dev/null 2>&1
dc restart app >/dev/null 2>&1; sleep 6
before=$(health)
check "old version running ($before), user: $(users)" '[ "$(users)" = ayush ] && [ "${before%% *}" = "(none)" ]'

echo "--- 2. inside market hours: refused"
DEPLOY_NOW=$IN_HOURS deploy "v$version" >"$WORK/refused.out" 2>&1; code=$?
check "deploy at Fri 10:00 IST refused (exit $code): $(tail -1 "$WORK/refused.out")" '[ $code = 3 ]'
check "nothing changed: still $(health)" '[ "$(health)" = "$before" ] && [ "$(git describe --tags)" = v0.0.1 ]'

echo "--- 3. outside market hours: deployed"
DEPLOY_NOW=$AFTER deploy "v$version" >"$WORK/deploy.out" 2>&1; code=$?
now=$(health)
check "deploy v$version OK (exit $code): health says $now" '[ $code = 0 ] && [ "${now%% *}" = "$version" ] && [ "${now##* }" = "$(git rev-parse --short "v$version")" ]'
check "data kept: $(users)" '[ "$(users)" = ayush ]'
check "a backup was taken first: $(sed -n "s/^backup=//p" deploy/.previous)" 'grep -q "^backup=/data/backups/" deploy/.previous'

echo "--- 4. rollback"
bash deploy/rollback.sh >"$WORK/rollback.out" 2>&1; code=$?
check "rolled back (exit $code): $(health), code at $(git describe --tags --always)" '[ $code = 0 ] && [ "$(health)" = "$before" ] && [ "$(git describe --tags)" = v0.0.1 ]'
check "data kept: $(users)" '[ "$(users)" = ayush ]'

echo "--- 5. a deploy whose version check fails rolls itself back"
DEPLOY_NOW=$AFTER deploy v9.9.9 >"$WORK/bad.out" 2>&1; code=$?
check "v9.9.9 refused by its own check (exit $code): $(grep -o 'FAILED.*' "$WORK/bad.out" | head -1)" '[ $code = 5 ]'
check "and rolled back: $(health)" '[ "$(health)" = "$before" ]'

echo "--- 6. emergency deploy, then rollback with the data as it was"
DEPLOY_NOW=$IN_HOURS deploy "v$version" --emergency "rehearsal: a fix that can't wait" >"$WORK/emergency.out" 2>&1; code=$?
check "emergency deploy went ahead (exit $code): $(health)" '[ $code = 0 ] && [ "$(health | cut -d" " -f1)" = "$version" ]'
check "and was logged with who and why" 'grep -q "EMERGENCY deploy of v$version in market hours by .*: rehearsal: a fix that can.t wait" "$DEPLOY_LOG"'
printf 'a-long-password-2\na-long-password-2\n' |
  dc exec -T app python -m mocktrade.tools.users add-user late "After the deploy" --roles trader >/dev/null 2>&1
check "a user added after the deploy: $(users)" '[ "$(users)" = "ayush late" ]'
bash deploy/rollback.sh --restore-data >"$WORK/restore.out" 2>&1; code=$?
check "rollback --restore-data (exit $code): $(health), users: $(users)" '[ $code = 0 ] && [ "$(health)" = "$before" ] && [ "$(users)" = ayush ]'
aside=$(dc exec -T app sh -c 'ls -d /data/.before-restore-*' 2>/dev/null)
check "what was replaced is kept, not deleted: $aside" '[ -n "$aside" ]'

echo "--- the deploy log"
cat "$DEPLOY_LOG"
dc down -v >/dev/null 2>&1
echo
echo "$pass/$((pass + fail)) checks passed"
[ "$fail" = 0 ]
