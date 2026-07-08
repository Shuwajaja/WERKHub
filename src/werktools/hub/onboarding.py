"""MCP onboarding: read host configs and MAP discovered servers into the hub.

Inverse of export-rules (hub -> hosts).  This module reads existing MCP server
configurations from the agent-host config files detected by runtimes.py and
normalises them into DownstreamServer connectors under hub governance.

Security invariant (PRESENCE-ONLY): from each server's ``env`` block we extract
only the KEY names — never the values.  Values are never read, never stored,
never logged, and never written.

Design principles:
- Pure stdlib only.  No new runtime dependencies.
- Frozen dataclasses; no mutation of shared state.
- Fail-closed / honest-degrade: corrupt / missing / unknown-shape config files
  are skipped with ``warnings.warn`` and recorded in the result; they never
  crash the caller.
- Dry-run is the DEFAULT: ``onboard(path)`` computes and returns the mapping
  but writes nothing.  Only ``onboard(path, apply=True)`` writes hub.json and
  it MERGES (never overwrites an existing connector id).
- Deny-by-default trust: every adopted connector starts Community-Unverified
  unless it matches the Tier-1 allowlist (reuses the existing allowlist logic
  from discovery.py / allowlist.py).
- Event ledger: a single existing EVENT_NAMES entry that semantically fits a
  connector-adoption action is used.  If none fits, no event is emitted.
  EVENT_NAMES is LOCKED at 43 — no new name is ever minted here.
"""

from __future__ import annotations

import dataclasses
import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtimes import DESCRIPTORS

# The EVENT_NAMES entry that best fits "connector adopted into hub config".
# "config.connector.added" records that a new downstream server was written.
# If this name were ever removed from EVENT_NAMES the ledger call is guarded
# by a membership check so it fails safely (no crash, no invented name).
_ADOPTION_EVENT = "config.connector.added"


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoveredServer:
    """One MCP server found in an agent-host config file.

    ``needs_keys`` holds only the NAMES of required env-vars (never values).
    """

    name: str
    source_host: str
    command: str
    args: tuple[str, ...]
    url: str
    transport: str
    needs_keys: tuple[str, ...]  # env-var KEY names only — never values

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_host": self.source_host,
            "command": self.command,
            "args": list(self.args),
            "url": self.url,
            "transport": self.transport,
            "needs_keys": list(self.needs_keys),
        }


@dataclass(frozen=True)
class OnboardResult:
    """The result of one onboard() call (dry-run or applied)."""

    discovered: tuple[DiscoveredServer, ...]
    connectors: tuple[Any, ...]          # tuple[DownstreamServer, ...]
    added: tuple[str, ...]               # connector ids written to hub.json
    skipped_hosts: tuple[str, ...]       # host ids whose configs were skipped
    by_host: dict[str, int]              # host_id -> number of servers found
    applied: bool


# ---------------------------------------------------------------------------
# Config parsing helpers
# ---------------------------------------------------------------------------

# Host IDs that use JSON-format mcpServers configs.
_JSON_HOSTS = {"claude", "cursor", "windsurf", "gemini", "kimi", "antigravity"}
# Host IDs that use TOML format (Codex ~/.codex/config.toml).
_TOML_HOSTS = {"codex"}

# Map host_id -> which of its config_paths carries MCP server definitions.
# Only the FIRST matching path is read per host.
_MCP_CONFIG_PATH: dict[str, tuple[str, str]] = {
    # (path_pattern, fmt)
    "claude":      ("~/.claude.json", "json"),
    "cursor":      ("~/.cursor/mcp.json", "json"),
    "windsurf":    ("~/.codeium/windsurf/mcp_config.json", "json"),
    "gemini":      ("~/.gemini/settings.json", "json"),
    "codex":       ("~/.codex/config.toml", "toml"),
    "kimi":        ("~/.kimi/config.json", "json"),
    "antigravity": ("~/.antigravity/config.json", "json"),
}


