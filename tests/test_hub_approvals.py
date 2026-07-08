import json

import pytest

from werktools.hub.approvals import (
    ApprovalRecord,
    _write_record,
    approve_request,
    consume_token,
    deny_request,
    hash_call_args,
    list_records,
    load_record,
    request_approval,
    sweep_expired,
)
from werktools.hub.ledger import recent_events


def _dirs(tmp_path):
    return tmp_path / "hub-approvals", tmp_path / "ledger.jsonl"


def _backdate(approvals_dir, request_id, created_at="2020-01-01T00:00:00Z"):
    """Rewrite a record's created_at on disk so it is past the TTL."""
    rec = load_record(approvals_dir, request_id)
    _write_record(approvals_dir, ApprovalRecord(**{**rec.to_dict(), "created_at": created_at}))


def _consume_worker(approvals_dir, ledger_path, request_id, token, args_hash, queue):
    """Module-level worker so multiprocessing 'spawn' can pickle it."""
    try:
        consume_token(str(approvals_dir), str(ledger_path), request_id, token, expected_args_hash=args_hash)
        queue.put("ok")
    except Exception:
        pass


def test_request_creates_pending_with_redacted_args(tmp_path):
    approvals, ledger = _dirs(tmp_path)

    record = request_approval(approvals, ledger, "github.create_pr", "codex-builder", {"token": "secret", "title": "x"})

    assert record.request_id.startswith("apr_")
    assert record.status == "pending"
    assert record.token
    on_disk = json.loads((approvals / f"{record.request_id}.json").read_text(encoding="utf-8"))
    assert on_disk["call_args"]["token"] == "[redacted]"
    assert on_disk["call_args"]["title"] == "x"
    assert recent_events(ledger, limit=1)[0]["payload"]["type"] == "approval.requested"


def test_list_filters_by_status(tmp_path):
    approvals, ledger = _dirs(tmp_path)
    a = request_approval(approvals, ledger, "t1", "p", {})
    request_approval(approvals, ledger, "t2", "p", {})
    approve_request(approvals, ledger, a.request_id)

    assert len(list_records(approvals)) == 2
    assert [r.request_id for r in list_records(approvals, status="approved")] == [a.request_id]


def test_approve_then_consume_executes_once(tmp_path):
    approvals, ledger = _dirs(tmp_path)
    rec = request_approval(approvals, ledger, "fs.write", "codex-builder", {})

    approved = approve_request(approvals, ledger, rec.request_id)
    assert approved.status == "approved"
    assert approved.token == rec.token

    consumed = consume_token(approvals, ledger, rec.request_id, rec.token, expected_args_hash=rec.args_hash)
    assert consumed.status == "consumed"
    assert consumed.token == ""

    # second consume is impossible
    with pytest.raises(ValueError):
        consume_token(approvals, ledger, rec.request_id, rec.token, expected_args_hash=rec.args_hash)

    types = [e["payload"]["type"] for e in recent_events(ledger, limit=10)]
    assert "approval.token_consumed" in types


def test_wrong_token_rejected_and_status_unchanged(tmp_path):
    approvals, ledger = _dirs(tmp_path)
    rec = request_approval(approvals, ledger, "fs.write", "p", {})
    approve_request(approvals, ledger, rec.request_id)

    with pytest.raises(ValueError):
        consume_token(approvals, ledger, rec.request_id, "deadbeef" * 4, expected_args_hash=rec.args_hash)

    assert load_record(approvals, rec.request_id).status == "approved"


def test_consume_requires_approved(tmp_path):
    approvals, ledger = _dirs(tmp_path)
    rec = request_approval(approvals, ledger, "fs.write", "p", {})

    with pytest.raises(ValueError):
        consume_token(approvals, ledger, rec.request_id, rec.token, expected_args_hash=rec.args_hash)


def test_approve_already_approved_rejected(tmp_path):
    approvals, ledger = _dirs(tmp_path)
    rec = request_approval(approvals, ledger, "t", "p", {})
    approve_request(approvals, ledger, rec.request_id)

    with pytest.raises(ValueError):
        approve_request(approvals, ledger, rec.request_id)


