"""#213 mutation check: break one funds / holdings / tradebook rule, the tests must notice
(tests/test_portfolio.py, tests/test_recovery.py). Restores each file in `finally`; a mutant
that hangs the tests counts as caught. Run alone (two runs at once overwrite each other's files).
Last run: 25/27; the two survivors behave the same as the original (an option at qty 0 has no
margin either way; a reducing case never beats the position as it is inside max())."""
import subprocess
F, P, R = "src/laddrix/broker/paper_funds.py", "src/laddrix/broker/paper.py", "src/laddrix/recovery.py"
S, K = "src/laddrix/portfolio/service.py", "src/laddrix/broker/kite/portfolio.py"
D = "src/laddrix/domain/portfolio.py"
M = [
 (F, "                return DELIVERY, Fraction(qty * price)", "                return DELIVERY, Fraction(abs(qty) * price)"),
 (F, "            return MIS, size * price * self.mis_equity_pct / 100", "            return MIS, size * price"),
 (F, "        strike = instrument.strike_paise or price", "        strike = price"),
 (F, "        if qty >= 0:  # an option bought", "        if qty > 0:  # an option bought"),
 (F, "        line, amount = max(cases, key=lambda c: c[1])", "        line, amount = cases[0]"),
 (F, "        if amount < 0:\n            credit -= amount", "        if amount < 0:\n            pass"),
 (F, "    if after == 0 or (after > 0) == (e.qty > 0):\n        return rates.margin(e.instrument, e.product, after, e.price)", "    if after == 0 or (after > 0) == (e.qty > 0):\n        return rates.margin(e.instrument, e.product, e.qty, e.price)"),
 (F, "        return line, held + added", "        return line, added"),
 (F, "    opening = starting_paise + earlier_pnl_paise - holdings_cost_paise", "    opening = starting_paise + earlier_pnl_paise"),
 (F, "    available = opening + realised_paise + unrealised_paise + credit_paise - used_total", "    available = opening + realised_paise + credit_paise - used_total"),
 (P, "            if t.product is Product.CNC:\n                sold", "            if True:\n                sold"),
 (P, "        self._book.apply_fill(r.instrument_id, r.product, r.side, qty, price)", "        pass"),
 (P, "            if (tick := self._market_data.latest(p.instrument_id)) is not None:\n                self._book.mark(tick)", "            if False:\n                self._book.mark(tick)"),
 (R, '            if held.product is Product.CNC and held.qty > 0:', '            if False:'),
 (R, '            earlier_pnl += (price - held.avg_price_paise) * held.qty  # settled at the close', '            pass'),
 (R, "            if qty > 0:  # the same rule as the book", "            if True:  # the same rule as the book"),
 (S, "            items = tuple(await getattr(self._broker, kind)())", "            items = tuple(await getattr(self._broker, kind)()) if kind != 'funds' or not self._snapshots else ()"),
 (S, "        self._store(Snapshot(kind, old.items if old else (), old.as_of if old else None, error),", "        self._store(Snapshot(kind, (), None, error),"),
 (S, "        if task is None or task.done():", "        if True:"),
 (S, "            self._fill_timer.cancel()  # a burst", "            pass  # a burst"),
 (S, "        if before is not None and (before.items, before.error) == (snapshot.items, snapshot.error):", "        if before is not None and before.items == snapshot.items:"),
 (S, "        return replace(t, client_order_id=self._client_id_of(t.broker_order_id) or \"\")", "        return t"),
 (S, "        if marked != old.items:", "        if False:"),
 (K, "        if not isinstance(row, dict) or row.get(\"enabled\") is False:", "        if not isinstance(row, dict):"),
 (K, "            ts=_ts(row.get(\"fill_timestamp\") or row.get(\"exchange_timestamp\")),", "            ts=None,"),
 (D, "        return self.qty + self.t1_qty", "        return self.qty"),
 (D, "        return self.total_qty * (self.ltp_paise - self.close_paise)", "        return self.qty * (self.ltp_paise - self.close_paise)"),
]
caught = 0
for f, a, b in M:
    src = open(f).read()
    assert a in src, (f, a)
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_portfolio.py",
                            "tests/test_recovery.py"], capture_output=True, timeout=90)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:80])
    else: caught += 1
print(f"{caught}/{len(M)} caught")
