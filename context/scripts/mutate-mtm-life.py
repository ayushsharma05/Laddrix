"""#263 mutation check: break one rule of the MTM breach lifecycle (fired rules and breaches
saved; nothing judged before the restore; states from orders and positions; completion only
when closed and the broker agrees; unknown never retried; retry only when blocked; a refused
close visible; unreadable data kept aside and shown; auto-close held when the day's record is
lost; atomic saves); the tests must notice. Restores each file in `finally`. Run alone."""
import subprocess

M = "src/mocktrade/risk/mtm.py"
G = "src/mocktrade/ops/guards.py"
P = "src/mocktrade/platform.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_mtm_life.py", "tests/test_mtm.py",
         "tests/test_exits.py", "tests/test_server.py"]
MUTANTS = [
 (M, '        data = {"fired": {k: d.isoformat() for k, d in self._fired.items() if d == today},', '        data = {"fired": {},'),
 (M, "        self._fired = fired\n", "        pass\n"),
 (M, "        if not self._ready():\n            return []", "        if False:\n            return []"),
 (M, "        if not self._ready():\n            return\n        changed = False", "        if False:\n            return\n        changed = False"),
 (M, "            if b.state in (ALERTED, COMPLETED):\n                continue", "            if b.state in (ALERTED,):\n                continue"),
 (M, "        if unconfirmed:\n            return UNKNOWN", "        if False:\n            return UNKNOWN"),
 (M, "        if unknown:\n            return UNKNOWN", "        if False:\n            return UNKNOWN"),
 (M, "        if b.id in self._closing:\n            return REQUESTED", "        if False:\n            return REQUESTED"),
 (M, "        if working:\n            return WORKING", "        if False:\n            return WORKING"),
 (M, "        b = next((b for b in self.breaches.values() if b.key == key and b.state == BLOCKED),", "        b = next((b for b in self.breaches.values() if b.key == key),"),
 (M, "        self._save_state()  # the firing is remembered before anything is sent", "        pass"),
 (M, "        if self._index is not None and basket in self._index:\n            if order.client_order_id not in self._index[basket]:\n                self._index[basket].append(order.client_order_id)\n            self.follow()", "        if self._index is not None and basket in self._index:\n            self.follow()"),
 (M, "            kept = _keep_aside(self._path)\n", "            kept = self._path\n"),
 (M, "            self._fired = {r: today for r in [*self.positions, \"portfolio\"]}", "            pass"),
 (M, "                if problems := rule_problems(rule):\n                    raise ValueError(\"; \".join(problems))\n                self.positions[rule.key] = rule", "                self.positions[rule.key] = rule"),
 (M, "        if self.problem.startswith(\"rules:\"):\n            self.problem = \"\"", "        if False:\n            self.problem = \"\""),
 (M, "    os.replace(tmp, path)\n\n\ndef _keep_aside", "    os.replace(tmp, path)\n    tmp.write_text('')\n\n\ndef _keep_aside"),
 (G, "                self._mtm_attention(account_id, runtime.mtm)", "                pass"),
 (P, "        orders.subscribe(mtm.on_order_event)", "        pass"),
]
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
