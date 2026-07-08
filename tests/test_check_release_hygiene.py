"""Tests for scripts/check_release_hygiene.py.

Uses tmp_path fixtures to avoid touching the real repo state.
All personal-path fixtures are synthetic; no real personal data is included.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the scripts/ dir importable regardless of install state.
REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_release_hygiene as hyg  # noqa: E402, I001


# ---------------------------------------------------------------------------
# check_personal_paths
# ---------------------------------------------------------------------------


def test_personal_path_windows_slash_detected(tmp_path: Path) -> None:
    f = tmp_path / "README.md"
    f.write_text("see C:/Users/devuser/projects for details", encoding="utf-8")
    findings = hyg.check_personal_paths([f])
    assert any("FAIL" == sev for sev, _, _ in findings)
    assert any("devuser" in msg for _, msg, _ in findings)


def test_personal_path_workplace_detected(tmp_path: Path) -> None:
    f = tmp_path / "README.md"
    # NOTE: fixture text must contain a literal scannable path.
    # The pattern is built via concatenation in the script to avoid self-match,
    # but test fixtures are allowed to contain the literal string.
    f.write_text("repo at C:/Workplace/myproject --agents 3", encoding="utf-8")
    findings = hyg.check_personal_paths([f])
    assert any("FAIL" == sev for sev, _, _ in findings)
    assert any("Workplace" in msg for _, msg, _ in findings)


def test_personal_path_windows_backslash_detected(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text(r"path: C:\Users\devuser\AppData", encoding="utf-8")
    findings = hyg.check_personal_paths([f])
    assert any("FAIL" == sev for sev, _, _ in findings)


def test_personal_path_unix_detected(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("see /Users/johndoe/projects", encoding="utf-8")
    findings = hyg.check_personal_paths([f])
    assert any("FAIL" == sev for sev, _, _ in findings)


def test_placeholder_unix_path_not_flagged(tmp_path: Path) -> None:
    f = tmp_path / "README.md"
    f.write_text("replace /Users/username/projects with your path", encoding="utf-8")
    findings = hyg.check_personal_paths([f])
    assert findings == []


def test_placeholder_unix_user_not_flagged(tmp_path: Path) -> None:
    f = tmp_path / "README.md"
    f.write_text("e.g. /Users/yourname/config", encoding="utf-8")
    findings = hyg.check_personal_paths([f])
    assert findings == []


def test_clean_file_no_findings(tmp_path: Path) -> None:
    f = tmp_path / "clean.md"
    f.write_text("werktools hub serve --profile community-builder", encoding="utf-8")
    findings = hyg.check_personal_paths([f])
    assert findings == []


# ---------------------------------------------------------------------------
# check_remote_url_placeholder
# ---------------------------------------------------------------------------


def test_remote_url_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project.urls]\nHomepage = "REMOTE_URL"\n', encoding="utf-8"
    )
    monkeypatch.setattr(hyg, "REPO_ROOT", tmp_path)
    findings = hyg.check_remote_url_placeholder()
    assert any("WARN" == sev for sev, _, _ in findings)
    assert any("REMOTE_URL" in msg for _, msg, _ in findings)


def test_no_remote_url_no_warn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project.urls]\nHomepage = "https://github.com/example/repo"\n', encoding="utf-8"
    )
    monkeypatch.setattr(hyg, "REPO_ROOT", tmp_path)
    findings = hyg.check_remote_url_placeholder()
    assert findings == []


# ---------------------------------------------------------------------------
# check_readme_tool_count
# ---------------------------------------------------------------------------


def _write_server(tmp_path: Path, count: int) -> None:
    """Write a fake hub/server.py with `count` always-on bridge tool definitions.

    Definitions are written as indented (nested) functions to match the
    real server.py layout where tools are defined inside make_server().
    The regex uses '^\\s+def (' with re.MULTILINE so indentation is required.
    """
    all_tools = [
        "_tool_search",
        "_tool_describe",
        "_tool_call",
        "_profile_info",
        "_ledger_recent",
        "_approval_status",
        "_hub_status_tool",
        "_registry_search",
    ]
    lines = ["def make_server():"]
    for name in all_tools[:count]:
        lines.append(f"    def {name}(args):")
        lines.append("        pass")
    server_dir = tmp_path / "src" / "werktools" / "hub"
    server_dir.mkdir(parents=True)
    (server_dir / "server.py").write_text("\n".join(lines), encoding="utf-8")


def test_readme_count_matches_no_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_server(tmp_path, 8)
    readme = tmp_path / "README.md"
    readme.write_text(
        "eight always-on bridge tools (`tool_search`, `tool_describe`)", encoding="utf-8"
    )
    monkeypatch.setattr(hyg, "REPO_ROOT", tmp_path)
    findings = hyg.check_readme_tool_count()
    assert findings == []


def test_readme_count_drift_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_server(tmp_path, 6)  # actual = 6
    readme = tmp_path / "README.md"
    readme.write_text(
        "eight always-on bridge tools (`tool_search`, `tool_describe`)", encoding="utf-8"
    )
    monkeypatch.setattr(hyg, "REPO_ROOT", tmp_path)
    findings = hyg.check_readme_tool_count()
    assert any("FAIL" == sev for sev, _, _ in findings)
    assert any("8" in msg and "6" in msg for _, msg, _ in findings)


def test_readme_no_count_claim_no_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_server(tmp_path, 8)
    readme = tmp_path / "README.md"
    readme.write_text("bridge tools: tool_search, tool_describe ...", encoding="utf-8")
    monkeypatch.setattr(hyg, "REPO_ROOT", tmp_path)
    findings = hyg.check_readme_tool_count()
    assert findings == []


# ---------------------------------------------------------------------------
# main() two-tier exit contract
# ---------------------------------------------------------------------------


def test_main_strict_exits_1_on_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--strict exits 1 when there is a FAIL-class finding."""
    readme = tmp_path / "README.md"
    readme.write_text("see C:/Users/devuser/config", encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'x'\n", encoding="utf-8")
    # No server.py => check_readme_tool_count skips
    monkeypatch.setattr(hyg, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(hyg, "SCAN_TARGETS", ["README.md"])
    result = hyg.main(["--strict"])
    assert result == 1


def test_main_no_strict_exits_0_on_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --strict, FAIL findings still exit 0."""
    readme = tmp_path / "README.md"
    readme.write_text("see C:/Users/devuser/config", encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'x'\n", encoding="utf-8")
    monkeypatch.setattr(hyg, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(hyg, "SCAN_TARGETS", ["README.md"])
    result = hyg.main([])
    assert result == 0


def test_main_clean_repo_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hyg, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(hyg, "SCAN_TARGETS", [])
    result = hyg.main(["--strict"])
    assert result == 0
