"""Deterministic local docs/code truth checks."""

from __future__ import annotations

import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path

CLAIM_LABELS = (
    "code_verified",
    "source_provided",
    "externally_verified",
    "unverified",
    "stale_risk",
    "contradicted",
)

_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_URL_RE = re.compile(r"https?://[^\s)>\]]+")


@dataclass(frozen=True)
class RepoFacts:
    """Concrete local repo facts gathered without executing project code."""

    root: str
    markdown_files: tuple[str, ...]
    python_files: tuple[str, ...]
    test_files: tuple[str, ...]
    project_name: str | None
    dependencies_empty: bool | None
    console_scripts: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "markdown_files": list(self.markdown_files),
            "python_files": list(self.python_files),
            "test_files": list(self.test_files),
            "project_name": self.project_name,
            "dependencies_empty": self.dependencies_empty,
            "console_scripts": list(self.console_scripts),
        }


@dataclass(frozen=True)
class TruthClaim:
    """One simple, checkable claim extracted from markdown."""

    source: str
    kind: str
    target: str
    text: str

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "kind": self.kind,
            "target": self.target,
            "text": self.text,
        }


@dataclass(frozen=True)
class TruthCheck:
    """Result of checking one TruthClaim against local facts."""

    claim: TruthClaim
    label: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "claim": self.claim.to_dict(),
            "label": self.label,
            "reason": self.reason,
        }


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


IGNORED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    "dist",
    "build",
}
_IGNORED_DIRS = IGNORED_DIRS  # backward-compatible alias


def _iter_files(root: Path, suffix: str) -> tuple[str, ...]:
    # os.walk with pruning instead of rglob: vendored/env dirs are not part
    # of the repo's own truth, and broken or over-long entries inside them
    # (pnpm links on Windows) must not abort the whole scan.
    files: list[str] = []
    _walk_errors: list[OSError] = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=_walk_errors.append):
        dirnames[:] = [name for name in dirnames if name not in _IGNORED_DIRS]
        for name in filenames:
            if not name.lower().endswith(suffix):
                continue
            path = Path(dirpath) / name
            try:
                if path.is_file():
                    files.append(_rel(path, root))
            except OSError:
                continue
    if _walk_errors:
        warnings.warn(
            f"truth._iter_files: {len(_walk_errors)} directory error(s) during walk of {root!r} (first: {_walk_errors[0]}); scan may be incomplete",
            stacklevel=3,
        )
    return tuple(sorted(files))


def _strip_toml_string(value: str) -> str:
    return value.strip().strip('"').strip("'")


def _pyproject_facts(root: Path) -> tuple[str | None, bool | None, tuple[str, ...]]:
    path = root / "pyproject.toml"
    if not path.exists():
        return None, None, ()

    section = ""
    name: str | None = None
    dependencies_empty: bool | None = None
    scripts: list[str] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line.strip("[]")
            continue
        if "=" not in line:
            continue

        key, value = (part.strip() for part in line.split("=", 1))
        if section == "project" and key == "name":
            name = _strip_toml_string(value)
        elif section == "project" and key == "dependencies":
            dependencies_empty = value == "[]"
        elif section == "project.scripts":
            scripts.append(_strip_toml_string(key))

    return name, dependencies_empty, tuple(sorted(scripts))


def scan_repo(repo: str | Path) -> RepoFacts:
    """Scan local repo metadata without executing commands."""
    root = Path(repo).resolve()
    project_name, dependencies_empty, scripts = _pyproject_facts(root)
    python_files = _iter_files(root, ".py")
    return RepoFacts(
        root=str(root),
        markdown_files=_iter_files(root, ".md"),
        python_files=python_files,
        test_files=tuple(path for path in python_files if path.startswith("tests/")),
        project_name=project_name,
        dependencies_empty=dependencies_empty,
        console_scripts=scripts,
    )


def _path_like(target: str) -> bool:
    return (
        "/" in target
        or "\\" in target
        or target.endswith((".md", ".py", ".toml", ".json", ".yaml", ".yml", ".txt"))
    )


def extract_claims(path: str | Path, text: str) -> list[TruthClaim]:
    """Extract simple local path and external URL claims from markdown text."""
    source = Path(path).as_posix()
    claims: list[TruthClaim] = []

    for match in _BACKTICK_RE.finditer(text):
        target = match.group(1).strip()
        if target.startswith(("http://", "https://")) or _path_like(target):
            claims.append(TruthClaim(source, "path", target.replace("\\", "/"), match.group(0)))

    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,")
        claims.append(TruthClaim(source, "external_url", url, url))

    return claims


def collect_claims(repo: str | Path) -> list[TruthClaim]:
    """Collect simple checkable claims from all markdown files under repo."""
    root = Path(repo)
    claims: list[TruthClaim] = []
    for markdown in scan_repo(root).markdown_files:
        path = root / markdown
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            warnings.warn(f"collect_claims: skipping {path}: {exc}", stacklevel=2)
            continue
        claims.extend(extract_claims(markdown, text))
    return claims


def check_claims(repo: str | Path, claims: list[TruthClaim]) -> list[TruthCheck]:
    """Check extracted claims against local repo files and source links."""
    root = Path(repo)
    checks: list[TruthCheck] = []
    for claim in claims:
        if claim.kind == "external_url":
            checks.append(TruthCheck(claim, "source_provided", "external source URL provided"))
            continue

        if claim.kind == "path":
            target = claim.target
            if target.startswith(("http://", "https://")):
                checks.append(TruthCheck(claim, "source_provided", "external source URL provided"))
            elif (root / target).exists():
                checks.append(TruthCheck(claim, "code_verified", "local path exists"))
            else:
                checks.append(TruthCheck(claim, "contradicted", "local path is missing"))
            continue

        checks.append(TruthCheck(claim, "unverified", "claim type is not checked by v0"))

    return checks


def summarize_checks(checks: list[TruthCheck]) -> dict[str, int]:
    """Return stable label counts for a list of checks."""
    counts = {label: 0 for label in CLAIM_LABELS}
    for check in checks:
        counts[check.label] = counts.get(check.label, 0) + 1
    return counts


def write_report(repo: str | Path, out: str | Path) -> dict:
    """Write a readable Markdown truth report on explicit request."""
    facts = scan_repo(repo)
    checks = check_claims(repo, collect_claims(repo))
    summary = summarize_checks(checks)
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Truth Auditor Report",
        "",
        f"Repo: `{facts.root}`",
        "",
        "## Summary",
        "",
    ]
    for label in CLAIM_LABELS:
        lines.append(f"- `{label}`: {summary[label]}")
    lines.extend(["", "## Checks", ""])
    for check in checks:
        lines.append(
            f"- `{check.label}` `{check.claim.target}` from `{check.claim.source}` - {check.reason}"
        )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"facts": facts.to_dict(), "summary": summary, "checks": [c.to_dict() for c in checks]}
