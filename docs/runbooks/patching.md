# Patching dependencies (#365)

Laddrix is built from other people's code: Python packages for the server, npm packages for
the screens and the desktop app, the Python and Node base images, and GitHub's CI actions. A
flaw in any of them is a flaw in Laddrix. This page says who fixes what, and by when.

## What watches for flaws

| What | How | Where you see it |
| --- | --- | --- |
| Python packages | `pip-audit` on `requirements.lock` in every CI run | the **supply-chain** job turns red |
| Screens and desktop app | `npm audit --audit-level=high` in every CI run | the **ui** / **desktop** job turns red |
| Everything above + base images + CI actions | Dependabot (`.github/dependabot.yml`): one grouped PR a week per folder with the minor and patch updates (#392) | a PR from Dependabot |
| Secrets committed by mistake | `gitleaks` over the whole history in every CI run | the **supply-chain** job turns red |

A red job on `main` with no code change of ours means a new advisory was published for
something we use. It is not a flaky test: it is the start of the clock below.

## How fast

The clock starts when the advisory reaches us: a red job or a Dependabot PR.

| Severity (the advisory's own) | While trading live | Before going live |
| --- | --- | --- |
| **Critical** | **48 hours**, emergency deploy allowed (`deploy.sh --emergency`) | next working day |
| **High** | **7 days** | before go-live |
| Medium | next planned release | next planned release |
| Low | when convenient | when convenient |

**Who:** whoever maintains the code (today Claude, with Ayush approving the merge) makes the fix
PR. Ayush deploys it ([release.md](release.md)). If a fix can't be made in time (no patched
version exists yet), write down why and what limits the risk meanwhile (for example the
feature turned off) in the decision log, and look again daily.

**Not reachable from our code?** Many advisories are for a part of a package we never use.
Still patch within the time, because "we never call it" is easy to get wrong. If no patched
version exists, record it in an ignore entry with the advisory id, the reason and a date to look
again. An ignore without a date is not allowed.

## Major versions

Dependabot never proposes a major version (React 19→20, Vite 7→8, Node 22→24...): each can change
behaviour. Upgrade one on purpose: an issue, the upgrade notes read, the code changed where
needed, and the release scenarios run. The exception is a security fix that only exists in a
new major. CI's audits fail on it, and the times above apply.

## Updating a Python dependency

```bash
# change the range in pyproject.toml if needed, then re-lock (exact versions with hashes):
uv pip compile pyproject.toml --universal --generate-hashes --python-version 3.12 -o requirements.lock
# to move a single package to its newest allowed version:
uv pip compile pyproject.toml --universal --generate-hashes --python-version 3.12 \
    -o requirements.lock --upgrade-package <name>
pip install --require-hashes -r requirements.lock && pytest
```

`tests/test_supply_chain.py` fails if `pyproject.toml` and `requirements.lock` disagree, or if
any pin has no hash. The image installs only from the lock (`--require-hashes`), so a package
swapped on the index after we locked it is refused at build time.

## Updating npm dependencies

```bash
cd ui            # or ui/desktop
npm update <name>   # or npm install <name>@<version>
npm audit --audit-level=high && npm test && npm run build
```

`package-lock.json` holds every version with its integrity hash, and `npm ci` (CI and the image)
refuses anything that doesn't match it.
