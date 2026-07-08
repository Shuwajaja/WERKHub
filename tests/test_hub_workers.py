import json
from pathlib import Path

import pytest

from werktools.hub.workers import (
    WorkerManifest,
    _env_key_for,
    _redact_prompt,
    _summarise_response,
    check_budget,
    dispatch_worker,
    get_worker,
    load_workers,
    report_workers,
)
from werktools.tools.cost import record_cost

CASSETTE = Path(__file__).parent / "cassettes" / "workers_policy.json"


def _worker(**over):
    base = {
        "id": "reviewer",
        "provider": "openrouter",
        "allowed_models": ["deepseek/deepseek-chat"],
        "max_cost_usd": "5.00",
        "max_tokens": 1024,
    }
    base.update(over)
    return WorkerManifest.from_dict(base)


def test_manifest_round_trip_and_defaults():
    w = WorkerManifest.from_dict({"id": "x"})
    assert w.provider == "openrouter"
    assert w.enabled is True
    assert WorkerManifest.from_dict(w.to_dict()) == w


def test_load_from_dict_and_file(tmp_path):
    assert [w.id for w in load_workers({"workers": [{"id": "a"}, {"id": "b"}]})] == ["a", "b"]
    path = tmp_path / "workers.json"
    path.write_text(json.dumps({"workers": [{"id": "c"}]}), encoding="utf-8")
    assert [w.id for w in load_workers(path)] == ["c"]
    assert load_workers(tmp_path / "missing.json") == []


def test_get_worker():
    workers = [_worker(id="a"), _worker(id="b")]
    assert get_worker(workers, "b").id == "b"
    assert get_worker(workers, "z") is None


