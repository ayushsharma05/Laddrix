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

![The build map](../archive/history/laddrix-build-map.png)

Milestones K0 to K13 built the platform, piece by piece: foundations, live prices, the paper
broker, orders and risk, positions, server and screens, algos, deployment, options, more order
types, portfolio, research, charts, alerts, admin, themes. The V1 steps (V1.1 to V1.6) made it
release-ready: recording, restart drills, backup and restore, the go-live checklist, docs, the
release process.

Then an outside review listed what a professional platform still needs. That list is the
**hardening gate, #257**.

## 9.4 What's next

What is done and what comes next changes every week, so it is kept in one place:
[context/status.md](../../context/status.md) (where things are, and the next issue). The
[roadmap](../roadmap/README.md) lists the feature issues, and the [changelog](../../CHANGELOG.md)
says what changed, version by version.

Since this chapter was first written (16 Oct), the whole hardening gate (#257) is done:
lost stops, orders on disk before sending, stale prices, a new trading day, the audit trail,
backups, supervised loops, several firms on one server, and more. So are the Autospreader
(#414–#416, #424–#429) and operations (#287).

## 9.5 Your steps (only you can do these; slowly, when ready)

They are one GitHub issue, **"Ayush's to-do list"**, each step with what it is for and where
the instructions are:

```
 ① LEARN IT         read this guide; run the demo; use it like a trader for a while
        │
        ▼
 ② ACCEPT v1        say "v1 accepted" on the to-do issue (your honest opinion, written down)
        │
        ▼
 ③ ASK ZERODHA      the questions about several traders, keys and algos (Claude drafts the
        │           email; you send it)
        ▼
 ④ GO LIVE, SMALL   set up the server, then docs/runbooks/go-live.md, with your own Zerodha
        │           login, the server's fixed IP and the smallest quantity.
        │           NO live order is ever placed without you saying so
        ▼
 ⑤ A SECURITY REVIEW  by an independent firm, before real money for others
```

## 9.6 Questions

Write them as you read, and bring them to the next session. Good ones:
- "In Chapter X, I didn't understand why …"
- "What happens if … (a scenario)?"
- "Show me where in the code …"

---

[Back to the guide home](README.md)
