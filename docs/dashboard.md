# Dashboard

```bash
werktools hub dashboard
werktools hub dashboard --open          # open browser automatically
werktools hub dashboard --port 7880     # custom port (default: 7879)
```

The dashboard is a local, CSP-safe single-page Bento control plane served from
`127.0.0.1` only (never `0.0.0.0`). It requires the hub to be configured but
does **not** require `hub serve` to be running — it reads the config and ledger
directly.

---

## What you see

The dashboard is a vertical stack of quiet panels:

### Status band

A top-of-page summary: number of MCP servers in config, combined memory of any
live relay processes, and the hub name and active profile.

### Connectors table

All downstream servers from `hub.json`, with columns:

| Column | Notes |
|---|---|
| Server ID | The connector slug used in hub.json |
| Transport | `stdio` (current); `http`/`sse` are config-representable but relay is stdio-only in v0 |
| Trust tier | `Official` / `Security-Scanned` / `Community-Unverified` — rectangular stamp, always text-labelled |
| State | `warm` / `idle` / `dead` / `unconfigured` |

Trust tier badges are rectangular (3 px radius) and always carry the tier label — color is never the only signal.

### Trust posture donut

A pure-CSS donut showing the distribution of trust tiers across connectors —
how many are `Official`, `Security-Scanned`, and `Community-Unverified`. Reads
directly from the connector list.

### AI Runtimes panel

One row per known agent host, showing:

- A host glyph or monogram chip (2-letter, e.g. `CC` for Claude Code).
- **State:** `detected` (green) or `missing` (muted) — always with the word.
- **Token presence:** `token-env` / `token-file` pills — **presence only**. No token value, no file path is ever surfaced.
- **At-risk badge** for deprecated or in-transition hosts, reachable by keyboard.

Probed hosts: Claude Code, Codex, Cursor, Windsurf, Goose, Gemini, Kimi, and others.

### MCP Registry browse (gated)

A search interface into the capability registry. Gated behind the environment
variable `WERK_ALLOW_HUB_REGISTRY` — not active by default. When enabled:
search for a server, see its trust tier, stage an install.

### Evidence lane

A compact tail of the hash-chained ledger, rendered as monospace rows. Shows
recent events: tool calls, policy decisions, approvals, runtime probes.

---

## Status API

When `hub serve` runs with `--status-port` (default `9371`), it exposes a
loopback-only status API the dashboard reads:

```
GET /status     → HubStatus JSON (fleet snapshot)
GET /api/events → SSE stream of ledger events
POST /api/kill  → Gated by WERK_ALLOW_HUB_KILL; destructive, ledgered
```

The dashboard can be used without the status server — it falls back to reading
the config file directly.

---

## Current limitations

!!! warning "Process-kill is a preview"
    The **Kill** button in the fleet table is fully gated (loopback +
    per-session token + live-fleet PIDs) and the ledger path is wired.
    However, the live process-supervision wiring is stub-only in
    `0.2.0`. The button surfaces state rather than performing real
    process termination. This gap is tracked in `SECURITY.md` and will be
    closed before `1.0`.

The dashboard is otherwise functional: connectors, trust posture, runtime
detection, and the evidence lane are all live reads from real data.

---

## Accessibility

The dashboard targets WCAG 2.2 AA:

- Color is never the sole signal — every trust/state/at-risk indicator carries a text label.
- Auto-updating regions use `aria-live="polite"`.
- All controls are keyboard-operable with an indigo focus ring.
- Tables use `<th scope>`.
- A skip link is present.
