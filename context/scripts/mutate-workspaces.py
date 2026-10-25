"""#394 (368a) mutation check: break one workspace rule (the rule in the three checks, the
store's migration and firm checks, STOP per workspace, the admin screens, the stream, the
audit names, surveillance, ops alerts, backtests, the operator-only controls); the tests must
notice. Runs the tests unmutated first. Restores each file. Run alone."""
import subprocess

D = "src/laddrix/domain/accounts.py"
U = "src/laddrix/server/users.py"
P = "src/laddrix/platform.py"
C = "src/laddrix/server/context.py"
ROUTES_OPS = "src/laddrix/server/routes/ops.py"
ROUTES_RISK = "src/laddrix/server/routes/risk.py"
ROUTES_SESSION = "src/laddrix/server/routes/session.py"
M = "src/laddrix/server/admin.py"
S = "src/laddrix/server/stream.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_workspaces.py"]
MUTANTS = [
 (S, "            message[\"scope\"] = \"firm\"  # to its own people it is simply their firm's STOP\n", ""),
 (ROUTES_RISK, "                if in_workspace(user, a.account_id)]  # fmt: skip", "                ]  # fmt: skip"),
 (ROUTES_RISK, "        if found is None or not in_workspace(user, found.account_id):", "        if found is None:"),
 (ROUTES_OPS, "                if a.account_id is None or a.account_id in mine\n", ""),
 (D, "    return user.workspace == account.workspace", "    return True"),
 (D, "    if not user.active or not same_workspace(user, account):", "    if not user.active:"),
 (D, "    if not same_workspace(user, account):\n        return False\n    if not (user.has_role(Role.TRADER) and account.active):", "    if not (user.has_role(Role.TRADER) and account.active):"),
 (U, "                known = {r[\"id\"] for r in db.execute(\"SELECT id FROM users WHERE workspace = ?\",", "                known = {r[\"id\"] for r in db.execute(\"SELECT id FROM users WHERE ? IS NOT NULL\","),
 (U, "                        owner.workspace,\n", "                        \"main\",\n"),
 (U, "                        workspace,\n                    ),", "                        \"main\",\n                    ),"),
 (P, "        firm = self.firm_switch_of(account.workspace)  # its own firm's STOP, no other (#368)", "        firm = self.firm_switch"),
 (P, "        if workspace == MAIN:\n            return self.firm_switch", "        if True:\n            return self.firm_switch"),
 (ROUTES_RISK, "        runs = [r for r in platform.accounts.values() if r.account.workspace == user.workspace]", "        runs = list(platform.accounts.values())"),
 (ROUTES_RISK, "        firm = platform.firm_switch_of(user.workspace)\n", "        firm = platform.firm_switch\n"),
 (ROUTES_RISK, "        platform.firm_switch_of(user.workspace).reset(by=user.id)", "        platform.firm_switch.reset(by=user.id)"),
 (ROUTES_SESSION, "        \"firm_stopped\": platform.firm_switch_of(user.workspace).engaged,", "        \"firm_stopped\": platform.firm_switch.engaged,"),
 (C, "        if len(self.users.workspaces()) > 1:", "        if False:"),
 (M, "        return [e for e in users.users() if e[\"user\"].workspace == user.workspace]", "        return users.users()"),
 (M, "        if account is None or account.workspace != user.workspace:  # another firm's: none", "        if account is None:"),
 (M, "        return {\"accounts\": [account_dict(a) for a in users.accounts()\n                             if a.workspace == user.workspace],", "        return {\"accounts\": [account_dict(a) for a in users.accounts()],"),
 (M, "must_change=True, workspace=user.workspace", "must_change=True"),
 (S, "            if (connection.user.workspace == firm if firm is not None", "            if (True if firm is not None"),
]
if subprocess.run(TESTS, capture_output=True, timeout=300).returncode != 0:
    raise SystemExit("the tests fail (or a test file is missing) before any mutation: fix that first")
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
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
