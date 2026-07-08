from werktools.cli import main
from werktools.hub.invariants import (
    check_core_dependencies_empty,
    check_event_names_unique,
    check_lifecycle_extra_empty,
    check_no_in_core_daemon,
    check_no_module_scope_fastmcp,
    run_all,
)


def test_current_tree_holds_all_invariants():
    results = run_all()
    failing = {k: v for k, v in results.items() if v}
    assert failing == {}, f"invariant violations on the current tree: {failing}"


def test_dependencies_empty():
    assert check_core_dependencies_empty() == []


def test_lifecycle_extra_empty():
    assert check_lifecycle_extra_empty() == []


def test_no_module_scope_fastmcp_on_tree():
    assert check_no_module_scope_fastmcp() == []


def test_no_in_core_daemon_on_tree():
    assert check_no_in_core_daemon() == []


def test_event_names_unique():
    assert check_event_names_unique() == []


def test_fastmcp_scan_flags_a_bad_file(tmp_path):
    pkg = tmp_path / "werktools"
    pkg.mkdir()
    (pkg / "rogue.py").write_text("import fastmcp\n", encoding="utf-8")

    violations = check_no_module_scope_fastmcp(src_root=tmp_path, exclude=set())

    assert any("rogue.py" in v for v in violations)


def test_daemon_scan_flags_while_true(tmp_path):
    pkg = tmp_path / "werktools"
    pkg.mkdir()
    (pkg / "loopy.py").write_text("def go():\n    while True:\n        pass\n", encoding="utf-8")

    violations = check_no_in_core_daemon(src_root=tmp_path)

    assert any("loopy.py" in v for v in violations)


def test_hub_doctor_cli_passes_on_clean_tree(tmp_path, capsys):
    code = main(["--config", str(tmp_path / "hub.json"), "hub", "doctor"])

    out = capsys.readouterr().out
    assert code == 0
    assert "all invariants hold" in out


def test_fastmcp_scan_flags_indented_try_block_import(tmp_path):
    # SF8 part 1: the codebase's canonical form is an INDENTED `try: from
    # fastmcp import ...` block (column 4). The old `^...` regex matched
    # nothing; `^\s*...` must now detect it.
    pkg = tmp_path / "werktools"
    pkg.mkdir()
    (pkg / "rogue_relay.py").write_text(
        "try:\n    from fastmcp import Client\nexcept ImportError:\n    raise\n",
        encoding="utf-8",
    )

    violations = check_no_module_scope_fastmcp(src_root=tmp_path, exclude=set())

    assert any("rogue_relay.py" in v for v in violations)


def test_fastmcp_allowlist_is_exact_not_suffix(tmp_path):
    # SF8 part 2: an allowlist of the exact rel-path 'server.py' must NOT
    # exempt a nested 'plugins/server.py' (the old suffix-match did).
    pkg = tmp_path / "werktools" / "plugins"
    pkg.mkdir(parents=True)
    (pkg / "server.py").write_text("import fastmcp\n", encoding="utf-8")

    violations = check_no_module_scope_fastmcp(src_root=tmp_path, exclude={"server.py"})

    assert any("plugins/server.py" in v for v in violations)


def test_real_tree_fastmcp_entry_files_stay_allowed():
    # The two real indented fastmcp imports (server.py, hub/relay.py) must
    # remain allowlisted under the exact-match allowlist + indented-aware
    # regex, so hub doctor stays green.
    assert check_no_module_scope_fastmcp() == []


def test_daemon_scan_ignores_comments(tmp_path):
    """check_no_in_core_daemon must not flag daemon patterns inside comments (Fix 21)."""
    pkg = tmp_path / "werktools"
    pkg.mkdir()
    # A file that only references 'while True' inside a comment must not trigger.
    (pkg / "clean.py").write_text(
        "# while True is forbidden in core modules\n"
        "def foo():\n    pass\n",
        encoding="utf-8",
    )

    violations = check_no_in_core_daemon(src_root=tmp_path, exclude=set())

    assert violations == [], f"comment-only daemon reference should not trigger: {violations}"


def test_daemon_scan_ignores_string_literals(tmp_path):
    """check_no_in_core_daemon must not flag daemon patterns inside string literals."""
    from werktools.hub.invariants import check_no_in_core_daemon

    pkg = tmp_path / "werktools"
    pkg.mkdir()
    # A docstring that contains 'while True' must not trigger.
    (pkg / "docstring_ref.py").write_text(
        '"""No daemon: while True is forbidden here."""\n'
        "def foo():\n    pass\n",
        encoding="utf-8",
    )
    violations = check_no_in_core_daemon(src_root=tmp_path, exclude=set())
    assert violations == [], f"string-literal daemon reference should not trigger: {violations}"


def test_runtimes_subprocess_deferred_passes_on_real_codebase():
    """check_runtimes_subprocess_deferred must pass on the current codebase."""
    from werktools.hub.invariants import check_runtimes_subprocess_deferred

    violations = check_runtimes_subprocess_deferred()
    assert violations == [], f"runtimes subprocess check failed: {violations}"


def test_run_all_includes_runtimes_check():
    """run_all() must include the runtimes_subprocess_deferred check."""
    from werktools.hub.invariants import run_all

    result = run_all()
    assert "runtimes_subprocess_deferred" in result
    assert result["runtimes_subprocess_deferred"] == []
