#!/usr/bin/env bash
# Smoke test of a built image: starts it locked down (read-only, no capabilities), checks
# health and the screens, then stops it and expects a clean exit 0.
#   deploy/smoke.sh [image]        (default mocktrade:ci; used by CI)
set -euo pipefail
IMAGE=${1:-mocktrade:ci}
key=$(docker run --rm "$IMAGE" python -m mocktrade.tools.users new-key | head -1 | cut -d= -f2-)
docker rm -f mt >/dev/null 2>&1 || true
docker run -d --name mt --read-only --tmpfs /tmp --cap-drop ALL \
  -e MOCKTRADE_SECRET_KEY="$key" -e MOCKTRADE_ORIGIN=https://localhost -p 8000:8000 "$IMAGE"
for i in $(seq 1 30); do [ "$(docker inspect -f '{{.State.Health.Status}}' mt)" = healthy ] && break; sleep 1; done
docker inspect -f 'health={{.State.Health.Status}}' mt
curl -fsS --noproxy '*' localhost:8000/api/health; echo
curl -fsS --noproxy '*' localhost:8000/ | grep -q '<div id="root">' && echo "screens served"
docker stop mt >/dev/null
test "$(docker inspect -f '{{.State.ExitCode}}' mt)" = 0 && echo "exit 0"
docker logs mt 2>&1 | grep -q "stopped cleanly" && echo "stopped cleanly"
docker rm mt >/dev/null
