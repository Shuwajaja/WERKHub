"""Integration tests for werktools.server via FastMCP in-memory client."""

import json
import sys

import pytest

try:
    import fastmcp  # noqa: F401

    FASTMCP_AVAILABLE = True
except ImportError:
    FASTMCP_AVAILABLE = False


def _content_items(result):
    return getattr(result, "content", result)


def test_import_error_message_when_fastmcp_missing(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def blocking_import(name, *args, **kwargs):
        if name == "fastmcp":
            raise ImportError("No module named 'fastmcp'")
        return real_import(name, *args, **kwargs)

    sys.modules.pop("werktools.server", None)
    monkeypatch.setattr(builtins, "__import__", blocking_import)

    with pytest.raises(ImportError) as exc_info:
        import werktools.server  # noqa: F401

    assert "werktools[server]" in str(exc_info.value)
    sys.modules.pop("werktools.server", None)


@pytest.mark.skipif(
    not FASTMCP_AVAILABLE, reason="fastmcp not installed (werktools[server] required)"
)
class TestMakeServer:
    def test_returns_fastmcp_instance(self):
        from fastmcp import FastMCP

        from werktools.server import make_server

        server = make_server("test-server")
        assert isinstance(server, FastMCP)

    def test_custom_name_and_version(self):
        from werktools.server import make_server

        server = make_server("my-tool", version="1.2")
        assert server.name == "my-tool"


@pytest.mark.skipif(
    not FASTMCP_AVAILABLE, reason="fastmcp not installed (werktools[server] required)"
)
class TestRegisterEchoTool:
    def _make_echo_server(self):
        from werktools.server import make_server, register

        server = make_server("echo-server")
        schema = {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        }

        def echo_handler(args: dict) -> dict:
            return {
                "ok": True,
                "command": "echo.ping",
                "data": {"echo": args.get("msg")},
                "error": None,
            }

        register(server, "echo", "Echo the msg back.", schema, echo_handler)
        return server

    def test_tool_appears_in_list(self):
        import asyncio

        from fastmcp import Client

        server = self._make_echo_server()

        async def run():
            async with Client(server) as client:
                tools = await client.list_tools()
                names = [tool.name for tool in tools]
                assert "echo" in names

        asyncio.run(run())

    def test_call_returns_envelope_content(self):
        import asyncio

        from fastmcp import Client

        server = self._make_echo_server()

        async def run():
            async with Client(server) as client:
                result = await client.call_tool("echo", {"msg": "hello"})
                content = _content_items(result)
                assert content, "Expected at least one content item"
                envelope = json.loads(content[0].text)
                assert envelope["ok"] is True
                assert envelope["command"] == "echo.ping"
                assert envelope["data"]["echo"] == "hello"
                assert envelope["error"] is None

        asyncio.run(run())

    def test_non_serializable_result_becomes_err_envelope(self):
        import asyncio

        from fastmcp import Client

        from werktools.server import make_server, register

        server = make_server("weird-server")
        schema = {"type": "object", "properties": {}, "required": []}

        def weird_handler(args: dict) -> dict:
            return {"ok": True, "command": "weird", "data": {"obj": object()}, "error": None}

        register(server, "weird", "Returns a non-serializable result.", schema, weird_handler)

        async def run():
            async with Client(server) as client:
                result = await client.call_tool("weird", {})
                envelope = json.loads(_content_items(result)[0].text)
                assert envelope["ok"] is False
                assert "non-serializable" in envelope["error"]
                assert envelope["command"] == "weird"

        asyncio.run(run())

    def test_handler_exception_becomes_err_envelope(self):
        import asyncio

        from fastmcp import Client

        from werktools.server import make_server, register

        server = make_server("bomb-server")
        schema = {"type": "object", "properties": {}, "required": []}

        def bomb_handler(args: dict) -> dict:
            raise ValueError("intentional failure")

        register(server, "bomb", "Always explodes.", schema, bomb_handler)

        async def run():
            async with Client(server) as client:
                result = await client.call_tool("bomb", {})
                envelope = json.loads(_content_items(result)[0].text)
                assert envelope["ok"] is False
                assert "intentional failure" in envelope["error"]
                assert envelope["command"] == "bomb"

        asyncio.run(run())


@pytest.mark.skipif(
    not FASTMCP_AVAILABLE, reason="fastmcp not installed (werktools[server] required)"
)
class TestPolicyGate:
    def _make_gated_server(self, allowed_tool: str):
        from werktools.policy import PolicySnapshot
        from werktools.server import make_server, register

        snapshot = PolicySnapshot(
            visible=(allowed_tool,),
            allowed=(allowed_tool,),
            blocked=(),
            hidden=(),
            risk="low",
            counts={"visible": 1, "allowed": 1, "blocked": 0, "hidden": 0},
            decisions={allowed_tool: "allow", "denied_tool": "deny"},
        )

        server = make_server("gated-server")
        schema = {"type": "object", "properties": {}, "required": []}

        def noop_handler(args: dict) -> dict:
            return {"ok": True, "command": "noop", "data": {}, "error": None}

        register(server, allowed_tool, "Allowed.", schema, noop_handler, snapshot=snapshot)
        register(server, "denied_tool", "Denied.", schema, noop_handler, snapshot=snapshot)
        return server

    def test_allowed_tool_passes(self):
        import asyncio

        from fastmcp import Client

        server = self._make_gated_server("allowed_tool")

        async def run():
            async with Client(server) as client:
                result = await client.call_tool("allowed_tool", {})
                envelope = json.loads(_content_items(result)[0].text)
                assert envelope["ok"] is True

        asyncio.run(run())

    def test_denied_tool_returns_err_envelope(self):
        import asyncio

        from fastmcp import Client

        server = self._make_gated_server("allowed_tool")

        async def run():
            async with Client(server) as client:
                result = await client.call_tool("denied_tool", {})
                envelope = json.loads(_content_items(result)[0].text)
                assert envelope["ok"] is False
                assert "denied" in envelope["error"].lower() or (
                    "deny" in envelope["error"].lower()
                )

        asyncio.run(run())
