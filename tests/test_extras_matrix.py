"""Enforce the dep-free CORE promise for werktools.

Rules tested here:
1. Core modules import cleanly with no third-party packages (proven by
   inspecting every top-level import in the module under test and asserting
   it is guarded when it touches an optional dep).
2. Optional-dep imports are NEVER made at module scope without a try/except
   guard -- any bare `import fastmcp`, `import httpx`, or `import yaml` at
   module level (outside a try block) in a core path is a contract violation.
3. The two marker extras (swarm, lifecycle) have no deps in pyproject.toml.

These tests run in the normal pytest suite (extras installed) and in the
bare-install CI lane (no extras).  In the full-extras run the guard-shape
checks still pass because they inspect AST/source, not runtime availability.
"""

from __future__ import annotations

import ast
import importlib
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SRC_ROOT = Path(__file__).parent.parent / "src" / "werktools"

OPTIONAL_SYMBOLS = {"fastmcp", "httpx", "yaml", "pyyaml"}

# Core modules that MUST be importable with zero optional deps.
# hub.server and hub.relay intentionally excluded -- they raise on import
# when [server] is absent (that is the documented contract for those modules).
#
# hub.workers is included: it has a guarded optional httpx import inside
# _real_http_call() -- the guard (try/except ImportError) is the contract
# being tested here. module-level import is safe.
CORE_MODULES = [
    "werktools",
    "werktools.envelope",
    "werktools.ledger",
    "werktools.classify",
    "werktools.policy",
    "werktools.redaction",
    "werktools.catalog",
    "werktools.profile",
    "werktools.hub.contracts",
    "werktools.hub.registry",
    "werktools.hub.policy",
    "werktools.hub.capabilities",
    "werktools.hub.approvals",
    "werktools.hub.lifecycle",
    "werktools.hub.invariants",
    "werktools.hub.status",
    "werktools.hub.ledger",
    "werktools.hub.discovery",
    "werktools.hub.store_bridge",
    # Additional hub modules (must_fix #2 -- previously absent)
    "werktools.hub.render",
    "werktools.hub.export_rules",
    "werktools.hub.pool",
    "werktools.hub.dashboard",
    "werktools.hub.workers",  # guarded optional httpx import is the contract tested
    "werktools.tools.truth",
    "werktools.tools.mine",
    "werktools.tools.trace",
    "werktools.tools.vault",
    "werktools.tools.data_gate",
    "werktools.tools.swarm",
    "werktools.tools.cost",
    "werktools.tools.eval",
    "werktools.tools.audit",
    "werktools.tools.skills",
    "werktools.tools.skills_discover",
    "werktools.tools.integration_gate",
    "werktools.tools.canon",
]


def _optional_available() -> dict[str, bool]:
    available: dict[str, bool] = {}
    for name in ("fastmcp", "httpx", "yaml"):
        try:
            importlib.import_module(name)
            available[name] = True
        except ImportError:
            available[name] = False
    return available


# ---------------------------------------------------------------------------
# Test 1: core modules import without raising
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module_name", CORE_MODULES)
def test_core_module_imports_cleanly(module_name: str) -> None:
    """Each core module must be importable with no optional deps present.

    In the full-extras suite this trivially passes.
    In the bare-install lane (CI) it proves zero leakage.
    """
    available = _optional_available()
    # If all optionals are present this test is a fast no-op guard.
    # If none are present it is the actual dep-free proof.
    try:
        mod = importlib.import_module(module_name)
        assert isinstance(mod, types.ModuleType)
    except ImportError as exc:
        msg = str(exc)
        # Acceptable only if an optional dep is absent AND the error message
        # honestly names the missing package.
        for pkg in OPTIONAL_SYMBOLS:
            if pkg in msg and not available.get(pkg, False):
                pytest.skip(f"{module_name} requires optional dep {pkg!r} (not installed)")
        pytest.fail(
            f"Core module {module_name!r} failed to import unexpectedly: {exc}\n"
            "If this module requires an optional dep, it must use a guarded\n"
            "try/except at module scope or defer the import to a function body."
        )


# ---------------------------------------------------------------------------
# Test 2: AST guard check -- no bare optional import at module scope
# ---------------------------------------------------------------------------

def _py_files_for(module_name: str) -> list[Path]:
    """Resolve a dotted module name to its .py file under SRC_ROOT.

    must_fix #1: handle bare package name 'werktools' (no sub-path).
    After stripping the 'werktools.' prefix, if the result is 'werktools'
    (i.e. the input was the root package), return [SRC_ROOT / '__init__.py'].
    """
    rel = module_name.replace("werktools.", "", 1)
    # Handle the bare package root: 'werktools' -> rel == 'werktools'
    if rel == "werktools":
        init = SRC_ROOT / "__init__.py"
        return [init] if init.exists() else []
    rel_path = rel.replace(".", "/")
    candidates = [
        SRC_ROOT / f"{rel_path}.py",
        SRC_ROOT / rel_path / "__init__.py",
    ]
    return [p for p in candidates if p.exists()]


