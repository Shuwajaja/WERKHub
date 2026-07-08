# Getting started

## Requirements

- Python 3.10 or later
- pip
- At least one agent host that supports MCP (Claude Code, Codex, Cursor, Windsurf, …)

## Install

The core package has **zero third-party runtime dependencies** — pure Python stdlib:

```bash
pip install werktools
```

To run the live MCP server (`hub serve`), also install the `server` extra:

```bash
pip install werktools[server]
```

Full extras reference:

| Extra | What it adds | Stability |
|---|---|---|
| *(none)* | Core CLI, policy engine, ledger, onboarding, dashboard | Stable |
| `server` | `hub serve` — live MCP relay over stdio (requires `fastmcp>=2.2`) | Beta |
| `yaml` | YAML profile loading (`load_profile`) | Stable |
| `worker` | Model-worker HTTP dispatch to OpenRouter (`httpx>=0.27`) | Beta |
| `all` | `server` + `yaml` + `worker` | Beta |

## Initialize the hub

Create a default `hub.json` config in the current project:

```bash
werktools hub init
```

This writes `.werktools/hub.json` with a deny-by-default policy and a default
`claude-reviewer` profile. Nothing executes until you wire a host to the hub.

For a community-neutral profile (not Claude-specific):

```bash
werktools hub init --community
```

## Run a health check

```bash
werktools hub doctor
```

`doctor` checks internal invariants, reads the config, and probes known agent
hosts (Claude Code, Codex, Cursor, Windsurf, Goose, Gemini…) for binary paths,
config files, and token-env presence — **presence only, never a value**.

```
ok   event_contract
ok   ledger_append
ok   policy_fail_closed
config .werktools/hub.json: profiles=3 tools=12 servers=4

Runtimes
  [CC] detected  Claude Code          ~/.claude
  [CX] missing   Codex                -
  [CU] missing   Cursor               -
```

## Adopt your existing MCPs

See [Onboarding your MCPs](onboarding.md) for the full flow. Quick version:

```bash
werktools hub onboard          # dry-run — shows what it found, writes nothing
werktools hub onboard --apply  # write connectors to hub.json
```

## Point a host at the hub

Once `hub.json` is populated, add the hub as an MCP server in your host's config:

=== "Claude Code (.mcp.json)"

    ```json
    {
      "mcpServers": {
        "werk-hub": {
          "command": "werktools",
          "args": ["hub", "serve", "--profile", "claude-reviewer",
                   "--config", ".werktools/hub.json"]
        }
      }
    }
    ```

=== "Codex (~/.codex/config.toml)"

    ```toml
    [mcp_servers.werk-hub]
    command = "werktools"
    args = ["hub", "serve", "--profile", "codex-builder",
            "--config", ".werktools/hub.json"]
    ```

The profile can also be set via the environment variable `WERKTOOLS_HUB_PROFILE`.

## Check hub status

```bash
werktools hub status
```

```
Hub: werk-hub
Default profile: claude-reviewer
Profiles: 3
Tools: 12
Ledger: .werktools/hub.jsonl
```

## Open the dashboard

```bash
werktools hub dashboard --open
```

This serves the local Bento control-plane dashboard at `http://127.0.0.1:7879`
and opens it in your browser. See [Dashboard](dashboard.md) for what you will find there.

## Next steps

- [Onboarding your MCPs](onboarding.md) — adopt existing MCP configs in bulk
- [Concepts](concepts.md) — understand the policy model, approval tokens, and ledger
- [CLI reference](cli.md) — every command and flag
