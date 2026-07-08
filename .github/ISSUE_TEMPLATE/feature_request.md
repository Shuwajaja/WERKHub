---
name: Feature request
about: Propose a capability or change for WERKHub that respects the governed, fail-closed core
title: "feat: <short summary>"
labels: [enhancement, triage]
assignees: []
---

## Problem

What problem are you trying to solve? Describe the situation and who it affects.
Lead with the pain, not the solution.

## Proposed change

What you would like WERKHub to do. Be concrete about the surface (CLI, MCP
server, dashboard, policy, ledger) and the expected behavior.

## Why it belongs in WERKHub

WERKHub is the local, governed MCP control plane: browse, connect, and
supervise agent MCP extensions, deny-by-default and everything ledgered.
Explain how this proposal fits that scope rather than expanding it elsewhere.

## Guardrail check

Confirm the proposal is compatible with the project's hard invariants:

- [ ] Keeps the core **stdlib-only** (`dependencies = []`); any new dependency
      lives behind an optional extra and is imported inside function bodies, not
      at module scope in the core.
- [ ] **No daemon / background network** added to the core; long-lived loops
      stay in the serve/dashboard entry points only.
- [ ] **Fail-closed**: new write/destructive paths are gated (approval token)
      and unknown profiles/tools/scopes still deny.
- [ ] **Ledgered**: new state-changing actions emit a tamper-evident event.
- [ ] Testable **offline and deterministically** (no network, no real keys).

## Alternatives considered

Other approaches you weighed and why you rejected them.

## Additional context

Mockups, links, prior art, or related issues. Note any new event names or
policy scopes the change would introduce.
