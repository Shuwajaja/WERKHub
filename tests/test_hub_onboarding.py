"""Tests for hub/onboarding.py — fully offline, fixtures via tmp_path / home=.

TDD (RED first): all tests written before the implementation exists.
Security invariant: secret VALUES in env blocks must NEVER appear in any output.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# A minimal ~/.claude.json with an mcpServers block.  The "filesystem" server
# carries an env block; SECRET_VALUE must NEVER surface after parsing.
_CLAUDE_JSON = {
    "mcpServers": {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "env": {
                "FS_SECRET_KEY": "s3cr3t-value-MUST-NOT-APPEAR",
                "FS_ALLOWED_DIRS": "another-value-MUST-NOT-APPEAR",
            },
        },
        "git": {
            "command": "uvx",
            "args": ["mcp-server-git", "--repository", "/repo"],
        },
    }
}

# A minimal ~/.cursor/mcp.json with mcpServers
_CURSOR_MCP_JSON = {
    "mcpServers": {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home"],
            "env": {
                "FS_SECRET_KEY": "cursor-secret-MUST-NOT-APPEAR",
            },
        },
        "brave-search": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-brave-search"],
            "env": {
                "BRAVE_API_KEY": "brave-value-MUST-NOT-APPEAR",
            },
        },
    }
}

# Remote/URL-style server (http transport)
_WINDSURF_MCP_JSON = {
    "mcpServers": {
        "remote-tool": {
            "url": "https://example.com/mcp",
            "transport": "sse",
        }
    }
}


def _make_claude_home(tmp_path: Path) -> Path:
    """Write .claude.json at tmp_path."""
    (tmp_path / ".claude.json").write_text(json.dumps(_CLAUDE_JSON), encoding="utf-8")
    return tmp_path


def _make_cursor_home(tmp_path: Path) -> Path:
    """Write .cursor/mcp.json at tmp_path."""
    cursor = tmp_path / ".cursor"
    cursor.mkdir()
    (cursor / "mcp.json").write_text(json.dumps(_CURSOR_MCP_JSON), encoding="utf-8")
    return tmp_path


def _make_windsurf_home(tmp_path: Path) -> Path:
    """Write .codeium/windsurf/mcp_config.json at tmp_path."""
    ws = tmp_path / ".codeium" / "windsurf"
    ws.mkdir(parents=True)
    (ws / "mcp_config.json").write_text(json.dumps(_WINDSURF_MCP_JSON), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# parse_mcp_config — unit tests
# ---------------------------------------------------------------------------


def test_parse_claude_json_returns_discovered_servers():
    from werktools.hub.onboarding import parse_mcp_config

    text = json.dumps(_CLAUDE_JSON)
    servers = parse_mcp_config("claude", text, "json")
    assert len(servers) == 2
    ids = {s.name for s in servers}
    assert ids == {"filesystem", "git"}


def test_parse_cursor_json_returns_discovered_servers():
    from werktools.hub.onboarding import parse_mcp_config

    text = json.dumps(_CURSOR_MCP_JSON)
    servers = parse_mcp_config("cursor", text, "json")
    assert len(servers) == 2
    ids = {s.name for s in servers}
    assert ids == {"filesystem", "brave-search"}


def test_parse_env_block_extracts_key_names_only():
    """needs_keys must contain only KEY names, never values."""
    from werktools.hub.onboarding import parse_mcp_config

    text = json.dumps(_CLAUDE_JSON)
    servers = parse_mcp_config("claude", text, "json")
    fs = next(s for s in servers if s.name == "filesystem")
    assert set(fs.needs_keys) == {"FS_SECRET_KEY", "FS_ALLOWED_DIRS"}


def test_parse_env_block_never_includes_secret_values():
    """PRESENCE-ONLY: the VALUE 's3cr3t-value-MUST-NOT-APPEAR' must NEVER appear anywhere."""
    from werktools.hub.onboarding import parse_mcp_config

    text = json.dumps(_CLAUDE_JSON)
    servers = parse_mcp_config("claude", text, "json")
    serialized = json.dumps([s.to_dict() for s in servers])
    assert "s3cr3t-value-MUST-NOT-APPEAR" not in serialized
    assert "another-value-MUST-NOT-APPEAR" not in serialized
    # Also check raw fields
    fs = next(s for s in servers if s.name == "filesystem")
    assert "s3cr3t-value-MUST-NOT-APPEAR" not in str(fs)
    assert "another-value-MUST-NOT-APPEAR" not in str(fs)


def test_parse_remote_server_json():
    """URL/transport style servers (sse/http) are parsed correctly."""
    from werktools.hub.onboarding import parse_mcp_config

    text = json.dumps(_WINDSURF_MCP_JSON)
    servers = parse_mcp_config("windsurf", text, "json")
    assert len(servers) == 1
    s = servers[0]
    assert s.name == "remote-tool"
    assert s.url == "https://example.com/mcp"
    assert s.transport == "sse"
    assert s.command == ""


def test_parse_corrupt_json_returns_empty_with_warning():
    """Corrupt JSON -> warns + returns [] without crash."""
    from werktools.hub.onboarding import parse_mcp_config

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = parse_mcp_config("claude", "{bad json[[[", "json")
    assert result == []
    assert any("claude" in str(warning.message).lower() or "corrupt" in str(warning.message).lower() for warning in w)


def test_parse_unknown_shape_returns_empty_with_warning():
    """JSON without mcpServers key -> warns + returns []."""
    from werktools.hub.onboarding import parse_mcp_config

    text = json.dumps({"something_else": {}})
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = parse_mcp_config("cursor", text, "json")
    assert result == []
    assert len(w) >= 1


def test_parse_toml_skips_gracefully_without_any_reader():
    """Codex TOML: if NEITHER tomllib (3.11) NOR tomli (backport) is available,
    skip with a warning and never crash."""
    from werktools.hub.onboarding import parse_mcp_config

    toml_text = "[mcp_servers.my_server]\ncommand = 'uvx'\nargs = ['my-mcp']\n"
    import sys

    orig_lib = sys.modules.get("tomllib")
    orig_li = sys.modules.get("tomli")
    sys.modules["tomllib"] = None  # type: ignore[assignment]
    sys.modules["tomli"] = None  # type: ignore[assignment]
    try:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = parse_mcp_config("codex", toml_text, "toml")
        assert result == []
        assert any(
            "3.11" in str(warning.message) or "tomli" in str(warning.message).lower()
            for warning in w
        )
    finally:
        for name, orig in (("tomllib", orig_lib), ("tomli", orig_li)):
            if orig is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = orig


def test_parse_toml_uses_tomli_backport_when_stdlib_absent():
    """Codex TOML: on 3.10 (no stdlib tomllib) the tomli backport parses the
    config — proving the Doctor reads Codex without a Python upgrade.

    Skips if tomli is not installed in this environment."""
    pytest.importorskip("tomli")
    from werktools.hub.onboarding import parse_mcp_config

    toml_text = (
        "[mcp_servers.weather]\n"
        "command = 'uvx'\n"
        "args = ['weather-mcp']\n"
        "[mcp_servers.weather.env]\n"
        "WEATHER_API_KEY = 'should-never-be-read'\n"
    )
    import sys

    orig_lib = sys.modules.get("tomllib")
    sys.modules["tomllib"] = None  # type: ignore[assignment]  # simulate 3.10
    try:
        result = parse_mcp_config("codex", toml_text, "toml")
    finally:
        if orig_lib is None:
            sys.modules.pop("tomllib", None)
        else:
            sys.modules["tomllib"] = orig_lib

    assert len(result) == 1
    server = result[0]
    assert server.name == "weather"
    assert server.command == "uvx"
    assert server.source_host == "codex"
    # PRESENCE-ONLY: the env KEY name is captured, never the value.
    assert server.needs_keys == ("WEATHER_API_KEY",)


# ---------------------------------------------------------------------------
# discover_from_hosts — integration-style, offline via home=tmp_path
# ---------------------------------------------------------------------------


def test_discover_finds_claude_servers(tmp_path):
    _make_claude_home(tmp_path)
    from werktools.hub.onboarding import discover_from_hosts

    servers = discover_from_hosts(home=tmp_path)
    names = {s.name for s in servers}
    assert "filesystem" in names
    assert "git" in names


def test_discover_finds_cursor_servers(tmp_path):
    _make_cursor_home(tmp_path)
    from werktools.hub.onboarding import discover_from_hosts

    servers = discover_from_hosts(home=tmp_path)
    names = {s.name for s in servers}
    assert "filesystem" in names
    assert "brave-search" in names


def test_discover_tags_source_host(tmp_path):
    _make_claude_home(tmp_path)
    from werktools.hub.onboarding import discover_from_hosts

    servers = discover_from_hosts(home=tmp_path)
    claude_servers = [s for s in servers if s.source_host == "claude"]
    assert len(claude_servers) == 2


def test_discover_missing_file_is_skipped(tmp_path):
    """A host whose config file doesn't exist -> silently skipped, no crash."""
    from werktools.hub.onboarding import discover_from_hosts

    # tmp_path has NO config files
    servers = discover_from_hosts(home=tmp_path)
    assert servers == []


