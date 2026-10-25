"""CI runs every check a contributor runs (#440, #442), and the checks stay switched on.

    the server:   ruff check · ruff format --check · mypy · pytest
    the screens:  typecheck · ESLint (TypeScript, React's hooks rules) · vitest · build
    the desktop:  typecheck · ESLint · vitest

A step dropped from CI, from `make lint` or from a package's scripts, or a hooks rule turned
down to a warning (CI passes on warnings), would let mistakes through without anyone
noticing.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def job(ci: str, name: str) -> str:
    """One job's text in ci.yml: from `  name:` to the next job."""
    m = re.search(rf"^  {name}:\n(.*?)(?=^  \S|\Z)", ci, re.S | re.M)
    assert m, f"no {name} job in ci.yml"
    return m.group(1)


def test_ci_runs_every_check():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    server = job(ci, "test")
    for step in ("ruff check .", "ruff format --check .", "run: mypy", "run: pytest"):
        assert step in server, step
    for name, steps in (("ui", ["typecheck", "lint", "test", "build"]),
                        ("desktop", ["typecheck", "lint", "test"])):  # fmt: skip
        text = job(ci, name)
        for step in steps:
            want = "npm test" if step == "test" else f"npm run {step}"
            assert want in text, (name, want)


def test_make_lint_runs_the_same_checks():
    lint = re.search(r"^lint:\n((?:\t.*\n)+)", (ROOT / "Makefile").read_text(), re.M)
    assert lint
    for step in ("ruff check .", "ruff format --check .", "mypy",
                 "cd ui && npm run typecheck && npm run lint",
                 "cd ui/desktop && npm run typecheck && npm run lint"):  # fmt: skip
        assert step in lint.group(1), step


def test_each_package_lints_its_own_code():
    for folder, config in (("ui", "eslint.config.js"), ("ui/desktop", "eslint.config.mjs")):
        scripts = json.loads((ROOT / folder / "package.json").read_text())["scripts"]
        assert scripts["lint"] == "eslint .", folder
        text = (ROOT / folder / config).read_text()
        assert "tseslint.configs.recommended" in text
        assert 'reportUnusedDisableDirectives: "error"' in text  # a stale exception fails too


def test_the_hooks_rules_are_errors_not_warnings():
    text = (ROOT / "ui/eslint.config.js").read_text()
    assert '"react-hooks/rules-of-hooks": "error"' in text
    assert '"react-hooks/exhaustive-deps": "error"' in text  # the plugin's default is a warning


def test_every_lint_exception_says_why():
    """A rule is turned off for one line only, with its reason on that line."""
    for path in [*(ROOT / "ui/src").rglob("*.ts*"), *(ROOT / "ui/desktop/src").rglob("*.ts")]:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if "eslint-disable" in line:
                where = f"{path.relative_to(ROOT)}:{n}"
                assert "eslint-disable-line" in line or "eslint-disable-next-line" in line, where
                assert re.search(r"eslint-disable(?:-next)?-line \S+ -- \S", line), where
