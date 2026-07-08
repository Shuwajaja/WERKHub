---
name: Bug report
about: Report a reproducible defect in WERKHub (the local, governed MCP control plane)
title: "bug: <short summary>"
labels: [bug, triage]
assignees: []
---

<!--
For a security vulnerability, DO NOT open a public issue.
Use GitHub private vulnerability reporting (Security > Report a vulnerability)
and see SECURITY.md. This keeps the fail-closed trust boundary intact.
-->

## Summary

A clear, one-sentence description of the defect and the observed wrong behavior.

## Surface

Which WERKHub surface is affected (check all that apply):

- [ ] CLI (`werktools hub ...`)
- [ ] MCP server (`hub serve`)
- [ ] Dashboard (`hub doctor` / `hub dashboard`)
- [ ] Policy / classification (gating, allowlist, approval tokens)
- [ ] Ledger / evidence chain
- [ ] Other (describe below)

## Steps to reproduce

A minimal, deterministic, offline reproduction (no network, no real keys).

```text
1.
2.
3.
```

## Expected behavior

What you expected to happen. If this involves a guardrail (deny-by-default,
fail-closed, approval-gated, ledgered), state the invariant you expected to hold.

## Actual behavior

What actually happened. Paste the exact envelope / error output. Redact any
secrets and personal paths before pasting.

```text
<output>
```

## Environment

- WERKHub version: <e.g. 0.2.0> (`werktools --version` / pyproject)
- Python version: <e.g. 3.10 / 3.11 / 3.12>
- OS: <e.g. Windows 11 / Ubuntu 22.04>
- Install extras: <core only / [server] / [yaml] / [worker] / [all]>

## Impact

- [ ] Guardrail bypass or fail-open behavior (security-relevant — consider private reporting instead)
- [ ] Data loss / ledger integrity
- [ ] Crash / unhandled error
- [ ] Incorrect result, no integrity impact
- [ ] Cosmetic / docs

## Additional context

Logs, ledger excerpts (redacted), or links. Confirm no secrets or personal
paths are present in anything you paste.
