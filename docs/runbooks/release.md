# Making a release (V1.6, #289)

## Versions

- **One version** lives in `src/mocktrade/version.py`. `pyproject.toml` holds the same number in Python's form (`1.0.0rc1`), and `ui/package.json` and its lockfile hold it too. `tests/test_release.py` fails if they differ, or if the version has no release notes or changelog entry.
- **Semantic versions:** MAJOR.MINOR.PATCH. A fix is a PATCH; a feature that changes nothing for existing data or screens is a MINOR; a change that needs action from you is a MAJOR. A release candidate is `-rc.N`.
- **The running server says its version** at `/api/health` (`version`, plus `build`, the commit the image was made from). The screens show it in the status bar, bottom right, with the build on hover.

## Making a release

1. **Change the version:** edit `version.py` and `pyproject.toml`, `ui/package.json`, then `cd ui && npm install --package-lock-only` for the lockfile.
2. **Write the notes:** `docs/releases/vX.Y.Z.md` (what changed, data migrations, known limits) and a short entry at the top of `CHANGELOG.md`.
3. **Run the release scenarios:** those in [the test plan](../test/README.md#scenarios-that-must-pass-before-a-release).
4. **Merge,** then check that CI is green on `main`.
5. **Tag `main`:** `git tag -a vX.Y.Z -m "MockTrade X.Y.Z"` and `git push origin vX.Y.Z`. For **v1.0.0**, only after Ayush has run the live steps of the [go-live checklist](go-live.md) and recorded his acceptance on #240.

## Deploying (on the server)

**Outside market hours only:** not 09:00–15:45 India time on a trading day. The script enforces this. Run it **from the release being deployed**, inside the server's checkout:

```bash
cd /opt/mocktrade
git fetch --tags
git show vX.Y.Z:deploy/deploy.sh >/tmp/deploy.sh
sudo bash /tmp/deploy.sh vX.Y.Z
```

**What `deploy.sh` does:**
- **Before:**
  - the tag must exist and the checkout must have no local changes;
  - the market-hours rule, taken from the release itself (`deploy/window.sh`, with `MOCKTRADE_HOLIDAYS` from `deploy/.env`);
  - a **backup** of all state;
  - the running image is kept as `mocktrade:previous`, and `deploy/.previous` records the commit and the backup.
- **Then:** check out the tag, build the image (stamped with its commit), and start it.
- **After:** the app must be healthy and `/api/health` must report the tag's version. If either check fails, it **rolls back at once by itself**.
- **Log:** every step is written to `/var/log/mocktrade-deploy.log` with who ran it.

**Emergency fix in market hours:** `sudo bash /tmp/deploy.sh vX.Y.Z --emergency "what and why"`. It goes ahead, and the log records `EMERGENCY … by <who>: <reason>`. Use it only for a fix that can't wait for 15:45.

**After deploying:**
- the status bar shows the new version;
- `GET /api/ops` shows no new alerts;
- the next morning, the usual routine ([morning.md](morning.md)).

## Rollback

```bash
cd /opt/mocktrade
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
