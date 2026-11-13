# werktools changelog history

This file preserves the full `CHANGELOG.md` from the private repository
`Shuwajaja/werktools` at commit `c22a43a` (08.07.2026).

The version numbers recorded here (including 0.2.x) are historical.
WERKHub itself stays at version 0.1.0; this copy does not bump the package
version.

---

# Changelog

All notable changes to werktools are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.1] - 2026-06-27

Relay interop, found live-testing real downstream MCPs behind the hub (each
hardened across two adversarial review rounds).

### Added - relay interop (live-testing against real downstream MCPs)

- **Per-downstream working directory.** `DownstreamServer` gains an optional
  `cwd` field, threaded through `relay.transport_for` into the FastMCP stdio
  spec, so context-dependent MCPs that a host launches from a specific directory
  can be reproduced behind the hub.
- **Discovery stderr diagnostics.** When a stdio downstream fails discovery,
  FastMCP's `Client` raises a generic "Failed to initialize server session"
  that hides the server's own stderr. The `tool.discovered` error event now
  carries a secret-masked tail of the downstream's startup stderr (e.g. a
  Windows "Zugriff verweigert"/access-denied), so operators see *why* without a
  manual probe. Best-effort, error-path only, thread-free (`subprocess.run` with
  a timeout -- core modules forbid `threading.Thread`).
- **Discovery-failure classifier (`hub/diagnose.py`).** Maps the failure to an
  actionable verdict `{cause, supported, remedy, diagnosis_heuristic}` merged
  into the `tool.discovered` event: `single_instance` (exclusive port/lock --
  the codebase-memory-mcp case), `not_found`, `needs_auth`,
  `missing_cwd_or_file` (the only `supported=True`), `startup_hang`, or
  `unknown` (fail-closed). Pure stdlib, ASCII-only, flagged heuristic.

> Known limitation (not addressed here): the relay still spawns a fresh
> downstream subprocess per discovery/call (`hub/lifecycle.py`), which is
> incompatible with **single-instance** MCPs that hold an exclusive lock (a
> second copy gets access-denied). A connection-reuse / attach-to-running mode
> is a larger design change tracked separately.

## [0.2.0] - 2026-06-27

First WERKHub release: the local governed MCP control plane built across the
goal-spec waves. Agents connect to ONE MCP server (`werktools hub serve`) which
routes to all downstream MCPs through a deny-by-default policy gate + a
tamper-evident, hash-chained evidence ledger. Install: `uvx --from
"werktools[server]" werktools hub serve` (see SETUP.md).

### Added - the desktop Doctor (onboard, this release)

- **`GET /api/onboard` + gated `POST /api/onboard/apply`** in `hub/dashboard.py`
  so the desktop UI can run the onboarding Doctor (the CLI is for agents). Dry-run
  discovery is loopback-only and presence-only (env-var key NAMES only, never
  values); apply is fail-closed behind `WERK_ALLOW_HUB_ONBOARD` + a session token
  and reuses `onboard(apply=True)` (persists hub.json + ledgers
  `config.connector.added`). Unknown host → 400, missing config_path → 503.
- **Desktop Onboard view** (`desktop/src/views/Onboard.tsx`): host-grouped
  discovery, presence-only key chips, confirm-gated Adopt, honest-degrade.
- **Codex TOML on Python 3.10** — `_parse_toml_servers` falls back to the `tomli`
  backport (new optional extra `onboard`), so the Doctor reads
  `~/.codex/config.toml` without a Python upgrade. Core stays zero-dependency.

### Added - P1 (WERKHub runtimes + trust)

- **Host runtime detection (`hub/runtimes.py`).** `RuntimeDescriptor` registry
  + `RuntimeProbe`/`RuntimeReport` + `probe_one`/`probe_all`/`get_descriptor`
  over a canonical 9-host roster (claude, codex, cursor, vscode, windsurf,
  goose, gemini, kimi, antigravity; goose+gemini flagged at-risk). Detection
  via `shutil.which` + known GUI/config paths. **Token health is
  presence/date ONLY — the value is never read.** Pure stdlib; the version
  subprocess probe is deferred behind `probe_versions=True` (the default path
  is subprocess-free).
- **`hub doctor` runtimes panel** (text + monochrome monogram placeholders, no
  vendor logos) plus `--json`, `--host`, `--probe-versions`, `--detected-only`.
  Emits the `runtime.probed` ledger event.
- **Trust taxonomy (metadata only, never an enforcement decision in P1):**
  `Official` / `Security-Scanned` / `Community-Unverified` on `ToolCard`,
  `DownstreamServer`, `CatalogCard`, SKILL.md frontmatter, and Integration-Gate
  connectors. Default and every unknown value fail closed to
  `Community-Unverified`.
