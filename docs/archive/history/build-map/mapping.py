BOXES = [
 # id, col, row, name, desc, build, design, explain
 ("ticket","A",1,"Order ticket & list","type orders, see them",[42,47],[],"Where the trader types an order and watches its state: working, filled, rejected."),
 ("ladder","A",2,"Price ladder","click a price to trade",[51,103],[],"The depth ladder. One click on a price sends an order. Later it shows your place in the queue."),
 ("tns","A",3,"Time & Sales","every trade, live",[120],[],"The tape: every trade with time, price, quantity and contract."),
 ("posui","A",4,"Positions & P&L","risk view, fill alerts",[57,121],[],"Live positions and profit per contract, plus fill alerts and the order-confirmation setting."),
 ("algoui","A",5,"Algo control","start and stop robots",[63],[],"The screen where the trader configures, starts and stops algos and spreads."),
 ("screens","A",6,"Audit & workspace","hedge manager, layouts",[110,111],[],"Order history screen, hedge manager and the multi-window workspace that reopens as you left it."),
 ("appserver","B",1,"Reception desk","App Server: login, routing",[41,95,74],[31],"Every screen talks only to this. It checks who you are, what you may do, and sends each order to the right site's guard."),
 ("broadcaster","B",2,"Live broadcaster","pushes updates to screens",[97],[],"Streams prices, orders, fills and positions to every open screen."),
 ("settings","B",3,"Settings service","saved layouts, preferences",[98],[],"Stores each user's settings and layouts so any screen can load them."),
 ("admin","B",4,"Admin panel","users, limits, permissions",[96,94],[],"Where an admin creates users and accounts and sets risk limits, without code."),
 ("poscopy","B",5,"Positions check","compare with drop copy",[55],[],"Compares positions from our own orders with the exchange's drop copy, and flags any difference."),
 ("gate","B",6,"Office gate","load balancer",[113],[],"Spreads users across several reception desks so one crash doesn't stop trading."),
 ("guard","C1",1,"Guard (ROM)","risk checks, order state",[40,44,45,46,56,78,79,80,81,82,83],[16,21,22,26,29],"The safety officer. Every order passes its checks. It tracks each order's life, survives restarts and can stop everything."),
 ("runner","C2",1,"Runner (SOR)","talks to the exchange",[39,75,76,77,84],[16],"Turns our neutral order into the exchange's own message and back. Owns sessions, throttling and disconnect handling."),
 ("spreadprice","C1",2,"Spread pricing","synthetic spread prices",[61],[],"Builds a ladder for any spread, fly or condor from its legs' books."),
 ("newsroom","C2",2,"Newsroom (MDS)","builds books from the feed",[50,100,101,87],[15],"Listens to the exchange's price feed, keeps a copy of every book, recovers from gaps and publishes updates."),
 ("algo","C1",3,"Algo server","runs the robots",[59,60],[],"Runs algos next to the exchange, so they react in microseconds. Their orders still go through the guard."),
 ("spreader","C2",3,"Spreader","quotes a leg, hedges rest",[62,109],[],"Works the quoting leg of a structure and hedges the other legs the moment it fills."),
 ("scripted","C1",4,"Scripted strategies","sandbox and upload tool",[107,108],[],"Traders' own small strategy scripts, run safely in a sandbox."),
 ("sdk","C2",4,"Client SDK app","an outside algo app",[106],[],"A library so an outside program can trade through Laddrix like a screen does."),
 ("practice","C1",5,"Practice mode","simulated runner",[102],[],"A runner that fills orders in a simulator instead of the exchange, for safe practice."),
 ("gateways","C2",5,"FIX & broker","FIX gateway, mock broker",[104,105],[],"Lets outside systems send FIX orders in, and routes some orders through a mock broker."),
 ("mockcme","D",1,"MockCME","its own repo, E1–E4",[],[14,15,16,18],"Our own exchange, built in its own repo (ayushsharma05/MockCME) like a real outside exchange. It keeps order books, matches by price and time, takes orders over iLink-lite, broadcasts prices over MDP-lite and replays real COMEX data."),
 ("mockcme2","D",2,"Second exchange","a second site",[],[],"A second mock exchange at another site, so routing and multi-site risk are real."),
 ("refdata","O",1,"Reference data","instruments and spreads",[86],[17,19,21],"The one list of products, contracts, tick sizes and spread legs every service reads."),
 ("pnl","O",1,"Positions & P&L","fills to net position to P&L",[54,89],[],"Turns fills into net positions and P&L as a stream."),
 ("firmbook","O",1,"Firm order book","copy and query API",[91],[],"A copy of every order in the firm that support and risk can search."),
 ("dropcopy","O",1,"Drop copy readers","every fill, a second time",[90],[],"Reads the exchange's copy of every fill, independent of the trading path."),
 ("eventlog","O",2,"Event log","careful lane, never loses",[88],[26],"An append-only record of every event, for audit and replay."),
 ("compliance","O",2,"Compliance alerts","spots rule breaks",[92],[],"Watches orders and fills and raises alerts when a rule is broken."),
 ("eod","O",2,"End-of-day jobs","settle, keep GTC orders",[93],[],"Settlement prices, end-of-day P&L, removing Day orders and carrying GTC orders."),
 ("shared","P",1,"Shared code","messages, service skeleton",[36,118],[24,25,28],"The message classes and the service template every service is built from."),
 ("monitor","P",1,"Monitoring","logs, metrics, dashboards",[65,66,67,116,117],[],"Logs, health checks, metrics, Grafana dashboards, log search and a status page."),
 ("deploy","P",1,"Deploy & environments","two sites, fake distance",[68,114,115],[],"Docker Compose sites with simulated distance, the environments and the release process."),
 ("drills","P",1,"Drills & runbooks","break it on purpose",[69,70],[],"Failure drills and the step-by-step runbooks for fixing each failure."),
]
CONTAINER_DESIGN = {"trader":[27]}
WHOLE_DESIGN = [3,4,5,6,7,8,9,10,11,12,13,20,23,30,32,33,35]
RELEASES = [43,48,53,58,64,85,99,112,71,119]

CME = "https://github.com/ayushsharma05/MockCME"
EXT = {
 "mockcme": [
   {"label": "E1", "rel": 1, "title": "E1 · Matching core (MockCME #4–#13)", "url": CME + "/milestone/2"},
   {"label": "E2", "rel": 1, "title": "E2 · Order entry (MockCME #14–#19)", "url": CME + "/milestone/3"},
   {"label": "E3", "rel": 3, "title": "E3 · Market data (MockCME #20–#24)", "url": CME + "/milestone/4"},
   {"label": "E4", "rel": 3, "title": "E4 · Real data replay and fill model (MockCME #25–#29)", "url": CME + "/milestone/5"},
 ],
 "mockcme2": [
   {"label": "E8", "rel": 6, "title": "E8 · Second venue (MockCME #40)", "url": CME + "/milestone/9"},
 ],
}
