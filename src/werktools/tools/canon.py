"""Canon-docs: completeness checks + generators with a verify loop.

Extends the Truth Auditor from path/URL claim-checking to canon
completeness: is AGENTS.md present, do its refs resolve, are documented CLI
commands real, are DESIGN tokens still used? Plus generators for AGENTS.md
and the Kiro/SpecKit requirements+design+tasks template. The unique value
is the verify loop — generation is commodity, drift-catching is not.

Stdlib + werktools.tools.truth only. Pure synchronous, read-only checks;
generators return strings (the CLI owns writes). No auto-fix, no ledger.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .truth import IGNORED_DIRS

CANON_DOC_NAMES = {"AGENTS.md": "error", "README.md": "error", "CHANGELOG.md": "warning"}
SPEC_DOC_NAMES = ("requirements.md", "design.md", "tasks.md")

_WIN_ABS = re.compile(r"^[A-Za-z]:[\\/]")
_BACKTICK = re.compile(r"`([^`]+)`")
_ADD_PARSER = re.compile(r"\.add_parser\(\s*[\"']([A-Za-z0-9_-]+)[\"']")
_TOKEN = re.compile(r"\|\s*`([a-z][a-z0-9_]*)`")


@dataclass(frozen=True)
class CanonIssue:
    kind: str
    severity: str
    source: str
    target: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "source": self.source,
            "target": self.target,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CanonReport:
    repo: str
    issues: tuple[CanonIssue, ...]
    checked_docs: tuple[str, ...]
    cli_commands_found: tuple[str, ...]
    token_names_found: tuple[str, ...]
    ok: bool = field(default=True)

    @property
    def errors(self) -> list[CanonIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[CanonIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "issues": [i.to_dict() for i in self.issues],
            "checked_docs": list(self.checked_docs),
            "cli_commands_found": list(self.cli_commands_found),
            "token_names_found": list(self.token_names_found),
            "ok": self.ok,
        }


def check_canon_docs(repo: Path) -> list[CanonIssue]:
    issues: list[CanonIssue] = []
    for name, severity in CANON_DOC_NAMES.items():
        if not (repo / name).exists():
            issues.append(CanonIssue("missing_doc", severity, str(repo), name, f"{name} is absent"))
    return issues


def _looks_local_relative(target: str) -> bool:
    return ("/" in target or "\\" in target) and not _WIN_ABS.match(target) and not target.startswith(("http://", "https://"))


def check_agents_refs(repo: Path) -> list[CanonIssue]:
    agents = repo / "AGENTS.md"
    if not agents.exists():
        return []
    issues: list[CanonIssue] = []
    text = agents.read_text(encoding="utf-8")
    for match in _BACKTICK.finditer(text):
        target = match.group(1).strip()
        if _WIN_ABS.match(target):
            continue  # absolute paths on another machine — not checkable
        if _looks_local_relative(target):
            if not (repo / target).exists():
                issues.append(CanonIssue("broken_ref", "warning", "AGENTS.md", target, "referenced path is missing"))
        elif re.fullmatch(r"[A-Za-z0-9_./-]+\.[A-Za-z0-9]+", target) and "/" not in target:
            # bare single filename — warn only, never error (could live anywhere)
            if not list(repo.rglob(target)):
                issues.append(CanonIssue("broken_ref", "warning", "AGENTS.md", target, "bare filename not found"))
    return issues


def check_cli_commands(repo: Path, documented: set[str] | None = None) -> tuple[list[CanonIssue], list[str]]:
    cli = repo / "src" / "werktools" / "cli.py"
    found: list[str] = []
    if cli.exists():
        found = sorted(set(_ADD_PARSER.findall(cli.read_text(encoding="utf-8"))))
    issues: list[CanonIssue] = []
    if documented:
        for cmd in sorted(documented):
            if cmd not in found:
                issues.append(CanonIssue("missing_cli_cmd", "warning", "docs", cmd, "documented command not in cli.py"))
    return issues, found


def check_design_tokens(repo: Path, design_glob: str = "DESIGN.md") -> tuple[list[CanonIssue], list[str]]:
    issues: list[CanonIssue] = []
    tokens: list[str] = []
    sources = "\n".join(
        p.read_text(encoding="utf-8")
        for p in repo.rglob("*.py")
        if not any(part in IGNORED_DIRS for part in p.parts)
    )
    for design in repo.glob(design_glob):
        text = design.read_text(encoding="utf-8")
        for match in _TOKEN.finditer(text):
            token = match.group(1)
            tokens.append(token)
            if token not in sources and token not in text.replace(match.group(0), ""):
                issues.append(CanonIssue("unused_token", "warning", design.name, token, "design token not referenced"))
    return issues, tokens


def check_spec_refs(repo: Path, spec_names: tuple[str, ...] = SPEC_DOC_NAMES) -> list[CanonIssue]:
    from .truth import check_claims, extract_claims

    issues: list[CanonIssue] = []
    for name in spec_names:
        spec = repo / name
        if not spec.exists():
            continue
        claims = extract_claims(name, spec.read_text(encoding="utf-8"))
        for check in check_claims(repo, claims):
            if check.label == "contradicted" and check.claim.kind == "path":
                issues.append(
                    CanonIssue("broken_ref", "error", name, check.claim.target, "spec references a missing path")
                )
    return issues


def check_canon(repo: str | Path, *, spec_glob: str = "", design_glob: str = "DESIGN.md") -> CanonReport:
    """Run all canon checks. Read-only; never writes a report."""
    root = Path(repo)
    documented: set[str] = set()
    issues: list[CanonIssue] = []
    issues.extend(check_canon_docs(root))
    issues.extend(check_agents_refs(root))
    cli_issues, found = check_cli_commands(root, documented)
    issues.extend(cli_issues)
    token_issues, tokens = check_design_tokens(root, design_glob)
    issues.extend(token_issues)
    issues.extend(check_spec_refs(root))
    ok = not any(i.severity == "error" for i in issues)
    return CanonReport(
        repo=str(root),
        issues=tuple(issues),
        checked_docs=tuple(CANON_DOC_NAMES),
        cli_commands_found=tuple(found),
        token_names_found=tuple(tokens),
        ok=ok,
    )


def gen_agents_md(
    repo: str | Path,
    *,
    project_name: str | None = None,
    console_scripts: tuple[str, ...] = (),
    extra_guardrails: list[str] | None = None,
    extra_conventions: list[str] | None = None,
) -> str:
    name = project_name or Path(repo).name
    guardrails = ["Write or change code only inside this repository."] + list(extra_guardrails or [])
    conventions = ["Use strict TDD: write the failing test, confirm red, implement, confirm green."] + list(
        extra_conventions or []
    )
    lines = ["# AGENTS.md", "", f"Project: `{name}`", "", "## Hard Guardrail", ""]
    lines += [f"- {g}" for g in guardrails]
    lines += ["", "## Build Conventions", ""]
    lines += [f"- {c}" for c in conventions]
    if console_scripts:
        lines += ["", f"- Console scripts: {', '.join(console_scripts)}"]
    lines += ["", "## Style", "", "- Prefer clear, readable Markdown.", "- Keep files UTF-8/ASCII clean.", ""]
    return "\n".join(lines)


def gen_spec_template(
    feature_name: str,
    *,
    kind: str = "mcp",
    tool_names: tuple[str, ...] | None = None,
    cli_commands: tuple[str, ...] | None = None,
) -> dict[str, str]:
    req = textwrap.dedent(
        f"""\
        # Requirements: {feature_name}

        ## User Stories

        - As an operator, I want {feature_name} so that ...

        ## Acceptance Criteria

        - [ ] ...
        """
    )
    design_sections = [f"# Design: {feature_name}", "", "## Architecture", "", "..."]
    if kind == "mcp":
        design_sections += ["", "## MCP Tools", ""]
        for t in tool_names or ():
            design_sections.append(f"- `{t}`")
    if cli_commands:
        design_sections += ["", "## CLI", ""]
        for c in cli_commands:
            design_sections.append(f"- `{c}`")
    tasks = textwrap.dedent(
        f"""\
        # Tasks: {feature_name}

        - [ ] Write failing tests
        - [ ] Implement {feature_name}
        - [ ] Gate (pytest + ruff + mypy)
        """
    )
    return {"requirements.md": req, "design.md": "\n".join(design_sections) + "\n", "tasks.md": tasks}
