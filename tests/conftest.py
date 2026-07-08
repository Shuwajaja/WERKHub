"""Shared offline test fixtures for the werktools suite.

Importable helpers (cassette, fake_process) live alongside this file; the
sys.path insertion below lets them be imported by bare name regardless of
pytest's import mode.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

_IS_WINDOWS = sys.platform.startswith("win")

skip_if_not_windows = pytest.mark.skipif(not _IS_WINDOWS, reason="windows-only behaviour")
skip_if_windows = pytest.mark.skipif(_IS_WINDOWS, reason="posix-only behaviour")


@pytest.fixture
def frozen_clock(monkeypatch):
    """Freeze the single canonical timestamp source used by the ledger."""
    frozen = "2026-01-01T00:00:00Z"
    monkeypatch.setattr("werktools.ledger._now_iso", lambda: frozen)
    return frozen


@pytest.fixture
def fake_server_process():
    """Spawn a real, deterministic long-lived child; reap it on teardown.

    Used by lifecycle/warm-pool tests that need a genuine PID and process
    group without depending on a downstream MCP. The child loops forever
    until terminated.
    """
    procs: list[subprocess.Popen] = []

    def _spawn():
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if _IS_WINDOWS else 0
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time\nwhile True:\n    time.sleep(1)"],
            creationflags=creationflags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        procs.append(proc)
        return proc

    yield _spawn

    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    pass


@pytest.fixture
def tmp_hub_config(tmp_path):
    """Write the default hub config to a temp path and return (path, config)."""
    from werktools.hub.registry import save_default_config

    path = tmp_path / "hub.json"
    config = save_default_config(path)
    return path, config
