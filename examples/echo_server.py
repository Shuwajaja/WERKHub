"""End-to-end demo of the FastMCP helper."""

from __future__ import annotations

import asyncio
import json
import pathlib
import tempfile

from fastmcp import Client

from werktools.classify import classify_tool
from werktools.envelope import ok
from werktools.ledger import append, read
from werktools.policy import BRIDGE_TOOLS, PolicySnapshot
from werktools.profile import load_profile, to_card
from werktools.server import make_server, register


def _content_items(result):
    return getattr(result, "content", result)


def build_demo_server():
    snapshot = PolicySnapshot(
        visible=("echo",),
        allowed=("echo",),
        blocked=("secret",),
        hidden=(),
        risk="low",
        counts={"visible": 1, "allowed": 1, "blocked": 1, "hidden": 0},
        decisions={"echo": "allow", "secret": "deny"},
    )

    server = make_server("demo-echo-server", version="0.1")

    def echo_handler(args: dict) -> dict:
        return ok("echo.ping", data={"echo": args.get("msg", ""), "ts": "demo"})

    def secret_handler(args: dict) -> dict:
        return ok("secret.read", data={"value": "TOP SECRET"})

    register(
        server,
        "echo",
        "Echo back the msg.",
        {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
        echo_handler,
        snapshot=snapshot,
    )

    register(
        server,
        "secret",
        "Read a secret (blocked by policy).",
        {"type": "object", "properties": {}, "required": []},
        secret_handler,
        snapshot=snapshot,
    )

    return server


async def run_demo() -> None:
    server = build_demo_server()

    async with Client(server) as client:
        result = await client.call_tool("echo", {"msg": "hello world"})
        envelope = json.loads(_content_items(result)[0].text)
        print("[demo] echo call result:", envelope)
        assert envelope["ok"] is True
        assert envelope["data"]["echo"] == "hello world"

        result = await client.call_tool("secret", {})
        envelope = json.loads(_content_items(result)[0].text)
        print("[demo] blocked call result:", envelope)
        assert envelope["ok"] is False
        assert "deny" in envelope["error"].lower()

    env = ok("demo.check", data={"status": "green"})
    assert env["ok"] is True

    profile = load_profile({"id": "demo-agent", "role": "worker", "skills": ["echo"]})
    card = to_card(profile)
    assert card["schema"] == "werk.agent-card/1"

    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = pathlib.Path(tmp) / "demo.jsonl"
        append(ledger_path, {"type": "demo.event", "value": 1})
        events = read(ledger_path)
        assert len(events) == 1

    risk = classify_tool(
        {
            "name": "run_shell",
            "description": "Execute shell commands",
            "inputSchema": {},
        }
    )
    assert risk["risk"] == "critical"
    assert "tool_search" in BRIDGE_TOOLS

    print("[demo] all assertions passed")


if __name__ == "__main__":
    asyncio.run(run_demo())