- **Tier-1 install allowlist (`hub/allowlist.py`).** A commit-pinned curated
  set (70 servers seeded from the Tier-1 MCP research) gates
  `discovery.stage_install` / `approve_and_write` deny-by-default: a candidate
  not on the allowlist stays `Community-Unverified` and carries an `[UNVETTED]`
  note; the registry stays discovery-only. An invalid override file fails
  closed. Honest-degrade: the seed carries no real OCI digests yet
  (`digest=unpinned` in the trust note); a digest backfill is a follow-up.
- **Three new ledger events** (EVENT_NAMES total 40 -> 43): `runtime.probed`,
  `registry.allowlist.error`, `registry.allowlist.tier_downgrade`.
- New optional `HubConfig.tier1_allowlist_path` field. See ADR-004 (string
  trust tiers) and ADR-005 (deny-by-default Tier-1 allowlist).

### Hardened (review-driven security pass)

- **Approval tokens are argument-bound, expiring, and OS-atomic.** A token is
  bound to a `sha256` of the call arguments (worker+model+prompt for
  `model_worker_call`) -- an arg-swapped retry is rejected. Tokens expire 900s
  after request (enforced inline at consume; `sweep_expired()` is a tested
  helper, not yet wired to a production caller). Consumption uses an
  `O_CREAT|O_EXCL` claim file so exactly one consumer wins across processes,
  and `enforce()` re-runs after consume so a token can never bypass a
  since-tightened policy. Admin write/external relayed tools now mint an
  approval instead of dead-ending.
- **Relay trust boundary tightened.** Token-free auto-forward is reserved for
  tools the operator config-pinned as `risk=read`; a downstream-declared
  `readOnlyHint` is no longer trusted on its own. `enforce()` collapsed to the
  honest single-gate contract (`enforce()` == the hub explanation is `allow`);
  the prior "two-axis" framing was a tautology and its dead branch was removed.
  The fictional warm-pool relay path was deleted -- `serve` spawns a fresh
  downstream subprocess per call; `hub/lifecycle.py` is the real PID reaper.
- **Meta-tools gated; secret redaction extended.** `registry_search`'s outbound
  call is denied for `cautious`/`unknown` profiles (no network); `approval_status`
  is scoped to the caller's profile. `DownstreamServer.to_dict(redact=True)`
  masks auth headers / env / secret-looking args on trace surfaces. Downstream
  timeouts now produce honest non-empty error messages.
- **Ledger integrity surfaced (MF12).** `recent_events_verified()` adds a
  `chain_verified` bool + integer `chain_errors` to `GET /api/status` and the
  `ledger_recent` MCP tool, so a forged ledger is flagged not served as clean
  evidence. (The `/api/events` SSE frame still carries the event list only.)
- **Dashboard hardened.** Wildcard CORS removed; a non-loopback `--host` bind
  warns loudly. `POST /api/kill` now requires a per-session `X-Werk-Token`
  (never ledgered) + loopback `Host` + same-origin `Origin`, and is constrained
  to live-fleet PIDs (currently inert under stub-only wiring -- fail-closed).
- **CLI / packaging / gate honesty.** `hub serve` accepts `--config` after the
  subcommand (the host-registration snippets work); the FastMCP module-scope
  scanner now catches indented imports with an exact-path allowlist; `[all]`
  pulls `[worker]`; `canon check --strict` is green.

> **MF11 (cross-process ledger append-lock) is now implemented.** `ledger.append`
> takes an exclusive advisory lock on a sidecar `<ledger>.lock` (`fcntl.flock` on
> POSIX, `msvcrt.locking` on Windows) around the read-prev-hash + write critical
> section, in addition to the existing in-process `threading.Lock`. Concurrent
> appends from separate OS processes (serve + CLI + dashboard) can no longer read
> the same `prev_hash` and fork the tamper-evident chain. Crash-safe (the OS
> releases the advisory lock on process death — no stale lock to reap); blocking
> acquire, so the core stays free of any spin loop. Verified by a
> multiprocessing-spawn test asserting the chain stays linear and verifies.

### Hardened (autonomous adversarial sweep, 9 cycles)

A nine-cycle autonomous quality loop (parallel ECC finder agents -> adversarial
confirm -> builder -> Opus gate-verify + per-cycle commit, stopped at effective-dry).
802 tests green throughout; `EVENT_NAMES` held at 43, no new dependencies.

- **Approval call-argument binding is now mandatory.** `consume_token`'s
  `expected_args_hash` is required (was optional with a skip-if-`None` guard), so
  the call-argument binding can no longer be bypassed by omission.
