# ADR-002: Store Bridge — connectors.json stays separate; hub.json is runtime truth

Status: accepted  
Date: 2026-06-10

## Context

Two local JSON stores describe MCP servers from different angles:

- `connectors.json` (under the Integration Gate root) is the
  **governance/scope catalog**: connector manifests, declared scopes,
  approval requests. Written by `tools/integration_gate.py`.
- `hub.json` (`HubConfig`) is the **runtime relay truth**: the downstream
  servers `hub serve` actually spawns. Hand-editable.

The Discovery pillar (registry → install) needs a path from "catalogued and
approved connector" to "running downstream server." The question: merge the
two stores, or keep them separate?

## Decision

**Keep `connectors.json` and `hub.json` as separate files.** They answer
different questions and have different mutation rules (the catalog is
governance state; hub.json is runtime config). Bridge them with ONE
explicit, synchronous, operator-invoked function — never a background sync,
never a merged store.

- `store_bridge.connector_to_downstream_server(connector)` — pure mapping.
- `store_bridge.sync_connectors_to_hub(connectors_root, hub_config, profile)`
  — pure (no writes); returns a new `HubConfig` with approved connectors
  merged as servers, deduped by id, bad connectors skipped via warning.
- `store_bridge.persist_hub_config(config, hub_json_path)` — the only
  writer, atomic via `os.replace`.

## Consequences

- Fail-closed preserved: no connector auto-promotes into relay config; the
  bridge runs only on explicit operator action (the install approval step).
- The runtime config stays deterministic and hand-editable; catalog churn
  never silently mutates what the hub spawns.
- Discovery's `catalog → classify → approve → connect` maps cleanly:
  approve stages in `connectors.json`; connect calls the bridge + persists
  `hub.json`.

## Note: DownstreamServer transport extension

This slice also extended `DownstreamServer` with `transport` / `url` /
`headers` / `env` (the Phase-2 config-renderer prerequisite). The model can
now represent http/sse/ws servers, but **the v0 relay is stdio-only**:
`relay.transport_for` raises `NotImplementedError` for a non-stdio server
(fail-closed) rather than silently spawning an empty command. Spawning
http/sse downstreams is deferred to the Phase-2 renderer wave.

## Revisit Trigger

If operators demand live two-way sync (catalog edits reflected in the
runtime without an explicit step), reopen this — but that reintroduces the
non-determinism this decision avoids.
