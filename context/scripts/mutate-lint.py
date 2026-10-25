"""#442 mutation check: add one mistake the linters exist to catch (a conditional hook, an effect
missing what it reads, an unused name, an `any`, a lint exception with no reason or no longer
needed, the same in the desktop app), or weaken the checks themselves (a hooks rule down to a
warning, lint dropped from CI, from `make lint` or from a package); the lint or
tests/test_checks.py must fail. Runs both unmutated first. Restores each file. Run alone."""
import subprocess

UI = ["npm", "run", "--silent", "lint"]
CHECKS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_checks.py"]
R = "ui/src/widgets/research/Recordings.tsx"
S = "ui/src/widgets/strategy/StrategyBuilder.tsx"
L = "ui/src/widgets/ladder/Ladder.tsx"
D = "ui/desktop/src/config.ts"
MUTANTS = [  # (file, old, new, the check that must fail, where it runs)
 (R, "  const [loads, setLoads] = useState(0);\n",
     "  const [loads, setLoads] = useState(0);\n  if (!rest) return null;\n", UI, "ui"),  # hooks after a return
 (S, "  }, [rest, key]);", "  }, [rest]);", UI, "ui"),  # the effect misses what it reads
 (R, "  const [open, setOpen] = useState<string | null>(null);\n",
     "  const [open, setOpen] = useState<string | null>(null);\n  const unused = 1;\n", UI, "ui"),
 (R, "export function Recordings({ publish }: WidgetProps<BaseParams>) {",
     "export function Recordings({ publish }: WidgetProps<any>) {", UI, "ui"),
 (L, "-- vapVersion: the counter changed in place", "", CHECKS, "."),  # an exception without its reason
 (L, "    // eslint-disable-next-line react-hooks/exhaustive-deps -- vapVersion",
     "    // eslint-disable-next-line no-console -- vapVersion", UI, "ui"),  # one no longer needed
 (D, "  let url: URL;\n", "  let url: URL;\n  const unused = 1;\n", UI, "ui/desktop"),
 ("ui/eslint.config.js", '"react-hooks/exhaustive-deps": "error"', '"react-hooks/exhaustive-deps": "warn"', CHECKS, "."),
 (".github/workflows/ci.yml", "ui/package-lock.json\n      - run: npm ci\n      - run: npm audit --audit-level=high\n"
     "      - run: npm run typecheck\n      - run: npm run lint  # ESLint (#442): eslint.config.*\n",
     "ui/package-lock.json\n      - run: npm ci\n      - run: npm audit --audit-level=high\n"
     "      - run: npm run typecheck\n", CHECKS, "."),  # the screens' job
 (".github/workflows/ci.yml", "ui/desktop/package-lock.json\n      - run: npm ci\n      - run: npm audit --audit-level=high\n"
     "      - run: npm run typecheck\n      - run: npm run lint  # ESLint (#442): eslint.config.*\n",
     "ui/desktop/package-lock.json\n      - run: npm ci\n      - run: npm audit --audit-level=high\n"
     "      - run: npm run typecheck\n", CHECKS, "."),  # the desktop's job
 ("Makefile", "\tcd ui && npm run typecheck && npm run lint\n", "\tcd ui && npm run typecheck\n", CHECKS, "."),
 ("ui/desktop/package.json", '"lint": "eslint ."', '"lint": "true"', CHECKS, "."),
]
for cmd, where in ((UI, "ui"), (UI, "ui/desktop"), (CHECKS, ".")):
    if subprocess.run(cmd, cwd=where, capture_output=True, timeout=600).returncode != 0:
        raise SystemExit(f"{' '.join(cmd)} fails in {where} before any mutation: fix that first")
caught = 0
for f, a, b, cmd, where in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        survived = subprocess.run(cmd, cwd=where, capture_output=True, timeout=600).returncode == 0
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
