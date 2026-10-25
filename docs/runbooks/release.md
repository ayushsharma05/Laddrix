# Making a release (V1.6, #289)

## Versions

- **One version** lives in `src/laddrix/version.py`. `pyproject.toml` holds the same number in Python's form (`1.0.0rc1`), and `ui/package.json`, `ui/desktop/package.json` and their lockfiles hold it too (`laddrix.__version__` reads `version.py`). `tests/test_release.py` fails if they differ, or if the version has no release notes or changelog entry.
- **Semantic versions:** MAJOR.MINOR.PATCH. A fix is a PATCH; a feature that changes nothing for existing data or screens is a MINOR; a change that needs action from you is a MAJOR. A release candidate is `-rc.N`.
- **The running server says its version** at `/api/health` (`version`, plus `build`, the commit the image was made from). The screens show it in the status bar, bottom right, with the build on hover.

## Making a release

1. **Change the version:** edit `version.py` and `pyproject.toml`, `ui/package.json` and `ui/desktop/package.json`, then `npm install --package-lock-only` in `ui` and in `ui/desktop` for the lockfiles.
2. **Write the notes:** `docs/releases/vX.Y.Z.md` (what changed, data migrations, known limits) and a short entry at the top of `CHANGELOG.md`.
3. **Run the release scenarios:** those in [the test plan](../test/README.md#scenarios-that-must-pass-before-a-release).
4. **Merge,** then check that CI is green on `main`.
5. **Tag `main`:** `git tag -a vX.Y.Z -m "Laddrix X.Y.Z"` and `git push origin vX.Y.Z`. For **v1.0.0**, only after Ayush has run the live steps of the [go-live checklist](go-live.md) and recorded his acceptance on #240.
6. **Keep the SBOMs** (#365): the tag's CI run makes `sbom-vX.Y.Z`, the CycloneDX lists of every package in the server, the screens and the desktop app. Download it from the run's page and keep it with the release notes. When a new advisory comes out, it answers "were we shipping that?" for any past release.

## The desktop app (#365)

The desktop app isn't distributed today: it is built from source, and it has no auto-update
(`tests/test_supply_chain.py` fails if one is added). **Before giving the installer to anyone:**
- **Sign it:** a code-signing certificate in the firm's name. electron-builder signs the Windows
  installer when `CSC_LINK`/`CSC_KEY_PASSWORD` are set on the build machine, never in git.
- **Updates only if signed:** if auto-update is ever added, it must check each download's
  signature against the firm's certificate before installing (electron-updater does this for
  signed Windows builds), and be served over HTTPS only.
- **Publish its SHA-256** with the release notes, so anyone can check their download.

## Deploying (on the server)

**Outside market hours only:** not 09:00–15:45 India time on a trading day. The script enforces this. Run it **from the release being deployed**, inside the server's checkout:

```bash
cd /opt/laddrix
git fetch --tags
git show vX.Y.Z:deploy/deploy.sh >/tmp/deploy.sh
sudo bash /tmp/deploy.sh vX.Y.Z
```

**What `deploy.sh` does:**
- **Before:**
  - the tag must exist and the checkout must have no local changes;
  - the market-hours rule, taken from the release itself (`deploy/window.sh`, with `LADDRIX_HOLIDAYS` from `deploy/.env`);
  - a **backup** of all state;
  - the running image is kept as `laddrix:previous`, and `deploy/.previous` records the commit and the backup.
- **Then:** check out the tag, build the image (stamped with its commit), and start it.
- **After:** the app must be healthy and `/api/health` must report the tag's version. If either check fails, it **rolls back at once by itself**.
- **Log:** every step is written to `/var/log/laddrix-deploy.log` with who ran it.

**Emergency fix in market hours:** `sudo bash /tmp/deploy.sh vX.Y.Z --emergency "what and why"`. It goes ahead, and the log records `EMERGENCY … by <who>: <reason>`. Use it only for a fix that can't wait for 15:45.

**After deploying:**
- the status bar shows the new version;
- `GET /api/ops` shows no new alerts;
- the next morning, the usual routine ([morning.md](morning.md)).

## Rollback

```bash
cd /opt/laddrix
sudo bash deploy/rollback.sh                   # the previous version, data as it is now
sudo bash deploy/rollback.sh --restore-data    # …and the data as it was before the deploy
```

- **The plain rollback** puts back the previous image and code. That is enough when the new version changed no data, which is true for every release so far. `users.db` only ever gains columns, and older code ignores them.
- **`--restore-data`** is for when the release notes name a data migration that can't run backwards. It restores the backup taken just before the deploy. What happened since is not lost: it is moved into `/data/.before-restore-<time>/`. The restore uses the newer image's restore tool, before switching back, because an older one may not restore in place.
- **After a rollback,** the status bar shows the old version again; check `GET /api/ops`.

## Rehearsed

`context/scripts/drill-deploy-rollback.sh <old-ref> <new-ref>` runs the real scripts with real images and a real data volume on a staging copy. Its last run passed 15/15 (`context/evidence/v1-6-deploy-rollback-rehearsal.txt`). It went from the pre-1.0 `main` to `1.0.0-rc.1`:
- a deploy in market hours was refused, and nothing changed;
- a deploy after hours ran the new version, stamped with its commit, with the data kept;
- a rollback brought back the old version, with the data;
- a release claiming the wrong version rolled itself back;
- an emergency deploy went ahead and was logged with its reason;
- `--restore-data` brought back the data from before that deploy, and kept what it replaced.

**Three problems the rehearsal found, now fixed:**
1. The first deploy failed because the old checkout had no deploy script; the script now runs from the release.
2. A rollback could check out code without its own script; it now runs from a copy.
3. The old image's restore tool couldn't restore in place on a volume; `--restore-data` now uses the newer image's tool.
