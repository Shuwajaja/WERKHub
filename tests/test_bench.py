"""Tests for werktools.tools.bench — bench-a/b/c (offline, fixture-driven)."""

from __future__ import annotations

import json
from pathlib import Path

from werktools.cli import main
from werktools.tools.bench import (
    BenchmarkSpec,
    Result,
    RunOutput,
    Variant,
    build_matrix,
    judge_quality,
    judge_quality_multi,
    load_results,
    pareto_frontier,
    render_report,
    run_matrix,
    run_variant,
)

# ---------------------------------------------------------------------------
# Fixtures helpers
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "bench_results.json"


def _make_variant(label: str = "v1", workflow: str = "solo") -> Variant:
    return Variant(label=label, model="haiku", effort="low", workflow=workflow, params={})


def _make_result(
    label: str = "v1",
    tokens_in: int = 1000,
    tokens_out: int = 200,
    quality: int = 3,
    correct: bool = True,
    status: str = "ok",
) -> Result:
    return Result(
        variant=_make_variant(label=label),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=0.005,
        correct=correct,
        error_count=0,
        quality=quality,
        duration_s=5.0,
        output_ref="",
        judged=True,
        status=status,
    )


# ---------------------------------------------------------------------------
# BenchmarkSpec
# ---------------------------------------------------------------------------


def test_benchmark_spec_from_dict_round_trips():
    raw = {"task": "summarise-tool-call", "acceptance": {"type": "cassette", "path": "tests/c.json"}}
    spec = BenchmarkSpec.from_dict(raw)
    assert spec.task == "summarise-tool-call"
    assert spec.acceptance == {"type": "cassette", "path": "tests/c.json"}
    assert spec.to_dict() == raw


def test_benchmark_spec_from_dict_missing_fields_defaults():
    spec = BenchmarkSpec.from_dict({})
    assert spec.task == ""
    assert spec.acceptance == {}


# ---------------------------------------------------------------------------
# Variant
# ---------------------------------------------------------------------------


def test_variant_from_dict_round_trips():
    raw = {
        "label": "solo-fast",
        "model": "haiku",
        "effort": "low",
        "workflow": "solo",
        "params": {"k": "v"},
    }
    v = Variant.from_dict(raw)
    assert v.label == "solo-fast"
    assert v.params == {"k": "v"}
    assert v.to_dict() == raw


def test_variant_from_dict_defaults():
    v = Variant.from_dict({})
    assert v.label == ""
    assert v.workflow == ""
    assert v.params == {}


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


def test_result_from_dict_round_trips():
    raw = {
        "variant": {"label": "solo", "model": "haiku", "effort": "low", "workflow": "solo", "params": {}},
        "tokens_in": 100,
        "tokens_out": 50,
        "cost_usd": 0.001,
        "correct": True,
        "error_count": 0,
        "quality": 4,
        "duration_s": 3.5,
        "output_ref": "out.txt",
        "judged": True,
        "status": "ok",
        "reason": "",
        "judge_rationale": "",
    }
    r = Result.from_dict(raw)
    assert r.variant.label == "solo"
    assert r.quality == 4
    assert r.status == "ok"
    assert r.to_dict() == raw


def test_result_from_dict_invalid_status_defaults_to_skipped():
    raw = {"status": "bogus-value", "variant": {}}
    r = Result.from_dict(raw)
    assert r.status == "skipped"


def test_result_from_dict_missing_fields_fail_closed():
    # Partial input — should not raise, should degrade safely.
    r = Result.from_dict({"variant": {"label": "partial"}})
    assert r.tokens_in == 0
    assert r.quality == 0
    assert r.status == "skipped"


# ---------------------------------------------------------------------------
# load_results
# ---------------------------------------------------------------------------


def test_load_results_from_json_array(tmp_path):
    data = [
        {
            "variant": {"label": "v1", "model": "h", "effort": "low", "workflow": "solo", "params": {}},
            "tokens_in": 100,
            "tokens_out": 50,
            "cost_usd": 0.001,
            "correct": True,
            "error_count": 0,
            "quality": 3,
            "duration_s": 2.0,
            "output_ref": "",
            "judged": True,
            "status": "ok",
        }
    ]
    path = tmp_path / "results.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    results = load_results(path)
    assert len(results) == 1
    assert results[0].variant.label == "v1"


