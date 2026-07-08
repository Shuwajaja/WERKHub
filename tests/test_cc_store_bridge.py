import json

import pytest

from werktools.hub.contracts import DownstreamServer, HubConfig
from werktools.hub.store_bridge import (
    connector_to_downstream_server,
    persist_hub_config,
    sync_connectors_to_hub,
)
from werktools.tools.integration_gate import Connector, add_connector


def test_connector_to_downstream_server_basic():
    connector = Connector.from_dict(
        {
            "connector_id": "docs",
            "scopes": [{"name": "read", "access": "read", "description": "d"}],
            "metadata": {"command": "python", "args": ["docs_server.py"]},
        }
    )

    server = connector_to_downstream_server(connector)

    assert server.id == "docs"
    assert server.command == "python"
    assert server.args == ("docs_server.py",)
    assert server.transport == "stdio"


def test_connector_without_command_raises():
    connector = Connector.from_dict(
        {"connector_id": "bad", "scopes": [{"name": "s", "access": "read", "description": "d"}], "metadata": {}}
    )

    with pytest.raises(ValueError):
        connector_to_downstream_server(connector)


def test_sync_is_pure_and_dedupes(tmp_path):
    root = tmp_path / "gate"
    add_connector(
        root,
        "github",
        scopes=({"name": "repo:read", "access": "read", "description": "d"},),
        metadata={"command": "node", "args": ["gh.js"]},
    )
    base = HubConfig(name="werk-hub")

    once = sync_connectors_to_hub(root, base)
    twice = sync_connectors_to_hub(root, once)

    assert [s.id for s in once.servers] == ["github"]
    assert [s.id for s in twice.servers] == ["github"]
    # purity: no hub.json written by sync
    assert not (tmp_path / "hub.json").exists()


def test_sync_skips_bad_connector_with_warning(tmp_path):
    root = tmp_path / "gate"
    add_connector(
        root,
        "nocommand",
        scopes=({"name": "s", "access": "read", "description": "d"},),
        metadata={"note": "no command here"},
    )
    base = HubConfig(name="werk-hub")

    with pytest.warns(UserWarning):
        result = sync_connectors_to_hub(root, base)

    assert result.servers == ()


def test_persist_is_atomic_and_valid(tmp_path):
    config = HubConfig(name="werk-hub", servers=(DownstreamServer(id="x", command="python"),))
    out = tmp_path / "nested" / "hub.json"

    persist_hub_config(config, out)

    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["servers"][0]["id"] == "x"
    assert not (tmp_path / "nested" / "hub.json.tmp").exists()


def test_downstream_transport_round_trip():
    server = DownstreamServer(
        id="remote",
        transport="http",
        url="https://example.com/mcp",
        headers={"Authorization-Style": "bearer"},
    )

    restored = DownstreamServer.from_dict(server.to_dict())

    assert restored.transport == "http"
    assert restored.url == "https://example.com/mcp"
    assert restored.headers == {"Authorization-Style": "bearer"}


def test_downstream_backward_compat_minimal():
    server = DownstreamServer.from_dict({"id": "old", "command": "python"})

    assert server.transport == "stdio"
    assert server.url is None
    assert server.headers == {}
    assert server.env == {}
    assert "url" not in server.to_dict()


def test_downstream_unknown_transport_coerces_to_stdio():
    server = DownstreamServer.from_dict({"id": "x", "command": "y", "transport": "carrier-pigeon"})

    assert server.transport == "stdio"


def test_downstream_rejects_secret_env_key():
    with pytest.raises(ValueError):
        DownstreamServer.from_dict({"id": "x", "command": "y", "env": {"API_KEY": "sk-123"}})


def test_downstream_is_unhashable():
    with pytest.raises(TypeError):
        hash(DownstreamServer(id="x", command="y"))


def test_downstream_no_dict_mutation_across_instances():
    a = DownstreamServer(id="a", command="x")
    b = DownstreamServer(id="b", command="y")
    a.headers["leak"] = "1"

    assert b.headers == {}


def test_connector_unknown_transport_coerces_to_stdio():
    from werktools.tools.integration_gate import Connector

    connector = Connector.from_dict(
        {
            "connector_id": "weird",
            "scopes": [{"name": "s", "access": "read", "description": "d"}],
            "metadata": {"command": "python", "transport": "carrier-pigeon"},
        }
    )

    server = connector_to_downstream_server(connector)

    assert server.transport == "stdio"


def test_transport_for_http_target():
    # Wave 5 extended transport_for to support http/sse via server.url.
    import pytest as _pytest

    _pytest.importorskip("fastmcp")
    from werktools.hub.relay import transport_for

    server = DownstreamServer(id="remote", transport="http", url="https://example.com/mcp")
    target = transport_for(server)
    assert target["mcpServers"]["remote"]["url"] == "https://example.com/mcp"

    with _pytest.raises(ValueError):
        transport_for(DownstreamServer(id="r", transport="http"))
