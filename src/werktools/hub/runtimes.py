"""Host-detection for AI runtimes (Claude Code, Codex, Cursor, ...).

Pure-stdlib, zero-I/O-on-import module that encodes per-host detection
knowledge as a frozen registry. Callers ask for a ``RuntimeReport`` describing
which AI hosts are present on this machine. No subprocess runs unless
``probe_versions=True`` is explicitly opted in.

Token health is reported as PRESENCE/DATE ONLY — an env var name being set, or
a token file existing (with its mtime). The token VALUE is never read, copied,
or logged. This mirrors the ``is_secret_key`` discipline used elsewhere.

Detection signals per host: a binary on PATH (``shutil.which``), a known GUI
install directory, a known config path, and token presence. A host counts as
``detected`` when the binary, a GUI path, or a config path is found.

No daemon constructs (no threads/atexit/while-True/asyncio): the module must
pass ``hub/invariants.py``. ``subprocess`` is imported lazily inside
``probe_one`` so the default path stays subprocess-free.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_VERSION_TIMEOUT_S = 5
_VERSION_OUTPUT_MAX = 200


@dataclass(frozen=True)
class RuntimeDescriptor:
    """Immutable detection knowledge for one supported AI host."""

    host_id: str
    display_name: str
    binary_names: tuple[str, ...]
    gui_install_paths_windows: tuple[str, ...]
    gui_install_paths_posix: tuple[str, ...]
    config_paths: tuple[str, ...]
    version_cmd: tuple[str, ...]
    token_env_vars: tuple[str, ...]
    token_file_paths: tuple[str, ...]
    monogram: str = ""  # monochrome placeholder (NO vendor logo)
    at_risk: bool = False  # host is deprecated / in transition
    at_risk_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id,
            "display_name": self.display_name,
            "monogram": self.monogram,
            "at_risk": self.at_risk,
            "at_risk_reason": self.at_risk_reason,
        }


@dataclass(frozen=True)
class RuntimeProbe:
    """The result of probing one host. Token values are never present here."""

    host_id: str
    binary_found: bool
    binary_path: str | None
    gui_path_found: str | None
    config_path_found: str | None
    version_str: str | None
    version_error: str | None
    token_env_present: bool
    token_file_present: bool
    token_file_mtime: float | None
    detected: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id,
            "binary_found": self.binary_found,
            "binary_path": self.binary_path,
            "gui_path_found": self.gui_path_found,
            "config_path_found": self.config_path_found,
            "version_str": self.version_str,
            "version_error": self.version_error,
            "token_env_present": self.token_env_present,
            "token_file_present": self.token_file_present,
            "token_file_mtime": self.token_file_mtime,
            "detected": self.detected,
        }


@dataclass(frozen=True)
class RuntimeReport:
    """A full sweep over all descriptors, with a UTC timestamp."""

    probes: tuple[RuntimeProbe, ...]
    generated_at: str
    probe_versions: bool

    def detected_hosts(self) -> tuple[str, ...]:
        return tuple(p.host_id for p in self.probes if p.detected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "probe_versions": self.probe_versions,
            "total": len(self.probes),
            "detected": list(self.detected_hosts()),
            "probes": [p.to_dict() for p in self.probes],
        }


# ── Canonical 9-host roster (per BUILD_PLAN §P1 item 2) ───────────────────────
# Detection paths for kimi/antigravity (and goose/gemini config) are
# best-effort placeholders flagged for operator confirmation in the spec's
# open questions — they never read secret values, so a wrong path only means a
# missed detection, never a leak.
DESCRIPTORS: tuple[RuntimeDescriptor, ...] = (
    RuntimeDescriptor(
        host_id="claude",
        display_name="Claude Code",
        binary_names=("claude",),
        gui_install_paths_windows=(r"%LOCALAPPDATA%\AnthropicClaude",),
        gui_install_paths_posix=("/Applications/Claude.app",),
        config_paths=("~/.claude.json", "~/.claude/settings.json"),
        version_cmd=("claude", "--version"),
        token_env_vars=("ANTHROPIC_API_KEY",),
        token_file_paths=("~/.claude.json",),
        monogram="CC",
    ),
    RuntimeDescriptor(
        host_id="codex",
        display_name="OpenAI Codex CLI",
        binary_names=("codex",),
        gui_install_paths_windows=(),
        gui_install_paths_posix=(),
        config_paths=("~/.codex/config.toml", "~/.codex/instructions.md"),
        version_cmd=("codex", "--version"),
        token_env_vars=("OPENAI_API_KEY",),
        token_file_paths=(),
        monogram="CX",
    ),
    RuntimeDescriptor(
        host_id="cursor",
        display_name="Cursor",
        binary_names=("cursor",),
        gui_install_paths_windows=(r"%LOCALAPPDATA%\Programs\cursor", r"%APPDATA%\Cursor"),
        gui_install_paths_posix=("/Applications/Cursor.app", "~/.local/share/cursor"),
        config_paths=("~/.cursor/mcp.json", "~/.cursor/settings.json"),
        version_cmd=("cursor", "--version"),
        token_env_vars=(),
        token_file_paths=("~/.cursor/mcp.json",),
        monogram="CU",
    ),
    RuntimeDescriptor(
        host_id="vscode",
        display_name="VS Code (MCP)",
        binary_names=("code",),
        gui_install_paths_windows=(r"%LOCALAPPDATA%\Programs\Microsoft VS Code",),
        gui_install_paths_posix=("/Applications/Visual Studio Code.app",),
        config_paths=("~/.vscode/extensions",),
        version_cmd=("code", "--version"),
        token_env_vars=(),
        token_file_paths=(),
        monogram="VS",
    ),
    RuntimeDescriptor(
        host_id="windsurf",
        display_name="Windsurf",
        binary_names=("windsurf",),
        gui_install_paths_windows=(r"%LOCALAPPDATA%\Programs\windsurf",),
        gui_install_paths_posix=("/Applications/Windsurf.app",),
        config_paths=("~/.codeium/windsurf/mcp_config.json",),
        version_cmd=("windsurf", "--version"),
        token_env_vars=(),
        token_file_paths=("~/.codeium/windsurf/mcp_config.json",),
        monogram="WS",
    ),
    RuntimeDescriptor(
        host_id="goose",
        display_name="Goose",
        binary_names=("goose",),
        gui_install_paths_windows=(),
        gui_install_paths_posix=(),
        config_paths=(),
        version_cmd=("goose", "--version"),
        token_env_vars=(),
        token_file_paths=(),
        monogram="GO",
        at_risk=True,
        at_risk_reason="agentic CLI (Block); no documented config/GUI path — confirm before relying on detection",
    ),
    RuntimeDescriptor(
        host_id="gemini",
        display_name="Gemini CLI",
        binary_names=("gemini",),
        gui_install_paths_windows=(),
        gui_install_paths_posix=(),
        config_paths=("~/.gemini/settings.json",),
        version_cmd=("gemini", "--version"),
        token_env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        token_file_paths=(),
        monogram="GM",
        at_risk=True,
        at_risk_reason="Gemini CLI deprecated 2026-06-18 in favor of Antigravity",
    ),
    RuntimeDescriptor(
        host_id="kimi",
        display_name="Kimi (Moonshot)",
        binary_names=("kimi",),
        gui_install_paths_windows=(r"%LOCALAPPDATA%\Programs\kimi",),
        gui_install_paths_posix=("/Applications/Kimi.app",),
        config_paths=("~/.kimi/config.json",),
        version_cmd=(),
        token_env_vars=("MOONSHOT_API_KEY",),
        token_file_paths=(),
        monogram="KM",
    ),
    RuntimeDescriptor(
        host_id="antigravity",
        display_name="Antigravity",
        binary_names=("antigravity",),
        gui_install_paths_windows=(r"%LOCALAPPDATA%\Programs\antigravity",),
        gui_install_paths_posix=("/Applications/Antigravity.app",),
        config_paths=("~/.antigravity/config.json",),
        version_cmd=(),
        token_env_vars=(),
        token_file_paths=(),
        monogram="AG",
    ),
)

_BY_ID: dict[str, RuntimeDescriptor] = {d.host_id: d for d in DESCRIPTORS}


def get_descriptor(host_id: str) -> RuntimeDescriptor:
    """Return the descriptor for ``host_id``; raise ``KeyError`` if unknown."""
    return _BY_ID[host_id]


def _expand(path: str) -> Path:
    """Expand %VAR% (Windows GUI paths) and ~ (home-relative paths)."""
    return Path(os.path.expanduser(os.path.expandvars(path)))


def _first_existing(paths: tuple[str, ...]) -> str | None:
    for raw in paths:
        try:
            candidate = _expand(raw)
        except (ValueError, OSError):
            continue
        if candidate.exists():
            return str(candidate)
    return None


def _probe_binary(binary_names: tuple[str, ...]) -> tuple[bool, str | None]:
    for name in binary_names:
        found = shutil.which(name)
        if found:
            return True, found
    return False, None


def _probe_token_file(paths: tuple[str, ...]) -> tuple[bool, float | None]:
    for raw in paths:
        try:
            candidate = _expand(raw)
            if candidate.exists():
                return True, os.stat(candidate).st_mtime
        except OSError:
            # race / permission — treat as absent rather than crash the probe
            continue
    return False, None


def _probe_version(version_cmd: tuple[str, ...]) -> tuple[str | None, str | None]:
    """Run the version command in a subprocess. Never raises."""
    import subprocess  # deferred: keeps the default path subprocess-free

    try:
        result = subprocess.run(  # noqa: S603 - fixed argv from the static registry
            list(version_cmd),
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc) or exc.__class__.__name__
    if result.returncode != 0:
        raw_err = (result.stderr or "").strip()
        # Apply the same isprintable() filter used for stdout (strips ANSI escape
        # sequences and other control characters from untrusted binary output).
        detail = "".join(ch for ch in raw_err if ch.isprintable())[:_VERSION_OUTPUT_MAX]
        return None, detail or f"version command exited {result.returncode}"
    lines = (result.stdout or "").splitlines()
    first = lines[0].strip() if lines else ""
    # strip terminal control characters: a binary on PATH is untrusted output
    cleaned = "".join(ch for ch in first if ch.isprintable())[:_VERSION_OUTPUT_MAX]
    return (cleaned or None), None


def probe_one(descriptor: RuntimeDescriptor, *, probe_versions: bool = False) -> RuntimeProbe:
    """Probe a single host. Pure detection; the token value is never read."""
    binary_found, binary_path = _probe_binary(descriptor.binary_names)

    gui_paths = (
        descriptor.gui_install_paths_windows
        if os.name == "nt"
        else descriptor.gui_install_paths_posix
    )
    gui_path_found = _first_existing(gui_paths)
    config_path_found = _first_existing(descriptor.config_paths)

    token_env_present = any(name in os.environ for name in descriptor.token_env_vars)
    token_file_present, token_file_mtime = _probe_token_file(descriptor.token_file_paths)

    version_str: str | None = None
    version_error: str | None = None
    if probe_versions and descriptor.version_cmd:
        version_str, version_error = _probe_version(descriptor.version_cmd)

    detected = binary_found or gui_path_found is not None or config_path_found is not None

    return RuntimeProbe(
        host_id=descriptor.host_id,
        binary_found=binary_found,
        binary_path=binary_path,
        gui_path_found=gui_path_found,
        config_path_found=config_path_found,
        version_str=version_str,
        version_error=version_error,
        token_env_present=token_env_present,
        token_file_present=token_file_present,
        token_file_mtime=token_file_mtime,
        detected=detected,
    )


def probe_all(*, probe_versions: bool = False) -> RuntimeReport:
    """Probe every host in ``DESCRIPTORS`` and return a frozen report.

    Each probe is isolated: a failure for one descriptor emits a warning and
    returns a degraded ``RuntimeProbe`` (detected=False, version_error set)
    so the rest of the report is always complete.
    """
    import warnings as _warnings

    probe_results: list[RuntimeProbe] = []
    for d in DESCRIPTORS:
        try:
            probe_results.append(probe_one(d, probe_versions=probe_versions))
        except Exception as exc:
            _warnings.warn(
                f"probe_all: probe_one for {d.host_id!r} raised {type(exc).__name__}: {exc}; "
                "reporting as undetected",
                stacklevel=2,
            )
            probe_results.append(
                RuntimeProbe(
                    host_id=d.host_id,
                    binary_found=False,
                    binary_path=None,
                    gui_path_found=None,
                    config_path_found=None,
                    version_str=None,
                    version_error=f"{type(exc).__name__}: {exc}",
                    token_env_present=False,
                    token_file_present=False,
                    token_file_mtime=None,
                    detected=False,
                )
            )
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return RuntimeReport(probes=tuple(probe_results), generated_at=generated_at, probe_versions=probe_versions)
