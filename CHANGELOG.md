# Changelog

All notable changes to werktools are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-07-08

Initial release of WERKHub — the local, governed MCP control plane.

### Added

- **MCP Bridge Server** — single FastMCP endpoint with 8 always-on bridge tools:
  `tool_search`, `tool_describe`, `tool_call`, `profile_info`, `ledger_recent`,
  `approval_status`, `hub_status`, `registry_search`.
- **Policy Gate** — deny-by-default enforcement via `enforce()`; allowlist-driven
  trust tiers (Tier-1 OCI-pinned, Security-Scanned, Community-Unverified).
- **Approvals** — one-use token-gated install approval, argument-bound, expiring.
- **Tamper-Evident Ledger** — hash-chained JSONL evidence log.
- **Downstream Relay** — MCP Discovery + policy-gated forwarding to downstream servers.
- **Process Lifecycle** — POSIX process-group spawning (`start_new_session=True`),
  Windows Job Object bind (`KILL_ON_JOB_CLOSE`), TTL-based reaper with SIGTERM/SIGKILL
  escalation, cross-session orphan sweep.
- **Diagnostics** — discovery-failure classifier (`hub/diagnose.py`) with actionable
  verdicts (single_instance, not_found, needs_auth, missing_cwd_or_file, startup_hang).
- **Runtime Doctor** — detects Claude Code, Cursor, Windsurf, Gemini CLI, Kimi,
  Antigravity, Codex, Goose, VS Code MCP; all invariants check (core deps, lifecycle
  extras, event names, subprocess deferral).
- **Local Tool Slices** — truth, mine, trace, vault, data-gate, swarm, cost, eval,
  audit, skills, integration-gate, canon.
- **Desktop Dashboard** — local HTTP supervisor console + Deno/Vite/React supervisor UI.
