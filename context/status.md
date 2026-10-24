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
- **Free pages (#410, 24 Oct, Ayush's ask):** a page can hold widgets as windows of any
  size, anywhere, with gaps; the page scrolls both ways to reach them. Edges snap to
  neighbours and an 8 px grid (Alt: no snap); merged in PR #411. Each page tab is FREE or DOCKED (click the label
  to switch); new pages and the first-run Main are free; old layouts open docked. Screenshot:
  `context/images/free-canvas-page.png`.
- **Right-click menus (#412):** the app's own menu on right-click (Menu key, Shift+F10): the
  page's settings and "Open widget" on empty space, a widget's own settings on a widget.
- **Right-click menus:** merged (PR #413).
- **Autospreader (Ayush's ask, 24 Oct), in three parts** ([docs/design/spreads.md](../docs/design/spreads.md)):
  1. #414 spread definitions (2 to 6 legs) and live implied prices, the Autospreader widget:
     done, in its PR.
  2. #415 multi-leg execution (quote one leg, hedge the others): next.
  3. #416 the spread ladder, with right-click "Autospreader settings…": after #415.
- **Pull requests:** the #414 PR, until it merges.

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
