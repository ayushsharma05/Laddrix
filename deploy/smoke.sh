#!/usr/bin/env bash
# Smoke test of a built image: starts it locked down (read-only, no capabilities), checks
# that it becomes healthy (= ready, #287), answers and serves the screens, logs JSON lines,
# then stops it and expects a clean exit 0.
#   deploy/smoke.sh [image]        (default mocktrade:ci; used by CI)
set -euo pipefail
IMAGE=${1:-mocktrade:ci}
# whole outputs are read before filtering: `cmd | head`/`grep -q` can stop reading early,
# and with pipefail the writer's "broken pipe" would fail the script at random
out=$(docker run --rm "$IMAGE" python -m mocktrade.tools.users new-key)
key=$(printf '%s\n' "$out" | sed -n '1s/^[^=]*=//p')
docker rm -f mt >/dev/null 2>&1 || true
docker run -d --name mt --read-only --tmpfs /tmp --cap-drop ALL \
  -e MOCKTRADE_SECRET_KEY="$key" -e MOCKTRADE_ORIGIN=https://localhost -p 8000:8000 "$IMAGE"
for _ in $(seq 1 30); do [ "$(docker inspect -f '{{.State.Health.Status}}' mt)" = healthy ] && break; sleep 1; done
docker inspect -f 'health={{.State.Health.Status}}' mt
curl -fsS --noproxy '*' localhost:8000/api/health; echo
curl -fsS --noproxy '*' localhost:8000/api/ready; echo
page=$(curl -fsS --noproxy '*' localhost:8000/)
grep -q '<div id="root">' <<<"$page" && echo "screens served"
docker stop mt >/dev/null
test "$(docker inspect -f '{{.State.ExitCode}}' mt)" = 0 && echo "exit 0"
logs=$(docker logs mt 2>&1)
grep -q '"msg":"stopped cleanly"' <<<"$logs" && echo "stopped cleanly (a JSON log line)"
docker rm mt >/dev/null
