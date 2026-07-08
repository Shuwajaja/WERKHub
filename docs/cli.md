# CLI reference

All commands are available via `werktools`. The global `--config` flag sets the
hub config path (default: `.werktools/hub.json`).

```
werktools [--config PATH] <command> [subcommand] [flags]
```

---

## hub

Static hub inspection and live server management.

### hub init

```bash
werktools hub init
werktools hub init --community     # neutral community profiles
```

Creates `.werktools/hub.json` with deny-by-default policy. Safe to re-run —
an existing config is left unchanged.

### hub status

```bash
werktools hub status
```

Prints hub name, default profile, profile count, tool count, and ledger path.

### hub doctor

```bash
werktools hub doctor
werktools hub doctor --json
werktools hub doctor --probe-versions
werktools hub doctor --host claude
werktools hub doctor --detected-only
```

Checks internal invariants, reads the config, and probes known agent hosts for
binary paths, config files, and token-env presence. `--probe-versions` opts into
subprocess-based version detection (slower). Exit code 0 = all clear.

### hub serve

```bash
werktools hub serve --profile claude-reviewer
werktools hub serve --profile codex-builder --config .werktools/hub.json
werktools hub serve --status-port 9371
```

Starts the live MCP relay over stdio. Requires `pip install werktools[server]`.
The profile can also be set via `WERKTOOLS_HUB_PROFILE`. Downstream servers
are spawned as subprocesses per call; the lifecycle manager tracks and reaps PIDs.

### hub dashboard

```bash
werktools hub dashboard
werktools hub dashboard --open          # open browser
werktools hub dashboard --host 127.0.0.1 --port 7879
```

Serves the local Bento control-plane dashboard. Bound to `127.0.0.1` only.

### hub doctor

See above.

### hub onboard

```bash
werktools hub onboard                   # dry-run
werktools hub onboard --apply           # write connectors to hub.json
werktools hub onboard --host claude
werktools hub onboard --home /path/to/home
```

Discovers existing MCP configs from known host locations and adopts them as
connectors. Presence-only key handling — never reads token values.

### hub tools

```bash
werktools hub tools
werktools hub tools --profile codex-builder
```

Lists all visible tools for the given profile with their risk class and policy
decision (`allow` / `approval_required` / `hidden` / `deny`).

### hub policy explain

```bash
werktools hub policy explain github.create_pr
werktools hub policy explain github.create_pr --profile codex-builder
```

Explains the policy decision for a specific tool in the given profile.

### hub registry

```bash
werktools hub registry build
werktools hub registry build --skills-dir .werktools/skills
werktools hub registry list
werktools hub registry list --category filesystem
werktools hub registry list --deluxe
werktools hub registry select "review the PR" --budget 8
werktools hub registry search --query "github"
werktools hub registry install --query "github"
werktools hub registry approve --request-id <id> --hub-config .werktools/hub.json
```

See [Registry & capabilities](registry.md) for the full flow.

### hub approvals

```bash
werktools hub approvals list
werktools hub approvals approve <request_id>
werktools hub approvals deny <request_id>
```

Human-side approval management. `approve` mints the one-use, argument-bound
token. `deny` closes the request permanently.

### hub export-rules

```bash
werktools hub export-rules \
  --agents-md AGENTS.md \
  --skills-dir .werktools/skills \
  --out .werktools/rules-export \
  --profile claude-reviewer \
  --host claude
```

Exports a host-specific rules bundle (skills + profile manifest) to a directory.

### hub pool-status

```bash
werktools hub pool-status
werktools hub pool-status --profile codex-builder
```

Prints the current hub fleet snapshot as JSON (reads `/status` directly, no
status-port required).

### hub reap

```bash
werktools hub reap
werktools hub reap --ttl 300
werktools hub reap --sidecar .werktools/hub-procs.json
```

Sweeps orphaned or TTL-expired downstream relay processes. `--ttl 0` (default)
sweeps genuinely orphaned PIDs only.

### hub render

```bash
werktools hub render --host claude --out .mcp.json
werktools hub render --host codex --profile codex-builder
```

Renders a host-specific MCP config snippet from the hub config.

---

## capability

Static capability card inspection (no server required).

```bash
werktools capability list
werktools capability show docs.search
werktools capability classify .werktools/my-tool-manifest.json
werktools capability export --out .werktools/capabilities.json
```

---

## bench

