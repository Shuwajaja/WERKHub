# Onboarding your MCPs

WERKHub can discover the MCP servers you already have wired into Claude Code,
Codex, Cursor, Windsurf, and other hosts — and adopt them into one governed
`hub.json` config in a single command.

## Discovery: what it reads

`hub onboard` scans well-known config locations for each host:

| Host | Config location (typical) |
|---|---|
| Claude Code | `~/.claude/settings.json`, `.mcp.json` |
| Codex | `~/.codex/config.toml` |
| Cursor | `~/.cursor/mcp.json` |
| Windsurf | `~/.codeium/windsurf/mcp_settings.json` |
| Goose | `~/.config/goose/config.yaml` |

It reads only the MCP *config files* to extract server IDs, commands, and
argument lists. It does **not** read, log, or store any API key or token value —
only the env-var *names* a server declares.

## Dry run first

```bash
werktools hub onboard
```

This prints what was found and what would be adopted, and writes nothing:

```
Discovered servers by host:
  claude: 4
  cursor: 2

Would adopt:
  github          stdio   Community-Unverified
  filesystem      stdio   Community-Unverified
  docs-search     stdio   Community-Unverified
  brave-search    stdio   Community-Unverified
  cursor-rules    stdio   Community-Unverified
  cursor-editor   stdio   Community-Unverified

(dry-run) Pass --apply to write 6 connector(s) to hub.json
```

!!! tip "Trust tiers on adoption"
    Adopted servers start at `Community-Unverified` by default.
    Servers on the [Tier-1 allowlist](security.md#the-tier-1-allowlist) are
    promoted to `Official` or `Security-Scanned` automatically.

## Apply

Once you are satisfied with the dry-run output:

```bash
werktools hub onboard --apply
```

This writes the discovered servers into `.werktools/hub.json` as connectors,
all with deny-by-default policy. Secrets are presence-only: the hub records
*which* env-var names a server needs but never reads or stores values.

Servers already present in `hub.json` are left unchanged (no duplication).

## Scope to one host

```bash
werktools hub onboard --host claude
werktools hub onboard --host cursor --apply
```

Use `--host` with the host ID (`claude`, `codex`, `cursor`, `windsurf`, `goose`,
`gemini`) to restrict discovery to one source.

## Override the home directory

```bash
werktools hub onboard --home /path/to/home
```

Useful in CI or when running in a non-standard environment.

## After onboarding

1. **Verify** the adopted connectors look correct:
   ```bash
   werktools hub status
   ```

2. **Check trust tiers** and audit the list:
   ```bash
   werktools --config .werktools/hub.json capability list
   ```

3. **Start the hub** and point your agent host at it:
   ```bash
   werktools hub serve --profile claude-reviewer
   ```

4. **Open the dashboard** to confirm connectors are visible:
   ```bash
   werktools hub dashboard --open
   ```

## What happens at call time

Once a host is pointed at `hub serve`, every MCP tool call flows through the policy gate:

```
Agent → hub tool_call → enforce() → allow / approval_required / deny
                                         ↓
                                    Downstream MCP (on allow only)
                                         ↓
                                    Evidence ledger (always)
```

Tools whose risk class requires it will surface an `approval_required` verdict
instead of executing. A human approves via `werktools hub approvals approve <id>`,
and the agent retries with the one-use token. See [Concepts](concepts.md) for the
full approval flow.
