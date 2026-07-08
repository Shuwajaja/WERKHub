from werktools.cli import main
from werktools.tools.truth import (
    check_claims,
    collect_claims,
    extract_claims,
    scan_repo,
    summarize_checks,
)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scan_repo_ignores_vendored_and_env_dirs(tmp_path):
    _write(tmp_path / "src" / "app.py", "x = 1\n")
    _write(tmp_path / "node_modules" / "pkg" / "setup.py", "x = 1\n")
    _write(tmp_path / "node_modules" / "pkg" / "README.md", "# vendored\n")
    _write(tmp_path / ".venv" / "lib" / "mod.py", "x = 1\n")

    facts = scan_repo(tmp_path)

    assert facts.python_files == ("src/app.py",)
    assert facts.markdown_files == ()


def test_collect_claims_skips_undecodable_markdown(tmp_path):
    _write(tmp_path / "good.md", "see `good.md`")
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe broken \x9c bytes")

    claims = collect_claims(tmp_path)

    assert any(claim.source == "good.md" for claim in claims)


def test_scan_repo_finds_local_facts(tmp_path):
    _write(
        tmp_path / "pyproject.toml",
        """
[project]
name = "demo"
dependencies = []

[project.scripts]
demo = "demo.cli:main"
""".strip(),
    )
    _write(tmp_path / "README.md", "# Demo\n")
    _write(tmp_path / "src" / "demo" / "thing.py", "")
    _write(tmp_path / "tests" / "test_thing.py", "")

    facts = scan_repo(tmp_path)

    assert facts.project_name == "demo"
    assert facts.dependencies_empty is True
    assert "demo" in facts.console_scripts
    assert "README.md" in facts.markdown_files
    assert "src/demo/thing.py" in facts.python_files
    assert "tests/test_thing.py" in facts.test_files


def test_extract_claims_finds_path_and_url_claims():
    claims = extract_claims(
        "README.md",
        "See `src/demo/thing.py` and https://example.com/spec for source.",
    )

    assert [claim.kind for claim in claims] == ["path", "external_url"]
    assert claims[0].target == "src/demo/thing.py"
    assert claims[1].target == "https://example.com/spec"


def test_check_claims_marks_existing_paths_verified(tmp_path):
    _write(tmp_path / "README.md", "See `src/demo/thing.py`.")
    _write(tmp_path / "src" / "demo" / "thing.py", "")

    claims = extract_claims("README.md", (tmp_path / "README.md").read_text())
    checks = check_claims(tmp_path, claims)

    assert checks[0].label == "code_verified"


def test_check_claims_marks_missing_paths_contradicted(tmp_path):
    _write(tmp_path / "README.md", "See `src/demo/missing.py`.")

    claims = extract_claims("README.md", (tmp_path / "README.md").read_text())
    checks = check_claims(tmp_path, claims)

    assert checks[0].label == "contradicted"


def test_external_urls_are_source_provided_not_code_verified(tmp_path):
    claims = extract_claims("README.md", "Source: https://example.com/spec")

    checks = check_claims(tmp_path, claims)

    assert checks[0].label == "source_provided"


def test_summarize_checks_returns_stable_counts(tmp_path):
    _write(tmp_path / "README.md", "`README.md` `missing.md` https://example.com")
    claims = extract_claims("README.md", (tmp_path / "README.md").read_text())

    summary = summarize_checks(check_claims(tmp_path, claims))

    assert summary["code_verified"] == 1
    assert summary["contradicted"] == 1
    assert summary["source_provided"] == 1


def test_truth_scan_cli_prints_counts(tmp_path, capsys):
    _write(tmp_path / "pyproject.toml", "[project]\nname = \"demo\"\ndependencies = []\n")
    _write(tmp_path / "README.md", "# Demo\n")
    _write(tmp_path / "tests" / "test_demo.py", "")

    code = main(["truth", "scan", "--repo", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Markdown:" in out
    assert "Tests:" in out
    assert "Project: demo" in out


def test_iter_files_os_walk_error_warns(tmp_path, monkeypatch):
    # _iter_files must emit a UserWarning when os.walk reports an OS error via
    # the onerror callback, and still return the files it found.
    import os as _os
    import warnings as _warnings

    from werktools.tools import truth

    (tmp_path / "good.md").write_text("# doc", encoding="utf-8")

    real_walk = _os.walk

    def patched_walk(root, onerror=None):
        yield from real_walk(root)
        if onerror:
            onerror(OSError(13, "Permission denied", str(tmp_path / "locked")))

    monkeypatch.setattr(truth, "os", type("_FakeOs", (), {
        "walk": staticmethod(patched_walk),
    })())

    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        result = truth._iter_files(tmp_path, ".md")

    warning_messages = [str(w.message) for w in caught]
    assert any("directory error" in m.lower() for m in warning_messages), (
        f"expected a 'directory error' warning; got: {warning_messages}"
    )
    assert any("good.md" in f for f in result)


def test_truth_report_cli_writes_markdown_without_modifying_source(tmp_path):
    readme = tmp_path / "README.md"
    _write(readme, "See `missing.md` and https://example.com/source")
    before = readme.read_text(encoding="utf-8")
    out_path = tmp_path / "TRUTH_REPORT.md"

    code = main(["truth", "report", "--repo", str(tmp_path), "--out", str(out_path)])

    assert code == 0
    assert readme.read_text(encoding="utf-8") == before
    report = out_path.read_text(encoding="utf-8")
    assert "Truth Auditor Report" in report
    assert "contradicted" in report
    assert "source_provided" in report
