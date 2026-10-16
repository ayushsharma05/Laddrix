"""#231 mutation check: break one candle/chart rule; tests/test_charts.py (and the Kite live test)
must notice. Restores each file in `finally`; a hang counts as caught. Run alone. Every anchor is
checked to be unique before it is used.

Not a mutant: dropping `watch.drain()` in the candle stream. Without it the loop wakes every
interval, but the "only when it changed" check still sends nothing new: a cost, not a wrong answer.
"""
import subprocess

C = "src/mocktrade/charts/candles.py"
S = "src/mocktrade/charts/service.py"
H = "src/mocktrade/charts/history.py"
W = "src/mocktrade/server/stream.py"
A = "src/mocktrade/server/app.py"
CMD = ["python3", "-m", "pytest", "-q", "-x", "tests/test_charts.py", "tests/test_kite_live.py"]
MUTANTS = [
 (C, "    n = (local - opens) // timedelta(seconds=interval.seconds)", "    n = int((local - opens).total_seconds() / interval.seconds)"),
 (C, "    if interval.seconds >= DAY:\n        return datetime.combine(local.date(), time(0), IST)", "    if False:\n        return datetime.combine(local.date(), time(0), IST)"),
 (C, "                traded = tick.volume - seen.volume", "                traded = 0"),
 (C, "            if traded == 0 and tick.ltp_paise == seen.ltp and tick.volume >= seen.volume:", "            if False:"),
 (C, "            if tick.volume >= seen.volume:\n                traded", "            if True:\n                traded"),
 (C, "        elif last is not None and start < last.start:", "        elif False:"),
 (C, "                del seen.minutes[: len(seen.minutes) - self.keep_minutes]", "                pass"),
 (C, "        return bucket_start(seen.since, MINUTE, exchange_of(instrument_id)) + timedelta(minutes=1)", "        return bucket_start(seen.since, MINUTE, exchange_of(instrument_id))"),
 (C, "            last.close = m.close", "            last.close = last.close"),
 (C, "            if m.oi is not None:\n                last.oi = m.oi", "            if False:\n                last.oi = m.oi"),
 (S, "            if (whole_from is not None and c.start >= whole_from) or c.start not in merged:", "            if c.start not in merged:"),
 (S, "            if whole_from < complete:", "            if False:"),
 (S, "            if qty <= 0:\n                continue", "            if False:\n                continue"),
 (S, "        if (end - start).days > interval.max_range_days:", "        if False:"),
 (H, "                if to < today:  # a finished day never changes", "                if True:  # a finished day never changes"),
 (H, "            if wait > 0:\n                await self._sleep(wait)", "            if False:\n                await self._sleep(wait)"),
 (H, "        step = timedelta(days=interval.kite_max_days - 1)", "        step = timedelta(days=interval.kite_max_days)"),
 (H, "self._monotonic() - kept[1] < self.TODAY_FOR_S:", "self._monotonic() - kept[1] < 10**9:"),
 (H, "            if tick.exchange_time is None and tick.received_at is None:", "            if False:"),
 (W, "                await asyncio.sleep(self.candle_interval_s)\n                await watch.wait()", "                await watch.wait()"),
 (W, "        if len(connection.charts) >= MAX_CHARTS:", "        if False:"),
 (A, "        return at if at.tzinfo else at.replace(tzinfo=IST)  # a time without a zone is India's", "        return at"),
 (A, "        runtime(account_id, user)\n        service = chart_service()\n        last =", "        service = chart_service()\n        last ="),
]
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(CMD, capture_output=True, timeout=180)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
