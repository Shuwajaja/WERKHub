"""Explicit bridge between the Integration Gate catalog and the hub runtime.

Per ADR-002 the two stores stay separate: integration_gate's
``connectors.json`` is the governance/scope catalog; ``hub.json`` is the
runtime relay truth read at ``hub serve`` start. This module is the ONE
explicit, operator-invoked bridge — no background sync, no merged store.

``sync_connectors_to_hub`` is pure (it performs no disk writes);
``persist_hub_config`` is the only function here that writes, and it writes
atomically via ``os.replace``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import warnings
from pathlib import Path

from .contracts import DownstreamServer, HubConfig


def connector_to_downstream_server(connector) -> DownstreamServer:
    """Derive a runtime DownstreamServer from a governance connector.

    The connector's ``metadata.command`` is required (raises ValueError if
    absent); ``metadata.args`` is optional. Risk stays governed at the relay
    and policy layers — this only carries the spawn config.
    """
    metadata = getattr(connector, "metadata", {}) or {}
    command = metadata.get("command")
    if not command:
        raise ValueError(
            f"connector {getattr(connector, 'connector_id', '?')!r} has no metadata.command; "
            "cannot derive a runtime server"
        )
    args = metadata.get("args", [])
    # Route through from_dict so transport validation (unknown -> stdio) and
    # secret-env rejection match the JSON round-trip path exactly.
    return DownstreamServer.from_dict(
        {
            "id": str(connector.connector_id),
            "command": str(command),
            "args": [str(item) for item in args],
            "transport": metadata.get("transport", "stdio"),
        }
    )


def sync_connectors_to_hub(connectors_root, hub_config: HubConfig, profile: str | None = None) -> HubConfig:
    """Return a new HubConfig with approved connectors merged as servers.

    Pure: reads the connector catalog but performs no writes. Connectors
    that cannot be converted are skipped with a warning (never a ledger
    write). Dedupes by server id against existing hub servers.
    """
    from ..tools.integration_gate import connectors as load_connectors

    seen = {server.id for server in hub_config.servers}
    merged = list(hub_config.servers)
    for connector in load_connectors(connectors_root, profile=profile):
        try:
            server = connector_to_downstream_server(connector)
        except ValueError as exc:
            warnings.warn(str(exc), stacklevel=2)
            continue
        if server.id in seen:
            continue
        seen.add(server.id)
        merged.append(server)
    return dataclasses.replace(hub_config, servers=tuple(merged))


def persist_hub_config(config: HubConfig, hub_json_path) -> None:
    """Atomically write a HubConfig to hub.json (the only writer here)."""
    target = Path(hub_json_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # tmp lives in the same directory as target so os.replace is atomic
    # (same filesystem) and never crosses devices.
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
