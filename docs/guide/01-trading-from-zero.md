# Chapter 1 · Trading, from zero

[Guide home](README.md) · next: [Chapter 2 · The big picture](02-big-picture.md)

Before the code makes sense, the world it lives in has to make sense. This chapter uses no code
at all.

---

## 1.1 A market is a big fruit stall

Imagine one giant fruit stall where everyone in India buys and sells mangoes.

```
   people who want to SELL mangoes          people who want to BUY mangoes
   "I'll sell 10 at ₹101"                   "I'll buy 5 at ₹100"
   "I'll sell 20 at ₹102"                   "I'll buy 8 at ₹99"
                 \                                /
                  \                              /
                   ▼                            ▼
              ┌────────────────────────────────────┐
              │  THE EXCHANGE (NSE or BSE)          │
              │  a referee that matches buyers      │
              │  and sellers, fairly, in order      │
              └────────────────────────────────────┘
                   when a buyer and a seller agree on a price:
                   a TRADE happens, and that price is the LAST PRICE
```

- **The exchange** is the referee. In India the big ones are **NSE** and **BSE**.
- What is traded isn't mangoes but **instruments**: shares of companies (RELIANCE), and
  contracts like futures and options (NIFTY futures).
- **A price** moves all day, because people keep changing what they'll pay.

## 1.2 You can't walk up to the exchange yourself: you need a broker

```
   you  ──►  BROKER (Zerodha)  ──►  EXCHANGE (NSE)
            holds your money,        matches orders
            checks you can pay,
            sends your orders
```

**Zerodha** is our broker. **Kite Connect** is the door Zerodha gives to programs (like
Laddrix) instead of people. Through it a program can:
- **hear prices** as they change (a "WebSocket": a phone line that stays open and keeps talking);
- **send orders** and hear what happened to them (a "REST API": you ask, it answers once).

## 1.3 An order is a note you hand to the broker

```
 ┌───────────────────────────────────────────┐
 │  BUY   75   NIFTY futures   at ₹25,010     │   ← what, how many, at what price
 │  type: LIMIT   product: MIS   valid: DAY   │   ← the small print
 └───────────────────────────────────────────┘
```

| Word | Meaning, simply |
| --- | --- |
| **BUY / SELL** | Which way. |
| **Quantity** | How many. Futures and options come in fixed bundles called **lots** (NIFTY: 75 in a lot), so 75, 150, 225… |
| **LIMIT** | "Only at this price or better." It may wait. |
| **MARKET** | "At whatever the price is now." Fast, but the price can surprise you. |
| **Stop-loss (SL, SL-M)** | "Wake up only if the price reaches X, then sell." Used to limit a loss. |
| **MIS** | Intraday: you promise to close it today. If you don't, the broker closes it for you around 15:20. |
| **NRML** | Overnight futures and options: it can stay open for days. |
| **CNC** | Buying shares to keep. |
| **Tick** | The smallest step a price can move (NIFTY futures: 5 paise). It also means one price update. |

## 1.4 What happens to an order

```
 you hand the note ──► PENDING (the broker is reading it)
                          │
                          ▼
                        OPEN (waiting at the exchange for someone to agree)
                          │
               someone sells you 50 of your 75
                          ▼
                        PARTIAL (50 done, 25 still waiting)
                          │
               the other 25 arrive
                          ▼
                        FILLED (all done)  ✓

 other endings:  REJECTED (refused: no money, a wrong price…)
                 CANCELLED (you changed your mind before it filled)
```

A **fill** is each piece that got done ("50 at ₹25,010").

## 1.5 A position, and profit and loss (P&L)

After your buy fills, you **hold** 75 NIFTY futures. That holding is your **position**.

```
 bought 75 at ₹25,010   ──►  position: +75 (LONG: you gain if the price rises)
 price now ₹25,030         P&L = 75 × (25,030 − 25,010) = +₹1,500
 price now ₹24,990         P&L = 75 × (24,990 − 25,010) = −₹1,500

 sold 75 you didn't have ──► position: −75 (SHORT: you gain if the price falls)
 sell 75 back after buying 75 ──► position 0 ("flat"): the P&L is now locked in ("realised")
```

**Closing** a position means doing the opposite trade: selling what you bought, or buying back
what you sold.

## 1.6 Why safety matters so much

A program can send orders much faster than a person, and so it can make mistakes much faster too.
A bug can turn a ₹1,500 loss into a ₹1,50,000 loss in a second. That's why so much of
Laddrix is checks, limits and "what if the line goes dead?" The safety parts get their own
chapter, [Chapter 5](05-safety.md).

## 1.7 Paper trading: a practice stall

Laddrix has a **paper broker**: a pretend broker inside Laddrix. It hears the real prices, but
fills your orders in pretend money. You can learn and test with zero risk.

```
 real prices ──► paper broker: "your buy at ₹25,010 would have filled now" ──► pretend fills
                 (no money moves, nothing reaches the exchange)
```

The **demo** goes one step further: it doesn't even need real prices. It makes up believable
ones, so everything runs on your own computer.

---

**Check yourself:** explain to someone, in your own words, what happens between "BUY 75 NIFTY"
and "position +75". If you can, go on to [Chapter 2](02-big-picture.md).
