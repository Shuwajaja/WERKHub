# WERK HUB

<div class="wh-hero">
  <img src="assets/werkhub-dashboard.svg" alt="WERKHub dashboard — fleet health, trust posture, detected agent runtimes, and the hash-chained evidence ledger" />
</div>

<p class="wh-oneliner">The local, governed MCP control plane — one policy-gated front door for all your agent tools.</p>

Your agent hosts (Claude Code, Codex, Cursor, Windsurf…) each wire MCP servers
independently, with no shared policy, no audit trail, and trust taken on faith.
**WERKHub** is one stdio MCP front door for all of them: every tool call is
policy-gated, hash-chained into a tamper-evident ledger, and explained — with
risky tools requiring a one-use, argument-bound approval token before anything runs.

Deny-by-default. Fail-closed. No telemetry. Runs entirely on your machine.

---

## What you get

- **One MCP front door** — eight always-on bridge tools (`tool_search`, `tool_describe`,
  `tool_call`, `profile_info`, `ledger_recent`, `approval_status`, `hub_status`,
  `registry_search`) shared across every host that points at the hub.
- **Deny-by-default policy gate** — a tool executes only when `enforce()` returns
  `allow`. `approval_required`, `hidden`, and `deny` never run.
- **One-use approval tokens** — risky tools mint a token bound to the call arguments;
  arg-swapped retries are rejected; the policy re-runs after the token is consumed.
- **Hash-chained evidence ledger** — an append-only JSONL file with verified-chain
  reads; a forged entry is flagged, not silently served.
- **Trust taxonomy** — `Official` / `Security-Scanned` / `Community-Unverified` labels
  travel with every connector; a commit-pinned Tier-1 allowlist gates installs.
- **Bento control-plane dashboard** — `werktools hub dashboard`, a local, CSP-safe
  single-page view (WCAG 2.2 AA) with live connectors, trust-posture donut,
  detected agent runtimes, and the ledger tail.
- **1069 tests**, `ruff` + `mypy` clean, CI on Linux + Windows, Python 3.10–3.12.

---

## 30-second onboard

WERKHub reads the MCP servers already wired into Claude Code, Cursor, Windsurf, and
friends — and maps them onto one governed front door in three steps:

```bash
pip install werktools

# 1. Dry-run: see what it finds across your hosts, writes nothing
werktools hub onboard

# 2. Adopt them into hub.json (deny-by-default, presence-only keys)
werktools hub onboard --apply

# 3. Point a host at the hub
#    Add to .mcp.json (Claude Code) or equivalent:
#    "werktools hub serve --profile claude-reviewer --config .werktools/hub.json"
```

Then run the dashboard to verify everything is wired:

```bash
werktools hub dashboard --open
```

---

## Where to go next

| I want to… | Go to |
|---|---|
| Install and configure for the first time | [Getting started](getting-started.md) |
| Adopt my existing MCPs into the hub | [Onboarding your MCPs](onboarding.md) |
| Understand the policy model and approval flow | [Concepts](concepts.md) |
| Browse the control-plane dashboard | [Dashboard](dashboard.md) |
| Explore the capability registry | [Registry & capabilities](registry.md) |
| Run benchmark comparisons across tool variants | [Bench matrix](bench.md) |
| See every CLI command | [CLI reference](cli.md) |
| Read the security model and threat model | [Security model](security.md) |
| Understand architecture decisions | [Architecture / ADRs](adr/ADR-001-two-policy-models.md) |

---

!!! note "Status"
    WERKHub is `0.2.1` and publicly available as an early release. The pure-stdlib
    core is stable. The live MCP server requires `pip install werktools[server]`.
    The dashboard's process-kill action is still a **preview** (gated, ledgered,
    but backed by stub-only process wiring for now). License: AGPL-3.0-or-later.
