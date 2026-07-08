"""Unit tests for hub/runtimes.py host-detection (TDD — written first).

All tests use monkeypatch + tmp_path; zero real filesystem reads outside
tmp_path and zero real subprocess calls. Token VALUES are never read by the
probe — only presence (env name in os.environ) and file existence/mtime.
"""

import dataclasses
import json
import subprocess

import pytest

from werktools.hub import runtimes
from werktools.hub.runtimes import (
    DESCRIPTORS,
    RuntimeDescriptor,
    RuntimeProbe,
    RuntimeReport,
    get_descriptor,
    probe_all,
    probe_one,
)

CANONICAL_ROSTER = (
    "claude",
    "codex",
    "cursor",
    "vscode",
    "windsurf",
    "goose",
    "gemini",
    "kimi",
    "antigravity",
)


def _desc(**overrides) -> RuntimeDescriptor:
    base = dict(
        host_id="test",
        display_name="Test Host",
        binary_names=(),
        gui_install_paths_windows=(),
        gui_install_paths_posix=(),
        config_paths=(),
        version_cmd=(),
        token_env_vars=(),
        token_file_paths=(),
    )
    base.update(overrides)
    return RuntimeDescriptor(**base)


# ── registry ────────────────────────────────────────────────────────────────


def test_descriptors_cover_canonical_nine_host_roster():
    ids = tuple(d.host_id for d in DESCRIPTORS)
    assert len(DESCRIPTORS) == 9
    assert set(ids) == set(CANONICAL_ROSTER)


def test_get_descriptor_returns_named_host():
    assert get_descriptor("claude").host_id == "claude"
    assert get_descriptor("claude").display_name


def test_get_descriptor_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        get_descriptor("does-not-exist")


def test_goose_and_gemini_marked_at_risk_others_not():
    assert get_descriptor("goose").at_risk is True
    assert get_descriptor("gemini").at_risk is True
    assert get_descriptor("claude").at_risk is False
    assert get_descriptor("codex").at_risk is False


def test_every_descriptor_has_a_monogram_and_no_empty_binary_list():
    for d in DESCRIPTORS:
        assert d.monogram, f"{d.host_id} needs a monochrome monogram placeholder"
        assert d.binary_names, f"{d.host_id} needs at least one binary name"


# ── binary detection ──────────────────────────────────────────────────────────


def test_probe_one_binary_found(monkeypatch):
    monkeypatch.setattr(
        runtimes.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None
    )
    probe = probe_one(_desc(binary_names=("claude",)))
    assert probe.binary_found is True
    assert probe.binary_path == "/usr/bin/claude"
    assert probe.detected is True


def test_probe_one_binary_missing(monkeypatch):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)
    probe = probe_one(_desc(binary_names=("nope",)))
    assert probe.binary_found is False
    assert probe.binary_path is None


def test_probe_one_first_binary_name_wins(monkeypatch):
    monkeypatch.setattr(
        runtimes.shutil, "which", lambda name: "/opt/second" if name == "second" else None
    )
    probe = probe_one(_desc(binary_names=("first", "second")))
    assert probe.binary_path == "/opt/second"


# ── gui / config / detection ─────────────────────────────────────────────────


def test_probe_one_gui_path_detected(monkeypatch, tmp_path):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)
    gui = tmp_path / "app-install"
    gui.mkdir()
    # put the same absolute path in both lists so the OS branch is irrelevant
    probe = probe_one(
        _desc(
            gui_install_paths_windows=(str(gui),),
            gui_install_paths_posix=(str(gui),),
        )
    )
    assert probe.gui_path_found == str(gui)
    assert probe.detected is True


def test_probe_one_config_path_detected_sets_detected(monkeypatch, tmp_path):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    probe = probe_one(_desc(config_paths=(str(cfg),)))
    assert probe.config_path_found == str(cfg)
    assert probe.binary_found is False
    assert probe.gui_path_found is None
    assert probe.detected is True


def test_probe_one_detected_false_when_nothing_found(monkeypatch):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)
    probe = probe_one(_desc(binary_names=("ghost",), config_paths=("/no/such/path/x.json",)))
    assert probe.detected is False


# ── token health: presence/date ONLY, never the value ─────────────────────────


def test_probe_one_token_env_present_without_reading_value(monkeypatch):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)
    monkeypatch.setenv("WERK_TEST_TOKEN", "super-secret-sentinel-value")
    probe = probe_one(_desc(token_env_vars=("WERK_TEST_TOKEN",)))
    assert probe.token_env_present is True
    # the secret value must never surface in any field of the probe
    assert "super-secret-sentinel-value" not in json.dumps(probe.to_dict())


def test_probe_one_token_env_absent(monkeypatch):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)
    monkeypatch.delenv("WERK_TEST_TOKEN", raising=False)
    probe = probe_one(_desc(token_env_vars=("WERK_TEST_TOKEN",)))
    assert probe.token_env_present is False


def test_probe_one_token_file_presence_and_mtime(monkeypatch, tmp_path):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)
    token = tmp_path / ".credentials.json"
    token.write_text("{}", encoding="utf-8")
    probe = probe_one(_desc(token_file_paths=(str(token),)))
    assert probe.token_file_present is True
    assert isinstance(probe.token_file_mtime, float)


def test_probe_one_token_file_absent(monkeypatch):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)
    probe = probe_one(_desc(token_file_paths=("/no/such/token/file.json",)))
    assert probe.token_file_present is False
    assert probe.token_file_mtime is None


