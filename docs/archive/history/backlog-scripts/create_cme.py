import json, subprocess, time, sys
sys.path.insert(0, '.')
from cme_backlog import MILESTONES, LABELS, I
R = "repos/ayushsharma05/MockCME"
def api(method, path, payload=None):
    cmd = ["gh", "api", "-X", method, path]
    p = subprocess.run(cmd + (["--input", "-"] if payload else []), input=json.dumps(payload) if payload else None, capture_output=True, text=True)
    if p.returncode: raise SystemExit(f"FAIL {path}: {p.stdout} {p.stderr}")
    return json.loads(p.stdout) if p.stdout.strip() else None
# remove default labels
for l in api("GET", f"{R}/labels?per_page=100"):
    api("DELETE", f"{R}/labels/{l['name'].replace(' ', '%20')}")
for name, color, desc in LABELS:
    api("POST", f"{R}/labels", {"name": name, "color": color, "description": desc})
ms = {}
for title, desc in MILESTONES:
    ms[title] = api("POST", f"{R}/milestones", {"title": title, "description": desc})["number"]
for n, (m, labels, title, body) in enumerate(I, 1):
    got = api("POST", f"{R}/issues", {"title": title, "body": body, "labels": labels, "milestone": ms[m]})["number"]
    if got != n: raise SystemExit(f"number mismatch: expected {n} got {got}")
    print(got, title, flush=True)
    time.sleep(1.2)
