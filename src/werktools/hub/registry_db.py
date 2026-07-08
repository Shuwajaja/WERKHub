"""Read-model registry over the scored MCP / skill capability catalog (stdlib sqlite3).

The JSON seed (``registry_seed.json``) and the ledger stay the source of truth; this
module builds a queryable SQLite *read-model* from a list of capability rows. Every
read path FAILS CLOSED: a missing or corrupt database yields empty results, never an
exception, so the hub can never crash because the registry cache is bad. Build is a
deterministic, idempotent rebuild (drop + recreate); it never mutates the source JSON.

Pure stdlib (``sqlite3``). No daemon, no network.
"""

from __future__ import annotations

import json
import re
import sqlite3
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SEED_PATH = Path(__file__).parent / "registry_seed.json"

_TRUST_TIERS = ("Official", "Security-Scanned", "Community-Unverified")
_DEFAULT_TRUST = "Community-Unverified"
# Env-var NAMES only (presence-only): derived from a security note when not given.
_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}_(?:KEY|TOKEN|SECRET)\b")


@dataclass(frozen=True)
class Capability:
    """One catalogued capability (an MCP tool/server or a skill)."""

    id: str
    kind: str
    category: str
    trust_tier: str
    what_it_is: str
    maintainer: str
    maintenance: str
    popularity: str
    security_note: str
    deluxe_reason: str
    deluxe_base: bool
    verified: bool
    needs_keys: tuple[str, ...]
    risk: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Capability":
        d = dict(row)
        raw_keys = str(d.get("needs_keys", "") or "")
        keys = tuple(k.strip() for k in raw_keys.split(",") if k.strip())
        return cls(
            id=str(d.get("id", "")),
            kind=str(d.get("kind", "tool")),
            category=str(d.get("category", "other")),
            trust_tier=str(d.get("trust_tier", _DEFAULT_TRUST)),
            what_it_is=str(d.get("what_it_is", "")),
            maintainer=str(d.get("maintainer", "")),
            maintenance=str(d.get("maintenance", "unknown")),
            popularity=str(d.get("popularity", "unverified")),
            security_note=str(d.get("security_note", "")),
            deluxe_reason=str(d.get("deluxe_reason", "")),
            deluxe_base=bool(d.get("deluxe_base", 0)),
            verified=bool(d.get("verified", 0)),
            needs_keys=keys,
            risk=str(d.get("risk", "unknown")),
        )


def load_seed(path: str | Path = SEED_PATH) -> list[dict[str, Any]]:
    """Load capability rows from the JSON seed. Missing/corrupt -> []."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []
    caps = data.get("capabilities", []) if isinstance(data, dict) else data
    return [c for c in caps if isinstance(c, dict)] if isinstance(caps, list) else []


def _str(val: Any, default: str) -> str:
    """Return ``default`` when ``val`` is None, else ``str(val)``.

    Prevents explicit ``None`` field values from being coerced to the string
    ``'None'`` (which would be indistinguishable from the literal string and
    would bypass guards like the risk gate that compare against known values).
    """
    return default if val is None else str(val)


def _norm(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw row (accepts ``id`` or ``server_id``); trust fails closed."""
    rid = str(row.get("id") or row.get("server_id") or "").strip()
    tier = str(row.get("trust_tier", _DEFAULT_TRUST))
    note = _str(row.get("security_note"), "")
    raw_keys = row.get("needs_keys")
    if isinstance(raw_keys, (list, tuple)):
        need = [str(k).strip() for k in raw_keys if str(k).strip()]
    elif isinstance(raw_keys, str):
        need = [k.strip() for k in raw_keys.split(",") if k.strip()]
    else:
        if raw_keys is not None:
            warnings.warn(
                f"_norm: unexpected type for needs_keys: {type(raw_keys).__name__!r}; ignoring",
                stacklevel=2,
            )
        need = []
    if not need:  # derive env-var NAMES from the security note (presence-only)
        need = sorted(set(_KEY_RE.findall(note)))
    resolved_tier = tier if tier in _TRUST_TIERS else _DEFAULT_TRUST
    if resolved_tier != tier:
        warnings.warn(
            f"_norm: unrecognised trust_tier {tier!r} for id {rid!r}; falling back to {_DEFAULT_TRUST!r}",
            stacklevel=2,
        )
    return {
        "id": str(rid),
        "kind": _str(row.get("kind"), "tool"),
        "category": _str(row.get("category"), "other"),
        "trust_tier": resolved_tier,
        "what_it_is": _str(row.get("what_it_is"), ""),
        "maintainer": _str(row.get("maintainer"), ""),
        "maintenance": _str(row.get("maintenance"), "unknown"),
        "popularity": _str(row.get("popularity"), "unverified"),
        "security_note": note,
        "deluxe_reason": _str(row.get("deluxe_reason"), ""),
        "deluxe_base": 1 if row.get("deluxe_base") else 0,
        "verified": 1 if row.get("verified") else 0,
        "needs_keys": ",".join(need),
        "risk": _str(row.get("risk"), "unknown"),
    }


