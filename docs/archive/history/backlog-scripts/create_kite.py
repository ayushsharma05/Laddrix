import json, re, subprocess, sys, time
sys.path.insert(0, '.')
from kite_backlog import MILESTONES, LABELS, I
R = "repos/ayushsharma05/MockTrade"
START = int(sys.argv[1])
def api(method, path, payload=None):
    p = subprocess.run(["gh", "api", "-X", method, path] + (["--input", "-"] if payload else []),
                       input=json.dumps(payload) if payload else None, capture_output=True, text=True)
    if p.returncode: raise SystemExit(f"FAIL {path}: {p.stdout} {p.stderr}")
    return json.loads(p.stdout) if p.stdout.strip() else None
# placeholder ids -> real numbers
keys, counters = [], {}
for m, *_ in I:
    k = m.split(" ")[0]; counters[k] = counters.get(k, 0) + 1; keys.append(f"{k}.{counters[k]}")
num = {k: START + i for i, k in enumerate(keys)}
fix = lambda s: re.sub(r"#(K\d\.\d)", lambda mt: f"#{num[mt.group(1)]}", s)
existing = {l["name"] for l in api("GET", f"{R}/labels?per_page=100")}
for name, color, desc in LABELS:
    if name not in existing: api("POST", f"{R}/labels", {"name": name, "color": color, "description": desc})
ms = {}
for title, desc in MILESTONES:
    ms[title] = api("POST", f"{R}/milestones", {"title": title, "description": desc})["number"]
for k, (m, labels, title, body) in zip(keys, I):
    got = api("POST", f"{R}/issues", {"title": f"{k} {title}", "body": fix(body), "labels": labels + ["track: kite"], "milestone": ms[m]})["number"]
    if got != num[k]: raise SystemExit(f"number mismatch {k}: expected {num[k]} got {got}")
    print(got, k, title, flush=True); time.sleep(1.2)
json.dump(num, open('kite_numbers.json', 'w'))