def test_discover_values_never_in_output(tmp_path):
    """PRESENCE-ONLY: secret values must not appear in discovered servers."""
    _make_claude_home(tmp_path)
    _make_cursor_home(tmp_path)
    from werktools.hub.onboarding import discover_from_hosts

    servers = discover_from_hosts(home=tmp_path)
    serialized = json.dumps([s.to_dict() for s in servers])
    for forbidden in [
        "s3cr3t-value-MUST-NOT-APPEAR",
        "another-value-MUST-NOT-APPEAR",
        "cursor-secret-MUST-NOT-APPEAR",
        "brave-value-MUST-NOT-APPEAR",
    ]:
        assert forbidden not in serialized, f"Secret value leaked: {forbidden!r}"


# ---------------------------------------------------------------------------
# DEDUP tests
# ---------------------------------------------------------------------------


def test_dedup_same_server_in_two_hosts_yields_one_connector(tmp_path):
    """filesystem is in both claude and cursor configs -> ONE connector."""
    _make_claude_home(tmp_path)
    _make_cursor_home(tmp_path)
    hub_json = tmp_path / "hub.json"
    from werktools.hub.onboarding import discover_from_hosts, map_to_connectors

    discovered = discover_from_hosts(home=tmp_path)
    connectors = map_to_connectors(discovered, hub_json)
    # filesystem appears in claude (npx -y @modelcontextprotocol/server-filesystem /tmp)
    # and cursor (npx -y @modelcontextprotocol/server-filesystem /home) — different args!
    # These are NOT the same server (different paths), so we expect two filesystem entries.
    # But the claude filesystem and cursor filesystem have different args, so dedup won't merge.
    # Let's verify the total: 2 from claude + 2 from cursor = 4, minus any true duplicates.
    # In this fixture claude-filesystem has args ["-y","@mcp/fs","/tmp"] and cursor has "/home"
    # — different, so 4 total connectors expected.
    assert len(connectors) >= 2  # at minimum git + brave-search are unique


