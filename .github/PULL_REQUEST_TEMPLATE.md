<!--
WERKHub is the local, governed MCP control plane. Keep the core stdlib-only and
fail-closed. PRs that touch policy, ledger, or approval-token paths get extra
scrutiny. Run the full local gate before requesting review.
-->

## What & why

Briefly describe the change and the problem it solves. Link the issue it closes.

Closes #

## Type of change

- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change (behavior, CLI, MCP contract, or event names)
- [ ] Docs / chore / refactor (no behavior change)

## How tested

Describe the offline, deterministic verification. Note new fixtures, cassettes,
golden files, injected clocks, or platform skip-guards.

## Checklist (required)

- [ ] **Tests added/updated** and pass (`pytest -q`, offline & deterministic)
- [ ] **ruff clean** (`ruff check src/ tests/ examples/`)
- [ ] **mypy clean** (`mypy src/werktools`)
- [ ] **Invariants pass** (`werktools hub doctor`: `dependencies = []`, no module-scope
      FastMCP outside allowed files, no daemon in core, unique event names)
- [ ] **Release-hygiene clean** (`python scripts/check_release_hygiene.py --strict`)
- [ ] **No secrets / no personal paths** committed (keys, tokens, absolute home
      paths, machine names); redaction holds and unicode-safety passes

## Guardrail impact

- [ ] Core stays **stdlib-only**; any new dependency is behind an optional extra
      and imported inside function bodies (never module-scope in the core)
- [ ] No new daemon or background network call in the core
- [ ] New write/destructive paths are **fail-closed** and approval-gated
- [ ] New state-changing actions are **ledgered** (tamper-evident events)
- [ ] Public behavior changes (CLI, MCP contract, event names) are documented
      in `CHANGELOG.md`

## Notes for the reviewer

Anything reviewers should focus on, trade-offs made, or follow-ups deferred.
