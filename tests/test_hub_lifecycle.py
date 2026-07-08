import os
import sys
import time

import pytest

from werktools.hub.contracts import DownstreamServer
from werktools.hub.lifecycle import (
    ProcessRecord,
    ProcessRegistry,
    load_orphans,
    reap_dead,
    spawn,
)

_IS_WINDOWS = os.name == "nt"
skip_if_windows = pytest.mark.skipif(_IS_WINDOWS, reason="posix-only")
skip_if_not_windows = pytest.mark.skipif(not _IS_WINDOWS, reason="windows-only")

_SLEEP_CHILD = DownstreamServer(
    id="sleeper", command=sys.executable, args=("-c", "import time\nwhile True:\n    time.sleep(1)")
)


def _record(server_id="s", pid=1234, pgid=99, state="live"):
    return ProcessRecord(
        server_id=server_id, pid=pid, pgid=pgid, started_at="t", last_used_at="t",
        state=state, profile_owner="p",
    )


def _poll_dead(pid, fn, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not fn(pid):
            return True
        time.sleep(0.05)
    return False


class TestProcessRecord:
    def test_round_trip_posix_pgid(self):
        rec = _record(pgid=4321)
        assert ProcessRecord.from_dict(rec.to_dict()).pgid == 4321

    def test_round_trip_windows_pgid_none(self):
        rec = _record(pgid=None)
        assert ProcessRecord.from_dict(rec.to_dict()).pgid is None

    def test_unknown_state_coerces_to_dead(self):
        rec = ProcessRecord.from_dict({"server_id": "s", "pid": 1, "pgid": None, "state": "zombie"})
        assert rec.state == "dead"

    def test_to_dict_excludes_job_handle(self):
        rec = _record()
        rec.job_handle = 777
        assert "job_handle" not in rec.to_dict()


class TestProcessRegistry:
    def test_record_and_list(self, tmp_path):
        reg = ProcessRegistry(tmp_path / "procs.json")
        reg.record(_record("a"))
        assert [r.server_id for r in reg.list_all()] == ["a"]

    def test_mark_used_updates_state(self, tmp_path):
        reg = ProcessRegistry(tmp_path / "procs.json")
        reg.record(_record("a", state="starting"))
        reg.mark_used("a")
        assert reg.get("a").state == "live"

    def test_mark_dead(self, tmp_path):
        reg = ProcessRegistry(tmp_path / "procs.json")
        reg.record(_record("a"))
        reg.mark_dead("a")
        assert reg.get("a").state == "dead"

    def test_get_unknown_is_none(self, tmp_path):
        assert ProcessRegistry(tmp_path / "procs.json").get("nope") is None

    def test_sidecar_round_trip_atomic(self, tmp_path):
        path = tmp_path / "procs.json"
        reg = ProcessRegistry(path)
        reg.record(_record("a"))
        reg.save_sidecar()
        assert not (tmp_path / "procs.json.tmp").exists()
        loaded = ProcessRegistry(path).load_sidecar()
        assert [r.server_id for r in loaded] == ["a"]

    def test_load_missing_sidecar_is_empty(self, tmp_path):
        assert ProcessRegistry(tmp_path / "nope.json").load_sidecar() == []

    def test_load_corrupt_sidecar_is_empty(self, tmp_path):
        path = tmp_path / "procs.json"
        path.write_text("{corrupt", encoding="utf-8")
        assert ProcessRegistry(path).load_sidecar() == []

    def test_eight_thread_record_safety(self, tmp_path):
        import threading

        reg = ProcessRegistry(tmp_path / "procs.json")

        def worker(n):
            for i in range(20):
                reg.record(_record(f"{n}-{i}"))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(reg.list_all()) == 8 * 20


@skip_if_windows
class TestSpawnPosix:
    def test_spawn_creates_new_process_group(self, tmp_path):
        reg = ProcessRegistry(tmp_path / "procs.json")
        rec = spawn(_SLEEP_CHILD, reg, "codex-builder")
        try:
            assert rec.pid > 0
            assert rec.state == "live"
            assert rec.pgid is not None and rec.pgid != os.getpgid(0)
        finally:
            import signal

            os.killpg(rec.pgid, signal.SIGKILL)

    def test_spawn_missing_command_fails_closed(self, tmp_path):
        reg = ProcessRegistry(tmp_path / "procs.json")
        bad = DownstreamServer(id="bad", command="doesnotexist-xyz-123", args=())
        with pytest.raises(RuntimeError):
            spawn(bad, reg, "p")
        assert reg.list_all() == []


@skip_if_not_windows
class TestSpawnWindows:
    def test_spawn_creates_job_and_kills_on_close(self, tmp_path):
        from werktools.hub.lifecycle import _win_is_alive

        reg = ProcessRegistry(tmp_path / "procs.json")
        rec = spawn(_SLEEP_CHILD, reg, "codex-builder")
        assert rec.job_handle and rec.job_handle != 0
        assert rec.pgid is None
        assert _win_is_alive(rec.pid)
        # sidecar never serializes the job handle
        reg.save_sidecar()
        assert "job_handle" not in (tmp_path / "procs.json").read_text(encoding="utf-8")
        # closing the job (mark_dead) kills the whole tree (KILL_ON_JOB_CLOSE)
        pid = rec.pid
        reg.mark_dead(rec.server_id)
        assert _poll_dead(pid, _win_is_alive)

    def test_spawn_missing_command_fails_closed(self, tmp_path):
        reg = ProcessRegistry(tmp_path / "procs.json")
        bad = DownstreamServer(id="bad", command="doesnotexist-xyz-123.exe", args=())
        with pytest.raises(RuntimeError):
            spawn(bad, reg, "p")
        assert reg.list_all() == []

    def test_job_bind_failure_terminates_and_raises(self, tmp_path):
        reg = ProcessRegistry(tmp_path / "procs.json")
        terminated = {"called": False}

        class FakeProc:
            pid = 0x7FFFFFFE  # an unlikely-to-exist pid -> OpenProcess fails

            def terminate(self):
                terminated["called"] = True

        def fake_spawn(args, **kwargs):
            return FakeProc()

        with pytest.raises(RuntimeError):
            spawn(_SLEEP_CHILD, reg, "p", _os_spawn=fake_spawn)
        assert terminated["called"] is True
        assert reg.list_all() == []


class TestReapAndOrphans:
    def test_reap_marks_exited_process_dead(self, tmp_path):
        reg = ProcessRegistry(tmp_path / "procs.json")
        quick = DownstreamServer(id="quick", command=sys.executable, args=("-c", "pass"))
        rec = spawn(quick, reg, "p")
        from werktools.hub.lifecycle import _is_alive

        assert _poll_dead(rec.pid, _is_alive)
        newly_dead = reap_dead(reg)
        assert [r.server_id for r in newly_dead] == ["quick"]
        assert reg.get("quick").state == "dead"

    def test_reap_leaves_live_alone(self, tmp_path):
        reg = ProcessRegistry(tmp_path / "procs.json")
        rec = spawn(_SLEEP_CHILD, reg, "p")
        try:
            assert reap_dead(reg) == []
            assert reg.get("sleeper").state == "live"
        finally:
            reg.mark_dead(rec.server_id)
            if not _IS_WINDOWS:
                import signal

                try:
                    os.killpg(rec.pgid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass

    def test_load_orphans_detects_sidecar_only_records(self, tmp_path):
        path = tmp_path / "procs.json"
        seed = ProcessRegistry(path)
        seed.record(_record("ghost", pid=424242))
        seed.save_sidecar()

        fresh = ProcessRegistry(path)
        orphans = load_orphans(fresh)
        assert [r.server_id for r in orphans] == ["ghost"]

    def test_load_sidecar_warns_on_malformed_record(self, tmp_path):
        # A sidecar with one valid and one malformed record (missing server_id)
        # must emit a UserWarning and return only the valid record.
        import json

        import pytest

        path = tmp_path / "procs.json"
        body = {
            "schema": "hub-procs-v1",
            "written_at": "2020-01-01T00:00:00Z",
            "records": [
                {
                    "server_id": "good",
                    "pid": 1,
                    "pgid": None,
                    "state": "live",
                    "started_at": "t",
                    "last_used_at": "t",
                    "profile_owner": "p",
                },
                {
                    # missing server_id — malformed
                    "pid": 2,
                    "pgid": None,
                    "state": "live",
                    "started_at": "t",
                    "last_used_at": "t",
                    "profile_owner": "p",
                },
            ],
        }
        path.write_text(json.dumps(body), encoding="utf-8")
        reg = ProcessRegistry(path)

        with pytest.warns(UserWarning, match="malformed record"):
            records = reg.load_sidecar()

        assert [r.server_id for r in records] == ["good"]

    def test_load_orphans_excludes_known(self, tmp_path):
        path = tmp_path / "procs.json"
        reg = ProcessRegistry(path)
        reg.record(_record("known"))
        reg.save_sidecar()
        # same server_id in memory -> not an orphan
        assert load_orphans(reg) == []


class TestReapMalformedTimestamp:
    def test_unparseable_last_used_at_warns_and_reports_minus_one(self, tmp_path):
        """A sidecar record with last_used_at='not-a-date' must emit UserWarning
        and the ledger event idle_seconds must be -1."""
        import json
        from unittest.mock import patch

        from werktools.hub.lifecycle import reap

        sidecar = tmp_path / "procs.json"
        ledger = tmp_path / "ledger.jsonl"

        bad_rec = {
            "server_id": "broken-ts",
            "pid": 99999,
            "pgid": None,
            "state": "live",
            "started_at": "2020-01-01T00:00:00Z",
            "last_used_at": "not-a-date",
            "profile_owner": "p",
        }
        body = {"schema": "hub-procs-v1", "written_at": "2020-01-01T00:00:00Z", "records": [bad_rec]}
        sidecar.write_text(json.dumps(body), encoding="utf-8")

        import time as _time
        now = _time.time()

        # Mock _is_alive to return True so reap treats it as an idle (live) process.
        with patch("werktools.hub.lifecycle._is_alive", return_value=True), \
             pytest.warns(UserWarning, match="unparseable"):
            reaped = reap(now, idle_ttl=0.0, sidecar_path=sidecar, ledger_path=ledger)

        assert len(reaped) == 1
        assert reaped[0]["server_id"] == "broken-ts"

        # Ledger event idle_seconds must be -1 (inf sentinel).
        from werktools.hub.ledger import recent_events
        events = recent_events(ledger, limit=20)
        idle_events = [e for e in events if e.get("payload", {}).get("type") == "process.idle"]
        assert idle_events, "Expected a process.idle ledger event"
        assert idle_events[0]["payload"]["idle_seconds"] == -1


@skip_if_not_windows
class TestKillGroupWindowsTerminateProcessRetry:
    """Pin that a failed TerminateProcess raises OSError and reap preserves the record."""

    def test_terminate_process_failure_raises_oserror(self):
        """_kill_group_windows must raise OSError when TerminateProcess returns 0."""
        from unittest.mock import MagicMock, patch

        from werktools.hub.lifecycle import _kill_group_windows

        # Build a fake kernel32 that makes TerminateProcess return 0 (failure).
        fake_kernel32 = MagicMock()
        fake_kernel32.OpenProcess.return_value = 1  # non-zero handle
        fake_kernel32.TerminateProcess.return_value = 0  # failure
        fake_kernel32.get_last_error = MagicMock(return_value=5)  # ACCESS_DENIED

        with (
            patch("werktools.hub.lifecycle._win_kernel", return_value=fake_kernel32),
            patch("werktools.hub.lifecycle._win_is_alive", return_value=True),
            patch("ctypes.get_last_error", return_value=5),
        ):
            with pytest.raises(OSError):
                _kill_group_windows(12345, job_handle=None)

    def test_reap_preserves_record_when_kill_raises(self, tmp_path):
        """reap() must keep the sidecar record when _kill_group raises (retry guard)."""
        import json as _json
        from unittest.mock import patch

        from werktools.hub.lifecycle import reap

        sidecar = tmp_path / "procs.json"
        ledger = tmp_path / "ledger.jsonl"

        rec = {
            "server_id": "stubborn",
            "pid": 99998,
            "pgid": None,
            "state": "live",
            "started_at": "2020-01-01T00:00:00Z",
            "last_used_at": "2020-01-01T00:00:00Z",
            "profile_owner": "p",
        }
        body = {"schema": "hub-procs-v1", "written_at": "2020-01-01T00:00:00Z", "records": [rec]}
        sidecar.write_text(_json.dumps(body), encoding="utf-8")

        import time as _time

        now = _time.time()

        with (
            patch("werktools.hub.lifecycle._is_alive", return_value=True),
            patch("werktools.hub.lifecycle._kill_group", side_effect=OSError("TerminateProcess failed")),
            pytest.warns(UserWarning, match="kill failed"),
        ):
            reaped = reap(now, idle_ttl=0.0, sidecar_path=sidecar, ledger_path=ledger)

        # kill failed => record preserved in sidecar, not in reaped list
        assert reaped == []
        survivors = _json.loads(sidecar.read_text(encoding="utf-8"))["records"]
        assert any(r["server_id"] == "stubborn" for r in survivors)


@skip_if_windows
class TestKillGroupPosixProcessGroup:
    def test_kill_group_kills_grandchild(self, tmp_path):
        """POSIX: _kill_group_posix with a pgid must kill both child and grandchild."""
        import subprocess

        # Spawn a two-level subprocess tree: child forks grandchild in same session.
        # The grandchild sleeps; the child waits on it.
        script = (
            "import os, subprocess, sys, time\n"
            "try:\n"
            "    os.setsid()\n"
            "except OSError:\n"
            "    pass\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "sys.stdout.write(str(child.pid) + '\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(60)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            start_new_session=True,
        )
        grandchild_pid = int(proc.stdout.readline().decode().strip())
        pgid = os.getpgid(proc.pid)

        from werktools.hub.lifecycle import _kill_group_posix, _posix_is_alive

        _kill_group_posix(proc.pid, pgid=pgid, timeout=5.0)

        # Reap child to avoid zombie state (which would make _posix_is_alive return True)
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass

        assert _poll_dead(proc.pid, _posix_is_alive), "child should be dead"
        assert _poll_dead(grandchild_pid, _posix_is_alive), "grandchild should be dead"


def test_reap_cross_session_windows_emits_warning(tmp_path, monkeypatch):
    """When _IS_WINDOWS=True and no job handle exists, reap must warn about grandchild escape."""
    import unittest.mock as mock
    import warnings

    import werktools.hub.lifecycle as lc

    monkeypatch.setattr(lc, "_IS_WINDOWS", True)
    monkeypatch.setattr(lc, "_WINDOWS_HANDLES", {})

    sidecar = tmp_path / "procs.json"
    ledger = tmp_path / "ledger.jsonl"

    # Write a sidecar record with an imaginary process.
    reg = lc.ProcessRegistry(sidecar)
    import os
    own_pid = os.getpid()
    rec = lc.ProcessRecord(
        server_id="cross-sess",
        pid=own_pid,
        pgid=None,
        state="idle",
        started_at="2020-01-01T00:00:00Z",
        last_used_at="2020-01-01T00:00:00Z",
        profile_owner="test",
    )
    reg.record(rec)
    reg.save_sidecar()

    # Patch _is_alive to return True so the idle path is taken.
    import time
    with mock.patch.object(lc, "_is_alive", return_value=True):
        # Patch _kill_group to avoid actually sending a signal.
        with mock.patch.object(lc, "_kill_group", return_value="ok"):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                lc.reap(time.time(), 0.0, sidecar, ledger)
    messages = [str(w.message) for w in caught]
    assert any("no job handle" in m or "cross-session" in m for m in messages), (
        f"expected cross-session reap warning, got: {messages}"
    )