def test_check_budget_allow_under_over_unknown(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    cost = tmp_path / "hub-cost.jsonl"
    worker = _worker(max_cost_usd="5.00")

    assert check_budget(worker, ledger).decision == "allow"  # empty

    record_cost(cost, mission="reviewer", task="t", tool="model_worker_call", model="m", amount="2.00")
    record_cost(cost, mission="other", task="t", tool="model_worker_call", model="m", amount="99.00")
    # only the worker's own mission counts
    assert check_budget(worker, ledger).decision == "allow"

    record_cost(cost, mission="reviewer", task="t", tool="model_worker_call", model="m", amount="4.00")
    assert check_budget(worker, ledger).decision == "deny"


def test_check_budget_unknown_amount_fails_closed(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    cost = tmp_path / "hub-cost.jsonl"
    record_cost(cost, mission="reviewer", task="t", tool="model_worker_call", model="m", amount=None)

    assert check_budget(_worker(), ledger).decision == "unknown"


def test_env_key_mapping():
    assert _env_key_for("openrouter") == "OPENROUTER_API_KEY"
    assert _env_key_for("anthropic") == "ANTHROPIC_API_KEY"
    with pytest.raises(ValueError):
        _env_key_for("mystery")


def test_redact_and_summarise():
    assert "[redacted]" in _redact_prompt("api_key: sk-123\nhello", "redacted")
    assert _redact_prompt("x", "none") == ""
    assert _summarise_response("a  b   c", "summary") == "a b c"
    assert _summarise_response("x", "none") == ""


def test_dispatch_denied_model_not_in_allowlist(tmp_path):
    res = dispatch_worker(_worker(), "gpt-9", "hi", 100, tmp_path / "ledger.jsonl", _environ={"OPENROUTER_API_KEY": "k"})
    assert res.ok is False
    assert "allowlist" in res.reason


def test_dispatch_denied_disabled(tmp_path):
    res = dispatch_worker(_worker(enabled=False), "deepseek/deepseek-chat", "hi", 100, tmp_path / "l.jsonl", _environ={"OPENROUTER_API_KEY": "k"})
    assert res.ok is False
    assert "disabled" in res.reason


def test_dispatch_missing_env_key_raises(tmp_path):
    with pytest.raises(KeyError):
        dispatch_worker(
            _worker(), "deepseek/deepseek-chat", "hi", 100, tmp_path / "l.jsonl",
            _http_call=lambda *a, **k: {"text": "x"}, _environ={},
        )


def test_dispatch_records_cost_and_trace_on_success(tmp_path):
    ledger = tmp_path / "ledger.jsonl"

    def fake_http(provider, model, prompt, max_tokens, api_key):
        assert api_key == "secret-key"
        return {"text": "looks good", "cost_usd": "0.01", "tokens": 42}

    res = dispatch_worker(
        _worker(), "deepseek/deepseek-chat", "review this", 100, ledger,
        _http_call=fake_http, _environ={"OPENROUTER_API_KEY": "secret-key"},
    )

    assert res.ok is True
    assert res.summary == "looks good"
    cost_text = (tmp_path / "hub-cost.jsonl").read_text(encoding="utf-8")
    trace_text = (tmp_path / "hub-trace.jsonl").read_text(encoding="utf-8")
    assert "model_worker_call" in cost_text
    assert "model_worker.call.completed" in trace_text
    # the api key never appears in any artifact
    assert "secret-key" not in cost_text
    assert "secret-key" not in trace_text


def test_dispatch_absent_provider_cost_is_unknown_not_zero(tmp_path):
    """A provider that omits total_cost must record UNKNOWN spend (fail-closed),
    not a silent $0.00 that would bypass the budget on the next call."""
    ledger = tmp_path / "ledger.jsonl"

    def fake_http_no_cost(provider, model, prompt, max_tokens, api_key):
        # provider response without a cost_usd field at all
        return {"text": "done", "tokens": 7}

    res = dispatch_worker(
        _worker(), "deepseek/deepseek-chat", "hi", 100, ledger,
        _http_call=fake_http_no_cost, _environ={"OPENROUTER_API_KEY": "k"},
    )

    # the call succeeds but its cost is honestly reported as unknown
    assert res.ok is True
    assert res.cost_usd == "unknown"
    # the recorded cost event carries a null amount (not "0.00")
    cost_text = (tmp_path / "hub-cost.jsonl").read_text(encoding="utf-8")
    assert '"amount": null' in cost_text
    assert "0.00" not in cost_text
    # and that unknown spend makes the NEXT budget check fail closed
    assert check_budget(_worker(), ledger).decision == "unknown"


def test_report_workers(tmp_path):
    rows = report_workers([_worker()], tmp_path / "ledger.jsonl")
    assert rows[0]["worker"]["id"] == "reviewer"
    assert rows[0]["budget"]["decision"] == "allow"


def test_cassette_zero_failures():
    entries = json.loads(CASSETTE.read_text(encoding="utf-8"))
    assert {e["response"]["decision"] for e in entries} == {"allow", "deny", "unknown"}


# ---------------------------------------------------------------------------
# Regression tests: _real_http_call error handling + load_workers resilience
# ---------------------------------------------------------------------------

def test_real_http_call_http_error_raises_status_code_only(monkeypatch):
    # When the provider returns an HTTP error, _real_http_call must raise
    # RuntimeError with only the status code — chain suppressed (__cause__ is None)
    # and no API key in the message.
    import httpx

    from werktools.hub.workers import _real_http_call

    class FakeResponse:
        def raise_for_status(self):
            req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
            resp = httpx.Response(401, request=req)
            raise httpx.HTTPStatusError("401", request=req, response=resp)

        def json(self):
            return {"choices": [{"message": {"content": "should not reach here"}}]}

    monkeypatch.setattr("httpx.post", lambda *a, **k: FakeResponse())

    with pytest.raises(RuntimeError, match="HTTP 401") as exc_info:
        _real_http_call("openrouter", "some-model", "prompt", 100, "fake-key")

    # Chain must be suppressed: __cause__ is None (from None)
    assert exc_info.value.__cause__ is None
    # The API key must never appear in the error message
    assert "fake-key" not in str(exc_info.value)


def test_real_http_call_empty_choices_raises(monkeypatch):
    # When the provider returns an empty choices list, _real_http_call must raise
    # RuntimeError('provider returned empty choices').
    from werktools.hub.workers import _real_http_call

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": []}

    monkeypatch.setattr("httpx.post", lambda *a, **k: FakeResponse())

    with pytest.raises(RuntimeError, match="empty choices"):
        _real_http_call("openrouter", "some-model", "prompt", 100, "key")


def test_real_http_call_missing_content_raises(monkeypatch):
    # When choices[0].message.content is None, _real_http_call must raise
    # RuntimeError('provider response missing choices[0].message.content').
    from werktools.hub.workers import _real_http_call

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": None}}]}

    monkeypatch.setattr("httpx.post", lambda *a, **k: FakeResponse())

    with pytest.raises(RuntimeError, match=r"missing choices\[0\]\.message\.content"):
        _real_http_call("openrouter", "some-model", "prompt", 100, "key")


def test_load_workers_corrupt_json_warns_and_returns_empty(tmp_path):
    # load_workers must emit a UserWarning and return [] for a corrupt JSON file.
    path = tmp_path / "workers.json"
    path.write_text("{corrupt", encoding="utf-8")

    with pytest.warns(UserWarning, match="corrupt"):
        result = load_workers(path)

    assert result == []


def test_cli_models_list_and_report(tmp_path, capsys):
    from werktools.cli import main
    from werktools.hub.registry import default_config

    workers = tmp_path / "workers.json"
    workers.write_text(json.dumps({"workers": [{"id": "reviewer", "allowed_models": ["m"]}]}), encoding="utf-8")
    config = tmp_path / "hub.json"
    body = default_config().to_dict()
    body["ledger_path"] = str(tmp_path / "hub-ledger.jsonl")
    config.write_text(json.dumps(body), encoding="utf-8")

    assert main(["models", "list", "--workers", str(workers)]) == 0
    assert "reviewer" in capsys.readouterr().out

    assert main(["--config", str(config), "models", "report", "--workers", str(workers)]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)[0]["worker"]["id"] == "reviewer"


def test_dispatch_pre_call_trace_oserror_degrades_not_aborts(tmp_path, monkeypatch):
    # When append_event raises OSError for the pre-call trace write, the worker
    # call must still proceed and a UserWarning must be emitted.  The degrade-not-
    # abort contract is: trace failure is never fatal to the actual dispatch.
    import warnings as _warnings

    import werktools.hub.workers as _workers_mod

    call_count = {"n": 0}
    original_append = _workers_mod.append_event

    def _failing_append(path, event_type, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Fail only the first call (pre-call trace write)
            raise OSError("simulated trace failure")
        return original_append(path, event_type, **kwargs)

    monkeypatch.setattr(_workers_mod, "append_event", _failing_append)

    def fake_http(provider, model, prompt, max_tokens, api_key):
        return {"text": "ok", "cost_usd": "0.00", "tokens": 0}

    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        res = dispatch_worker(
            _worker(), "deepseek/deepseek-chat", "hello", 100,
            tmp_path / "ledger.jsonl",
            _http_call=fake_http,
            _environ={"OPENROUTER_API_KEY": "k"},
        )

    assert res.ok is True, f"Expected ok=True but got: {res.reason}"
    assert any("pre-call trace write failed" in str(w.message) for w in caught), (
        "Expected a UserWarning about pre-call trace write failure"
    )
