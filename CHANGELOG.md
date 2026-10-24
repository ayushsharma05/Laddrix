# Changelog

Versions follow [semantic versioning](https://semver.org): MAJOR.MINOR.PATCH, with `-rc.N` for a release candidate. Each version has notes in `docs/releases/`; how a release is made is in `docs/runbooks/release.md`.

## Unreleased (since 1.0.0-rc.1)

The production-hardening pass (#257) and the security gate (#279). Each item's PR is linked from its issue.

- **Trading correctness and recovery:**
  - positions, recovery and closing checks per product (#258);
  - an order with an unknown fate stays unresolved until the broker settles it (#259);
  - modify and cancel serialised, and their uncertain outcomes reconciled (#260);
  - every exit through one desk, so overlapping closes can't reverse a position (#261);
  - STOP, cancel and Flatten as separate safe actions (#262);
  - MTM breaches and closes survive restarts (#263);
  - protection stops recovered after an outage (#264);
  - an order is on disk before it is sent; a storage failure refuses new orders (#265);
  - a new trading day without a restart (#266);
  - execution ticks separate from screen updates, and stale prices refused (#267);
  - trading only on synchronised screens (#268);
  - candle paths contained and imports checked (#269);
  - ownership of alerts and research (#270);
  - backups of all state, and a proven restore (#272);
  - critical loops supervised, and one trading server per data folder (#273);
  - exact fill economics and broker corrections (#275, #352);
  - broker-held triggers and trades made outside MockTrade (#276, #355);
  - F&O expiry day (#290);
  - surveillance alerts and the SEBI algo id (#291).
- **Security:**
  - rights re-checked when delayed actions run (#366);
  - key rotation, revoking sessions and a stolen-credential drill (#364);
  - step-up for privileged changes, and your own session list (#363);
  - the supply chain: locked hashes, a dependency audit, secret scanning and SBOMs (#365);
  - data classes, logs without secrets, retention, export and erase, a tamper-evident audit trail (#367);
  - an IP per account (#372);
  - webhooks connect only to the address they checked (#271).
- **Several firms on one server (#368):**
  - workspaces, with every check scoped (#394);
  - each trader's own Zerodha prices (#395);
  - files and jobs per firm (#396).
- **Workload:** backtests in their own process, reports off the trading loop, scheduler jobs that never hold each other up, and backtests whose results don't depend on the machine (#274).

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