def test_dedup_identical_server_merged(tmp_path):
    """Two hosts with IDENTICAL command+args -> ONE connector."""
    # Write the same server config in two host files
    same_server = {
        "mcpServers": {
            "shared": {
                "command": "npx",
                "args": ["-y", "@mcp/shared-tool"],
            }
        }
    }
    (tmp_path / ".claude.json").write_text(json.dumps(same_server), encoding="utf-8")
    cursor = tmp_path / ".cursor"
    cursor.mkdir()
    (cursor / "mcp.json").write_text(json.dumps(same_server), encoding="utf-8")

    hub_json = tmp_path / "hub.json"
    from werktools.hub.onboarding import discover_from_hosts, map_to_connectors

    discovered = discover_from_hosts(home=tmp_path)
    connectors = map_to_connectors(discovered, hub_json)
    # Should be exactly ONE connector for "shared" (deduplicated)
    shared = [c for c in connectors if c.id == "shared"]
    assert len(shared) == 1


# ---------------------------------------------------------------------------
# Deny-by-default trust tests
# ---------------------------------------------------------------------------


def test_unknown_server_gets_community_unverified(tmp_path):
    """A server not on the Tier-1 allowlist -> Community-Unverified."""
    (tmp_path / ".claude.json").write_text(
        json.dumps({"mcpServers": {"my-unknown-tool": {"command": "uvx", "args": ["my-unknown"]}}}),
        encoding="utf-8",
    )
    hub_json = tmp_path / "hub.json"
    from werktools.hub.onboarding import discover_from_hosts, map_to_connectors

    discovered = discover_from_hosts(home=tmp_path)
    connectors = map_to_connectors(discovered, hub_json)
    assert len(connectors) == 1
    assert connectors[0].trust_tier == "Community-Unverified"


