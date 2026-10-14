# Check scripts (used by Claude in cloud sessions)

| Script | What it does | Run |
| --- | --- | --- |
| `e2e-ladder.cjs` | Real browser (Playwright): log in to the demo server, place/move/cancel from the ladder, a marketable buy fills, position updates, screenshots | `node context/scripts/e2e-ladder.cjs <out-dir>` |
| `e2e-books.cjs` | Real browser: orders from the ladder, then order book tabs, positions, audit trail + timeline, cancel from the order book, Messages | `node context/scripts/e2e-books.cjs <out-dir>` |
| `e2e-ticket.cjs` | Real browser: resets the saved layout, then LMT, MKT, SL-M and SL from the order ticket (including a refused stop) | `node context/scripts/e2e-ticket.cjs <out-dir>` |
| `e2e-algos.cjs` | Real browser: starts a slicer and a spreader from the algo form, expands the spread, pause/resume/stop, reads Messages | `node context/scripts/e2e-algos.cjs <out-dir>` |
| `mutate-algo-ui.py` | 12 mutations of the algo screen, its model and the store; each must fail a test | `cd ui && python3 ../context/scripts/mutate-algo-ui.py` |
| `e2e-compose.cjs` | Real browser through Caddy (`https://localhost`, compose stack up): log in with the user's TOTP secret, buy from the ladder, positions and audit | `node context/scripts/e2e-compose.cjs <totp-secret>` |
| `e2e-kite-login.cjs` | Real browser against `serve` in kite mode (no Kite needed): the strip says login is needed, and the button goes to Zerodha with api_key and state | `node context/scripts/e2e-kite-login.cjs <totp-secret>` |
| `mutate-kite-live.py` | 12 mutations of `broker/kite/live.py`; each must fail `tests/test_kite_live.py` | `python3 context/scripts/mutate-kite-live.py` |
| `mutate-pricing.py` | 16 formula mutations of `options/`; each must fail `tests/test_option_pricing.py` | `python3 context/scripts/mutate-pricing.py` |
| `e2e-chain.cjs` | Real browser: open the Option Chain (Widgets), maximize/restore, click a put to drive the ticket, switch expiry | `node context/scripts/e2e-chain.cjs <out-dir>` |
| `e2e-strategy.cjs` | Real browser: Strategy Builder, iron condor, payoff and figures, Alt-click a leg from the chain | `node context/scripts/e2e-strategy.cjs <out-dir>` |
| `mutate-strategy.py` | 13 mutations of `options/strategy.py` | `python3 context/scripts/mutate-strategy.py` |
| `e2e-popout.cjs` | Real browser: pop a ladder out, check it updates live, trade from it, dock it back | `node context/scripts/e2e-popout.cjs <out-dir>` |
| `ui/desktop/e2e/desktop-smoke.cjs` | The real Electron app under a display: login, pop-out, Node-access probe, memory | `cd ui/desktop && xvfb-run -a node e2e/desktop-smoke.cjs <out-dir>` |
| `mutate-algo.py` | Breaks each algo-engine safety rule and checks a test fails | `python3 context/scripts/mutate-algo.py` (repo root) |
| `mutate-ladder.py` | Breaks each ladder rule on purpose and checks a test fails (always restores the file) | `cd ui && python3 ../context/scripts/mutate-ladder.py` |
| `mutate-books.py` | Same for the K5.5 screens | `cd ui && python3 ../context/scripts/mutate-books.py` |

Before the e2e scripts: build the UI and start the demo server (fresh data = the default layout):

```bash
cd ui && npm ci && npm run build && cd ..
rm -rf data/demo
python3 -m mocktrade.tools.demo --ui ui/dist      # background; http://localhost:8000, demo / demo-password-123
```

The scripts assume the cloud container's paths (`/home/user/MockTrade`, Playwright at `/opt/node22/lib/node_modules/playwright`, Chromium at `/opt/pw-browsers/chromium-1194`); adjust elsewhere. Stop the demo with `pkill -f "^python3 -m mocktrade.tools.demo"` (the `^` stops it matching the shell's own command line, which killed the shell twice).

TOTP codes work only once (replay protection) and 5 failures lock the user for 15 minutes:
scripts must use a fresh code each login, and `python -m mocktrade.tools.demo --code` must run
from the repo root (elsewhere it creates a second demo user with a different secret).
