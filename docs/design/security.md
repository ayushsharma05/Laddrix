# Security: threat model and control map

Issue #279. It is a release gate, not a checklist that proves security. This page says:
- what MockTrade protects, from whom, and where the trust boundaries are;
- for each control, the **evidence** that it works (code and tests), or the **gap** and the
  child issue that will close it.

Reviewed 23 Oct 2026, against current `main`.

## 1. Deployment boundary (what is in scope today)

**Today: one firm, one self-hosted server.** One Docker host (Lightsail Mumbai) behind Caddy
(HTTPS), with one static IP that the broker accepts orders from. The firm's own users only:
admin, risk, traders. **Not multi-tenant:** admin and risk users see every account, by
design (`admin.md`, "Who sees what"). Offering MockTrade to other firms changes this whole
page. That decision is #278, and the work is #368.

| Who can read what, outside the app | |
| --- | --- |
| The server's operator (root on the host) | everything: memory, the data folder, backups, `.env`. Encryption at rest doesn't protect against this person. |
| Backups (owner-only files, copied off the machine) | password hashes (argon2id), users' webhook secrets, the audit trail. Never the secret key, `.env` or broker tokens. |
| A user of the screens or the desktop app | the screens' code (anything shipped to a browser can be read; minifying it protects nothing). No server secret is in it (tested). |

**Code secrecy:**
- **Hosted:** the trading logic stays on the server; a user only receives the screens.
- **Self-hosted by a customer:** that customer can read the backend code and its secrets.
  Promise code secrecy only for hosted use.

## 2. Assets, actors, trust boundaries

```
 browser / desktop ──HTTPS + cookie──► Caddy ──► API + stream (FastAPI) ──► OrderManager ─► broker (Kite | paper)
   (untrusted)          (TLS)            │          │  (trusted server)         │              (external)
                                         │          ├─► users.db (argon2, encrypted TOTP + secrets)
                                         │          ├─► events.db (audit trail) ─► backups ─► off-machine
                                         │          └─► data/ (rules, reports, alerts, …)
                                         └─ /kite/callback ◄── Kite login (state bound to user + account)
 operator (SSH, compose) ── .env / MOCKTRADE_SECRET_KEY ── password manager
```

- **Assets:** broker credentials and daily tokens; the secret key; user passwords and TOTP
  secrets; orders and positions (money); risk limits; the audit trail; reports; webhook
  secrets; the server code.
- **Actors:** anonymous Internet users; logged-in users acting maliciously; stolen sessions; an
  over-privileged admin; a compromised dependency or update; the operator's own mistakes.

## 3. Permission matrix (code: `domain/accounts.py`, checks in `server/app.py`)

| Action | trader (own / delegated accounts) | risk | admin |
| --- | --- | --- | --- |
| See orders and positions | own + delegated | all | all |
| Place, change, cancel, algos, GTTs | own + delegated (account on) | no | no (unless also trader) |
| Cancel, close, STOP an account (account off) | own + delegated | STOP any | STOP any |
| Firm-wide STOP / resume | no | STOP | STOP and resume |
| Users, accounts, limits | no | no | yes |
| Surveillance alerts, auditor's export | no | yes | yes |
| Adopt trades made outside MockTrade (#355) | no | yes | yes |
| Backtests | own | own | all |

Each row is enforced on the server for REST **and** the stream (#270). Hiding a button is never
the control.

## 4. Abuse cases, by priority

| # | Case | Today | Evidence / gap |
| --- | --- | --- | --- |
| 1 | Stolen session places orders | Sessions: httponly + SameSite=Strict + Secure cookie, 2 h idle / 12 h max, ended on logout, password change and disable; Origin checked on changes and on the stream | `test_server.py` (cookie, expiry, Origin); step-up for privileged changes: **#363** |
| 2 | Brute force or credential stuffing | argon2id; TOTP mandatory for everyone; 5 failures → 15 min lock; per-address limit | `test_server.py` (lockout, same answer for any wrong part) |
| 3 | Another user's account read or traded | default-deny per account, also on the stream; a missing account is the same answer as no access (404) | `test_server.py`, `test_ownership.py` (#270) |
| 4 | Broker login callback replayed or swapped | `state` single-use, bound to the session's user + account, 10 min; the Zerodha user id must match the account | `app.py /kite/callback`, `daily_login.py` |
| 5 | Order replayed with changed details | **Fixed here:** an order id reused for a different order is refused (409), never answered with the old one; an identical retry never places twice | `test_order_manager.py` |
| 6 | Risk limit raced, or action after permission removed | risk gate + throttle per account; durable orders (#265) | races and delayed side effects: **#366** |
| 7 | Secrets leaked via the screens, git or HTTP paths | screens and desktop sources scanned; `.env`, keys, tokens and databases never tracked; probes of 24 private paths (incl. path tricks) never serve a planted secret | `test_security_surface.py` (**new**), #269 |
| 8 | SSRF via webhooks | https only, private addresses refused | #271, `test_alerts*.py` |
| 9 | Key or credential compromised | — | rotation + drill: **#364** |
| 10 | Dependency or update compromised | `npm audit` in CI | Python audit, SBOM, pins, signing: **#365** |
| 11 | Logs leak secrets; retention | secrets kept out of backups | redaction tests, retention policy: **#367** |
| 12 | Storage, audit or feed outage | orders refused unless on disk first (#265); stale prices refuse risk-adding orders (#267); critical loops watched, new orders pause, cancels go (#273); STOP + Flatten work through it (#262) | those PRs' tests |

## 5. Mapped to OWASP ASVS 4.0.3 (selected chapters, target Level 2)

This is not certification: it only shows where each requirement area stands.

| ASVS chapter | Status |
| --- | --- |
| V2 Authentication | argon2id, TOTP for all, lockout; step-up for sensitive changes: #363 |
| V3 Session management | server-side sessions, rotation on login, timeouts, revocation; a user's own session list: #363 |
| V4 Access control | default-deny per account and on the stream (#270); delayed actions re-checked: #366 |
| V5 Validation | strict request models (`_Strict`, field limits); path checks (#269) |
| V6 Stored cryptography | Fernet (SecretBox) for TOTP and broker secrets; key rotation: #364 |
| V7 Errors and logging | audit trail of every order event and privileged action; redaction: #367 |
| V8 Data protection | owner-only backups; retention and deletion: #367 |
| V9 Communications | HTTPS via Caddy; HSTS and security headers (`test_server.py`) |
| V10 Malicious code | no user code runs on the server (algos are built-in, declarative settings) |
| V12 Files | path traversal fixed (#269); request and upload size limits: #274 |
| V13 API | CSRF by Origin + SameSite; no docs pages exposed |
| V14 Configuration | `.env` outside git; settings checked at start; supply chain: #365 |

## 6. Before a public real-money launch

- Every child issue above is closed or explicitly accepted. Their owner is Ayush and the
  acceptance goes in the decision log.
- An independent review and penetration test on staging with the paper broker only:
  **#369**.
- No critical or high finding open.

## 7. Review again when

- a new broker or API is added;
- algos change how they run (above all, any user-supplied strategy code: that needs a sandbox
  design first);
- a new role or a delegation model is added;
- the deployment changes (multi-tenant, another host, a desktop release);
- yearly in any case.
