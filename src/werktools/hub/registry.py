"""Static JSON registry for WERK Hub foundation."""

from __future__ import annotations

import json
from pathlib import Path

from .contracts import HubConfig, HubProfile, ToolCard


def default_config() -> HubConfig:
    """Return a small local default config for deterministic CLI/tests."""
    return HubConfig.from_dict(
        {
            "name": "werk-hub",
            "default_profile": "codex-builder",
            "ledger_path": ".werktools/hub-ledger.jsonl",
            "profiles": [
                {
                    "id": "codex-builder",
                    "label": "Codex Builder",
                    "purpose": "Implementation agent with approval-gated writes.",
                    "permission_profile": "balanced",
                    "visible_tags": ["read", "write", "external", "code"],
                    "hidden_tools": [],
                    "allowed_tools": ["github.create_pr"],
                },
                {
                    "id": "claude-reviewer",
                    "label": "Claude Reviewer",
                    "purpose": "Read-heavy review profile.",
                    "permission_profile": "cautious",
                    "visible_tags": ["read", "docs"],
                    "hidden_tools": ["filesystem.write_file", "github.create_pr", "shell.run"],
                },
                {
                    "id": "human-admin",
                    "label": "Human Admin",
                    "purpose": "Human inspection and approval profile.",
                    "permission_profile": "admin",
                    "visible_tags": ["read", "write", "external", "secret", "unknown"],
                    "hidden_tools": ["shell.run"],
                    "allowed_tools": ["mystery.tool"],
                },
            ],
            "tools": [
                {
                    "id": "docs.search",
                    "server_id": "local-docs",
                    "name": "search",
                    "description": "Search local project documentation.",
                    "tags": ["docs", "read"],
                    "risk": "read",
                    "read_only": True,
                },
                {
                    "id": "filesystem.write_file",
                    "server_id": "filesystem",
                    "name": "write_file",
                    "description": "Write a scoped local file.",
                    "tags": ["write", "code"],
                    "risk": "write",
                    "read_only": False,
                    "requires_approval": True,
                },
                {
                    "id": "github.create_pr",
                    "server_id": "github",
                    "name": "create_pr",
                    "description": "Create a GitHub pull request.",
                    "tags": ["external", "write"],
                    "risk": "external",
                    "read_only": False,
                    "requires_approval": True,
                },
                {
                    "id": "shell.run",
                    "server_id": "shell",
                    "name": "run",
                    "description": "Run a local shell command.",
                    "tags": ["destructive", "code"],
                    "risk": "destructive",
                    "read_only": False,
                    "destructive": True,
                    "source_annotations": {"readOnlyHint": True},
                },
                {
                    "id": "mystery.tool",
                    "server_id": "unknown",
                    "name": "mystery",
                    "description": "Unknown-risk capability card.",
                    "tags": ["unknown"],
                    "risk": "unknown",
                },
            ],
        }
    )


def community_default_config() -> HubConfig:
    """Neutral community-facing default profiles (operator profiles stay separate)."""
    return HubConfig.from_dict(
        {
            "name": "werk-hub",
            "default_profile": "community-reader",
            "ledger_path": ".werktools/hub-ledger.jsonl",
            "profiles": [
                {
                    "id": "community-reader",
                    "label": "Community Reader",
                    "purpose": "Read-only default profile for cautious use.",
                    "permission_profile": "cautious",
                    "visible_tags": ["read", "docs"],
                },
                {
                    "id": "community-builder",
                    "label": "Community Builder",
                    "purpose": "Build profile with approval-gated writes.",
                    "permission_profile": "balanced",
                    "visible_tags": ["read", "write", "external", "code"],
                },
                {
                    "id": "community-admin",
                    "label": "Community Admin",
                    "purpose": "Human admin/approval profile.",
                    "permission_profile": "admin",
                    "visible_tags": ["read", "write", "external", "secret", "unknown"],
                },
            ],
            "tools": [],
            "servers": [],
        }
    )


def save_community_default_config(path: str | Path) -> HubConfig:
    """Write the neutral community default config as deterministic JSON."""
    config = community_default_config()
    save_config(path, config)
    return config


def load_config(src: str | Path | dict | HubConfig) -> HubConfig:
    """Load a HubConfig from a config object, dict, or JSON file."""
    if isinstance(src, HubConfig):
        return src
    if isinstance(src, dict):
        return HubConfig.from_dict(src)

    path = Path(src)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected JSON object at top level, got {type(raw)}")
    return HubConfig.from_dict(raw)


def save_default_config(path: str | Path) -> HubConfig:
    """Write the default static config as deterministic JSON."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    config = default_config()
    target.write_text(
        json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return config


def save_config(path: str | Path, config: HubConfig) -> None:
    """Atomically write a HubConfig to a JSON file via os.replace."""
    import os

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def get_profile(config: HubConfig, profile_id: str | None = None) -> HubProfile:
    """Return a profile by id, defaulting through the config."""
    wanted = profile_id or config.default_profile
    for profile in config.profiles:
        if profile.id == wanted:
            return profile
    raise KeyError(f"Unknown hub profile: {wanted}")


def get_tool(config: HubConfig, tool_id: str) -> ToolCard:
    """Return a tool card by id."""
    for tool in config.tools:
        if tool.id == tool_id:
            return tool
    raise KeyError(f"Unknown hub tool: {tool_id}")


def visible_tools(config: HubConfig, profile_id: str | None = None) -> tuple[ToolCard, ...]:
    """Return tools visible through the selected profile lens."""
    profile = get_profile(config, profile_id)
    visible: list[ToolCard] = []
    visible_tags = set(profile.visible_tags)
    hidden_tools = set(profile.hidden_tools)
    allowed_tools = set(profile.allowed_tools)

    for tool in config.tools:
        if tool.id in hidden_tools:
            continue
        if tool.id in allowed_tools or visible_tags.intersection(tool.tags):
            visible.append(tool)

    return tuple(visible)