def test_deny_blanks_token_and_blocks_consume(tmp_path):
    approvals, ledger = _dirs(tmp_path)
    rec = request_approval(approvals, ledger, "t", "p", {})

    denied = deny_request(approvals, ledger, rec.request_id)
    assert denied.status == "denied"
    assert denied.token == ""

    with pytest.raises(ValueError):
        consume_token(approvals, ledger, rec.request_id, rec.token, expected_args_hash=rec.args_hash)


def test_deny_non_pending_rejected(tmp_path):
    approvals, ledger = _dirs(tmp_path)
    rec = request_approval(approvals, ledger, "t", "p", {})
    approve_request(approvals, ledger, rec.request_id)

    with pytest.raises(ValueError):
        deny_request(approvals, ledger, rec.request_id)


def test_concurrent_consume_executes_once(tmp_path):
    import threading

    approvals, ledger = _dirs(tmp_path)
    rec = request_approval(approvals, ledger, "fs.write", "p", {})
    approve_request(approvals, ledger, rec.request_id)

    successes = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        try:
            consume_token(approvals, ledger, rec.request_id, rec.token, expected_args_hash=rec.args_hash)
            successes.append(1)
        except ValueError:
            pass

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(successes) == 1


def test_wrong_binding_does_not_burn_token(tmp_path):
    # peek-before-consume: a token presented for the wrong tool is rejected at
    # the server layer without consuming it (the record stays approved).
    approvals, ledger = _dirs(tmp_path)
    rec = request_approval(approvals, ledger, "fs.write", "p", {})
    approved = approve_request(approvals, ledger, rec.request_id)
    assert load_record(approvals, rec.request_id).status == "approved"
    # the token is still valid because nothing consumed it
    consumed = consume_token(approvals, ledger, rec.request_id, approved.token, expected_args_hash=rec.args_hash)
    assert consumed.status == "consumed"


def test_cli_list_approve_deny(tmp_path, capsys):
    from werktools.cli import main
    from werktools.hub.registry import default_config

    config = tmp_path / "hub.json"
    body = default_config().to_dict()
    body["ledger_path"] = str(tmp_path / "hub-ledger.jsonl")
    config.write_text(json.dumps(body), encoding="utf-8")
    ledger = tmp_path / "hub-ledger.jsonl"
    approvals = tmp_path / "hub-approvals"
    a = request_approval(approvals, ledger, "fs.write", "codex-builder", {})
    b = request_approval(approvals, ledger, "shell.run", "codex-builder", {})

    assert main(["--config", str(config), "hub", "approvals", "list"]) == 0
    assert a.request_id in capsys.readouterr().out

    assert main(["--config", str(config), "hub", "approvals", "approve", a.request_id]) == 0
    out = capsys.readouterr().out
    assert "Token:" in out

    assert main(["--config", str(config), "hub", "approvals", "deny", b.request_id]) == 0
    assert "Denied" in capsys.readouterr().out


def test_consume_rejects_when_args_hash_mismatches(tmp_path):
    # MF2: a token is bound to the args it approved; a retry with different
    # args is rejected, and the rejection precedes the claim so the token is
    # NOT burned (the record stays approved for the legitimate retry).
    approvals, ledger = _dirs(tmp_path)
    rec = request_approval(approvals, ledger, "fs.write", "p", {"path": "/a"})
    approve_request(approvals, ledger, rec.request_id)

    with pytest.raises(ValueError, match="different call arguments"):
        consume_token(
            approvals, ledger, rec.request_id, rec.token,
            expected_args_hash=hash_call_args({"path": "/b"}),
        )
    assert load_record(approvals, rec.request_id).status == "approved"

    consumed = consume_token(
        approvals, ledger, rec.request_id, rec.token,
        expected_args_hash=hash_call_args({"path": "/a"}),
    )
    assert consumed.status == "consumed"


def test_consume_rejects_expired_token(tmp_path):
    # MF3: an approval older than the TTL fails closed; the expiry check
    # precedes the claim, so the record is untouched (still approved).
    approvals, ledger = _dirs(tmp_path)
    rec = request_approval(approvals, ledger, "fs.write", "p", {})
    approve_request(approvals, ledger, rec.request_id)
    _backdate(approvals, rec.request_id)

    with pytest.raises(ValueError, match="expired"):
        consume_token(approvals, ledger, rec.request_id, rec.token, expected_args_hash=rec.args_hash)
    assert load_record(approvals, rec.request_id).status == "approved"


