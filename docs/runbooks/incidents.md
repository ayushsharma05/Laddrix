# When something breaks

Each section: what you see, what it means, what to do. The first rule always applies:
**getting out works**. Cancel and STOP stay allowed when everything else is blocked.

## Login failed

**You see:** after Zerodha's page, MockTrade shows "Broker login failed", and the strip still
says "not logged in to Kite".

1. Look at the reason in the audit trail (`BROKER_LOGIN_FAILED`) or the server log
   (`docker compose -f deploy/compose.yaml logs app | grep -i "broker login"`).
2. **"logged in to Zerodha as XX…, but account … is YY…"**: you logged in with another
   Zerodha client id. Log out on kite.zerodha.com and log in again as the account's owner.
3. **Token / checksum errors**: `KITE_API_KEY` or `KITE_API_SECRET` in `deploy/.env` doesn't
   match the app in Kite's developer console. Fix the value, then
   `docker compose -f deploy/compose.yaml up -d`.
4. **The page says the redirect URL doesn't match**: in the developer console, set the
   redirect URL to exactly `https://<your domain>/kite/callback`.
5. **"Log in to Kite" says only the account holder can log in**: sign in to MockTrade as the
   user who owns that account.
6. Still failing at 09:10? Trade from the Kite app directly for now. MockTrade's other accounts
   (paper) keep working.

## WebSocket down

**You see:** the band "No prices for over a minute", or PRICES n s OLD on the screens; the
Prices light is amber or red.

1. Is it only you? If the Server light is red too, your own connection to MockTrade dropped.
   The screen reconnects by itself, and order clicks stay paused until it does.
2. Server side: `docker compose -f deploy/compose.yaml logs --since 10m app | grep -i ticker`.
   - `kite ticker dropped … reconnect`: the server is already retrying (1 s, 2 s, 4 s … up to
     30 s). Kite's status page and X/Twitter tell you if Kite is down for everyone.
   - `Kite refused the connection` / `logged out`: today's login is gone. Log in to Kite
     again from the strip.
3. While prices are stale, **don't click prices on the ladder**: what you see is old. Working
   orders at the exchange are not affected. After the feed comes back, orders are
   reconciled from Kite's order book automatically.
4. Need to act now? Use the Kite app, then let reconcile bring MockTrade up to date.

## Reconcile mismatch

**You see:** the band **POSITION DIFFERS · CONTRACT**: MockTrade's position differs from
Kite's. The Recon light is red.

1. Don't trade that contract until it is resolved: you can't trust either number yet.
2. Open the Kite app → Positions, and compare with MockTrade's Positions widget.
3. Common causes:
   - an order placed outside MockTrade (Kite app, another tool);
   - a fill MockTrade missed while disconnected;
   - an exchange-side adjustment.

   The audit trail for that contract shows what MockTrade saw.
4. If Kite is right (it usually is), MockTrade takes Kite's orders as the truth on its next
   resync, after a reconnect or a restart (`docker compose -f deploy/compose.yaml restart app`).
   The band clears by itself once the positions agree.
5. If they still differ after a restart, keep the screenshots and the audit trail CSV and
   treat it as a bug.

## Kill switch

**You see:** the red band **TRADING STOPPED · account** (or **ALL TRADING STOPPED (firm)**).

- **Who can stop:** any trader (their account), risk and admin (any account or the firm).
  The STOP button (Ctrl+Shift+K) asks for a reason.
- **What it does:** working orders are cancelled and running algos stopped, while positions
  stay as they are. New orders are refused until it is reset.
- **Stopped by itself ("auto: daily loss")?** The account hit its daily loss limit. Look at
  Positions and the audit trail before doing anything. Resetting the same day means
  overriding your own limit.
- **Resume:** the account owner, risk or an admin uses the STOPPED button. For the firm switch,
  only an admin can resume. Each reset is in the audit trail with who and when.
- Positions are not closed for you. Close them deliberately (ladder or ticket), or leave them.

## Trading paused after a restart

**What you see:** orders refused with "trading is paused after a restart: …", and on a
failure an ERROR alert "trading stays paused after a restart". `GET /api/accounts/{a}/positions`
shows `recovery: {state, reason}`.

**Why:** at start each account rebuilds its orders and positions from the audit trail and
checks them against the broker before taking new orders (`src/mocktrade/recovery.py`).
Cancels always go through.

| `recovery.state` | Meaning | Do |
|---|---|---|
| `recovering`, "waiting for the broker" | the broker can't be asked yet (Kite: before the daily login) | log in to Kite; it retries every 10 s |
| `ok` | rebuilt and matching; the reason says how many orders, fills and carried positions | nothing |
| `failed` | a working order the broker doesn't list, or a position that differs | below |

**When it failed:** compare the broker's own order book and positions (Kite console) with
MockTrade's order book and the reason. A difference means a trade made outside MockTrade,
or a lost update. Settle it at the broker if needed (cancel the stray order, close or
match the position), then **restart the server**: recovery runs again from the start.
Nothing is adopted silently: a person decides.

Paper accounts: the paper broker keeps its orders in memory, so working paper orders are
closed at a restart ("the paper broker restarted: paper orders don't survive a restart");
positions and today's P&L come back from the audit trail.


## Position UNPROTECTED

