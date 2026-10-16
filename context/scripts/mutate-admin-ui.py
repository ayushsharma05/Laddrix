"""#282 mutation check: break one rule of the admin screen (the limits form from the spec, the
second clicks, what is shown once, read-only for risk, the first-login password page, the role
filter on Widgets); the UI tests must notice. Restores each file in `finally`; a hang counts as
caught. Run alone. Every anchor is checked to be unique first."""
import subprocess

M = "ui/src/widgets/admin/model.ts"
A = "ui/src/widgets/admin/Admin.tsx"
P = "ui/src/shell/SetPassword.tsx"
S = "ui/src/state/session.ts"
APP = "ui/src/App.tsx"
R = "ui/src/workspace/registry.ts"
CMD = ["npx", "vitest", "run", "src/widgets/admin", "src/shell/SetPassword.test.tsx", "src/workspace/registry.test.ts", "src/state"]
MUTANTS = [
 (M, "  const paise = Number(m[1]) * 100 + Number((m[2] ?? \"\").padEnd(2, \"0\") || \"0\");", "  const paise = Number(m[1]) * 100 + Number(m[2] ?? \"0\");"),
 (M, "  const m = /^(\\d+)(?:\\.(\\d{1,2}))?$/.exec(clean);", "  const m = /^(\\d+)(?:\\.(\\d+))?$/.exec(clean);"),
 (M, "  return text.endsWith(\".00\") ? text.slice(0, -3) : text;", "  return text;"),
 (M, "    else if (LISTS.has(p.kind)) out[p.name] = Array.isArray(v) ? [...v] : [];", "    else if (LISTS.has(p.kind)) out[p.name] = Array.isArray(v) ? (v as string[]) : [];"),
 (M, "    const list = Array.isArray(raw) ? [...new Set(raw)].sort() : [];", "    const list = Array.isArray(raw) ? [...raw] : [];"),
 (M, "    if (p.min !== null && list.length < p.min) return", "    if (false) return"),
 (M, "    if (p.max !== null && list.length > p.max) return", "    if (false) return"),
 (M, "      if (bad.length) return { ok: false, problem: `${p.label}: ${bad.join", "      if (false) return { ok: false, problem: `${p.label}: ${bad.join"),
 (M, "  if (p.min !== null && value < p.min) return { ok: false, problem: `${p.label} must be at least ${shown(p.min)}` };", "  if (p.min !== null && value <= p.min - 1) return { ok: true, value };"),
 (M, "  if (p.max !== null && value > p.max) return { ok: false, problem: `${p.label} must be at most ${shown(p.max)}` };", ""),
 (M, "    if (!/^\\d+$/.test(text.replace(/,/g, \"\"))) return", "    if (false) return"),
 (M, "  return { limits: problems.length ? null : limits, problems };", "  return { limits, problems };"),
 (M, "    if (title && last?.title === title) last.params.push(p);", "    if (false) last!.params.push(p);"),
 (M, "  if (u.locked_until && Date.parse(u.locked_until) > now) return", "  if (u.locked_until) return"),
 (M, "  if (!a.active) return { text: \"off: closing trades only\", tone: \"dim\" };", ""),
 (M, "    if (url.protocol !== \"otpauth:\") return null;", ""),
 (A, "                      ? u.id !== me && second(", "                      ? second("),
 (A, "    armed === key ? (", "    true ? ("),
 (A, "                      armed === a.id ? (", "                      true ? ("),
 (A, "    setPreview(null); // what is saved must be what was previewed", "    // (kept)"),
 (A, "            (looser && !armed ? (", "            (false ? ("),
 (A, "      .put<LimitsSaved>(path, { limits: preview.limits })", "      .put<LimitsSaved>(path, { limits: parseLimits(spec, inputs!).limits })"),
 (A, "  const canChange = roles.includes(\"admin\");", "  const canChange = roles.includes(\"admin\") || roles.includes(\"risk\");"),
 (A, "  if (!canRead) return <div className=\"empty-note\">For admin and risk users.</div>;", ""),
 (A, "      <button type=\"button\" className=\"btn small\" onClick={onDone}>", "      <button type=\"button\" className=\"btn small\" onClick={() => undefined}>"),
 (A, "    if (!limits) return;\n    setBusy(true);", "    setBusy(true);"),
 (P, "    if (busy || differs) return;", "    if (busy) return;"),
 (P, "  const differs = again !== \"\" && again !== next;", "  const differs = false;"),
 (S, "      if (!me.user.must_change_password) this.stream.start(); // the server refuses it until then", "      this.stream.start();"),
 (S, "    if (!me.user.must_change_password) this.stream.start();\n  }", "  }"),
 (APP, "  return me.user.must_change_password ? <SetPassword /> : <Shell />;", "  return <Shell />;"),
 (R, "  return allWidgets().filter((w) => !w.roles || w.roles.some((r) => roles.includes(r)));", "  return allWidgets();"),
]
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(CMD, capture_output=True, timeout=300, cwd="ui")
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
