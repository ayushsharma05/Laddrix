"""#284 mutation check: break one rule of the keyboard shortcuts (how a key press is named, which
action a key starts, scopes, STOP's fixed key, browser keys, clashes, saved choices, the editor,
the ticket's keys and hint, the per-user setting on the server); the tests must notice. Restores
each file in `finally`; a hang counts as caught. Run alone. Every anchor is checked to be unique."""
import subprocess

M = "ui/src/keys/model.ts"
B = "ui/src/keys/bindings.ts"
E = "ui/src/widgets/keys/KeysEditor.tsx"
T = "ui/src/widgets/ticket/Ticket.tsx"
U = "src/mocktrade/server/users.py"
UI = (["npx", "vitest", "run", "src/keys", "src/widgets/keys", "src/widgets/ticket"], "ui")
PY = (["python3", "-m", "pytest", "-q", "-x", "tests/test_server.py", "-k", "settings"], ".")
MUTANTS = [
 (M, "  if ((e.altKey || e.ctrlKey) && /^Key[A-Z]$/.test(e.code)) key = e.code.slice(3);", ""),
 (M, "  if (e.ctrlKey || e.metaKey) parts.push(\"Ctrl\");", "  if (e.ctrlKey) parts.push(\"Ctrl\");"),
 (M, "  if (e.shiftKey && !(printable && parts.length === 0)) parts.push(\"Shift\");", "  if (e.shiftKey) parts.push(\"Shift\");"),
 (M, "  if (MODIFIERS.has(e.key)) return null;", ""),
 (M, "  return action.fixed && !chosen.includes(action.fixed) ? [...chosen, action.fixed] : chosen;", "  return chosen;"),
 (M, "  for (const want of scope === \"global\" ? [\"global\"] : [scope, \"global\"]) {", "  for (const want of [scope]) {"),
 (M, "  if (RESERVED.has(chord)) return", "  if (false) return"),
 (M, "(a.scope === action.scope || a.scope === \"global\" || action.scope === \"global\"),", "(a.scope === action.scope),"),
 (M, "  if (action.fixed === chord) return bindings;", ""),
 (M, "  return { ...bindings, [actionId]: now.includes(chord) ? now : [...now, chord] };", "  return { ...bindings, [actionId]: [...now, chord] };"),
 (M, "    if (!ACTIONS.some((a) => a.id === id) || !Array.isArray(keys)) continue;", "    if (!Array.isArray(keys)) continue;"),
 (M, "    if (keys.length && !list.length) continue; // nothing usable left: the defaults, never a dead key", ""),
 (M, "    if (out[id]!.some((k) => k !== action.fixed && refusal(id, k, without) !== null)) delete out[id]; // a clash: defaults", ""),
 (M, "  return first ? first.split(\"+\").map((part) => SHOWN[part] ?? part).join(\"+\") : \"–\";", "  return first ?? \"–\";"),
 (B, "      this.set(before);\n      throw err;", "      throw err;"),
 (B, "      await this.rest.put(PATH, { value: Object.keys(next).length ? { bindings: next } : null });", "      await this.rest.put(PATH, { value: { bindings: next } });"),
 (B, "      this.set(cleanBindings(answer.value?.bindings));", "      this.set((answer.value?.bindings ?? {}) as Bindings);"),
 (E, "    if (why) {\n      setProblem(why);\n      return;\n    }", "    if (why) setProblem(why);"),
 (E, "    if (!chord) return; // a modifier on its own: wait for the key", "    if (!chord) return setCapturing(null);"),
 (E, "    if (e.key === \"Escape\") {\n      setCapturing(null);\n      return;\n    }", ""),
 (E, "          (armed ? (", "          (true ? ("),
 (E, "                      k === a.fixed ? (", "                      false ? ("),
 (T, "        else if (action === \"ticket-sell\") setSide(\"SELL\");", ""),
 (T, "        if (action === \"ticket-send\" && !(e.target instanceof HTMLButtonElement)) send();", "        if (e.key === \"Enter\" && !(e.target instanceof HTMLButtonElement)) send();"),
 (T, "`${k(\"ticket-send\")} sends · ${k(\"ticket-clear\")} clears", "`Enter sends · ${k(\"ticket-clear\")} clears"),
 (U, "SETTINGS = frozenset({\"keys\", \"theme\"})", "SETTINGS = frozenset({\"keys\", \"theme\", \"secrets\"})"),
 (U, "            if len(value.encode()) > MAX_SETTING_BYTES:", "            if False:"),
]
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    cmd, cwd = UI if f.startswith("ui/") else PY
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300, cwd=cwd)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
