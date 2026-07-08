# Contributing to werktools / WERKHub

WERKHub is a local, governed MCP control plane. The core stays stdlib-only
and offline-testable. Contributions are welcome under AGPL-3.0-or-later
(the project license — see [LICENSE](LICENSE)).

## Setup

```bash
pip install -e .[all]    # core + server + yaml extras
```

## The local gate (all offline, no network, no keys)

```bash
python -c "from werktools.cli import main; import sys; sys.exit(main(['hub','doctor']))"  # guardrail invariants
ruff check src/ tests/ examples/
mypy src/werktools --ignore-missing-imports
pytest -q
```

`hub doctor` enforces the hard invariants: `dependencies = []`, no
module-scope FastMCP outside the two allowed files, no daemon in the core,
unique event names. Keep them green.

## Hard guardrails

- Core stays stdlib-only; optional deps live behind extras (`[server]`,
  `[yaml]`, `[worker]`, `[lifecycle]`) and are imported only inside function
  bodies, never at module scope in the core.
- No daemon in library modules. Long-lived loops live only inside the serve/
  dashboard entry points.
- Fail-closed policy; everything ledgered; write/destructive actions gated.
- Tests must be offline and deterministic (fake child processes, cassettes,
  golden fixtures, injected clocks; platform skip-guards for win/posix).

## TDD

Write the failing test first, confirm red, implement the minimum, confirm
green. One focused change per commit.
