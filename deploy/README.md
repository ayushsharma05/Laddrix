# Deploy

MockTrade runs as two containers:

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
| `smoke.sh` | Checks a built image: it starts locked down, answers, and stops with exit code 0. CI runs it. |

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

## Every day

| Task | Command |
|---|---|
| Logs | `docker compose -f deploy/compose.yaml logs -f app` |
| Status | `docker compose -f deploy/compose.yaml ps` (the app shows `healthy`) |
| Update | `git pull && docker compose -f deploy/compose.yaml up -d --build` |
| Stop | `docker compose -f deploy/compose.yaml stop` |
| Back up | `docker run --rm -v mocktrade_data:/data -v "$PWD":/out alpine tar czf /out/mocktrade-data.tgz -C /data .` |

Stopping waits for open requests to finish, then flushes the audit trail. The app exits with
code 0 and logs `stopped cleanly`.

## What is not here yet

- Live Kite prices and orders (`MOCKTRADE_PRICES=kite`): #183. Until then, kite accounts are
  listed as not running. They are never traded on paper without notice.
- The Mumbai server, static IP and hardening checklist: #153. Latency and load measurements:
  #154. Morning routine, alerts and runbooks: #155.
