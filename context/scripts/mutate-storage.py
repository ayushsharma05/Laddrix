"""#269 mutation check: break one rule that keeps file names inside their folder (the candle
store's id check and containment, whole-or-nothing writes, row checks, the import API, account
ids); the tests must notice. Restores each file in `finally`; a hang counts as caught. Run alone.
Every anchor is checked to be unique first."""
import subprocess

BT = "src/laddrix/research/backtest.py"
ROUTES_RESEARCH = "src/laddrix/server/routes/research.py"
US = "src/laddrix/server/users.py"
CMD = ["python3", "-m", "pytest", "-q", "-x", "tests/test_safe_storage.py", "tests/test_backtest.py",
       "tests/test_users.py"]
MUTANTS = [
 (BT, "        if not _CANDLE_ID.match(instrument_id) or \"..\" in instrument_id:", "        if not _CANDLE_ID.match(instrument_id):"),
 (BT, "        if not _CANDLE_ID.match(instrument_id) or \"..\" in instrument_id:", "        if \"..\" in instrument_id:"),
 (BT, "        if folder.resolve().parent != self.root.resolve():", "        if False:"),
 (BT, "_CANDLE_ID = re.compile(r\"^[A-Z]{2,4}:[A-Za-z0-9][A-Za-z0-9 &._-]{0,59}$\")", "_CANDLE_ID = re.compile(r\"^[A-Z]{2,4}:.+$\")"),
 (BT, "            elif at in seen:", "            elif False:"),
 (BT, "            if volume < 0:", "            if False:"),
 (BT, "            if extra:", "            if False:"),
 (BT, "    if isinstance(value, bool) or not isinstance(value, int):", "    if not isinstance(value, int):"),
 (BT, "        os.replace(part, target)  # whole or nothing: a reader never sees half a day", "        target.write_text(part.read_text())"),
 (BT, "        return sorted((p.stem for p in folder.glob(\"*.json\") if _DAY_FILE.match(p.stem)),", "        return sorted((p.stem for p in folder.glob(\"*.json\")),"),
 (BT, "        folder = self._folder(instrument_id)\n        problems = []", "        folder = self.root / instrument_id.replace(\":\", \"_\")\n        problems = []"),
 (ROUTES_RESEARCH, "        if platform.instruments.get(body.instrument_id) is None:\n            raise HTTPException(422, f\"unknown instrument", "        if False:\n            raise HTTPException(422, f\"unknown instrument"),
 (ROUTES_RESEARCH, "        audit(\"CANDLES_IMPORTED\"", "        (lambda *a: None)(\"CANDLES_IMPORTED\""),
 (US, "        if not account.id.isascii() or not account.id.replace(\"-\", \"\").isalnum():", "        if False:"),
]
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(CMD, capture_output=True, timeout=300)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
