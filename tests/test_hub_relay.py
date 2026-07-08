"""Offline tests for downstream MCP discovery and read-only forwarding."""

import asyncio
import json

import pytest

try:
    import fastmcp  # noqa: F401

    FASTMCP_AVAILABLE = True
except ImportError:
    FASTMCP_AVAILABLE = False

from werktools.hub.ledger import recent_events
from werktools.hub.registry import load_config


def _envelope(result):
    content = getattr(result, "content", result)
    return json.loads(content[0].text)


async def _call(server, tool, args):
    from fastmcp import Client

    async with Client(server) as client:
        result = await client.call_tool(tool, args)
        return _envelope(result)


def _downstream():
    from fastmcp import FastMCP

    server = FastMCP(name="docs")

    @server.tool(annotations={"readOnlyHint": True})
    def lookup(term: str) -> dict:
        return {"found": term}

    @server.tool()
    def write_note(text: str) -> dict:
        return {"wrote": text}

    @server.tool(annotations={"readOnlyHint": True})
    def purge(path: str) -> dict:
        """Delete and remove all files below a path."""
        return {"deleted": path}

    return server


def _config(permission_profile="cautious", allowed_servers=("docs",), pin_lookup=False):
    tools = []
    if pin_lookup:
        # Operator config-pins docs.lookup as read so it earns the token-free
        # auto-forward path; discovery then matches it by id.
        tools = [
            {
                "id": "docs.lookup",
                "name": "lookup",
                "server_id": "docs",
                "risk": "read",
                "read_only": True,
                "tags": ["read"],
            }
        ]
    return load_config(
        {
            "name": "werk-hub",
            "default_profile": "tester",
            "profiles": [
                {
                    "id": "tester",
                    "permission_profile": permission_profile,
                    "visible_tags": ["read", "write", "external", "docs"],
                    "allowed_servers": list(allowed_servers),
                }
            ],
            "tools": tools,
        }
    )


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed (werktools[server] required)")
class TestDiscovery:
    def test_discover_tools_normalizes_annotations(self):
        from werktools.hub.relay import discover_tools

        cards = {card.id: card for card in discover_tools("docs", _downstream())}

        assert cards["docs.lookup"].risk == "read"
        assert cards["docs.lookup"].read_only is True
        assert cards["docs.write_note"].risk != "read"

    def test_annotations_cannot_loosen_keyword_signals(self):
        from werktools.hub.relay import discover_tools

        cards = {card.id: card for card in discover_tools("docs", _downstream())}

        # purge claims readOnlyHint=True but its description says delete/remove:
        # the keyword classifier may only TIGHTEN, so it must not stay read.
        assert cards["docs.purge"].risk != "read"

    def test_non_keyword_destructive_vocab_does_not_become_read(self):
        from fastmcp import FastMCP

        from werktools.hub.relay import discover_tools

        server = FastMCP(name="cache")

        @server.tool(annotations={"readOnlyHint": True})
        def flush_cache() -> dict:
            """Clears the in-memory cache."""
            return {}

        @server.tool(annotations={"readOnlyHint": True})
        def reset_state() -> dict:
            """Resets the application state to defaults."""
            return {}

        cards = {card.id: card for card in discover_tools("cache", server)}

        # readOnlyHint is a hint, not a risk floor: a tool with no positive
        # read evidence and a mutating verb must not be classified read.
        assert cards["cache.flush_cache"].risk != "read"
        assert cards["cache.reset_state"].risk != "read"

    def test_plain_read_tool_stays_read(self):
        from fastmcp import FastMCP

        from werktools.hub.relay import discover_tools

        server = FastMCP(name="docs")

        @server.tool(annotations={"readOnlyHint": True})
        def get_document(doc_id: str) -> dict:
            """Return the document for an id."""
            return {"id": doc_id}

        cards = {card.id: card for card in discover_tools("docs", server)}

        assert cards["docs.get_document"].risk == "read"

    def test_discover_tools_times_out_on_dead_target(self):
        import time

        from werktools.hub.relay import discover_tools

        class DeadTarget:
            def __init__(self):
                pass

        # A target that never completes initialize must not hang forever;
        # discover_tools must raise within a bounded time.
        start = time.monotonic()
        with pytest.raises(Exception):
            discover_tools("dead", {"mcpServers": {"dead": {"command": "doesnotexist-xyz", "args": []}}})
        assert time.monotonic() - start < 60


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed (werktools[server] required)")
class TestForwarding:
    def _hub(self, tmp_path, config=None):
        from werktools.hub.server import build_hub_server

        return build_hub_server(
            config or _config(),
            profile_id="tester",
            ledger_path=tmp_path / "hub-ledger.jsonl",
            relay_targets={"docs": _downstream()},
        )

    def test_operator_pinned_read_tool_is_searchable_and_callable(self, tmp_path):
        # Only an operator-config-pinned read card may auto-forward token-free.
        hub = self._hub(tmp_path, config=_config(pin_lookup=True))

        search = asyncio.run(_call(hub, "tool_search", {"query": "lookup"}))
        ids = [tool["id"] for tool in search["data"]["tools"]]
        assert "docs.lookup" in ids

        envelope = asyncio.run(_call(hub, "tool_call", {"tool_id": "docs.lookup", "args": {"term": "policy"}}))

        assert envelope["ok"] is True
        events = [e["payload"]["type"] for e in recent_events(tmp_path / "hub-ledger.jsonl", limit=20)]
        assert "tool.discovered" in events
        assert "tool.call.completed" in events

    def test_downstream_declared_read_tool_needs_approval(self, tmp_path):
        # docs.lookup is discovered read-only by the downstream annotation but
        # is NOT operator-pinned: the token-free path must be closed and the
        # call must fail closed to an approval token.
        hub = self._hub(tmp_path)

        search = asyncio.run(_call(hub, "tool_search", {"query": "lookup"}))
        ids = [tool["id"] for tool in search["data"]["tools"]]
        assert "docs.lookup" in ids  # still discoverable/visible

        envelope = asyncio.run(_call(hub, "tool_call", {"tool_id": "docs.lookup", "args": {"term": "policy"}}))

        assert envelope["ok"] is False
        assert "approval token" in envelope["error"]
        events = [e["payload"]["type"] for e in recent_events(tmp_path / "hub-ledger.jsonl", limit=20)]
        assert "tool.call.completed" not in events

    def test_write_tool_is_denied_for_cautious_profile(self, tmp_path):
        hub = self._hub(tmp_path)

        envelope = asyncio.run(_call(hub, "tool_call", {"tool_id": "docs.write_note", "args": {"text": "x"}}))

        assert envelope["ok"] is False

    def test_relayed_write_fails_closed_even_for_admin(self, tmp_path):
        hub = self._hub(tmp_path, config=_config(permission_profile="admin"))

        envelope = asyncio.run(_call(hub, "tool_call", {"tool_id": "docs.write_note", "args": {"text": "x"}}))

        # admin's policy allows the write, but the v0 relay still requires an
        # explicit approval token to forward any non-read downstream tool.
        assert envelope["ok"] is False
        assert "approval token" in envelope["error"]
        events = [e["payload"]["type"] for e in recent_events(tmp_path / "hub-ledger.jsonl", limit=20)]
        assert "tool.call.completed" not in events

    def test_server_not_in_allowed_servers_is_invisible(self, tmp_path):
        hub = self._hub(tmp_path, config=_config(allowed_servers=()))

        search = asyncio.run(_call(hub, "tool_search", {}))
        ids = [tool["id"] for tool in search["data"]["tools"]]
        assert "docs.lookup" not in ids

        envelope = asyncio.run(_call(hub, "tool_call", {"tool_id": "docs.lookup", "args": {"term": "x"}}))
        assert envelope["ok"] is False