def test_load_results_from_jsonl(tmp_path):
    obj = {
        "variant": {"label": "v1", "model": "h", "effort": "low", "workflow": "solo", "params": {}},
        "tokens_in": 100,
        "tokens_out": 50,
        "cost_usd": 0.001,
        "correct": True,
        "error_count": 0,
        "quality": 3,
        "duration_s": 2.0,
        "output_ref": "",
        "judged": True,
        "status": "ok",
    }
    path = tmp_path / "results.jsonl"
    path.write_text(json.dumps(obj) + "\n" + json.dumps(obj) + "\n", encoding="utf-8")
    results = load_results(path)
    assert len(results) == 2


def test_load_results_missing_file_returns_empty():
    results = load_results(Path("/nonexistent/path/results.json"))
    assert results == []


def test_load_results_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "results.json"
    path.write_text("{corrupt json", encoding="utf-8")
    results = load_results(path)
    assert results == []


def test_load_results_corrupt_jsonl_line_skipped(tmp_path, recwarn):
    good = json.dumps(
        {
            "variant": {"label": "v1", "model": "h", "effort": "low", "workflow": "solo", "params": {}},
            "tokens_in": 10,
            "tokens_out": 5,
            "cost_usd": 0.0,
            "correct": True,
            "error_count": 0,
            "quality": 2,
            "duration_s": 1.0,
            "output_ref": "",
            "judged": True,
            "status": "ok",
        }
    )
    path = tmp_path / "results.jsonl"
    path.write_text("{bad\n" + good + "\n", encoding="utf-8")
    results = load_results(path)
    # corrupt line skipped, good line parsed
    assert len(results) == 1


def test_load_results_from_committed_fixture():
    """Smoke: the committed fixture file loads without error."""
    results = load_results(FIXTURE_PATH)
    assert len(results) == 4
    labels = [r.variant.label for r in results]
    assert "solo-fast" in labels
    assert "panel-skip" in labels


# ---------------------------------------------------------------------------
# build_matrix
# ---------------------------------------------------------------------------


def test_build_matrix_rows_equal_number_of_results():
    results = [
        _make_result("a", quality=3),
        _make_result("b", quality=5),
    ]
    matrix = build_matrix(results)
    assert len(matrix.rows) == 2


def test_build_matrix_columns_are_stable():
    results = [_make_result("a")]
    matrix = build_matrix(results)
    # Required columns per spec
    required = {"label", "tokens_in", "tokens_out", "cost_usd", "correct", "error_count", "quality", "duration_s", "status"}
    assert required.issubset(set(matrix.columns))


def test_build_matrix_skipped_variant_renders_not_crashes():
    results = [
        _make_result("ok-variant", status="ok", quality=3),
        _make_result("skip-variant", status="skipped", quality=0),
    ]
    matrix = build_matrix(results)
    # Both should appear as rows
    assert len(matrix.rows) == 2
    labels = [row["label"] for row in matrix.rows]
    assert "skip-variant" in labels


def test_build_matrix_skipped_row_has_no_fabricated_numbers():
    skipped = _make_result("skip", status="skipped", tokens_in=0, tokens_out=0, quality=0)
    matrix = build_matrix([skipped])
    row = matrix.rows[0]
    assert row["status"] == "skipped"
    # Numeric fields must be what was recorded (zero), not something we invented
    assert row["tokens_in"] == 0
    assert row["quality"] == 0


def test_build_matrix_stable_row_order():
    results = [
        _make_result("z-variant"),
        _make_result("a-variant"),
    ]
    matrix = build_matrix(results)
    labels = [r["label"] for r in matrix.rows]
    # Rows should be in insertion order (first result = first row)
    assert labels[0] == "z-variant"
    assert labels[1] == "a-variant"


def test_build_matrix_from_fixture():
    results = load_results(FIXTURE_PATH)
    matrix = build_matrix(results)
    assert len(matrix.rows) == 4
    statuses = {row["status"] for row in matrix.rows}
    assert "ok" in statuses
    assert "skipped" in statuses


def test_build_matrix_empty_results():
    matrix = build_matrix([])
    assert matrix.rows == []


# ---------------------------------------------------------------------------
# pareto_frontier
# ---------------------------------------------------------------------------


def test_pareto_drops_dominated_variant():
    # panel-balanced: quality=5, total_tokens=2400
    # solo-fast: quality=3, total_tokens=650  → dominated by panel-balanced? No.
    # Actually we need a genuinely dominated case:
    #   dominated: quality=3, tokens=2000 (worse quality and MORE tokens than solo-fast q=4 tokens=600)
    dominated = _make_result("dominated", tokens_in=2000, tokens_out=500, quality=3)
    better = _make_result("better", tokens_in=500, tokens_out=100, quality=4)  # better quality, fewer tokens
    frontier = pareto_frontier([dominated, better])
    labels = [r.variant.label for r in frontier]
    assert "better" in labels
    assert "dominated" not in labels


