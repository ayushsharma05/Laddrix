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
