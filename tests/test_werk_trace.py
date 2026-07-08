import json

from werktools.cli import main
from werktools.ledger import GENESIS
from werktools.tools.trace import (
    append_event,
    export_trace,
    read_events,
    recent_events,
    verify_trace,
)


def test_append_event_redacts_secret_payload_keys(tmp_path):
    path = tmp_path / "trace.jsonl"

    event = append_event(
        path,
        "tool.call.completed",
        actor="codex",
        payload={"token": "secret", "nested": {"password": "hidden"}, "ok": True},
        created_at="2026-06-10T00:00:00Z",
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert event.event_type == "tool.call.completed"
    assert raw["payload"]["token"] == "[redacted]"
    assert raw["payload"]["nested"]["password"] == "[redacted]"
    assert raw["payload"]["ok"] is True
    assert raw["prev_hash"] == GENESIS
    assert raw["hash"]


def test_recent_events_returns_newest_limit(tmp_path):
    path = tmp_path / "trace.jsonl"
    append_event(path, "first", created_at="2026-06-10T00:00:00Z")
    append_event(path, "second", created_at="2026-06-10T00:00:01Z")

    events = recent_events(path, limit=1)

    assert [event.event_type for event in events] == ["second"]


def test_verify_trace_detects_tampering(tmp_path):
    path = tmp_path / "trace.jsonl"
    append_event(path, "tool.call.completed", payload={"status": "ok"})

    assert verify_trace(path).ok is True

    line = path.read_text(encoding="utf-8")
    path.write_text(line.replace('"ok"', '"changed"'), encoding="utf-8")

    result = verify_trace(path)
    assert result.ok is False
    assert "hash mismatch" in result.errors[0]


def test_read_events_skips_corrupt_lines(tmp_path):
    path = tmp_path / "trace.jsonl"
    append_event(path, "tool.call.completed")
    path.write_text("{corrupt\n" + path.read_text(encoding="utf-8") + '{"no_id": true}\n', encoding="utf-8")

    events = read_events(path)

    assert len(events) == 1
    assert events[0].event_type == "tool.call.completed"


def test_verify_trace_rejects_cleared_hashes(tmp_path):
    path = tmp_path / "trace.jsonl"
    append_event(path, "a", payload={"ok": True})
    append_event(path, "b", payload={"ok": True})

    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        raw["hash"] = ""
        lines.append(json.dumps(raw, sort_keys=True))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = verify_trace(path)

    assert result.ok is False
    assert result.errors


def test_verify_trace_rejects_fully_cleared_chain(tmp_path):
    path = tmp_path / "trace.jsonl"
    append_event(path, "a", payload={"ok": True})
    append_event(path, "b", payload={"ok": True})

    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        raw["hash"] = ""
        raw["prev_hash"] = ""
        raw["payload"] = {"ok": "TAMPERED"}
        lines.append(json.dumps(raw, sort_keys=True))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = verify_trace(path)

    assert result.ok is False
    assert "no hashed events" in result.errors[0]


def test_verify_trace_rejects_unhashed_event_after_chained(tmp_path):
    path = tmp_path / "trace.jsonl"
    append_event(path, "a")
    append_event(path, "b", hash_chain=False)

    result = verify_trace(path)

    assert result.ok is False
    assert "unhashed" in result.errors[0]


def test_append_links_over_unhashed_events(tmp_path):
    path = tmp_path / "trace.jsonl"
    first = append_event(path, "a")
    append_event(path, "b", hash_chain=False)
    third = append_event(path, "c")

    assert third.prev_hash == first.hash


def test_export_trace_filters_by_type(tmp_path):
    path = tmp_path / "trace.jsonl"
    out = tmp_path / "filtered.jsonl"
    append_event(path, "tool.call.started")
    append_event(path, "tool.call.completed")

    count = export_trace(path, out, event_type="tool.call.completed")

    assert count == 1
    assert read_events(out)[0].event_type == "tool.call.completed"


def test_trace_append_cli_writes_redacted_event(tmp_path, capsys):
    path = tmp_path / "trace.jsonl"

    code = main(
        [
            "trace",
            "append",
            "--file",
            str(path),
            "--type",
            "tool.call.completed",
            "--actor",
            "codex",
            "--payload",
            '{"api_key": "secret", "status": "ok"}',
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "trace_" in out
    assert "[redacted]" in path.read_text(encoding="utf-8")


def test_trace_recent_cli_prints_recent_event(tmp_path, capsys):
    path = tmp_path / "trace.jsonl"
    append_event(path, "old")
    append_event(path, "new")

    code = main(["trace", "recent", "--file", str(path), "--limit", "1"])

    out = capsys.readouterr().out
    assert code == 0
    assert "new" in out
    assert "old" not in out


def test_trace_verify_cli_reports_ok(tmp_path, capsys):
    path = tmp_path / "trace.jsonl"
    append_event(path, "tool.call.completed")

    code = main(["trace", "verify", str(path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "OK: True" in out


def test_trace_export_cli_writes_filtered_file(tmp_path):
    path = tmp_path / "trace.jsonl"
    out = tmp_path / "filtered.jsonl"
    append_event(path, "a")
    append_event(path, "b")

    code = main(["trace", "export", str(path), "--out", str(out), "--type", "b"])

    assert code == 0
    assert read_events(out)[0].event_type == "b"
