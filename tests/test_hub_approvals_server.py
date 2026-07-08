"""Server-level execute-after-approval tests (FastMCP in-process)."""

import asyncio
import json

import pytest

try:
    import fastmcp  # noqa: F401

    FASTMCP_AVAILABLE = True
except ImportError:
    FASTMCP_AVAILABLE = False

from werktools.hub.approvals import approve_request, deny_request, list_records
from werktools.hub.ledger import recent_events
from werktools.hub.registry import default_config, load_config


def _envelope(result):
    content = getattr(result, "content", result)
    return json.loads(content[0].text)


async def _call(server, tool, args):
    from fastmcp import Client

    async with Client(server) as client:
        return _envelope(await client.call_tool(tool, args))


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestExecuteAfterApproval:
    def _build(self, tmp_path, calls):
        from werktools.hub.server import build_hub_server

        return build_hub_server(
            default_config(),
            profile_id="codex-builder",
            handlers={"github.create_pr": lambda a: calls.append(a) or {"pr": 1}},
            ledger_path=tmp_path / "ledger.jsonl",
            approvals_dir=tmp_path / "approvals",
        )

    def test_first_call_returns_request_id(self, tmp_path):
        calls = []
        server = self._build(tmp_path, calls)

        env = asyncio.run(_call(server, "tool_call", {"tool_id": "github.create_pr", "args": {}}))

        assert env["ok"] is False
        assert env["data"]["request_id"].startswith("apr_")
        assert calls == []

    def test_approve_then_retry_executes_once(self, tmp_path):
        calls = []
        server = self._build(tmp_path, calls)
        approvals = tmp_path / "approvals"
        ledger = tmp_path / "ledger.jsonl"

        first = asyncio.run(_call(server, "tool_call", {"tool_id": "github.create_pr", "args": {}}))
        rid = first["data"]["request_id"]
        approved = approve_request(approvals, ledger, rid)

        retry = asyncio.run(
            _call(
                server,
                "tool_call",
                {"tool_id": "github.create_pr", "args": {}, "_approval_request_id": rid, "_approval_token": approved.token},
            )
        )

        assert retry["ok"] is True
        assert retry["data"]["result"] == {"pr": 1}
        assert len(calls) == 1
        assert list_records(approvals, status="consumed")

    def test_double_retry_is_blocked(self, tmp_path):
        calls = []
        server = self._build(tmp_path, calls)
        approvals = tmp_path / "approvals"
        ledger = tmp_path / "ledger.jsonl"

        first = asyncio.run(_call(server, "tool_call", {"tool_id": "github.create_pr", "args": {}}))
        rid = first["data"]["request_id"]
        approved = approve_request(approvals, ledger, rid)
        payload = {"tool_id": "github.create_pr", "args": {}, "_approval_request_id": rid, "_approval_token": approved.token}

        asyncio.run(_call(server, "tool_call", payload))
        second = asyncio.run(_call(server, "tool_call", payload))

        assert second["ok"] is False
        assert len(calls) == 1

    def test_wrong_token_rejected_handler_not_called(self, tmp_path):
        calls = []
        server = self._build(tmp_path, calls)
        approvals = tmp_path / "approvals"
        ledger = tmp_path / "ledger.jsonl"

        first = asyncio.run(_call(server, "tool_call", {"tool_id": "github.create_pr", "args": {}}))
        rid = first["data"]["request_id"]
        approve_request(approvals, ledger, rid)

        env = asyncio.run(
            _call(
                server,
                "tool_call",
                {"tool_id": "github.create_pr", "args": {}, "_approval_request_id": rid, "_approval_token": "0" * 32},
            )
        )

        assert env["ok"] is False
        assert calls == []

    def test_retry_after_deny_rejected(self, tmp_path):
        calls = []
        server = self._build(tmp_path, calls)
        approvals = tmp_path / "approvals"
        ledger = tmp_path / "ledger.jsonl"

        first = asyncio.run(_call(server, "tool_call", {"tool_id": "github.create_pr", "args": {}}))
        rid = first["data"]["request_id"]
        # capture the minted token before deny blanks it
        token = list_records(approvals)[0].token
        deny_request(approvals, ledger, rid)

        env = asyncio.run(
            _call(
                server,
                "tool_call",
                {"tool_id": "github.create_pr", "args": {}, "_approval_request_id": rid, "_approval_token": token},
            )
        )

        assert env["ok"] is False
        assert calls == []

    def test_approval_status_lists_pending(self, tmp_path):
        server = self._build(tmp_path, [])

        first = asyncio.run(_call(server, "tool_call", {"tool_id": "github.create_pr", "args": {}}))
        rid = first["data"]["request_id"]

        status = asyncio.run(_call(server, "approval_status", {}))
        assert any(p["request_id"] == rid for p in status["data"]["pending"])

        by_id = asyncio.run(_call(server, "approval_status", {"request_id": rid}))
        assert len(by_id["data"]["records"]) == 1

    def test_retry_with_different_args_rejected(self, tmp_path):
        # MF2: a token is bound to the args it approved. A retry with a valid
        # token but SWAPPED args must be rejected and the handler not run.
        calls = []
        server = self._build(tmp_path, calls)
        approvals = tmp_path / "approvals"
        ledger = tmp_path / "ledger.jsonl"

        first = asyncio.run(_call(server, "tool_call", {"tool_id": "github.create_pr", "args": {"title": "a"}}))
        rid = first["data"]["request_id"]
        approved = approve_request(approvals, ledger, rid)

        env = asyncio.run(
            _call(
                server,
                "tool_call",
                {
                    "tool_id": "github.create_pr",
                    "args": {"title": "b"},
                    "_approval_request_id": rid,
                    "_approval_token": approved.token,
                },
            )
        )

        assert env["ok"] is False
        assert calls == []


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
def test_relayed_write_executes_after_approval(tmp_path):
    from fastmcp import FastMCP

    from werktools.hub.relay import discover_tools  # noqa: F401
    from werktools.hub.server import build_hub_server

    downstream = FastMCP(name="docs")

    @downstream.tool()
    def write_note(text: str) -> dict:
        return {"wrote": text}

    config = load_config(
        {
            "name": "werk-hub",
            "default_profile": "builder",
            "profiles": [
                {
                    "id": "builder",
                    "permission_profile": "balanced",
                    "visible_tags": ["read", "write", "external"],
                    "allowed_servers": ["docs"],
                }
            ],
            "tools": [],
        }
    )
    server = build_hub_server(
        config,
        profile_id="builder",
        ledger_path=tmp_path / "ledger.jsonl",
        approvals_dir=tmp_path / "approvals",
        relay_targets={"docs": downstream},
    )

    first = asyncio.run(_call(server, "tool_call", {"tool_id": "docs.write_note", "args": {"text": "hi"}}))
    assert first["ok"] is False
    rid = first["data"]["request_id"]
    approved = approve_request(tmp_path / "approvals", tmp_path / "ledger.jsonl", rid)

    retry = asyncio.run(
        _call(
            server,
            "tool_call",
            {
                "tool_id": "docs.write_note",
                "args": {"text": "hi"},
                "_approval_request_id": rid,
                "_approval_token": approved.token,
            },
        )
    )

    assert retry["ok"] is True


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
def test_admin_relayed_write_returns_approval_required(tmp_path):
    # MF9: under an admin profile enforce() returns a plain allow for a relayed
    # write tool, which previously dead-ended ("requires an approval token"
    # with no request ever minted). Now it mints an approval and the gate fires.
    from fastmcp import FastMCP

    from werktools.hub.server import build_hub_server

    downstream = FastMCP(name="docs")

    @downstream.tool()
    def write_note(text: str) -> dict:
        return {"wrote": text}

    config = load_config(
        {
            "name": "werk-hub",
            "default_profile": "admin-tester",
            "profiles": [
                {
                    "id": "admin-tester",
                    "permission_profile": "admin",
                    "visible_tags": ["read", "write", "external"],
                    "allowed_servers": ["docs"],
                }
            ],
            "tools": [],
        }
    )
    server = build_hub_server(
        config,
        profile_id="admin-tester",
        ledger_path=tmp_path / "ledger.jsonl",
        approvals_dir=tmp_path / "approvals",
        relay_targets={"docs": downstream},
    )

    first = asyncio.run(_call(server, "tool_call", {"tool_id": "docs.write_note", "args": {"text": "hi"}}))
    assert first["ok"] is False
    rid = first["data"]["request_id"]
    assert rid.startswith("apr_")  # an approval was minted, not a dead-end error
    events = recent_events(tmp_path / "ledger.jsonl")
    assert any(
        e["payload"]["type"] == "tool.call.approval_required" and e["payload"].get("tool") == "docs.write_note"
        for e in events
    )

    approved = approve_request(tmp_path / "approvals", tmp_path / "ledger.jsonl", rid)
    retry = asyncio.run(
        _call(
            server,
            "tool_call",
            {
                "tool_id": "docs.write_note",
                "args": {"text": "hi"},
                "_approval_request_id": rid,
                "_approval_token": approved.token,
            },
        )
    )
    assert retry["ok"] is True


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
def test_request_id_without_token_ledgers_denied(tmp_path):
    """_tool_call must emit a tool.call.denied event when _approval_request_id is
    supplied without _approval_token (Fix 7 — symmetric ledger path)."""
    from werktools.hub.server import build_hub_server

    calls = []
    server = build_hub_server(
        load_config(
            {
                "name": "werk-hub",
                "default_profile": "codex-builder",
                "profiles": [
                    {"id": "codex-builder", "permission_profile": "balanced", "visible_tags": ["read", "write"]}
                ],
                "tools": [{"id": "github.create_pr", "name": "github.create_pr", "risk": "write"}],
                "servers": [],
            }
        ),
        profile_id="codex-builder",
        handlers={"github.create_pr": lambda a: calls.append(a) or {"pr": 1}},
        ledger_path=tmp_path / "ledger.jsonl",
        approvals_dir=tmp_path / "approvals",
    )

    result = asyncio.run(
        _call(
            server,
            "tool_call",
            {"tool_id": "github.create_pr", "args": {}, "_approval_request_id": "apr_aabbccddeeff"},
        )
    )

    assert result["ok"] is False
    events = recent_events(tmp_path / "ledger.jsonl")
    denied_events = [
        e for e in events
        if e["payload"]["type"] == "tool.call.denied"
        and "_approval_request_id" in e["payload"].get("reason", "")
    ]
    assert denied_events, "expected a tool.call.denied event for missing _approval_token"
    assert calls == []
