#!/usr/bin/env bash
# May MockTrade be deployed now? (V1.6, #289) Exit 0: yes. Exit 3: no, inside market hours
# (09:00-15:45 India time, Monday to Friday, except the holidays in MOCKTRADE_HOLIDAYS); the
# reason is printed. Exit 2: MOCKTRADE_HOLIDAYS isn't a list of dates.
#
# It runs on the server itself, not in the app's image, so it works whichever version is
# running (even one from before this rule existed). DEPLOY_NOW (epoch seconds) is for tests.
set -euo pipefail
now=${DEPLOY_NOW:-$(date +%s)}
ist() { TZ=Asia/Kolkata date -d "@$now" "$@"; }
day=$(ist +%F)
IFS=',' read -r -a holidays <<<"${MOCKTRADE_HOLIDAYS:-}"
for h in "${holidays[@]}"; do
  h=${h// /}
  [ -z "$h" ] && continue
  if ! [[ $h =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || ! date -d "$h" +%F >/dev/null 2>&1; then
    echo "MOCKTRADE_HOLIDAYS is not a list of dates: $h" >&2
    exit 2
  fi
  if [ "$h" = "$day" ]; then
    echo "outside market hours ($day is a holiday): a deploy may go ahead"
    exit 0
  fi
done
if [ "$(ist +%u)" -ge 6 ]; then
  echo "outside market hours (weekend): a deploy may go ahead"
  exit 0
fi
hm=$((10#$(ist +%H%M)))
if [ "$hm" -ge 900 ] && [ "$hm" -lt 1545 ]; then
  echo "no deploy now: $(ist '+%a %d %b %H:%M') India time is inside market hours (09:00-15:45 on trading days)" >&2
  exit 3
fi
echo "outside market hours: a deploy may go ahead"
