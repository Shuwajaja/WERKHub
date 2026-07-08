# Concepts

This page explains the five core ideas behind WERKHub. Understanding them will
help you configure profiles, reason about approval flows, and trust the evidence
ledger.

---

## The front door

WERKHub exposes a single MCP server over stdio — `werktools hub serve`. Every
agent host that points its MCP config at this server goes through the same
governed front door. The hub does **not** add a network layer: it runs as a
subprocess of the host, exactly like any other MCP server, but it relays to
multiple downstream servers in one place.

Eight always-on bridge tools are visible to every agent regardless of profile:

| Tool | What it does |
|---|---|
| `tool_search` | Find tools by name or keyword |
| `tool_describe` | Return the schema for a specific tool |
| `tool_call` | Execute a tool (subject to policy gate) |
| `profile_info` | Show the active profile and its permissions |
| `ledger_recent` | Return recent ledger events |
| `approval_status` | Check the status of an approval request |
| `hub_status` | Return live fleet health |
| `registry_search` | Search the capability registry |

When a worker manifest is configured, four `model_worker_*` tools are also
available for HTTP dispatch to model providers.

---

## Deny-by-default policy

Every tool call enters `enforce()` before anything runs. `enforce()` crosses two
axes:

- **Permission profile** — `cautious` / `balanced` / `admin`, set per-profile in `hub.json`.
- **Risk class** — `read` / `write` / `destructive` / `external` / `secret` / `unknown`,
  derived from the tool's manifest via the offline risk classifier.

The result is one of four verdicts:

| Verdict | Meaning |
|---|---|
| `allow` | Tool executes immediately |
| `approval_required` | Tool is held; a one-use token is required |
| `hidden` | Tool is not advertised and will not execute |
| `deny` | Tool is explicitly blocked; call returns an error |

Tools whose manifest is missing or unrecognized default to `deny` (fail-closed,
never silently allowed). The core rule: **if `enforce()` does not say `allow`,
nothing runs.**

See [ADR-001](adr/ADR-001-two-policy-models.md) for the separation between the
enforcement path and the hub's explanation/introspection path.

---

## One-use approval tokens

When `enforce()` returns `approval_required`, the hub:

1. Persists a pending record with a `request_id` and a SHA-256 of the call arguments.
2. Returns the `request_id` to the agent (not a token).
3. Waits for a human to run `werktools hub approvals approve <request_id>`.

On approval, the hub mints a one-use token bound to the argument hash. The agent
retries the call with this token. The hub:

- Validates the token in constant time (`hmac.compare_digest`).
- Consumes it atomically before execution (OS-level rename).
- Re-runs `enforce()` after consumption — a since-tightened policy still blocks.

Token lifetime is 900 seconds. An arg-swapped retry (same token, different args)
is rejected because the argument hash no longer matches. A consumed, expired, or
denied token is equally rejected. The only path to execution is: correct token,
matching args, consumed exactly once, policy still says allow.

---

## Hash-chained evidence ledger

Every significant event — tool calls, policy decisions, approval requests,
runtime probes, config loads — is appended to a local JSONL file at
`.werktools/hub.jsonl`. Each record carries:

- A monotonic timestamp and UUID event ID.
- The event type (43 named types in the current contract).
- A SHA-256 of the previous record's hash, forming a chain.

Reading the ledger with `werktools trace verify` re-derives the chain and reports
any broken link. A forged or deleted entry breaks the chain at that position; the
read is flagged, not silently served as clean evidence.

The ledger is **append-only and local**. No event is sent to any remote system.
No secret value is written (only env-var *names*).

---

## Trust tiers and the Tier-1 allowlist

Every connector (downstream MCP server) carries a string trust tier:

| Tier | Meaning | Badge color |
|---|---|---|
| `Official` | First-party or Anthropic-maintained connector on the curated allowlist | Green |
| `Security-Scanned` | Docker-built with Cosign + SBOM provenance on the allowlist | Slate (audit) |
| `Community-Unverified` | Not on the Tier-1 allowlist; default for all unknown servers | Muted |

!!! warning "Community-Unverified is not "dangerous""
    `Community-Unverified` means *not yet vetted*, not *known bad*. The color is
    muted, not red. Red is reserved for genuinely denied or failed states.

The **Tier-1 allowlist** is a commit-pinned set of ~70 curated servers embedded
in the package. A server discovered via `hub registry install` that is not on the
allowlist stays `Community-Unverified` and is marked `[UNVETTED]`. Operators can
supply an override file at `.werktools/tier1_allowlist.json`; a corrupt override
fails closed to an empty allowlist (no silent promotion).

Trust tier is **metadata only** in the current release — it is recorded,
displayed, and explained, but it does not change the `enforce()` outcome by
itself. Policy decisions remain the job of the permission-profile × risk-class
gate. Trust-based enforcement (a minimum-tier gate) is a planned P2 feature.

See [ADR-004](adr/ADR-004-string-trust-tiers.md) and [ADR-005](adr/ADR-005-tier1-allowlist-gate.md).
