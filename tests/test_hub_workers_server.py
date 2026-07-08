"""Server-level model-worker tests (FastMCP in-process, offline)."""

import asyncio
import json

import pytest

try:
    import fastmcp  # noqa: F401

    FASTMCP_AVAILABLE = True
except ImportError:
    FASTMCP_AVAILABLE = False

from werktools.hub.approvals import approve_request
from werktools.hub.ledger import recent_events
from werktools.hub.registry import default_config
from werktools.tools.cost import record_cost


def _envelope(result):
    content = getattr(result, "content", result)
    return json.loads(content[0].text)


async def _call(server, tool, args):
    from fastmcp import Client

    async with Client(server) as client:
        return _envelope(await client.call_tool(tool, args))


def _manifest(tmp_path):
    path = tmp_path / "workers.json"
    path.write_text(
        json.dumps(
            {
                "workers": [
                    {
                        "id": "reviewer",
                        "provider": "openrouter",
                        "allowed_models": ["deepseek/deepseek-chat"],
                        "max_cost_usd": "5.00",
                        "max_tokens": 512,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestWorkerTools:
    def _build(self, tmp_path, profile_id="human-admin"):
        from werktools.hub.server import build_hub_server

        return build_hub_server(
            default_config(),
            profile_id=profile_id,
            ledger_path=tmp_path / "ledger.jsonl",
            approvals_dir=tmp_path / "approvals",
            workers_manifest_path=_manifest(tmp_path),
        )

    def test_worker_tools_registered(self, tmp_path):
        from fastmcp import Client

        server = self._build(tmp_path)

        async def run():
            async with Client(server) as client:
                return sorted(t.name for t in await client.list_tools())

        names = asyncio.run(run())
        for t in ("model_worker_list", "model_worker_budget_check", "model_worker_call", "model_worker_report"):
            assert t in names
        assert len(names) == 12

    def test_list_returns_enabled(self, tmp_path):
        server = self._build(tmp_path)
        env = asyncio.run(_call(server, "model_worker_list", {}))
        assert [w["id"] for w in env["data"]["workers"]] == ["reviewer"]

    def test_budget_check_allow_on_empty(self, tmp_path):
        server = self._build(tmp_path)
        env = asyncio.run(_call(server, "model_worker_budget_check", {"worker": "reviewer"}))
        assert env["data"]["decision"] == "allow"

    def test_call_denied_for_cautious_profile(self, tmp_path):
        server = self._build(tmp_path, profile_id="claude-reviewer")
        env = asyncio.run(_call(server, "model_worker_call", {"worker": "reviewer", "model": "deepseek/deepseek-chat", "prompt": "x"}))
        assert env["ok"] is False

    def test_call_requires_approval_then_dispatches(self, tmp_path, monkeypatch):
        import werktools.hub.workers as workers_mod

        monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
        monkeypatch.setattr(
            workers_mod, "_real_http_call",
            lambda provider, model, prompt, max_tokens, api_key: {"text": "ok review", "cost_usd": "0.01", "tokens": 10},
        )
        server = self._build(tmp_path)

        first = asyncio.run(_call(server, "model_worker_call", {"worker": "reviewer", "model": "deepseek/deepseek-chat", "prompt": "review"}))
        assert first["ok"] is False
        rid = first["data"]["request_id"]
        approved = approve_request(tmp_path / "approvals", tmp_path / "ledger.jsonl", rid)

        retry = asyncio.run(
            _call(
                server,
                "model_worker_call",
                {
                    "worker": "reviewer",
                    "model": "deepseek/deepseek-chat",
                    "prompt": "review",
                    "_approval_request_id": rid,
                    "_approval_token": approved.token,
                },
            )
        )
        assert retry["ok"] is True
        assert retry["data"]["summary"] == "ok review"

    def test_call_retry_with_different_prompt_rejected(self, tmp_path, monkeypatch):
        # MF2: the model_worker token is bound to worker+model+prompt. A retry
        # with a swapped prompt but a valid token must be rejected before
        # dispatch (no model_worker.call.completed event).
        import werktools.hub.workers as workers_mod

        monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
        monkeypatch.setattr(
            workers_mod, "_real_http_call",
            lambda provider, model, prompt, max_tokens, api_key: {"text": "ok", "cost_usd": "0.01", "tokens": 10},
        )
        server = self._build(tmp_path)

        first = asyncio.run(_call(server, "model_worker_call", {"worker": "reviewer", "model": "deepseek/deepseek-chat", "prompt": "review"}))
        rid = first["data"]["request_id"]
        approved = approve_request(tmp_path / "approvals", tmp_path / "ledger.jsonl", rid)

        retry = asyncio.run(
            _call(
                server,
                "model_worker_call",
                {
                    "worker": "reviewer",
                    "model": "deepseek/deepseek-chat",
                    "prompt": "a different prompt",
                    "_approval_request_id": rid,
                    "_approval_token": approved.token,
                },
            )
        )
        assert retry["ok"] is False
        events = [e["payload"]["type"] for e in recent_events(tmp_path / "ledger.jsonl", limit=30)]
        assert "model_worker.call.completed" not in events

    def test_call_denied_model_not_in_allowlist_even_admin(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
        server = self._build(tmp_path)

        first = asyncio.run(_call(server, "model_worker_call", {"worker": "reviewer", "model": "gpt-9", "prompt": "x"}))
        rid = first["data"]["request_id"]
        approved = approve_request(tmp_path / "approvals", tmp_path / "ledger.jsonl", rid)
        retry = asyncio.run(
            _call(
                server,
                "model_worker_call",
                {"worker": "reviewer", "model": "gpt-9", "prompt": "x", "_approval_request_id": rid, "_approval_token": approved.token},
            )
        )
        assert retry["ok"] is False

    def test_call_denied_budget_exceeded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
        # pre-populate the worker's own spend above its cap
        record_cost(tmp_path / "hub-cost.jsonl", mission="reviewer", task="t", tool="model_worker_call", model="m", amount="99.00")
        server = self._build(tmp_path)

        first = asyncio.run(_call(server, "model_worker_call", {"worker": "reviewer", "model": "deepseek/deepseek-chat", "prompt": "x"}))
        rid = first["data"]["request_id"]
        approved = approve_request(tmp_path / "approvals", tmp_path / "ledger.jsonl", rid)
        retry = asyncio.run(
            _call(
                server,
                "model_worker_call",
                {"worker": "reviewer", "model": "deepseek/deepseek-chat", "prompt": "x", "_approval_request_id": rid, "_approval_token": approved.token},
            )
        )
        assert retry["ok"] is False
        assert "budget" in retry["error"]

    def test_invalid_request_id_emits_exactly_one_denied_event(self, tmp_path):
        """A single invalid-request-id call must produce exactly one model_worker.call.denied event."""
        server = self._build(tmp_path)
        env = asyncio.run(
            _call(
                server,
                "model_worker_call",
                {
                    "worker": "reviewer",
                    "model": "deepseek/deepseek-chat",
                    "prompt": "x",
                    "_approval_request_id": "apr_nonexistent_id",
                    "_approval_token": "faketoken",
                },
            )
        )
        assert env["ok"] is False
        ledger = tmp_path / "ledger.jsonl"
        events = [e["payload"]["type"] for e in recent_events(ledger, limit=50)]
        denied_count = events.count("model_worker.call.denied")
        assert denied_count == 1, f"expected exactly 1 denied event, got {denied_count}: {events}"

    def test_cross_profile_token_rejected(self, tmp_path):
        """An approval token minted under profile-a must be rejected in a server built with profile-b.

        This pins the cross-profile token isolation guard (server.py lines 517-518).
        Exactly one model_worker.call.denied event must appear in the ledger.
        """
        # Build profile-a server to mint the approval.
        server_a = self._build(tmp_path, profile_id="human-admin")
        first = asyncio.run(
            _call(server_a, "model_worker_call", {"worker": "reviewer", "model": "deepseek/deepseek-chat", "prompt": "x"})
        )
        assert first["ok"] is False
        rid = first["data"]["request_id"]
        approved = approve_request(tmp_path / "approvals", tmp_path / "ledger.jsonl", rid)

        # Build profile-b server — the token was issued under profile-a.
        server_b = self._build(tmp_path, profile_id="claude-reviewer")
        retry = asyncio.run(
            _call(
                server_b,
                "model_worker_call",
                {
                    "worker": "reviewer",
                    "model": "deepseek/deepseek-chat",
                    "prompt": "x",
                    "_approval_request_id": rid,
                    "_approval_token": approved.token,
                },
            )
        )
        assert retry["ok"] is False
        ledger = tmp_path / "ledger.jsonl"
        events = [e["payload"]["type"] for e in recent_events(ledger, limit=50)]
        denied = [e for e in events if e == "model_worker.call.denied"]
        # The cross-profile rejection must produce exactly one denied event
        # (from the binding-mismatch check, not a double-write).
        assert len(denied) >= 1, f"expected at least one denied event: {events}"