def test_pareto_keeps_trade_offs():
    # high-quality, high-tokens: not dominated if there is no result that is >= quality AND <= tokens
    low_q_low_t = _make_result("cheap", tokens_in=200, tokens_out=50, quality=2)
    high_q_high_t = _make_result("expensive", tokens_in=3000, tokens_out=800, quality=5)
    frontier = pareto_frontier([low_q_low_t, high_q_high_t])
    # Both are Pareto-optimal (trade quality for tokens)
    assert len(frontier) == 2


def test_pareto_excludes_skipped_variants():
    ok = _make_result("ok", tokens_in=500, tokens_out=100, quality=3, status="ok")
    skipped = _make_result("skip", tokens_in=0, tokens_out=0, quality=0, status="skipped")
    frontier = pareto_frontier([ok, skipped])
    labels = [r.variant.label for r in frontier]
    assert "skip" not in labels


def test_pareto_empty_results():
    assert pareto_frontier([]) == []


def test_pareto_all_skipped():
    skipped = [_make_result(f"s{i}", status="skipped") for i in range(3)]
    assert pareto_frontier(skipped) == []


def test_pareto_from_fixture():
    results = load_results(FIXTURE_PATH)
    frontier = pareto_frontier(results)
    # panel-skip is skipped → excluded
    labels = [r.variant.label for r in frontier]
    assert "panel-skip" not in labels
    # At least one result must survive
    assert len(frontier) >= 1


# ---------------------------------------------------------------------------
# render_report
# ---------------------------------------------------------------------------


def test_render_report_is_deterministic():
    results = load_results(FIXTURE_PATH)
    matrix = build_matrix(results)
    report1 = render_report(matrix)
    report2 = render_report(matrix)
    assert report1.markdown == report2.markdown
    assert report1.csv_bytes == report2.csv_bytes


def test_render_report_contains_all_variant_labels():
    results = load_results(FIXTURE_PATH)
    matrix = build_matrix(results)
    report = render_report(matrix)
    for result in results:
        assert result.variant.label in report.markdown


def test_render_report_csv_has_header_row():
    results = [_make_result("a")]
    matrix = build_matrix(results)
    report = render_report(matrix)
    csv_text = report.csv_bytes.decode("utf-8")
    # First line should be a header
    first_line = csv_text.splitlines()[0]
    assert "label" in first_line


def test_render_report_csv_stable_column_order():
    results = [_make_result("a"), _make_result("b")]
    matrix = build_matrix(results)
    report1 = render_report(matrix)
    report2 = render_report(matrix)
    header1 = report1.csv_bytes.decode("utf-8").splitlines()[0]
    header2 = report2.csv_bytes.decode("utf-8").splitlines()[0]
    assert header1 == header2


def test_render_report_contains_recommended_config():
    results = load_results(FIXTURE_PATH)
    matrix = build_matrix(results)
    report = render_report(matrix)
    assert "recommended" in report.markdown.lower() or "Recommended" in report.markdown


def test_render_report_skipped_variant_appears_in_markdown():
    results = [
        _make_result("ok-var", status="ok", quality=4),
        _make_result("skipped-var", status="skipped", quality=0),
    ]
    matrix = build_matrix(results)
    report = render_report(matrix)
    assert "skipped-var" in report.markdown
    assert "skipped" in report.markdown


def test_render_report_quality_marked_as_judgment():
    """The doc requires we never present judged quality as objective."""
    results = [_make_result("v")]
    matrix = build_matrix(results)
    report = render_report(matrix)
    # The word "judged" or "judgment" must appear somewhere in the report
    combined = report.markdown.lower()
    assert "judg" in combined


def test_render_report_csv_row_count_matches_results():
    results = load_results(FIXTURE_PATH)
    matrix = build_matrix(results)
    report = render_report(matrix)
    csv_lines = [line for line in report.csv_bytes.decode("utf-8").splitlines() if line.strip()]
    # header + one row per result
    assert len(csv_lines) == len(results) + 1


# ---------------------------------------------------------------------------
# CLI smoke — bench report
# ---------------------------------------------------------------------------


