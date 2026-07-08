import json
import sys

import pytest

from werktools.hub.contracts import DownstreamServer
from werktools.hub.registry import load_config
from werktools.hub.relay import transport_for
from werktools.hub.render import HOSTS, _allowed_servers, render


def _config():
    # local-docs exposes a read tool (visible to claude-reviewer); writer-fs
    # exposes only a write tool hidden from claude-reviewer; remote-api is in
    # allowed_servers explicitly; disabled-srv is off.
    return load_config(
        {
            "name": "werk-hub",
            "default_profile": "claude-reviewer",
            "profiles": [
                {
                    "id": "claude-reviewer",
                    "permission_profile": "cautious",
                    "visible_tags": ["read", "docs"],
                    "allowed_servers": ["remote-api"],
                },
                {
                    "id": "human-admin",
                    "permission_profile": "admin",
                    "visible_tags": ["read", "write", "external", "secret", "unknown"],
                },
            ],
            "tools": [
                {"id": "docs.search", "server_id": "local-docs", "name": "search", "risk": "read", "tags": ["read"]},
                {"id": "fs.write", "server_id": "writer-fs", "name": "write", "risk": "write", "read_only": False, "tags": ["write"]},
            ],
            "servers": [
                {"id": "local-docs", "command": "python", "args": ["docs.py"]},
                {"id": "writer-fs", "command": "python", "args": ["fs.py"]},
                {"id": "remote-api", "transport": "http", "url": "https://api.example.com/mcp", "headers": {"X-Tag": "v1"}},
                {"id": "disabled-srv", "command": "python", "args": [], "enabled": False},
            ],
        }
    )


def test_transport_for_stdio_with_env():
    server = DownstreamServer(id="s", command="python", args=("x",), env={"FOO": "bar"})
    target = transport_for(server)
    assert target["mcpServers"]["s"]["env"] == {"FOO": "bar"}


def test_transport_for_http_uses_url():
    server = DownstreamServer(id="r", transport="http", url="https://x/mcp")
    assert transport_for(server)["mcpServers"]["r"]["url"] == "https://x/mcp"


def test_transport_for_http_without_url_raises():
    with pytest.raises(ValueError):
        transport_for(DownstreamServer(id="r", transport="http"))


def test_allowed_servers_filter():
    ids = {s.id for s in _allowed_servers(_config(), "claude-reviewer")}
    assert "local-docs" in ids  # has a visible read tool
    assert "remote-api" in ids  # in allowed_servers
    assert "writer-fs" not in ids  # only a hidden write tool
    assert "disabled-srv" not in ids


def test_write_server_physically_absent_all_hosts():
    cfg = _config()
    for host in HOSTS:
        if host == "goose":
            continue
        text = render(cfg, "claude-reviewer", host)
        assert "writer-fs" not in text
        assert "disabled-srv" not in text


def test_admin_profile_includes_write_server():
    text = render(_config(), "human-admin", "claude")
    assert "writer-fs" in text


def test_claude_shape():
    body = json.loads(render(_config(), "claude-reviewer", "claude"))
    assert "mcpServers" in body
    assert body["mcpServers"]["remote-api"]["url"] == "https://api.example.com/mcp"


def test_vscode_uses_servers_key():
    body = json.loads(render(_config(), "claude-reviewer", "vscode"))
    assert "servers" in body and "mcpServers" not in body


def test_windsurf_uses_serverurl():
    body = json.loads(render(_config(), "claude-reviewer", "windsurf"))
    assert body["mcpServers"]["remote-api"]["serverUrl"] == "https://api.example.com/mcp"


def test_gemini_uses_httpurl():
    body = json.loads(render(_config(), "claude-reviewer", "gemini"))
    assert body["mcpServers"]["remote-api"]["httpUrl"] == "https://api.example.com/mcp"


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib needs 3.11+")
def test_codex_is_valid_toml():
    import tomllib

    cfg = load_config(
        {
            "name": "werk-hub",
            "default_profile": "admin",
            "profiles": [{"id": "admin", "permission_profile": "admin", "visible_tags": ["read"], "allowed_servers": ["hyphen-id"]}],
            "tools": [],
            "servers": [{"id": "hyphen-id", "command": "python", "args": ["x"], "env": {"K": "v"}}],
        }
    )
    parsed = tomllib.loads(render(cfg, "admin", "codex"))
    assert parsed["mcp_servers"]["hyphen-id"]["command"] == "python"
    assert parsed["mcp_servers"]["hyphen-id"]["env"]["K"] == "v"


def test_unknown_host_raises():
    with pytest.raises(ValueError):
        render(_config(), "claude-reviewer", "emacs")


def test_cli_render_and_ledger(tmp_path, capsys):
    from werktools.cli import main
    from werktools.hub.ledger import recent_events

    config = tmp_path / "hub.json"
    body = _config().to_dict()
    body["ledger_path"] = str(tmp_path / "hub-ledger.jsonl")
    config.write_text(json.dumps(body), encoding="utf-8")

    assert main(["--config", str(config), "hub", "render", "--profile", "claude-reviewer", "--host", "claude"]) == 0
    assert "mcpServers" in capsys.readouterr().out

    out_file = tmp_path / "out.json"
    assert main(["--config", str(config), "hub", "render", "--host", "vscode", "--out", str(out_file)]) == 0
    assert out_file.exists()

    assert main(["--config", str(config), "hub", "render", "--host", "emacs"]) == 1

    types = [e["payload"]["type"] for e in recent_events(tmp_path / "hub-ledger.jsonl", limit=10)]
    assert "config.rendered" in types
