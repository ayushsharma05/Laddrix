"""#216 mutation check: break one margin-check rule, tests/test_margin_gate.py must notice.
Restores each file in `finally`; a mutant that hangs the tests counts as caught.
Run alone (two runs at once overwrite each other's files)."""
import subprocess
G, P, L = "src/laddrix/risk/margin.py", "src/laddrix/broker/paper.py", "src/laddrix/platform.py"
M = [
 (G, "        if net and (net > 0) != (request.side.sign > 0) and request.qty <= abs(net):", "        if net and (net > 0) != (request.side.sign > 0):"),
 (G, "        if net and (net > 0) != (request.side.sign > 0) and request.qty <= abs(net):", "        if False:"),
 (G, "        return self.required_paise <= self.available_paise", "        return self.required_paise < self.available_paise or True"),
 (G, "            return None  # fail open: say so, let it through", "            return \"margin unknown\""),
 (G, "            if self._on_unchecked is not None:\n                self._on_unchecked(request, result.note)", "            if False:\n                self._on_unchecked(request, result.note)"),
 (G, "        except TimeoutError:", "        except ZeroDivisionError:"),
 (G, "        except Exception as exc:  # a read: nothing was placed", "        except ZeroDivisionError as exc:  # a read: nothing was placed"),
 (G, "        more = new.qty - order.request.qty\n        if more <= 0:\n            return None", "        more = new.qty\n        if more <= 0:\n            return None"),
 (G, "        if more <= 0:\n            return None\n        increase", "        if False:\n            return None\n        increase"),
 (G, "            required = margin.final_paise", "            required = margin.initial_paise"),
 (G, "            available = None\n        note", "            raise\n        note"),
 (P, "        return BasketMargin(initial_paise=initial, final_paise=max(0, used(True) - used(False)))", "        return BasketMargin(initial_paise=initial, final_paise=used(True))"),
 (P, "            row[1] += r.side.sign * r.qty", "            row[1] += r.qty"),
 (L, '        checks = (pause, rights, recovery, risk, margin if margin_check else None, throttle)', '        checks = (pause, rights, recovery, risk, None, throttle)'),
 (L, "            equity = [f for f in funds if f.segment == \"equity\"] or list(funds)\n            return equity[0].available_paise if equity else None", "            return None"),
]
caught = 0
for f, a, b in M:
    src = open(f).read()
    assert a in src, (f, a)
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_margin_gate.py"],
                           capture_output=True, timeout=90)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(M)} caught")