def test_probe_one_token_file_content_never_read(monkeypatch, tmp_path):
    # presence/date only — the file CONTENT (a secret) must never surface
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)
    token = tmp_path / ".credentials.json"
    token.write_text("SUPER-SECRET-FILE-CONTENT", encoding="utf-8")
    probe = probe_one(_desc(token_file_paths=(str(token),)))
    assert probe.token_file_present is True
    assert "SUPER-SECRET-FILE-CONTENT" not in json.dumps(probe.to_dict())


# ── version probe deferred behind probe_versions=True ─────────────────────────


def test_version_probe_not_run_by_default(monkeypatch):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)

    def _boom(*args, **kwargs):
        raise AssertionError("subprocess must NOT run unless probe_versions=True")

    monkeypatch.setattr(subprocess, "run", _boom)
    probe = probe_one(_desc(version_cmd=("claude", "--version")))
    assert probe.version_str is None
    assert probe.version_error is None


def test_version_probe_runs_when_opted_in(monkeypatch):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)

    class _Result:
        returncode = 0
        stdout = "claude 1.2.3\nextra line\n"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    probe = probe_one(_desc(version_cmd=("claude", "--version")), probe_versions=True)
    assert probe.version_str == "claude 1.2.3"
    assert probe.version_error is None


def test_version_probe_timeout_is_isolated(monkeypatch):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)

    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=5)

    monkeypatch.setattr(subprocess, "run", _timeout)
    probe = probe_one(_desc(version_cmd=("claude", "--version")), probe_versions=True)
    assert probe.version_str is None
    assert probe.version_error is not None


def test_version_probe_oserror_is_isolated(monkeypatch):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)

    def _oserr(*a, **k):
        raise OSError("command not found")

    monkeypatch.setattr(subprocess, "run", _oserr)
    probe = probe_one(_desc(version_cmd=("x", "--version")), probe_versions=True)
    assert probe.version_str is None
    assert probe.version_error is not None


def test_version_probe_nonzero_exit_captures_stderr(monkeypatch):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)

    class _Result:
        returncode = 1
        stdout = ""
        stderr = "permission denied"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    probe = probe_one(_desc(version_cmd=("x", "--version")), probe_versions=True)
    assert probe.version_str is None
    assert "permission denied" in probe.version_error


def test_version_probe_skipped_when_cmd_empty(monkeypatch):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)

    def _boom(*args, **kwargs):
        raise AssertionError("no version_cmd means no subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    probe = probe_one(_desc(version_cmd=()), probe_versions=True)
    assert probe.version_str is None


# ── probe_all / report ────────────────────────────────────────────────────────


def test_probe_all_one_probe_per_descriptor(monkeypatch):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)
    report = probe_all()
    assert isinstance(report, RuntimeReport)
    assert len(report.probes) == len(DESCRIPTORS)
    assert report.generated_at.endswith("Z")
    assert report.probe_versions is False


def test_probe_all_never_runs_subprocess_by_default(monkeypatch):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)

    def _boom(*args, **kwargs):
        raise AssertionError("hub doctor / default probe must be subprocess-free")

    monkeypatch.setattr(subprocess, "run", _boom)
    probe_all()  # must not raise


def test_detected_hosts_lists_only_detected(monkeypatch):
    monkeypatch.setattr(
        runtimes.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None
    )
    report = probe_all()
    detected = report.detected_hosts()
    assert "claude" in detected
    for host in detected:
        probe = next(p for p in report.probes if p.host_id == host)
        assert probe.detected is True


def test_report_to_dict_is_json_serializable(monkeypatch):
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)
    report = probe_all()
    text = json.dumps(report.to_dict())
    assert "generated_at" in text
    assert isinstance(report.to_dict()["probes"], list)


def test_probe_is_frozen():
    probe = RuntimeProbe(
        host_id="x",
        binary_found=False,
        binary_path=None,
        gui_path_found=None,
        config_path_found=None,
        version_str=None,
        version_error=None,
        token_env_present=False,
        token_file_present=False,
        token_file_mtime=None,
        detected=False,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        probe.host_id = "y"  # type: ignore[misc]


def test_version_stderr_ansi_stripped(monkeypatch):
    """ANSI escape sequences in stderr must be stripped from version_error."""
    monkeypatch.setattr(runtimes.shutil, "which", lambda name: None)

    class _Result:
        returncode = 1
        stdout = ""
        stderr = "\x1b[31mError:\x1b[0m permission denied\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    probe = probe_one(_desc(version_cmd=("x", "--version")), probe_versions=True)
    assert probe.version_error is not None
    assert "\x1b" not in probe.version_error, "ANSI escape should be stripped from stderr"
    assert "permission denied" in probe.version_error


def test_probe_all_isolates_per_probe_exception(monkeypatch):
    """probe_all must return a full report even if one probe_one raises (Fix 23)."""
    import warnings as _warnings

    call_count = {"n": 0}
    original_probe_one = probe_one

    def failing_probe_one(d, *, probe_versions=False):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated probe failure")
        return original_probe_one(d, probe_versions=probe_versions)

    monkeypatch.setattr(runtimes, "probe_one", failing_probe_one)

    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        report = probe_all(probe_versions=False)

    # All descriptors must be represented in the report
    assert len(report.probes) == len(DESCRIPTORS)
    # The first probe (which raised) must appear as undetected with a version_error
    first = report.probes[0]
    assert first.detected is False
    assert first.version_error is not None
    # A warning must have been emitted for the failing probe
    assert any("probe_one" in str(w.message) or "simulated" in str(w.message) for w in caught), (
        f"expected a warning about the failing probe; got: {[str(w.message) for w in caught]}"
    )
