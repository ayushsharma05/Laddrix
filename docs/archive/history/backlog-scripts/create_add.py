import json, subprocess, sys, time
sys.path.insert(0, '.')
import backlog_add as a
R = "repos/ayushsharma05/MockTrade"
def api(method, path, data=None):
    args = ["gh", "api", "-X", method, path]
    p = subprocess.run(args + (["--input", "-"] if data is not None else []), input=json.dumps(data) if data is not None else None, capture_output=True, text=True)
    if p.returncode != 0: return None, (p.stderr or p.stdout).strip()
    return json.loads(p.stdout), None
ms = {}
for key, (title, desc) in zip(["v06", "v07", "v08"], a.NEW_MILESTONES):
    r, e = api("POST", f"{R}/milestones", {"title": title, "description": desc})
    if not r: sys.exit(f"milestone failed {title}: {e}")
    ms[key] = r["number"]; print("milestone", r["number"], title)
existing, _ = api("GET", f"{R}/milestones?state=all&per_page=50")
ms["v10"] = [m["number"] for m in existing if m["title"].startswith("v1.0")][0]
for i, (title, m, labels, body) in enumerate(a.A):
    expect = 73 + i
    r, e = api("POST", f"{R}/issues", {"title": title, "body": body, "milestone": ms[m], "labels": labels})
    if not r: sys.exit(f"issue failed at expected #{expect}: {e}")
    print("issue", r["number"], "expected", expect, title[:45])
    if r["number"] != expect: sys.exit("NUMBER MISMATCH - stopping")
    time.sleep(1.2)
r, e = api("POST", f"{R}/issues", {"title": "Architecture coverage: every reference box built", "body": a.COVERAGE,
          "milestone": ms["v10"], "labels": ["type:design"]})
print("coverage issue", r["number"] if r else e)
# patch existing issues
def patch(n, old, new):
    cur, e = api("GET", f"{R}/issues/{n}")
    b = cur["body"]
    if old not in b: print(f"#{n}: anchor not found"); return
    r, e = api("PATCH", f"{R}/issues/{n}", {"body": b.replace(old, new, 1)})
    print(f"#{n} patched" if r else f"#{n} failed {e}")
patch(23, "Create `docs/design/architecture.md`.",
      "Create `docs/design/architecture.md`.\n4. Add a **coverage table**: every box of the reference one-chart → the Laddrix component that plays its role → issue number. Nothing may be left unmapped (see #119).")
patch(65, "## Depends on\n#64", "## Depends on\n#112")
patch(71, "## Depends on\n#70", "## Depends on\n#70, #112, #113, #114, #115, #116, #117, #118, #119")