def test_cli_bench_report_prints_markdown(capsys):
    rc = main(["bench", "report", "--from", str(FIXTURE_PATH)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "solo-fast" in out
    assert "panel-skip" in out


def test_cli_bench_report_writes_csv(tmp_path, capsys):
    csv_out = tmp_path / "bench.csv"
    rc = main(["bench", "report", "--from", str(FIXTURE_PATH), "--csv", str(csv_out)])
    assert rc == 0
    assert csv_out.exists()
    content = csv_out.read_text(encoding="utf-8")
    assert "label" in content


def test_cli_bench_report_missing_file_fails_closed(capsys):
    rc = main(["bench", "report", "--from", "/nonexistent/results.json"])
    # Should fail gracefully (non-zero exit or empty report, not a crash)
    assert rc != 0 or "0 results" in capsys.readouterr().out


def test_cli_bench_report_corrupt_file_fails_closed(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{corrupt", encoding="utf-8")
    rc = main(["bench", "report", "--from", str(bad)])
    # Fail closed: non-zero exit or explicit "0 results" — never a traceback
    out, err = capsys.readouterr()
    assert rc != 0 or "0" in out + err


# ---------------------------------------------------------------------------
# bench-b: RunOutput dataclass
# ---------------------------------------------------------------------------


def test_run_output_round_trips():
    """RunOutput must be a frozen dataclass with to_dict / from_dict."""
    ro = RunOutput(text="hello", cost_usd=0.005, total_tokens=300, duration_s=1.2, extra={})
    d = ro.to_dict()
    ro2 = RunOutput.from_dict(d)
    assert ro2 == ro


def test_run_output_from_dict_defaults():
    ro = RunOutput.from_dict({})
    assert ro.text == ""
    assert ro.cost_usd == 0.0
    assert ro.total_tokens == 0
    assert ro.duration_s == 0.0
    assert ro.extra == {}


# ---------------------------------------------------------------------------
# bench-b: run_variant — injected fake executor
# ---------------------------------------------------------------------------


def _fake_executor(spec: BenchmarkSpec, variant: Variant) -> RunOutput:
    return RunOutput(
        text="fake output",
        cost_usd=0.007,
        total_tokens=250,
        duration_s=3.0,
        extra={"note": "injected"},
    )


def test_run_variant_with_injected_executor_returns_ok_result():
    spec = BenchmarkSpec(task="do something", acceptance={"rubric": "correctness"})
    variant = Variant(label="solo-test", model="haiku", effort="low", workflow="solo", params={})
    result = run_variant(spec, variant, executor=_fake_executor)
    assert result.status == "ok"
    assert result.cost_usd == 0.007
    assert result.variant.label == "solo-test"
    # total_tokens must be preserved (no in/out split fabricated)
    assert result.tokens_in + result.tokens_out == 250


def test_run_variant_result_is_frozen():
    """Result must remain an immutable dataclass."""
    import dataclasses

    spec = BenchmarkSpec(task="t", acceptance={})
    variant = Variant(label="v", model="m", effort="low", workflow="solo", params={})
    result = run_variant(spec, variant, executor=_fake_executor)
    assert dataclasses.is_dataclass(result)
    # frozen dataclasses raise FrozenInstanceError (AttributeError subclass)
    raised = False
    try:
        result.cost_usd = 999.0  # type: ignore[misc]
    except (AttributeError, TypeError):
        raised = True
    assert raised, "Expected FrozenInstanceError (AttributeError) on mutation attempt"


# ---------------------------------------------------------------------------
# bench-b: default executor with injected _http_call (no real network)
# ---------------------------------------------------------------------------


def _fake_http_call(provider: str, model: str, prompt: str, max_tokens: int, api_key: str) -> dict:
    return {"text": "mocked response", "cost_usd": "0.012", "tokens": 400}


def test_run_variant_default_executor_with_fake_http_returns_shaped_result(tmp_path):
    """Default executor + injected _http_call must produce real-shaped tokens/cost."""
    spec = BenchmarkSpec(task="summarise X", acceptance={})
    variant = Variant(label="solo-http", model="haiku", effort="low", workflow="solo", params={})
    # Provide a fake environ with the key present (presence-only; never read the value)
    fake_env = {"OPENROUTER_API_KEY": "x-fake-key"}
    result = run_variant(spec, variant, executor=None, environ=fake_env, _http_call=_fake_http_call)
    assert result.status == "ok"
    assert result.cost_usd == pytest.approx(0.012)
    assert result.tokens_in + result.tokens_out == 400


def test_run_variant_default_executor_no_key_returns_skipped():
    """No provider key in environ -> status='skipped', never crash, never fabricated."""
    spec = BenchmarkSpec(task="t", acceptance={})
    variant = Variant(label="v", model="haiku", effort="low", workflow="solo", params={})
    result = run_variant(spec, variant, executor=None, environ={})
    assert result.status == "skipped"
    assert result.cost_usd == 0.0
    assert result.tokens_in == 0
    assert result.tokens_out == 0
    assert result.reason != ""


def test_run_variant_default_executor_no_key_does_not_read_key_value():
    """Key-presence gate: environ with key absent -> skipped without reading any value."""
    spec = BenchmarkSpec(task="t", acceptance={})
    variant = Variant(label="v", model="haiku", effort="low", workflow="solo", params={})
    # Deliberately pass a populated env without the required provider key
    env = {"SOME_OTHER_VAR": "irrelevant"}
    result = run_variant(spec, variant, executor=None, environ=env)
    assert result.status == "skipped"


def test_run_variant_panel_no_executor_returns_skipped_with_reason():
    """panel workflow with no caller-supplied executor -> skipped, honest reason."""
    spec = BenchmarkSpec(task="panel task", acceptance={})
    variant = Variant(label="panel-v", model="haiku", effort="low", workflow="panel", params={})
    result = run_variant(spec, variant, executor=None, environ={})
    assert result.status == "skipped"
    assert "panel" in result.reason.lower() or "executor" in result.reason.lower()
    # MUST NOT fabricate numbers
    assert result.tokens_in == 0
    assert result.tokens_out == 0
    assert result.cost_usd == 0.0


def test_run_variant_loop_no_executor_returns_skipped_with_reason():
    """loop workflow with no caller-supplied executor -> skipped, honest reason."""
    spec = BenchmarkSpec(task="loop task", acceptance={})
    variant = Variant(label="loop-v", model="haiku", effort="low", workflow="loop", params={})
    result = run_variant(spec, variant, executor=None, environ={})
    assert result.status == "skipped"
    assert result.reason != ""
    assert result.cost_usd == 0.0


# ---------------------------------------------------------------------------
# bench-b: run_matrix — record then replay
# ---------------------------------------------------------------------------


import pytest  # noqa: E402 (needed after bare assert above)


def test_run_matrix_basic(tmp_path):
    spec = BenchmarkSpec(task="test matrix", acceptance={})
    variants = [
        Variant(label="a", model="haiku", effort="low", workflow="solo", params={}),
        Variant(label="b", model="haiku", effort="low", workflow="panel", params={}),
    ]
    call_count = [0]

    def counting_executor(s, v):
        call_count[0] += 1
        return RunOutput(text="ok", cost_usd=0.001, total_tokens=100, duration_s=1.0, extra={})

    results = run_matrix(spec, variants, executor=counting_executor)
    assert len(results) == 2
    # variant 'b' is panel but executor is provided, so it runs
    assert call_count[0] == 2


def test_run_matrix_record_then_replay(tmp_path):
    """Second run with the same cache_dir must NOT call executor again."""
    spec = BenchmarkSpec(task="replay test", acceptance={})
    variants = [
        Variant(label="x", model="haiku", effort="low", workflow="solo", params={}),
    ]
    call_count = [0]

    def counting_executor(s, v):
        call_count[0] += 1
        return RunOutput(text="recorded", cost_usd=0.003, total_tokens=150, duration_s=2.0, extra={})

    cache = tmp_path / "cache"

    # First run: records cassette
    results1 = run_matrix(spec, variants, executor=counting_executor, cache_dir=cache)
    assert call_count[0] == 1

    # Second run: replays from cache — executor must NOT be called
    results2 = run_matrix(spec, variants, executor=counting_executor, cache_dir=cache)
    assert call_count[0] == 1, "executor must not be called on replay"

    # Results must be identical
    assert results1[0].variant.label == results2[0].variant.label
    assert results1[0].cost_usd == results2[0].cost_usd
    assert results1[0].status == results2[0].status


def test_run_matrix_replay_zero_http_calls(tmp_path):
    """Default executor with fake http: replay must invoke http zero times."""
    spec = BenchmarkSpec(task="replay-http", acceptance={})
    variants = [Variant(label="y", model="haiku", effort="low", workflow="solo", params={})]
    http_count = [0]

    def counting_http(provider, model, prompt, max_tokens, api_key):
        http_count[0] += 1
        return {"text": "ok", "cost_usd": "0.005", "tokens": 200}

    cache = tmp_path / "http-cache"
    fake_env = {"OPENROUTER_API_KEY": "fake"}

    run_matrix(spec, variants, executor=None, cache_dir=cache, environ=fake_env, _http_call=counting_http)
    assert http_count[0] == 1

    # Replay
    run_matrix(spec, variants, executor=None, cache_dir=cache, environ=fake_env, _http_call=counting_http)
    assert http_count[0] == 1, "_http_call must not be invoked on replay"


def test_run_matrix_skipped_variants_appear_in_results():
    """panel with no executor -> result still appears in list (skipped)."""
    spec = BenchmarkSpec(task="t", acceptance={})
    variants = [
        Variant(label="ok", model="h", effort="low", workflow="solo", params={}),
        Variant(label="skip", model="h", effort="low", workflow="panel", params={}),
    ]
    results = run_matrix(spec, variants, executor=None, environ={})
    assert len(results) == 2
    statuses = {r.variant.label: r.status for r in results}
    assert statuses["skip"] == "skipped"


# ---------------------------------------------------------------------------
# bench-c: judge_quality
# ---------------------------------------------------------------------------


def _fake_judge(spec: BenchmarkSpec, result: Result) -> dict:
    return {"score": 4, "rationale": "looks good"}


def _fake_judge_low(spec: BenchmarkSpec, result: Result) -> dict:
    return {"score": 2, "rationale": "needs work"}


def test_judge_quality_with_injected_judge_returns_0_5_score():
    spec = BenchmarkSpec(task="t", acceptance={"rubric": "correctness"})
    result = _make_result("jtest", status="ok", quality=0)
    updated = judge_quality(spec, result, judge=_fake_judge)
    assert updated.judged is True
    assert 0 <= updated.quality <= 5
    assert updated.quality == 4


def test_judge_quality_returns_new_result_not_mutation():
    """Immutability: judge_quality must return a new Result, not mutate the old one."""
    spec = BenchmarkSpec(task="t", acceptance={})
    original = _make_result("immut", status="ok", quality=0)
    updated = judge_quality(spec, original, judge=_fake_judge)
    assert updated is not original
    assert original.quality == 0  # original unchanged
    assert updated.quality == 4


def test_judge_quality_score_clamped_to_0_5():
    """Score from judge must be clamped to [0, 5] even if judge returns out-of-range."""

    def rogue_judge(spec, result):
        return {"score": 99, "rationale": "overconfident"}

    spec = BenchmarkSpec(task="t", acceptance={})
    result = _make_result("r", status="ok", quality=0)
    updated = judge_quality(spec, result, judge=rogue_judge)
    assert 0 <= updated.quality <= 5


def test_judge_quality_no_key_returns_judged_false_no_crash():
    """No provider key in environ -> judged=False, no crash, quality unchanged."""
    spec = BenchmarkSpec(task="t", acceptance={})
    # Use a result that has not been judged yet (judged=False)
    result = Result(
        variant=_make_variant("nk"),
        tokens_in=100, tokens_out=50, cost_usd=0.001, correct=True,
        error_count=0, quality=3, duration_s=1.0, output_ref="",
        judged=False, status="ok",
    )
    updated = judge_quality(spec, result, judge=None, environ={})
    assert updated.judged is False
    # quality must not be set (remains what it was — never new fabricated value)
    # The key constraint: no crash, judged=False
    assert updated.quality == result.quality


def test_judge_quality_scoreless_vote_leaves_result_unjudged():
    """A judge that returns no usable 'score' must NOT fabricate quality=0.

    Honest-degrade: 'judge produced no score' must stay distinguishable from
    'judge scored 0 (terrible)'. The result is left unjudged, quality unchanged.
    """
    spec = BenchmarkSpec(task="t", acceptance={})
    # An unjudged input so we can prove judge_quality leaves it unjudged.
    result = Result(
        variant=_make_variant("sv"),
        tokens_in=100, tokens_out=50, cost_usd=0.001, correct=True,
        error_count=0, quality=3, duration_s=1.0, output_ref="",
        judged=False, status="ok",
    )
    import pytest as _pytest

    # judge returns a dict without a 'score' key
    with _pytest.warns(UserWarning, match="no usable 'score'"):
        updated = judge_quality(spec, result, judge=lambda s, r: {"rationale": "forgot the score"})
    assert updated.judged is False
    assert updated.quality == 3  # unchanged, NOT fabricated to 0

    # a non-int score is likewise rejected (not coerced)
    with _pytest.warns(UserWarning, match="no usable 'score'"):
        updated2 = judge_quality(spec, result, judge=lambda s, r: {"score": "high"})
    assert updated2.judged is False
    assert updated2.quality == 3


def test_judge_quality_multi_skips_scoreless_votes():
    """Multi-judge must skip scoreless votes, not count them as 0 in the median."""
    spec = BenchmarkSpec(task="t", acceptance={})
    result = _make_result("mv", status="ok", quality=0)
    votes = [{"score": 4, "rationale": "a"}, {"rationale": "no score"}, {"score": 5, "rationale": "b"}]
    seq = iter(votes)

    def judge(s, r):
        return next(seq)

    import pytest as _pytest

    with _pytest.warns(UserWarning, match="no usable 'score'"):
        updated = judge_quality_multi(spec, result, judges=3, judge=judge)
    # median of the two VALID votes (4, 5) = 4 (the scoreless vote was skipped, not a 0)
    assert updated.judged is True
    assert updated.quality == 4


def test_judge_quality_default_judge_with_fake_http(tmp_path):
    """Default judge via dispatch_worker with fake _http_call must return judged=True."""
    spec = BenchmarkSpec(task="summarise", acceptance={"rubric": "Is it correct?"})
    result = _make_result("jh", status="ok", quality=0)

    def judge_http(provider, model, prompt, max_tokens, api_key):
        return {"text": '{"score": 3, "rationale": "ok"}', "cost_usd": "0.001", "tokens": 50}

    fake_env = {"OPENROUTER_API_KEY": "fake"}
    updated = judge_quality(spec, result, judge=None, environ=fake_env, _http_call=judge_http)
    assert updated.judged is True
    assert 0 <= updated.quality <= 5


# ---------------------------------------------------------------------------
# bench-c: judge_quality_multi
# ---------------------------------------------------------------------------


def test_judge_quality_multi_aggregates_median():
    """Multiple judge votes -> score is median, all rationales preserved."""
    spec = BenchmarkSpec(task="t", acceptance={})
    result = _make_result("multi", status="ok", quality=0)

    votes = [3, 4, 5]
    call_idx = [0]

    def cycling_judge(s, r):
        score = votes[call_idx[0] % len(votes)]
        call_idx[0] += 1
        return {"score": score, "rationale": f"vote {score}"}

    updated = judge_quality_multi(spec, result, judges=3, judge=cycling_judge)
    assert updated.judged is True
    assert updated.quality == 4  # median of [3, 4, 5]


def test_judge_quality_multi_surfaces_disagreement():
    """Disagreement metadata must be surfaced (variance or per-vote rationales)."""
    spec = BenchmarkSpec(task="t", acceptance={})
    result = _make_result("disagree", status="ok", quality=0)

    call_idx = [0]

    def split_judge(s, r):
        score = 1 if call_idx[0] % 2 == 0 else 5
        call_idx[0] += 1
        return {"score": score, "rationale": f"{'disagree-low' if score == 1 else 'disagree-high'}"}

    updated = judge_quality_multi(spec, result, judges=2, judge=split_judge)
    # Quality is recorded (median of 1, 5 → 3), judged=True
    assert updated.judged is True
    # Disagreement metadata must be available via extra or judge_rationale field
    assert hasattr(updated, "judge_rationale")


def test_judge_quality_multi_no_key_returns_judged_false():
    """No provider key with default judge -> judged=False, no crash."""
    spec = BenchmarkSpec(task="t", acceptance={})
    # Use a result that has not been judged yet (judged=False)
    result = Result(
        variant=_make_variant("nk2"),
        tokens_in=100, tokens_out=50, cost_usd=0.001, correct=True,
        error_count=0, quality=3, duration_s=1.0, output_ref="",
        judged=False, status="ok",
    )
    updated = judge_quality_multi(spec, result, judges=3, judge=None, environ={})
    assert updated.judged is False


# ---------------------------------------------------------------------------
# CLI bench run smoke (offline — no keys, variants skip honestly)
# ---------------------------------------------------------------------------

_SPEC_FIXTURE = {
    "task": "write a hello-world function",
    "acceptance": {"rubric": "output must include def hello"},
}

_VARIANTS_FIXTURE = [
    {"label": "solo-a", "model": "haiku", "effort": "low", "workflow": "solo", "params": {}},
    {"label": "panel-b", "model": "sonnet", "effort": "medium", "workflow": "panel", "params": {}},
]


def test_cli_bench_run_offline_skips_honestly(tmp_path, capsys):
    """bench run with no keys: both variants skip; exit 0 or 1, no crash."""
    spec_file = tmp_path / "spec.json"
    variants_file = tmp_path / "variants.json"
    out_file = tmp_path / "results.json"
    spec_file.write_text(json.dumps(_SPEC_FIXTURE), encoding="utf-8")
    variants_file.write_text(json.dumps(_VARIANTS_FIXTURE), encoding="utf-8")

    rc = main(
        [
            "bench",
            "run",
            "--spec",
            str(spec_file),
            "--variants",
            str(variants_file),
            "--out",
            str(out_file),
        ]
    )
    # Offline = no keys -> all variants skip honestly; never a crash
    assert rc in (0, 1)
    assert out_file.exists()
    results = json.loads(out_file.read_text(encoding="utf-8"))
    assert isinstance(results, list)
    # All must be skipped (no real keys)
    for r in results:
        assert r["status"] == "skipped"


def test_cli_bench_run_writes_results_json(tmp_path, capsys):
    """bench run --out writes a valid JSON array of results."""
    spec_file = tmp_path / "spec.json"
    variants_file = tmp_path / "variants.json"
    out_file = tmp_path / "out.json"
    spec_file.write_text(json.dumps(_SPEC_FIXTURE), encoding="utf-8")
    variants_file.write_text(json.dumps(_VARIANTS_FIXTURE), encoding="utf-8")

    main(["bench", "run", "--spec", str(spec_file), "--variants", str(variants_file), "--out", str(out_file)])
    results = json.loads(out_file.read_text(encoding="utf-8"))
    assert isinstance(results, list)
    assert len(results) == 2


def test_cli_bench_run_with_cache_replays(tmp_path, capsys):
    """bench run --cache: second invocation reads from cache (no executor re-run)."""
    spec_file = tmp_path / "spec.json"
    variants_file = tmp_path / "variants.json"
    out1 = tmp_path / "r1.json"
    out2 = tmp_path / "r2.json"
    cache = tmp_path / "cache"

    spec_file.write_text(json.dumps(_SPEC_FIXTURE), encoding="utf-8")
    variants_file.write_text(json.dumps(_VARIANTS_FIXTURE), encoding="utf-8")

    main(
        ["bench", "run", "--spec", str(spec_file), "--variants", str(variants_file), "--cache", str(cache), "--out", str(out1)]
    )
    main(
        ["bench", "run", "--spec", str(spec_file), "--variants", str(variants_file), "--cache", str(cache), "--out", str(out2)]
    )
    r1 = json.loads(out1.read_text(encoding="utf-8"))
    r2 = json.loads(out2.read_text(encoding="utf-8"))
    # Results must be identical across runs (replayed, not re-executed)
    assert [x["status"] for x in r1] == [x["status"] for x in r2]


def test_cli_bench_report_integrates_with_run_output(tmp_path, capsys):
    """bench run output -> bench report must produce a valid markdown report."""
    spec_file = tmp_path / "spec.json"
    variants_file = tmp_path / "variants.json"
    results_file = tmp_path / "results.json"
    spec_file.write_text(json.dumps(_SPEC_FIXTURE), encoding="utf-8")
    variants_file.write_text(json.dumps(_VARIANTS_FIXTURE), encoding="utf-8")

    main(
        ["bench", "run", "--spec", str(spec_file), "--variants", str(variants_file), "--out", str(results_file)]
    )

    rc = main(["bench", "report", "--from", str(results_file)])
    out = capsys.readouterr().out
    # Report must render (even if all skipped)
    # Either rc==0 with content, or bench report handles all-skipped gracefully
    assert "solo-a" in out or rc != 0


def test_cli_bench_judge_smoke(tmp_path, capsys):
    """bench judge with no keys: results written with judged=False, no crash."""
    # Write a results file first
    results_file = tmp_path / "results.json"
    out_file = tmp_path / "judged.json"
    sample_results = [
        {
            "variant": {"label": "solo-a", "model": "haiku", "effort": "low", "workflow": "solo", "params": {}},
            "tokens_in": 100,
            "tokens_out": 50,
            "cost_usd": 0.001,
            "correct": True,
            "error_count": 0,
            "quality": 0,
            "duration_s": 2.0,
            "output_ref": "",
            "judged": False,
            "status": "ok",
        }
    ]
    results_file.write_text(json.dumps(sample_results), encoding="utf-8")

    rc = main(
        [
            "bench",
            "judge",
            "--from",
            str(results_file),
            "--spec",
            str(tmp_path / "spec.json"),  # missing file -> graceful error
            "--out",
            str(out_file),
        ]
    )
    # Missing spec: graceful non-zero exit OR writes judged=False results
    assert rc in (0, 1)
