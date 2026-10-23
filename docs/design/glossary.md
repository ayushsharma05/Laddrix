# Glossary

Every term a trader or a developer meets in MockTrade, in plain words. Code names are in `code` font; where a term has one home in the code, it is named.

## The market

| Term | Meaning |
|---|---|
| **NSE, BSE** | India's two stock exchanges. `NSE:` and `BSE:` are their cash (share) markets. |
| **NFO, BFO** | The futures and options segments of NSE and BSE. |
| **Instrument** | Anything with a price: a share, a future, an option, an index. Our id is `EXCHANGE:SYMBOL`, for example `NFO:NIFTY26OCTFUT` (`domain/instruments.py`). |
| **Index** | A number like NIFTY 50 that has prices but can't be traded. |
| **Future (FUT)** | A contract to buy or sell an underlying at a set price on an expiry day. |
| **Expiry** | The day a future or option ends: 15:30 India time on its expiry date. After that it can't be traded, and every position in it is settled ([expiry.md](expiry.md)). |
| **Settlement (cash, physical)** | How an expired contract's positions end. Index F&O: in cash, at the final settlement price. Stock F&O: by delivery of the shares ("physical"). |
| **Option (CE, PE)** | The right, but not the duty, to buy (call, CE) or sell (put, PE) the underlying at the **strike** price, until expiry. |
| **Underlying** | What a future or option is on, for example NIFTY. |
| **Expiry** | The last day a future or option exists. |
| **Lot, lot size** | Futures and options trade in whole lots, for example 75 for NIFTY. Shares have a lot of 1. An order of 50 when the lot is 75 is refused. |
| **Tick, tick size** | The smallest price step, for example ₹0.10 (10 paise). A price off the tick grid is refused. |
| **Paise** | One hundredth of a rupee. Every price and amount in MockTrade is a whole number of paise, never a decimal, so sums are exact (`domain/money.py`). |
| **LTP** | Last traded price. |
| **LTQ** | Last traded quantity. |
| **Bid, ask** | The best price someone will buy at (bid) and sell at (ask). |
| **Depth** | The best five bids and asks with their quantities and number of orders (`DepthLevel`). |
| **Volume** | How much has traded today. |
| **OI (open interest)** | How many futures or options contracts are open. |
| **VAP (volume at price)** | How much traded at each price today; a column on the ladder. |
| **Tick (price message)** | One price update from the feed (`domain/ticks.py`). The word also means the price step above; the context tells which. |
| **Holiday** | A weekday the exchange is closed. Listed in `MOCKTRADE_HOLIDAYS`. |

## Orders

