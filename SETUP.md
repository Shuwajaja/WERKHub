# WERKHub — Setup & Self-Test

WERKHub is **one governed MCP gateway**: your agent harness (Claude Code, Codex, …)
connects to **one** MCP server — WERKHub — and through it reaches **all** your
downstream MCP servers, with a deny-by-default policy gate and a tamper-evident
evidence ledger.

There are two processes:

| Process | What it is | Run via |
|---|---|---|
| `werktools hub serve` | the **MCP server** your agent connects to (stdio) | added to your harness MCP config |
| `werktools hub dashboard` | the **HTTP API** the desktop app reads (loopback) | started by the desktop app as a sidecar |

The desktop app (`desktop/`, a native Deno Desktop `.msi`) is the human supervisor
console over the gateway.

## Self-test: route your existing Claude MCPs through WERKHub

### 1. Add WERKHub as an MCP server in Claude Code

WERKHub is a Python package; the npx-equivalent is **`uvx`** (or `pipx`). Once
`werktools` is published to PyPI, the MCP config is a clean one-liner — no clone, no
`pip install` step, `uvx` fetches and runs it on demand:

```json
{
  "mcpServers": {
    "werkhub": {
      "command": "uvx",
      "args": ["--from", "werktools[server]", "werktools", "hub", "serve"]
    }
  }
}
```

`uv` is the one prerequisite (`pip install uv`, or the standalone installer from
astral.sh). `--from werktools[server]` pulls the FastMCP server extra.

Equivalent installs:

```bash
pipx run --spec "werktools[server]" werktools hub serve   # pipx
pip install "werktools[server]" && werktools hub serve     # plain pip (PATH command)
```

> **Until it's on PyPI** (see "Publish to PyPI" below), point `uvx --from` at the local
> wheel or the repo instead: `uvx --from "C:/Workplace/werktools[server]" werktools hub serve`.

Restart Claude Code. It now sees WERKHub's bridge tools (`tool_search`,
`tool_describe`, `tool_call`, `profile_info`, `ledger_recent`, `approval_status`,
`hub_status`, `registry_search`).

### 2. Import your existing MCPs with the Doctor (`hub onboard`)

WERKHub only routes to the connectors in its config — but you don't enter them by
hand. The **Doctor** scans your existing agent-host MCP configs and adopts them:

```bash
werktools hub onboard           # dry-run: lists what it would adopt
werktools hub onboard --apply   # writes them into hub.json as connectors
```

It reads **Claude (`~/.claude.json`), Cursor, Windsurf, Gemini, Kimi, Antigravity**
(JSON) and **Codex (`~/.codex/config.toml`, needs Python ≥ 3.11)**, and is
**presence-only**: from each server's `env` it extracts only the **key names**, never
the secret values. Existing connectors are kept (collision-safe). Set the actual API
keys/tokens in the **hub process environment** (WERKHub never stores them at rest).

The CLI is the agent-facing path (`hs`). For a human, the **desktop Onboard view**
does the same: it lists what the Doctor found and adopts it with one click. The list
is always available; the **Adopt** button is gated on `WERK_ALLOW_HUB_ONBOARD=1` in
the hub's environment (fail-closed), matching the `--apply` opt-in.

> Separately, `werktools hub doctor` is a runtime health-check (invariants + detected
> runtimes) — different command, different job.

### 3. Test the routing

In Claude, ask it to use a tool that lives on a downstream MCP. The call goes
`Claude → WERKHub (gate + ledger) → downstream MCP`. Watch it in the desktop app:

- **Timeline** — the hash-chained evidence of every routed call.
- **Connectors** — which downstream servers are live.
- **Approvals** — risk-gated calls waiting for your approve/deny.

## Publish to PyPI (makes the `uvx` one-liner work for everyone)

The package is release-ready: console script `werktools` exists, `python -m build`
produces a clean sdist + wheel, and `twine check` passes. Verified in a fresh venv:
`pip install "werktools[server]"` resolves FastMCP and `werktools hub serve` starts.

To publish (needs your PyPI account + an API token — keep it out of the repo):

```bash
python -m build                       # -> dist/werktools-<ver>-py3-none-any.whl + .tar.gz
python -m twine check dist/*          # metadata sanity
python -m twine upload dist/*         # prompts for token, or use TWINE_PASSWORD
```

Notes before the first upload:

- **Name** `werktools` is currently free on PyPI but is only reserved once you upload.
- **Version** is `0.2.1` (final, not a pre-release), so `uvx`/`pip` install it without any
  `--prerelease` flag. Bump the version in `pyproject.toml` + `src/werktools/__init__.py` for
  the next release.
- Test first against **TestPyPI**: `python -m twine upload -r testpypi dist/*`, then
  `uvx --index-url https://test.pypi.org/simple/ --from "werktools[server]" werktools hub serve`.

## Run the desktop app

Dev (you have Python + werktools): `cd desktop && pnpm dev` → open `localhost:5311`,
and separately run `werktools hub dashboard --port 7879`.

Native build: `cd desktop && deno task build:web && deno task desktop:build` →
`WERKHub.msi`. For a Python-free install, also `deno task build:backend` (bundles the
backend to `werkhub-backend.exe`, which the app prefers when installed beside it).

## Status

Approvals/Permissions are **live** (backend `/api/approvals`). The **Doctor** is live
both ways: `hub onboard` (CLI, for agents) and the desktop **Onboard** view (for
humans, backed by `/api/onboard` + gated `/api/onboard/apply`) — both presence-only,
and now reading **all** hosts incl. Codex on Python 3.10 (`pip install werktools[onboard]`
pulls the tomli backport; on 3.11+ it's stdlib).

The one remaining distribution item is **Windows code-signing** of the `.msi` — a CA
purchase, not a code change. Unsigned builds run fine; SmartScreen shows an "unknown
publisher" prompt the first time. That only matters for public distribution to strangers,
so it's deferred until launch. To sign once you have an Authenticode cert:
`signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 WERKHub.msi`.
