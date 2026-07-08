"""Process registry + sidecar for downstream MCP lifecycle (pure stdlib).

The hub spawns downstream MCP servers; this module tracks them so the L1
reaper can kill idle/orphan processes and the L3 status API can report
them. Each downstream is started in its OWN process group so the whole
tree dies with the hub:

- POSIX: ``start_new_session=True`` (a new session/process group).
- Windows: a Job Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` — the
  job handle is held for the process lifetime and closing it (or the hub
  exiting) kills the whole tree.

No daemon: every function here is synchronous. ctypes only, no psutil.
"""

from __future__ import annotations

import copy
import errno as _errno_mod
import json
import os
import subprocess
import threading
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, cast

ProcessState = Literal["starting", "live", "idle", "dead"]
_STATES = ("starting", "live", "idle", "dead")

_IS_WINDOWS = os.name == "nt"
_STILL_ACTIVE = 259

# Job handles by server_id, kept open for the process lifetime (Windows only).
_WINDOWS_HANDLES: dict[str, int] = {}
_SIDECAR_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sidecar_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _SIDECAR_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SIDECAR_LOCKS[key] = lock
        return lock


@dataclass
class ProcessRecord:
    """One tracked downstream process (mutable runtime state)."""

    server_id: str
    pid: int
    pgid: int | None
    started_at: str
    last_used_at: str
    state: ProcessState
    profile_owner: str
    job_handle: int | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProcessRecord":
        state = str(raw.get("state", "dead"))
        pgid = raw.get("pgid")
        return cls(
            server_id=str(raw["server_id"]),
            pid=int(raw["pid"]),
            pgid=int(pgid) if pgid is not None else None,
            started_at=str(raw.get("started_at", "")),
            last_used_at=str(raw.get("last_used_at", "")),
            state=cast("ProcessState", state if state in _STATES else "dead"),
            profile_owner=str(raw.get("profile_owner", "")),
            job_handle=None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "pid": self.pid,
            "pgid": self.pgid,
            "started_at": self.started_at,
            "last_used_at": self.last_used_at,
            "state": self.state,
            "profile_owner": self.profile_owner,
        }


# --- Windows ctypes plumbing (lazy, only touched on nt) ---------------------


def _win_kernel():
    import ctypes

    return ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]


def _make_job_kill_on_close():
    """Create a Job Object configured to kill its processes on handle close."""
    import ctypes
    from ctypes import wintypes

    kernel32 = _win_kernel()
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")  # type: ignore[attr-defined]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_void_p),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job, 9, ctypes.byref(info), ctypes.sizeof(info)  # JobObjectExtendedLimitInformation
    ):
        err = ctypes.get_last_error()  # type: ignore[attr-defined]
        kernel32.CloseHandle(job)
        raise OSError(err, "SetInformationJobObject failed")
    return int(job)


def _win_assign(job: int, pid: int) -> None:
    import ctypes

    kernel32 = _win_kernel()
    # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    handle = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), f"OpenProcess({pid}) failed")  # type: ignore[attr-defined]
    try:
        if not kernel32.AssignProcessToJobObject(job, handle):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")  # type: ignore[attr-defined]
    finally:
        kernel32.CloseHandle(handle)


def _win_close_handle(handle: int | None) -> None:
    if handle:
        _win_kernel().CloseHandle(handle)


def _win_is_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = _win_kernel()
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        err = ctypes.get_last_error()  # type: ignore[attr-defined]
        if err == 5:  # ERROR_ACCESS_DENIED: process exists but is inaccessible
            warnings.warn(
                f"_win_is_alive: OpenProcess({pid}) returned ERROR_ACCESS_DENIED; "
                "treating process as alive (inaccessible elevated process)",
                stacklevel=2,
            )
            return True
        # ERROR_INVALID_PARAMETER (87) or other not-found codes -> process does not exist
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)



def _posix_is_alive(pid: int) -> bool:
    """Robust POSIX liveness check.

    On Linux prefers /proc (zombies show as state 'Z' => not alive).
    Falls back to os.kill(pid, 0) for non-Linux or if /proc unavailable.
    """
    try:
        if pid <= 0:
            return False
        if os.path.isdir("/proc"):
            proc_path = f"/proc/{pid}"
            if os.path.exists(proc_path):
                try:
                    with open(os.path.join(proc_path, "stat"), "r", encoding="utf-8") as fh:
                        data = fh.read()
                    end = data.find(") ")
                    if end != -1:
                        rest = data[end + 2:]
                        state = rest.split(None, 1)[0]
                        if state == "Z":
                            return False
                        return True
                except (OSError, IndexError):
                    pass
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError as exc:
            err = getattr(exc, "errno", None)
            if err == _errno_mod.ESRCH:
                return False
            if err == _errno_mod.EINVAL:
                return False
            raise
        return True
    except Exception:
        return False


