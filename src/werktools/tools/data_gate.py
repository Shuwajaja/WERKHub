"""SQLite-only read gate helpers for WERK Data Gate."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .trace import TraceEvent, append_event, recent_events

_SOURCES = "sources.json"
_PREVIEWS = "previews.json"
_AUDIT = "audit.jsonl"
_REDACTED = "[redacted]"


@dataclass(frozen=True)
class DataSource:
    """One configured SQLite source."""

    source_id: str
    label: str
    sqlite_path: str
    tables: tuple[str, ...]
    masked_columns: tuple[str, ...]
    profiles: tuple[str, ...]
    row_limit: int
    created_at: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DataSource":
        return cls(
            source_id=str(raw["source_id"]),
            label=str(raw.get("label", raw["source_id"])),
            sqlite_path=str(raw["sqlite_path"]),
            tables=tuple(str(item) for item in raw.get("tables", ())),
            masked_columns=tuple(str(item) for item in raw.get("masked_columns", ())),
            profiles=tuple(str(item) for item in raw.get("profiles", ("default",))),
            row_limit=int(raw.get("row_limit", 50)),
            created_at=str(raw.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "label": self.label,
            "sqlite_path": self.sqlite_path,
            "tables": list(self.tables),
            "masked_columns": list(self.masked_columns),
            "profiles": list(self.profiles),
            "row_limit": self.row_limit,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class DataSchema:
    """Allowlisted schema for one data source."""

    source_id: str
    tables: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class QueryPreview:
    """A saved safe read query plan.

    The sql field is display-only and never persisted: query_read rebuilds
    the statement from the authoritative source config at execution time, so
    a tampered previews.json cannot widen access.
    """

    preview_id: str
    source_id: str
    profile: str
    table: str
    sql: str
    masked_columns: tuple[str, ...]
    row_limit: int
    created_at: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "QueryPreview":
        return cls(
            preview_id=str(raw["preview_id"]),
            source_id=str(raw["source_id"]),
            profile=str(raw.get("profile", "default")),
            table=str(raw["table"]),
            sql="",
            masked_columns=tuple(str(item) for item in raw.get("masked_columns", ())),
            row_limit=int(raw.get("row_limit", 50)),
            created_at=str(raw.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "preview_id": self.preview_id,
            "source_id": self.source_id,
            "profile": self.profile,
            "table": self.table,
            "masked_columns": list(self.masked_columns),
            "row_limit": self.row_limit,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class QueryResult:
    """Bounded rows returned from an approved preview."""

    preview_id: str
    source_id: str
    rows: tuple[dict[str, Any], ...]
    rows_returned: int
    masked_columns: tuple[str, ...]
    audit_id: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _root(root: str | Path) -> Path:
    return Path(root)


def _sources_path(root: str | Path) -> Path:
    return _root(root) / _SOURCES


def _previews_path(root: str | Path) -> Path:
    return _root(root) / _PREVIEWS


def _audit_path(root: str | Path) -> Path:
    return _root(root) / _AUDIT


def init_data_gate(root: str | Path) -> Path:
    """Create an empty local Data Gate directory if needed."""
    gate = _root(root)
    gate.mkdir(parents=True, exist_ok=True)
    if not _sources_path(gate).exists():
        _sources_path(gate).write_text("[]\n", encoding="utf-8")
    if not _previews_path(gate).exists():
        _previews_path(gate).write_text("[]\n", encoding="utf-8")
    return gate


def _load_sources(root: str | Path) -> list[DataSource]:
    init_data_gate(root)
    raw = json.loads(_sources_path(root).read_text(encoding="utf-8") or "[]")
    if not isinstance(raw, list):
        return []
    return [DataSource.from_dict(item) for item in raw if isinstance(item, dict)]


def _write_sources(root: str | Path, values: list[DataSource]) -> None:
    init_data_gate(root)
    _sources_path(root).write_text(
        json.dumps([source.to_dict() for source in values], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_previews(root: str | Path) -> list[QueryPreview]:
    init_data_gate(root)
    raw = json.loads(_previews_path(root).read_text(encoding="utf-8") or "[]")
    if not isinstance(raw, list):
        return []
    return [QueryPreview.from_dict(item) for item in raw if isinstance(item, dict)]


def _write_previews(root: str | Path, values: list[QueryPreview]) -> None:
    init_data_gate(root)
    _previews_path(root).write_text(
        json.dumps([preview.to_dict() for preview in values], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _tuple(value: tuple[str, ...] | list[str] | None, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    result = tuple(str(item) for item in value or default if str(item))
    return result


def _is_visible(source: DataSource, profile: str) -> bool:
    return "*" in source.profiles or profile in source.profiles


def _source_map(root: str | Path) -> dict[str, DataSource]:
    return {source.source_id: source for source in _load_sources(root)}


def _source_or_raise(root: str | Path, source_id: str) -> DataSource:
    source = _source_map(root).get(source_id)
    if source is None:
        raise KeyError(f"unknown data source: {source_id}")
    return source


def _audit(root: str | Path, event_type: str, profile: str, payload: dict[str, Any]) -> TraceEvent:
    return append_event(_audit_path(root), event_type, actor=profile, payload=payload)


def _connect_readonly(path: str) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _preview_id(source_id: str, profile: str, table: str, created_at: str) -> str:
    digest = hashlib.sha256(f"{source_id}\n{profile}\n{table}\n{created_at}\n{uuid.uuid4().hex}".encode("utf-8"))
    return f"dq_{digest.hexdigest()[:12]}"


def add_source(
    root: str | Path,
    source_id: str,
    sqlite_path: str | Path,
    label: str | None = None,
    tables: tuple[str, ...] | list[str] | None = None,
    masked_columns: tuple[str, ...] | list[str] | None = None,
    profiles: tuple[str, ...] | list[str] | None = None,
    row_limit: int = 50,
) -> DataSource:
    """Configure one local SQLite source with an allowlist."""
    table_values = _tuple(tables)
    if not table_values:
        raise ValueError("Data Gate sources require at least one allowlisted table")
    path = Path(sqlite_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"SQLite source not found: {path}")
    source = DataSource(
        source_id=source_id,
        label=label or source_id,
        sqlite_path=str(path),
        tables=table_values,
        masked_columns=_tuple(masked_columns),
        profiles=_tuple(profiles, ("default",)) or ("default",),
        row_limit=max(1, int(row_limit)),
        created_at=_now_iso(),
    )
    known = [item for item in _load_sources(root) if item.source_id != source.source_id]
    known.append(source)
    _write_sources(root, sorted(known, key=lambda item: item.source_id))
    return source


def sources(root: str | Path, profile: str | None = None) -> list[DataSource]:
    """List configured sources, optionally filtered by profile visibility."""
    values = _load_sources(root)
    if profile is None:
        return values
    return [source for source in values if _is_visible(source, profile)]


def schema(root: str | Path, source_id: str, profile: str = "default") -> DataSchema:
    """Describe allowlisted tables for a visible source."""
    source = _source_or_raise(root, source_id)
    if not _is_visible(source, profile):
        _audit(root, "data.schema.denied", profile, {"source_id": source.source_id})
        raise PermissionError(f"source {source_id!r} is not visible to profile {profile!r}")
    tables: dict[str, tuple[str, ...]] = {}
    with _connect_readonly(source.sqlite_path) as connection:
        for table in source.tables:
            rows = connection.execute(f"pragma table_info({_quote_identifier(table)})").fetchall()
            columns = tuple(str(row["name"]) for row in rows)
            if columns:
                tables[table] = columns
    return DataSchema(source_id=source.source_id, tables=tables)


def _choose_table(source: DataSource, intent: str) -> str:
    lowered = intent.lower()
    for table in source.tables:
        if table.lower() in lowered:
            return table
    return source.tables[0]


def query_preview(root: str | Path, source_id: str, intent: str, profile: str = "default") -> QueryPreview:
    """Create and store a safe read-only query preview without executing it."""
    source = _source_or_raise(root, source_id)
    if not _is_visible(source, profile):
        _audit(root, "data.preview.denied", profile, {"source_id": source.source_id, "intent": intent})
        raise PermissionError(f"source {source_id!r} is not visible to profile {profile!r}")

    table = _choose_table(source, intent)
    table_schema = schema(root, source_id, profile=profile).tables.get(table)
    if not table_schema:
        raise ValueError(f"allowlisted table {table!r} is not present in source {source_id!r}")
    columns = ", ".join(_quote_identifier(column) for column in table_schema)
    sql = f"select {columns} from {_quote_identifier(table)} limit {source.row_limit}"
    created_at = _now_iso()
    preview = QueryPreview(
        preview_id=_preview_id(source.source_id, profile, table, created_at),
        source_id=source.source_id,
        profile=profile,
        table=table,
        sql=sql,
        masked_columns=source.masked_columns,
        row_limit=source.row_limit,
        created_at=created_at,
    )
    previews = [item for item in _load_previews(root) if item.preview_id != preview.preview_id]
    previews.append(preview)
    _write_previews(root, previews)
    _audit(
        root,
        "data.query.previewed",
        profile,
        {"preview_id": preview.preview_id, "source_id": source.source_id, "table": table},
    )
    return preview


def _preview_or_raise(root: str | Path, preview_id: str) -> QueryPreview:
    for preview in _load_previews(root):
        if preview.preview_id == preview_id:
            return preview
    raise KeyError(f"unknown query preview: {preview_id}")


def query_read(root: str | Path, preview_id: str, profile: str = "default") -> QueryResult:
    """Execute an approved read-only preview and mask configured columns.

    The persisted preview is treated as a request, not an authority: table
    allowlisting, masking, row limit, and the SQL itself are re-derived from
    the live source config at execution time.
    """
    preview = _preview_or_raise(root, preview_id)
    source = _source_or_raise(root, preview.source_id)
    if preview.profile != profile or not _is_visible(source, profile):
        _audit(root, "data.query.denied", profile, {"preview_id": preview_id, "source_id": source.source_id})
        raise PermissionError(f"preview {preview_id!r} is not approved for profile {profile!r}")
    if preview.table not in source.tables:
        _audit(
            root,
            "data.query.denied",
            profile,
            {"preview_id": preview_id, "source_id": source.source_id, "table": preview.table},
        )
        raise PermissionError(f"table {preview.table!r} is not on the allowlist of source {source.source_id!r}")

    row_limit = max(1, min(preview.row_limit, source.row_limit))
    table_schema = schema(root, source.source_id, profile=profile).tables.get(preview.table)
    if not table_schema:
        raise ValueError(f"allowlisted table {preview.table!r} is not present in source {source.source_id!r}")
    columns = ", ".join(_quote_identifier(column) for column in table_schema)
    sql = f"select {columns} from {_quote_identifier(preview.table)} limit {row_limit}"

    with _connect_readonly(source.sqlite_path) as connection:
        rows = connection.execute(sql).fetchall()
    masked = {column.lower() for column in source.masked_columns}
    result_rows: list[dict[str, Any]] = []
    for row in rows[:row_limit]:
        result_rows.append({key: _REDACTED if key.lower() in masked else row[key] for key in row.keys()})
    audit = _audit(
        root,
        "data.query.completed",
        profile,
        {
            "preview_id": preview.preview_id,
            "source_id": source.source_id,
            "rows_returned": len(result_rows),
            "masked_columns": list(source.masked_columns),
        },
    )
    return QueryResult(
        preview_id=preview.preview_id,
        source_id=source.source_id,
        rows=tuple(result_rows),
        rows_returned=len(result_rows),
        masked_columns=source.masked_columns,
        audit_id=audit.event_id,
    )


def audit_recent(root: str | Path, limit: int = 20) -> list[TraceEvent]:
    """Return recent Data Gate audit events."""
    return recent_events(_audit_path(root), limit=limit)
