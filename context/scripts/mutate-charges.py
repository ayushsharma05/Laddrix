"""#214 mutation check: break one charges rule, tests/test_charges.py must notice.
Restores each file in `finally`; a mutant that hangs the tests counts as caught.
Run alone (two runs at once overwrite each other's files).
The FILL-only filter in service.py is not listed: recomputing on other events changes nothing
(an unchanged bill is never pushed), so no test can tell; it only saves work."""
import subprocess
M_, S = "src/mocktrade/charges/model.py", "src/mocktrade/charges/schedules.py"
V = "src/mocktrade/charges/service.py"
M = [
 (M_, "        return Segment.DELIVERY if product is Product.CNC else Segment.INTRADAY", "        return Segment.INTRADAY"),
 (M_, "                brokerage = min(brokerage, Fraction(rates.brokerage_cap_paise))", "                pass"),
 (M_, "            brokerage = Fraction(rates.brokerage_flat_paise)", "            brokerage = Fraction(0)"),
 (M_, "            \"stt\": pct(rates.stt_buy_pct if buying else rates.stt_sell_pct),", "            \"stt\": pct(rates.stt_sell_pct),"),
 (M_, "            \"stamp\": pct(rates.stamp_buy_pct) if buying else Fraction(0),", "            \"stamp\": pct(rates.stamp_buy_pct),"),
 (M_, "        taxable = parts[\"brokerage\"] + parts[\"exchange\"] + parts[\"sebi\"] + parts[\"ipft\"]", "        taxable = parts[\"brokerage\"] + parts[\"exchange\"]"),
 (M_, "        parts[\"gst\"] = taxable * self.schedule.gst_pct / 100", "        parts[\"gst\"] = taxable * self.schedule.gst_pct / 1000"),
 (M_, "                mine[\"dp\"] = Fraction(self._rates(o).dp_paise)", "                mine[\"dp\"] += Fraction(self._rates(o).dp_paise)"),
 (M_, "VENUE = {\"NSE\": \"NSE\", \"NFO\": \"NSE\", \"BSE\": \"BSE\", \"BFO\": \"BSE\"}", "VENUE = {}"),
 (M_, "            by_position={k: sum(r.values()) for k, r in sorted(rounded.items())},", "            by_position={k: _round(sum(exact[k].values())) for k in sorted(rounded)},"),
 (M_, "    return int(value + Fraction(1, 2)) if value >= 0 else -int(-value + Fraction(1, 2))", "    return int(value)"),
 (M_, "        if rates is None or o.qty <= 0:", "        if rates is None:"),
 (M_, "    return f\"{instrument_id}|{product.value}\"", "    return instrument_id"),
 (S, "            stt_sell_pct=pct(\"0.05\"),", "            stt_sell_pct=pct(\"0.02\"),"),
 (S, "            stt_sell_pct=pct(\"0.15\"),", "            stt_sell_pct=pct(\"0.1\"),"),
 (S, "            stamp_buy_pct=pct(\"0.015\"),", "            stamp_buy_pct=pct(\"0.003\"),"),
 (S, "    missing = [s.value for s in Segment if s not in schedule.segments]", "    missing = []"),
 (S, "                brokerage_cap_paise=_int_or_none(rates.get(\"brokerage_cap_paise\")),", "                brokerage_cap_paise=None,"),
 (V, "        if qty <= 0 or price_paise <= 0:", "        if False:"),
 (V, "        today = [v for v in self._orders.orders() if v.created_at >= since]", "        today = list(self._orders.orders())[:1]"),
]
caught = 0
for f, a, b in M:
    src = open(f).read()
    assert a in src, (f, a)
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_charges.py"],
                           capture_output=True, timeout=90)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(M)} caught")
