"""#235 mutation check: break one rule of deleting recorded days or the replay command; the tests
must notice. Restores each file in `finally`; a hang counts as caught. Run alone. Anchors are
unique (checked before each mutant)."""
import subprocess

T = "src/mocktrade/research/ticks.py"
ROUTES_RESEARCH = "src/mocktrade/server/routes/research.py"
R = "ui/src/widgets/research/Recordings.tsx"
PY = ["python3", "-m", "pytest", "-q", "-x", "tests/test_ticks_recorder.py"]
UI = ["npx", "vitest", "run", "src/widgets/research"]
MUTANTS = [
 (T, '        if day == self.today():\n            raise ValueError', '        if False:\n            raise ValueError', PY),
 (T, '        return self._clock().astimezone(IST).date()', '        return self._clock().date()', PY),
 (T, '        if not folder.is_dir():\n            raise LookupError(f"nothing recorded on {day}")', '        if False:\n            raise LookupError(f"nothing recorded on {day}")', PY),
 (ROUTES_RESEARCH, '        if not user.has_role(Role.ADMIN):\n            raise HTTPException(403, "only an admin can delete recordings")', '        if False:\n            raise HTTPException(403, "only an admin can delete recordings")', PY),
 (ROUTES_RESEARCH, '            row["deletable"] = admin and day != store.today()', '            row["deletable"] = admin', PY),
 (ROUTES_RESEARCH, '            if replay_command is not None:  # only where a replay of this server\'s days works\n                row["replay"] = replay_command(day)', '            if replay_command is not None:  # only where a replay of this server\'s days works\n                row["replay"] = replay_command(day - timedelta(days=1))', PY),
 (ROUTES_RESEARCH, '        audit("RECORDING_DELETED", f"{user.id} deleted the ticks of {day} ({size} bytes)", {})', '        pass', PY),
 (ROUTES_RESEARCH, '        except ValueError as exc:\n            raise HTTPException(409, str(exc)) from None\n        audit("RECORDING_DELETED"', '        except ValueError as exc:\n            raise HTTPException(404, str(exc)) from None\n        audit("RECORDING_DELETED"', PY),
 (R, '          <button type="button" className="btn small" onClick={() => setArmed(true)}>\n            Delete…', '          <button type="button" className="btn small" onClick={remove}>\n            Delete…', UI),
 (R, '                      setOpen(null);\n                      refresh();', '                      setOpen(null);', UI),
 (R, '      {d.deletable &&\n        (armed ? (', '      {true &&\n        (armed ? (', UI),
 (R, '        setBusy(false);\n        setArmed(false);', '        setBusy(false);', UI),
 (R, '      ?.writeText(d.replay!)', '      ?.writeText(d.day)', UI),
]
caught = 0
for f, a, b, cmd in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180, cwd="ui" if cmd is UI else None)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
