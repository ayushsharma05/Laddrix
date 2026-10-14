# MockTrade UI

The trading terminal: React + TypeScript (Vite), dockview workspace. Design: `../docs/design/ui.md`;
stream contract: `../docs/design/stream.md`.

```bash
npm ci
npm run dev        # http://localhost:5173 (start the server: python -m mocktrade.tools.demo --origin http://localhost:5173)
npm test           # unit tests (Vitest + Testing Library)
npm run build      # production build in dist/ (served by the Python server)
```

## How it fits together

```
main.tsx ─► AppProvider (rest, stream, store, session, instruments, links)
              └► App ─► Login            (no session)
                    └► Shell             (menu bar, toolbar, env strip, bands)
                          └► Workspace   (dockview + page tabs) ─► widgets
```

| Folder | What lives there |
|---|---|
| `src/api/` | `rest.ts` (HTTP + errors), `stream.ts` (the one WebSocket, shared subscriptions, reconnect), `types.ts` (server shapes) |
| `src/state/` | `store.ts` (orders, positions, kill switches, messages; merge by `updated_at`), `session.ts` (login, snapshots after every reconnect), `gate.ts` (why an order click can't go), `instruments.ts` |
| `src/workspace/` | `controller.ts` (pages, autosave), `layouts.ts` (server + browser storage), `registry.ts` (widget kinds), `links.ts` (link groups), `Panel.tsx`, `Workspace.tsx`, `defaults.ts` (first-run layout) |
| `src/widgets/` | one file per widget; importing it registers it |
| `src/shell/` | Login, Shell (menu bar, toolbar), Bands, STOP dialog |
| `src/lib/format.ts` | paise → Indian-grouped text (₹56,27,250.00) |

Rules every widget follows: fixed-size text, tabular numbers, buy blue / sell red, every state
written out, no popups for routine things, nothing sent while disconnected or stopped.
