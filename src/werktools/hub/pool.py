"""In-memory registry of downstream connection metadata, keyed by server_id.

NOT wired into the live relay path. `serve` spawns a fresh downstream
subprocess per call (hub/relay.py:call_downstream), and `build_hub_server`
is always invoked with ``pool=None`` from the CLI, so in production no
WarmPool is ever populated and `hub_status` reports every server as
``unconfigured``. The real downstream lifecycle — actual PID tracking and
idle/orphan reaping of spawned subprocesses — lives in hub/lifecycle.py.

This module is a pure-stdlib data structure kept for the status projection
and its unit tests: it holds PoolEntry metadata and can TerminateProcess a
PID it is given, but nothing in the relay supplies it a real PID. No daemon,
no threads: every method is synchronous and lock-guarded, and the client
field is typed ``Any`` so this module never imports FastMCP.
"""

from __future__ import annotations

import os
import platform
import signal
import threading
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

PoolState = Literal["warm", "idle", "dead"]
_IS_WINDOWS = platform.system() == "Windows"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class PoolEntry:
    """One warm downstream connection's metadata (the live client is opaque)."""

    server_id: str
    pid: int | None
    name: str
    client: Any = field(default=None, compare=False)
    started_at: str = ""
    last_used_at: str = ""
    tool_count: int = 0
    state: PoolState = "warm"


def _win32_terminate(pid: int) -> bool:
    """Terminate a process via ctypes OpenProcess+TerminateProcess (no psutil)."""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


class WarmPool:
    """Thread-safe registry of warm downstream entries."""

    def __init__(self) -> None:
        self._entries: dict[str, PoolEntry] = {}
        self._lock = threading.Lock()

    def put(self, entry: PoolEntry) -> None:
        with self._lock:
            self._entries[entry.server_id] = entry

    def get(self, server_id: str) -> PoolEntry | None:
        with self._lock:
            return self._entries.get(server_id)

    def mark_used(self, server_id: str) -> None:
        with self._lock:
            entry = self._entries.get(server_id)
            if entry is not None:
                entry.last_used_at = _now_iso()
                entry.state = "warm"

    def mark_idle(self, server_id: str) -> None:
        with self._lock:
            entry = self._entries.get(server_id)
            if entry is not None:
                entry.state = "idle"

    def kill(self, server_id: str) -> bool:
        """Terminate the entry's PID and mark it dead only on confirmed kill.

        State is set to 'dead' only after the OS kill succeeds (or the process
        is already gone). A ``PermissionError`` leaves the state unchanged so
        ``hub_status`` does not incorrectly report a server as down when the
        kill was not actually delivered.
        """
        with self._lock:
            entry = self._entries.get(server_id)
            if entry is None or entry.pid is None:
                if entry is not None:
                    entry.state = "dead"
                return False
            pid = entry.pid
        # OS kill — outside the lock so we do not hold it during a blocking call.
        try:
            if _IS_WINDOWS:
                ok = _win32_terminate(pid)
            else:
                os.kill(pid, signal.SIGTERM)
                ok = True
        except ProcessLookupError:
            # Process is already dead — still mark it dead (it is genuinely gone).
            ok = True
        except OSError as exc:
            # Permission denied or other unexpected OS error: process may still be alive.
            warnings.warn(f"pool.kill({server_id}) pid={pid}: {exc}", stacklevel=2)
            return False
        # Re-acquire lock to update state only on confirmed kill.
        with self._lock:
            entry = self._entries.get(server_id)
            if entry is not None:
                entry.state = "dead"
        return ok

    def kill_all(self) -> None:
        for entry in self.entries():
            self.kill(entry.server_id)

    def remove(self, server_id: str) -> None:
        with self._lock:
            self._entries.pop(server_id, None)

    def entries(self) -> list[PoolEntry]:
        with self._lock:
            return list(self._entries.values())
