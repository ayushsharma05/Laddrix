"""What we ship is what was reviewed (#365).

    pyproject.toml (ranges) ─► requirements.lock (exact versions, each with its hashes)
                             ─► the image installs only from the lock, --require-hashes
    CI: the lock installed first (tests run against what ships); pip-audit on the lock;
        gitleaks over the whole history; CycloneDX SBOMs kept with each run (a tag's = a
        release's)

These tests keep the pieces from drifting apart: a new dependency added to pyproject.toml but
not to the lock, a pin outside its range, a pin without a hash, a Dockerfile or CI step that
stops using them, or an update path in the desktop app that nobody signs.
"""

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def locked() -> dict[str, tuple[Version, list[str]]]:
    """name -> (version, hashes), from requirements.lock."""
    text = (ROOT / "requirements.lock").read_text()
    pins = {}
    for block in re.split(r"\n(?=\S)", text):
        first = block.split("\n", 1)[0]
        if first.startswith("#") or not first.strip():
            continue
        m = re.match(r"([A-Za-z0-9_.-]+)==([^\s;\\]+)", first)
        assert m, f"not an exact pin: {first}"
        pins[canonicalize_name(m[1])] = (
            Version(m[2]),
            re.findall(r"--hash=sha256:[0-9a-f]{64}", block),
        )
    return pins


def test_every_dependency_is_pinned_within_its_range_with_hashes():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    pins = locked()
    for spec in project["dependencies"]:
        need = Requirement(spec.split("#")[0])
        name = canonicalize_name(need.name)
        assert name in pins, (
            f"{need.name} is in pyproject.toml but not in requirements.lock: re-lock"
        )
        assert need.specifier.contains(pins[name][0]), f"{need.name} {pins[name][0]} breaks {spec}"
        for extra in need.extras:  # uvicorn[standard] brings its own
            assert extra == "standard" and "uvloop" in pins and "httptools" in pins
    for name, (_, hashes) in pins.items():
        assert hashes, f"{name} has no hash"


def test_the_image_and_ci_install_only_what_is_locked():
    docker = (ROOT / "Dockerfile").read_text()
    assert "pip install --require-hashes -r requirements.lock" in docker
    assert "pip install --no-deps ." in docker  # our code adds nothing unlocked
    sent = (ROOT / ".dockerignore").read_text().splitlines()
    assert "!requirements.lock" in sent  # the build context lets only listed files in
    assert not re.search(r"pip install \.(?!\S)", docker.replace("--no-deps .", ""))
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "pip install --require-hashes -r requirements.lock" in ci
    assert "pip-audit -r requirements.lock" in ci and "--strict" in ci
    assert "gitleaks git" in ci and "sha256sum --check" in ci  # a checked download
    assert "fetch-depth: 0" in ci  # gitleaks sees every commit, not the last one
    for sbom in ("server", "screens", "desktop"):
        assert f"sbom/mocktrade-{sbom}.cdx.json" in ci
    assert 'tags: ["v*"]' in ci  # a release tag runs it: its SBOMs are the release's
    assert "github.ref_type == 'tag' && github.ref_name || github.sha" in ci  # a valid name


def test_every_accepted_secret_finding_is_a_test_file():
    for line in (ROOT / ".gitleaksignore").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            path = line.split(":")[1]
            assert ".test." in path or path.startswith("tests/"), line


def test_the_desktop_app_has_no_unsigned_update_path():
    """No auto-update today. Adding one needs signed releases checked before install
    (docs/runbooks/release.md, "The desktop app"): this test is the reminder."""
    sources = [p for p in (ROOT / "ui/desktop/src").rglob("*.ts")]
    sources.append(ROOT / "ui/desktop/package.json")
    for path in sources:
        text = path.read_text()
        assert "autoUpdater" not in text and "electron-updater" not in text, path
    assert "publish" not in (ROOT / "ui/desktop/electron-builder.yml").read_text()


def test_update_proposals_are_grouped_and_never_major():
    """#392: react without react-dom broke the screens; majors arrived unasked."""
    import yaml

    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    folders = {(u["package-ecosystem"], u["directory"]) for u in config["updates"]}
    # the Compose images (Caddy, Prometheus, Grafana) too, since #439
    assert folders == {("pip", "/"), ("npm", "/ui"), ("npm", "/ui/desktop"),
                       ("github-actions", "/"), ("docker", "/"),
                       ("docker-compose", "/deploy")}  # fmt: skip
    for update in config["updates"]:
        ignored = {t for rule in update.get("ignore", []) if rule["dependency-name"] == "*"
                   for t in rule["update-types"]}  # fmt: skip
        assert "version-update:semver-major" in ignored, update["package-ecosystem"]
        if update["package-ecosystem"] != "docker":  # one image line: nothing to group
            groups = update["groups"].values()
            assert any(set(g["update-types"]) == {"minor", "patch"} for g in groups)
