"""Pure-stdlib hub status projection (no FastMCP import).

Reads the active profile's allowed downstream servers and, IF a WarmPool is
passed, projects its entries. In production no pool is wired (the CLI and
serve pass ``pool=None``), so every allowed server is reported as
``unconfigured`` with no pid/uptime — this snapshot does not reflect any
live subprocess. Real downstream process state (spawned PIDs, idle/orphan
reaping) is tracked separately in hub/lifecycle.py and is not surfaced here.
Produces a JSON-serializable snapshot for the status MCP tool, the localhost
status endpoint, the dashboard, and an optional external fleet view.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .contracts import HubConfig
from .registry import get_profile


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> float | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class ServerStatus:
    server_id: str
    name: str
    state: str
    pid: int | None
    uptime_s: int
    idle_for_s: int
    tool_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "name": self.name,
            "state": self.state,
            "pid": self.pid,
            "uptime_s": self.uptime_s,
            "idle_for_s": self.idle_for_s,
            "tool_count": self.tool_count,
        }


@dataclass(frozen=True)
class HubStatus:
    hub_name: str
    profile_id: str
    servers: tuple[ServerStatus, ...]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "hub_name": self.hub_name,
            "profile_id": self.profile_id,
            "generated_at": self.generated_at,
            "servers": [s.to_dict() for s in self.servers],
        }


def hub_status(config: HubConfig, profile_id: str | None = None, pool: Any = None) -> HubStatus:
    """Project per-server status for the profile's allowed servers only."""
    profile = get_profile(config, profile_id)
    now_iso = _now_iso()
    now = _parse_iso(now_iso) or 0.0
    servers: list[ServerStatus] = []
    for server_id in sorted(profile.allowed_servers):
        entry = pool.get(server_id) if pool is not None else None
        if entry is None:
            servers.append(ServerStatus(server_id, server_id, "unconfigured", None, 0, 0, 0))
            continue
        started = _parse_iso(entry.started_at)
        if entry.started_at and started is None:
            warnings.warn(
                f"hub_status: pool entry {entry.server_id!r} has unparseable "
                f"started_at={entry.started_at!r}; uptime reported as 0",
                stacklevel=2,
            )
        used = _parse_iso(entry.last_used_at)
        if entry.last_used_at and used is None:
            warnings.warn(
                f"hub_status: pool entry {entry.server_id!r} has unparseable "
                f"last_used_at={entry.last_used_at!r}; idle_for_s reported as 0",
                stacklevel=2,
            )
        uptime = int(now - started) if started is not None else 0
        idle = int(now - used) if used is not None else 0
        servers.append(
            ServerStatus(
                server_id=entry.server_id,
                name=entry.name,
                state=entry.state,
                pid=entry.pid,
                uptime_s=max(0, uptime),
                idle_for_s=max(0, idle),
                tool_count=entry.tool_count,
            )
        )
    return HubStatus(config.name, profile.id, tuple(servers), now_iso)