| Term | Meaning |
|---|---|
| **Order** | A request to buy or sell. What was asked is the `OrderRequest`; what is known about it now is the `OrderView`. |
| **Client order id** | Our id for an order. It is also sent to the broker as the order's tag, so an update can be matched even before the broker's own id arrives. |
| **Side** | BUY or SELL. |
| **Market order** | Trade now at the best price available. MockTrade always sends **market protection** with it. |
| **Limit order (LMT)** | Trade only at this price or better. It **rests** until it fills or is cancelled. |
| **SL (stop-loss limit)** | Waits until the price reaches the **trigger**, then works as a limit order. |
| **SL-M (stop-loss market)** | Waits for the trigger, then works as a protected market order. |
| **Market protection** | The furthest from the last price a market order may trade, in percent. SEBI requires it (never 0); `-1` means the broker's automatic protection. |
| **Validity** | DAY (until the market closes) or IOC (immediate or cancel: what can't trade at once is cancelled). |
| **Product** | **CNC**: shares for delivery. **MIS**: intraday; the broker closes it before the market closes. **NRML**: futures and options held overnight. |
| **AMO** | After-market order: placed when the market is closed, sent when it opens. |
| **Order status** | PENDING, OPEN, PARTIAL, FILLED, CANCELLED or REJECTED. It only moves forward. See [order-life.md](order-life.md). |
| **Unconfirmed** | An order sent with no clear answer back (a timeout or a dropped connection). It is never assumed rejected: the broker's order list settles it. |
| **Fill** | New quantity traded on an order. Positions are built from fills, each counted once. |
| **Partial fill** | Part of the order has traded; the rest is still working. |
| **Modify** | Change a working order's quantity, price or trigger. |
| **Cancel** | Ask the broker to stop a working order. Cancels always pass, even when trading is stopped. |
| **Reject** | Refused, with the reason in words: by our rules, the risk gate, the throttle, the broker or the exchange. |
| **Order spec** | What the server says an instrument allows: its order types, products, tick, lot and their rules. The screens draw the ticket from it and hard-code no market rules (`domain/order_spec.py`). |
| **GTT (good till triggered)** | An order the broker holds for up to a year. When the price reaches the trigger it places a limit order. **Single** has one trigger; **two-leg (OCO)** has a target and a stop. |
| **Basket** | Several orders sent together, for example the legs of an options strategy. |
| **OPS** | Orders per second. SEBI and Kite cap it; MockTrade's **throttle** keeps under 8/s, 350/min and 4,500/day. |
| **Static IP** | Kite takes orders only from a registered IP address (SEBI rule), so live orders go from the server, never a laptop. |

## Positions and money

| Term | Meaning |
|---|---|
| **Position** | What you hold now in one instrument and product: + long, − short, 0 flat (`PositionView`). |
| **Long, short, flat** | Bought more than sold; sold more than bought; even. |
| **Average price** | What the open position cost per unit, kept exactly as a fraction. |
| **P&L** | Profit and loss. **Realised**: on what has been closed. **Unrealised**: on what is still open, at the last price. |
| **MTM (mark to market)** | Valuing open positions at the current price. **MTM limits** are the trader's own loss or target limits per position or for the whole portfolio; they can close positions automatically (`risk/mtm.py`). |
| **Square-off** | Closing intraday (MIS) positions before the broker's cut-off. MockTrade warns from 15:00 and can close them all, after asking (`risk/squareoff.py`). |
| **Margin** | Money the broker blocks while an order or position is open. The ticket shows what an order would block. |
| **Charges** | Brokerage, taxes and fees on each trade; estimated in the ticket and totalled per day (`charges/`). |
| **Holdings** | Shares kept from earlier days (delivery). |
| **Funds** | Money available and used, per segment. |
| **Reconcile, broker check** | Comparing our positions and orders with the broker's own, every few minutes and after a restart or reconnect. A difference shows **POSITION DIFFERS**; nothing is adopted silently. |

## Safety

| Term | Meaning |
|---|---|
| **Risk gate** | The checks every new order passes: kill switch, allowed instruments and products, lots, price band, order value, worst-case position, daily loss, open orders, double click (`risk/checks.py`). |
| **Price band** | How far an order's price may be from the last price before it is refused as a likely typo. |
| **Kill switch, STOP** | Stops trading at once: new orders and modifies refused, working orders cancelled, algos stopped, positions kept. One per account and one for the firm. Ctrl+Shift+K. A person resets it (`risk/kill_switch.py`). |
| **Daily loss stop** | Engages the kill switch when the day's loss reaches the account's limit. |
| **Recovery** | After a restart, each account rebuilds its orders and positions from the audit trail and checks them with the broker before taking new orders (`recovery.py`). |
| **Audit trail** | Every order event, fill, kill switch and admin action, written to `events.db` and never changed. |
| **Unprotected** | A position whose stop held by MockTrade is no longer running; a red band says so (`ops/protection.py`). |
| **Band** | A strip across the top of every screen for something you must know now: STOP, disconnected, prices old, position differs, ops alerts. |
| **Ops alert** | A server-side warning (login needed, no prices, disk filling) shown as a band and sent to the phone webhook. |

## Algos

| Term | Meaning |
|---|---|
| **Algo, parent order** | An order MockTrade works for you over time by sending **child** orders. |
| **Slicer, TWAP, VWAP, POV, Iceberg, Spreader** | Execution algos: equal slices; even over time; in step with the day's volume; a share of volume; only a small slice showing; two legs at a spread. |
| **Trailing stop, OCO, Bracket** | Protection algos: a stop that follows the price; a target and a stop; an entry with both. **Held by MockTrade**, so they work only while the server runs. |
| **Algo id (SEBI)** | The exchange-issued id an approved algo must carry. |

## Platform

| Term | Meaning |
|---|---|
| **Paper, live** | **Paper**: the paper broker fills orders against real or simulated prices, and no money moves. **Live**: orders go to Kite and the exchange. The strip at the top always says which. |
| **Demo** | The whole platform on a laptop with simulated prices and a paper account (`tools/demo.py`). |
| **Replay** | Paper trading on a recorded day, with every part on the replayed time. |
| **Recording** | Every tick of a day, kept with that day's instrument list. |
| **Backtest** | Running an algo over recorded days to see what it would have done. |
| **Broker account** | One account at one broker (Kite or paper), with an owner and allowed traders. |
| **Roles** | **admin**: users, accounts, limits. **trader**: orders and algos. **risk**: sees everything and can stop trading, but places no orders. |
| **Widget** | One panel (ladder, ticket, order book…). It opens, closes and resizes anywhere and works at any size. |
| **Screen, workspace** | A screen is a page of widgets; your screens and pop-out windows together are your workspace, saved per user. |
| **Link group** | Widgets sharing a colour follow the same instrument. |
| **Ladder** | The price ladder: bids and asks by price; click to trade. **PIQ** is the estimated quantity ahead of your order. |
| **Theme** | Dark, light or colour-blind safe, saved per user. |
