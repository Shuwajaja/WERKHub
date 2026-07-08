# Security Notes

Status: RC threat-model summary  
Date: 2026-06-11

werktools is a local toolbox/MCP suite, not a runtime and not a service.
These notes state what the suite promises, where the trust boundary sits,
and which invariants are enforced where.

## Threat Model

- Local-first: the **stdlib core has no daemon and no background network
  calls**, and the whole suite's tests run offline and deterministically.
  `werktools hub serve --status-port` and `werktools hub dashboard` run a
  localhost HTTP thread, and three optional surfaces make network calls --
  all are listed under "Network surfaces" below and none fire silently.
- The adversary the tools defend against is a *confused or over-eager
  agent process* operating through the documented surfaces (CLI/MCP), plus
  accidental secret leakage into logs, indexes, and exports.
- A process with **write access to a tool's data directory** (vault dir,
  data-gate dir, trace files, hub config, approvals dir) is outside the
  defended boundary: it is already the local user. Tampered state is
  *detected* where feasible (hash chains) and *neutralized* where state is
  re-derivable (data gate re-validation, vault reveal path check), but a
  full rewrite of all local state by the local user is not detectable
  without an external anchor.
- **Cross-process ledger write-atomicity is deferred (MF11).** `ledger.py`
  serializes concurrent appends within one process via a per-path
  `threading.Lock`; there is no OS-level append lock across separate
  processes. Do not run multiple writers against the same ledger file
  concurrently.

## Enforced Invariants (and where)

| Promise | Enforced in |
| --- | --- |
| Read-only SQLite access, allowlisted tables, masked columns, bounded rows; persisted previews re-validated at execution time | `tools/data_gate.py` (`mode=ro` URI, `query_read` re-derivation) |
| Secret-like payload keys redacted before anything is written | `redaction.py` via `tools/trace.py`, `hub/ledger.py` |
| Secret-like text masked in indexed snippets, including YAML block scalars | `redaction.mask_secret_text` via `tools/vault.py` |
| Downstream auth headers / env values / secret-looking args masked on trace surfaces via opt-in `to_dict(redact=True)` (the default keeps real secrets so a host config can connect) | `hub/contracts.py::DownstreamServer.to_dict` |
| Vault reveal cannot follow a tampered index outside the registered source root; symlinks are not indexed | `tools/vault.py` (`show_item`, `_source_files`) |
| Tamper-evident event chains; cleared hashes and unhashed gaps are integrity errors | `ledger.py`, `tools/trace.py`, `tools/audit.py` (one canonical format) |
| Tool policy fails closed; unknown profiles/tools/scopes deny | `policy.py`, `hub/policy.py`, `tools/integration_gate.py` |
| Integration Gate holds no credentials; secret-like manifest fields are rejected; write scopes require approval; nothing auto-grants | `tools/integration_gate.py` |
| Skills are knowledge assets, never executed | `catalog.py` / `tools/skills.py` (read/export only) |
| MCP handlers always return the envelope, even on crashes or non-serializable results | `server.py` |
| werk-hub executes a tool only when `enforce()` returns `allow`; `enforce()` == the hub explanation (an honest single gate, not a two-axis check); `approval_required`/`hidden`/`deny` fail closed | `hub/policy.py::enforce`, `hub/server.py::_tool_call` |
| werk-hub profile is pinned at server launch; agents cannot self-assert a profile per call | `hub/server.py::build_hub_server`, `cli.py` |
| Discovered tool risk may only tighten (a downstream `readOnlyHint` cannot downgrade a mutating-verb tool to read); discovery/calls are timeout-bounded with honest non-empty timeout messages | `hub/relay.py`, `hub/server.py` |

## Approval-token execution model (ADR-001, now wired)

`approval_required` is no longer explanation-only. A classified call persists
a pending record and returns only a `request_id` to the caller (the one-use
token is surfaced to the *human* via `hub approvals approve`, never to the
agent). The caller retries with `_approval_request_id` + `_approval_token`:

