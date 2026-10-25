# Archive

Material that is no longer how Laddrix works, kept because it may be useful again. Nothing
here is needed to build, run or understand the current platform; start at
[docs/README.md](../README.md) for that.

## `comex-track/`: the paused COMEX / CME track (before 14 Oct 2026)

Laddrix started as a trading screen for COMEX futures against a mock CME exchange
("MockCME"). On 14 Oct 2026 the project moved to NSE/BSE through Zerodha Kite Connect, and the
CME track was paused. Its issues were closed on 16 Oct 2026 with notes. Anything still relevant
was carried into the Kite track's issues (operations ideas in #287, among others).

| File | What it is |
| --- | --- |
| [cme-wiki-guide.md](comex-track/cme-wiki-guide.md) | A beginner's guide to CME's market-data feed (MDP 3.0) and order entry (iLink 3), with diagrams: useful again if a CME broker or exchange gateway is ever added behind the broker interface |
| [requirements/](comex-track/requirements/) | The phase 1–2 product brief and user stories written for the CME track. Their trader needs (the ladder, one-click trading, a kill switch, audit) shaped the Kite track's design |
| [images/](comex-track/images/) | The CME track's diagrams: the two pipes (prices out, orders in), gap recovery, the order lifecycle, the MockCME architecture |

## `history/`: how the project was run

| File | What it is |
| --- | --- |
| [merge-recovery-2026-10-15.md](history/merge-recovery-2026-10-15.md) | The audit after PRs were merged in mixed order on 15 Oct 2026: nothing was lost, and how the branches were brought together |
| [backlog-scripts/](history/backlog-scripts/) | The one-off scripts that created the first issues, labels and milestones, for both tracks |
| [build-map/](history/build-map/) | The interactive "build map" of the first issue plan: each architecture box with the issues that designed and built it. Open `Laddrix-Build-Map.html` in a browser |
| [status-to-2026-10-23.md](history/status-to-2026-10-23.md) | The full status file as it stood before the 24 Oct 2026 tidy-up: every milestone's PRs and checks, day by day |
| `laddrix-build-map*.png` | Pictures of the build map |

The day-by-day record of every decision stays in
[context/conversation-log.md](../../context/conversation-log.md), and git keeps every earlier
version of every file.
