"""Every mutation script still breaks what it says it breaks (#448).

A mutation script (`context/scripts/mutate-*.py`) changes one piece of code at a time, runs the
tests, and counts a mutant as caught when they fail. A mutant whose code has since changed
matches nothing: the script skips it (or breaks the first of two places), and quietly checks
less than it says. This reads every script, without running it: each mutant's text must be in
its file exactly once.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted((ROOT / "context/scripts").glob("mutate-*.py"))


def paths(tree: ast.Module) -> dict[str, str]:
    """The script's file names: `A = "src/…"` and `A, B = "src/…", "src/…"`."""
    found: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            names = target.elts if isinstance(target, ast.Tuple) else [target]
            values = node.value.elts if isinstance(node.value, ast.Tuple) else [node.value]
            for name, value in zip(names, values, strict=False):
                if isinstance(name, ast.Name) and isinstance(value, ast.Constant):
                    if isinstance(value.value, str) and "/" in value.value:
                        found[name.id] = value.value
    return found


def mutants(script: Path) -> list[tuple[int, str, str]]:
    """(line, file, text to change) of each mutant in the script's top-level lists."""
    tree = ast.parse(script.read_text())
    names = paths(tree)
    out = []
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.List)):
            continue
        for item in node.value.elts:
            if not (isinstance(item, ast.Tuple) and len(item.elts) >= 3):
                continue
            where, old = item.elts[0], item.elts[1]
            file = names.get(where.id) if isinstance(where, ast.Name) else None
            if isinstance(where, ast.Constant) and isinstance(where.value, str):
                file = where.value
            if file and isinstance(old, ast.Constant) and isinstance(old.value, str):
                out.append((item.lineno, file, old.value))
    return out


def test_the_scripts_are_read():
    assert len(SCRIPTS) > 80
    assert sum(len(mutants(s)) for s in SCRIPTS) > 1000  # every script's mutants, not none


def test_every_mutant_matches_its_code_exactly_once():
    stale = []
    for script in SCRIPTS:
        for line, file, old in mutants(script):
            target = ROOT / file
            if not target.is_file():  # a screens' script run from inside ui/
                target = ROOT / "ui" / file
            count = target.read_text().count(old) if target.is_file() else -1
            if count != 1:
                where = "no such file" if count < 0 else f"found {count} times"
                stale.append(f"{script.name}:{line} {file}: {where}: {old[:60]!r}")
    assert stale == [], "\n".join(stale)
