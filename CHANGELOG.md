# Changelog

Versions follow [semantic versioning](https://semver.org): MAJOR.MINOR.PATCH, with `-rc.N` for a release candidate. Each version has notes in `docs/releases/`; how a release is made is in `docs/runbooks/release.md`.

## 1.0.0-rc.1 (16 Oct 2026)

The release candidate for core v1. See [docs/releases/v1.0.0.md](docs/releases/v1.0.0.md).

- **Core v1 gate (#240):**
  - each recorded day keeps its instrument list (#327);
  - the crash-and-restart drill and the UNPROTECTED band (#328);
  - backup of all state and a checked restore (#329);
  - the go-live checklist (#330);
  - design docs and the test plan (#288);
  - the release process and this version (#289).
- **K13:** the admin API and screen (#281, #282), keyboard shortcuts (#284), themes (#285).
- **Before that:** K0–K12, the whole platform. See the release notes.
