# Pre-trade margin check (K10.4, #216)

Before an order that adds exposure is sent, MockTrade asks what it would block and compares that
with the funds available. If they don't cover it, the order is refused and the message gives both
numbers. Getting out is never checked.

```
order ─► exit? (opposite side, no bigger than the position) ─► allowed, nothing asked
      ─► MarginQuoter.basket_margin([order]).final ─► what it adds, with the positions considered
            Kite: POST /margins/basket?consider_positions=true
            paper: PaperMargins (broker/paper_funds.py), an estimate
      ─► funds available (K10.1 FundsSource, cached by PortfolioService; "equity" segment)
      ─► required > available ─► refused: "not enough margin: this order needs ₹2,26,336.50,
                                           ₹94,624.00 is available"
      ─► no answer ─► allowed, with a MARGIN_UNCHECKED note in the audit trail
```

Gates in order: recovery → risk → **margin** → throttle. Risk refuses first, so a broker margin
request is spent only on orders that pass the cheaper checks; the throttle counts only what is
really sent.

## Rules (each a real failure)

- **Exits are never blocked and never wait.** An order on the other side of the net position, no
  bigger than it, is allowed without asking the broker. An order that crosses zero is checked,
  because its far side opens a new position.
- **Fail open.** A broker error, no answer within 1 s, or unknown funds all let the order through,
  and each one is recorded in the audit trail as `MARGIN_UNCHECKED` with the reason. A margin
  outage must never stop trading, least of all getting out. The broker checks margin again
  anyway when the order arrives.
- **`final`, not `initial`.** The broker's `final` figure counts the hedge from the positions
  already held: what is actually blocked.
- **A modify** is checked only when it raises the quantity, and then only the increase. A cancel
  always goes.
- **Optional per account** (`add_account(margin_check=True)`), on in serve and the demo. The ticket
  shows the margin whether the check is on or not.
- **Cost:** one broker request for each order that adds exposure (Kite: about 50–150 ms; paper: none).
  Exits cost nothing.

## Screens

**Ticket:** "Margin ≈ ₹2,26,336.50 of ₹10,00,000.00" for the order as it stands.
- The text turns red when the order needs more than is available.
- Hovering says whether the order will be refused before sending, or may be rejected by the broker.
- An exit shows "Margin: none (an exit)".
- When the margin is unknown, it shows "Margin: –" and hovering gives the reason.

**API:** `POST /api/accounts/{a}/margin` with `{instrument_id, side, qty, price_paise, product}`.
It returns `{exit, required_paise, available_paise, enough, note, checked_before_sending}`.

**Paper:** the paper broker now quotes margins too, an estimate from the rates in
`docs/design/portfolio.md`. Baskets therefore show a margin on paper as well.

## Checks

- `tests/test_margin_gate.py`: enough / not enough, exits never asked, crossing zero checked, six ways
  of failing open, modify and cancel, paper quotes, a paper account with the check on, the API
  and the audit note.
- `context/scripts/mutate-margin.py`: 15/15 caught.
- Real browser check: `context/scripts/e2e-margin.cjs`.