- **Arg-bound (MF2):** the token is bound to a `sha256` of the canonical call
  arguments (worker+model+prompt for `model_worker_call`). A retry with
  different arguments is rejected at `consume_token`.
- **One-use, OS-atomic (MF4):** consumption creates an `O_CREAT|O_EXCL` claim
  file, so exactly one consumer wins even across processes; the token is
  blanked on disk on consume/deny.
- **TTL (MF3):** a token expires 900s after request, enforced **inline at
  `consume_token`**. `sweep_expired()` is a *synchronous, tested helper* for a
  bulk pass -- it is **not yet wired into a production caller**; live expiry is
  the inline check only. No daemon, no background thread.
- **Post-consume recheck (MF3):** `enforce()` re-runs after consume; a since-
  tightened policy fails the call closed even though the token was burned.
- **Relay write-after-token:** token-free auto-forward is reserved for tools
  the *operator* config-pinned as `risk=read`; a self-declared `readOnlyHint`
  is not trusted and needs a token. Admin write/external relayed tools mint an
  approval instead of dead-ending (MF9).

The token is a short-lived plaintext bearer secret on disk between request and
consume; it is blanked on use and never reaches the ledger/trace (only
`request_id` is ledgered). Keep the approvals dir on a user-private volume.

## Network surfaces

All are absent from the stdlib core; none run automatically.

| Surface | When it fires | Gate |
| --- | --- | --- |
| `registry_search` MCP tool | Explicit agent/CLI call | Only `balanced`/`admin` profiles reach the network; `cautious`/`unknown` are denied with no network contact (ledgered `tool.call.denied`) |
| `model_worker_call` | Agent call to a configured worker | Requires a consumed approval token + budget check + model allowlist; uses env-supplied provider keys |
| Status / dashboard HTTP | `hub serve --status-port` / `hub dashboard` | Binds `127.0.0.1` by default; a non-loopback `--host` prints a loud stderr warning |

## Dashboard kill gating

`POST /api/kill` is fail-closed and layered: (1) `WERK_ALLOW_HUB_KILL=1` must be
set; (2) a per-session `secrets.token_urlsafe` token (embedded in the page,
constant-time compared via `X-Werk-Token`, **never ledgered**); (3) loopback
`Host` + same-origin `Origin`; (4) the target pid must be in the live fleet
(`killable` rows of the current snapshot). The wildcard CORS header was removed,
so the custom token header also blocks CSRF. **Currently inert:** the dashboard
is only wired with `stub_processes()` (every `pid=None`, `killable=False`), so
the fleet allowset is empty and every kill is rejected -- correct fail-closed
behavior, not a working kill button. A future caller supplying real killable
rows gets a working, fleet-bounded kill.

## Ledger integrity marker (MF12)

`recent_events_verified()` runs the canonical `tools/audit.py::verify_chain`
over the whole file and surfaces a `chain_verified` bool + integer
`chain_errors` on `GET /api/status` and the `ledger_recent` MCP tool, so a
forged ledger is flagged rather than served as clean evidence. (The
`/api/events` SSE frame carries the event list only, not the marker.) Only the
integer count is exposed -- never payloads or verifier error strings.

## Known Limitations (honest, by design)

- Redaction/masking is heuristic (key-name and line markers). It reduces
  accidental leakage; it is not DLP. Secrets inside free prose with
  unremarkable wording will pass through.
- Hash chains are self-contained: they detect modification and partial
  clearing, not a wholesale rewrite by the local user.
- `vault show --reveal-secrets` intentionally returns unmasked text to the
  operator; every reveal is audited with `revealed: true`.
- The Windows Job-Object kill has a documented narrow startup race (a
  grandchild spawned before job assignment can escape `KILL_ON_JOB_CLOSE`);
  the reaper mitigates by pid/pgid. The real ctypes `CreateProcess` fix is
  deferred.

## Reporting

This is a local, pre-release project. Report issues in the repo's issue
tracker or directly to the operator.
