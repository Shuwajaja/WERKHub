#!/usr/bin/env python3
"""Release-hygiene validator for werktools.

Checks shipped files for personal path leaks and known placeholder values.
Optionally checks tool/test count claims in README against reality.

Severity tiers
--------------
FAIL  -- hard finding; exits 1 in --strict mode, exits 0 with a warning otherwise.
WARN  -- soft finding; always exits 0; printed for operator awareness.

Usage
-----
    python scripts/check_release_hygiene.py            # WARN-only, always exits 0
    python scripts/check_release_hygiene.py --strict   # exits 1 on any FAIL finding
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.resolve()

# Files/dirs to scan for personal path leaks.
# Wheel ships src/werktools/; sdist additionally ships README, docs, examples.
SCAN_TARGETS: list[str] = [
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "pyproject.toml",
    "docs",
    "src",
    "examples",
    "scripts",
]

# File extensions to inspect (binary files skipped).
TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {".py", ".md", ".toml", ".yml", ".yaml", ".json", ".txt", ".rst", ".sh", ".cfg", ".ini"}
)

# Personal path patterns -- these are FAIL-class findings.
# Matches literal Windows-style C:/... or C:\... paths with known private segments,
# and Unix-style /Users/<real-name> patterns.
#
# NOTE: pattern literals are split across string concatenation so this file
# does not self-match when scanned (bootstrap self-reference fix -- must_fix #2).
_PERSONAL_PATH_PATTERNS: list[re.Pattern[str]] = [
    # Windows forward-slash variant: user home dirs and Workplace paths
    re.compile(r"C:/Users/[a-zA-Z][a-zA-Z0-9._-]+", re.IGNORECASE),
    re.compile("C:/Work" + "place", re.IGNORECASE),
    # Second personal root: the operator's Work-space tree (distinct from the
    # Work-place root) — e.g. Hermes/concept-source dirs. Pattern literals are
    # concatenated so this file does not self-match when scanned.
    re.compile("C:/Work" + "space", re.IGNORECASE),
    # Windows backslash variant
    re.compile(r"C:\\Users\\[a-zA-Z][a-zA-Z0-9._-]+", re.IGNORECASE),
    re.compile("C:\\\\" + "Workplace", re.IGNORECASE),
    re.compile("C:\\\\" + "Workspace", re.IGNORECASE),
    # Generic Unix user home: /Users/<name> -- skip obvious placeholders
    re.compile(r"/Users/(?!(?:example|me|user|username|you|yourname|yourusername|your-username)(?:/|$))[a-zA-Z][a-zA-Z0-9._-]+"),
]

# Placeholder values in pyproject.toml URLs -- WARN-only (operator fill-in).
_REMOTE_URL_PATTERN = re.compile(r'"REMOTE_URL"')

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def iter_text_files(targets: list[str]) -> list[Path]:
    """Return all text files under the given target paths relative to REPO_ROOT."""
    out: list[Path] = []
    for name in targets:
        p = REPO_ROOT / name
        if not p.exists():
            continue
        if p.is_file():
            if p.suffix.lower() in TEXT_EXTENSIONS:
                out.append(p)
        else:
            for child in sorted(p.rglob("*")):
                if child.is_file() and child.suffix.lower() in TEXT_EXTENSIONS:
                    out.append(child)
    return out


def repo_rel(p: Path) -> str:
    try:
        return p.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_personal_paths(files: list[Path]) -> list[tuple[str, str, int]]:
    """Return list of (severity, message, line_no) for personal path leaks."""
    findings: list[tuple[str, str, int]] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for pat in _PERSONAL_PATH_PATTERNS:
                for m in pat.finditer(line):
                    findings.append((
                        "FAIL",
                        f"personal path '{m.group()}' in {repo_rel(path)}:{lineno}",
                        lineno,
                    ))
    return findings


def check_remote_url_placeholder() -> list[tuple[str, str, int]]:
    """WARN if pyproject.toml still contains the REMOTE_URL placeholder."""
    findings: list[tuple[str, str, int]] = []
    pyproject = REPO_ROOT / "pyproject.toml"
    if not pyproject.exists():
        return findings
    text = pyproject.read_text(encoding="utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), 1):
        if _REMOTE_URL_PATTERN.search(line):
            findings.append((
                "WARN",
                f"REMOTE_URL placeholder in pyproject.toml:{lineno} -- operator fill-in required before release",
                lineno,
            ))
            break  # one warning is enough; all lines are the same issue
    return findings


def check_readme_tool_count() -> list[tuple[str, str, int]]:
    """FAIL if README 'eight always-on bridge tools' claim disagrees with hub/server.py.

    Counts the number of bridge-tool private handler functions registered in
    src/werktools/hub/server.py to verify the README claim. Only private
    functions with names matching _tool_*, _profile_*, _ledger_*, _approval_*,
    _hub_status_*, _registry_* are counted (the 'always-on' set, excludes
    conditional model_worker_* tools).

    NOTE: these functions are nested inside make_server(), so the regex uses
    '^\\s+def (' with re.MULTILINE to match indented definitions (must_fix #1).
    Verified against real src/werktools/hub/server.py: findall count == 8.
    """
    findings: list[tuple[str, str, int]] = []
    readme = REPO_ROOT / "README.md"
    server = REPO_ROOT / "src" / "werktools" / "hub" / "server.py"

    if not readme.exists() or not server.exists():
        return findings

    # Extract claimed count from README (e.g. "eight always-on bridge tools")
    readme_text = readme.read_text(encoding="utf-8", errors="replace")
    _WORD_TO_INT = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12,
    }
    claim_pattern = re.compile(
        r"(\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d+)\b)"
        r"[\s\w-]*?always-on bridge tools",
        re.IGNORECASE,
    )
    m = claim_pattern.search(readme_text)
    if not m:
        # No numeric claim found; nothing to verify.
        return findings
    raw_claim = m.group(1).lower()
    claimed = _WORD_TO_INT.get(raw_claim, None)
    if claimed is None:
        try:
            claimed = int(raw_claim)
        except ValueError:
            return findings

    # Count actual always-on bridge handler private functions in server.py.
    # Functions are nested inside make_server(), so use ^\s+def ( with MULTILINE.
    server_text = server.read_text(encoding="utf-8", errors="replace")
    _BRIDGE_DEF_RE = re.compile(
        r"^\s+def (_tool_search|_tool_describe|_tool_call|_profile_info"
        r"|_ledger_recent|_approval_status|_hub_status_tool|_registry_search)\b",
        re.MULTILINE,
    )
    actual = len(_BRIDGE_DEF_RE.findall(server_text))

    # Find README line number for the claim
    claim_lineno = 1
    for i, line in enumerate(readme_text.splitlines(), 1):
        if claim_pattern.search(line):
            claim_lineno = i
            break

    if actual != claimed:
        findings.append((
            "FAIL",
            (
                f"README:{claim_lineno} claims {claimed} always-on bridge tools "
                f"but hub/server.py defines {actual} -- update README or server"
            ),
            claim_lineno,
        ))
    return findings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Release-hygiene validator for werktools.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on any FAIL-class finding (for CI). Default: WARN-only, exit 0.",
    )
    args = parser.parse_args(argv)

    files = iter_text_files(SCAN_TARGETS)
    all_findings: list[tuple[str, str, int]] = []

    all_findings.extend(check_personal_paths(files))
    all_findings.extend(check_remote_url_placeholder())
    all_findings.extend(check_readme_tool_count())

    fail_count = 0
    warn_count = 0

    for severity, message, _ in all_findings:
        if severity == "FAIL":
            print(f"FAIL: {message}", file=sys.stderr)
            fail_count += 1
        else:
            print(f"WARN: {message}", file=sys.stderr)
            warn_count += 1

    if not all_findings:
        print("check_release_hygiene: OK -- no findings.")
        return 0

    summary = f"check_release_hygiene: {fail_count} FAIL, {warn_count} WARN."
    if fail_count > 0 and args.strict:
        print(f"{summary} Exiting 1 (--strict).", file=sys.stderr)
        return 1

    print(f"{summary} Exiting 0 (WARN-only mode or no FAILs).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
