import json
import sqlite3

from werktools.cli import main
from werktools.tools.data_gate import (
    add_source,
    audit_recent,
    query_preview,
    query_read,
    schema,
    sources,
)


def _db(path):
    con = sqlite3.connect(path)
    con.execute("create table cases (id integer, customer text, email text, severity text)")
    con.execute("insert into cases values (1, 'ACME', 'a@example.com', 'critical')")
    con.execute("insert into cases values (2, 'Globex', 'g@example.com', 'low')")
    con.execute("create table secrets (key text)")
    con.execute("insert into secrets values ('hunter2')")
    con.commit()
    con.close()


def test_add_source_and_schema_show_only_allowlisted_tables(tmp_path):
    db = tmp_path / "support.db"
    _db(db)
    root = tmp_path / "data-gate"

    add_source(root, "support", db, tables=("cases",), masked_columns=("email",), row_limit=1)

    visible = sources(root, profile="default")
    info = schema(root, "support", profile="default")

    assert [source.source_id for source in visible] == ["support"]
    assert list(info.tables) == ["cases"]
    assert info.tables["cases"] == ("id", "customer", "email", "severity")


def test_preview_and_read_mask_columns_and_enforce_limit(tmp_path):
    db = tmp_path / "support.db"
    _db(db)
    root = tmp_path / "data-gate"
    add_source(root, "support", db, tables=("cases",), masked_columns=("email",), row_limit=1)

    preview = query_preview(root, "support", "critical cases", profile="default")
    result = query_read(root, preview.preview_id, profile="default")

    assert result.rows_returned == 1
    assert result.rows[0]["email"] == "[redacted]"
    assert result.masked_columns == ("email",)
    assert "limit 1" in preview.sql.lower()


def test_query_read_rejects_tampered_preview_table(tmp_path):
    db = tmp_path / "support.db"
    _db(db)
    root = tmp_path / "data-gate"
    add_source(root, "support", db, tables=("cases",), masked_columns=("email",), row_limit=1)
    preview = query_preview(root, "support", "critical cases", profile="default")

    previews_file = root / "previews.json"
    raw = json.loads(previews_file.read_text(encoding="utf-8"))
    assert "sql" not in raw[0]
    raw[0]["table"] = "secrets"
    previews_file.write_text(json.dumps(raw), encoding="utf-8")

    try:
        query_read(root, preview.preview_id, profile="default")
    except PermissionError as exc:
        assert "allowlist" in str(exc)
    else:
        raise AssertionError("expected tampered table to be rejected")


def test_query_read_ignores_tampered_masking_and_limit(tmp_path):
    db = tmp_path / "support.db"
    _db(db)
    root = tmp_path / "data-gate"
    add_source(root, "support", db, tables=("cases",), masked_columns=("email",), row_limit=1)
    preview = query_preview(root, "support", "critical cases", profile="default")

    previews_file = root / "previews.json"
    raw = json.loads(previews_file.read_text(encoding="utf-8"))
    raw[0]["masked_columns"] = []
    raw[0]["row_limit"] = 999
    raw[0]["sql"] = "select * from secrets"
    previews_file.write_text(json.dumps(raw), encoding="utf-8")

    result = query_read(root, preview.preview_id, profile="default")

    assert result.rows_returned == 1
    assert result.rows[0]["email"] == "[redacted]"


def test_masking_is_case_insensitive(tmp_path):
    db = tmp_path / "crm.db"
    con = sqlite3.connect(db)
    con.execute('create table people (id integer, "Email" text)')
    con.execute("insert into people values (1, 'p@example.com')")
    con.commit()
    con.close()
    root = tmp_path / "data-gate"
    add_source(root, "crm", db, tables=("people",), masked_columns=("email",))

    preview = query_preview(root, "crm", "people", profile="default")
    result = query_read(root, preview.preview_id, profile="default")

    assert result.rows[0]["Email"] == "[redacted]"


def test_denied_profile_cannot_preview_source(tmp_path):
    db = tmp_path / "support.db"
    _db(db)
    root = tmp_path / "data-gate"
    add_source(root, "support", db, tables=("cases",), profiles=("admin",))

    try:
        query_preview(root, "support", "cases", profile="default")
    except PermissionError as exc:
        assert "not visible" in str(exc)
    else:
        raise AssertionError("expected denial")

    assert audit_recent(root, limit=1)[0].event_type == "data.preview.denied"


def test_data_cli_add_source_preview_read_and_audit(tmp_path, capsys):
    db = tmp_path / "support.db"
    _db(db)
    root = tmp_path / "data-gate"

    assert (
        main(
            [
                "data",
                "add-source",
                str(db),
                "--dir",
                str(root),
                "--source",
                "support",
                "--table",
                "cases",
                "--mask",
                "email",
                "--limit",
                "1",
            ]
        )
        == 0
    )
    assert main(["data", "sources", "--dir", str(root)]) == 0
    assert main(["data", "schema", "support", "--dir", str(root)]) == 0
    assert main(["data", "preview", "--source", "support", "--intent", "critical cases", "--dir", str(root)]) == 0

    preview_id = [
        line for line in capsys.readouterr().out.splitlines() if line.startswith("Preview: ")
    ][-1].split(": ", 1)[1]

    assert main(["data", "read", "--preview-id", preview_id, "--dir", str(root)]) == 0
    out = capsys.readouterr().out
    assert "[redacted]" in out
    assert "a@example.com" not in out

    assert main(["data", "audit", "--dir", str(root), "--limit", "2"]) == 0
    assert "data.query.completed" in capsys.readouterr().out