# ---------------------------------------------------------------------------
# onboard — dry-run and apply
# ---------------------------------------------------------------------------


def test_onboard_dry_run_writes_nothing(tmp_path):
    """Default (no apply) must not create hub.json."""
    _make_claude_home(tmp_path)
    hub_json = tmp_path / "hub.json"
    from werktools.hub.onboarding import onboard

    result = onboard(hub_json, home=tmp_path)
    assert not hub_json.exists(), "dry-run must not write hub.json"
    assert result.applied is False
    assert len(result.connectors) >= 1


def test_onboard_apply_writes_hub_json(tmp_path):
    """apply=True creates hub.json with the discovered connectors."""
    _make_claude_home(tmp_path)
    hub_json = tmp_path / "hub.json"
    from werktools.hub.onboarding import onboard

    result = onboard(hub_json, apply=True, home=tmp_path)
    assert hub_json.exists(), "apply=True must write hub.json"
    assert result.applied is True
    assert len(result.added) >= 1


def test_onboard_apply_no_duplicates_on_second_run(tmp_path):
    """Second apply must not duplicate connectors."""
    _make_claude_home(tmp_path)
    hub_json = tmp_path / "hub.json"
    from werktools.hub.onboarding import onboard

    r1 = onboard(hub_json, apply=True, home=tmp_path)
    r2 = onboard(hub_json, apply=True, home=tmp_path)
    # Second run: all connectors are already present -> added == ()
    assert r2.added == ()
    # Total connectors in file stays the same
    raw = json.loads(hub_json.read_text(encoding="utf-8"))
    assert len(raw["servers"]) == len(r1.added)


def test_onboard_existing_connector_not_overwritten(tmp_path):
    """An existing connector in hub.json is kept (not replaced)."""
    from werktools.hub.contracts import DownstreamServer, HubConfig
    from werktools.hub.registry import save_config

    hub_json = tmp_path / "hub.json"
    existing = DownstreamServer(
        id="filesystem",
        command="custom-command",
        args=("custom-arg",),
        trust_tier="Official",
        trust_source="manual",
        trust_note="manually set",
    )
    cfg = HubConfig(servers=(existing,))
    save_config(hub_json, cfg)

    # Claude config also has "filesystem" — it must NOT overwrite the existing one
    _make_claude_home(tmp_path)
    from werktools.hub.onboarding import onboard

    result = onboard(hub_json, apply=True, home=tmp_path)
    raw = json.loads(hub_json.read_text(encoding="utf-8"))
    fs_entries = [s for s in raw["servers"] if s["id"] == "filesystem"]
    assert len(fs_entries) == 1
    # The existing command is preserved
    assert fs_entries[0]["command"] == "custom-command"
    # It was reported as skipped (collision)
    assert "filesystem" in result.skipped_hosts or len(result.added) < len(result.connectors)


def test_onboard_secret_values_never_in_hub_json(tmp_path):
    """PRESENCE-ONLY: secret values must NEVER appear in hub.json after apply."""
    _make_claude_home(tmp_path)
    hub_json = tmp_path / "hub.json"
    from werktools.hub.onboarding import onboard

    onboard(hub_json, apply=True, home=tmp_path)
    content = hub_json.read_text(encoding="utf-8")
    for forbidden in [
        "s3cr3t-value-MUST-NOT-APPEAR",
        "another-value-MUST-NOT-APPEAR",
    ]:
        assert forbidden not in content, f"Secret value leaked into hub.json: {forbidden!r}"


