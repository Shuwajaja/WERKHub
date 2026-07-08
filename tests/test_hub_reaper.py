import sys
import time

import pytest

from werktools.hub.contracts import DownstreamServer
from werktools.hub.ledger import recent_events
from werktools.hub.lifecycle import (
    ProcessRegistry,
    _is_alive,
    _kill_group,
    orphan_sweep,
    reap,
    spawn,
)

_SLEEP = DownstreamServer(id="sleeper", command=sys.executable, args=("-c", "import time\nwhile True:\n    time.sleep(1)"))


def _poll_dead(pid, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            return True
        time.sleep(0.05)
    return False


def _kill_record(rec):
    _kill_group(rec.pid, rec.pgid, rec.job_handle)


def test_is_alive_detects_dead(tmp_path):
    reg = ProcessRegistry(tmp_path / "p.json")
    quick = DownstreamServer(id="q", command=sys.executable, args=("-c", "pass"))
    rec = spawn(quick, reg, "p")
    assert _poll_dead(rec.pid)
    assert _is_alive(rec.pid) is False
    assert _is_alive(0x7FFFFFFE) is False


def test_kill_group_terminates_real_child(tmp_path):
    reg = ProcessRegistry(tmp_path / "p.json")
    rec = spawn(_SLEEP, reg, "p")
    status = _kill_group(rec.pid, rec.pgid, rec.job_handle)
    assert status in ("sigterm", "sigkill", "win_terminate", "win_job")
    assert _poll_dead(rec.pid)


def test_kill_group_already_dead(tmp_path):
    reg = ProcessRegistry(tmp_path / "p.json")
    quick = DownstreamServer(id="q", command=sys.executable, args=("-c", "pass"))
    rec = spawn(quick, reg, "p")
    assert _poll_dead(rec.pid)
    assert _kill_group(rec.pid, rec.pgid, rec.job_handle) == "already_dead"


def test_reap_kills_idle_and_ledgers(tmp_path):
    sidecar = tmp_path / "hub-procs.json"
    ledger = tmp_path / "ledger.jsonl"
    reg = ProcessRegistry(sidecar)
    rec = spawn(_SLEEP, reg, "p")
    # backdate last_used so it counts as idle under a 1s ttl.
    # Use record() to replace the entry since get() now returns a copy.
    import dataclasses as _dc
    backdated = _dc.replace(rec, last_used_at="2020-01-01T00:00:00Z")
    reg.record(backdated)
    reg.save_sidecar()

    reaped = reap(time.time(), 1.0, sidecar, ledger)

    assert [r["reason"] for r in reaped] == ["idle"]
    assert _poll_dead(rec.pid)
    types = [e["payload"]["type"] for e in recent_events(ledger, limit=10)]
    assert "process.idle" in types and "process.killed" in types
    # reaped record removed from the sidecar
    assert ProcessRegistry(sidecar).load_sidecar() == []


def test_reap_marks_dead_record(tmp_path):
    sidecar = tmp_path / "hub-procs.json"
    ledger = tmp_path / "ledger.jsonl"
    reg = ProcessRegistry(sidecar)
    quick = DownstreamServer(id="q", command=sys.executable, args=("-c", "pass"))
    rec = spawn(quick, reg, "p")
    assert _poll_dead(rec.pid)
    reg.save_sidecar()

    reaped = reap(time.time(), 9999.0, sidecar, ledger)

    assert reaped[0]["reason"] == "dead"
    assert reaped[0]["kill_status"] == "already_dead"


def test_reap_skips_live_not_idle(tmp_path):
    sidecar = tmp_path / "hub-procs.json"
    ledger = tmp_path / "ledger.jsonl"
    reg = ProcessRegistry(sidecar)
    rec = spawn(_SLEEP, reg, "p")
    reg.save_sidecar()
    try:
        reaped = reap(time.time(), 9999.0, sidecar, ledger)
        assert reaped == []
        assert _is_alive(rec.pid)
        assert not ledger.exists()
    finally:
        _kill_record(reg.get(rec.server_id))


def test_orphan_sweep_overrides_reason(tmp_path):
    sidecar = tmp_path / "hub-procs.json"
    ledger = tmp_path / "ledger.jsonl"
    reg = ProcessRegistry(sidecar)
    rec = spawn(_SLEEP, reg, "p")
    reg.save_sidecar()

    reaped = orphan_sweep(sidecar, ledger)

    assert [r["reason"] for r in reaped] == ["orphan"]
    assert _poll_dead(rec.pid)


def test_reap_empty_sidecar_no_ledger(tmp_path):
    sidecar = tmp_path / "hub-procs.json"
    ledger = tmp_path / "ledger.jsonl"

    assert reap(time.time(), 0.0, sidecar, ledger) == []
    assert not ledger.exists()


# ---------------------------------------------------------------------------
# Regression test: kill failure preserves survivor and emits warning
# ---------------------------------------------------------------------------

def test_reap_kill_failure_preserves_survivor_and_warns(tmp_path, monkeypatch):
    # When _kill_group raises an exception the record must be added to survivors
    # (not silently dropped) and warnings.warn must fire with the error detail.
    import json

    from werktools.hub import lifecycle

    sidecar = tmp_path / "procs.json"
    ledger = tmp_path / "ledger.jsonl"

    # Patch _is_alive -> True so the record is treated as alive+idle
    monkeypatch.setattr(lifecycle, "_is_alive", lambda pid: True)

    # Patch _kill_group to always raise
    def _boom(pid, pgid, job_handle, timeout=5.0):
        raise RuntimeError("simulated kill failure")

    monkeypatch.setattr(lifecycle, "_kill_group", _boom)

    # Plant a record with a backdated last_used_at so idle_ttl=0 triggers a kill
    raw = {
        "schema": "hub-procs-v1",
        "written_at": "2020-01-01T00:00:00Z",
        "records": [
            {
                "server_id": "s1",
                "pid": 9999,
                "pgid": None,
                "state": "live",
                "started_at": "2020-01-01T00:00:00Z",
                "last_used_at": "2020-01-01T00:00:00Z",
                "profile_owner": "p",
            }
        ],
    }
    sidecar.write_text(json.dumps(raw), encoding="utf-8")

    import time
    with pytest.warns(UserWarning, match="kill failed"):
        reaped = reap(time.time(), idle_ttl=0.0, sidecar_path=sidecar, ledger_path=ledger)

    # The record was NOT reported as successfully reaped
    assert reaped == []

    # The survivor was written back to the sidecar
    from werktools.hub.lifecycle import ProcessRegistry
    survivors = ProcessRegistry(sidecar).load_sidecar()
    assert any(r.server_id == "s1" for r in survivors)


def test_cli_reap_empty(tmp_path, capsys):
    import json

    from werktools.cli import main
    from werktools.hub.registry import default_config

    config = tmp_path / "hub.json"
    body = default_config().to_dict()
    body["ledger_path"] = str(tmp_path / "hub-ledger.jsonl")
    config.write_text(json.dumps(body), encoding="utf-8")

    code = main(["--config", str(config), "hub", "reap"])

    assert code == 0
    assert "total reaped: 0" in capsys.readouterr().out