def test_sweep_expired_denies_stale_and_ledgers(tmp_path):
    # MF3: sweep_expired transitions stale pending/approved records to denied
    # (token blanked, resolved_by=expiry) and ledgers approval.resolved; fresh
    # records are untouched.
    approvals, ledger = _dirs(tmp_path)
    fresh = request_approval(approvals, ledger, "fresh.tool", "p", {})
    stale_pending = request_approval(approvals, ledger, "stale.pending", "p", {})
    stale_approved = request_approval(approvals, ledger, "stale.approved", "p", {})
    approve_request(approvals, ledger, stale_approved.request_id)
    _backdate(approvals, stale_pending.request_id)
    _backdate(approvals, stale_approved.request_id)

    expired = sweep_expired(approvals, ledger)

    assert set(expired) == {stale_pending.request_id, stale_approved.request_id}
    for rid in expired:
        rec = load_record(approvals, rid)
        assert rec.status == "denied"
        assert rec.token == ""
        assert rec.resolved_by == "expiry"
    assert load_record(approvals, fresh.request_id).status == "pending"
    events = recent_events(ledger)
    assert any(
        e["payload"]["type"] == "approval.resolved"
        and e["payload"].get("decision") == "denied"
        and e["payload"].get("resolved_by") == "expiry"
        for e in events
    )


# ---------------------------------------------------------------------------
# Regression tests: path-traversal / security hardening
# ---------------------------------------------------------------------------

def test_validate_request_id_rejects_bad_ids():
    # _validate_request_id must raise ValueError for every malformed/traversal id.
    from werktools.hub.approvals import _validate_request_id

    bad_ids = [
        "../../../etc/passwd",
        "req_000000000000",   # wrong prefix (req_ not apr_)
        "APR_000000000000",   # uppercase prefix
        "apr_",               # too short (no hex digits)
        "apr_00000000000z",   # non-hex character
        "apr_0000000000000",  # 13 hex digits (one too many)
        "",                   # empty string
        "apr_abcde",          # 5 hex digits (too short)
    ]
    for rid in bad_ids:
        with pytest.raises(ValueError, match="invalid request_id"):
            _validate_request_id(rid)

    # A valid id must NOT raise
    _validate_request_id("apr_abcdef123456")


def test_validate_request_id_accepts_valid():
    # Boundary: exactly apr_ + 12 lowercase hex chars passes.
    from werktools.hub.approvals import _validate_request_id

    _validate_request_id("apr_000000000000")
    _validate_request_id("apr_ffffffffffff")
    _validate_request_id("apr_abcdef012345")


def test_consume_token_rejects_traversal_id_before_fs(tmp_path):
    # consume_token must raise ValueError before any FS side-effect for a
    # traversal id. The approvals dir must remain empty.
    approvals = tmp_path / "approvals"

    with pytest.raises(ValueError, match="invalid request_id"):
        consume_token(approvals, tmp_path / "ledger.jsonl",
                      "../etc/passwd", "token", expected_args_hash="hash")

    # No files were created by the traversal attempt
    assert not approvals.exists() or list(approvals.rglob("*")) == []


def test_consume_token_mandatory_expected_args_hash(tmp_path):
    # expected_args_hash is a mandatory positional parameter; calling without it
    # must raise TypeError (not silently skip hmac.compare_digest).
    approvals, ledger = _dirs(tmp_path)
    rec = request_approval(approvals, ledger, "fs.write", "p", {"path": "/secret"})
    approve_request(approvals, ledger, rec.request_id)

    # Omitting expected_args_hash is a TypeError
    with pytest.raises(TypeError):
        consume_token(approvals, ledger, rec.request_id, rec.token)  # type: ignore[call-arg]

    # Passing a wrong hash raises ValueError (binding is enforced)
    with pytest.raises(ValueError, match="different call arguments"):
        consume_token(approvals, ledger, rec.request_id, rec.token,
                      expected_args_hash=hash_call_args({"path": "/other"}))


