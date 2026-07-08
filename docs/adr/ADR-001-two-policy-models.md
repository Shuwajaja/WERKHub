# ADR-001: Two Policy Models (Core Enforcement vs Hub Explanation)

Status: accepted  
Date: 2026-06-10

## Context

werktools currently ships two policy modules that look similar but answer
different questions:

- `werktools.policy` (core) is the **enforcement axis**. `resolve(profile,
  registry)` produces a frozen `PolicySnapshot`, and `decide(snapshot,
  tool_id)` returns a call-time verdict that `server.register()` enforces at
  the MCP boundary. Its verdict vocabulary is effectively `allow` / `deny`
  (a `hidden` tool is callable but not advertised; bridge tools are always
  allowed).
- `werktools.hub.policy` (hub) is the **explanation axis**. `explain(config,
  profile_id, tool_id)` answers "what would the Hub say about this
  profile/tool pair" for static tool cards, using `permission_profile`
  (cautious / balanced / admin) crossed with risk classes (read / write /
  destructive / external / secret / unknown). Its vocabulary is wider:
  `allow` / `deny` / `approval_required` / `hidden`, with human-readable
  reasons. Nothing executes behind it.

A 2026-06-10 review flagged the risk that these silently diverge, and that
`approval_required` has no enforcement path at the MCP boundary today.

## Decision

Keep the two models separate for v0, with these explicit contracts:

1. `werktools.policy` is the only module that gates execution. Anything that
   actually runs a handler must go through `decide()`.
2. `werktools.hub.policy` is advisory: it explains and documents. CLI
   commands (`hub policy explain`, `hub tools`) and exported capability
   cards may cite it, but it must never be the only check in front of an
   execution path.
3. `approval_required` is therefore an *explanation-only* verdict in v0.
   This is a documented gap, not an accident: the core `decide()` has no
   approval verdict because werktools has no approval envelope yet.

## Consequences

- Wave-5 catalog tools reuse the hub vocabulary (risk classes +
  `approval_required`) for cards and exports, and stay execution-free, so
  the split stays safe.
- Anyone wiring a real downstream execution bridge (deferred: Integration
  Gate live calls, Model Workers, downstream MCP) MUST first promote
  approval handling into the core: either add `approval_required` as a
  first-class `decide()` verdict that `server.register()` can honor, or
  fail those tools closed.

## Revisit Trigger

Unify (or bridge) the two models before merging any feature that executes a
tool whose hub explanation is `approval_required`.

## Status Note (2026-06-10, hub serve slice 0)

`hub.policy.enforce()` now implements the permitted fail-closed bridge:
execution paths (the werk-hub `tool_call`) run only when the hub
explanation is `allow` AND the projected core `decide()` verdict is
`allow`. `approval_required`, `hidden`, and `deny` never execute.

## Revisit Resolved (2026-06-10, S4 approval queue)

The revisit trigger is satisfied. `hub/approvals.py` + the `tool_call`
token path make `approval_required` an **allow-after-token** verdict: the
first call persists a pending record and returns a one-use token; a human
approves via `hub approvals approve`; the caller retries with the token; the
hub validates it in constant time (`hmac.compare_digest`), consumes it
atomically before execution, and runs the tool exactly once. Every other
token state (wrong/blank/denied/already-consumed) stays fail-closed. The
relay's non-read read-only guard is lifted only on the consumed-token path.
