"""#372 mutation check: break one rule of each account's way out (its proxy used for orders and
the daily login; none: direct; per-account setting over the default; 'direct' overrides;
bad schemes refused); the tests must notice. Runs the tests unmutated first. Restores each
file. Run alone."""
import subprocess

H = "src/laddrix/broker/kite/http.py"
L = "src/laddrix/broker/kite/live.py"
D = "src/laddrix/broker/kite/daily_login.py"
V = "src/laddrix/tools/serve.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_egress.py"]
MUTANTS = [
 (H, "            proxy=proxy,", ""),
 (H, "    return factory(app.api_key, proxy=egress) if egress else factory(app.api_key)", "    return factory(app.api_key)"),
 (L, "        http = http_for(self._http_factory, app)  # its own IP when it has one (#372)", "        http = self._http_factory(app.api_key)"),
 (D, "        http = http_for(self._http_factory, app)  # the login leaves the way its orders do", "        http = self._http_factory(app.api_key)"),
 (V, "        egress = env.get(f\"KITE_EGRESS{suffix}\") or env.get(\"KITE_EGRESS\") or None", "        egress = env.get(\"KITE_EGRESS\") or None"),
 (V, "        if egress == \"direct\":\n            egress = None", "        if False:\n            egress = None"),
 (V, "        if egress and urlparse(egress).scheme not in EGRESS_SCHEMES:", "        if False:"),
]
if subprocess.run(TESTS, capture_output=True, timeout=300).returncode != 0:
    raise SystemExit("the tests fail (or a test file is missing) before any mutation: fix that first")
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(TESTS, capture_output=True, timeout=300)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
