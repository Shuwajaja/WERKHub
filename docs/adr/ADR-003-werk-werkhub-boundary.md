# ADR-003: WERK <-> WERKHub boundary — two governance layers, composed over MCP

Status: accepted  
Date: 2026-06-11  
Author: [CLAUDE]

## Context

Two products live under the WERK umbrella and both have a "policy / gating"
surface, which risks building the same engine twice:

- **WERK** (codename WERKCommander) is the **orchestration
  runtime**: missions, the planner -> builder -> reviewer flow, the engine
  chain (`_run_engine_chain`), `run_graph`, the frozen 12-event ledger. It
  decides *which engine/worker runs* and *what a mission may do/cost*.
- **WERKHub** (this product; Python package + CLI namespace stay `werktools`,
  no install churn — mirrors WERKAgent=brand / WERKCommander=codename) is the
  **local MCP gateway / tool-governance layer**, hardened 2026-06-11: one
  governed MCP door (`werktools hub serve`) in front of downstream tools, with
  profiles, argument-bound one-use approval tokens (sha256 / 900s TTL /
  OS-atomic / deny-by-default), and a hash-chained tamper-evident ledger.

A competitive scan (2026-06-11) confirmed WERKHub occupies an unoccupied niche
(no existing MCP gateway combines arg-bound approval tokens + hash-chained
ledger + local-first + deny-by-default), so it was ratified as a **standalone
repo and product** that WERK uses **by composition**, not by code merge. The
open question this ADR closes: *who governs what, and where does each policy
engine's authority stop, so the two never duplicate or contradict each other?*

This complements [ADR-001](ADR-001-two-policy-models.md) (WERKHub's own
internal split: core `enforce()` vs hub explanation) and is the WERKHub-side
record of the boundary the WERK session pinned in WO-3
(`knowledge-hub/werk/WERK_WORK_ORDERS_2026-06-11.md`).

## Decision

**WERK and WERKHub are two independent governance layers that compose over the
open MCP/stdio seam. Neither rebuilds the other's engine.**

- **WERK gates ENGINES and MISSIONS.** *Which* worker/engine runs, the
  conductor-side risk tier, and what a mission may cost. This is expressed in
  `configs/tool_guard_policy.yaml` (WO-3, deny-by-default, with a never-
  overridable block list hardcoded outside the YAML) plus the engine chain.
- **WERKHub gates downstream TOOLS.** *Whether a given tool call executes*,
  behind its own MCP door, via profiles + the approval-token machinery +
  the ledger. This is `hub/policy.py::enforce`, `hub/approvals.py`, and the
  relay trust boundary.
- **They meet over MCP.** A WERK worker that needs governed tool access points
  its MCP config at `werktools hub serve --profile <id>` — it does **not** get
  a second tool-policy engine inside WERK. WERK never imports WERKHub code;
  WERKHub never imports WERK code. The only contract is the MCP protocol.

**Do NOT rebuild WERKHub's profile/approval engine inside WERK, and do NOT
rebuild WERK's engine/mission gate inside WERKHub.** `tool_guard_policy.yaml`
covers ONLY the conductor-side engine/risk-tier gate; if a worker needs
governed *tool* access, that is WERKHub's job, reached by composition.

## Consequences

- **Defense in depth, not duplication.** Two orthogonal gates: WERK decides the
  conductor may run engine X for mission M; WERKHub independently decides each
  tool call that engine then makes is allowed/approved. A bypass in one layer
  does not open the other.
- **One source of truth per concern.** Engine/mission/cost policy lives in
  WERK; tool/approval/secret policy lives in WERKHub. No drift between two
  copies of the same rules.
- **Standalone optionality preserved.** WERKHub keeps its own repo, release
  cadence (`werktools` package, `[0.2.0rc1]`), buyer (every MCP user, not just
  WERK users), and credibility artifact (a real local security product, 553
  tests). WERK gains tool governance for the cost of one MCP-config line.
- **The seam is reversible and private-coupling-free.** stdio + the MCP
  protocol; swap WERKHub out, or run WERK without it, with no code change.

## Honest limitation (must hold before any "production" claim)

WERKHub's dashboard kill button is currently **fail-closed inert** (the live
fleet is stub-only) and `sweep_expired()` is a **tested-but-unwired** helper
(live token expiry is the inline-at-consume check only). WERK must therefore
**not assume WERKHub enforces what it does not yet** — e.g. do not build a WERK
flow that relies on the hub's kill path. Both gaps are tracked in WERKHub's
SECURITY.md / CHANGELOG and must be closed before WERKHub is described as
"production" anywhere in either product.

## Revisit Trigger

If a worker genuinely needs a *tool*-policy decision that depends on
*conductor/mission* context (a cross-cutting case), reopen this — but the first
answer is to pass that context **into** the WERKHub profile/approval request
(e.g. via the profile lens or the request's call args), **not** to fork a
second tool-policy engine into WERK.
