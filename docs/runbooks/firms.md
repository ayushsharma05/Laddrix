# Running several firms on one server

**When:** you (the operator) host another trading firm on the same MockTrade server.
**Design:** [tenancy.md](../design/tenancy.md). **Before the first outside firm trades:** Zerodha's
answer on shared IPs and platform-held keys (#278) and the two-firm drill (#397).

```
operator (server terminal)                 the firm's own admin (Admin screen)
──────────────────────────                 ───────────────────────────────────
users add-workspace acme "Acme" --admin ravi
  └─► firm "acme" + its first admin ravi ─► ravi logs in, adds acme's users and accounts
                                            sees and changes nothing of any other firm
```

## 1. Add a firm

```bash
docker compose -f deploy/compose.yaml run --rm -it app \
  python -m mocktrade.tools.users add-workspace acme "Acme Capital" --admin ravi
```

- It prints ravi's authenticator link **once**. Send it to ravi privately and never put it in a
  ticket or chat.
- It asks for ravi's first password; ravi changes it at the first login.
- The id (`acme`) uses letters, digits and dashes only. It names the firm's folders.

Each Kite account needs its app key in the settings (`KITE_API_KEY_<ACCOUNT>`, see
[server.md](server.md)) and a restart before it trades.

## 2. What each firm owns, and where it lives

| What | Who sees it | Where on disk |
| --- | --- | --- |
| users, accounts, orders, positions, algos, GTTs, alerts | that firm only | `users.db`, `events.db` (each row names its account) |
| live prices, charts, option chain, movers | the trader whose Kite subscription it is | memory |
| **recorded ticks** | the subscription's owner | `data/ticks/@<account id>/<day>/` |
| **imported candles** | that firm's backtests; imported by its admin | `data/candles/` (firm `main`), `data/candles/@<firm>/` |
| **backtests** | who ran it, and their firm's admin | `data/backtests/<run id>/` |
| **end-of-day reports** | the account's viewers | `data/reports/<account id>/<day>.json` |
| price-silence bands | that account's viewers | none (the screens) |
| the firm STOP | that firm only | `events.db` |

On a simulator or replay server there is one price feed: MockTrade's own simulated data,
shared by everyone. Its recordings stay in `data/ticks/<day>/`.

## 3. The daily jobs, per firm

- **Recording:** each Kite subscription is written to its own folder. One firm's recorded day
  is never in another firm's.
- **Silence check (09:16 to 15:30):** each subscription is checked on its own. "acc-x: No prices
  for over a minute" appears only to that account's viewers. Fix it with the "websocket down"
  steps in [incidents.md](incidents.md).
- **End-of-day reports (16:00):** each account's open positions are valued at its own
  subscription's prices.
- **Overload band:** dropped ticks on any subscription mean the whole server is behind, so this
  band goes to everyone. Its text names no firm.

## 4. What only the operator can do

These touch every firm, so no screen offers them:
- creating a firm;
- deleting the simulator's shared recordings, or importing into a shared candle set;
- a replay's clock;
- resetting metrics;
- restoring a backup (it restores every firm at once).

A firm's admin can delete their own subscription's recorded days and import candles for
their own firm.

## 5. Checks after adding a firm

1. Log in as the new admin. The Admin screen lists only that firm's users and accounts.
2. Log in as a user of firm `main`. Nothing of the new firm is visible: accounts, alerts,
   backtests, recordings.
3. After the next trading day, `ls data/ticks` shows one `@<account>` folder per Kite
   subscription.
4. The next backup's manifest lists `candles/@<firm>/...` files once that firm has imported
   candles.

## 6. A firm leaves

1. Turn off its accounts from its Admin screen (or `users disable <user>` for each user).
2. Export what it is owed: `users export-user <user>` for each user. The audit trail is kept
   for 5 years ([data.md](../design/data.md)).
3. After the retention period, erase each user with `users erase-user <user> --confirm <user>`,
   and remove `data/ticks/@<account>` and `data/candles/@<firm>`.
