import subprocess
F = "src/mocktrade/broker/kite/live.py"
M = [
    ("        if tokens:\n            asyncio.ensure_future(self._subscribe(link, tokens))", "        pass"),
    ("                    asyncio.ensure_future(self._take_prices(other))", "                    pass"),
    ("        link.http.access_token = None\n", ""),
    ("        if link is self._prices:\n            self._feed.on_ticks(ticks)", "        self._feed.on_ticks(ticks)"),
    ("            if link.expires is not None and now >= link.expires:", "            if False:"),
    ("        if self._instruments_day == today:\n            return", "        pass"),
    ("        os.replace(part, self._cache)", "        pass"),
    ("        if self._on_reconnect is not None:\n            self._on_reconnect(link.account.id)", "        pass"),
    ("            self.log_out(link, f\"Kite refused the connection ({exc})\")", "            pass"),
    ("        tokens = self._tokens(self._md.watched())", "        tokens = []"),
    ("            if session is not None:\n                await self.go_live(link.account, session)", "            pass"),
    ("            link.broker.on_order_message,", "            None,"),
]
src = open(F).read()
caught = 0
for a, b in M:
    assert a in src, a
    open(F, "w").write(src.replace(a, b, 1))
    r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_kite_live.py"], capture_output=True)
    if r.returncode: caught += 1
    else: print("SURVIVED:", a.strip()[:70])
    open(F, "w").write(src)
print(f"{caught}/{len(M)} caught")
