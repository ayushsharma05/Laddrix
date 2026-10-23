# Runbooks

Operational guidance for phase 9: what to do when specific failures occur.

| Runbook | When |
|---|---|
| [morning.md](morning.md) | The trading day: what runs by itself, and the one thing you do (Log in to Kite) |
| [incidents.md](incidents.md) | Login failed, WebSocket down, reconcile mismatch, kill switch |
| [server.md](server.md) | Setting up the Mumbai server: static IP, hardening, Kite IP whitelist, backups and restore |
| [go-live.md](go-live.md) | Before the first live session: every step with how to check it; paper evidence and the live boxes Ayush ticks |
| [release.md](release.md) | Versions, release notes, deploying outside market hours, rollback (rehearsed) |
| [patching.md](patching.md) | A dependency has a known flaw: who fixes it and how fast (critical 48 h, high 7 days) |
