# Security model

WERKHub is designed around a small number of hard security properties. This page
describes those properties, the known limitations at `0.2.0`, and how to
report a vulnerability.

---

## Core properties

### Deny-by-default, fail-closed

Every tool call passes through `enforce()` before anything runs. The default
verdict for an unrecognized tool, a missing manifest, or any configuration error
is `deny`. There is no "allow by exception" path — the fail-closed rule is not
overridable via config.

The policy engine has two axes (permission profile × risk class) and four
verdicts (`allow` / `approval_required` / `hidden` / `deny`). Only `allow`
executes. See [Concepts](concepts.md) and [ADR-001](adr/ADR-001-two-policy-models.md).

### Argument-bound one-use approval tokens

Risky tools (`approval_required` verdict) require an operator-issued token before
executing:

- Tokens are bound to a SHA-256 of the call arguments (`hmac.compare_digest`).
- Tokens expire after 900 seconds.
- Tokens are consumed atomically (OS-level rename) before execution.
- `enforce()` re-runs after consumption — a since-tightened policy blocks even
  a valid token.
- An arg-swapped retry (different args, same token) is rejected because the
  argument hash no longer matches.

There is no path to execution with a wrong, expired, blank, denied, or
already-consumed token.

### Hash-chained evidence ledger

The append-only JSONL ledger is the authoritative audit trail. Each record
contains the SHA-256 of the previous record's hash. `audit verify` re-derives
the chain and flags any break — a forged or deleted entry is surfaced, never
silently served.

Ledger events include: `tool.call.requested`, `tool.call.denied`,
`tool.call.completed`, `approval.requested`, `approval.granted`,
`approval.denied`, `runtime.probed`, `config.loaded`, `registry.allowlist.error`,
`registry.allowlist.tier_downgrade`, and 33 others (43 total).

### Presence-only secret handling

WERKHub never reads, logs, stores, or returns a secret value:

- Onboarding extracts MCP server *command + args* from host configs — not tokens.
- The capability registry stores env-var **names** only (`GITHUB_TOKEN`, not `ghp_…`).
- `hub doctor` reports token-env presence as a boolean, not a value.
- The ledger never contains a secret value; any secret-like key that would appear
  in a payload is redacted by the shared redaction layer before write.

### Path-traversal guards

File-path arguments to vault, data-gate, and trace commands are validated against
allowed roots. Package-name arguments to registry install commands are checked
against npm/pypi/OCI name allowlists before any subprocess is invoked.

### Provider API keys in tracebacks

Provider API keys (OpenRouter, model workers) are extracted from error tracebacks
before they could appear in ledger events or CLI output.

### The Tier-1 install allowlist

`hub registry install` gates all installs through a commit-pinned allowlist.
A server not on the allowlist is:

- Assigned `Community-Unverified` trust tier.
- Marked `[UNVETTED]` in all surfaces.
- Blocked from automatic promotion into `hub.json`.

A corrupt override file at `.werktools/tier1_allowlist.json` fails closed to an
empty allowlist — a bad override never silently promotes anything.

See [ADR-005](adr/ADR-005-tier1-allowlist-gate.md).

---

## The Tier-1 allowlist

The embedded seed covers ~70 servers: 49 Docker-built (Cosign + SBOM provenance
expected) and 21 first-party or Anthropic-maintained connectors. The seed ships
with `image_digest=None` (honest-degrade: no OCI digest sweep was done yet;
fabricating SHA-256 values would violate the no-fiction rule). Real digest-pinning
is a planned future release.

---

## Known limitations (0.2.0)

!!! warning "Process-kill is a preview"
    The dashboard Kill button is gated (loopback + per-session token + live-fleet
    PIDs) and ledger-wired, but the live process-supervision is stub-only. The
    button surfaces state rather than terminating processes. **Do not depend on
    WERKHub process control in any production flow.**

!!! warning "sweep_expired() is tested but unwired"
    The token-expiry sweep helper is tested but not called on a schedule. Live
    token expiry is the inline-at-consume check only (valid for the current
    implementation). A timed sweep is a future hardening item.

!!! note "stdio relay only"
    `hub serve` spawns downstream servers as stdio subprocesses. HTTP/SSE/WS
    downstreams can be represented in `hub.json` but the relay will raise
    `NotImplementedError` (fail-closed) for non-stdio transports.

!!! note "Trust tier is metadata only in v0"
    Trust tier does not yet change `enforce()` outcomes. A minimum-tier
    enforcement gate is planned for P2.

---

## Reporting a vulnerability

Report security vulnerabilities **privately** — do not open a public issue.

See [SECURITY.md](https://github.com/Shuwajaja/WERKHub/blob/main/SECURITY.md)
for the private disclosure channel.

---

## Threat model summary

| Threat | Mitigation |
|---|---|
| Agent calls a dangerous tool without human review | Deny-by-default + `approval_required` verdict + one-use arg-bound tokens |
| Forged ledger presented as clean audit trail | Hash-chained JSONL; chain break surfaced, never silently served |
| Secret value leaked via logs or ledger | Presence-only handling; redaction layer on all payloads |
| Untrusted MCP server silently promoted | Tier-1 allowlist; `Community-Unverified` default; `[UNVETTED]` label |
| Approval token reused or arg-swapped | Token consumed atomically; arg hash mismatch = reject; `enforce()` re-runs after consume |
| Config path traversal | Validated against allowed roots; package-name allowlists on installs |
| Status API exposed to network | Status server bound to `127.0.0.1` only; non-GET → 405; unknown path → 404 |
