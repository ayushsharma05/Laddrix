"""#365 mutation check: break one supply-chain rule (the image and CI install from the lock with
hashes, a pin loses its hash or leaves its range, a dependency is missing from the lock, the
audit or secret scan or an SBOM is dropped, the history isn't fetched, an unsigned update path
appears); the tests must notice. Runs the tests unmutated first. Restores each file. Run alone."""
import subprocess

D = "Dockerfile"
C = ".github/workflows/ci.yml"
L = "requirements.lock"
P = "pyproject.toml"
I = ".gitleaksignore"
E = "ui/desktop/package.json"
K = ".dockerignore"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_supply_chain.py"]
MUTANTS = [
 (D, "RUN pip install --require-hashes -r requirements.lock \\\n    && pip install --no-deps . \\", "RUN pip install . \\"),
 (D, "    && pip install --no-deps . \\", "    && pip install . \\"),
 (C, "      - run: pip install --require-hashes -r requirements.lock # what the image ships (#365)\n", ""),
 (C, "pip-audit -r requirements.lock --no-deps --disable-pip --strict", "echo skipped"),
 (C, "          ./gitleaks git --redact --no-banner .", "          true"),
 (C, "          echo \"${GITLEAKS_SHA256}  gitleaks.tgz\" | sha256sum --check\n", ""),
 (C, "          fetch-depth: 0 # the whole history: a secret removed later is still leaked", "          fetch-depth: 1"),
 (C, "-o sbom/mocktrade-server.cdx.json", "-o sbom/server.json"),
 (C, "    tags: [\"v*\"] # a release: its SBOMs are kept with the run (docs/runbooks/release.md)\n", ""),
 (L, "pyotp==2.10.0 \\\n    --hash=sha256:1df2f6a1bcc3bb0716172a5215ddc2f8c7c7fd26a13df9927d52e1746934836c \\\n    --hash=sha256:d01e9703443616b03c57c700b5cbffd56a1f929c1b0f8f03131bc78c1fca9d3f\n", "pyotp==2.10.0\n"),
 (L, "httpx==0.28.1", "httpx==0.26.0"),
 (P, "    \"orjson>=3.10\",", "    \"orjson>=3.10\",\n    \"left-pad>=1\","),
 (I, "# Admin.test.tsx: fake one-time passwords returned by the scripted test server", "abc:src/mocktrade/server/app.py:generic-api-key:1"),
 (K, "!requirements.lock\n", ""),
 (E, "\"electron\": ", "\"electron-updater\": \"^6.0.0\",\n    \"electron\": "),
]
if subprocess.run(TESTS, capture_output=True, timeout=300).returncode != 0:
    raise SystemExit("the tests fail (or a test file is missing) before any mutation: fix that first")
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(TESTS, capture_output=True, timeout=300)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