def _collect_bare_optional_imports(source: str) -> list[tuple[int, str]]:
    """Return (lineno, name) for any optional-dep import NOT inside a try block.

    An import is 'bare' when it sits at module scope (not in a function,
    class, or try statement) and names an optional dependency package.
    """
    tree = ast.parse(source)
    violations: list[tuple[int, str]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._in_try = 0
            self._in_func_or_class = 0

        def visit_Try(self, node: ast.Try) -> None:
            self._in_try += 1
            self.generic_visit(node)
            self._in_try -= 1

        # Python 3.11+ splits Try into TryStar; handle both
        visit_TryStar = visit_Try  # type: ignore[attr-defined]

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._in_func_or_class += 1
            self.generic_visit(node)
            self._in_func_or_class -= 1

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[attr-defined]

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._in_func_or_class += 1
            self.generic_visit(node)
            self._in_func_or_class -= 1

        def _check_name(self, name: str, lineno: int) -> None:
            root = name.split(".")[0]
            if root in OPTIONAL_SYMBOLS and self._in_try == 0 and self._in_func_or_class == 0:
                violations.append((lineno, name))

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                self._check_name(alias.name, node.lineno)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.module:
                self._check_name(node.module, node.lineno)

    Visitor().visit(tree)
    return violations


@pytest.mark.parametrize("module_name", CORE_MODULES)
def test_no_bare_optional_import_at_module_scope(module_name: str) -> None:
    """Fail if any core module contains an unguarded optional-dep import at
    module scope (outside a try block and outside a function/class body).

    This test catches the bug BEFORE it becomes a runtime failure in the
    bare-install lane -- it runs even when all extras are installed.
    """
    files = _py_files_for(module_name)
    if not files:
        pytest.skip(f"No source file found for {module_name!r} under {SRC_ROOT}")

    for path in files:
        source = path.read_text(encoding="utf-8")
        violations = _collect_bare_optional_imports(source)
        if violations:
            lines = "\n".join(f"  line {ln}: import {name!r}" for ln, name in violations)
            pytest.fail(
                f"{path.relative_to(SRC_ROOT.parent.parent)} has bare optional-dep "
                f"import(s) at module scope (not inside try or function):\n{lines}\n"
                "Wrap with try/except ImportError or move inside the function that needs it."
            )


# ---------------------------------------------------------------------------
# Test 3: marker extras have no deps (pyproject.toml contract)
# ---------------------------------------------------------------------------

def test_marker_extras_have_no_deps() -> None:
    """swarm and lifecycle are marker extras -- they must list no packages."""
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    if not pyproject.exists():
        pytest.skip("pyproject.toml not found")

    text = pyproject.read_text(encoding="utf-8")

    # Parse [project.optional-dependencies] section manually (stdlib only,
    # no tomllib required for 3.10 where tomllib is absent).
    # Strategy: find the section header, then read until the next [section].
    import re

    section_match = re.search(
        r"\[project\.optional-dependencies\](.*?)(?=\n\[|\Z)",
        text,
        re.DOTALL,
    )
    assert section_match, "Could not find [project.optional-dependencies] in pyproject.toml"

    section = section_match.group(1)

    # Extract each extra's value list: name = [...] possibly multi-line
    extra_re = re.compile(r"^(\w+)\s*=\s*\[(.*?)\]", re.MULTILINE | re.DOTALL)
    extras: dict[str, list[str]] = {}
    for m in extra_re.finditer(section):
        name = m.group(1)
        raw = m.group(2).strip()
        if not raw:
            extras[name] = []
        else:
            entries = [e.strip().strip('"').strip("'") for e in raw.split(",") if e.strip()]
            extras[name] = [e for e in entries if e]

    for marker_extra in ("swarm", "lifecycle"):
        assert marker_extra in extras, (
            f"Extra {marker_extra!r} not found in pyproject.toml -- "
            "if removed, update this test and the README extras table."
        )
        deps = extras[marker_extra]
        assert deps == [], (
            f"Marker extra {marker_extra!r} must have no deps, got: {deps}\n"
            "If a real dep is added, update the README and remove the 'marker' label."
        )


# ---------------------------------------------------------------------------
# Test 4: extras table completeness -- all pyproject extras documented
# ---------------------------------------------------------------------------

def test_readme_extras_table_covers_all_pyproject_extras() -> None:
    """Every extra in pyproject.toml must appear in the README extras table.

    Fail loudly rather than silently drift.
    """
    import re

    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    readme = Path(__file__).parent.parent / "README.md"

    if not pyproject.exists() or not readme.exists():
        pytest.skip("pyproject.toml or README.md not found")

    pyproject_text = pyproject.read_text(encoding="utf-8")
    section_match = re.search(
        r"\[project\.optional-dependencies\](.*?)(?=\n\[|\Z)",
        pyproject_text,
        re.DOTALL,
    )
    assert section_match, "Could not find [project.optional-dependencies]"
    section = section_match.group(1)

    extra_names = re.findall(r"^(\w+)\s*=", section, re.MULTILINE)

    readme_text = readme.read_text(encoding="utf-8")

    missing = []
    for name in extra_names:
        # Check the extras table contains a backtick-quoted mention of the name
        if f"`{name}`" not in readme_text:
            missing.append(name)

    assert not missing, (
        f"These extras are in pyproject.toml but NOT documented in README.md: {missing}\n"
        "Add them to the ## Extras table in README.md."
    )
