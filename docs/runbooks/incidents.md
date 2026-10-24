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
4. **A trade made outside MockTrade** (#355; risk or admin): ask MockTrade what explains it,
   `GET /api/accounts/<account>/external-trades?instrument_id=<id>&product=<NRML|MIS|CNC>`.
   It lists the broker's trades in that position that no MockTrade order placed, and says
   whether they explain the difference **exactly**. If they do, check them with whoever
   traded, then adopt them: `POST` the same URL with
   `{"instrument_id", "product", "acknowledgement": "what you checked"}`. Each trade goes into
   the position at its own price, in the audit trail with your name, your words and the
   broker's trade id (EXTERNAL_ADOPTED); it is never adopted twice and survives a restart.
   The band clears. If they don't explain it, nothing can be adopted: go on below.
5. If Kite is right (it usually is), MockTrade takes Kite's orders as the truth on its next
   resync, after a reconnect or a restart (`docker compose -f deploy/compose.yaml restart app`).
   The band clears by itself once the positions agree.
6. If they still differ after a restart, keep the screenshots and the audit trail CSV and
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
- **The band says exactly what the STOP did** (#262), in the server's words: which orders
  were asked to cancel, any cancel that was refused (check those at the broker), any order
  the broker never answered (it is cancelled the moment the broker names it), how many algos
  stopped, and which positions are still open. The same lines go to Messages.
- **GTTs at the broker** (#276): a GTT whose every leg only reduces the position (a
  stop-loss or a target) is **kept**. Any GTT that could open or add is **deleted at the
  broker** (audit: GTT_STOPPED). If a delete fails (GTT_STOP_UNCLEAR) it is tried again every
  few seconds; if it keeps failing, delete it in the broker's app. One that fires anyway has its
  order cancelled (GTT_FIRED_IN_STOP); if that cancel failed too, the message says so: check
  the order at the broker. After Flatten, the closed position's stop-losses are deleted too.
- **Positions are not closed for you.** To close them **without resuming**, press
  **Flatten (N)** in the red band, then **Confirm**. It closes with orders that can only
  reduce (never reverse), through the exit desk; nothing else gets through the stop. Pressing
  it twice is safe: the second finds nothing left. Who may: the account's traders, risk, admin.
- **"size not known, close it at the broker":** MockTrade and the broker disagree on that
  position (a reconcile alert, or a difference being re-checked). Flatten won't guess a size;
  close it in the broker's own app, then check [Reconcile mismatch](#reconcile-mismatch).
- **A Flatten message in red** means something wasn't fully closed: a refused order (its
  reason is in the message), or part left to orders already closing it (see
  [A close says "left to orders already closing it"](#a-close-says-left-to-orders-already-closing-it-261)).

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

**Autospreaders after a restart (#426):** each one that was working comes back on the Algos
screen **PAUSED**, saying "restored after a restart: check it, then resume or stop". Its
quote was cancelled; its hedges at the broker keep working. Look at its legs and what is
unhedged, then:
- **Resume:** it hedges what is short now, then quotes again;
- **Hedge now** or **Flatten:** to settle a short leg at once;
- **Stop:** it ends (an UNHEDGED alert names any leg left short).
One that couldn't be rebuilt is in the audit trail as ALGO_LOST, in words; its orders were
cancelled. Check its legs by hand.


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

**Restore the stop it had (#264):** the band's **Restore stop** button (two clicks) starts the
same stop again for the position as it is now: the same algo and settings, sized to what is
still open. A bracket comes back as an OCO at its target and stop levels (its entry is done).
A trailing stop starts trailing again from the current price, not from where it had got to.
It is refused, saying why, when it could duplicate an exit:
- the account is still being rebuilt after a restart;
- an order of the old stop is still working, or one isn't answered by the broker yet: wait
  for its cancel to settle;
- another stop on it is still there, perhaps paused: resume or stop that one instead;
- the position has flipped or closed;
- MockTrade and the broker disagree on its size (see [Reconcile mismatch](#reconcile-mismatch)).

**The band survives restarts (#264):** each lost stop is written to the audit trail
(UNPROTECTED) and so is its resolution (PROTECTION_RESOLVED, with how). After any number of
restarts, an unresolved one comes back. A stop you restored is itself held by MockTrade, so a
later restart loses it too, and the band says so again. **Only a stop-loss order or a GTT held
by the broker survives an outage.**

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

## MTM close blocked or unclear (#263)

**You see:** a red band "`<account>` MTM `<position>`: close blocked: … still open: refused
(…); retry when ready", or "close unclear: …". The MTM Limits screen lists it under the limits
reached, with its state.

1. **Blocked:** read the reason (a risk refusal, a broker reject, a failure, or a restart while
   closing). Fix what it names if you can (e.g. trading stopped: use Flatten), then press
   **Retry**, then **Confirm**. It closes only what is still open.
2. **Unclear:** an order the broker hasn't answered, or MockTrade and the broker disagree on the
   size. Don't retry. Check the broker's own order book and positions; see
   [An order says Unconfirmed](#an-order-says-unconfirmed) or [Reconcile mismatch](#reconcile-mismatch).
3. The band clears itself once the position is closed and the broker agrees.

## MTM limits can't be read (#263)

**You see:** a red band "`<account>` MTM rules: the saved MTM limits can't be read …; NO loss
limits are watched …".

1. Your limits are **not** being watched. Set them again on the MTM Limits screen now; the band
   clears.
2. The damaged file is kept next to it (`mtm-<account>.unreadable-<time>.json`) to see what
   was there.
3. "What the MTM limits did today can't be read" means auto-close is held for today (it can't
   know whether it already closed). Check positions and orders by hand; tomorrow it starts
   fresh.

## The audit log can't write (#265)

**You see:** a red band "the audit log can't write to events.db (…): new orders are refused
until it writes again (cancels and forced closes still go). Check the disk." Or "the audit
log has fallen behind (N events waiting)". New orders come back refused with "not sent: it
couldn't be recorded on disk first (…)".

**Why:** every order is written to disk **before** it is sent, so that after any crash
MockTrade still knows it and never reuses its id. If the disk is full or broken, or writing
has stalled, MockTrade refuses new orders rather than send ones it couldn't record.

1. **Cancels always work.** Flatten and the other forced closes still go, because they only
   reduce risk; they are logged as sent without a record on disk.
2. **Check the disk:** `df -h /data` on the server (or the disk alert band). Free space by
   removing old backups or recorded ticks (`data/ticks/`).
3. **It recovers by itself:** writes are retried, nothing queued is lost, and the band clears
   once writing works again. Then new orders are accepted.
4. **If the server must be restarted meanwhile,** whatever was still waiting in memory may be
   lost. Orders already sent were written first, and the broker's own order book settles the
   rest at start (recovery).

## "The last price for … is N s old" (#267)

**You see:** an order refused with "the last price for NIFTY26OCTFUT is 75 s old (limit 60 s):
open its ladder or wait for a new price; closing trades still go".

**Why:** an order that adds risk is checked against **its own instrument's** latest price,
and that price must be at most 60 s old. Other instruments ticking busily don't count. A
quiet or unsubscribed instrument's old price could be far from the market.

1. Open the instrument's ladder (or watchlist row): that subscribes it, and a fresh price
   arrives within a second if the market is open.
2. If no price comes: the market may be closed, or the feed is down (see the prices band).
3. Closing orders (and Flatten, square-off, loss limits) are never refused for this.

## "prices: N ticks were dropped" (#267)

**You see:** a yellow band "prices: N ticks were dropped since the last check because algos or
paper fills fell behind; the server is overloaded".

**Why:** algos and the paper broker see **every** tick (so a spike through a stop is never
skipped), with up to 10,000 unread per reader. Past that, the oldest are dropped and counted,
so memory stays bounded.

1. It clears by itself at the next check with no new drops.
2. If it keeps showing: too many algos or instruments for this machine. Stop some algos,
   watch fewer instruments, or move to a bigger server (`/api/metrics` shows the load).

## An order's fills don't match the broker's trades (#352)

**What you see:** the audit trail has TRADES_DIFFER ("order MT… : MockTrade has 150 filled,
the broker's trades 100"), or a CORRECTED row ("the broker's trade list corrected what its
fills traded for by +₹0.50").

**What it means:**
- **CORRECTED:** the quantities agree, but the value didn't. The broker's trades are the
  authority, so the order was corrected and positions and charges followed. Nothing to do.
- **TRADES_DIFFER:** the quantities differ on two reads in a row. MockTrade changes nothing by
  itself.

**Steps:**
1. Open the order's audit history (double-click it) and the broker's tradebook (Kite web →
   Orders → Trades) for that order.
2. If the broker has *more* filled: an order update was missed. Reconcile (Positions → the
   broker state) brings the order in line; check the position matches the broker.
3. If the broker has *less*: a trade was busted (cancelled by the exchange). Tell the broker
   desk; the position follows once the order list says so.
4. Note the order id and what you did in the day's log.

## "… is restarting" or "… keeps failing" (#273)

**What you see:** an alert such as `acc-ayush: broker updates failed (ConnectionError: …):
restarting in 2 s; new orders paused until it runs`. If it keeps failing:
`… keeps failing (…): stopped trying; new orders paused. Restart the server once the cause is
fixed`.

**What it means:** one of MockTrade's background loops crashed. They are an account's broker
updates, recovery, GTT watch, position reconciling and algos, plus marking positions to prices.
- **While it restarts** (1 s, 2 s, 4 s … up to 30 s): new orders and changes on that account
  wait, and say why. **Cancels still go.**
- **After 5 failures in 5 minutes** it stops trying, and new orders stay paused until the
  server restarts.
- **`GET /api/ready`** answers 503 meanwhile, and Docker shows the app `unhealthy`.
  `GET /api/health` still says alive: the web server is fine, trading isn't. The Admin
  screen's **Status** tab shows the loop, its error and since when ([monitoring.md](monitoring.md)).

**Steps:**
1. Read the error in the alert and in the server log (`docker compose -f deploy/compose.yaml
   logs app --since 10m`; searching it with jq: [monitoring.md](monitoring.md#the-log)).
2. Fix the cause: the broker down, the disk full, the network.
3. A loop that recovered needs nothing more: the band clears by itself. One that was stopped
   needs a restart (`docker compose -f deploy/compose.yaml restart app`). Recovery then
   rebuilds and reconciles before orders open.

## "Another MockTrade server owns data/" (#273)

**What you see:** the server refuses to start:
`another MockTrade server owns /data (pid 1234 since …): only one may trade its accounts`.

**What it means:** a second server was started on the same data folder. Two servers would
trade the same accounts as the same owner, so the second refuses. The lock belongs to the
operating system: a crashed or killed server frees it at once. A **hung** one keeps it, on
purpose: it might wake up and send orders.

**Steps:**
1. Is the other server running? `docker compose -f deploy/compose.yaml ps`. If yes, use that
   one; don't start another.
2. If it is hung (no answer on `/api/health`), end it: `docker compose -f deploy/compose.yaml
   kill app`. Then start again. Recovery reconciles with the broker before taking orders.

## A credential is stolen (#364)

Do these in order. Speed matters more than understanding. The order is: **contain → stop new risk →
reconcile → keep the evidence → rotate → restore without the old sessions**. In the commands
below, `users` means
`docker compose -f deploy/compose.yaml run --rm app python -m mocktrade.tools.users`.

| What leaked | Contain at once |
| --- | --- |
| **A session** (a laptop left logged in, a cookie copied) | The user can end it themselves: menu bar **Sessions** → **End**. A stolen session alone can't make privileged changes (they need a fresh code, #363). Or `users revoke-sessions --user <id>`: their open screens close within 2 s, and logging in again needs the password **and** the authenticator. Then `users password <id>` (ends sessions too). |
| **A password and authenticator** (a phone and password both) | Admin screen: turn the user **off** (ends their sessions; their running algos stop and opening GTTs are deleted, #366). Then reset the password and the authenticator. |
| **`MOCKTRADE_SECRET_KEY` or `deploy/.env`** | Everything below, including both rotations. Assume the attacker can open every authenticator secret in any copy of `users.db` they also have. |
| **The Kite API secret** | Rotate it (below), then log every Kite account in again. |
| **A Kite token file** (`kite_token_*.json`) | It works for the rest of the day (until 6 AM). Log that account out at the broker, which ends the token, and log in again through MockTrade. If unsure, STOP the account until 6 AM. |

1. **Contain.** Use the table above. If you don't know who: `users revoke-sessions` (everyone).
2. **Stop new risk.** Press STOP for the firm (or the account). Working orders are cancelled,
   algos stop, and opening GTTs are deleted. Closing is still allowed: see [Kill switch](#kill-switch).
3. **Reconcile with the broker.** In the broker's own app, compare orders, trades and positions
   with MockTrade's. Anything MockTrade didn't place shows as a reconcile alert or under
   "trades made outside MockTrade" (#355). Don't adopt it until you know who placed it.
4. **Keep the evidence.** Before changing anything else:
   - make a backup (`python -m mocktrade.tools.backup`);
   - export the audit trail of each affected account (the auditor's export, risk or admin);
   - save the server log (`docker compose ... logs app > incident-<date>.log`).
   Write down the times. Logins, orders and every admin change are in the audit trail with who
   and when.
5. **Rotate what leaked.**
   - **The secret key:** `users new-key` and save it in the password manager. Stop the server
     (`docker compose ... stop app`). Then run
     `users rotate-key --check` and `users rotate-key`, with `MOCKTRADE_NEW_SECRET_KEY=<new>`
     added for that one command (`run --rm -e MOCKTRADE_NEW_SECRET_KEY=... app ...`). It
     refuses while the server runs, and changes nothing unless every secret re-encrypts and reads
     back. Put the new key in `deploy/.env` and start the server. **Keep the old key** with the
     backups made before today: restoring one of them needs it. After a restore, run
     `rotate-key` again from the old key to the new one.
   - **The Kite API secret:** in Zerodha's developer console, regenerate the app's API secret
     (ask Zerodha support if the console doesn't offer it). Put it in `deploy/.env`
     (`KITE_API_SECRET`, or `KITE_API_SECRET_<ACCOUNT>` for an account's own app), restart, and
     log each Kite account in again. The old secret can no longer start a session.
   - **Passwords:** each affected user changes theirs; reset authenticators if phones are in
     doubt.
6. **Restore without the old sessions.** Backups never hold sessions, so a restore brings none
   back. Even so, run `users revoke-sessions` once more after the restart and before you
   resume: it ends any session made while you worked. Resume from the STOP band only after
   step 3 is clean.

**Drilled** on the demo on 23 Oct 2026: `context/evidence/h-364-credential-drill.txt`.
