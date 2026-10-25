"""#426 mutation check: break one rule of restoring algos after a restart (which algos come
back, ended ones not, their children, PAUSED, quotes pulled and hedges kept, nothing sent
until resumed, the spread copied at start, a broken record noted as lost in words, ids that
never repeat across runs); the tests must notice. Restores each file in `finally`; a hang
counts as caught. Run alone from the repo root."""
import subprocess

E = "src/laddrix/algos/engine.py"
A = "src/laddrix/algos/autospreader.py"
P = "src/laddrix/platform.py"
PY = (["python3", "-m", "pytest", "-q", "-x", "tests/test_algo_restore.py", "tests/test_autospreader.py",
       "tests/test_ase_controls.py", "tests/test_protect.py"], ".")
MUTANTS = [
 # which algos come back
 (P, "            else:\n                open_.pop(algo_id, None)\n        said = []", "            else:\n                pass\n        said = []", PY),
 (P, "                if e.extra.get(\"code\") in restorable or \"snapshot\" in e.extra:", "                if e.extra.get(\"code\") in restorable:", PY),
 (P, "        self.restore_algos(runtime.account.id, runtime)\n", "", PY),
 (P, "                log_.record_note(\"ALGO_LOST\", account_id, text, a)\n", "", PY),
 (P, "                        **({\"snapshot\": event.snapshot} if event.snapshot is not None else {}),\n", "", PY),
 # the engine
 (E, "            state=AlgoState.PAUSED,\n            status=\"restored", "            state=AlgoState.WORKING,\n            status=\"restored", PY),
 (E, "            children=[o.client_order_id for o in children],\n", "", PY),
 (E, "        self._pull(parent)  # its quotes; hedges stay\n", "", PY),
 (E, "        if kind.restore is None:\n            raise AlgoError", "        if False:\n            raise AlgoError", PY),
 (E, "        self._prefix = f\"{id_prefix}{_base36(int(clock().timestamp() * 1000))}-\"", "        self._prefix = id_prefix", PY),
 # the Autospreader
 (A, "        if self.flattening or self.restored:  # restored: the trader decides first (#426)", "        if self.flattening:  # restored: the trader decides first (#426)", PY),
 (A, "        self.restored = False  # the trader has looked: hedge what is short, then quote\n        self._hedge(ctx)\n", "        self.restored = False  # the trader has looked: hedge what is short, then quote\n", PY),
 (A, "        self.restored = True\n", "", PY),
 (A, "    return AutoSpreader(params, lambda _id: spread)", "    return AutoSpreader(params, lambda _id: None)", PY),
 (A, "    if not snapshot or \"spread\" not in snapshot:", "    if False:", PY),
]
for cmd, cwd in (PY,):
    if subprocess.run(cmd, capture_output=True, timeout=600, cwd=cwd).returncode != 0:
        raise SystemExit(f"the tests fail before any mutation ({' '.join(cmd)}): fix that first")
caught = 0
for f, a, b, (cmd, cwd) in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600, cwd=cwd)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
