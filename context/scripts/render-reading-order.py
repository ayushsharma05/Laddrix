"""Keep docs/guide/reading-order.md complete (#439): every code file once, under its own
folder's heading, numbered 1..N in each part.

What it keeps: the order of the headings and of the files under them (the order to read
them in). What it writes: each file's own first sentence beside it (its docstring or top
comment, so a description can never go stale); a file listed under another folder's heading
moves to its own folder's heading (made after the one it was under); a file not listed is
added at the end of its folder's last heading; a listed file that is gone is dropped; the
numbers and each part's file count.

    python3 context/scripts/render-reading-order.py          # rewrites the file
    python3 context/scripts/render-reading-order.py --check  # exit 1 if it would change
"""
import re
import subprocess
import sys
from pathlib import Path

DOC = Path("docs/guide/reading-order.md")
PARTS = {"Part A": ("src/laddrix/", lambda f: f.endswith(".py") and not f.endswith("__init__.py")),
         "Part B": ("ui/src/", lambda f: re.search(r"\.(ts|tsx)$", f) and ".test." not in f
                    and "/test/" not in f and not f.endswith("testdata.ts"))}  # fmt: skip
ENTRY = re.compile(r"^\d+[a-z]?\. \[ \] `([^`]+)` — ?(.*)$")
HEADING = re.compile(r"^\*\*(.+)/\*\*$")


def files(prefix, keep):
    out = subprocess.run(["git", "ls-files", prefix], capture_output=True, text=True).stdout
    return [f for f in out.split() if keep(f)]


def folder(path, prefix):
    rest = path[len(prefix):]
    return rest.rsplit("/", 1)[0] if "/" in rest else "(top level)"


def first_sentence(path):
    """The file's own first sentence: its docstring (Python) or its top comment (TypeScript),
    joined across wrapped lines."""
    text = Path(path).read_text(encoding="utf-8")
    words = []
    if path.endswith(".py"):
        m = re.match(r'\s*(?:#[^\n]*\n\s*)*"""(.*?)(?:\n\s*\n|""")', text, re.S)
        words = m.group(1).split() if m else []
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith(("//", "/*", "*")) or line in ("//", "/*", "*", "*/", "/**"):
                if words or (line and not line.startswith(("//", "/*", "*"))):
                    break
                continue
            words += line.lstrip("/*").strip().removesuffix("*/").split()
    paragraph = " ".join(words)
    m = re.search(r"^(.+?[.!?])(?=\s|$)", paragraph)
    sentence = m.group(1) if m else paragraph
    return sentence if len(sentence) <= 180 else sentence[:177].rsplit(" ", 1)[0] + "…"


def render(text):
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        part = next((p for p in PARTS if line.startswith(f"## {p} ")), None)
        if part is None:
            out.append(line)
            i += 1
            continue
        prefix, keep = PARTS[part]
        j = i + 1
        while j < len(lines) and not lines[j].startswith("## "):
            j += 1
        out.extend(render_part(lines[i:j], prefix, files(prefix, keep)))
        i = j
    return "\n".join(out) + "\n"


def render_part(block, prefix, present):
    head, sections, current = [block[0]], [], None  # sections: [heading, [(path, words)]]
    tail = []
    for line in block[1:]:
        m, e = HEADING.match(line), ENTRY.match(line)
        if m:
            current = [m.group(1), []]
            sections.append(current)
        elif e and current is not None:
            current[1].append((e.group(1), e.group(2)))
        elif current is None:
            head.append(line)
        elif line.strip() and not line.startswith("   "):
            tail.append(line)  # anything else after the lists: kept at the end
        elif line.startswith("   ") and current[1]:  # a wrapped description
            path, words = current[1][-1]
            current[1][-1] = (path, words + " " + line.strip())
    alive = set(present)
    for section in list(sections):  # the objects, not positions: headings may be inserted
        name, entries = section
        wrong = [x for x in entries if folder(x[0], prefix) != name and x[0] in alive]
        section[1] = [x for x in entries if folder(x[0], prefix) == name and x[0] in alive]
        for x in wrong:
            target = folder(x[0], prefix)
            home = next((s for s in sections if s[0] == target), None)
            if home is None:
                home = [target, []]
                sections.insert(sections.index(section) + 1, home)
            home[1].append(x)
    placed = {p for _, entries in sections for p, _ in entries}
    for path in present:
        if path in placed:
            continue
        name = folder(path, prefix)
        homes = [s for s in sections if s[0] == name]
        home = homes[-1] if homes else None
        if home is None:
            home = [name, []]
            sections.append(home)
        home[1].append((path, ""))
    sections = [s for s in sections if s[1]]
    count = sum(len(s[1]) for s in sections)
    title = re.sub(r", \d+ files$", f", {count} files", head[0])
    out = [title, *head[1:]]
    while out and not out[-1].strip():
        out.pop()
    n = 0
    for name, entries in sections:
        out += ["", f"**{name}/**", ""]
        for path, _ in entries:
            n += 1
            out.append(f"{n}. [ ] `{path}` — {first_sentence(path)}".rstrip())
    if tail:
        out += ["", *tail]
    out.append("")
    return out


if __name__ == "__main__":
    old = DOC.read_text(encoding="utf-8")
    new = render(old)
    if "--check" in sys.argv:
        sys.exit(0 if new == old else 1)
    DOC.write_text(new, encoding="utf-8")
    print("reading order: up to date" if new == old else "reading order: rewritten")
