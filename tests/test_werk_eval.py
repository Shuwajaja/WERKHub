import json

from werktools.cli import main
from werktools.tools.eval import list_cassettes, run_cassette, write_report


def test_list_cassettes_finds_json_files(tmp_path):
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.md").write_text("skip", encoding="utf-8")

    assert [path.name for path in list_cassettes(tmp_path)] == ["a.json"]


def test_run_cassette_reports_pass_and_failure(tmp_path):
    cassette = tmp_path / "policy.json"
    cassette.write_text(
        json.dumps(
            {
                "id": "policy",
                "cases": [
                    {"name": "allow", "expected": {"decision": "allow"}, "actual": {"decision": "allow"}},
                    {"name": "deny", "expected": {"decision": "deny"}, "actual": {"decision": "allow"}},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_cassette(cassette)

    assert result.total == 2
    assert result.passed == 1
    assert result.failed == 1
    assert "deny" in result.diffs[0]


def test_write_report_summarizes_directory(tmp_path):
    cassette = tmp_path / "policy.json"
    out = tmp_path / "eval.md"
    cassette.write_text(
        json.dumps({"id": "policy", "cases": [{"name": "allow", "expected": 1, "actual": 1}]}),
        encoding="utf-8",
    )

    write_report(tmp_path, out)

    assert "WERK Eval Report" in out.read_text(encoding="utf-8")


def test_eval_cli_list_run_and_report(tmp_path, capsys):
    cassette = tmp_path / "policy.json"
    out = tmp_path / "eval.md"
    cassette.write_text(
        json.dumps({"id": "policy", "cases": [{"name": "allow", "expected": 1, "actual": 1}]}),
        encoding="utf-8",
    )

    assert main(["eval", "list", str(tmp_path)]) == 0
    assert "policy.json" in capsys.readouterr().out

    assert main(["eval", "run", str(cassette)]) == 0
    assert "Passed: 1" in capsys.readouterr().out

    assert main(["eval", "report", "--dir", str(tmp_path), "--out", str(out)]) == 0
    assert "WERK Eval Report" in out.read_text(encoding="utf-8")