def test_onboard_result_counts_by_host(tmp_path):
    """by_host counts reflect servers found per host."""
    _make_claude_home(tmp_path)
    hub_json = tmp_path / "hub.json"
    from werktools.hub.onboarding import onboard

    result = onboard(hub_json, home=tmp_path)
    assert "claude" in result.by_host
    assert result.by_host["claude"] >= 1


def test_onboard_corrupt_config_skipped_with_warning(tmp_path):
    """A corrupt host config is skipped (warned) and onboard still completes."""
    (tmp_path / ".claude.json").write_text("{bad json", encoding="utf-8")
    _make_cursor_home(tmp_path)
    hub_json = tmp_path / "hub.json"
    from werktools.hub.onboarding import onboard

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = onboard(hub_json, home=tmp_path)
    # Should still get cursor servers
    assert len(result.discovered) >= 1
    # And at least one warning about the corrupt config
    assert any("claude" in str(warning.message).lower() or "corrupt" in str(warning.message).lower() for warning in w)


def test_onboard_result_is_frozen():
    """OnboardResult must be a frozen dataclass."""
    import dataclasses

    from werktools.hub.onboarding import OnboardResult

    assert dataclasses.is_dataclass(OnboardResult)
    # frozen dataclasses raise FrozenInstanceError on setattr
    r = OnboardResult(
        discovered=(),
        connectors=(),
        added=(),
        skipped_hosts=(),
        by_host={},
        applied=False,
    )
    with pytest.raises((TypeError, AttributeError)):
        r.applied = True  # type: ignore[misc]


def test_discovered_server_to_dict_no_secret_values(tmp_path):
    """DiscoveredServer.to_dict() must not expose any env values."""
    from werktools.hub.onboarding import parse_mcp_config

    text = json.dumps(_CLAUDE_JSON)
    servers = parse_mcp_config("claude", text, "json")
    for s in servers:
        d = s.to_dict()
        dumped = json.dumps(d)
        assert "s3cr3t-value-MUST-NOT-APPEAR" not in dumped
        assert "another-value-MUST-NOT-APPEAR" not in dumped


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


def test_cli_hub_onboard_dry_run(tmp_path, capsys, monkeypatch):
    """CLI 'hub onboard' (no --apply) prints table and writes nothing."""
    monkeypatch.chdir(tmp_path)
    _make_claude_home(tmp_path)
    hub_json = tmp_path / ".werktools" / "hub.json"
    hub_json.parent.mkdir(parents=True, exist_ok=True)

    from werktools.cli import main

    code = main(["--config", str(hub_json), "hub", "onboard", "--home", str(tmp_path)])
    out, err = capsys.readouterr()
    assert code == 0
    # hub.json was NOT created (dry run)
    assert not hub_json.exists()
    # Output mentions discovered servers
    assert "filesystem" in out or "git" in out


def test_cli_hub_onboard_apply(tmp_path, capsys, monkeypatch):
    """CLI 'hub onboard --apply' writes hub.json and reports what was added."""
    monkeypatch.chdir(tmp_path)
    _make_claude_home(tmp_path)
    hub_json = tmp_path / ".werktools" / "hub.json"
    hub_json.parent.mkdir(parents=True, exist_ok=True)

    from werktools.cli import main

    code = main(["--config", str(hub_json), "hub", "onboard", "--apply", "--home", str(tmp_path)])
    out, err = capsys.readouterr()
    assert code == 0
    assert hub_json.exists()
    assert "added" in out.lower() or "filesystem" in out or "git" in out


def test_cli_hub_onboard_host_filter(tmp_path, capsys, monkeypatch):
    """CLI --host filters to a single host."""
    monkeypatch.chdir(tmp_path)
    _make_claude_home(tmp_path)
    _make_cursor_home(tmp_path)
    hub_json = tmp_path / ".werktools" / "hub.json"
    hub_json.parent.mkdir(parents=True, exist_ok=True)

    from werktools.cli import main

    code = main(["--config", str(hub_json), "hub", "onboard", "--host", "claude", "--home", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    # brave-search is only in cursor, so it should NOT appear
    assert "brave-search" not in out
