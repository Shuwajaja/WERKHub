"""File-based approval queue with one-use execute-after-token (ADR-001 revisit).

A tool call the policy classifies ``approval_required`` persists a pending
record and returns an opaque one-use token. A human approves via CLI; the
caller retries with the token; the hub validates it in constant time,
consumes it atomically BEFORE execution, and runs the tool exactly once.

Stdlib-only: no FastMCP, no daemon. Tokens expire after a TTL
(``_TOKEN_TTL_SECONDS``) enforced inline at consume time; ``sweep_expired``
is a SYNCHRONOUS janitor (called by the CLI/serve, never a background
thread). Every transition is ledgered. The approvals dir defaults to the
ledger parent's hub-approvals folder — never the integration-gate dir.

Trust model: an approval is bound to a `tool_id` + `profile_id` AND a sha256
of the exact call arguments it approved (``args_hash``); a retry with
different arguments is rejected at consume time. Consumption is OS-atomic (an
``O_CREAT|O_EXCL`` claim file) so it wins exactly once even across processes.
The pending record's `call_args` are redacted on disk for review; the binding
hash is computed over the RAW args and leaks nothing.

Secret-handling (SF11): the one-use `token` is a short-lived bearer secret
written PLAINTEXT to the record file between request and consume. It is
blanked on disk the moment it is consumed or denied, and the dir is 0o700 on
POSIX, so the exposure window is small and it never reaches the ledger/trace
(only `request_id` is ledgered). It is intentionally NOT run through the
secret-redaction discipline because the consume path needs the real value via
`hmac.compare_digest`. Keep the approvals dir on a user-private volume; do not
sync or back it up to shared storage. On Windows the dir inherits the user's
directory ACL.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..redaction import redact_payload
from .ledger import record_event

_STATUSES = ("pending", "approved", "denied", "consumed")

# An approved token is only redeemable for this long after it was REQUESTED
# (created_at). Past this the token fails closed and a sweep can evict it.
_TOKEN_TTL_SECONDS = 900


def hash_call_args(call_args: dict[str, Any]) -> str:
    """Stable sha256 of the call arguments an approval is bound to.

    The hash binds a token to the exact arguments that were approved (MF2):
    it is computed over the RAW args (canonical JSON, sorted keys) so a retry
    with different arguments is rejected. The digest leaks nothing, so it is
    safe to persist on disk and ledger; the secret-bearing args themselves are
    never stored here (the pending record keeps a redacted copy for review).
    """
    canonical = json.dumps(call_args or {}, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _is_expired(created_at: str, now: datetime | None = None) -> bool:
    """True when an approval is older than the TTL (unparseable => expired)."""
    moment = _parse_iso(created_at)
    if moment is None:
        return True
    current = now or datetime.now(timezone.utc)
    return current - moment > timedelta(seconds=_TOKEN_TTL_SECONDS)


# Per-request locks so a concurrent double-consume (FastMCP dispatches sync
# handlers on a thread pool) cannot execute a tool twice on one token.
_CONSUME_LOCKS: dict[str, threading.Lock] = {}
_CONSUME_GUARD = threading.Lock()


def _consume_lock(approvals_dir: str | Path, request_id: str) -> threading.Lock:
    key = f"{Path(approvals_dir).resolve()}::{request_id}"
    with _CONSUME_GUARD:
        lock = _CONSUME_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _CONSUME_LOCKS[key] = lock
        return lock


def _evict_consume_lock(approvals_dir: str | Path, request_id: str) -> None:
    key = f"{Path(approvals_dir).resolve()}::{request_id}"
    with _CONSUME_GUARD:
        _CONSUME_LOCKS.pop(key, None)


def _validate_request_id(request_id: str) -> None:
    """Reject any request_id that does not match the expected format (path-traversal guard)."""
    if not re.fullmatch(r"apr_[0-9a-f]{12}", str(request_id)):
        raise ValueError(f"invalid request_id: {request_id!r}")


def _claim_path(approvals_dir: str | Path, request_id: str) -> Path:
    _validate_request_id(request_id)
    base = _dir(approvals_dir)
    path = base / f"{request_id}.claim"
    # Defense-in-depth: confirm the resolved path stays inside approvals_dir.
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        raise ValueError(f"request_id resolves outside approvals dir: {request_id!r}")
    return path


def _claim_once(approvals_dir: str | Path, request_id: str) -> bool:
    """Atomically claim a request exactly once across processes (MF4).

    Creates a sentinel via O_CREAT|O_EXCL: the first caller (in any process)
    wins and gets True; every later caller gets FileExistsError -> False. This
    is the OS-level single-writer the in-process thread lock cannot provide.
    """
    path = _claim_path(approvals_dir, request_id)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    os.close(fd)
    return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class ApprovalRecord:
    """One approval request and its lifecycle state.

    Note: unhashable due to mutable call_args field; do not use as a dict key or set member.
    """

    request_id: str
    tool_id: str
    profile_id: str
    call_args: dict[str, Any]
    status: str
    token: str
    created_at: str
    args_hash: str = ""
    resolved_at: str = ""
    resolved_by: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ApprovalRecord":
        status = str(raw.get("status", "pending"))
        return cls(
            request_id=str(raw["request_id"]),
            tool_id=str(raw["tool_id"]),
            profile_id=str(raw.get("profile_id", "")),
            call_args=dict(raw.get("call_args", {})),
            status=status if status in _STATUSES else "pending",
            token=str(raw.get("token", "")),
            created_at=str(raw.get("created_at", "")),
            args_hash=str(raw.get("args_hash", "")),
            resolved_at=str(raw.get("resolved_at", "")),
            resolved_by=str(raw.get("resolved_by", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "tool_id": self.tool_id,
            "profile_id": self.profile_id,
            "call_args": self.call_args,
            "status": self.status,
            "token": self.token,
            "created_at": self.created_at,
            "args_hash": self.args_hash,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
        }


def _dir(approvals_dir: str | Path) -> Path:
    path = Path(approvals_dir)
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            path.chmod(0o700)
        except OSError as exc:
            warnings.warn(f"approvals dir {path}: could not set 0o700: {exc}", stacklevel=3)
    return path


def _record_path(approvals_dir: str | Path, request_id: str) -> Path:
    _validate_request_id(request_id)
    base = _dir(approvals_dir)
    path = base / f"{request_id}.json"
    # Defense-in-depth: confirm the resolved path stays inside approvals_dir.
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        raise ValueError(f"request_id resolves outside approvals dir: {request_id!r}")
    return path


def _write_record(approvals_dir: str | Path, record: ApprovalRecord) -> None:
    dest = _record_path(approvals_dir, record.request_id)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        tmp.write_text(json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def load_record(approvals_dir: str | Path, request_id: str) -> ApprovalRecord:
    """Load one approval record by id (raises KeyError if absent)."""
    path = _record_path(approvals_dir, request_id)
    if not path.exists():
        raise KeyError(f"unknown approval request: {request_id}")
    return ApprovalRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))


def list_records(approvals_dir: str | Path, status: str | None = None) -> list[ApprovalRecord]:
    """List approval records, optionally filtered by status."""
    root = _dir(approvals_dir)
    records: list[ApprovalRecord] = []
    for path in sorted(root.glob("apr_*.json")):
        try:
            records.append(ApprovalRecord.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            warnings.warn(f"list_records: skipping corrupt record {path.name}: {exc}", stacklevel=2)
            continue
    if status is not None:
        records = [r for r in records if r.status == status]
    return records


def request_approval(
    approvals_dir: str | Path,
    ledger_path: str | Path,
    tool_id: str,
    profile_id: str,
    call_args: dict[str, Any],
) -> ApprovalRecord:
    """Persist a pending approval request and return it (with a fresh token)."""
    record = ApprovalRecord(
        request_id="apr_" + secrets.token_hex(6),
        tool_id=tool_id,
        profile_id=profile_id,
        call_args=redact_payload(call_args or {}),
        status="pending",
        token=secrets.token_hex(16),
        created_at=_now_iso(),
        args_hash=hash_call_args(call_args or {}),
    )
    _write_record(approvals_dir, record)
    record_event(
        ledger_path,
        "approval.requested",
        {"request_id": record.request_id, "tool_id": tool_id, "profile": profile_id},
    )
    return record


def approve_request(
    approvals_dir: str | Path,
    ledger_path: str | Path,
    request_id: str,
    resolved_by: str = "human",
) -> ApprovalRecord:
    """Transition a pending request to approved (token preserved).

    Acquires the per-request consume lock before reading the record so that a
    concurrent ``sweep_expired`` call cannot write 'denied' between our load
    and our write (TOCTOU guard matching the pattern in ``sweep_expired``).
    """
    lock = _consume_lock(approvals_dir, request_id)
    try:
        with lock:
            record = load_record(approvals_dir, request_id)
            if record.status != "pending":
                raise ValueError(f"request {request_id} is {record.status}, not pending")
            approved = dataclasses.replace(record, status="approved", resolved_at=_now_iso(), resolved_by=resolved_by)
            _write_record(approvals_dir, approved)
            record_event(
                ledger_path,
                "approval.resolved",
                {"request_id": request_id, "decision": "approved", "resolved_by": resolved_by},
            )
    finally:
        _evict_consume_lock(approvals_dir, request_id)
    return approved


def deny_request(
    approvals_dir: str | Path,
    ledger_path: str | Path,
    request_id: str,
    resolved_by: str = "human",
) -> ApprovalRecord:
    """Transition a pending request to denied (token blanked).

    Acquires the per-request consume lock before reading the record so that a
    concurrent ``sweep_expired`` call cannot write 'denied' between our load
    and our write (TOCTOU guard matching the pattern in ``sweep_expired``).
    """
    lock = _consume_lock(approvals_dir, request_id)
    try:
        with lock:
            record = load_record(approvals_dir, request_id)
            if record.status != "pending":
                raise ValueError(f"request {request_id} is {record.status}, not pending")
            denied = dataclasses.replace(record, status="denied", token="", resolved_at=_now_iso(), resolved_by=resolved_by)
            _write_record(approvals_dir, denied)
            record_event(
                ledger_path,
                "approval.resolved",
                {"request_id": request_id, "decision": "denied", "resolved_by": resolved_by},
            )
    finally:
        _evict_consume_lock(approvals_dir, request_id)
    return denied


def sweep_expired(
    approvals_dir: str | Path,
    ledger_path: str | Path,
    now: datetime | None = None,
) -> list[str]:
    """Synchronously expire stale pending/approved records (MF3).

    Pure function, NO daemon and NO background thread: approvals.py is not on
    the no_in_core_daemon allowlist, so any sweep must be an explicit call
    (the CLI or a serve loop invokes it). Returns the request_ids it expired.
    """
    moment = now or datetime.now(timezone.utc)
    expired: list[str] = []
    for record in list_records(approvals_dir):
        if record.status not in ("pending", "approved"):
            continue
        if not _is_expired(record.created_at, moment):
            continue
        # Acquire the per-request consume lock before reading/writing the record
        # to prevent a race with consume_token (TOCTOU guard: consume_token may
        # have transitioned the record between our list_records read and here).
        lock = _consume_lock(approvals_dir, record.request_id)
        skip = False
        try:
            with lock:
                # Re-read inside the lock to detect concurrent consume_token.
                try:
                    current = load_record(approvals_dir, record.request_id)
                except KeyError:
                    skip = True  # record was deleted by a concurrent operation
                    current = record  # type: ignore[assignment]  # not used when skip=True
                if not skip and current.status not in ("pending", "approved"):
                    # Already consumed or denied by a concurrent consume_token call.
                    skip = True
                if not skip:
                    stale = dataclasses.replace(current, status="denied", token="", resolved_at=_now_iso(), resolved_by="expiry")
                    _write_record(approvals_dir, stale)
                    record_event(
                        ledger_path,
                        "approval.resolved",
                        {"request_id": current.request_id, "decision": "denied", "resolved_by": "expiry"},
                    )
        finally:
            _evict_consume_lock(approvals_dir, record.request_id)
        if skip:
            continue
        expired.append(record.request_id)
    return expired


def consume_token(
    approvals_dir: str | Path,
    ledger_path: str | Path,
    request_id: str,
    token: str,
    expected_args_hash: str,
) -> ApprovalRecord:
    """Validate + atomically consume an approved token BEFORE execution.

    Raises ValueError on not-found, not-approved, blank, mismatched token,
    argument-hash mismatch (MF2), or expiry (MF3). Consumption is made
    OS-atomic by an O_CREAT|O_EXCL claim file so a second consume in another
    process cannot also execute (MF4). On success the record is moved to
    consumed and the token is blanked on disk before this returns.
    """
    try:
        with _consume_lock(approvals_dir, request_id):
            try:
                record = load_record(approvals_dir, request_id)
            except KeyError as exc:
                raise ValueError(str(exc)) from exc
            if record.status != "approved":
                raise ValueError(f"request {request_id} is {record.status}, not approved")
            if not record.token or not token or not hmac.compare_digest(record.token, str(token)):
                raise ValueError("approval token mismatch")
            if (
                not record.args_hash
                or not expected_args_hash
                or not hmac.compare_digest(record.args_hash, str(expected_args_hash))
            ):
                raise ValueError("approval token bound to different call arguments")
            if _is_expired(record.created_at):
                raise ValueError(f"approval {request_id} expired")
            # OS-atomic single-winner across processes; the thread lock above only
            # guards this process, the claim file guards every process.
            if not _claim_once(approvals_dir, request_id):
                raise ValueError(f"approval {request_id} already consumed")
            consumed = dataclasses.replace(record, status="consumed", token="", resolved_at=_now_iso(), resolved_by="system:consume")
            try:
                _write_record(approvals_dir, consumed)
            except Exception:
                # Roll back the claim sentinel so the token remains retryable.
                # Without this, the claim file persists and every subsequent
                # consume_token call raises ValueError("already consumed"), which
                # permanently bricks the approval while the record still reads
                # "approved" on disk. Widened from OSError: _write_record can
                # also raise TypeError (non-serialisable field), which must also
                # trigger cleanup.
                try:
                    _claim_path(approvals_dir, request_id).unlink(missing_ok=True)
                except OSError as _ue:
                    warnings.warn(
                        f"consume_token: rollback unlink failed for {request_id!r}: {_ue}",
                        stacklevel=3,
                    )
                raise
            try:
                record_event(
                    ledger_path,
                    "approval.token_consumed",
                    {"request_id": request_id, "tool_id": record.tool_id, "profile": record.profile_id},
                )
            except OSError as _re:
                # Token is already consumed; losing the audit event is preferable
                # to surfacing a confusing OSError to the caller.
                warnings.warn(
                    f"consume_token: audit record_event failed for {request_id!r}: {_re}",
                    stacklevel=2,
                )
    finally:
        _evict_consume_lock(approvals_dir, request_id)
    return consumed
