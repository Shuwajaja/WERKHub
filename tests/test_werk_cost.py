import json

from werktools.cli import main
from werktools.tools.cost import budget_check, record_cost, rollup_costs, write_report


def test_record_and_rollup_known_costs(tmp_path):
    path = tmp_path / "cost.jsonl"
    record_cost(path, mission="m1", task="docs", tool="model", model="gpt", amount="0.25")
    record_cost(path, mission="m1", task="tests", tool="model", model="gpt", amount="0.75")

    rollup = rollup_costs(path)

    assert rollup.total == "1.00"
    assert rollup.unknown_count == 0
    assert rollup.by_model["gpt"] == "1.00"
    assert rollup.by_task["docs"] == "0.25"


def test_rollup_tracks_unknown_costs_not_zero(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text(json.dumps({"event_type": "model.call", "payload": {"model": "gpt"}}) + "\n", encoding="utf-8")

    rollup = rollup_costs(path)

    assert rollup.total == "0.00"
    assert rollup.unknown_count == 1


def test_lifecycle_events_are_not_cost_relevant(tmp_path):
    path = tmp_path / "trace.jsonl"
    lines = [
        json.dumps({"event_type": "mission.created", "payload": {"mission": "m1"}}),
        json.dumps({"event_type": "task.started", "payload": {"mission": "m1", "task": "docs"}}),
        json.dumps({"event_type": "task.completed", "payload": {"mission": "m1", "task": "docs", "model": "gpt"}}),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rollup = rollup_costs(path)
    decision = budget_check(rollup, budget="1.00")

    assert rollup.event_count == 0
    assert rollup.unknown_count == 0
    assert decision.decision == "allow"


def test_payload_with_amount_is_cost_relevant_regardless_of_type(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text(
        json.dumps({"event_type": "custom.spend", "payload": {"mission": "m1", "amount": "0.40"}}) + "\n",
        encoding="utf-8",
    )

    rollup = rollup_costs(path)

    assert rollup.total == "0.40"
    assert rollup.unknown_count == 0


def test_budget_check_denies_unknown_and_over_budget(tmp_path):
    path = tmp_path / "cost.jsonl"
    record_cost(path, mission="m1", task="docs", tool="model", model="gpt", amount="2.00")

    denied = budget_check(rollup_costs(path), budget="1.00")

    assert denied.decision == "deny"
    assert "exceeds" in denied.reason


def test_load_cost_events_skips_corrupt_lines(tmp_path):
    path = tmp_path / "cost.jsonl"
    record_cost(path, mission="m1", task="docs", tool="model", model="gpt", amount="0.25")
    path.write_text("{corrupt\n" + path.read_text(encoding="utf-8"), encoding="utf-8")

    rollup = rollup_costs(path)

    assert rollup.event_count == 1
    assert rollup.total == "0.25"


def test_budget_check_handles_invalid_budget(tmp_path):
    path = tmp_path / "cost.jsonl"
    record_cost(path, mission="m1", task="docs", tool="model", model="gpt", amount="1.00")

    decision = budget_check(rollup_costs(path), budget="not-a-number")

    assert decision.decision == "error"
    assert "budget" in decision.reason


def test_non_finite_amounts_count_as_unknown(tmp_path):
    path = tmp_path / "cost.jsonl"
    lines = [
        json.dumps({"event_type": "cost.recorded", "payload": {"amount": "NaN"}}),
        json.dumps({"event_type": "cost.recorded", "payload": {"amount": "Infinity"}}),
        json.dumps({"event_type": "cost.recorded", "payload": {"amount": "0.10"}}),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rollup = rollup_costs(path)

    assert rollup.total == "0.10"
    assert rollup.unknown_count == 2


def test_write_report_contains_unknown_count(tmp_path):
    path = tmp_path / "trace.jsonl"
    out = tmp_path / "cost.md"
    path.write_text(json.dumps({"event_type": "model.call", "payload": {"model": "gpt"}}) + "\n", encoding="utf-8")

    write_report(path, out)

    assert "Unknown cost events: 1" in out.read_text(encoding="utf-8")


def test_cost_cli_record_rollup_budget_and_report(tmp_path, capsys):
    path = tmp_path / "cost.jsonl"
    out = tmp_path / "cost.md"

    assert (
        main(
            [
                "cost",
                "record",
                str(path),
                "--mission",
                "m1",
                "--task",
                "docs",
                "--tool",
                "model",
                "--model",
                "gpt",
                "--amount",
                "0.50",
            ]
        )
        == 0
    )
    assert main(["cost", "rollup", str(path)]) == 0
    assert "Total: 0.50" in capsys.readouterr().out

    assert main(["cost", "budget-check", str(path), "--budget", "1.00"]) == 0
    assert "Decision: allow" in capsys.readouterr().out

    assert main(["cost", "report", str(path), "--out", str(out)]) == 0
    assert "WERK Cost Report" in out.read_text(encoding="utf-8")