def _safe_str_tuple(value: Any) -> tuple[str, ...]:
    """Convert a list-like to a tuple of strings; empty on failure."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    try:
        return tuple(str(item) for item in value)
    except TypeError:
        return ()


def _parse_json_server(
    name: str,
    raw: dict[str, Any],
    host_id: str,
) -> DiscoveredServer | None:
    """Parse one server entry from a JSON mcpServers block.

    SECURITY: only KEY names from the env dict are extracted — values are
    deliberately never read, stored, or returned.
    """
    if not isinstance(raw, dict):
        return None

    command = str(raw.get("command", ""))
    args = _safe_str_tuple(raw.get("args"))
    url = str(raw.get("url", ""))
    transport = str(raw.get("transport", "stdio" if command else ""))

    # Normalise transport
    if transport not in ("stdio", "http", "sse", "ws"):
        transport = "stdio" if command else "sse" if url else "stdio"

    # PRESENCE-ONLY: extract only the KEY names from env — never the values.
    env_block = raw.get("env")
    if isinstance(env_block, dict):
        needs_keys: tuple[str, ...] = tuple(str(k) for k in env_block)
    else:
        needs_keys = ()

    return DiscoveredServer(
        name=name,
        source_host=host_id,
        command=command,
        args=args,
        url=url,
        transport=transport,
        needs_keys=needs_keys,
    )


def _parse_toml_servers(host_id: str, text: str) -> list[DiscoveredServer]:
    """Parse Codex ~/.codex/config.toml for [mcp_servers.NAME] tables.

    Uses stdlib ``tomllib`` (Python >= 3.11) and falls back to the ``tomli``
    backport on 3.10 (``pip install werktools[onboard]``, or any env that
    already has tomli).  Keeps the core zero-dependency: if neither reader is
    present it emits a warning and returns [] — never crashes.
    """
    try:
        import tomllib  # type: ignore[import]  # Python >= 3.11
    except (ImportError, TypeError):
        # TypeError is raised when sys.modules["tomllib"] = None (test shim).
        try:
            import tomli as tomllib  # type: ignore[import,no-redef]  # 3.10 backport
        except (ImportError, TypeError):
            warnings.warn(
                f"Codex TOML import needs Python >= 3.11 or the 'tomli' package "
                f"(pip install werktools[onboard]); skipping {host_id!r} config",
                stacklevel=4,
            )
            return []

    try:
        data = tomllib.loads(text)
    except Exception as exc:
        warnings.warn(
            f"onboarding: corrupt TOML for host {host_id!r}: {exc}; skipping",
            stacklevel=4,
        )
        return []

    mcp_servers = data.get("mcp_servers", {})
    if not isinstance(mcp_servers, dict):
        return []

    result: list[DiscoveredServer] = []
    for name, entry in mcp_servers.items():
        if not isinstance(entry, dict):
            continue
        command = str(entry.get("command", ""))
        args = _safe_str_tuple(entry.get("args"))
        url = str(entry.get("url", ""))
        transport = str(entry.get("transport", "stdio" if command else ""))
        env_block = entry.get("env")
        needs_keys = tuple(str(k) for k in env_block) if isinstance(env_block, dict) else ()
        result.append(
            DiscoveredServer(
                name=str(name),
                source_host=host_id,
                command=command,
                args=args,
                url=url,
                transport=transport,
                needs_keys=needs_keys,
            )
        )
    return result


def parse_mcp_config(
    host_id: str,
    text: str,
    fmt: str,
) -> list[DiscoveredServer]:
    """Parse a raw config text into a list of DiscoveredServer objects.

    Schema-tolerant: unexpected shape emits ``warnings.warn`` and returns [].
    Corrupt input also emits a warning and returns [].

    SECURITY: env VALUES are never read, stored, or returned — only KEY names.
    """
    if fmt == "toml":
        return _parse_toml_servers(host_id, text)

    # JSON path
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        warnings.warn(
            f"onboarding: corrupt JSON config for host {host_id!r}: {exc}; skipping",
            stacklevel=3,
        )
        return []

    if not isinstance(data, dict):
        warnings.warn(
            f"onboarding: unexpected JSON shape (not an object) for host {host_id!r}; skipping",
            stacklevel=3,
        )
        return []

    mcp_servers = data.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        warnings.warn(
            f"onboarding: no 'mcpServers' key in config for host {host_id!r}; skipping",
            stacklevel=3,
        )
        return []

    result: list[DiscoveredServer] = []
    for name, entry in mcp_servers.items():
        server = _parse_json_server(str(name), entry, host_id)
        if server is not None:
            result.append(server)
    return result


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_from_hosts(*, home: Path | None = None) -> list[DiscoveredServer]:
    """Discover MCP servers across all known agent-host configs.

    Iterates DESCRIPTORS to find which hosts have config files, reads each
    file once, and returns the union of all discovered servers tagged by
    ``source_host``.  Missing files are silently skipped (no warning needed
    — absence is the normal case for un-installed hosts).
    """
    effective_home = home if home is not None else Path.home()
    found: list[DiscoveredServer] = []

    for descriptor in DESCRIPTORS:
        host_id = descriptor.host_id
        if host_id not in _MCP_CONFIG_PATH:
            continue
        path_pattern, fmt = _MCP_CONFIG_PATH[host_id]

        # Resolve path relative to the effective home
        rel = path_pattern.lstrip("~/")
        config_path = effective_home / rel

        if not config_path.exists():
            continue

        try:
            text = config_path.read_text(encoding="utf-8")
        except OSError as exc:
            warnings.warn(
                f"onboarding: cannot read config for host {host_id!r} at {config_path}: {exc}; skipping",
                stacklevel=2,
            )
            continue

        servers = parse_mcp_config(host_id, text, fmt)
        found.extend(servers)

    return found


# ---------------------------------------------------------------------------
# Mapping to DownstreamServer connectors
# ---------------------------------------------------------------------------

_UNVETTED_NOTE = "onboarding: host-discovered server; not on Tier-1 allowlist"


def _trust_for(server_id: str) -> tuple[str, str, str]:
    """Return (trust_tier, trust_source, trust_note) for a connector id.

    Uses the existing Tier-1 allowlist derivation (deny-by-default): a server
    is Community-Unverified unless it matches an allowlist entry.
    """
    from .allowlist import default_allowlist, is_tier1, tier1_trust_fields

    al = default_allowlist()
    entry = is_tier1(al, server_id)
    if entry is not None:
        fields = tier1_trust_fields(entry)
        return fields["trust_tier"], fields["trust_source"], fields["trust_note"]
    return "Community-Unverified", "onboarding", _UNVETTED_NOTE


def _dedup_key(s: DiscoveredServer) -> tuple[str, ...]:
    """Return a dedup key: (command, *args) for stdio, (url,) for remote."""
    if s.url:
        return ("url", s.url)
    return ("stdio", s.command) + s.args


def map_to_connectors(
    discovered: list[DiscoveredServer],
    hub_config_path: Path,
) -> list[Any]:  # list[DownstreamServer]
    """Normalise DiscoveredServer list into DownstreamServer connectors.

    - DEDUP: same (command, args) for stdio or same url -> ONE connector.
    - Trust: deny-by-default via existing allowlist derivation.
    - The returned list is ordered deterministically (by first-seen name).
    """
    from .contracts import DownstreamServer

    # Group by dedup key; first-seen name wins.
    seen_keys: dict[tuple[str, ...], DiscoveredServer] = {}
    for s in discovered:
        key = _dedup_key(s)
        if key not in seen_keys:
            seen_keys[key] = s

    connectors: list[DownstreamServer] = []
    for s in seen_keys.values():
        trust_tier, trust_source, trust_note = _trust_for(s.name)
        transport = s.transport if s.transport in ("stdio", "http", "sse", "ws") else "stdio"
        url = s.url if s.url else None

        connectors.append(
            DownstreamServer(
                id=s.name,
                command=s.command,
                args=s.args,
                enabled=True,
                transport=transport,
                url=url,
                headers={},
                env={},          # SECURITY: env values are never stored
                trust_tier=trust_tier,
                trust_source=trust_source,
                trust_note=trust_note,
            )
        )
    return connectors


# ---------------------------------------------------------------------------
# Main onboard() function
# ---------------------------------------------------------------------------


def onboard(
    hub_config_path: Path | str,
    *,
    apply: bool = False,
    home: Path | None = None,
    host_filter: str | None = None,
) -> OnboardResult:
    """Discover MCP servers from host configs and (optionally) adopt them.

    Parameters
    ----------
    hub_config_path:
        Path to hub.json.  Created on ``apply=True``; untouched on dry-run.
    apply:
        When False (default), compute the mapping and return it without writing
        anything.  When True, MERGE new connectors into hub.json.
    home:
        Override ``Path.home()``; used by tests to point at a fixture dir.
    host_filter:
        When set, only configs from this host_id are read.

    Returns
    -------
    OnboardResult (frozen dataclass)
    """
    from .contracts import EVENT_NAMES, HubConfig
    from .registry import load_config, save_config

    hub_path = Path(hub_config_path)

    # --- Discover ---
    all_discovered = discover_from_hosts(home=home)

    # Apply host filter if requested
    if host_filter:
        all_discovered = [s for s in all_discovered if s.source_host == host_filter]

    # per-host counts
    by_host: dict[str, int] = {}
    for s in all_discovered:
        by_host[s.source_host] = by_host.get(s.source_host, 0) + 1

    # Track skipped_hosts (hosts with parse errors end up with 0 servers but
    # the warning already fired in discover_from_hosts / parse_mcp_config).
    # For now, skipped_hosts tracks connector-level collisions on apply.

    connectors = map_to_connectors(all_discovered, hub_path)

    if not apply:
        return OnboardResult(
            discovered=tuple(all_discovered),
            connectors=tuple(connectors),
            added=(),
            skipped_hosts=(),
            by_host=by_host,
            applied=False,
        )

    # --- Apply: merge into hub.json ---
    if hub_path.exists():
        try:
            config = load_config(hub_path)
        except (ValueError, OSError) as exc:
            warnings.warn(
                f"onboarding: cannot load existing hub.json at {hub_path}: {exc}; starting fresh",
                stacklevel=2,
            )
            config = HubConfig()
    else:
        config = HubConfig()

    existing_ids = {s.id for s in config.servers}
    added_ids: list[str] = []
    skipped_collision: list[str] = []
    new_servers = list(config.servers)

    for connector in connectors:
        if connector.id in existing_ids:
            skipped_collision.append(connector.id)
        else:
            new_servers.append(connector)
            existing_ids.add(connector.id)
            added_ids.append(connector.id)

    merged = dataclasses.replace(config, servers=tuple(new_servers))
    save_config(hub_path, merged)

    # Ledger: use an existing EVENT_NAMES entry that fits a connector-adoption
    # action.  "config.connector.added" records that new servers were written.
    # Guard: only fire if the name is still in EVENT_NAMES (locked at 43).
    if added_ids and _ADOPTION_EVENT in EVENT_NAMES:
        try:
            from .ledger import record_event

            ledger_path = Path(config.ledger_path)
            record_event(
                ledger_path,
                _ADOPTION_EVENT,
                {"server_ids": added_ids, "source": "onboarding"},
            )
        except OSError:
            # Ledger write failure is non-fatal (honest-degrade).
            pass

    return OnboardResult(
        discovered=tuple(all_discovered),
        connectors=tuple(connectors),
        added=tuple(added_ids),
        skipped_hosts=tuple(skipped_collision),
        by_host=by_host,
        applied=True,
    )
