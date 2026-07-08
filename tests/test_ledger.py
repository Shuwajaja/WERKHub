"""Deterministic, offline, dep-free tests for werktools.ledger."""

import hashlib
import json
import threading
from pathlib import Path

from werktools.ledger import append, read, tail
from werktools.tools.audit import verify_chain


def _mp_append_worker(path_str: str, count: int, label: str) -> None:
    """Top-level worker for the cross-process append test (picklable under spawn)."""
    from werktools.ledger import append as _append

    for i in range(count):
        _append(Path(path_str), {"proc": label, "seq": i})


def _all_events(tmp_path: Path, n: int = 3) -> tuple[Path, list[dict]]:
    """Append n events to a fresh ledger file and return path plus records."""
    p = tmp_path / "ledger.jsonl"
    records = [append(p, {"type": "evt", "seq": i}) for i in range(n)]
    return p, records


def test_append_returns_required_keys(tmp_path):
    p = tmp_path / "ledger.jsonl"
    rec = append(p, {"type": "test.event", "value": 42})
    for key in ("event_id", "ts", "hash", "payload"):
        assert key in rec, f"missing key: {key}"


def test_append_creates_file_and_writes_valid_json(tmp_path):
    p = tmp_path / "ledger.jsonl"
    append(p, {"type": "test.event"})
    assert p.exists()
    lines = [line for line in p.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert "event_id" in obj


def test_append_increments_line_count(tmp_path):
    p, _ = _all_events(tmp_path, n=5)
    lines = [line for line in p.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 5


def test_append_with_explicit_prev_hash(tmp_path):
    p = tmp_path / "ledger.jsonl"
    custom_hash = "a" * 64
    rec = append(p, {"type": "test.event"}, prev_hash=custom_hash)
    assert rec.get("prev_hash") == custom_hash


def test_hash_chain_links_correctly(tmp_path):
    _, records = _all_events(tmp_path, n=4)
    genesis = "0" * 64
    expected_prev = genesis
    for rec in records:
        assert rec["prev_hash"] == expected_prev
        expected_prev = rec["hash"]


def test_hash_is_deterministic_sha256(tmp_path):
    p = tmp_path / "ledger.jsonl"
    rec = append(p, {"type": "stable"})
    body = {k: v for k, v in rec.items() if k != "hash"}
    expected_hash = hashlib.sha256(
        json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    assert rec["hash"] == expected_hash


def test_read_round_trip(tmp_path):
    p, records = _all_events(tmp_path, n=3)
    loaded = read(p)
    assert len(loaded) == 3
    assert [r["event_id"] for r in loaded] == [r["event_id"] for r in records]


def test_read_nonexistent_file_returns_empty(tmp_path):
    result = read(tmp_path / "missing.jsonl")
    assert result == []


def test_read_skips_corrupt_lines(tmp_path):
    p = tmp_path / "ledger.jsonl"
    p.write_text(
        '{"event_id":"e1","ts":"2026-01-01T00:00:00Z","hash":"aaa",'
        '"prev_hash":"'
        + "0" * 64
        + '","payload":{}}\n'
        "NOT_VALID_JSON\n"
        '{"event_id":"e3","ts":"2026-01-01T00:00:01Z","hash":"bbb",'
        '"prev_hash":"aaa","payload":{}}\n',
        encoding="utf-8",
    )
    result = read(p)
    assert len(result) == 2
    assert result[0]["event_id"] == "e1"
    assert result[1]["event_id"] == "e3"


def test_read_skips_blank_lines(tmp_path):
    p = tmp_path / "ledger.jsonl"
    p.write_text(
        '\n{"event_id":"e1","hash":"x","prev_hash":"'
        + "0" * 64
        + '","ts":"t","payload":{}}\n\n',
        encoding="utf-8",
    )
    assert len(read(p)) == 1


def test_tail_no_cursor_returns_all(tmp_path):
    p, _ = _all_events(tmp_path, n=4)
    result = tail(p)
    assert len(result) == 4


def test_tail_after_event_id_returns_subsequent_events(tmp_path):
    p, records = _all_events(tmp_path, n=5)
    pivot_id = records[1]["event_id"]
    result = tail(p, after_event_id=pivot_id)
    assert len(result) == 3
    assert result[0]["event_id"] == records[2]["event_id"]


def test_tail_after_last_event_id_returns_empty(tmp_path):
    p, records = _all_events(tmp_path, n=3)
    result = tail(p, after_event_id=records[-1]["event_id"])
    assert result == []


def test_tail_after_unknown_id_returns_all(tmp_path):
    p, _ = _all_events(tmp_path, n=3)
    result = tail(p, after_event_id="evt_doesnotexist")
    assert len(result) == 3


def test_read_corrupt_line_emits_warning(tmp_path):
    # ledger.read must emit a UserWarning when it skips a corrupt line —
    # not just silently drop it.
    import warnings as _warnings

    p = tmp_path / "ledger.jsonl"
    append(p, {"type": "ok", "seq": 0})

    # Inject a corrupt line at the start, between valid lines
    lines = p.read_text(encoding="utf-8").splitlines()
    lines.insert(0, "{NOT_JSON")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        records = read(p)

    warning_messages = [str(w.message) for w in caught]
    assert any("corrupt" in m.lower() for m in warning_messages), (
        f"expected a 'corrupt' warning; got: {warning_messages}"
    )
    # The valid line is still returned
    assert len(records) == 1


def test_concurrent_appends_produce_no_data_loss(tmp_path):
    p = tmp_path / "concurrent.jsonl"
    errors: list[Exception] = []

    def worker(thread_id: int) -> None:
        try:
            for i in range(5):
                append(p, {"thread": thread_id, "seq": i})
        except Exception as exc:  # pragma: no cover - reported by assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == [], f"Thread errors: {errors}"
    events = read(p)
    assert len(events) == 50, f"Expected 50 events, got {len(events)}"
    assert all("event_id" in event for event in events)


def test_cross_process_lock_creates_sidecar(tmp_path):
    """The advisory lock uses a sidecar <ledger>.lock file, separate from the ledger."""
    from werktools.ledger import _cross_process_lock

    p = tmp_path / "l.jsonl"
    with _cross_process_lock(p):
        assert (tmp_path / "l.jsonl.lock").exists()


def test_concurrent_cross_process_appends_keep_chain_intact(tmp_path):
    """MF11: appends from multiple OS PROCESSES must not fork the hash chain.

    Without a cross-process lock, two processes read the same prev_hash and write
    competing records that share it -> a forked, unverifiable chain. The advisory
    file lock serializes them so the chain stays linear and verifies end-to-end.
    """
    import multiprocessing as mp

    p = tmp_path / "mp.jsonl"
    ctx = mp.get_context("spawn")  # spawn = the strict case (also Windows default)
    per = 15
    procs = [ctx.Process(target=_mp_append_worker, args=(str(p), per, f"p{k}")) for k in range(3)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=120)
        assert proc.exitcode == 0, f"worker exited with {proc.exitcode}"

    events = read(p)
    assert len(events) == 3 * per, f"expected {3 * per} events, got {len(events)}"
    # The whole chain must verify (any fork breaks the linkage).
    result = verify_chain(p)
    assert result.ok, f"chain forked/broken under cross-process append: {len(result.errors)} errors"
    # Explicit anti-fork check: a linear chain has all-unique prev_hash values
    # (a fork shows up as two records sharing one prev_hash, e.g. both GENESIS).
    prevs = [e["prev_hash"] for e in events]
    assert len(set(prevs)) == len(prevs), "duplicate prev_hash -> the chain forked"
