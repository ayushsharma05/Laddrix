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

## What a backup holds (V1.3, #329)

The 21:45 cron job runs `python -m mocktrade.tools.backup`. One folder per run:

| In the backup | What it is |
|---|---|
| `users.db` | users, accounts, saved layouts, settings (keys, theme), risk limits. Not the logged-in sessions: after a restore everyone logs in again (#364) |
| `events.db` | the audit trail: orders, fills and positions are rebuilt from it at start |
| `rules/`, `reports/`, `alerts/` | MTM limits, end-of-day reports, market alerts (with each user's webhook) |
| `candles/`, `backtests/` | imported candles, backtest results |
| `ticks/` | recorded days: **only with `--with-ticks`** (large; losing them loses no trading state) |
| `manifest.json` | every file with its size and SHA-256: a restore checks them first |

**Never in a backup:** keep these in the password manager instead.
- `MOCKTRADE_SECRET_KEY` and `deploy/.env`: the key decrypts the authenticator secrets stored in `users.db`.
- The broker's API secret.
- The broker tokens (`kite_token_*.json`). They expire at 6 AM anyway: log in again after a restore.
- The daily instrument list. It is downloaded again.

Backups hold password hashes and users' webhook secrets, so they are readable by their owner only.

**Each backup also proves the audit trail (#367).** Its `manifest.json` records the audit trail's head (`audit_chain`). To check that nothing in the trail was changed or cut since a backup was made, run `python -m mocktrade.tools.audit verify --against /var/backups/mocktrade/<stamp>/manifest.json`. Do it monthly, and after any incident. A copy of the manifests kept off the machine is what makes this proof ([data.md](../design/data.md) §5).

**Nothing new is left out unnoticed (#272).** Every top-level entry in the data folder must be
either backed up or listed in `tools/backup.py` (`FOLDERS` for state, `REPRODUCIBLE` for what is
rebuilt or kept elsewhere).
- **Something else turns up** (a new feature's folder): the backup is still made, but it prints
  `backup made, but INCOMPLETE: <name> ...`, names it in the manifest (`not_covered`) and exits
  with 2. The nightly job's failure alert reaches you then, not on the day you need a restore.
- **Fix:** add it to the right list and release.

**How much you can lose, and how long it takes (RPO / RTO, measured 23 Oct 2026):**

| | |
| --- | --- |
| **RPO** (what a restore can lose) | **MockTrade's own state since the last backup.** Backups run Mon–Fri at 21:45, plus the cloud's daily snapshot, so up to a trading day: audit events, MTM limits, alerts, reports, users or settings changed since. **Positions and working orders are not lost:** at the broker they are the truth, and the restart rebuilds today's from the broker's lists before trading opens (recovery). A restored server never sends an order twice (tested). |
| **RTO** (how long) | **The tools take seconds:** a 217 MB audit trail took 2.4 s to back up and 1.7 s to restore with every check. **The rest is people, about 15 to 20 minutes:** stop the server; check the backup into an empty folder; restore; put back the secret key and `.env` from the password manager; start; log in to Kite; wait for the recovery band to clear (it reconciles with the broker before taking orders). Rehearse it: `context/scripts/drill-backup-restore.py` (19/19 on 23 Oct, `context/evidence/h-272-backup-restore-drill.txt`). |

**A backup from another MockTrade version** is refused by name (`backup format N, this
MockTrade restores format 1`): restore with the version that made it, then upgrade.

## One IP per account, or one shared (#372)

SEBI wants API orders to come from a static IP the broker has on file. Whether several traders
may share the server's IP, or each needs their own, is for Zerodha to confirm (#278). Either
way it is a setting:

| Setting (in `deploy/.env`) | Orders and the daily login leave |
| --- | --- |
| nothing (default), or `KITE_EGRESS_<ACCOUNT>=direct` | from the server's static IP (shared) |
| `KITE_EGRESS_<ACCOUNT>=http://user:pass@1.2.3.4:3128` | through that proxy: the account's own IP |
| `KITE_EGRESS=http://…` | the same proxy for every account without its own setting |

`<ACCOUNT>` is the account id in capitals with other characters as `_`: `acc-ayush` →
`KITE_EGRESS_ACC_AYUSH`.
- **Proxies:** `http://` and `https://` only. Any static-IP proxy service gives one.
- **Which IP to register:** register *that* IP in the account's Kite app (Profile → IP
  whitelist), not the server's.
- **Prices:** the price feed is not routed this way. Only order requests are checked against
  the IP.
- **Check:** after setting it, restart and place one small paper-safe test (the IP check in
  § 5) for that account.

## Restore from a backup

**1. Check the backup first, without touching anything.** Restore it into an empty, isolated folder. A damaged or incomplete backup is refused at this step, naming the file.

```bash
docker compose -f deploy/compose.yaml run --rm --no-deps \
  -v /var/backups/mocktrade/<YYYYMMDDTHHMMSSZ>:/restore:ro app \
  python -m mocktrade.tools.restore --from /restore --to /tmp/check
```

**2. Then for real, with the app stopped:**

```bash
cd /opt/mocktrade
docker compose -f deploy/compose.yaml stop app
docker compose -f deploy/compose.yaml run --rm --no-deps \
  -v /var/backups/mocktrade/<YYYYMMDDTHHMMSSZ>:/restore:ro app \
  python -m mocktrade.tools.restore --from /restore --to /data --replace
docker compose -f deploy/compose.yaml start app
```

- `--replace` first moves the current data into `/data/.before-restore-<time>/`, inside the volume (a volume's root can't be moved); nothing is deleted, and `backups/` stays where it is. Without it, a folder with data in it is refused.
- The restore runs as the app's own user, so the files keep the right owner.
- At the end the command lists what to put back by hand: the same `MOCKTRADE_SECRET_KEY` (without it, users must set up their authenticator again), `.env`, and a fresh Kite login.

**Practise it:** `context/scripts/drill-backup-restore.py` does all of this on the demo. Its last run passed 19/19; the output is in `context/evidence/v1-3-backup-restore-drill.txt`.
- **Backed up while the server ran:** a second user, a trade, a layout, a theme, an MTM limit and a market alert.
- **Restored into an isolated folder,** and started from there: everything was back, and every audit event was kept.
- **The only addition** was the restart's own recovery note.
- **A trade made after the backup** was not there, as it should not be.

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
