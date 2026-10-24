# Chapter 10 · Build it yourself (a plan)

**Goal (Ayush, 16 Oct):** understand every code file well enough to write MockTrade from scratch.
Chapters 1–9 explain *what* each part does; this chapter will explain *how to build* each file,
in the order you'd write them, each step small enough to type, run and test yourself.

**Shape of every step:**
1. what we're building and why (one picture);
2. the file, explained line by line;
3. its test, explained;
4. "type it yourself" and run the test;
5. how it connects to the steps before.

**The build order** (each step uses only the ones before it):

| Step | Files | You end with |
| --- | --- | --- |
| 1 | `domain/money.py`, `domain/instruments.py`, `domain/orders.py`, `domain/ticks.py` | the words: money, instruments, orders, ticks |
| 2 | `refdata/store.py` | a list of instruments to look up |
| 3 | `marketdata/service.py`, `marketdata/simulator.py` | made-up prices flowing |
| 4 | `broker/base.py`, `broker/fake.py` | the broker plug and a test broker |
| 5 | `orders/model.py`, `orders/manager.py` (submit, apply first) | orders tracked from send to fill |
| 6 | `positions/book.py` | positions and P&L from fills |
| 7 | `risk/limits.py`, `risk/checks.py`, `orders/gate.py`, `risk/throttle.py` | the safety inspector |
| 8 | `risk/kill_switch.py`, `store/events.py` | STOP and the ledger |
| 9 | `broker/paper.py`, `broker/rules.py`, `broker/paper_funds.py` | a paper broker filling against prices |
| 10 | `platform.py` (`add_account`) | everything wired for one account |
| 11 | `server/security.py`, `server/users.py`, `server/app.py` (login, place, orders), `server/stream.py` | a web API and live stream |
| 12 | `tools/demo.py` | the demo you can run |
| 13 | `ui/src/api/`, `ui/src/state/store.ts`, `ui/src/shell/`, one widget (the order book) | screens that show orders |
| 14 | the ladder, the ticket | trading from the screen |
| 15 | `recovery.py`, `positions/reconcile.py`, unanswered orders in the manager | surviving crashes |
| 16 | `risk/closing.py`, `risk/flatten.py`, `risk/mtm.py`, `risk/squareoff.py` | safe closing |
| 17 | `algos/engine.py`, `algos/slicer.py`, `algos/protect.py` | robot helpers |
| 18 | `broker/kite/` | the real broker |
| 19 | options, charges, charts, alerts, reports, research | the extras, one at a time |
| 20 | `deploy/`, `tools/backup.py`, `tools/restore.py` | running it on a server |

**How we'll write it:** steps 1–5 first (the core), then you type step 1 yourself before we go on.
Explaining every one of about 200 files line by line will take several sessions, so we'll write
the chapter one step at a time, in this order.
