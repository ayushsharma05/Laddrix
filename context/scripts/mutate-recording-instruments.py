"""#327 mutation check: break one rule of the day's instrument list (what is saved, when, merged
with what is on disk, read back, and how a replay uses it or refuses); the tests must notice.
Restores each file in `finally`; a hang counts as caught. Run alone. Anchors are checked unique."""
import subprocess

R = "src/mocktrade/refdata/store.py"
T = "src/mocktrade/research/ticks.py"
P = "src/mocktrade/research/replay.py"
D = "src/mocktrade/tools/demo.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_recording_instruments.py",
         "tests/test_replay.py", "tests/test_ticks_recorder.py", "tests/test_refdata_store.py"]
MUTANTS = [
 (R, '"lot_size": instrument.lot_size,', '"lot_size": 1,'),
 (R, '"expiry": instrument.expiry.isoformat() if instrument.expiry else None,', '"expiry": None,'),
 (R, '"strike_paise": instrument.strike_paise,', '"strike_paise": None,'),
 (R, '            None if record.get("strike_paise") is None else int(record["strike_paise"]),',
     '            record.get("strike_paise"),'),
 (R, '        raise ValueError(f"instrument record without {exc.args[0]!r}") from None',
     '        raise'),
 (R, '    except (TypeError, AttributeError) as exc:', '    except AttributeError as exc:'),
 (T, "        for day, per_instrument in counts.items():  # describe first: no tick goes undescribed\n            self._remember(day, per_instrument)\n", ""),
 (T, "            known = self._described[day] = set(self._saved(day))", "            known = self._described[day] = set()"),
 (T, "            if instrument_id in known:\n                continue\n", ""),
 (T, "        records = {**self._saved(day), **new}", "        records = new"),
 (T, "        known.update(new)", "        known.update(instrument_ids)"),
 (T, "            raise NoInstrumentList(f\"{day}: no instrument list ({INSTRUMENTS})\") from None", "            return []"),
 (T, "        except (ValueError, AttributeError) as exc:\n            raise ValueError(f\"{day}: the instrument list {path} is broken: {exc}\") from None", "        except NotImplementedError:\n            raise"),
 (T, "        return saved if isinstance(saved, dict) else {}", "        return saved"),
 (P, "            if instrument_id in others:", "            if False:"),
 (P, "    found = {i.id: i for i in saved}", "    found = {}"),
 (P, "    except NoInstrumentList:\n        saved = []\n    except ValueError as exc:", "    except NoInstrumentList:\n        saved = []\n    except NotImplementedError as exc:"),
 (P, "    if missing:", "    if False:"),
 (P, '        more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""', '        more = ""'),
 (P, "    found = {i.id: i for i in saved}\n    others = {i.id: i for i in fallback}", "    found = {i.id: i for i in fallback}\n    others = {i.id: i for i in saved}"),
 (D, "    instruments = InstrumentStore()\n    if replay_day is None:", "    setup_users(data)\n    instruments = InstrumentStore()\n    if replay_day is None:"),
 (D, "        except ReplayInstrumentsError as exc:", "        except NotImplementedError as exc:"),
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
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
