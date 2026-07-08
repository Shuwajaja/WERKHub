"""Capability selection — when does a skill/tool enter an agent's context, and when not.

A *capability* is a skill or an MCP tool (the unified `Capability` row from `registry_db`).
This layer adds what the read-model alone doesn't:

- presence-only **key status** — which required keys are set, NEVER a key value, and
- a fail-closed **selection policy** that, for a task + allowed-trust set + budget, returns a
  ranked shortlist WITH a reason per capability (why in / why out) — the "wann rein / wann raus".

Skills load from the local catalog (Markdown cards) as ``kind="skill"``; MCP tools come from
the scored registry seed as ``kind="tool"``. Same selection, different surfacing: a selected
*skill* enters as injected content, a selected *tool* enters as a callable.

Pure stdlib. No network, no daemon. (Distinct from ``hub/capabilities.py``, which lists Hub
tool *cards* from the static config.)
"""

from __future__ import annotations

import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .registry_db import Capability

_WORD_RE = re.compile(r"[a-z0-9]+")
_DEFAULT_ALLOWED: tuple[str, ...] = ("Official", "Security-Scanned")
_RISKY = ("write", "destructive")


def key_status(needs_keys: tuple[str, ...] | list[str]) -> dict[str, bool]:
    """Presence-only: ``{ENV_NAME: is_set}``. Never reads or returns a key VALUE."""
    return {str(name): bool(os.environ.get(str(name))) for name in needs_keys}


def keys_satisfied(cap: Capability) -> bool:
    """True if every key the capability needs is present (or it needs none)."""
    return all(key_status(cap.needs_keys).values()) if cap.needs_keys else True


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD_RE.findall(text.lower()) if len(t) > 2}


def load_skill_capabilities(skills_dir: str | Path) -> list[dict[str, Any]]:
    """Load local Markdown skills as capability rows (``kind='skill'``). Missing dir -> []."""
    from ..catalog import load_cards

    rows: list[dict[str, Any]] = []
    for card in load_cards(skills_dir, "skill"):
        rows.append(
            {
                "id": card.card_id,
                "kind": "skill",
                "category": (card.tags[0] if card.tags else "other"),
                "trust_tier": card.trust_tier,
                "what_it_is": card.summary or card.title,
                "maintainer": "local",
                "maintenance": "active",
                "popularity": "unverified",
                "security_note": "",
                "deluxe_base": False,
                "verified": False,
                "risk": card.risk,
            }
        )
    return rows


@dataclass(frozen=True)
class Decision:
    """One capability's in/out verdict for a task, with a human reason."""

    capability: Capability
    included: bool
    score: int
    reason: str


@dataclass(frozen=True)
class Selection:
    task: str
    included: tuple[Decision, ...]
    excluded: tuple[Decision, ...]


def select_capabilities(
    caps: list[Capability],
    task: str,
    *,
    allowed_tiers: tuple[str, ...] = _DEFAULT_ALLOWED,
    budget: int = 8,
    require_keys: bool = True,
) -> Selection:
    """Decide which capabilities enter context for ``task`` — fail-closed, with reasons.

    Pipeline (each step can exclude): trust gate (deny-by-default) -> relevance (token overlap)
    -> key presence -> risk gate (write/destructive need an approval token) -> budget cap.
    """
    needle = _tokens(task)
    if not needle:
        warnings.warn(
            f"select_capabilities: task {task!r} produced no tokens; all capabilities will score 0",
            stacklevel=2,
        )
    keep: list[Decision] = []
    excluded: list[Decision] = []
    for cap in caps:
        score = len(needle & _tokens(f"{cap.id} {cap.category} {cap.what_it_is}"))
        if cap.trust_tier not in allowed_tiers:
            excluded.append(
                Decision(cap, False, score, f"trust '{cap.trust_tier}' not in allowed {list(allowed_tiers)}")
            )
            continue
        if score == 0:
            excluded.append(Decision(cap, False, 0, "no relevance to the task"))
            continue
        if require_keys and not keys_satisfied(cap):
            missing = [k for k, ok in key_status(cap.needs_keys).items() if not ok]
            excluded.append(Decision(cap, False, score, f"missing key(s): {', '.join(missing)}"))
            continue
        if cap.risk in _RISKY:
            excluded.append(
                Decision(cap, False, score, f"risk '{cap.risk}' requires an approval token (not auto-entered)")
            )
            continue
        keep.append(Decision(cap, True, score, f"relevant (score {score}), trust {cap.trust_tier}, keys ok"))
    keep.sort(key=lambda d: (-d.score, not d.capability.deluxe_base, d.capability.id))
    cap_n = max(0, budget)
    for d in keep[cap_n:]:
        excluded.append(Decision(d.capability, False, d.score, f"over budget (top {cap_n})"))
    return Selection(task=task, included=tuple(keep[:cap_n]), excluded=tuple(excluded))
