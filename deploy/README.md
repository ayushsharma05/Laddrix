# Deploy

MockTrade runs as two containers (plus two optional ones for monitoring):

```
browser ──https──► caddy (ports 80/443, certificates) ──► app (port 8000, private network)
                                                            └─ data volume: users.db, events.db, broker tokens
```

| File | What it is |
|---|---|
| `../Dockerfile` | The image: the screens built with Node, then the Python server. It runs as a non-root user. |
| `compose.yaml` | The app and Caddy. The app has a read-only filesystem, no capabilities and a private network, and keeps its data in a volume. |
| `Caddyfile` | HTTPS for `MOCKTRADE_DOMAIN`. Stream messages pass through unbuffered. |
| `.env.example` | The settings to copy into `deploy/.env`. Never commit `deploy/.env`. |
| `smoke.sh` | Checks a built image: it starts locked down, becomes healthy (ready), answers, logs JSON, and stops with exit code 0. CI runs it. |
| `monitoring/` | Optional Prometheus and Grafana with the MockTrade dashboard (`--profile monitoring`). See `docs/runbooks/monitoring.md`. |
| `deploy.sh`, `rollback.sh`, `window.sh` | Deploy a tagged release outside market hours, with a backup first and an automatic rollback if it isn't healthy; go back by hand. See `docs/runbooks/release.md`. |

The server entry point is `python -m mocktrade.tools.serve`. Its settings are listed at the
top of `src/mocktrade/tools/serve.py`. Run `serve --check` to validate them without starting.

## Try it on your machine (paper trading, simulated prices)

```bash
cp deploy/.env.example deploy/.env
#   MOCKTRADE_DOMAIN=localhost
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml run --rm app python -m mocktrade.tools.users new-key
#   put the printed MOCKTRADE_SECRET_KEY=... line into deploy/.env
docker compose -f deploy/compose.yaml up -d

# a user (prints an authenticator link once) and a paper account
docker compose -f deploy/compose.yaml run --rm -it app python -m mocktrade.tools.users add-user ayush "Ayush" --roles admin,trader
docker compose -f deploy/compose.yaml run --rm app python -m mocktrade.tools.users add-account acc-paper paper PAPER1 --owner ayush
docker compose -f deploy/compose.yaml restart app     # accounts are started when the server starts
```

Open <https://localhost>. For `localhost`, Caddy uses its own local certificate, so the
browser warns once.

## Live Kite (`MOCKTRADE_PRICES=kite`)

1. In Kite's developer console, set the app's redirect URL to `https://<your domain>/kite/callback`.
   Register the server's static IP (orders are accepted only from it).
2. In `deploy/.env`, set `MOCKTRADE_PRICES=kite`, `KITE_API_KEY` and `KITE_API_SECRET`.
3. Add the account: `... users add-account acc-ayush kite AB1234 --owner ayush`. The third
   value is the Zerodha client id. Then restart the app.
4. Every morning, the account holder clicks **Log in to Kite** on the account strip and logs in
   on Zerodha's own page. MockTrade never sees the Zerodha password. Prices, orders and order
   updates start at once, with no restart. The login lasts until 6 AM the next day; after that
   the strip asks again and new orders are refused until then.

The first login of the day downloads Kite's instrument list. It is cached in the data volume,
so contracts can be searched after a restart even before that day's login.

## Every day

| Task | Command |
|---|---|
| Logs | `docker compose -f deploy/compose.yaml logs -f app` (JSON lines: search them with `jq`, see `docs/runbooks/monitoring.md`) |
| Status | `docker compose -f deploy/compose.yaml ps` (the app shows `healthy` when it can trade); which part and why: Admin → Status |
| Update | `git pull && docker compose -f deploy/compose.yaml up -d --build` |
| Stop | `docker compose -f deploy/compose.yaml stop` |
| Back up | `docker run --rm -v mocktrade_data:/data -v "$PWD":/out alpine tar czf /out/mocktrade-data.tgz -C /data .` |

Stopping waits for open requests to finish, then flushes the audit trail. The app exits with
code 0 and logs `stopped cleanly`.

## What is not here yet

- A check with a real Zerodha account (only Ayush can run it) is in the runbook (#155).
- The Mumbai server, static IP and hardening checklist: #153. Latency and load measurements:
  #154. Morning routine, alerts and runbooks: #155.
