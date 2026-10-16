# The MockTrade guide

**For Ayush. You need no programming or trading knowledge to start.** Each chapter builds on the
one before, with pictures, flowcharts, everyday comparisons and small pieces of real code
explained line by line.

```
  Chapter 1         Chapter 2          Chapter 3            Chapter 4
  Trading      ──►  The big       ──►  One order's     ──►  Prices
  from zero         picture            journey
                                                               │
  Chapter 9         Chapter 8          Chapter 7            Chapter 5
  How we work, ◄──  Data, tests,  ◄──  The code,       ◄──  Safety
  what's next       running it         folder by folder     (+ Chapter 6 · The screens)
```

| # | Chapter | You'll understand | Time |
| --- | --- | --- | --- |
| 1 | [Trading, from zero](01-trading-from-zero.md) | Exchanges, brokers, orders, lots, positions, P&L, paper trading | 20 min |
| 2 | [The big picture](02-big-picture.md) | The architecture as a restaurant; the "plug" idea; where it runs | 20 min |
| 3 | [One order's journey](03-one-orders-journey.md) | A click followed through the real code, stop by stop | 40 min |
| 4 | [Prices](04-prices.md) | Ticks, depth, the ladder, how prices flow, the demo's made-up prices | 15 min |
| 5 | [Safety](05-safety.md) | STOP, Flatten, reduce-only, the exit desk, loss limits, square-off, unanswered orders, reconcile, restarts, lost stops | 45 min |
| 6 | [The screens](06-screens.md) | Every widget, how screens get data, why the server decides | 20 min |
| 7 | [The code, folder by folder](07-code-map.md) | Every server file in one line; how to read Python and React here | 40 min |
| 8 | [Data, tests and running it](08-data-tests-running.md) | What's on disk, backups, releases, the four levels of testing, commands | 20 min |
| ★ | [Reading order: every file](reading-order.md) | Every code file in the order to read it, with what each is for | your pace |
| 10 | [Build it yourself (plan)](10-build-it-yourself-plan.md) | The order to write every file from scratch; written step by step from Wednesday | 5 min |
| 9 | [How we work, what's next, your steps](09-work-next-your-steps.md) | Issue → PR → merge; the plan from Wednesday; the steps only you can do | 15 min |

**A five-day plan:**

| Day | Read | Do |
| --- | --- | --- |
| Fri | Chapters 1–2 | Run the demo (Chapter 8.5), buy 75 NIFTY, watch positions change. |
| Sat | Chapters 3–4 | Place an order; double-click it to see its audit trail; match it to Chapter 3. |
| Sun | Chapter 5 | Press STOP, then Flatten. Set an MTM limit of ₹1 with auto-close. |
| Mon | Chapters 6–7 | Open each widget. Open `orders/manager.py` and find `submit`. |
| Tue | Chapters 8–9 | Run the tests; break one line on purpose, see a test fail, undo it. Write your questions. |

**Shorter versions:** [../GUIDE.md](../GUIDE.md) is the whole thing on one page;
[../design/glossary.md](../design/glossary.md) explains every word.
