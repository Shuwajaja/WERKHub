---
name: antigravity-evidence-worker
description: Delegate bounded, low-cost read-only scouting, inventory, document comparison, and independent review tasks to Google Antigravity while keeping Codex as the orchestrator and integrator. Use when a task can be answered from explicitly allowed local files and the result can be treated as imported evidence rather than runtime truth. Do not use for unbounded implementation, secret access, git integration, destructive actions, or autonomous worker execution.
---

# Antigravity Evidence Worker

Use Antigravity Flash as an external evidence worker. Keep orchestration,
integration, safety decisions, and final claims with Codex.

## Operating Model

```text
Codex: decomposes work, sets scope, verifies evidence, integrates conclusions
Antigravity Flash: performs one bounded scout or reviewer task
Result: imported evidence, never an automatic source of truth
```

Create one Antigravity Project per approved task scope. Add only the folders or
repositories needed for that task. Antigravity project boundaries and security
policies are part of the assignment, not an afterthought.

## Select a Mode

| Task class | Antigravity mode | Allowed outcome |
|---|---|---|
| Inventory, document comparison, test-log grouping, read-only review | Local Mode with read-only task rules | Report only |
| Isolated implementation experiment | New Worktree Mode | Diff and validation report; no merge |
| Headless automation | Deferred | Only after the CLI response contract is proven |

Current local status: use **L0 manual handoff**. The local `agy` CLI starts and
can reach the provider, but its print-mode response was not reliably emitted to
stdout and its Agent API needs a configured language-server address. Do not treat
it as a reliable automated worker yet.

## Create a Work Order

Every assignment must include all fields below.

```text
WorkOrder: AGY-<area>-<number>
Role: external_evidence_worker
Model: configured Antigravity Flash option
Mode: Local Mode or New Worktree Mode
Allowed paths: exact absolute paths
Forbidden: secrets, .env*, .mcp.json, credential stores, git push/merge,
           dependency installation, browser use, network use, shell commands
Task: one measurable question
Validation: file list, supplied command output, or explicit checklist
Stop conditions: missing source, scope ambiguity, permission prompt, or any write
Deliverable: report using the Result Contract
```

Use the smallest sufficient scope. Split independent read-only questions into
separate WorkOrders. Do not give Antigravity the whole workspace merely for
convenience.

## Prompt Template

```text
You are an external evidence worker, not the decision maker.

WorkOrder: <id>
Allowed paths: <exact paths>
Task: <one question>

Do not modify files. Do not run commands. Do not access secrets, environment
files, credential files, browser tools, or the network. If the answer requires
anything outside the allowed paths, stop and report BLOCKED.

Return only the Result Contract. Cite each finding with file path and line or
section. Separate facts, inferences, and open questions.
```

## Result Contract

Require this structure. Reject prose that omits scope or evidence.

```json
{
  "work_order": "AGY-area-001",
  "status": "completed | blocked | partial",
  "scope_observed": ["absolute allowed path"],
  "findings": [
    {
      "claim": "short factual claim",
      "evidence": [{"path": "path", "location": "line or section"}],
      "confidence": "high | medium | low"
    }
  ],
  "inferences": [],
  "open_questions": [],
  "changes_made": [],
  "validation": [],
  "limitations": ["external report; provider trace may be unavailable"],
  "truth_level": "imported_evidence"
}
```

Codex must independently spot-check material claims before using them in a plan,
commit, approval, or runtime record.

## Cost and Quality Routing

Use Flash for broad, cheap evidence work:

- file and documentation inventories;
- duplicate or contradiction detection;
- test failure clustering from supplied logs;
- acceptance-checklist review;
- a second, independent reading of a bounded design document.

Escalate to Codex or a stronger reviewer for architecture decisions, security and
permission design, code changes, tests, commits, merges, releases, incomplete
citations, or conflicts between evidence sources.

Never infer a price or quota from a selected Flash model. Record visible usage only
when the Antigravity surface provides it. Stop on a quota, authentication, or model
selection error instead of retrying blindly.

## Evidence Handling

Store the handoff and result together under the task's approved evidence location.
Label the result as:

```text
worker_mode: external_reported
provider_trace_available: false unless explicitly captured
truth_level: imported_evidence
```

Do not import the result as a ledger fact, merge its worktree, or apply its edits
without a separate Codex review and the project-specific validation gate.

## Promotion Gates

Advance beyond manual L0 only when all are true:

1. The CLI emits deterministic machine-readable stdout for a fixed smoke prompt.
2. The exact supported model identifier is discovered from the CLI or official UI.
3. A fake Handoff -> Result Marker -> Import loop passes without a provider call.
4. Result imports preserve the evidence labels above.
5. A path-lock or isolated-worktree rule exists before any write-capable task.

Until then, do not build a subprocess runner, PTY integration, background watcher,
or autonomous loop around Antigravity.
