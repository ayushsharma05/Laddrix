# MockTrade desktop app

The same MockTrade screens in a desktop window, with widgets that pop out into **real windows
on any monitor**. The app ships no screens of its own: it opens your MockTrade server, so the
desktop and the browser are always the same version.

```
main window ── our server's page (login, workspace)
   │  ⧉ on a widget's tab
   └─► app window for that widget: drawn by the main window, so it shares the one live
       connection and state (no second login, no extra delay); ⇲ or closing it docks it back
```

## Use it

```bash
cd ui/desktop
npm ci
npm start -- --server=https://trade.example.in    # or set MOCKTRADE_URL, or settings.json
```

Without `--server` it opens `http://localhost:8000` (the demo server: `python -m
mocktrade.tools.demo --ui ui/dist`). The server address can also be saved in
`settings.json` in the app's data folder (Windows: `%APPDATA%\MockTrade\settings.json`):

```json
{ "server": "https://trade.example.in" }
```

Only `https` is accepted, except `http://localhost` for development.

## Windows installer

```bash
npm run dist:win     # → release/MockTrade Setup <version>.exe
```

Run it on Windows (or anywhere with wine). Pop-outs, window places and the session are kept
per user.

## Security (`src/security.ts`, tested)

- Pages get **no Node.js and no Electron APIs**: context isolation, sandbox, no preload script.
- The main window stays on our server's origin; only `/popout.html` may open as an app window;
  other `https` links open in your normal browser; `file:`, `data:`, `javascript:` are refused.
- No camera, microphone, location or notification permissions.
- One copy runs at a time; starting it again brings the window to the front.

## Measured (14 Oct 2026, Electron 44, Linux, demo server)

| What | Memory |
|---|---|
| Whole app with the default workspace (watch, 2 ladders, ticket, book, positions, audit) | ~600 MB in 4 processes (main 170, GPU 160, network 90, page 185) |
| Each popped-out widget window | **~+20–25 MB** (pop-outs share the page's process) |

A browser tab with the same workspace uses about the page's share (~185 MB) plus the
browser's own processes, which are shared with your other tabs.

## Check it yourself

`e2e/desktop-smoke.cjs` starts the real app (under a display, e.g. `xvfb-run -a`), logs in to
the demo server, pops a ladder out, checks it updates live and that neither window can reach
Node, and prints the memory per process.
