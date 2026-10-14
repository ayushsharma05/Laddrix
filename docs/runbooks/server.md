# Server: Mumbai, static IP, hardened

Checklist for putting MockTrade on its own server. Steps marked **(Ayush)** need your AWS,
domain or Zerodha login. Everything else is scripted. Tick each box as you go.

Why Mumbai: the NSE and BSE matching engines and Kite's servers are in Mumbai. A server there
is a few milliseconds from Kite, while a laptop at home is tens of milliseconds and on a
changing IP. Kite accepts orders **only from registered static IPs** (one primary and one
backup).

## 1. Server (Ayush, about 15 minutes)

- [ ] AWS Lightsail → Create instance → region **Mumbai (ap-south-1)**, **Linux · Ubuntu 24.04 LTS**,
      plan **2 GB RAM**. Name it `mocktrade-1`. Upload your SSH public key (or download the
      Lightsail key) when asked.
- [ ] Networking → **Create static IP** → attach it to `mocktrade-1`. Note the IP.
- [ ] Networking → IPv4 firewall:

      | Rule | Who can reach it |
      |---|---|
      | SSH 22 | **your home IP only** ("Restrict to IP address") |
      | HTTP 80 | everyone (Caddy needs it for certificates and the redirect to HTTPS) |
      | HTTPS 443 | everyone (TCP, and UDP for HTTP/3) |

- [ ] Snapshots → enable **automatic daily snapshots**. This keeps a whole-machine copy off the
      server's own disk.
- [ ] DNS: an **A record** for your trading name (for example `trade.<your-domain>.in`) points at the
      static IP.

## 2. Hardening (scripted, about 5 minutes)

```bash
ssh ubuntu@<static-ip>
sudo git clone https://github.com/ayushsharma05/MockTrade.git /opt/mocktrade
sudo chown -R ubuntu: /opt/mocktrade
sudo /opt/mocktrade/deploy/server-setup.sh
exit   # log in again so the docker group applies
```

`deploy/server-setup.sh` can be run again safely. It does the following:

| Step | Why |
|---|---|
| Installs security updates, and keeps installing them automatically | most break-ins use old, known holes |
| Installs Docker and Compose | the app and Caddy run in containers (`deploy/compose.yaml`) |
| Firewall (ufw): only 22, 80 and 443 in; fail2ban | two layers with the Lightsail firewall; repeated SSH guessing is banned |
| SSH: keys only, no root login | passwords can be guessed. It refuses to change SSH if you have no key yet, so you can't lock yourself out |
| Keeps the clock in sync | sign-in codes are time-based; Kite logins expire at 6 AM |
| Adds 2 GB swap | building the image on a 2 GB server needs it |
| Runs a backup Mon–Fri at 21:45 India time | `tools/backup.py`: consistent copies while running; 14 days kept in the volume, 30 days in `/var/backups/mocktrade` |

- [ ] The script ends with `done`, and `ssh: keys only` appears (or you add a key and run it again).

## 3. MockTrade (about 10 minutes)

```bash
cd /opt/mocktrade
cp deploy/.env.example deploy/.env && chmod 600 deploy/.env
nano deploy/.env               # MOCKTRADE_DOMAIN=trade.<your-domain>.in
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml run --rm app python -m mocktrade.tools.users new-key
nano deploy/.env               # paste the MOCKTRADE_SECRET_KEY=... line
docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml run --rm -it app python -m mocktrade.tools.users add-user ayush "Ayush" --roles admin,trader
```

- [ ] `https://trade.<your-domain>.in` shows the sign-in page with a valid certificate.
- [ ] You can sign in with the authenticator code; a paper account trades
      (`users add-account acc-paper paper PAPER1 --owner ayush`, then `restart app`).

## 4. Kite (Ayush)

- [ ] developers.kite.trade → your app → **Redirect URL** `https://trade.<your-domain>.in/kite/callback`.
- [ ] Same page → **IP whitelist**: the static IP (primary). Add the laptop only if you want
      a backup IP; otherwise leave it out.
- [ ] `deploy/.env`: `MOCKTRADE_PRICES=kite`, `KITE_API_KEY`, `KITE_API_SECRET`. Then add your
      account: `users add-account acc-ayush kite <ZERODHA-ID> --owner ayush` and run
      `docker compose -f deploy/compose.yaml up -d` (restart).
- [ ] On the screen, pick `acc-ayush` → **Log in to Kite** → Zerodha's page → you're back and the
      strip turns green (LIVE · KITE). Prices stream.

## 5. Done when: the IP check (Ayush, outside market hours)

An after-market order (AMO) placed from the server is accepted. The same check from the
laptop is refused, which shows the IP whitelist is doing its job.

```bash
# on the server, after today's Kite login from the screen
docker compose -f deploy/compose.yaml exec -it -e KITE_TOKEN_FILE=/data/kite_token_acc-ayush.json \
  app python -m mocktrade.tools.amo_check NSE:SBIN 1 500.00
```

Use a price far from the market (it is cancelled straight away, and must never fill even if
the cancel failed). Then check the Kite app.

- [ ] Server: `placed: Kite order …`, `cancel sent`, `final status: CANCELLED`.
- [ ] Laptop: the same command is refused by Kite (IP not allowed).

## Restore from a backup

```bash
cd /opt/mocktrade
docker compose -f deploy/compose.yaml stop app
docker compose -f deploy/compose.yaml run --rm --no-deps \
  -v /var/backups/mocktrade/<YYYYMMDDTHHMMSSZ>:/restore:ro app \
  sh -c 'rm -f /data/*.db-wal /data/*.db-shm && cp /restore/*.db /data/'
docker compose -f deploy/compose.yaml start app
```

This runs as the app's own user, so the files keep the right owner. It removes the old
`-wal`/`-shm` files first: a stale one would be replayed onto the restored database.

The same `MOCKTRADE_SECRET_KEY` is needed. Without it, users must set up their authenticator
again. Keep a copy of `deploy/.env` in your password manager.

## Measure speed on this server (#154)

The numbers in `docs/design/performance.md` were taken on a shared cloud VM. Take them again
here, outside market hours, and replace the table:

```bash
cd /opt/mocktrade
docker compose -f deploy/compose.yaml run --rm --no-deps app python -m mocktrade.tools.loadtest
docker compose -f deploy/compose.yaml run --rm --no-deps app python -m mocktrade.tools.loadtest \
  --instruments 3000 --rate 1 --clients 10
```

The test starts its own server and a fake Kite inside the container. It never touches the
real server or Kite. While trading, `GET /api/metrics` (admin) shows the same measures live.
