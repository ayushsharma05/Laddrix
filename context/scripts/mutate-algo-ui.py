import subprocess, sys
F = {
 "src/widgets/algos/model.ts": [
  ('return m[1] === "-" ? -paise : paise;', 'return paise;'),
  ('qty: t.lots * t.lotSize', 'qty: t.lots'),
  ('if (p.max !== null && value > p.max)', 'if (false)'),
  ('const live = Number(isActive(b)) - Number(isActive(a));', 'const live = 0;'),
  ('else if (!("skip" in r)) params[p.name] = r.value;', 'else params[p.name] = (r as {value?: unknown}).value;'),
  ('alert: name === "unhedged" && value !== 0', 'alert: false'),
  ('export const canStop = (a: AlgoView) => a.state === "WORKING" || a.state === "PAUSED";', 'export const canStop = (a: AlgoView) => a.state === "WORKING";'),
 ],
 "src/widgets/algos/AlgoManager.tsx": [
  ('if (action !== "stop") {', 'if (true) {'),
  ('.then((view) => store.applyAlgos(accountId, [view]))', '.then(() => undefined)'),
  ('setValues(initialValues(chosen));', ''),
 ],
 "src/state/store.ts": [
  ('if (newer(a.updated_at, merged[a.algo_id]?.updated_at)) merged[a.algo_id] = a;', 'merged[a.algo_id] = a;'),
  ('      return { level: "ERROR", text: `${what}: ${detail}` };', '      return null;'),
 ],
}
caught = total = 0
for f, muts in F.items():
    src = open(f).read()
    for a, b in muts:
        assert a in src, a
        open(f, "w").write(src.replace(a, b, 1))
        r = subprocess.run(["npx", "vitest", "run", "src/widgets/algos", "src/state"], capture_output=True, text=True)
        total += 1
        if r.returncode: caught += 1
        else: print("SURVIVED:", f, a)
        open(f, "w").write(src)
print(f"{caught}/{total} caught")
