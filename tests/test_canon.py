from pathlib import Path

from werktools.tools.canon import (
    check_agents_refs,
    check_canon,
    check_canon_docs,
    check_cli_commands,
    check_design_tokens,
    check_spec_refs,
    gen_agents_md,
    gen_spec_template,
)


def _repo(tmp_path, agents=True, readme=True, changelog=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    if agents:
        (tmp_path / "AGENTS.md").write_text("# AGENTS.md\n\n## Hard Guardrail\n\n- only here\n", encoding="utf-8")
    if readme:
        (tmp_path / "README.md").write_text("# r\n", encoding="utf-8")
    if changelog:
        (tmp_path / "CHANGELOG.md").write_text("# c\n", encoding="utf-8")
    return tmp_path


def test_missing_agents_is_error(tmp_path):
    _repo(tmp_path, agents=False)
    issues = check_canon_docs(tmp_path)
    assert any(i.kind == "missing_doc" and i.severity == "error" and i.target == "AGENTS.md" for i in issues)


def test_all_present_no_issue(tmp_path):
    _repo(tmp_path)
    assert check_canon_docs(tmp_path) == []


def test_missing_changelog_is_warning(tmp_path):
    _repo(tmp_path, changelog=False)
    issues = check_canon_docs(tmp_path)
    assert any(i.target == "CHANGELOG.md" and i.severity == "warning" for i in issues)


def test_agents_broken_ref_flagged(tmp_path):
    (tmp_path / "AGENTS.md").write_text("see `src/missing/thing.py` for details\n", encoding="utf-8")
    issues = check_agents_refs(tmp_path)
    assert any(i.kind == "broken_ref" for i in issues)


def test_agents_existing_ref_ok(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "real.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("see `sub/real.py`\n", encoding="utf-8")
    assert check_agents_refs(tmp_path) == []


def test_agents_windows_absolute_skipped(tmp_path):
    (tmp_path / "AGENTS.md").write_text("see `C:\\\\Workplace\\\\other`\n", encoding="utf-8")
    assert check_agents_refs(tmp_path) == []


def test_cli_documented_missing_is_warning(tmp_path):
    src = tmp_path / "src" / "werktools"
    src.mkdir(parents=True)
    (src / "cli.py").write_text('p.add_parser("hub")\np.add_parser("truth")\n', encoding="utf-8")
    issues, found = check_cli_commands(tmp_path, documented={"hub", "ghost"})
    assert "hub" in found
    assert any(i.target == "ghost" and i.severity == "warning" for i in issues)


def test_design_tokens_unused_warns(tmp_path):
    (tmp_path / "DESIGN.md").write_text("| `used_token` | x |\n| `dead_token` | y |\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("used_token = 1\n", encoding="utf-8")
    issues, tokens = check_design_tokens(tmp_path)
    assert "used_token" in tokens
    assert any(i.target == "dead_token" for i in issues)


def test_spec_refs_missing_path_is_error(tmp_path):
    (tmp_path / "tasks.md").write_text("- [ ] edit `src/does/not/exist.py`\n", encoding="utf-8")
    issues = check_spec_refs(tmp_path)
    assert any(i.severity == "error" and i.kind == "broken_ref" for i in issues)


def test_check_canon_clean_vs_stale(tmp_path):
    _repo(tmp_path)
    assert check_canon(tmp_path).ok is True

    stale = _repo(tmp_path / "stale", agents=False)
    report = check_canon(stale)
    assert report.ok is False
    assert report.errors


def test_gen_agents_md_required_sections():
    text = gen_agents_md(Path("/x/proj"), project_name="proj", extra_guardrails=["never touch prod"])
    assert "## Hard Guardrail" in text
    assert "## Build Conventions" in text
    assert "## Style" in text
    assert "never touch prod" in text


def test_gen_agents_md_then_canon_clean(tmp_path):
    _repo(tmp_path, agents=False)
    (tmp_path / "AGENTS.md").write_text(gen_agents_md(tmp_path), encoding="utf-8")
    assert not any(i.target == "AGENTS.md" for i in check_canon_docs(tmp_path))


def test_gen_spec_template():
    files = gen_spec_template("my-tool", kind="mcp", tool_names=("foo",))
    assert set(files) == {"requirements.md", "design.md", "tasks.md"}
    assert "my-tool" in files["requirements.md"]
    assert "## MCP Tools" in files["design.md"]
    cli_kind = gen_spec_template("x", kind="cli")
    assert "## MCP Tools" not in cli_kind["design.md"]


def test_cli_canon_check_and_gen(tmp_path, capsys):
    from werktools.cli import main

    _repo(tmp_path)
    assert main(["canon", "check", "--repo", str(tmp_path)]) == 0
    assert "OK" in capsys.readouterr().out

    _repo(tmp_path / "bad", agents=False)
    assert main(["canon", "check", "--repo", str(tmp_path / "bad")]) == 1
    assert "FAIL" in capsys.readouterr().out

    assert main(["canon", "gen", "agents", "--repo", str(tmp_path), "--dry-run"]) == 0
    assert "## Hard Guardrail" in capsys.readouterr().out

    assert main(["canon", "gen", "spec", "thing", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "requirements.md" in out and "tasks.md" in out


def test_real_repo_canon_check_passes():
    report = check_canon(Path(__file__).parent.parent)
    assert report.ok is True
