# Data: what we hold, how it is protected, how long it is kept (#367)

Part of the security gate (#279, [security.md](security.md)). Every piece of data Laddrix
holds is listed here with its class, where it lives, who can read it, and how long it is kept.

## 1. Classes

| Class | Meaning | Examples | Rules |
| --- | --- | --- | --- |
| **Public** | anyone may know it | contract names, exchange prices shown on screens, this documentation | none |
| **Internal** | the firm's own business | orders, fills, positions, P&L, risk limits, reports, alerts, the audit trail, backtests | logged in and allowed for that account only (#270); in backups (owner-only files) |
| **Personal** | about one person | name, user id, roles, login times, sessions (browser, IP address), settings, layouts, their webhook address | as Internal, plus: the person can have it exported, and erased (§4) |
| **Secret** | gives access to something | passwords, authenticator secrets, session cookies, `LADDRIX_SECRET_KEY`, the broker's API secret and daily tokens, webhook signing keys, one-time passwords | never in a log (§2), never in git (`test_security_surface.py`, gitleaks); stored hashed (passwords: argon2id; sessions: SHA-256) or encrypted (authenticator secrets: the secret key), or kept out of the data folder entirely (`.env`, password manager) |

## 2. Logs never hold a secret

- `laddrix/logsafe.py` filters every log line the server writes, including the web server's
  access log. That log would otherwise show the broker login's `request_token` in a URL.
- It hides:
  - secret-named parameters and JSON fields;
  - `Authorization` headers;
  - encrypted values;
  - authenticator links;
  - the path of any full web address, because a webhook's address is often its secret.
- The HTTP client's own request log is turned down to warnings, since it prints full URLs.
- The objects that hold secrets leave them out when printed: the broker settings (API secret,
  proxy address), the broker session (access token) and the server settings (secret key).
- **Checked by** `tests/test_log_redaction.py`. It runs the flows that touch secrets with every
  logger at DEBUG and the production log setup, then searches every line, exceptions included,
  for each secret. It also asserts our own code never needed the filter: the filter is a safety
  net, not the rule.

## 3. How long each kind is kept

| What | Where | Kept | Why |
| --- | --- | --- | --- |
| Audit trail (every order event, fill, STOP, admin change, login) | `events.db` | **at least 5 years; never deleted by Laddrix** | SEBI requires stock brokers to preserve their books and records for at least five years, and longer while any investigation or case about them is open (SEBI (Stock Brokers) Regulations, reg. 18; SEBI circular MRD/DoP/SE/Cir-21/2009). Laddrix is not the broker, but the firm using it must be able to show what it did. **The firm's compliance officer confirms the period** before going live. |
| End-of-day reports | `reports/` | as the audit trail | they are built from it, and are what an auditor reads first |
| Risk limits, MTM rules, alerts, spreads, users and accounts | `users.db`, `rules/`, `alerts/`, `spreads/` | while in use | current configuration; every change is in the audit trail |
| Sessions | `users.db` | 2 h idle, 12 h at most; ended by logout, password change, disabling, revoke | access, not records |
| Backups | the volume / `/var/backups/laddrix` | 14 days / 30 days | recovery, not archiving: for older history, the audit trail itself is kept |
| Broker tokens | `kite_token_*.json` | until 6 AM the next day (they expire) | never backed up |
| Recorded ticks, imported candles, backtests | `ticks/`, `candles/`, `backtests/` | until someone deletes them | research data, not records |

**Archiving the audit trail:** `events.db` grows by roughly what a day's orders take. When it
grows too large, copy it whole (a backup does exactly that), check the copy with
`python -m laddrix.tools.audit verify --data <copy>`, and keep the copy for the period above.
Laddrix itself never deletes from it.

## 4. One person's data

- **Export:** `python -m laddrix.tools.users export-user <id> > <id>.json`.
  - It contains their profile, their accounts (as owner or trader), settings, layouts and
    sessions, their alerts and webhook address, their spread definitions, their backtests,
    and every audit entry that names them.
  - It never contains a secret: the password hash, the authenticator secret and the webhook's
    signing key are named as held, not given.
- **Erase:** `python -m laddrix.tools.users erase-user <id> --confirm <id>`, with the server
  stopped.
  - **Refused** if they own an account (give it another owner first), if they are the last
    admin, or while a server holds the data folder.
  - **Removes:** their alerts and webhook, their spreads, their backtests, their sessions, settings and
    layouts, and them from every account's traders. Their name, password and authenticator
    are replaced, and they can't log in.
  - **Kept:** their id and their entries in the audit trail. The trail must be kept (§3) and
    must stay whole (§5); an erased user is just an id in it.
  - Backups made before the erase still hold them until those backups age out (14 or 30 days).

## 5. The audit trail's protection

**Who may read it:**
- the account's own traders, for their account's orders (the audit screen);
- risk and admin, all of it (`/api/accounts/{id}/audit-export`, #291);
- the server's operator, the file itself.

**Who may change or delete it:** no one, through Laddrix. The writer only ever appends, and
no endpoint or tool deletes or edits a row. The operator (root on the server) can, by editing
the file. That is what tamper evidence is for.

**Tamper evidence (hash chain):**
- **How it works:** each row stores a `chain`, the SHA-256 of that row together with the chain
  of the row before it. `python -m laddrix.tools.audit verify` recomputes them all.
- **What it catches:**
  - a row that was **changed**;
  - a row **removed** from the middle;
  - a row **slipped in** without a chain.
- **What it can't catch alone:**
  - someone with the file and this code recomputing the **whole chain** after an edit;
  - rows **cut from the end**.
- **How backups close that gap:**
  - each backup's manifest records the head (the last row's number and chain) at that moment;
  - `verify --against <backup>/manifest.json` checks that the head is still in the log,
    unchanged, so a rewrite or a cut before that point is caught;
  - backups are copied off the machine, so the head is somewhere the server's operator alone
    can't rewrite.
- **What it never does:** prevent anything. It is evidence after the fact.
- **Not covered:** rows written before #367 have no chain. They are counted and reported as
  "from before chaining", not checked.

## 6. No production data in development

- Development, tests and demos use the paper broker, the market simulator and the demo user
  (`data/demo/`, its own throwaway key). Nothing in the repository reads a production data
  folder.
- **Never** copy a production `users.db`, `events.db`, backup or `.env` to a development
  machine, a test, a screenshot or an issue. Production data holds people's personal data and
  the firm's trading.
- A production problem is reproduced with made-up data that has the same shape. The backup
  drill restores into a fresh, isolated folder on the server itself.
- Screenshots in `context/images/` come from the demo only. The project rule on the employer's
  details applies too: no names, internal systems or screens from any employer.
