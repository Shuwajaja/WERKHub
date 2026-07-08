"""End-to-end CLI smoke for `hub registry build|list|select`."""

from __future__ import annotations

import sys

from werktools.cli import main


def _run(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["werktools", *args])
    try:
        rc = main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 0
    return rc, capsys.readouterr().out


def test_registry_cli_build_list_select(tmp_path, monkeypatch, capsys):
    cfg = str(tmp_path / "hub.json")

    rc, out = _run(monkeypatch, capsys, "--config", cfg, "hub", "registry", "build")
    assert rc == 0
    assert "capabilities" in out
    assert (tmp_path / "registry.db").exists()

    rc, out = _run(monkeypatch, capsys, "--config", cfg, "hub", "registry", "list", "--deluxe")
    assert rc == 0
    assert "deluxe" in out
    assert "mcp-grafana" in out  # a known deluxe entry

    rc, out = _run(monkeypatch, capsys, "--config", cfg, "hub", "registry", "select", "git pull request review on github")
    assert rc == 0
    assert "selected for" in out
    assert "git" in out  # a git/github tool was selected


def test_registry_cli_list_before_build_is_empty(tmp_path, monkeypatch, capsys):
    cfg = str(tmp_path / "hub.json")
    rc, out = _run(monkeypatch, capsys, "--config", cfg, "hub", "registry", "list")
    # no DB yet -> fail-closed, no crash
    assert rc == 0


def test_registry_cli_build_merges_skills(tmp_path, monkeypatch, capsys):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "review-policy.md").write_text(
        "# Review Policy\nrisk: read\ntags: review\n\nReview the policy module.\n", encoding="utf-8"
    )
    cfg = str(tmp_path / "hub.json")
    rc, _ = _run(monkeypatch, capsys, "--config", cfg, "hub", "registry", "build", "--skills-dir", str(skills))
    assert rc == 0
    rc, out = _run(monkeypatch, capsys, "--config", cfg, "hub", "registry", "list", "--category", "review")
    assert rc == 0
    assert "review-policy" in out and "skill" in out  # the skill entered the registry as kind=skill