- **Path traversal blocked on both approval paths.** `discovery.approve_and_write`
  (format-validates `req_*` ids + `resolve()`/`relative_to` containment) and
  `approvals._claim_path`/`_record_path` (validate `apr_*` ids before any FS access).
- **OCI install hardening.** `candidate_to_downstream` validates package names
  before building an argv: npm/pypi name allow-lists, and OCI images restricted to
  `^mcp/<name>[:tag]` -- this blocks `docker run` flag-injection via a
  registry-supplied name *and* enforces deny-by-default for OCI. (Loosen the regex,
  keeping the no-leading-dash property, if a non-`mcp/` OCI image must be installable.)
- **Provider API keys never reach a traceback.** Worker HTTP errors re-raise as a
  status-code-only `RuntimeError ... from None` (the httpx exception carries the
  request headers, and a 401 body can echo a partial key).
- **Dashboard hardened.** Loopback `Host` guard on every GET endpoint (incl. `/`),
  IPv6-bracketed `Host` parsing fixed, `X-Frame-Options`/`X-Content-Type-Options`/
  `Content-Security-Policy` on all responses, oversized request bodies capped (413
  with connection close). The kill-button badge now renders (`kill_allowed` is
  emitted in the snapshot).
- **Silent failures surfaced (honest-degrade).** ~15 bare `continue`/`pass` swallows
  across `catalog`, `ledger`, `approvals`, `lifecycle`, `discovery`, `integration_gate`,
  `vault`, `truth`, `audit`, `cost`, `mine`, and the runtime probe now emit
  `warnings.warn`; failed process kills are preserved for retry; consume-lock eviction
  runs on every exit path; robust provider-response and `load_workers` parsing.
- **Type tightening.** `PolicySnapshot` dict fields, `PoolState` -> `Literal`,
  `_runtime_row` annotations.

### Changed - dashboard (P2: "Bento" control-plane UI)

- **`hub dashboard` rebuilt to the WERK Hub "Bento" design handoff** within the same
  constraints (pure stdlib, single inline page, CSP-safe with zero external resources,
  A-FINAL tokens + Seam-W mark, scales to 30+ MCPs): a sticky topbar (Seam-W + wordmark,
  fail-closed dot, live clock, gated **Kill-switch** confirm strip), three tabs — **Board**
  (bento: connectors-live, pure-CSS Trust-posture donut, Agent runtimes, Evidence ledger),
  **Timeline** (full ledger), **Connectors** (connector table + the live process fleet with
  per-process kill + registry browse) — and a status bar. All live tiles are computed
  client-side from the EXISTING `/api` fields; `DASHBOARD_HTML` is a raw string.
- **Colored vendor-style host glyphs** (Claude / Codex / Gemini / DeepSeek / Ollama /
  OpenRouter / MCP), an operator-approved departure from the earlier monochrome rule; hosts
  without a brand glyph fall back to a monochrome monogram chip. (Faithful re-creations, not
  official vendor SVGs.)
- **Honest-degrade seams.** Tiles with no hub API yet (Needs-you / Model-routing / Spend /
  the approval-queue modal, and the connectors-live trend sparkline) carry a dashed amber
  "not wired" seam with a CLI fallback instead of fabricated values.
- **Evidence ledger** renders the handoff's 5-column tone grid (time / event-kind in its
  per-kind tone / detail / id / sha) on the Board tile and Timeline tab. Fixed a real bug:
  the server emits a NAMED `ledger` SSE event, so the client now `addEventListener`s it
  (`onmessage` alone left the ledger blank on the real server).
- **Live-config consistency.** `/api/status`, the SSE snapshot, and the kill path all read
  the live `cfg_box[0]` (an in-session connector write no longer desyncs the views or the
  kill gate). Kill / toggle failures are surfaced (no silent swallow).
- **Accessibility (WCAG 2.2 AA pass).** Tablist keyboard nav (Arrow/Home/End + roving
  tabindex, `aria-controls`/`aria-labelledby`); focus-on-open + Escape + return-focus +
  Tab-trap for the kill strip and approval modal; `aria-label` on icon/row buttons;
  `role=table/row/columnheader/cell` on the connector and process grids; `prefers-reduced-
  motion` honored; aria-live polling noise removed. Contrast raised to AA where it failed
  (disabled-ink data text, the donut Unverified segment, the ledger link, input focus ring).
- Preserved throughout: every dashboard test invariant, the XSS-safe delegated click
  handler, `WERK_TOKEN`, and the security headers / loopback / 413 guards. node --check clean.

### Added - WERKHub build (goal-spec waves)

- **Wave 0 (CC-001):** shared offline test infra (frozen_clock, fake child,
  cassette), `DownstreamServer` transport/url/headers/env extension,
  `hub/store_bridge.py` (ADR-002: connectors.json <-> hub.json), machine-checkable
  `hub/invariants.py` + `werktools hub doctor`, `[lifecycle]` extra.
