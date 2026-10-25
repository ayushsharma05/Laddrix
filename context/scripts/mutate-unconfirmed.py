"""#259 mutation check: break one rule for unanswered orders (one list proves nothing; enough
lists over enough time; the evidence in the reason; asking again by itself and stopping once
settled; after a restart; unknown broker statuses kept; a person settling it, only after
confirming); the tests must notice. Restores each file in `finally`. Run alone."""
import subprocess

M = "src/mocktrade/orders/manager.py"
K = "src/mocktrade/broker/kite/orders.py"
R = "src/mocktrade/recovery.py"
ROUTES_TRADING = "src/mocktrade/server/routes/trading.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_unconfirmed.py",
         "tests/test_order_manager.py", "tests/test_kite_orders.py", "tests/test_kite_broker.py",
         "tests/test_event_log.py", "tests/test_recovery.py", "tests/test_server.py"]
MUTANTS = [
 (M, "UNSEEN_CHECKS = 3", "UNSEEN_CHECKS = 1"),
 (M, "UNSEEN_AFTER = timedelta(seconds=60)", "UNSEEN_AFTER = timedelta(seconds=0)"),
 (M, "            if len(order.unseen) >= UNSEEN_CHECKS and now - order.created_at >= UNSEEN_AFTER:", "            if len(order.unseen) >= UNSEEN_CHECKS or now - order.created_at >= UNSEEN_AFTER:"),
 (M, "            order.unseen.append(now)  # one more list without it: evidence, not proof", "            order.unseen = [now]"),
 (M, "            self.follow_unconfirmed()  # ask the broker again, by itself", "            pass"),
 (M, '            if self._in_doubt(order) and not order.following:', '            if self._in_doubt(order):'),
 (M, '                if not self._in_doubt(order):\n                    return  # settled', '                if False:\n                    return  # settled'),
 (M, "                    self.reconcile(await self._broker.orders())", "                    await self._broker.orders()"),
 (M, '            if self._in_doubt(order):\n                what = (', '            if False:\n                what = ('),
 (M, "        if not self._unsettled(order):\n            raise OrderError(\"only an unconfirmed", "        if False:\n            raise OrderError(\"only an unconfirmed"),
 (M, '        said = f" ({update.reason})" if update.reason else ""', '        said = ""'),
 (K, "            status=S.PENDING,\n            filled_qty=int(order.get(\"filled_quantity\") or 0),", "            status=S.OPEN,\n            filled_qty=int(order.get(\"filled_quantity\") or 0),"),
 (R, "    orders.follow_unconfirmed()  # the manager asks the broker again, by itself", "    pass"),
 (ROUTES_TRADING, "        if not body.confirm:\n            raise HTTPException(422, \"check the broker's own", "        if False:\n            raise HTTPException(422, \"check the broker's own"),
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
    if survived: print("SURVIVED:", f, a[:90], "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
