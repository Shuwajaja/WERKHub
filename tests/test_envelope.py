"""Offline, dep-free unit tests for werktools.envelope."""

import json

from werktools.envelope import err, ok, to_mcp_text


class TestOk:
    def test_returns_ok_true(self):
        result = ok("swarm.research")
        assert result["ok"] is True

    def test_command_preserved(self):
        result = ok("swarm.research")
        assert result["command"] == "swarm.research"

    def test_data_defaults_to_empty_dict(self):
        result = ok("swarm.research")
        assert result["data"] == {}

    def test_data_explicit_none_becomes_empty_dict(self):
        result = ok("swarm.research", data=None)
        assert result["data"] == {}

    def test_data_dict_preserved(self):
        payload = {"count": 3, "items": ["a", "b", "c"]}
        result = ok("swarm.research", data=payload)
        assert result["data"] == payload

    def test_error_is_none(self):
        result = ok("swarm.research")
        assert result["error"] is None

    def test_no_missing_fields(self):
        result = ok("x")
        assert set(result.keys()) == {"ok", "command", "data", "error"}

    def test_ok_is_bool_not_truthy(self):
        result = ok("x")
        assert type(result["ok"]) is bool


class TestErr:
    def test_returns_ok_false(self):
        result = err("swarm.research", "something went wrong")
        assert result["ok"] is False

    def test_command_preserved(self):
        result = err("swarm.research", "boom")
        assert result["command"] == "swarm.research"

    def test_error_is_the_string(self):
        result = err("swarm.research", "boom")
        assert result["error"] == "boom"

    def test_error_is_never_none(self):
        result = err("swarm.research", "e")
        assert result["error"] is not None

    def test_data_defaults_to_none(self):
        result = err("swarm.research", "boom")
        assert result["data"] is None

    def test_data_explicit_dict_preserved(self):
        extra = {"code": 42}
        result = err("swarm.research", "boom", data=extra)
        assert result["data"] == extra

    def test_no_missing_fields(self):
        result = err("x", "e")
        assert set(result.keys()) == {"ok", "command", "data", "error"}

    def test_ok_is_bool_not_falsy(self):
        result = err("x", "e")
        assert type(result["ok"]) is bool

    def test_empty_error_string_still_stored(self):
        result = err("x", "")
        assert result["error"] == ""


class TestToMcpText:
    def test_ok_envelope_iserror_false(self):
        envelope = ok("swarm.research", data={"k": 1})
        result = to_mcp_text(envelope)
        assert result["isError"] is False

    def test_err_envelope_iserror_true(self):
        envelope = err("swarm.research", "oops")
        result = to_mcp_text(envelope)
        assert result["isError"] is True

    def test_content_is_list_with_one_text_item(self):
        envelope = ok("x")
        result = to_mcp_text(envelope)
        assert isinstance(result["content"], list)
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"

    def test_content_text_is_json_serialised_envelope(self):
        envelope = ok("swarm.research", data={"n": 7})
        result = to_mcp_text(envelope)
        parsed = json.loads(result["content"][0]["text"])
        assert parsed == envelope

    def test_content_text_ascii_safe(self):
        envelope = ok("x", data={"msg": "cafe\u00e9"})
        result = to_mcp_text(envelope)
        text = result["content"][0]["text"]
        text.encode("ascii")

    def test_no_missing_fields(self):
        result = to_mcp_text(ok("x"))
        assert set(result.keys()) == {"content", "isError"}

    def test_missing_ok_key_treated_as_error(self):
        result = to_mcp_text({"command": "x", "data": None, "error": "bad"})
        assert result["isError"] is True

    def test_ok_false_explicit_iserror_true(self):
        envelope = {"ok": False, "command": "x", "data": None, "error": "e"}
        result = to_mcp_text(envelope)
        assert result["isError"] is True
