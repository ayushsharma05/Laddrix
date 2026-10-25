"""#448 mutation check: make one mutation script stale in each way it can drift (its code
changed, two places match, its file was renamed, a screens script's path, a mutant in a
list named M rather than MUTANTS, a path given in a tuple of names), or weaken the check that
reads them (no ui/ fallback); tests/test_mutants.py must fail. Runs it unmutated first.
Restores each file. Run alone."""
import subprocess

TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_mutants.py"]
S = "context/scripts/"
MUTANTS = [
 (S + "mutate-routes.py", '(A, "    for area in AREAS:\\n", "    for area in AREAS[1:]:\\n"),',
     '(A, "    for area in AREAS :\\n", "    for area in AREAS[1:]:\\n"),'),  # the code changed
 (S + "mutate-algo.py", '            log.exception("algo %s failed", p.algo_id)\\n            self.stop_parent(',
     '            self.stop_parent('),  # now matches two places
 (S + "mutate-lint.py", 'R = "ui/src/widgets/research/Recordings.tsx"', 'R = "ui/src/widgets/research/Recording.tsx"'),
 (S + "mutate-ladder.py", '("src/widgets/ladder/model.ts", "return Math.round(pricePaise / tickPaise) * tickPaise;"',
     '("src/widgets/ladder/model.ts", "return Math.round(pricePaise / tickPaise) * tickPaise ;"'),  # inside ui/
 (S + "mutate-gtt.py", '(D, "        if leg.qty <= 0 or leg.qty % spec.lot_size:"', '(D, "        if leg.qty <= 0 or leg.qty % spec.lot:"'),  # M = [...]
 (S + "mutate-reports.py", '(P, ', '(P, "  no such line",'),  # a path from `P, S, E = …`
 ("tests/test_mutants.py", '                target = ROOT / "ui" / file\n', '                pass\n'),
]
if subprocess.run(TESTS, capture_output=True, timeout=300).returncode != 0:
    raise SystemExit("the tests fail (or a test file is missing) before any mutation: fix that first")
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) < 1:
        print("MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        survived = subprocess.run(TESTS, capture_output=True, timeout=300).returncode == 0
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
