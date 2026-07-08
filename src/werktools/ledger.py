"""Append-only JSONL ledger primitive."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import threading
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Platform-specific advisory file locking (stdlib only; one of these exists).
# Use sys.platform (not os.name) so mypy narrows the branches per platform.
if sys.platform == "win32":  # pragma: no cover - platform-specific
    import msvcrt
else:  # pragma: no cover - platform-specific
    import fcntl

GENESIS = "0" * 64

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_LOCK_SUFFIX = ".lock"


def _get_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


@contextmanager
def _cross_process_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive cross-process advisory lock for the duration of a block.

    The in-process ``threading.Lock`` only serializes one interpreter; two
    separate OS processes (e.g. the serve process, a CLI command, the dashboard)
    would otherwise each read the same ``prev_hash`` and append competing chain
    entries, forking the tamper-evident ledger. This serializes appends ACROSS
    processes via a sidecar ``<ledger>.lock`` file: ``fcntl.flock`` on POSIX,
    ``msvcrt.locking`` on Windows.

    Crash-safe: the OS releases the advisory lock automatically when the holding
    process dies, so there is no stale lock file to reap. The acquire blocks at
    the OS level (no Python spin loop — keeps the core free of ``while True``).
    """
    lock_path = path.with_name(path.name + _LOCK_SUFFIX)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if sys.platform == "win32":  # pragma: no cover - platform-specific
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
            if sys.platform == "win32":  # pragma: no cover - platform-specific
                try:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_event_id() -> str:
    return "evt_" + secrets.token_hex(4)


def _hash_body(body: dict) -> str:
    canonical = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _last_hash_from_file(path: Path) -> str:
    last_hash = GENESIS
    for rec in read(path):
        current = rec.get("hash")
        if current:
            last_hash = str(current)
    return last_hash


def append(path: Path, event: dict, prev_hash: str | None = None) -> dict:
    """Append one event record to a JSONL ledger file."""
    path = Path(path)
    lock = _get_lock(path)

    # In-process lock first (serializes this interpreter's threads), then the
    # cross-process file lock (serializes other OS processes). Reading prev_hash
    # and appending the record is one atomic critical section under both, so the
    # hash chain cannot fork.
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _cross_process_lock(path):
            link_hash = prev_hash if prev_hash is not None else _last_hash_from_file(path)

            body = {
                "event_id": _new_event_id(),
                "ts": _now_iso(),
                "prev_hash": link_hash,
                "payload": event,
            }
            record = {**body, "hash": _hash_body(body)}

            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

            return record


def read(path: Path) -> list[dict]:
    """Read valid ledger records, skipping blank or corrupt lines."""
    path = Path(path)
    if not path.exists():
        return []

    records: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                loaded = json.loads(stripped)
            except json.JSONDecodeError as exc:
                warnings.warn(f"ledger.read: skipping corrupt line at path {path}: {exc}", stacklevel=2)
                continue
            if isinstance(loaded, dict):
                records.append(loaded)
    return records


def tail(path: Path, after_event_id: str | None = None) -> list[dict]:
    """Return records after the given event id, or all records without a cursor."""
    records = read(path)
    if after_event_id is None:
        return records

    for index, rec in enumerate(records):
        if rec.get("event_id") == after_event_id:
            return records[index + 1 :]

    return records
