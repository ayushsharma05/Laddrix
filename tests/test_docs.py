"""The docs keep up with the code (#439).

The gaps before: 25 code files had been added since the reading order was written, and the
code map had no refdata/ at all. Nothing noticed.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\]\(([^)\s]+)\)")
SKIP = ("docs/archive/",)  # the paused track, kept as it was


def markdown_files() -> list[Path]:
    listed = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True,
                            text=True, check=True).stdout.split()  # fmt: skip
    return [ROOT / f for f in listed if not f.startswith(SKIP)]


def test_every_code_file_is_in_the_reading_order_in_its_own_folder():
    """The reading order lists every file once, under its folder, with the file's own first
    sentence beside it: render-reading-order.py --check finds nothing to change."""
    run = subprocess.run([sys.executable, "context/scripts/render-reading-order.py", "--check"],
                         cwd=ROOT)  # fmt: skip
    assert run.returncode == 0, (
        "docs/guide/reading-order.md is out of date: "
        "run python3 context/scripts/render-reading-order.py"
    )


def test_every_server_folder_is_in_the_code_map():
    code_map = (ROOT / "docs/guide/07-code-map.md").read_text(encoding="utf-8")
    folders = sorted(p.name for p in (ROOT / "src/mocktrade").iterdir()
                     if p.is_dir() and (p / "__init__.py").exists())  # fmt: skip
    missing = [f for f in folders if f"`{f}/" not in code_map]
    assert missing == [], f"the code map (docs/guide/07-code-map.md) has no {missing}"


def test_every_link_between_the_docs_leads_somewhere():
    broken = []
    for doc in markdown_files():
        text = re.sub(r"```.*?```", "", doc.read_text(encoding="utf-8"), flags=re.S)
        for target in LINK.findall(text):
            if re.match(r"[a-z]+:", target) or target.startswith(("#", "<")):
                continue  # a web address, a mail link or a place on the same page
            path = target.split("#")[0]
            if path and not (doc.parent / path).exists():
                broken.append(f"{doc.relative_to(ROOT)} → {target}")
    assert broken == []
