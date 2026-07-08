"""Pure per-host MCP config renderer (write once, render per host).

One hub.json + a chosen profile renders to each host's native config,
filtered to only the servers the profile is allowed to reach (a read-only
profile's config physically cannot contain a write-only server). Pure
stdlib, no I/O, no network, no relay/server import — the CLI owns writes
and the ledger.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .contracts import DownstreamServer, HubConfig
from .registry import get_profile, visible_tools

HOSTS = ("claude", "vscode", "codex", "cursor", "windsurf", "goose", "gemini")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _allowed_servers(config: HubConfig, profile_id: str | None = None) -> tuple[DownstreamServer, ...]:
    profile = get_profile(config, profile_id)
    visible_server_ids = {tool.server_id for tool in visible_tools(config, profile.id)}
    allowed = set(profile.allowed_servers)
    out: list[DownstreamServer] = []
    for server in config.servers:
        if not server.enabled:
            continue
        if server.id in visible_server_ids or server.id in allowed:
            out.append(server)
    return tuple(out)


def _stdio_entry(server: DownstreamServer) -> dict[str, Any]:
    entry: dict[str, Any] = {"command": server.command, "args": list(server.args)}
    if server.env:
        entry["env"] = dict(server.env)
    return entry


def _mcpservers_block(servers, *, url_key: str = "url") -> dict[str, Any]:
    block: dict[str, Any] = {}
    for server in servers:
        if server.transport == "stdio":
            block[server.id] = _stdio_entry(server)
        else:
            entry: dict[str, Any] = {url_key: server.url}
            if server.headers:
                entry["headers"] = dict(server.headers)
            block[server.id] = entry
    return block


def render_claude(servers) -> str:
    return json.dumps({"mcpServers": _mcpservers_block(servers)}, indent=2, sort_keys=True)


def render_vscode(servers) -> str:
    return json.dumps({"servers": _mcpservers_block(servers)}, indent=2, sort_keys=True)


def render_cursor(servers) -> str:
    return json.dumps({"mcpServers": _mcpservers_block(servers)}, indent=2, sort_keys=True)


def render_windsurf(servers) -> str:
    return json.dumps({"mcpServers": _mcpservers_block(servers, url_key="serverUrl")}, indent=2, sort_keys=True)


def render_gemini(servers) -> str:
    return json.dumps({"mcpServers": _mcpservers_block(servers, url_key="httpUrl")}, indent=2, sort_keys=True)


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_array(values) -> str:
    return "[" + ", ".join(_toml_str(str(v)) for v in values) + "]"


def _toml_key(key: str) -> str:
    return key if _SAFE_KEY.match(key) else _toml_str(key)


def render_codex(servers) -> str:
    lines: list[str] = []
    for server in servers:
        lines.append(f"[mcp_servers.{_toml_key(server.id)}]")
        if server.transport == "stdio":
            lines.append(f"command = {_toml_str(server.command)}")
            lines.append(f"args = {_toml_array(server.args)}")
            if server.env:
                lines.append(f"[mcp_servers.{_toml_key(server.id)}.env]")
                for k, v in sorted(server.env.items()):
                    lines.append(f"{_toml_key(k)} = {_toml_str(str(v))}")
        else:
            lines.append(f"url = {_toml_str(server.url or '')}")
            if server.headers:
                lines.append(f"[mcp_servers.{_toml_key(server.id)}.headers]")
                for k, v in sorted(server.headers.items()):
                    lines.append(f"{_toml_key(k)} = {_toml_str(str(v))}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_goose(servers) -> str:
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("goose rendering requires PyYAML: pip install werktools[yaml]") from exc
    extensions: dict[str, Any] = {}
    for server in servers:
        if server.transport == "stdio":
            extensions[server.id] = {"type": "stdio", "cmd": server.command, "args": list(server.args)}
        else:
            extensions[server.id] = {"type": "sse", "uri": server.url}
    return yaml.safe_dump({"extensions": extensions}, sort_keys=True)


_RENDERERS = {
    "claude": render_claude,
    "vscode": render_vscode,
    "codex": render_codex,
    "cursor": render_cursor,
    "windsurf": render_windsurf,
    "goose": render_goose,
    "gemini": render_gemini,
}


def render(config: HubConfig, profile_id: str | None = None, host: str = "claude") -> str:
    """Render the profile-allowed servers as a host-native config string."""
    if host not in _RENDERERS:
        raise ValueError(f"unknown host {host!r}; known hosts: {', '.join(HOSTS)}")
    servers = _allowed_servers(config, profile_id)
    return _RENDERERS[host](servers)