_INSERT = (
    "INSERT INTO capabilities VALUES (:id,:kind,:category,:trust_tier,:what_it_is,"
    ":maintainer,:maintenance,:popularity,:security_note,:deluxe_reason,:deluxe_base,:verified,"
    ":needs_keys,:risk)"
)


def build_registry(db_path: str | Path, rows: list[dict[str, Any]]) -> int:
    """(Re)build the read-model from ``rows``. Idempotent rebuild; returns row count.

    The entire DDL + DML is wrapped in an explicit transaction: if any step
    fails the prior data is preserved (rollback) and the partially-built table
    is never committed.  Non-dict rows are skipped with ``warnings.warn`` so a
    single corrupt entry cannot destroy the whole rebuild.
    """
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = None
    try:
        conn = sqlite3.connect(str(p))
        conn.execute("BEGIN")
        conn.execute("DROP TABLE IF EXISTS capabilities")
        conn.execute(
            "CREATE TABLE capabilities ("
            "id TEXT PRIMARY KEY, kind TEXT, category TEXT, trust_tier TEXT, what_it_is TEXT, "
            "maintainer TEXT, maintenance TEXT, popularity TEXT, security_note TEXT, "
            "deluxe_reason TEXT, deluxe_base INTEGER, verified INTEGER, needs_keys TEXT, risk TEXT)"
        )
        seen: set[str] = set()
        count = 0
        for raw in rows:
            if not isinstance(raw, dict):
                warnings.warn(
                    f"build_registry: skipping non-dict row {type(raw).__name__!r}",
                    stacklevel=2,
                )
                continue
            norm = _norm(raw)
            if not norm["id"]:
                warnings.warn(
                    f"build_registry: skipping row with empty id (raw={raw!r})",
                    stacklevel=2,
                )
                continue
            if norm["id"] in seen:
                warnings.warn(
                    f"build_registry: duplicate id {norm['id']!r} — second occurrence dropped",
                    stacklevel=2,
                )
                continue
            seen.add(norm["id"])
            conn.execute(_INSERT, norm)
            count += 1
        conn.commit()
        return count
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()


def _read(db_path: str | Path, sql: str, params: tuple[Any, ...] = ()) -> list[Capability]:
    """Run a read query, failing closed to [] on a missing or corrupt database."""
    p = Path(db_path)
    if not p.exists():
        return []
    try:
        conn = sqlite3.connect(str(p))
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(sql, params)
            return [Capability.from_row(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        warnings.warn(
            f"registry _read failed (db={str(p)!r}): {exc}",
            stacklevel=2,
        )
        return []


def query_capabilities(
    db_path: str | Path,
    *,
    kind: str | None = None,
    category: str | None = None,
    trust_tier: str | None = None,
    deluxe_only: bool = False,
    search: str | None = None,
    limit: int = 200,
) -> list[Capability]:
    """Filtered read of the registry. Deluxe + higher-trust float to the top."""
    where: list[str] = []
    params: list[Any] = []
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if category:
        where.append("category = ?")
        params.append(category)
    if trust_tier:
        where.append("trust_tier = ?")
        params.append(trust_tier)
    if deluxe_only:
        where.append("deluxe_base = 1")
    if search:
        where.append(
            "(id LIKE ? ESCAPE '\\' OR what_it_is LIKE ? ESCAPE '\\' OR maintainer LIKE ? ESCAPE '\\')"
        )
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        params.extend([like, like, like])
    sql = "SELECT * FROM capabilities"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += (
        " ORDER BY deluxe_base DESC,"
        " CASE trust_tier WHEN 'Official' THEN 0 WHEN 'Security-Scanned' THEN 1 ELSE 2 END ASC,"
        " id ASC LIMIT ?"
    )
    params.append(max(1, int(limit)))
    return _read(db_path, sql, tuple(params))


def get_capability(db_path: str | Path, cap_id: str) -> Capability | None:
    """Fetch one capability by id, or None (also None on missing/corrupt DB)."""
    rows = _read(db_path, "SELECT * FROM capabilities WHERE id = ?", (str(cap_id),))
    return rows[0] if rows else None


def category_counts(db_path: str | Path) -> dict[str, int]:
    """Capability count per category; {} on a missing or corrupt database."""
    p = Path(db_path)
    if not p.exists():
        return {}
    try:
        conn = sqlite3.connect(str(p))
        try:
            cur = conn.execute("SELECT category, COUNT(*) FROM capabilities GROUP BY category ORDER BY category")
            return {str(k): int(v) for k, v in cur.fetchall()}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        warnings.warn(
            f"registry _category_counts failed (db={str(p)!r}): {exc}",
            stacklevel=2,
        )
        return {}