def _is_alive(pid: int) -> bool:
    return _win_is_alive(pid) if _IS_WINDOWS else _posix_is_alive(pid)


# --- Registry ---------------------------------------------------------------


class ProcessRegistry:
    """In-memory registry of tracked downstream processes + JSON sidecar."""

    def __init__(self, sidecar_path: str | Path = Path(".werktools/hub-procs.json")) -> None:
        self.sidecar_path = Path(sidecar_path)
        self._records: dict[str, ProcessRecord] = {}
        self._lock = threading.Lock()

    def record(self, rec: ProcessRecord) -> None:
        with self._lock:
            prior = self._records.get(rec.server_id)
            if prior is not None and prior.job_handle and prior.job_handle != rec.job_handle:
                _win_close_handle(prior.job_handle)
            self._records[rec.server_id] = rec
            if rec.job_handle:
                _WINDOWS_HANDLES[rec.server_id] = rec.job_handle

    def mark_used(self, server_id: str) -> None:
        with self._lock:
            rec = self._records.get(server_id)
            if rec is not None:
                rec.last_used_at = _now_iso()
                rec.state = "live"

    def mark_dead(self, server_id: str) -> None:
        with self._lock:
            rec = self._records.get(server_id)
            if rec is None:
                return
            rec.state = "dead"
            if rec.job_handle:
                _win_close_handle(rec.job_handle)
                rec.job_handle = None
                _WINDOWS_HANDLES.pop(server_id, None)

    def get(self, server_id: str) -> ProcessRecord | None:
        with self._lock:
            rec = self._records.get(server_id)
            return copy.copy(rec) if rec is not None else None

    def list_all(self) -> list[ProcessRecord]:
        with self._lock:
            return list(self._records.values())

    def list_live(self) -> list[ProcessRecord]:
        with self._lock:
            return [r for r in self._records.values() if r.state in ("starting", "live", "idle")]

    def save_sidecar(self) -> None:
        lock = _sidecar_lock(self.sidecar_path)
        with lock:
            self.sidecar_path.parent.mkdir(parents=True, exist_ok=True)
            body = {
                "schema": "hub-procs-v1",
                "written_at": _now_iso(),
                "records": [r.to_dict() for r in self.list_all()],
            }
            tmp = self.sidecar_path.with_name(self.sidecar_path.name + ".tmp")
            try:
                tmp.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                os.replace(tmp, self.sidecar_path)
            finally:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)

    def load_sidecar(self) -> list[ProcessRecord]:
        if not self.sidecar_path.exists():
            return []
        try:
            raw = json.loads(self.sidecar_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            warnings.warn(
                f"load_sidecar: corrupt sidecar at {self.sidecar_path}: {exc}; treating as empty",
                stacklevel=2,
            )
            return []
        if not isinstance(raw, dict):
            return []
        records: list[ProcessRecord] = []
        for item in raw.get("records", []):
            if not isinstance(item, dict):
                continue
            try:
                records.append(ProcessRecord.from_dict(item))
            except (KeyError, TypeError, ValueError) as exc:
                warnings.warn(
                    f"load_sidecar: skipping malformed record {item!r}: {exc}",
                    stacklevel=2,
                )
                continue
        return records


def spawn(
    server,
    registry: ProcessRegistry,
    profile_owner: str,
    *,
    _os_spawn: Callable[..., subprocess.Popen] | None = None,
) -> ProcessRecord:
    """Spawn a downstream stdio server in its own process group; record it.

    Fail-closed: if Popen fails the registry is unchanged (RuntimeError). On
    Windows, if Job-Object setup fails after Popen, the process is terminated
    and a RuntimeError is raised.

    Known Windows race: the child starts running before it is assigned to the
    Job Object, so a grandchild spawned in that brief window can escape the
    job and survive ``KILL_ON_JOB_CLOSE``. The clean fix is CREATE_SUSPENDED +
    AssignProcessToJobObject + ResumeThread, but ``subprocess.Popen`` does not
    expose the child's primary thread handle, so resuming via stdlib is not
    possible without ctypes CreateProcess plumbing; until that lands the race
    is documented and accepted (it is narrow and the reaper still catches
    orphans by pgid/pid). Do NOT pass CREATE_SUSPENDED here: a suspended child
    with no reachable thread handle would hang forever.
    """
    spawn_fn = _os_spawn or subprocess.Popen
    args = [server.command, *list(server.args)]
    job_handle: int | None = None
    if _IS_WINDOWS:
        try:
            proc = spawn_fn(
                args,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,  # type: ignore[attr-defined]
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise RuntimeError(f"failed to spawn {server.id!r}: {exc}") from exc
        try:
            job_handle = _make_job_kill_on_close()
            _win_assign(job_handle, proc.pid)
        except OSError as exc:
            try:
                proc.terminate()
            except OSError as term_exc:
                warnings.warn(f"spawn cleanup: terminate({proc.pid}) failed: {term_exc}", stacklevel=2)
            _win_close_handle(job_handle)
            raise RuntimeError(f"failed to job-bind {server.id!r}: {exc}") from exc
        pgid = None
    else:
        try:
            proc = spawn_fn(
                args,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise RuntimeError(f"failed to spawn {server.id!r}: {exc}") from exc
        try:
            pgid = os.getpgid(proc.pid)  # type: ignore[attr-defined]
        except (ProcessLookupError, OSError) as exc:
            warnings.warn(
                f"spawn: os.getpgid({proc.pid}) failed ({exc!r}); falling back to pid as pgid — process group may not be fully tracked",
                stacklevel=2,
            )
            pgid = proc.pid
    now = _now_iso()
    record = ProcessRecord(
        server_id=server.id,
        pid=proc.pid,
        pgid=pgid,
        started_at=now,
        last_used_at=now,
        state="live",
        profile_owner=profile_owner,
        job_handle=job_handle,
    )
    registry.record(record)
    return record


def reap_dead(registry: ProcessRegistry) -> list[ProcessRecord]:
    """Observe-only: mark exited processes dead (no signal sent)."""
    newly_dead: list[ProcessRecord] = []
    for rec in registry.list_live():
        if not _is_alive(rec.pid):
            registry.mark_dead(rec.server_id)
            newly_dead.append(rec)
    return newly_dead


def load_orphans(registry: ProcessRegistry) -> list[ProcessRecord]:
    """Return sidecar records not present in the live in-memory registry.

    Observe-only: no liveness check, no mutation. The L1 reaper decides what
    to do with these cross-session leftovers.
    """
    known = {r.server_id for r in registry.list_all()}
    return [rec for rec in registry.load_sidecar() if rec.server_id not in known]


# --- Reaper (L1-L2): one-shot, zero-daemon -----------------------------------


def _parse_iso(ts: str) -> float | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def _kill_group_posix(pid: int, pgid: int | None = None, timeout: float = 5.0) -> str:
    import signal

    if not _posix_is_alive(pid):
        return "already_dead"

    target_is_group = pgid is not None
    target: int = pgid if target_is_group else pid  # type: ignore[assignment]

    # SIGTERM
    try:
        if target_is_group:
            os.killpg(target, signal.SIGTERM)  # type: ignore[attr-defined]
        else:
            os.kill(target, signal.SIGTERM)
    except ProcessLookupError:
        return "already_dead"
    except OSError as exc:
        if getattr(exc, "errno", None) == _errno_mod.ESRCH:
            return "already_dead"
        if getattr(exc, "errno", None) == _errno_mod.EINVAL and target_is_group:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                return "already_dead"
        elif getattr(exc, "errno", None) == _errno_mod.EPERM:
            pass
        else:
            raise

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _posix_is_alive(pid):
            return "sigterm"
        time.sleep(0.05)

    # SIGKILL escalation
    try:
        if target_is_group:
            os.killpg(target, signal.SIGKILL)  # type: ignore[attr-defined]
        else:
            os.kill(pid, signal.SIGKILL)  # type: ignore[attr-defined]
    except ProcessLookupError:
        return "sigterm"
    except OSError as exc:
        if getattr(exc, "errno", None) == _errno_mod.ESRCH:
            return "sigterm"
        if getattr(exc, "errno", None) == _errno_mod.EINVAL and target_is_group:
            try:
                os.kill(pid, signal.SIGKILL)  # type: ignore[attr-defined]
            except ProcessLookupError:
                return "sigterm"
        else:
            raise

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not _posix_is_alive(pid):
            return "sigkill"
        time.sleep(0.05)

    return "sigkill"


def _kill_group_windows(pid: int, job_handle: int | None) -> str:
    import ctypes

    if not _win_is_alive(pid):
        return "already_dead"
    kernel32 = _win_kernel()
    if job_handle:
        if kernel32.TerminateJobObject(job_handle, 1):
            return "win_job"
        # TerminateJobObject failed — warn and fall through to TerminateProcess.
        err = ctypes.get_last_error()  # type: ignore[attr-defined]
        warnings.warn(
            f"_kill_group_windows: TerminateJobObject failed for pid={pid} "
            f"(error={err}); falling back to TerminateProcess",
            stacklevel=2,
        )
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not handle:
        return "already_dead"
    try:
        ok = kernel32.TerminateProcess(handle, 1)
        if not ok:
            err_code = ctypes.get_last_error()  # type: ignore[attr-defined]
            raise OSError(err_code, f"TerminateProcess({pid}) failed")
    finally:
        kernel32.CloseHandle(handle)
    return "win_terminate"


def _kill_group(pid: int, pgid: int | None, job_handle: int | None, timeout: float = 5.0) -> str:
    if _IS_WINDOWS:
        return _kill_group_windows(pid, job_handle)
    return _kill_group_posix(pid, pgid=pgid, timeout=timeout)


def reap(
    now: float,
    idle_ttl: float,
    sidecar_path: str | Path,
    ledger_path: str | Path,
    *,
    timeout: float = 5.0,
    _reason_override: str | None = None,
) -> list[dict[str, Any]]:
    """Kill idle/dead process groups from the sidecar; ledger; rewrite sidecar.

    Synchronous and one-shot — never a daemon. Returns one dict per reaped
    record {pid, server_id, reason, kill_status, ts}. Records that survive
    (alive and not idle) are written back atomically.
    """
    from .ledger import record_event

    registry = ProcessRegistry(sidecar_path)
    records = registry.load_sidecar()
    if not records:
        return []
    reaped: list[dict[str, Any]] = []
    survivors: list[ProcessRecord] = []
    for rec in records:
        alive = _is_alive(rec.pid)
        if alive:
            parsed = _parse_iso(rec.last_used_at)
            if parsed is None and rec.last_used_at:
                warnings.warn(
                    f"reap: pid={rec.pid} server_id={rec.server_id!r} has unparseable "
                    f"last_used_at={rec.last_used_at!r}; reporting idle_seconds=-1",
                    stacklevel=2,
                )
            idle_seconds = (now - parsed) if parsed is not None else float("inf")
            if idle_seconds < idle_ttl:
                survivors.append(rec)
                continue
            reason = _reason_override or "idle"
            record_event(
                ledger_path,
                "process.idle",
                {
                    "pid": rec.pid,
                    "server_id": rec.server_id,
                    "idle_seconds": round(idle_seconds, 3) if idle_seconds != float("inf") else -1,
                },
            )
            try:
                job_handle = _WINDOWS_HANDLES.get(rec.server_id) if _IS_WINDOWS else None
                if _IS_WINDOWS and job_handle is None:
                    warnings.warn(
                        f"reap: no job handle for {rec.server_id!r} (cross-session reap); "
                        f"only the direct PID will be terminated — grandchild processes may survive",
                        stacklevel=2,
                    )
                kill_status = _kill_group(rec.pid, rec.pgid, job_handle, timeout=timeout)
                # Close the Windows Job Object handle now that the process tree
                # has been killed — mirrors the cleanup in mark_dead() (lines
                # 249-251) and prevents accumulating open kernel handles across
                # reap cycles.
                if _IS_WINDOWS and job_handle:
                    _win_close_handle(job_handle)
                    _WINDOWS_HANDLES.pop(rec.server_id, None)
            except Exception as exc:
                kill_status = f"error:{type(exc).__name__}:{exc}"
                warnings.warn(
                    f"reap: kill failed for pid={rec.pid} server_id={rec.server_id!r}: {kill_status}; "
                    f"process.idle was already ledgered — repeated idle detection expected on next reap cycle",
                    stacklevel=2,
                )
                # Kill failed — preserve the record so it is retried on the next
                # reap cycle rather than silently dropped.
                survivors.append(rec)
                continue
        else:
            reason = _reason_override or "dead"
            kill_status = "already_dead"
        record_event(
            ledger_path,
            "process.killed",
            {"pid": rec.pid, "server_id": rec.server_id, "reason": reason, "kill_status": kill_status},
        )
        reaped.append(
            {"pid": rec.pid, "server_id": rec.server_id, "reason": reason, "kill_status": kill_status, "ts": _now_iso()}
        )
    out = ProcessRegistry(sidecar_path)
    for rec in survivors:
        out.record(rec)
    try:
        out.save_sidecar()
    except OSError as exc:
        # Surface the failure (honest-degrade) without minting a new ledger
        # event name: the EVENT_NAMES contract stays at its P1-locked set, and
        # the OSError already propagated before — we only add visibility.
        warnings.warn(f"reap: sidecar write failed: {exc}", stacklevel=2)
        raise
    return reaped


def orphan_sweep(sidecar_path: str | Path, ledger_path: str | Path, idle_ttl: float = 0.0) -> list[dict[str, Any]]:
    """Reap every survivor of a prior hub session (reason forced to 'orphan')."""
    return reap(time.time(), idle_ttl, sidecar_path, ledger_path, _reason_override="orphan")
