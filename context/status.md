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
     merged (PR #417).
  2. #415 multi-leg execution (the ASE algo: quote one leg, hedge the others): merged (PR #418).
  3. #416 the spread ladder, with right-click "Autospreader settings…": merged (PR #421).
  Left in #250/#251: several quoting legs, a maximum unhedged exposure, timeouts, restart
  recovery, structure positions, historical spread charts.
- **Bug #419** (paper ids repeated after a restart; found by the spread ladder check): fixed, merged (PR #420).
- **Bug #422 (found reviewing the Autospreader against the old brief):** a hedge bigger than
  one order may carry was refused whole and left the spread unhedged. Now every ASE order fits
  the account's lots and value limits: hedges go in pieces at once, the quote one piece at a
  time, and a leg that can't fit one lot fails at start in words. Fixed (PR #423).
- **Ayush asked (24 Oct, night) to continue with the Autospreader's open items.** Split into
  six issues, done in this order, one PR each:
  1. #424 freeze quantities (the exchange's most per order): merged (PR #430);
  2. #425 Autospreader controls (re-quote threshold, max unhedged, hedge chase and timeout,
     hedge-now and flatten actions): merged (PR #431);
  3. #426 algos survive a restart (reconcile first, come back paused, hedges kept): merged
     (PR #432);
  4. #427 several quoting legs at once: merged (PR #433);
  5. #428 structure positions (spreads held, their P&L, the unallocated rest): merged (PR #434);
  6. #429 spread charts (candles of the spread's mark from its legs, gaps shown): merged (PR #435).
  All six are done; the parent issues #250 and #251 are closed.
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