def test_downstream_server_config_round_trip():
    config = load_config(
        {
            "name": "werk-hub",
            "servers": [
                {"id": "docs", "command": "python", "args": ["docs_server.py"]},
                {"id": "off", "command": "python", "args": [], "enabled": False},
            ],
        }
    )

    assert [server.id for server in config.servers] == ["docs", "off"]
    assert config.servers[0].enabled is True
    assert config.servers[1].enabled is False
    assert config.to_dict()["servers"][0]["command"] == "python"


def test_downstream_call_timeout_has_honest_nonempty_message(monkeypatch):
    # SF12: a downstream call timeout must surface a non-empty error message
    # (str(asyncio.TimeoutError()) is "" -> empty envelope + empty ledger
    # reason). Drive the real wait_for timeout path with a tiny CALL_TIMEOUT
    # and a hanging client; assert the honest TimeoutError message.
    from werktools.hub import relay

    class HangingClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call_tool(self, *a, **k):
            await asyncio.sleep(5)  # longer than the patched timeout

    monkeypatch.setattr(relay, "Client", HangingClient)
    monkeypatch.setattr(relay, "CALL_TIMEOUT", 0.05)

    with pytest.raises(TimeoutError) as excinfo:
        asyncio.run(relay._call_tool(object(), "docs.lookup", {}))

    msg = str(excinfo.value)
    assert msg.strip() != ""
    assert "timed out" in msg