**What you see:** a red **ACTION NEEDED** band on every screen:

    UNPROTECTED acc-paper: 75 NFO:NIFTY26OCTFUT (long) is open with no stop: its stop
    (TRAILING_STOP SELL 75 NFO:NIFTY26OCTFUT (A1, demo)) was running when the server stopped
    and is not running now. Start a stop for it, or close it.

**Why:** a trailing stop, OCO or bracket is held by MockTrade, not by the exchange. It stops
protecting when:
- the server restarts (these algos don't survive one);
- someone pauses or stops it;
- it fails;
- it ends while the position is still open.

The server's protection guard (`src/mocktrade/ops/protection.py`, V1.2 #328) then keeps one band per position.
- **When it decides:** after the account's recovery has finished, so a half-rebuilt book is never taken for flat.
- **What it flags:** only positions that *had* protection and lost it. A position you chose never to protect is left alone.

**Do:** either protect it again or close it. The band clears by itself within about 5 seconds when any of these happens:
- the position is **flat**;
- a stop algo is **working** on it again (a new one, or the paused one resumed);
- a working **stop order** (SL or SL-M) is on the closing side (sell for a long, buy for a short);
- an active **GTT** is on it.

## Crash-and-restart drill (V1.2, #328)

Run it before going live and after any change to recovery, orders or positions. It uses the demo only; nothing reaches a broker.

    python3 context/scripts/drill-restart.py         # API checks: 17 PASS/FAIL lines
    node context/scripts/e2e-restart-bands.cjs       # the bands in a real browser (ui/dist built)

**What it proves:**
- **After a SIGKILL mid-session:**
  - the position comes back at its size;
  - every fill is counted once (bought 150, not 300);
  - a resting paper order is closed and the reason says why;
  - the lost trailing stop shows the UNPROTECTED band;
  - new orders and cancels work.
- **After protecting it again:** the band clears.
- **After a second crash:** the position and the fill count are the same again, and the second lost stop is named once.
- **After closing it:** the band goes.
- **In the browser:** the band appears after the restart; freezing the server for 9 s shows "PRICES 9 s OLD", which goes once it resumes. Screenshots: `context/images/v1-2-*.png`.

**What it doesn't cover:**
- **A live broker:** Kite's own order book after a crash is the go-live checklist's (#330) to check, live.
- **The recovery gate's wait:** new orders are held while recovery runs, and cancels still go through. On paper recovery is instant, so the drill can't see this wait; `tests/test_recovery.py` covers it.

## An order says Unconfirmed

**What you see:** an order in the order book with the status **Unconfirmed**, and in Messages "no answer from broker".

**Why:** the broker didn't answer when the order was sent (a timeout or a dropped connection), so MockTrade doesn't know whether it exists. It is **never retried**, and its risk stays counted, so you can't accidentally double it.

**It usually settles itself:** MockTrade asks the broker again for about three minutes.
- If the broker lists the order, it continues as normal.
- If 3 of the broker's lists over at least 60 seconds don't show it, it is marked **REJECTED: the broker never received this order**, with the times it checked.

**If it stays Unconfirmed** (the audit trail says "still not settled after asking the broker 7 times"):
1. Open the broker's own order book (the Kite app or console). Look for the order by its tag, which is our order id (for example `D2610161`).
2. **If it's there,** do nothing here: the next order update or reconnect settles it. A restart also does.
3. **If it isn't there,** press **Not placed?** in MockTrade's order book, then **Confirm: not in the broker's book**. The order becomes REJECTED, with your name in the reason and the audit trail.

**A status MockTrade doesn't know:** if Kite reports one, the order is kept (not dropped), and the audit trail shows "the broker reports a status MockTrade doesn't know: …". Tell Claude, so the status can be added (`broker/kite/orders.py`, `STATUS_MAP`).

## A change or a cancel got no answer

**What you see:** the order book says **Pending change** or **Pending cancel**. Messages say the broker didn't answer, and the audit trail shows **Replace unconfirmed** or "no clear answer".

**Why:** the broker didn't answer in time, so MockTrade can't know whether the change or cancel happened. It never guesses:
- **A change:** risk counts the **larger** quantity of the old and the new until the broker's own terms say. A second change is refused meanwhile.
- **A cancel:** the order stays "cancel on its way".

**It settles itself:** MockTrade asks the broker for about three minutes.
- **A change:** the order takes the broker's real terms.
- **A cancel:** the order becomes CANCELLED, or, if the broker still has it working, goes back to working with the message "the cancel didn't go through". You can then cancel again; pressing cancel again is always allowed in this state.

**If it stays pending** (the audit trail says "the change/cancel is still not settled after asking the broker 7 times"): look at the order in the broker's own app. A reconnect or a restart then brings MockTrade in line from the broker's order list.

## A close says "left to orders already closing it" (#261)

A loss limit or the square-off closed less than the whole position because other closing
orders were still working: a cancel that didn't take in 3 s, or an order whose fate is
unknown (Unconfirmed). Nothing is wrong yet; the position isn't closed twice.

1. Open the order book: the other closing orders are there, marked **close**.
2. If one is **Unconfirmed**, follow [An order says Unconfirmed](#an-order-says-unconfirmed).
3. If a cancel is still on its way, wait for the broker's answer (or press cancel again).
4. If the position is still open after they settle, close the rest by hand or press
   **Square off** again: the desk closes only what is left.

A refused close reading "would close more than is open" means the same thing from the other
side: something else is already closing that part.
