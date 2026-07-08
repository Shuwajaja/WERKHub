# Portable expert-role badge (for WERKAgent)

`roles.ts` (the engineering-role taxonomy) and `RoleBadge.tsx` are a **portable asset
for WERKAgent's crew** — they are deliberately **not used by WERKHub itself**.

## Why they live here but belong to WERKAgent

WERKHub governs **MCP connectors**. The agents that touch it are *consumers* identified
by a profile whose only role axis is an **access posture** — `admin` / `balanced` /
`read_only`. That posture is what WERKHub renders (`AccessBadge` in `Badge.tsx`).

The richer **engineering-role** taxonomy (planner, architect, code-reviewer,
react-reviewer, security-reviewer, …) describes the **crew that runs missions** — a
WERKAgent concept, not a WERKHub one. It was authored here, kept self-contained, ready
to lift.

## What WERKAgent gets

- `roles.ts` — pure TS, zero deps:
  - `FAMILIES` — 7 expert families (Orchestration / Build / Review / Verify / Govern /
    Knowledge / Ops) + a muted `unknown` fallback, each with a hex color.
  - `resolveFamily(agentType)` — maps **any** ECC agent type to a family via an explicit
    map + suffix heuristics (`*-reviewer` → review, `*-build-resolver` → build, …).
  - `shortModel(id)` / `vendorOf(id)` — terse model label + vendor for an identity badge.
- `RoleBadge.tsx` — one small React component (`color = family, text = role`). Needs only
  React, `roles.ts`, the `.werk-stamp` class and A-FINAL CSS vars — all of which WERKAgent
  already ships.

## How to lift it

Copy `roles.ts` + `RoleBadge.tsx` into WERKAgent (e.g. `apps/desktop/components/badges/`).
No other files are required. `roles.test.ts` documents the expected behavior if you want
to bring the tests too.

> Keep `roles.ts` as the single source of truth for the family→color map across products
> so WERKHub identity badges and WERKAgent crew badges stay visually consistent.
