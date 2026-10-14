import json, subprocess, sys, time
sys.path.insert(0, '.')
import backlog as b
R = "repos/ayushsharma05/MockTrade"
def api(method, path, data):
    p = subprocess.run(["gh", "api", "-X", method, path, "--input", "-"], input=json.dumps(data),
                       capture_output=True, text=True)
    if p.returncode != 0:
        return None, p.stderr.strip() or p.stdout.strip()
    return json.loads(p.stdout), None
# labels
for name, color, desc in b.LABELS:
    r, e = api("POST", f"{R}/labels", {"name": name, "color": color, "description": desc})
    print("label", name, "ok" if r else f"skip ({e[:60]})")
# milestones
ms = []
for title, desc in b.MILESTONES:
    r, e = api("POST", f"{R}/milestones", {"title": title, "description": desc})
    if not r: sys.exit(f"milestone failed: {title}: {e}")
    ms.append(r["number"]); print("milestone", r["number"], title)
# issues
for i, (title, m, labels, body) in enumerate(b.I):
    expect = i + 3
    r, e = api("POST", f"{R}/issues", {"title": title, "body": body, "milestone": ms[m], "labels": labels})
    if not r: sys.exit(f"issue failed at expected #{expect}: {e}")
    print("issue", r["number"], "(expected", expect, ")", title[:50])
    if r["number"] != expect: sys.exit("NUMBER MISMATCH - stopping")
    time.sleep(1.2)
