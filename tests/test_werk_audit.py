import json

from werktools import ledger
from werktools.cli import main
from werktools.tools.audit import export_bundle, redact_jsonl, verify_chain, write_report
from werktools.tools.trace import append_event, verify_trace


def test_verify_chain_accepts_trace_hash_chain(tmp_path):
    path = tmp_path / "trace.jsonl"
    append_event(path, "tool.call.completed", payload={"ok": True})

    result = verify_chain(path)

    assert result.ok is True
    assert result.record_count == 1


def test_verify_chain_detects_tampering(tmp_path):
    path = tmp_path / "trace.jsonl"
    append_event(path, "tool.call.completed", payload={"status": "ok"})
    path.write_text(path.read_text(encoding="utf-8").replace('"ok"', '"changed"'), encoding="utf-8")

    result = verify_chain(path)

    assert result.ok is False
    assert "hash mismatch" in result.errors[0]


def test_verify_chain_accepts_core_ledger_chain(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.append(path, {"kind": "first"})
    ledger.append(path, {"kind": "second"})

    result = verify_chain(path)

    assert result.ok is True
    assert result.record_count == 2


def test_verify_chain_and_verify_trace_agree_on_same_file(tmp_path):
    path = tmp_path / "trace.jsonl"
    append_event(path, "a", payload={"ok": True})
    append_event(path, "b", payload={"n": 2})

    assert verify_chain(path).ok is True
    assert verify_trace(path).ok is True

    path.write_text(path.read_text(encoding="utf-8").replace('"ok"', '"changed"'), encoding="utf-8")

    assert verify_chain(path).ok is False
    assert verify_trace(path).ok is False


def test_verify_chain_rejects_fully_cleared_chain(tmp_path):
    path = tmp_path / "trace.jsonl"
    append_event(path, "a", payload={"ok": True})

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["hash"] = ""
    raw["prev_hash"] = ""
    path.write_text(json.dumps(raw, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_chain(path)

    assert result.ok is False


def test_verify_chain_rejects_cleared_hashes(tmp_path):
    path = tmp_path / "trace.jsonl"
    append_event(path, "a", payload={"ok": True})

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["hash"] = ""
    path.write_text(json.dumps(raw, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_chain(path)

    assert result.ok is False


def test_redact_jsonl_masks_secret_payload_keys(tmp_path):
    src = tmp_path / "trace.jsonl"
    out = tmp_path / "redacted.jsonl"
    src.write_text(json.dumps({"payload": {"token": "secret", "ok": True}}) + "\n", encoding="utf-8")

    redact_jsonl(src, out)

    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["payload"]["token"] == "[redacted]"
    assert body["payload"]["ok"] is True


def test_redact_jsonl_skips_corrupt_lines(tmp_path):
    src = tmp_path / "trace.jsonl"
    out = tmp_path / "redacted.jsonl"
    src.write_text("{corrupt\n" + json.dumps({"payload": {"token": "secret"}}) + "\n", encoding="utf-8")

    redact_jsonl(src, out)

    lines = [line for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert "[redacted]" in lines[0]


def test_export_bundle_reports_missing_files(tmp_path):
    trace = tmp_path / "trace.jsonl"
    evidence = tmp_path / "evidence.md"
    missing = tmp_path / "missing.md"
    out = tmp_path / "bundle"
    trace.write_text("{}", encoding="utf-8")
    evidence.write_text("evidence", encoding="utf-8")

    result = export_bundle(out, traces=(trace,), evidence=(evidence, missing))

    assert (out / "trace.jsonl").exists()
    assert result.missing_files == (str(missing),)


def test_write_report_contains_verification_status(tmp_path):
    path = tmp_path / "trace.jsonl"
    out = tmp_path / "audit.md"
    append_event(path, "tool.call.completed", payload={"ok": True})

    write_report(path, out)

    assert "OK: True" in out.read_text(encoding="utf-8")


def test_audit_cli_verify_redact_export_and_report(tmp_path, capsys):
    trace = tmp_path / "trace.jsonl"
    redacted = tmp_path / "redacted.jsonl"
    report = tmp_path / "audit.md"
    bundle = tmp_path / "bundle"
    evidence = tmp_path / "evidence.md"
    append_event(trace, "tool.call.completed", payload={"token": "secret"})
    evidence.write_text("evidence", encoding="utf-8")

    assert main(["audit", "verify", str(trace)]) == 0
    assert "OK: True" in capsys.readouterr().out

    assert main(["audit", "redact", str(trace), "--out", str(redacted)]) == 0
    assert "[redacted]" in redacted.read_text(encoding="utf-8")

    assert main(["audit", "export", "--out", str(bundle), "--trace", str(trace), "--evidence", str(evidence)]) == 0
    assert (bundle / "manifest.json").exists()

    assert main(["audit", "report", str(trace), "--out", str(report)]) == 0
    assert "WERK Audit Report" in report.read_text(encoding="utf-8")
