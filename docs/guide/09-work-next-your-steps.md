# Chapter 9 · How we work, what's next, and your steps

[Guide home](README.md) · previous: [Chapter 8](08-data-tests-running.md)

---

## 9.1 How every change is made

```
 ① an ISSUE on GitHub         what is wrong or wanted, and why        (e.g. #261)
        │
        ▼
 ② a BRANCH from main          a private copy to work on               (h-261-exits)
        │
        ▼
 ③ code + tests + docs + the context folder, all together
        │
        ▼
 ④ a PULL REQUEST (PR)         "Closes #261": the change, explained
        │
        ▼
 ⑤ CI: GitHub runs every test by itself ──── red? ──► fix and push again
        │ green
        ▼
 ⑥ MERGED into main            the issue closes by itself
        │
        ▼
 ⑦ check main's tests are still green
```

**`main`** is the one true version. Nothing reaches it without passing ⑤.

## 9.2 The memory of the project: `context/`

| File | What's in it |
| --- | --- |
| `context/status.md` | Where things are right now, and what's next. |
| `context/conversation-log.md` | Every decision, with the date and why. |
| `context/working-with-ayush.md` | How you like to work. |
| `context/images/` | Every diagram and screenshot (the pictures in this guide). |
| `context/scripts/` | The checks: mutation scripts, real-browser runs, drills. |
| `context/evidence/` | The saved results of real runs. |

## 9.3 How it was built, step by step

![The build map](../archive/history/mocktrade-build-map.png)

Milestones K0 to K13 built the platform, piece by piece: foundations, live prices, the paper
broker, orders and risk, positions, server and screens, algos, deployment, options, more order
types, portfolio, research, charts, alerts, admin, themes. The V1 steps (V1.1 to V1.6) made it
release-ready: recording, restart drills, backup and restore, the go-live checklist, docs, the
release process.

Then an outside review listed what a professional platform still needs. That list is the
**hardening gate, #257**.

## 9.4 What's next (from Wednesday 21 Oct)

```
 DONE  #258 positions per product · #259 unanswered orders · #260 changes in doubt ·
       #261 coordinated exits · #262 STOP/flatten · #263 loss limits survive restarts · #269
   │
   ▼
 NEXT (most important, "P0")
   #264  lost stops: flag survives restarts + a safe "Restore stop"   (half done, branch
                                                                       h-264-protection)
   #265  write each order to disk BEFORE sending; stop safely if the disk fails
   #267  never trade on old prices
   #270  one user can never see another user's alerts or research files
   │
   ▼
 THEN ("P1")
   #266 the new trading day (P&L and limits reset at the right moment)
   #268 screens wait for a full sync before allowing trades
   #271–#277 webhooks, backups, supervision, capacity, exact charges, broker-held triggers,
             honest data labels
   #287 logs and a status page · #290 F&O expiry day · #291 compliance (SEBI algo id)
   │
   ▼
 YOUR DECISIONS   #279 security review · #278 who pays for live data, and many users on Zerodha
   │
   ▼
 NEW FEATURES     #241 (#242–#255) and the later roadmap #297–#322
```

**Why waiting until Wednesday is safe:** none of these matter until real money trades, and that
only happens when you do the steps below yourself.

## 9.5 Your steps (only you can do these; slowly, when ready)

```
 ① TRY IT               run the demo; use it like a trader for a while
        │
        ▼
 ② SAY "ACCEPTED"       write on GitHub issue #240 whether v1 does what you wanted
        │                (your honest opinion, written down)
        ▼
 ③ GO LIVE, SMALL       docs/runbooks/go-live.md: 28 boxes, done together, with your own
        │               Zerodha login, the server's fixed IP, and the smallest quantity
        │               NO live order is ever placed without you saying so
        ▼
 ④ TAG v1.0.0           one command (docs/runbooks/release.md) after ① to ③
```

## 9.6 Questions to bring on Wednesday

Write them as you read. Good ones:
- "In Chapter X, I didn't understand why …"
- "What happens if … (a scenario)?"
- "Show me where in the code …"

---

[Back to the guide home](README.md)