# ---------------------------------------------------------------------------
# Per-downstream cwd + discovery stderr diagnostics (interop gaps found
# live-testing a real context-dependent MCP on 2026-06-27).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
def test_transport_for_threads_cwd_into_stdio_spec():
    """A configured cwd reaches the FastMCP stdio spec; absent cwd is omitted."""
    from werktools.hub import relay
    from werktools.hub.contracts import DownstreamServer

    with_cwd = DownstreamServer(id="d", command="python", args=("x.py",), cwd="C:/data/dir")
    spec = relay.transport_for(with_cwd)["mcpServers"]["d"]
    assert spec["cwd"] == "C:/data/dir"

    without = DownstreamServer(id="d2", command="python")
    assert "cwd" not in relay.transport_for(without)["mcpServers"]["d2"]


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
def test_collect_startup_stderr_captures_downstream_diagnostic():
    """The diagnostic probe surfaces a stdio downstream's own stderr (the thing
    FastMCP's generic 'failed to initialize' error hides)."""
    import sys

    from werktools.hub import relay
    from werktools.hub.contracts import DownstreamServer

    server = DownstreamServer(
        id="diag",
        command=sys.executable,
        args=(
            "-c",
            "import sys,time; sys.stderr.write('Zugriff verweigert\n'); "
            "sys.stderr.flush(); time.sleep(5)",
        ),
    )
    out = relay.collect_startup_stderr(server, timeout=2.0)
    assert "Zugriff verweigert" in out


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
def test_collect_startup_stderr_runs_in_configured_cwd(tmp_path):
    """The probe spawns the downstream in its configured cwd."""
    import sys

    from werktools.hub import relay
    from werktools.hub.contracts import DownstreamServer

    server = DownstreamServer(
        id="cwdprobe",
        command=sys.executable,
        args=("-c", "import os,sys,time; print(os.getcwd(), file=sys.stderr); sys.stderr.flush(); time.sleep(5)"),
        cwd=str(tmp_path),
    )
    out = relay.collect_startup_stderr(server, timeout=2.0)
    assert tmp_path.name in out


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
def test_collect_startup_stderr_skips_non_stdio_and_commandless():
    """No probe (empty string) for http servers or stdio entries without a command."""
    from werktools.hub import relay
    from werktools.hub.contracts import DownstreamServer

    assert relay.collect_startup_stderr(DownstreamServer(id="h", transport="http", url="https://x")) == ""
    assert relay.collect_startup_stderr(DownstreamServer(id="empty", command="")) == ""


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
def test_failed_discovery_records_downstream_stderr(tmp_path, recwarn):
    """A stdio downstream that crashes on startup gets its stderr captured into
    the tool.discovered error event so the operator can see WHY."""
    import sys

    from werktools.hub.registry import load_config
    from werktools.hub.server import build_hub_server  # noqa: F401  (ensures import path)

    ledger = tmp_path / "l.jsonl"
    config = load_config(
        {
            "name": "werk-hub",
            "ledger_path": str(ledger),
            "servers": [
                {
                    "id": "broken",
                    "command": sys.executable,
                    # prints a diagnostic to stderr then exits non-zero -> discovery fails
                    "args": ["-c", "import sys; sys.stderr.write('boom: access denied\n'); sys.exit(3)"],
                }
            ],
            "profiles": [{"id": "p", "allowed_servers": ["broken"], "allowed_tools": []}],
        }
    )
    from werktools.hub.server import build_hub_server as build

    build(config, profile_id="p", ledger_path=str(ledger))
    events = [e for e in recent_events(ledger) if e.get("payload", {}).get("type") == "tool.discovered"]
    err_events = [e["payload"] for e in events if e["payload"].get("error")]
    assert err_events, "expected a failed discovery event"
    assert any("boom: access denied" in (p.get("stderr") or "") for p in err_events)
    # the raw stderr is also classified into an actionable verdict
    classified = [p for p in err_events if p.get("cause") == "single_instance"]
    assert classified, "expected the access-denied stderr to classify as single_instance"
    assert classified[0]["supported"] is False
    assert classified[0]["remedy"]


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
def test_collect_startup_stderr_handles_non_utf8_without_raising():
    """A downstream emitting non-UTF-8 bytes must not raise (the never-raises
    contract); it is decoded utf-8/replace, not via the locale codec."""
    import sys

    from werktools.hub import relay
    from werktools.hub.contracts import DownstreamServer

    server = DownstreamServer(
        id="binstderr",
        command=sys.executable,
        # write a raw invalid-UTF-8 byte (0xff) to stderr, then linger
        args=("-c", "import sys,time; sys.stderr.buffer.write(b'oops \xff bad\n'); sys.stderr.buffer.flush(); time.sleep(5)"),
    )
    out = relay.collect_startup_stderr(server, timeout=2.0)
    assert "oops" in out  # decoded, no crash


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
def test_collect_startup_stderr_marks_silent_timeout():
    """A downstream that hangs silently (no stderr) yields an honest timeout
    marker, not an empty string that reads as 'nothing to report'."""
    import sys

    from werktools.hub import relay
    from werktools.hub.contracts import DownstreamServer

    server = DownstreamServer(
        id="silent",
        command=sys.executable,
        args=("-c", "import time; time.sleep(10)"),  # no stdout/stderr, just hang
    )
    out = relay.collect_startup_stderr(server, timeout=1.0)
    assert "timed out" in out.lower()


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
def test_collect_startup_stderr_caps_flooded_stderr():
    """A downstream flooding stderr is byte-capped, so the result stays bounded."""
    import sys

    from werktools.hub import relay
    from werktools.hub.contracts import DownstreamServer

    server = DownstreamServer(
        id="flood",
        command=sys.executable,
        args=("-c", "import sys; sys.stderr.write('A'*200000); sys.stderr.flush()"),
    )
    out = relay.collect_startup_stderr(server, timeout=3.0)
    assert 0 < len(out) <= relay._DIAG_MAX_BYTES
