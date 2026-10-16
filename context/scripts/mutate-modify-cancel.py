"""#260 mutation check: break one rule of changes and cancels in doubt (one change at a time, even
while gates wait; the larger size while a change is unanswered; the broker's terms settling it;
old updates never undoing a newer change; an unanswered cancel staying on its way and being
pressable again; the broker's list settling a cancel; restart; risk re-checked after a wait);
the tests must notice. Restores each file in `finally`. Run alone."""
import subprocess

M = "src/mocktrade/orders/manager.py"
G = "src/mocktrade/orders/gate.py"
K = "src/mocktrade/broker/kite/orders.py"
R = "src/mocktrade/risk/checks.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_modify_cancel.py",
         "tests/test_unconfirmed.py", "tests/test_order_manager.py", "tests/test_kite_orders.py",
         "tests/test_risk_checks.py", "tests/test_recovery.py"]
MUTANTS = [
 (M, '        if order.pending is Pending.MODIFY:\n            raise OrderError("a change to this order is already on its way: wait for it")', '        if False:\n            raise OrderError("a change to this order is already on its way: wait for it")'),
 (M, "        order.pending = Pending.MODIFY  # from here: no second change, even while gates wait", "        pass"),
 (M, "            if order.status.is_final or order.cancel_requested:  # while it waited at the gate", "            if order.status.is_final:  # while it waited at the gate"),
 (M, "                order.request = replace(new, qty=max(old.qty, new.qty))  # the worse case", "                order.request = old"),
 (M, "                order.request = replace(new, qty=max(old.qty, new.qty))  # the worse case", "                order.request = new"),
 (M, "                order.change_sent_at = self._clock()\n                self._touch(order)\n                self._emit(\n                    EventKind.MODIFY_UNCONFIRMED", "                self._touch(order)\n                self._emit(\n                    EventKind.MODIFY_UNCONFIRMED"),
 (M, "        except BrokerError as exc:\n            if order.pending is Pending.MODIFY:\n                order.pending = Pending.NONE\n            raise OrderError(exc.reason) from None", "        except BrokerError as exc:\n            raise OrderError(exc.reason) from None"),
 (M, "        order.terms_at = self._clock()  # broker terms older than this", "        pass  # broker terms older than this"),
 (M, "            if since is not None and update.ts < since:", "            if False:"),
 (M, "        if not current:\n            since =", "        if False:\n            since ="),
 (M, "        return terms if terms != r or doubt else None", "        return terms if terms != r else None"),
 (M, "            if update.filled_qty == order.filled_qty and terms is None:", "            if update.filled_qty == order.filled_qty:"),
 (M, "            order.request = terms\n", "\n"),
 (M, "        if order.cancel_requested and not order.cancel_unclear:\n            return order.view()", "        if order.cancel_requested:\n            return order.view()"),
 (M, "            order.cancel_unclear = True\n            order.change_sent_at = self._clock()", "            order.cancel_requested = order.cancel_sent = False\n            order.pending = Pending.NONE\n            order.change_sent_at = self._clock()"),
 (M, "                self._cancel_did_not_take(snapshot)\n", "\n"),
 (M, "        if order is None or not order.cancel_unclear or order.status.is_final:", "        if order is None or order.status.is_final:"),
 (M, "                cancel_unclear=pending is Pending.CANCEL,", "                cancel_unclear=False,"),
 (M, "                change_sent_at=view.updated_at if pending is Pending.MODIFY else None,", "                change_sent_at=None,"),
 (M, "                self.apply(snapshot, current=True)", "                self.apply(snapshot)"),
 (G, "            if self._now() - started >= WAITED_S:", "            if False:"),
 (G, '                    if getattr(earlier, "recheck_after_wait", False):', '                    if True:'),
 (R, "    recheck_after_wait = True", "    recheck_after_wait = False"),
 (K, '        "price_paise": price or None,', '        "price_paise": price,'),
 (K, '        "qty": int(qty) if qty not in (None, "") else None,', '        "qty": None,'),
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
    if survived: print("SURVIVED:", f, a[:90].replace("\n", "|"), "->", b[:40].replace("\n", "|"))
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