- **Wave 1 (S4):** file-based approval queue with one-use execute-after-token
  (`hub/approvals.py`, the ADR-001 revisit). `approval_required` now mints a
  pending record + opaque token; `hub approvals list/approve/deny`; the
  `tool_call` token path validates (`hmac.compare_digest`), consumes
  atomically before execution, runs exactly once, and lifts the relay's
  non-read guard only on the consumed-token path. Tokens never re-use.

### Added - werk-hub MCP server (the one-MCP front door)

- `werktools hub serve --profile <id>` runs the **werk-hub** stdio MCP
  server: a single host-facing entry (Claude Code/Codex/Cursor) exposing
  eight always-on bridge tools (`tool_search`, `tool_describe`, `tool_call`,
  `profile_info`, `ledger_recent`, `approval_status`, `hub_status`,
  `registry_search`), plus four `model_worker_*` tools when a worker manifest
  is configured. Profile is pinned at launch (`--profile` /
  `WERKTOOLS_HUB_PROFILE`).
- `hub.policy.enforce()` fail-closed execution gate (ADR-001): a tool executes
  only when `enforce()` returns `allow`; `approval_required`/`hidden`/`deny`
  never execute. (`enforce()` == the hub explanation -- the earlier "two-axis"
  framing was collapsed to this honest single gate in the hardening pass.)
- `hub/relay.py` downstream MCP forwarding: discovers tools on the active
  profile's `allowed_servers`, normalizes them into ToolCards where
  annotations and keyword signals may only TIGHTEN risk, and forwards calls
  through the policy gate. Token-free auto-forward is reserved for
  operator-pinned `read` tools; write/destructive (and self-declared read)
  relayed tools require a consumed approval token. Discovery and calls are
  timeout-bounded.
- README host-registration snippets (.mcp.json / Codex toml);
  canonical server name settled to `werk-hub` (HubConfig default updated).

## [0.1.0] - 2026-06-10 (release candidate)

First coherent cut of the WERK Tools suite: stdlib-only core primitives,
a static local Hub, eleven local tool slices, and an RC fix pass.

### Added

- Core primitives: `envelope`, `profile`, `ledger`, `classify`, `policy`,
  `redaction` (shared secret masking), `catalog` (generic local catalogs),
  optional FastMCP helper `server` (extra `[server]`).
- Static WERK Hub: contracts, registry, policy explanation, ledger events,
  capability catalog, and the `werktools hub|capability` CLI.
- Tool slices with CLI surfaces: Truth Auditor, WERK Mine, WERK Trace,
  WERK Vault, WERK Data Gate, WERK Swarm (+ `werktools-swarm` alias),
  WERK Cost, WERK Eval, WERK Audit, Skill Library, Integration Gate
  (catalog-only).
- Examples: `echo_server.py` (synthetic) and `truth_server.py` (real tools
  over FastMCP, self-asserting).
- ADR-001 documenting the core-enforcement vs hub-explanation policy split.
- Acceptance matrix, security notes, and deferred-spec record.

### Fixed (2026-06-10 review-driven fix pass)

- cost: lifecycle events no longer counted as unknown spend (budget gate
  works on real event ledgers); non-finite amounts count as unknown;
  invalid budgets return a structured error decision.
- data gate: `query_read` re-derives SQL, allowlist, masking, and row limit
  from live source config (tampered previews cannot widen access); masking
  is case-insensitive.
- trace/audit: cleared-hash and unhashed-after-chain-start tampering are
  detected; appends link across unhashed gaps; one canonical hash format
  (GENESIS sentinel, `ensure_ascii=False`) shared by ledger, trace, audit.
- classify: removed command/spawn/token false-positive patterns.
- server: non-serializable handler results return the err envelope.
- vault: reveal re-validates paths against the registered source root,
  audits the reveal flag, skips symlinked files.
- swarm: agent ids validated (no path traversal); malformed plans raise
  `ValueError`.
- hub: `hub init` is idempotent.
- All JSONL readers skip corrupt lines instead of crashing.
- packaging: `[swarm]` extra no longer pulls FastMCP; `.deepeval/` ignored.

### Breaking

- Trace files written before 0.1.0 (empty-string genesis, ASCII hash
  canonicalization) no longer verify; re-create local traces.
- Data Gate previews no longer persist raw SQL; old previews still load.

### Deferred (documented, not v0)

Hook Catalog, Plugin Packs, WERK Intake, Agent Team Factory, Monetization
Pattern Mine, Design Extractor - see
the deferred-spec record (internal). (Model Workers shipped in the
0.2.0 RC and are no longer deferred.)
