# Tool Acceptance Matrix

Status: RC evidence  
Date: 2026-06-11

MCP status legend: `cli-only` = full local CLI surface, no dedicated MCP
server yet (the FastMCP helper in `werktools.server` can expose any handler;
`examples/truth_server.py` demonstrates this with real tools).

| Tool | Spec | CLI surface | MCP status | Tests | Acceptance criteria | Known-deferred |
| --- | --- | --- | --- | --- | --- | --- |
| Core primitives (envelope, profile, ledger, classify, policy, redaction, catalog) | 05_SPEC_werktools-core-design.md (WERK-Lab) | n/a (library) | helper (`werktools.server`) | test_envelope/profile/ledger/classify/policy/server/redaction/catalog | stdlib-only, fail-closed policy, offline | approval verdict in core policy (ADR-001) |
| WERK Hub (static) | SPEC_MCP_WERK_HUB.md | `werktools hub init/status/tools/policy explain` | cli-only | test_hub_contracts/registry/policy/ledger/cli | static/local, idempotent init, fail-closed explain | - |
| werk-hub MCP server | SPEC_MCP_WERK_HUB.md | `werktools hub serve --profile <id>` (stdio) | **live MCP server** (8 always-on bridge tools + 4 conditional `model_worker_*`) | test_hub_server, test_hub_relay, test_hub_cli, test_hub_approvals_server | one host entry, profile pinned at launch, honest single `enforce()`==explain gate, policy-gated downstream forwarding (operator-pinned `read` auto-forwards; writes need a token), every call ledgered | MF11 cross-process ledger lock (deferred) |
| Approval queue (execute-after-token) | SPEC_MCP_WERK_HUB.md (ADR-001 revisit) | `werktools hub approvals list/approve/deny` | via the hub `tool_call` token path | test_hub_approvals, test_hub_approvals_server | one-use, arg-bound (sha256), 900s TTL (inline at consume), OS-atomic consume, post-consume `enforce()` recheck; token never ledgered | `sweep_expired()` not yet wired to a production caller |
| Model Workers | SPEC_MCP_MODEL_WORKERS.md | hub tools `model_worker_list/budget_check/call/report` | live (when a worker manifest is configured) | test_hub_workers, test_hub_workers_server | governed + budgeted; self-approval blocked; model allowlist enforced; env-only keys | live provider pricing |
| Hub dashboard / status API | STATUS_API_CONTRACT.md | `werktools hub dashboard` / `hub serve --status-port` | localhost HTTP (stdlib) + SSE | test_hub_dashboard, test_hub_server (status endpoint) | binds 127.0.0.1 (non-loopback warns); no CORS; `chain_verified` marker on /api/status; `/api/kill` gated (env + per-session token + loopback/origin + live fleet) | kill button inert until a real ProcessRegistry is wired |
| Capability Catalog | SPEC_MCP_CAPABILITY_CATALOG.md | `werktools capability list/show/classify/export` | cli-only | test_capability_catalog | deterministic cards, offline classify, explicit export | - |
| Truth Auditor | SPEC_MCP_TRUTH_AUDITOR.md | `werktools truth scan/report` | demoed (`examples/truth_server.py`) | test_truth_auditor | no code execution, report only on request | claim checking beyond paths/URLs |
| WERK Mine | SPEC_MCP_WERK_MINE.md | `werktools mine extract/index/query/report` | cli-only | test_werk_mine | provided files only, `provided_unverified` status | autonomous browsing (never) |
| WERK Trace | SPEC_MCP_WERK_TRACE.md | `werktools trace append/recent/verify/export` | cli-only | test_werk_trace | append-only, redaction, chain verify (GENESIS canonical) | scheduling/resume (never) |
| WERK Vault | SPEC_MCP_WERK_VAULT.md | `werktools vault init/add-source/sources/search/show/explain-access/audit tail` | cli-only | test_werk_vault | source-scoped, profile-filtered, masked, audited, reveal path-checked | encryption at rest |
| WERK Data Gate | SPEC_MCP_WERK_DATA_GATE.md | `werktools data add-source/sources/schema/preview/read/audit` | cli-only | test_werk_data_gate | read-only SQLite, allowlist re-validated at read time, masked, bounded | non-SQLite backends |
| WERK Swarm | SPEC_MCP_WERK_SWARM.md | `werktools swarm plan/packet/collect/review` (+ `werktools-swarm` alias) | cli-only | test_werk_swarm | handoff helper only, validated agent ids | agent execution (never) |
| WERK Cost | SPEC_MCP_WERK_COST.md | `werktools cost record/rollup/budget-check/report` | cli-only | test_werk_cost | unknown-cost honesty, fail-closed budget, lifecycle events excluded | live provider pricing |
| WERK Eval | SPEC_MCP_WERK_EVAL.md | `werktools eval list/run/report` | cli-only | test_werk_eval | offline cassette compare, no model calls | live evals |
| WERK Audit | SPEC_MCP_WERK_AUDIT.md | `werktools audit verify/redact/export/report` | cli-only | test_werk_audit | single canonical chain format, explicit redaction, honest missing-files | legal compliance claims (never) |
| Skill Library | SPEC_MCP_SKILL_LIBRARY.md | `werktools skills list/show/match/export` | demoed (`examples/truth_server.py`) | test_skill_library | skills never executed, source tracked, profile visibility, explicit export | skill versioning |
| Integration Gate | SPEC_MCP_WERK_INTEGRATION_GATE.md | `werktools integration add/list/show/explain/request-access/audit` | cli-only | test_integration_gate | catalog-only, no secrets, write scopes approval_required, audited | call_read/call_write/sync/token_health (gated on ADR-001) |

Deferred specs remain documented in the internal planning set and are not part
of the public docs site.