```bash
werktools bench run bench-spec.json bench-variants.json --out results.json
werktools bench report results.json
werktools bench report results.json --csv-out matrix.csv
werktools bench judge results.json bench-spec.json --out judged.json
```

See [Bench matrix](bench.md).

---

## trace

Append-only local event ledger.

```bash
werktools trace append --file .werktools/trace.jsonl \
  --type tool.call.completed --actor me --payload '{"status":"ok"}'
werktools trace recent --file .werktools/trace.jsonl --limit 20
werktools trace verify .werktools/trace.jsonl
werktools trace export .werktools/trace.jsonl --out filtered.jsonl \
  --type tool.call.completed
```

---

## audit

Chain verification, redaction, and evidence export.

```bash
werktools audit verify .werktools/trace.jsonl
werktools audit redact .werktools/trace.jsonl --out redacted.jsonl
werktools audit export --out .werktools/audit-bundle \
  --trace .werktools/trace.jsonl --evidence docs/report.md
werktools audit report .werktools/trace.jsonl --out audit.md
```

---

## truth

Repo content auditor.

```bash
werktools truth scan --repo .
werktools truth report --repo . --out docs/TRUTH_REPORT.md
```

---

## skills

Local Markdown skill library (catalog-only; skills are never executed).

```bash
werktools skills list --dir .werktools/skills
werktools skills show review-policy --dir .werktools/skills
werktools skills match "review the policy module" --dir .werktools/skills
werktools skills export --dir .werktools/skills --out .werktools/skills.json
```

---

## vault

Local knowledge source registry with profile-based access control.

```bash
werktools vault init --dir .werktools/vault
werktools vault add-source ./docs --dir .werktools/vault \
  --label project-docs --class internal --profile default
werktools vault sources --dir .werktools/vault
werktools vault search "approval policy" --dir .werktools/vault --profile default
werktools vault show <item-id> --dir .werktools/vault --profile default
werktools vault explain-access <source-id> --dir .werktools/vault --profile default
werktools vault audit tail --dir .werktools/vault --limit 20
```

---

## data

Governed SQLite data access (allowlisted tables, row limits, column masking).

```bash
werktools data add-source ./support.db --dir .werktools/data-gate \
  --source support --table cases --mask email --limit 50
werktools data sources --dir .werktools/data-gate
werktools data schema support --dir .werktools/data-gate
werktools data preview --source support --intent "critical cases" \
  --dir .werktools/data-gate --profile default
werktools data read --preview-id <id> --dir .werktools/data-gate
werktools data audit --dir .werktools/data-gate --limit 20
```

---

## mine

Local notes-to-knowledge-card pipeline.

```bash
werktools mine extract docs/notes.md --out .werktools/mine --topic governance
werktools mine index .werktools/mine
werktools mine query "agent governance" --dir .werktools/mine
werktools mine report --dir .werktools/mine --out docs/MINE_REPORT.md
```

---

## swarm

Local work-packet handoff helper (starts no agents, edits no repos).

```bash
werktools swarm plan docs/GOAL.md --out .werktools/swarm --repo <repo> --agents 3
werktools swarm packet agent-1 --dir .werktools/swarm
werktools swarm collect .werktools/swarm-reports
werktools swarm review .werktools/swarm-reports
```

---

## cost

Local cost event ledger and rollup.

```bash
werktools cost record .werktools/cost.jsonl \
  --mission m1 --task docs --tool model --model gpt --amount 0.50
werktools cost rollup .werktools/cost.jsonl
werktools cost budget-check .werktools/cost.jsonl --budget 5.00
werktools cost report .werktools/cost.jsonl --out cost.md --budget 5.00
```

---

## eval

Offline cassette comparison (no model calls).

```bash
werktools eval list tests/cassettes
werktools eval run tests/cassettes/hub_policy.json
werktools eval report --dir tests/cassettes --out .werktools/eval.md
```

---

## integration

Connector manifest catalog (catalog-only; holds no credentials, calls no external system).

```bash
werktools integration add github --dir .werktools/integration-gate \
  --provider github.com --scope "repo:read=read:Read repository contents"
werktools integration list --dir .werktools/integration-gate
werktools integration show github --dir .werktools/integration-gate
werktools integration explain github --dir .werktools/integration-gate --scope repo:read
werktools integration request-access github --dir .werktools/integration-gate --scope repo:read
werktools integration audit --dir .werktools/integration-gate --limit 20
```
