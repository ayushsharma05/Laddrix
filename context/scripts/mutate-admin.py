"""#281 mutation check: break one rule of the admin API (the limits spec, a limit edit reaching
the running account, a turned-off account, the last-admin and self-off guards, the reset
password gate, account changes, a late account's loops); the tests must notice. Restores each
file in `finally`; a hang counts as caught. Run alone. Every anchor is checked to be unique."""
import subprocess

LS = "src/mocktrade/risk/limits_spec.py"
SP = "src/mocktrade/algos/spec.py"
CK = "src/mocktrade/risk/checks.py"
PF = "src/mocktrade/platform.py"
US = "src/mocktrade/server/users.py"
AD = "src/mocktrade/server/admin.py"
SERVER_CONTEXT = "src/mocktrade/server/context.py"
DA = "src/mocktrade/domain/accounts.py"
SR = "src/mocktrade/server/stream.py"
CMD = ["python3", "-m", "pytest", "-q", "-x", "tests/test_admin.py", "tests/test_server.py",
       "tests/test_accounts.py", "tests/test_users.py", "tests/test_risk_checks.py", "tests/test_algo_engine.py"]
# (`new > old` → `>=` and `bool(new) and not old` → `bool(new)` are left out on purpose: a change
# only ever holds values that differ, so those mutants are equivalent)
MUTANTS = [
 (SP, "        size = len(value) if isinstance(value, list) else value  # lists: how many", "        size = 1 if isinstance(value, list) else value"),
 (SP, "        return sorted(set(value))  # each once, in a stable order", "        return list(value)"),
 (SP, "            bad = [v for v in value if v not in p.choices]", "            bad = []"),
 (LS, "        return not set(new) <= set(old)", "        return set(new) != set(old)"),
 (LS, "        return not set(old) <= set(new)", "        return False"),
 (LS, "    return bool(old) and not new  # \"off\"", "    return False  # \"off\""),
 (LS, "        if lots > new.max_position_lots:", "        if lots >= new.max_position_lots:"),
 (LS, "        if instrument_id in new.blocked_instruments and qty:", "        if instrument_id in new.blocked_instruments:"),
 (LS, "    if working_orders > new.max_open_orders:", "    if working_orders >= new.max_open_orders:"),
 (LS, "    if day_pnl_paise <= -new.max_daily_loss_paise:", "    if day_pnl_paise < -new.max_daily_loss_paise:"),
 (LS, "    usable = {k: v for k, v in raw.items() if k in _BY_NAME and not limits_from_dict({k: v})[1]}", "    usable = {k: v for k, v in raw.items() if k in _BY_NAME}"),
 (LS, "            duplicate_window_s=float(clean[\"duplicate_window_s\"]),", "            duplicate_window_s=1.0,"),
 (CK, "        if self.off_reason is None or self._closes(request, leaves, others):", "        if self.off_reason is None:"),
 (CK, "            or (self._off_problem(new, leaves, others) if grows else None)", "            or None"),
 (CK, "            or self._off_problem(request, request.qty, others)\n", "\n"),
 (PF, "            self.loss_watch.limit_paise = limits.max_daily_loss_paise", "            pass"),
 (PF, "                closer.max_lots = limits.max_order_lots", "                pass"),
 (PF, "        runtime.set_active(account.active)\n", "\n"),
 (PF, "        self._start_late(runtime)\n", "\n"),
 (PF, "            for task in late:\n                task.cancel()", "            for task in late:\n                pass"),
 (US, "                if not any(u.has_role(Role.ADMIN) for u in others):", "                if not others:"),
 (US, "            if not after.active:\n                db.execute(\"DELETE FROM sessions WHERE user_id = ?\", (user_id,))\n        return before, after", "        return before, after"),
 (US, "                \"UPDATE users SET password_hash = ?, must_change = 1, failures = 0, \"", "                \"UPDATE users SET password_hash = ?, must_change = 0, failures = 0, \""),
 (US, "            if row is None or not security.verify_password(row[\"password_hash\"], old):", "            if row is None:"),
 (US, "                       (user_id, security.token_digest(keep_token)))  # fmt: skip", "                       (user_id, \"\"))  # fmt: skip"),
 (US, "                traders = set(traders) - {before.owner_user_id}  # the owner trades it anyway", "                traders = set(traders)"),
 (US, "                if unknown:\n                    raise ValueError(f\"no such user", "                if False:\n                    raise ValueError(f\"no such user"),
 (US, "                if column not in have:", "                if False:"),
 (AD, "        if user_id == user.id and body.active is False:", "        if False:"),
 (AD, "        if not user.has_role(Role.ADMIN):\n            raise HTTPException(403, \"only an admin can change this\")", "        if not (user.has_role(Role.ADMIN) or user.has_role(Role.RISK)):\n            raise HTTPException(403, \"only an admin can change this\")"),
 (AD, "        if changed and not dry_run:", "        if changed:"),
 (AD, "                running.apply_limits(new)", "                pass"),
 (AD, "            running.set_active(after.active)", "            pass"),
 (AD, "            gone = sorted(i for i in new.blocked_instruments if platform.instruments.get(i) is None)", "            gone = []"),
 (AD, "        if starter is None or body.broker not in starter.brokers:", "        if starter is None:"),
 (AD, "        if unknown:\n            raise HTTPException(422, f\"no such user", "        if False:\n            raise HTTPException(422, f\"no such user"),
 (AD, "        if why:\n            starter.not_running[account.id] = why", "        if False:\n            starter.not_running[account.id] = why"),
 (SERVER_CONTEXT, "        if user.must_change_password and request.url.path not in PASSWORD_FIRST:", "        if False:"),
 (SERVER_CONTEXT, "        if trade and not can_manage(user, account):", "        if trade and not can_view(user, account):"),
 (DA, "    return user.id == account.owner_user_id or user.id in account.traders\n\n\ndef can_view", "    return True\n\n\ndef can_view"),
 (SR, "        if user is None or user.must_change_password:  # a reset password: change it first", "        if user is None:"),
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
