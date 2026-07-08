# ADR-004: String trust tiers (Official / Security-Scanned / Community-Unverified)

Status: accepted
Date: 2026-06-19
Author: [CLAUDE]

## Context

WERKHub P1 introduces a 3-tier trust label that travels with agent-visible
assets (`ToolCard`), downstream servers (`DownstreamServer`), catalog cards
(`CatalogCard`), SKILL.md frontmatter, and Integration-Gate connectors. Two
candidate models existed:

- The earlier line-level spec modelled trust as an **integer** `trust_tier`
  (`1`/`2`/`3`, default `3`) with Docker-digest and MCP-Scoreboard machinery.
- The receiving work-order (and the P1 session brief) instead mandated a
  **string** taxonomy — `Official` / `Security-Scanned` / `Community-Unverified`,
  default `Community-Unverified` — and explicitly told the implementer to
  ignore stale doc details where they conflict.

The int model was ambiguous on polarity (is `1` or `3` the most trusted?) and
its values are opaque in hub.json, ledger records, and CLI output.

## Decision

**Trust tiers are the strings `Official`, `Security-Scanned`, and
`Community-Unverified`. The default and every unknown value fail closed to the
lowest tier, `Community-Unverified`.**

- The vocabulary and the `normalize_trust_tier()` fail-closed normalizer live
  in `catalog.py` (single source of truth); `contracts.py` consumes them.
- Trust in P1 is **metadata only** — it is recorded, serialized, and surfaced,
  but it never changes a policy/enforcement decision. Enforcement remains the
  job of `hub/policy.py::enforce` and the approval machinery (ADR-001).
- The Tier-1 allowlist (ADR-005) maps its curated entries onto these strings:
  `docker-built` -> `Security-Scanned` (Cosign + SBOM + commit-pin provenance);
  first-party vendor / Anthropic connector -> `Official`; everything else ->
  `Community-Unverified`.

### Ordering note (load-bearing for P2)

`TRUST_TIERS` is written most- to least-trusted, but the relative rank of
`Security-Scanned` (automated Cosign/SBOM, but the v0 seed is not OCI-pinned)
versus `Official` (first-party provenance, but unscanned) is a deliberate
judgement call, not a hard order. P1 treats trust as metadata so the ordering
is inert. **Before any P2 logic makes tier ordering load-bearing** (e.g. a
"minimum trust" gate), introduce an explicit rank map next to `TRUST_TIERS`
rather than relying on tuple index — do not let comparison code depend on the
declaration order.

## Consequences

- Self-describing values everywhere trust appears (hub.json, ledger, SKILL.md,
  CLI), with no polarity ambiguity.
- Fail-closed by construction: unknown/garbage input becomes the lowest tier,
  pinned by ~20 tests.
- One reconciliation cost: the allowlist gate (ADR-005) translates its
  curated-entry provenance into these three strings.

## Revisit Trigger

When P2 introduces trust-based enforcement (a minimum-tier gate, or a UI sort),
add the explicit rank map and re-confirm the `Security-Scanned` vs `Official`
ordering against the then-current digest-pinning state.
