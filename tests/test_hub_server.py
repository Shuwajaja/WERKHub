"""In-process tests for the werk-hub MCP server (bridge tools)."""

import asyncio
import json

import pytest

try:
    import fastmcp  # noqa: F401

    FASTMCP_AVAILABLE = True
except ImportError:
    FASTMCP_AVAILABLE = False

from werktools.hub.ledger import recent_events
from werktools.hub.registry import default_config


def _envelope(result):
    content = getattr(result, "content", result)
    return json.loads(content[0].text)


async def _call(server, tool, args):
    from fastmcp import Client

    async with Client(server) as client:
        result = await client.call_tool(tool, args)
        return _envelope(result)


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed (werktools[server] required)")
class TestHubServer:
    def _server(self, tmp_path, profile_id="claude-reviewer", handlers=None):
        from werktools.hub.server import build_hub_server

        return build_hub_server(
            default_config(),
            profile_id=profile_id,
            handlers=handlers,
            ledger_path=tmp_path / "hub-ledger.jsonl",
            approvals_dir=tmp_path / "hub-approvals",
        )

    def test_exposes_exactly_the_base_bridge_tools(self, tmp_path):
        from fastmcp import Client

        server = self._server(tmp_path)

        async def run():
            async with Client(server) as client:
                tools = await client.list_tools()
                return sorted(tool.name for tool in tools)

        names = asyncio.run(run())
        assert names == [
            "approval_status",
            "hub_status",
            "ledger_recent",
            "profile_info",
            "registry_search",
            "tool_call",
            "tool_describe",
            "tool_search",
        ]

    def test_tool_search_is_profile_filtered(self, tmp_path):
        server = self._server(tmp_path, profile_id="claude-reviewer")

        envelope = asyncio.run(_call(server, "tool_search", {}))

        ids = [tool["id"] for tool in envelope["data"]["tools"]]
        assert "docs.search" in ids
        assert "github.create_pr" not in ids
        assert "filesystem.write_file" not in ids

    def test_tool_describe_hides_invisible_tools(self, tmp_path):
        server = self._server(tmp_path, profile_id="claude-reviewer")

        visible = asyncio.run(_call(server, "tool_describe", {"tool_id": "docs.search"}))
        hidden = asyncio.run(_call(server, "tool_describe", {"tool_id": "filesystem.write_file"}))

        assert visible["ok"] is True
        assert visible["data"]["tool"]["id"] == "docs.search"
        assert hidden["ok"] is False

    def test_tool_call_runs_allowed_local_handler_and_ledgers(self, tmp_path):
        server = self._server(
            tmp_path,
            profile_id="claude-reviewer",
            handlers={"docs.search": lambda args: {"hits": [args.get("query", "")]}},
        )

        envelope = asyncio.run(_call(server, "tool_call", {"tool_id": "docs.search", "args": {"query": "policy"}}))

        assert envelope["ok"] is True
        assert envelope["data"]["result"] == {"hits": ["policy"]}
        events = [e["payload"]["type"] for e in recent_events(tmp_path / "hub-ledger.jsonl", limit=10)]
        assert "tool.call.requested" in events
        assert "tool.call.completed" in events

    def test_tool_call_approval_required_fails_closed(self, tmp_path):
        server = self._server(
            tmp_path,
            profile_id="codex-builder",
            handlers={"github.create_pr": lambda args: {"should": "never run"}},
        )

        envelope = asyncio.run(_call(server, "tool_call", {"tool_id": "github.create_pr", "args": {}}))

        assert envelope["ok"] is False
        events = [e["payload"]["type"] for e in recent_events(tmp_path / "hub-ledger.jsonl", limit=10)]
        assert "tool.call.approval_required" in events
        assert "tool.call.completed" not in events

    def test_tool_call_denied_tool_is_ledgered(self, tmp_path):
        server = self._server(tmp_path, profile_id="codex-builder")

        envelope = asyncio.run(_call(server, "tool_call", {"tool_id": "shell.run", "args": {}}))

        assert envelope["ok"] is False
        events = [e["payload"]["type"] for e in recent_events(tmp_path / "hub-ledger.jsonl", limit=10)]
        assert "tool.call.denied" in events

    def test_tool_call_without_handler_fails_honestly(self, tmp_path):
        server = self._server(tmp_path, profile_id="claude-reviewer")

        envelope = asyncio.run(_call(server, "tool_call", {"tool_id": "docs.search", "args": {}}))

        assert envelope["ok"] is False
        assert "handler" in envelope["error"]

    def test_handler_exception_returns_err_and_ledgers_failed(self, tmp_path):
        def boom(args):
            raise RuntimeError("downstream exploded")

        server = self._server(tmp_path, profile_id="claude-reviewer", handlers={"docs.search": boom})

        envelope = asyncio.run(_call(server, "tool_call", {"tool_id": "docs.search", "args": {}}))

        assert envelope["ok"] is False
        assert "downstream exploded" in envelope["error"]
        events = [e["payload"]["type"] for e in recent_events(tmp_path / "hub-ledger.jsonl", limit=10)]
        assert "tool.call.failed" in events

    def test_profile_info_reports_active_profile(self, tmp_path):
        server = self._server(tmp_path, profile_id="claude-reviewer")

        envelope = asyncio.run(_call(server, "profile_info", {}))

        assert envelope["ok"] is True
        assert envelope["data"]["profile"]["id"] == "claude-reviewer"

    def test_ledger_recent_returns_events(self, tmp_path):
        server = self._server(tmp_path, profile_id="claude-reviewer")

        asyncio.run(_call(server, "tool_search", {}))
        envelope = asyncio.run(_call(server, "ledger_recent", {"limit": 5}))

        assert envelope["ok"] is True
        assert envelope["data"]["events"]

    def test_approval_status_is_empty_in_v0(self, tmp_path):
        server = self._server(tmp_path, profile_id="claude-reviewer")

        envelope = asyncio.run(_call(server, "approval_status", {}))

        assert envelope["ok"] is True
        assert envelope["data"]["pending"] == []

    def test_hub_status_tool_returns_snapshot(self, tmp_path):
        server = self._server(tmp_path, profile_id="claude-reviewer")

        envelope = asyncio.run(_call(server, "hub_status", {}))

        assert envelope["ok"] is True
        assert "servers" in envelope["data"]
        assert envelope["data"]["profile_id"] == "claude-reviewer"

    def test_registry_search_denied_for_cautious_profile_makes_no_network_call(self, tmp_path):
        # SF10: registry_search makes an outbound HTTPS call, so a cautious
        # profile must be denied and NO network touched (fail closed).
        from werktools.hub.registry import default_config
        from werktools.hub.server import build_hub_server

        def spy(url):
            raise AssertionError("network touched on a denied profile")

        server = build_hub_server(
            default_config(),
            profile_id="claude-reviewer",  # cautious
            ledger_path=tmp_path / "hub-ledger.jsonl",
            approvals_dir=tmp_path / "hub-approvals",
            registry_http_get=spy,
        )

        envelope = asyncio.run(_call(server, "registry_search", {"query": "docs"}))

        assert envelope["ok"] is False
        assert "claude-reviewer" in envelope["error"] or "not allowed" in envelope["error"]
        events = recent_events(tmp_path / "hub-ledger.jsonl")
        assert any(
            e["payload"]["type"] == "tool.call.denied" and e["payload"].get("tool") == "registry_search"
            for e in events
        )

    def test_registry_search_allowed_for_balanced_profile(self, tmp_path):
        # SF10: a balanced profile may reach out; the gated path runs.
        from werktools.hub.registry import default_config
        from werktools.hub.server import build_hub_server

        def stub(url):
            return {"servers": []}

        server = build_hub_server(
            default_config(),
            profile_id="codex-builder",  # balanced
            ledger_path=tmp_path / "hub-ledger.jsonl",
            approvals_dir=tmp_path / "hub-approvals",
            registry_http_get=stub,
        )

        envelope = asyncio.run(_call(server, "registry_search", {"query": "docs"}))

        assert envelope["ok"] is True
        assert envelope["data"]["candidates"] == []
        events = recent_events(tmp_path / "hub-ledger.jsonl")
        assert any(e["payload"]["type"] == "registry.search" for e in events)

    def test_approval_status_is_scoped_to_caller_profile(self, tmp_path):
        # SF10: approval_status must not leak another profile's records.
        from werktools.hub.approvals import request_approval

        approvals_dir = tmp_path / "hub-approvals"
        ledger = tmp_path / "hub-ledger.jsonl"
        request_approval(approvals_dir, ledger, "docs.lookup", "claude-reviewer", {})
        other = request_approval(approvals_dir, ledger, "github.create_pr", "codex-builder", {})

        server = self._server(tmp_path, profile_id="claude-reviewer")
        envelope = asyncio.run(_call(server, "approval_status", {}))

        assert envelope["ok"] is True
        rows = envelope["data"]["records"]
        assert rows and all(r["profile_id"] == "claude-reviewer" for r in rows)
        assert other.request_id not in {r["request_id"] for r in rows}

    def test_ledger_recent_tool_flags_tampered_chain(self, tmp_path):
        # MF12: the ledger_recent meta-tool must flag a forged chain so an
        # agent reading the ledger as evidence is not silently misled.
        from werktools.hub.ledger import record_event

        ledger = tmp_path / "hub-ledger.jsonl"
        record_event(ledger, "policy.explained", {"a": 1})
        record_event(ledger, "tool.search", {"b": 2})
        server = self._server(tmp_path, profile_id="claude-reviewer")

        clean = asyncio.run(_call(server, "ledger_recent", {}))
        assert clean["ok"] is True
        assert clean["data"]["chain_verified"] is True
        assert clean["data"]["chain_errors"] == 0

        lines = ledger.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[0])
        rec["payload"]["forged"] = True
        lines[0] = json.dumps(rec, ensure_ascii=False)
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

        forged = asyncio.run(_call(server, "ledger_recent", {}))
        assert forged["data"]["chain_verified"] is False
        assert forged["data"]["chain_errors"] >= 1
        assert forged["data"]["events"]  # events still returned, not hidden
        # only a bool + int are exposed, never verify_chain error strings
        assert isinstance(forged["data"]["chain_errors"], int)


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
def test_status_http_endpoint(tmp_path):
    import json as _json
    import urllib.error
    import urllib.request

    from werktools.hub.registry import default_config
    from werktools.hub.server import _start_status_server

    httpd = _start_status_server(0, default_config(), "claude-reviewer", None)
    try:
        port = httpd.server_address[1]
        assert httpd.server_address[0] == "127.0.0.1"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"] == "application/json"
            body = _json.loads(resp.read())
        assert body["hub_name"] == "werk-hub"
        # unknown path -> 404
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=5)
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        httpd.shutdown()


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed (werktools[server] required)")
def test_relay_exception_reason_is_redacted_in_ledger(tmp_path):
    """Exception messages containing token-like substrings must be masked before
    they are stored in the ledger (SF11: no secret in audit trail)."""
    import asyncio

    from werktools.hub.contracts import HubConfig
    from werktools.hub.ledger import recent_events
    from werktools.hub.server import build_hub_server

    ledger = tmp_path / "ledger.jsonl"
    approvals = tmp_path / "approvals"

    # A handler that raises with a token-looking string in its message.
    def _bad_handler(args):
        raise RuntimeError("upstream failure: Bearer sk-proj-ABCDEF1234567890abcdef")

    config = HubConfig.from_dict({
        "name": "test-hub",
        "profiles": [
            {
                "id": "test-profile",
                "permission_profile": "admin",
                "allowed_tools": ["leaky_tool"],
                "allowed_servers": [],
            }
        ],
        "tools": [
            {
                "id": "leaky_tool",
                "name": "leaky_tool",
                "description": "A tool whose handler leaks a bearer token in the exception.",
                "risk": "read",
                "read_only": True,
            }
        ],
        "servers": [],
    })

    server = build_hub_server(
        config,
        profile_id="test-profile",
        handlers={"leaky_tool": _bad_handler},
        ledger_path=ledger,
        approvals_dir=approvals,
    )

    from fastmcp import Client

    async def run():
        async with Client(server) as client:
            result = await client.call_tool("tool_call", {"tool_id": "leaky_tool", "args": {}})
            return result

    asyncio.run(run())

    events = recent_events(ledger, limit=20)
    failed = [e for e in events if e.get("payload", {}).get("type") == "tool.call.failed"]
    assert failed, "Expected a tool.call.failed ledger event"
    reason = failed[0]["payload"].get("reason", "")
    assert "ABCDEF1234567890abcdef" not in reason, (
        f"Raw bearer token leaked into ledger reason: {reason!r}"
    )
    # The reason should still contain something useful (not totally blank).
    assert reason


def test_status_server_rejects_non_loopback_host(tmp_path):
    """_start_status_server must return 403 for a non-loopback Host header (Fix 18)."""
    import socket
    import struct

    from werktools.hub.registry import default_config
    from werktools.hub.server import _start_status_server

    config = default_config()
    httpd = _start_status_server(0, config, "claude-reviewer", pool=None)
    port = httpd.server_address[1]

    def _raw_get(host_header):
        raw = f"GET /status HTTP/1.1\r\nHost: {host_header}\r\nConnection: close\r\n\r\n"
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        try:
            sock.sendall(raw.encode("latin-1"))
            resp = sock.recv(8192).decode("latin-1")
        finally:
            sock.close()
        return int(resp.split(" ", 2)[1])

    try:
        # Loopback Host must be allowed
        assert _raw_get(f"127.0.0.1:{port}") == 200
        # Non-loopback Host must be rejected
        assert _raw_get("attacker.example") == 403
        assert _raw_get("192.168.1.1") == 403
    finally:
        httpd.shutdown()
