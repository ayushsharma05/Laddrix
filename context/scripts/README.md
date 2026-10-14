# Check scripts (used by Claude in cloud sessions)

| Script | What it does | Run |
| --- | --- | --- |
| `e2e-ladder.cjs` | Real browser (Playwright): log in to the demo server, place/move/cancel from the ladder, a marketable buy fills, position updates, screenshots | `node context/scripts/e2e-ladder.cjs <out-dir>` |
| `e2e-books.cjs` | Real browser: orders from the ladder, then order book tabs, positions, audit trail + timeline, cancel from the order book, Messages | `node context/scripts/e2e-books.cjs <out-dir>` |
| `e2e-ticket.cjs` | Real browser: resets the saved layout, then LMT, MKT, SL-M and SL from the order ticket (including a refused stop) | `node context/scripts/e2e-ticket.cjs <out-dir>` |
| `mutate-ladder.py` | Breaks each ladder rule on purpose and checks a test fails (always restores the file) | `cd ui && python3 ../context/scripts/mutate-ladder.py` |
| `mutate-books.py` | Same for the K5.5 screens | `cd ui && python3 ../context/scripts/mutate-books.py` |

Before the e2e scripts: build the UI and start the demo server (fresh data = the default layout):

```bash
cd ui && npm ci && npm run build && cd ..
rm -rf data/demo
python3 -m mocktrade.tools.demo --ui ui/dist      # background; http://localhost:8000, demo / demo-password-123
```

The scripts assume the cloud container's paths (`/home/user/MockTrade`, Playwright at `/opt/node22/lib/node_modules/playwright`, Chromium at `/opt/pw-browsers/chromium-1194`); adjust elsewhere. Stop the demo with `pkill -f "^python3 -m mocktrade.tools.demo"` (the `^` stops it matching the shell's own command line, which killed the shell twice).
