# MockTrade status

As of 24 Oct 2026 (India time). GitHub issues are the source of truth. The full history up to
23 Oct (every milestone's PRs and checks) is kept in
[docs/archive/history/status-to-2026-10-23.md](../docs/archive/history/status-to-2026-10-23.md).

## Where things are

- **The platform:** complete for paper trading on NSE/BSE through Zerodha Kite. This covers
  the terminal, orders, risk, algos, options, charts, alerts, research, reports, admin, the
  desktop app and deployment.
- **Hardening (#257) and security (#279):** every child issue is done. Live trading still waits
  on the steps only Ayush can do: the go-live run, Zerodha's answers, and the independent
  security review (#369).
- **Several firms on one server (#368):** workspaces (#394), each trader's own prices (#395),
  files and jobs per firm (#396), and the two-firm browser drill (#397).
- **Bug issues:** none open. The last ones fixed:
  - #271: webhooks connect only to the address they checked;
  - #274: research and reports never hold up trading; a backtest's results no longer depend
    on the machine;
  - #277: honest labels for estimated data, and the combined fault drills.
- **Pull requests:** none open.

## What's next

1. **Ayush's to-do list** (one GitHub issue): the steps only he can do, in order.
2. **The feature issues**, smallest dependency first:
   - operations: #287;
   - phone screens: #283;
   - Post-v1: #242–#255;
   - decisions: #292, #278;
   - Later: #297–#322.

## How to run and test it

[docs/running-locally.md](../docs/running-locally.md).
