"""#233 mutation check: break one rule of market alerts (the engine, webhooks, the service, the
stream); tests/test_market_alerts.py must notice. Restores each file in `finally`; a hang counts
as caught. Run alone. Every anchor is checked to be unique first.

Not mutants (no wrong answer, only cost): reading leg B's price from the tick vs. from the
latest price (MarketData stores the tick before its taps run, so both are the same tick)."""
import subprocess

E = "src/mocktrade/alerts/engine.py"
W = "src/mocktrade/alerts/webhook.py"
S = "src/mocktrade/alerts/service.py"
H = "src/mocktrade/server/stream.py"
CMD = ["python3", "-m", "pytest", "-q", "-x", "tests/test_market_alerts.py"]
MUTANTS = [
 (E, "            true = t.ltp_paise >= level if up else t.ltp_paise <= level", "            true = t.ltp_paise > level if up else t.ltp_paise < level"),
 (E, "            if not a.armed:\n                a.armed = True", "            if False:\n                a.armed = True"),
 (E, "        elif not a.armed:\n            if fresh or a.fired_count == 0:", "        elif not a.armed and False:\n            if fresh or a.fired_count == 0:"),
 (E, "        return since < timedelta(seconds=a.cooldown_s)", "        return False"),
 (E, "        if a.repeat == \"once\":\n            a.done = True", "        if False:\n            a.done = True"),
 (E, "        return pct >= threshold\n    if direction == \"down\":", "        return abs(pct) >= threshold\n    if direction == \"down\":"),
 (E, "        return pct <= -threshold", "        return pct <= threshold"),
 (E, "            if not t.close_paise:\n                return \"waiting for the previous close\"", "            if False:\n                return \"waiting for the previous close\""),
 (E, "                    a.baseline_oi = t.open_interest if t is not None else None", "                    a.baseline_oi = None"),
 (E, "        ids = self._index.get(tick.instrument_id)", "        ids = set(self._alerts)"),
 (E, "            elif other == instrument:", "            elif False:"),
 (E, "        if len(self.of(user_id)) >= MAX_ALERTS:", "        if False:"),
 (E, "        if a is None or a.user_id != user_id:", "        if a is None:"),
 (E, "            a.done = False  # switched on again", "            pass"),
 (E, "        self._unindex(alert_id, a.instruments())\n        del self._alerts[alert_id]", "        del self._alerts[alert_id]"),
 (E, "        if new:\n            self._watch(self.watched())", "        if False:\n            self._watch(self.watched())"),
 (E, "        if gone:\n            self._watch(self.watched())", "        if False:\n            self._watch(self.watched())"),
 (W, "    if ip is not None and not ip.is_global:", "    if False:"),
 (W, "            if not ipaddress.ip_address(a.split(\"%\", 1)[0]).is_global:", "            if False:"),
 (W, "        if r.status_code == 429 or r.status_code >= 500:", "        if False:"),
 (W, "    mac = hmac.new(secret.encode(), f\"{timestamp}.\".encode() + body, hashlib.sha256)", "    mac = hmac.new(secret.encode(), body, hashlib.sha256)"),
 (W, "        if len(self._tasks) >= MAX_PENDING:", "        if False:"),
 (W, "                if error.startswith(\"refused\") or attempt == len(BACKOFF_S):", "                if attempt == len(BACKOFF_S):"),
 (S, "        hook = self.store.webhook(a.user_id) if a.webhook else None", "        hook = self.store.webhook(a.user_id)"),
 (S, "        return {\"url\": hook[\"url\"] if hook else None, \"set\": hook is not None}", "        return {\"url\": hook[\"url\"] if hook else None, \"set\": hook is not None, \"secret\": hook and hook[\"secret\"]}"),
 (H, "            if connection.user.id == fired.alert.user_id:", "            if True:"),
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