def test_list_records_corrupt_file_warns_and_skips(tmp_path):
    # list_records must emit a UserWarning for a corrupt file and still return
    # the valid records.
    approvals_dir = tmp_path / "approvals"
    ledger = tmp_path / "ledger.jsonl"
    good = request_approval(approvals_dir, ledger, "fs.read", "p", {})

    # Plant a corrupt file that matches the glob pattern
    (approvals_dir / "apr_aabbccddeeff.json").write_text("{corrupt", encoding="utf-8")

    with pytest.warns(UserWarning, match="corrupt record"):
        records = list_records(approvals_dir)

    assert any(r.request_id == good.request_id for r in records)
    assert all(r.request_id != "apr_aabbccddeeff" for r in records)


def test_multiprocess_double_consume_executes_once(tmp_path):
    # MF4: the O_CREAT|O_EXCL claim wins exactly once across PROCESSES, not
    # just across threads.
    import multiprocessing

    approvals, ledger = _dirs(tmp_path)
    rec = request_approval(approvals, ledger, "fs.write", "p", {})
    approve_request(approvals, ledger, rec.request_id)

    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    procs = [
        ctx.Process(
            target=_consume_worker,
            args=(str(approvals), str(ledger), rec.request_id, rec.token, rec.args_hash, queue),
        )
        for _ in range(4)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join()

    successes = []
    while not queue.empty():
        successes.append(queue.get())
    assert successes == ["ok"]


def test_write_record_oserror_rolls_back_claim_sentinel(tmp_path, monkeypatch):
    # Regression: when _write_record raises OSError after _claim_once succeeds,
    # the claim sentinel must be cleaned up so a subsequent consume_token call
    # succeeds rather than raising "already consumed".
    import werktools.hub.approvals as _approvals_mod

    approvals, ledger = _dirs(tmp_path)
    rec = request_approval(approvals, ledger, "fs.write", "p", {})
    approve_request(approvals, ledger, rec.request_id)

    # Patch _write_record to raise OSError on the first call (the consumed write).
    _original_write = _approvals_mod._write_record
    call_count = {"n": 0}

    def _failing_write(approvals_dir, record):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("simulated disk full")
        return _original_write(approvals_dir, record)

    monkeypatch.setattr(_approvals_mod, "_write_record", _failing_write)

    with pytest.raises(OSError, match="simulated disk full"):
        consume_token(approvals, ledger, rec.request_id, rec.token, expected_args_hash=rec.args_hash)

    # The claim sentinel must have been cleaned up.
    from werktools.hub.approvals import _claim_path
    assert not _claim_path(approvals, rec.request_id).exists()

    # Restore and verify a fresh consume_token succeeds (token is not bricked).
    monkeypatch.undo()
    consumed = consume_token(approvals, ledger, rec.request_id, rec.token, expected_args_hash=rec.args_hash)
    assert consumed.status == "consumed"


def test_consume_token_blank_args_hash_on_disk_raises(tmp_path):
    """A record with args_hash='' on disk must not be consumable via blank bypass."""
    import dataclasses
    approvals, ledger = _dirs(tmp_path)
    rec = request_approval(approvals, ledger, "fs.write", "p", {})
    approve_request(approvals, ledger, rec.request_id)

    # Overwrite the record on disk with args_hash blanked (simulates old/corrupt record).
    blanked = dataclasses.replace(rec, status="approved", args_hash="")
    _write_record(approvals, blanked)

    with pytest.raises(ValueError, match="different call arguments"):
        consume_token(approvals, ledger, rec.request_id, rec.token, expected_args_hash="")

    # Confirm record is still approved (not consumed).
    assert load_record(approvals, rec.request_id).status == "approved"


def test_approve_request_after_sweep_raises_value_error(tmp_path):
    """Regression: approve_request on a record already swept to 'denied' must raise ValueError.

    This pins the locking behaviour: sweep_expired writes 'denied', then
    approve_request re-reads inside the lock and must see 'denied', not 'pending'.
    """
    approvals, ledger = _dirs(tmp_path)
    rec = request_approval(approvals, ledger, "tool.x", "p", {})
    _backdate(approvals, rec.request_id)
    swept = sweep_expired(approvals, ledger)
    assert rec.request_id in swept, "record should have been swept"
    assert load_record(approvals, rec.request_id).status == "denied"
    with pytest.raises(ValueError):
        approve_request(approvals, ledger, rec.request_id)
