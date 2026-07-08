import sys

from werktools.hub.contracts import DownstreamServer
from werktools.hub.lifecycle import ProcessRegistry, _is_alive, spawn
from werktools.hub.pool import PoolEntry, WarmPool


def _entry(server_id="a", pid=None):
    return PoolEntry(server_id=server_id, pid=pid, name=server_id, started_at="t", last_used_at="t")


def test_put_get():
    pool = WarmPool()
    pool.put(_entry("a"))
    assert pool.get("a").server_id == "a"


def test_get_missing_is_none():
    assert WarmPool().get("nope") is None


def test_mark_used_and_idle():
    pool = WarmPool()
    pool.put(_entry("a"))
    pool.mark_idle("a")
    assert pool.get("a").state == "idle"
    pool.mark_used("a")
    assert pool.get("a").state == "warm"


def test_kill_pid_none_returns_false_no_raise():
    pool = WarmPool()
    pool.put(_entry("a", pid=None))
    assert pool.kill("a") is False
    assert pool.get("a").state == "dead"


def test_kill_sets_dead(monkeypatch):
    import werktools.hub.pool as poolmod

    killed = {}

    def fake_term(pid):
        killed["pid"] = pid
        return True

    monkeypatch.setattr(poolmod, "_win32_terminate", fake_term)
    monkeypatch.setattr(poolmod, "_IS_WINDOWS", True)
    pool = WarmPool()
    pool.put(_entry("a", pid=4321))
    assert pool.kill("a") is True
    assert killed["pid"] == 4321
    assert pool.get("a").state == "dead"


def test_kill_all(monkeypatch):
    import werktools.hub.pool as poolmod

    monkeypatch.setattr(poolmod, "_win32_terminate", lambda pid: True)
    monkeypatch.setattr(poolmod, "_IS_WINDOWS", True)
    pool = WarmPool()
    pool.put(_entry("a", pid=1))
    pool.put(_entry("b", pid=2))
    pool.kill_all()
    assert all(e.state == "dead" for e in pool.entries())


def test_remove():
    pool = WarmPool()
    pool.put(_entry("a"))
    pool.remove("a")
    assert pool.get("a") is None


def test_entries_snapshot():
    pool = WarmPool()
    pool.put(_entry("a"))
    snap = pool.entries()
    pool.put(_entry("b"))
    assert len(snap) == 1


def test_four_thread_safe():
    import threading

    pool = WarmPool()

    def worker(n):
        for i in range(25):
            pool.put(_entry(f"{n}-{i}"))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(pool.entries()) == 100


def test_kill_terminates_real_subprocess(tmp_path):
    # proven on a REAL process: the pool's kill actually terminates it
    reg = ProcessRegistry(tmp_path / "p.json")
    sleeper = DownstreamServer(id="s", command=sys.executable, args=("-c", "import time\nwhile True:\n    time.sleep(1)"))
    rec = spawn(sleeper, reg, "p")
    pool = WarmPool()
    pool.put(_entry("s", pid=rec.pid))

    assert _is_alive(rec.pid)
    assert pool.kill("s") is True

    import time

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _is_alive(rec.pid):
        time.sleep(0.05)
    assert not _is_alive(rec.pid)
    reg.mark_dead("s")
