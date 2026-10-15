"""#201 mutation check: break one GTT rule, tests/test_gtt.py must notice.
Restores each file in `finally`; a mutant that hangs the tests counts as caught.
Run alone (two runs at once overwrite each other's files)."""
import subprocess
D, P, S, M_ = "src/mocktrade/domain/gtt.py", "src/mocktrade/broker/paper.py", "src/mocktrade/orders/gtt.py", "src/mocktrade/orders/manager.py"
M = [
 (D, "        if leg.qty <= 0 or leg.qty % spec.lot_size:", "        if leg.qty <= 0:"),
 (D, "            if gap < spec.min_distance_pct:", "            if False:"),
 (D, "        if low.trigger_paise >= high.trigger_paise:", "        if False:"),
 (D, "        elif ltp_paise and not low.trigger_paise < ltp_paise < high.trigger_paise:", "        elif False:"),
 (D, "        if low.side is not high.side:", "        if False:"),
 (D, "    if request.product not in spec.products:", "    if False:"),
 (P, "            if ltp <= legs[0].trigger_paise:", "            if ltp < legs[0].trigger_paise:"),
 (P, "        rising = trigger > self.placed_at_paise", "        rising = True"),
 (P, "                gtt.status, gtt.fired_leg = GttStatus.TRIGGERED, leg", "                gtt.fired_leg = leg"),
 (P, "        if gtt.status is not GttStatus.ACTIVE:\n            raise BrokerError(f\"GTT {gtt_id} is", "        if False:\n            raise BrokerError(f\"GTT {gtt_id} is"),
 (S, "            if theirs.status is GttStatus.TRIGGERED and theirs.order_id:\n                self._adopt(theirs)", "            if False:\n                self._adopt(theirs)"),
 (S, "                reason = await self._risk.admit_at(order, leg.trigger_paise)", "                reason = None"),
 (S, "        if request.account_id != self.account_id:", "        if False:"),
 (S, "        except Exception as exc:  # no clear answer", "        except ZeroDivisionError as exc:  # no clear answer"),
 (S, "        if view.status is not GttStatus.ACTIVE:\n            raise GttError", "        if False:\n            raise GttError"),
 (M_, "        self._replay_parked(broker_order_id)\n        return order.view()\n\n    def _timed", "        return order.view()\n\n    def _timed"),
 ("src/mocktrade/risk/checks.py", "            self._price = lambda i: Tick(i, ltp_paise) if i == request.instrument_id else real(i)", "            pass"),
]
caught = 0
for f, a, b in M:
    src = open(f).read()
    assert a in src, (f, a)
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_gtt.py"],
                           capture_output=True, timeout=60)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:80])
    else: caught += 1
print(f"{caught}/{len(M)} caught")
