# The trading day

What happens by itself, and the one thing you do. Times are India time on trading days
(Monday to Friday, minus the holidays in `LADDRIX_HOLIDAYS`).

| Time | What | Who |
|---|---|---|
| 06:00 | Yesterday's Kite login expires. Kite accounts show **Log in to Kite** and refuse new orders | automatic |
| 08:30 | Today's instrument list is downloaded (also at the first login, whichever comes first) | automatic |
| 08:45 | **New trading day** (when the server ran overnight, #266): yesterday's unfilled day orders end as *expired*; overnight positions carry over at yesterday's last price, so today's P&L starts at zero and the daily loss limit and MTM limits judge today only. Their original buy price stays visible. Delivery buys leave Positions (they are holdings now). Positions in F&O contracts that expired yesterday close at their final settlement price first (EXPIRY_SETTLED, #290; [expiry.md](../design/expiry.md)). The audit trail notes it (SESSION_ROLLED). A restart builds today the same way; it never rolls twice | automatic |
| 08:30–15:30 | Every minute, any kite account without today's login raises **"Log in to Kite for …"**: a band on every screen, a line in Messages, the audit trail, and your phone if `LADDRIX_ALERT_WEBHOOK` is set | automatic |
| **Before 09:05** | **Open Laddrix → pick the kite account → Log in to Kite → log in on Zerodha's page.** The strip turns green; prices and orders start at once | **you** |
| 09:05 | Still not logged in? The reminder becomes ACTION NEEDED with a countdown to 09:15 | automatic |
| 09:15–15:30 | Every 15 s: if no price arrives for over a minute while Kite is logged in → "No prices" alert (runbook: [websocket down](incidents.md#websocket-down)) | automatic |
| all day | Every 3 minutes: positions are checked against Kite's; a difference shows POSITION DIFFERS (runbook: [reconcile mismatch](incidents.md#reconcile-mismatch)) | automatic |
| 21:45 | Databases backed up (`deploy/server-setup.sh` set the timer); kept 14 days in the volume and 30 days on the server | automatic |

**Checking the day:** `GET /api/ops` (admin or risk) lists the active alerts and the jobs that
ran. `GET /api/metrics` shows the speed of each step.

**Holidays:** put NSE's list for the year in `deploy/.env`, for example
`LADDRIX_HOLIDAYS=2026-10-21,2026-11-10,…`, then restart. On those days no reminders or price
alerts are raised, and the 08:45 rollover waits for the next trading day.

**Alerts to your phone:** set `LADDRIX_ALERT_WEBHOOK` to an https:// address that accepts a
JSON POST `{"text": "…"}`. A Slack incoming webhook, Google Chat or ntfy.sh all work. Only
warnings and errors are sent, plus their all-clear.
