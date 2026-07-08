"""Frozen agent-profile dataclass, loader, and card serializer.

Dep-free for JSON and dict input. YAML loading is optional and requires the
`werktools[yaml]` extra.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Profile:
    """Immutable WERK profile/body card."""

    id: str
    role: str
    skills: tuple[str, ...] = ()
    tools_visible: tuple[str, ...] = ()
    tools_allowed: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    instructions: str | None = None
    budget: dict | None = None


def _to_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(str(item) for item in value)


def _from_dict(raw: dict) -> Profile:
    if "id" not in raw:
        raise KeyError("Profile dict is missing required field 'id'")
    if "role" not in raw:
        raise KeyError("Profile dict is missing required field 'role'")

    budget = raw.get("budget")
    if budget is not None and not isinstance(budget, dict):
        budget = None

    instructions = raw.get("instructions")

    return Profile(
        id=str(raw["id"]),
        role=str(raw["role"]),
        skills=_to_str_tuple(raw.get("skills")),
        tools_visible=_to_str_tuple(raw.get("tools_visible")),
        tools_allowed=_to_str_tuple(raw.get("tools_allowed")),
        capabilities=_to_str_tuple(raw.get("capabilities")),
        instructions=str(instructions) if instructions is not None else None,
        budget=budget,
    )


def load_profile(src: str | Path | dict) -> Profile:
    """Load a profile from a dict, JSON file, or YAML file."""
    if isinstance(src, dict):
        return _from_dict(src)

    path = Path(src)
    if not path.exists():
        raise FileNotFoundError(f"Profile file not found: {path}")

    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "Loading .yaml profiles requires pyyaml. "
                "Install it with: pip install werktools[yaml]"
            ) from exc

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Expected a YAML mapping at top level, got {type(raw)}")
        return _from_dict(raw)

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected a JSON object at top level, got {type(raw)}")
    return _from_dict(raw)


def to_card(p: Profile) -> dict:
    """Serialize a Profile to a stable werk.agent-card/1 dict."""
    card: dict[str, Any] = {
        "schema": "werk.agent-card/1",
        "id": p.id,
        "role": p.role,
        "skills": list(p.skills),
        "tools_visible": list(p.tools_visible),
        "tools_allowed": list(p.tools_allowed),
        "capabilities": list(p.capabilities),
    }
    if p.instructions is not None:
        card["instructions"] = p.instructions
    if p.budget is not None:
        card["budget"] = p.budget
    return card
