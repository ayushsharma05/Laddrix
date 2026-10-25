# Chapter 8 · Data, tests and running it

[Guide home](README.md) · previous: [Chapter 7](07-code-map.md) · next: [Chapter 9 · How we work, what's next, your steps](09-work-next-your-steps.md)

---

## 8.1 Where things are kept

```
 data/                      (data/demo/ for the demo; /data on the server)
  ├── users.db              people: names, password hashes, authenticator secrets (encrypted),
  │                         accounts, settings
  ├── events.db             THE LEDGER: every order event, fill, STOP, note, in order.
  │                         After a crash, Laddrix rebuilds itself from this.
  ├── rules/                your MTM limits, and what they did today
  ├── ticks/<day>/          recorded prices, for replay and research
  ├── reports/ alerts/ candles/ backtests/
  └── backups/<time>/       copies of all of the above, with a list of checksums
```

- **A database** (SQLite) is a file that holds tables, like a spreadsheet that programs can
  search quickly.
- **A password hash** is a one-way scramble of a password. Laddrix can check a password
  against it, but can never get the password back from it, so even a stolen `users.db` doesn't
  reveal passwords.

**Never committed to GitHub:** the data folder, `.env` files, keys, tokens.

## 8.2 Backups and restore

```
 backup:   copy everything ──► backups/<time>/  +  manifest.json
           (a "fingerprint" of each file: change one byte and the fingerprint changes)

 restore:  check every fingerprint ──── one wrong? ───► stop, change NOTHING
               │ all good
               ▼
           move today's data aside (into .before-restore-<time>, never deleted)
               ▼
           put the backup in its place
```

## 8.3 Releasing a new version

```
 market open (09:00–15:45 on a trading day)? ──yes──► refused (unless --emergency, logged)
        │ no
        ▼
 take a backup ──► keep the running version as "previous" ──► build and start the new one
        │
        ▼
 healthy, and showing the right version number? ──no──► roll back to "previous" by itself
        │ yes
        ▼
 done; every step written to the deploy log
```

Details: [runbooks/release.md](../runbooks/release.md).

## 8.4 How we know the code works

```
            ┌───────────────────────────────────────────────────────────┐
  level 1   │ UNIT TESTS: ~1,250 server + ~400 screen. Seconds to run.     │
            │ "given this, the code must do that"                          │
            ├───────────────────────────────────────────────────────────┤
  level 2   │ MUTATION CHECKS: break one rule at a time on purpose;        │
            │ a test must notice. Proves the tests really guard the rules.│
            ├───────────────────────────────────────────────────────────┤
  level 3   │ REAL RUNS: a real browser clicking the real demo;            │
            │ results saved in context/evidence/                           │
            ├───────────────────────────────────────────────────────────┤
  level 4   │ DRILLS: crash-and-restart, backup-and-restore,               │
            │ deploy-and-rollback, done for real                           │
            └───────────────────────────────────────────────────────────┘
  and on every PR, GitHub runs level 1 automatically (CI)
```

**A mutation check, step by step:**

```
 the real rule:      if qty > closable - taken:  refuse
 the deliberate bug: if qty > closable:          refuse     (forgets other closes)
        │
 run the tests ──► one fails ✓  good: this rule is guarded
                   all pass ✗   a missing test: write it, then try again
 put the line back; next bug.  "26/26 caught" = all 26 deliberate bugs were noticed.
```

## 8.5 Running it on your computer

**Once:** install the screen tools and build the screens:

```bash
cd ui && npm install && npm run build && cd ..
```

**The demo** (pretend money, made-up prices):

```bash
python3 -m laddrix.tools.demo --ui ui/dist    # leave it running
# in a second terminal:
python3 -m laddrix.tools.demo --code          # prints a 6-digit login code
# open http://localhost:8000
#   username: demo     password: demo-password-123     code: from the line above
# a code works once; wait 30 s for the next one if you log in again
```

**The tests:**

```bash
python3 -m pytest -q tests                       # the server's tests
cd ui && npx vitest run                          # the screens' tests
```

**Watch a live ladder in the terminal** (needs a Kite login):

```bash
python3 -m laddrix.tools.login
python3 -m laddrix.tools.watch NFO:NIFTY26OCTFUT
```

---

Next: [Chapter 9 · How we work, what's next, your steps](09-work-next-your-steps.md).
