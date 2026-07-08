import json

import pytest

from werktools.cli import main
from werktools.tools.vault import (
    add_source,
    audit_recent,
    explain_access,
    search,
    show_item,
)


def test_show_item_reveal_rejects_path_outside_source(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.md").write_text("hello vault", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("api_key: leak-me", encoding="utf-8")
    root = tmp_path / "vault"
    _, items = add_source(root, docs, label="docs")

    index = root / "index.jsonl"
    lines = []
    for line in index.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        raw["path"] = str(outside)
        lines.append(json.dumps(raw, sort_keys=True))
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:
        show_item(root, items[0].item_id, profile="default", reveal_secrets=True)
    except PermissionError:
        pass
    else:
        raise AssertionError("expected reveal outside the source root to be denied")

    assert audit_recent(root, limit=1)[0].event_type == "vault.show.denied"


def test_show_item_audits_reveal_flag(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.md").write_text("plain note", encoding="utf-8")
    root = tmp_path / "vault"
    _, items = add_source(root, docs, label="docs")

    show_item(root, items[0].item_id, profile="default", reveal_secrets=True)

    event = audit_recent(root, limit=1)[0]
    assert event.event_type == "vault.show"
    assert event.payload["revealed"] is True


def test_indexing_skips_symlinked_files(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "real.md").write_text("real note", encoding="utf-8")
    secret = tmp_path / "secret.md"
    secret.write_text("password: hidden", encoding="utf-8")
    link = docs / "link.md"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("symlinks are not available on this platform")
    root = tmp_path / "vault"

    _, items = add_source(root, docs, label="docs")

    assert all("link.md" not in item.path for item in items)


def test_search_survives_corrupt_index_line(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.md").write_text("searchable text", encoding="utf-8")
    root = tmp_path / "vault"
    add_source(root, docs, label="docs")

    index = root / "index.jsonl"
    index.write_text("{corrupt\n" + index.read_text(encoding="utf-8"), encoding="utf-8")

    results = search(root, "searchable", profile="default")

    assert len(results) == 1


def test_add_source_indexes_markdown_with_provenance(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "policy.md").write_text("# Approval Policy\nHuman approval for write tools.", encoding="utf-8")
    vault = tmp_path / "vault"

    source, items = add_source(vault, docs, label="project-docs", classification="internal", owner="ops")

    assert source.label == "project-docs"
    assert source.classification == "internal"
    assert len(items) == 1
    assert items[0].source_id == source.source_id
    assert items[0].path.endswith("policy.md")


def test_search_filters_denied_sources_and_audits_denial(tmp_path):
    public = tmp_path / "public"
    private = tmp_path / "private"
    public.mkdir()
    private.mkdir()
    (public / "notes.md").write_text("approval policy visible", encoding="utf-8")
    (private / "secret.md").write_text("approval policy private", encoding="utf-8")
    vault = tmp_path / "vault"
    add_source(vault, public, label="public", profiles=("default",))
    add_source(vault, private, label="private", profiles=("admin",))

    results = search(vault, "approval", profile="default")

    assert [item.source_label for item in results] == ["public"]
    audits = audit_recent(vault, limit=2)
    assert audits[-1].event_type == "vault.search"
    assert audits[-1].payload["denied_sources"] == ["private"]


def test_show_item_masks_secret_like_text_by_default(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "secrets.md").write_text("api_key: abc123\nsafe note", encoding="utf-8")
    vault = tmp_path / "vault"
    _, items = add_source(vault, docs, label="project-docs")

    item = show_item(vault, items[0].item_id, profile="default")

    assert "abc123" not in item.text
    assert "[redacted]" in item.text
    assert item.source_label == "project-docs"


def test_explain_access_returns_clear_denial(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "notes.md").write_text("internal", encoding="utf-8")
    vault = tmp_path / "vault"
    source, _ = add_source(vault, docs, label="private", profiles=("admin",))

    decision = explain_access(vault, source.source_id, profile="default")

    assert decision.decision == "deny"
    assert "not visible" in decision.reason


def test_vault_add_source_cli_registers_and_searches(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "policy.md").write_text("approval policy", encoding="utf-8")
    vault = tmp_path / "vault"

    code = main(["vault", "add-source", str(docs), "--dir", str(vault), "--label", "project-docs"])
    assert code == 0

    code = main(["vault", "search", "approval", "--dir", str(vault)])
    out = capsys.readouterr().out
    assert code == 0
    assert "project-docs" in out


def test_vault_sources_cli_filters_profile(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "policy.md").write_text("approval policy", encoding="utf-8")
    vault = tmp_path / "vault"
    add_source(vault, docs, label="private", profiles=("admin",))

    code = main(["vault", "sources", "--dir", str(vault), "--profile", "default"])
    default_out = capsys.readouterr().out
    assert code == 0
    assert "private" not in default_out

    code = main(["vault", "sources", "--dir", str(vault), "--profile", "admin"])
    admin_out = capsys.readouterr().out
    assert code == 0
    assert "private" in admin_out


def test_vault_show_cli_prints_masked_item(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "secrets.md").write_text("api_key: abc123\nsafe note", encoding="utf-8")
    vault = tmp_path / "vault"
    _, items = add_source(vault, docs, label="project-docs")

    code = main(["vault", "show", items[0].item_id, "--dir", str(vault)])

    out = capsys.readouterr().out
    assert code == 0
    assert "project-docs" in out
    assert "[redacted]" in out
    assert "abc123" not in out


def test_vault_explain_access_cli_reports_denial(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "policy.md").write_text("approval policy", encoding="utf-8")
    vault = tmp_path / "vault"
    source, _ = add_source(vault, docs, label="private", profiles=("admin",))

    code = main(["vault", "explain-access", source.source_id, "--dir", str(vault), "--profile", "default"])

    out = capsys.readouterr().out
    assert code == 0
    assert "deny" in out


def test_vault_audit_tail_cli_prints_recent_events(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "policy.md").write_text("approval policy", encoding="utf-8")
    vault = tmp_path / "vault"
    add_source(vault, docs, label="project-docs")
    search(vault, "approval")

    code = main(["vault", "audit", "tail", "--dir", str(vault), "--limit", "1"])

    out = capsys.readouterr().out
    assert code == 0
    assert "vault.search" in out
