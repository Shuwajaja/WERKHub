# AGENTS.md

Project context for AI coding agents (and humans) working on **werktools / WERKHub** —
the local, governed MCP control plane. This file is the authoritative source of repo
rules; see [README.md](README.md) for the full overview and [CONTRIBUTING.md](CONTRIBUTING.md)
for setup.

## What this repo is

A Python package (`werktools`) that ships the WERKHub MCP gateway: a stdio MCP server, a
local HTTP dashboard, a CLI, and local tool slices. A separate native desktop console
lives under `desktop/`. The package core is **stdlib-only and offline-testable**.

## Hard rules

- **Keep the core dependency-free.** Third-party imports are allowed only inside the
  optional extras (`server` / `worker` / `yaml`); every other sub-import must succeed with
  zero third-party packages installed (`tests/test_extras_matrix.py` enforces this).
- **No daemon constructs in core modules.** No extra OS threads, busy-loops, scheduled
  async tasks, exit hooks, or threaded HTTP servers. This is machine-checked by the
  invariants module under src/werktools/hub (its run_all check must return all-empty).
- **Deny-by-default, fail-closed.** A tool runs only when `enforce()` returns `allow`.
  Resolve unknown or ambiguous states to the safe option.
- **Presence-only on secrets.** Extract env-var key *names*, never values; mask secrets
  before anything is logged or written to the ledger.
- **The ledger is append-only and hash-chained.** Treat JSONL events, SQLite, and the
  ledger as the source of truth — not draft Markdown.
- **ASCII-only in printed/logged output.** No em-dashes or curly quotes in strings the CLI
  or ledger emit (they corrupt on legacy code pages).

## Workflow

Before reporting a change complete, run and pass:

```
pytest -q                                                   # the test suite
python -m mypy src/werktools                                # types
python -m ruff check src                                    # lint
python -c "from werktools.hub.invariants import run_all; print(run_all())"   # invariants
python -m werktools canon check --strict                    # repo-canon and release hygiene
```

Agents should:

- inspect the existing structure before changing code;
- prefer small, reviewable changes and add tests for new behavior;
- update docs when behavior changes; keep generated files separate from source;
- preserve evidence and avoid deleting files unless explicitly instructed.

Agents should not:

- invent missing architecture or silently rewrite unrelated files;
- treat draft documents as production truth;
- bypass the policy, approval, or invariant layers.

## License

AGPL-3.0-or-later. Contributions are accepted under the same license.
