# -*- coding: utf-8 -*-
import json

from market_snapshot import build_phase_snapshot
from outcome_definition import (
    default_outcome_definition,
    evaluate_prediction_outcome,
    reconcile_prediction_outcomes,
)
from prediction_review import (
    append_prediction,
    append_outcome,
    build_prediction_review,
    build_prediction_review_with_reconciliation,
)


def test_outcome_definition_is_versioned_and_json_safe():
    definition = default_outcome_definition()
    payload = definition.to_dict()
    assert payload["outcome_definition_id"] == "market-thesis/v1"
    assert payload["schema_version"] == "outcome/v1"
    assert payload["t1_open_rules"]
    assert payload["t3_persistence_rules"]
    assert json.dumps(payload, ensure_ascii=False)


def test_missing_numeric_facts_are_unknown_not_false():
    definition = default_outcome_definition()
    result = evaluate_prediction_outcome(
        {"scenario_id": "repair_after_breadth_only"},
        previous_snapshot={"breadth_ratio": 0.8, "promotion_rate": 0.2},
        next_snapshot={"report_date": "2026-08-18"},
        horizon="t1",
        definition=definition,
    )
    assert result["hit"] is None
    assert result["status"] == "unknown"
    assert result["missing_fields"]


def test_reconcile_prediction_outcomes_is_idempotent(tmp_path):
    history = tmp_path / "history.jsonl"
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    append_prediction(history, {
        "prediction_id": "2026-08-15:base",
        "report_date": "2026-08-15",
        "scenario_plans": [{"scenario_id": "repair_after_breadth_only", "probability": 0.6}],
        "market_snapshot": {"breadth_ratio": 0.4, "promotion_rate": 0.1, "limit_up": 80, "limit_down": 2},
    })
    for date, breadth, promotion in [
        ("2026-08-15", 0.4, 0.1),
        ("2026-08-18", 0.7, 0.3),
        ("2026-08-19", 0.8, 0.35),
        ("2026-08-21", 0.75, 0.32),
    ]:
        (snapshots / f"{date}.json").write_text(json.dumps({
            "report_date": date, "breadth_ratio": breadth,
            "promotion_rate": promotion, "limit_up": 80, "limit_down": 2,
        }), encoding="utf-8")

    first = reconcile_prediction_outcomes(history, snapshots)
    second = reconcile_prediction_outcomes(history, snapshots)
    assert first["appended"] == 2
    assert second["appended"] == 0
    review = build_prediction_review(history)
    assert review["predictions"]["2026-08-15:base"]["outcomes"]["t1"]["hit"] is True
    assert review["predictions"]["2026-08-15:base"]["outcomes"]["t3"]["hit"] is True


def test_prediction_review_reconciles_outcomes_before_rendering(tmp_path):
    history = tmp_path / "history.jsonl"
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    append_prediction(history, {
        "prediction_id": "2026-08-15:base",
        "report_date": "2026-08-15",
        "scenario_plans": [{"scenario_id": "repair_after_breadth_only"}],
        "market_snapshot": {
            "breadth_ratio": 0.4, "promotion_rate": 0.1,
            "limit_up": 80, "limit_down": 2,
        },
    })
    for day, breadth, promotion in [
        ("2026-08-15", 0.4, 0.1),
        ("2026-08-18", 0.7, 0.3),
        ("2026-08-19", 0.8, 0.35),
        ("2026-08-21", 0.75, 0.32),
    ]:
        (snapshots / f"{day}.json").write_text(
            json.dumps({
                "report_date": day, "breadth_ratio": breadth,
                "promotion_rate": promotion, "limit_up": 80, "limit_down": 2,
            }),
            encoding="utf-8",
        )

    review = build_prediction_review_with_reconciliation(history, snapshots)

    assert review["outcome_reconciliation"]["appended"] == 2
    assert review["predictions"]["2026-08-15:base"]["outcome_status"] == "matured"


def test_prediction_review_reconciliation_failure_is_non_blocking(tmp_path, monkeypatch):
    history = tmp_path / "history.jsonl"
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    append_prediction(history, {"prediction_id": "p1", "report_date": "2026-08-15"})

    import outcome_definition

    def fail(*args, **kwargs):
        raise RuntimeError("snapshot read failed")

    monkeypatch.setattr(outcome_definition, "reconcile_prediction_outcomes", fail)

    review = build_prediction_review_with_reconciliation(history, snapshots)

    assert review["prediction_count"] == 1
    assert review["outcome_reconciliation"]["status"] == "failed"
    assert "snapshot read failed" in review["outcome_reconciliation"]["error"]


def test_outcome_scoring_uses_explicit_primary_scenario():
    result = evaluate_prediction_outcome(
        {
            "primary_scenario_id": "high_level_retreat",
            "scenario_plans": [
                {"scenario_id": "mainline_continuation"},
                {"scenario_id": "high_level_retreat"},
            ],
        },
        previous_snapshot={
            "breadth_ratio": 0.8, "promotion_rate": 0.4,
            "limit_up": 80, "limit_down": 2,
        },
        next_snapshot={
            "report_date": "2026-08-18", "breadth_ratio": 0.7,
            "promotion_rate": 0.3, "limit_up": 60, "limit_down": 10,
        },
        horizon="t1",
    )

    assert result["scenario_id"] == "high_level_retreat"
    assert result["rule_id"] == "t1_negative_feedback"
    assert result["hit"] is True


def test_build_prediction_review_is_read_only(tmp_path):
    from prediction_review import build_prediction_review
    path = tmp_path / "history.jsonl"
    path.write_text(
        '{"event_type":"prediction","prediction_id":"p1","report_date":"2026-08-19"}\n',
        encoding="utf-8",
    )
    before = path.read_bytes()
    review = build_prediction_review(path)
    assert review["prediction_count"] == 1
    assert path.read_bytes() == before


def test_daily_report_builds_prediction_review_without_reconciliation_side_effect():
    import ast
    from pathlib import Path
    source = Path("src/主线强度追踪.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "build_prediction_review" in calls
    assert "build_prediction_review_with_reconciliation" not in calls


def test_reconcile_uses_explicit_trading_calendar_and_does_not_shift_missing_t1(tmp_path):
    from outcome_definition import reconcile_prediction_outcomes
    history = tmp_path / "history.jsonl"
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    append_prediction(history, {
        "prediction_id": "2026-08-15:close:v1",
        "report_date": "2026-08-15",
        "primary_scenario_id": "mainline_continuation",
        "market_snapshot": {"breadth_ratio": 0.5, "promotion_rate": 0.4, "limit_up": 30, "limit_down": 5},
    })
    for day in ("2026-08-19", "2026-08-20"):
        (snapshots / f"{day}.json").write_text(json.dumps({
            "report_date": day, "breadth_ratio": 0.7, "promotion_rate": 0.6,
            "limit_up": 50, "limit_down": 2,
        }, ensure_ascii=False), encoding="utf-8")

    result = reconcile_prediction_outcomes(
        history, snapshots,
        trading_days=["2026-08-15", "2026-08-18", "2026-08-19", "2026-08-20"],
        calendar_source="test_calendar",
    )
    events = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]
    outcomes = [event for event in events if event.get("event_type") == "outcome"]
    assert result["missing_snapshots"] == 1
    assert {(event["horizon"], event["actual"]["expected_trade_date"], event["actual"]["actual_trade_date"])
            for event in outcomes} == {("t3", "2026-08-20", "2026-08-20")}
    assert outcomes[0]["actual"]["calendar_source"] == "test_calendar"


def test_prediction_review_reports_trigger_rate_and_uses_primary_scenario_probability(tmp_path):
    history = tmp_path / "history.jsonl"
    append_prediction(history, {
        "prediction_id": "p1", "report_date": "2026-08-15",
        "primary_scenario_id": "repair",
        "scenario_plans": [{"scenario_id": "repair", "probability": 0.7}],
    })
    append_outcome(history, "p1", "t1", {"triggered": True, "hit": True})
    append_outcome(history, "p1", "t3", {"triggered": True, "hit": True})
    review = build_prediction_review(history)
    assert review["metrics_by_horizon"]["t3"]["trigger_rate"] == 1.0
    assert review["metrics_by_horizon"]["t3"]["hit_rate"] == 1.0
    assert round(review["metrics_by_horizon"]["t3"]["brier_score"], 4) == 0.09


def test_scenario_calibration_uses_historical_hits_for_probability_and_threshold(tmp_path):
    from prediction_review import build_scenario_calibration
    history = tmp_path / "history.jsonl"
    samples = [(0.65, True), (0.70, True), (0.80, True), (0.60, False)]
    for index, (breadth, hit) in enumerate(samples, 1):
        pid = f"p{index}"
        append_prediction(history, {
            "prediction_id": pid, "report_date": f"2026-08-{10 + index:02d}",
            "primary_scenario_id": "mainline_continuation",
            "scenario_plans": [{"scenario_id": "mainline_continuation", "probability": 0.5}],
        })
        append_outcome(history, pid, "t3", {
            "triggered": True, "hit": hit,
            "actual": {"breadth_ratio": breadth, "promotion_rate": 0.6, "limit_down": 2},
        })
    calibration = build_scenario_calibration(history, min_samples=4)
    row = calibration["scenarios"]["mainline_continuation"]
    assert round(row["calibrated_probability"], 4) == round(4 / 6, 4)
    assert row["sample_size"] == 4
    assert row["thresholds"]["breadth_ratio"]["operator"] == "gte"
    assert row["thresholds"]["breadth_ratio"]["value"] == 0.65



def test_prediction_snapshot_is_versioned_complete_and_supersedes_prior():
    from prediction_review import build_prediction_snapshot
    payload = build_prediction_snapshot(
        report_date="2026-08-19", as_of_phase="close", prediction_version="v2",
        market_thesis={"dimensions": {"index_breadth": {"state": "strong"}}},
        scenario_plans=[{"scenario_id": "repair", "trigger_rules": {"auction": []}}],
        market_snapshot={"breadth_ratio": 0.7}, focus_pool=[{"code": "sh600001"}],
        facts_fingerprint="abcdef1234567890", supersedes_prediction_id="old-id",
        generated_at="2026-08-19T16:00:00+08:00",
    )
    assert payload["prediction_schema"] == "market-prediction/v2"
    assert payload["prediction_id"].startswith("2026-08-19:close:v2:")
    assert payload["as_of_phase"] == "close"
    assert payload["prediction_version"] == "v2"
    assert payload["supersedes_prediction_id"] == "old-id"
    assert payload["facts_fingerprint"] == "abcdef1234567890"
    assert payload["market_thesis"] and payload["scenario_plans"] and payload["market_snapshot"]


def test_daily_flow_uses_history_calibration_and_versioned_prediction_snapshot():
    import ast
    from pathlib import Path
    source = Path("src/主线强度追踪.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    names = {node.func.id for node in calls if isinstance(node.func, ast.Name)}
    assert "build_scenario_calibration" in names
    assert "build_prediction_snapshot" in names
    assert "build_scenario_posterior_timeline" in names
    assert "load_phase_snapshots" in names
    assert "f'{_report_date}:base'" not in source


def test_calibration_separates_occurrence_prior_from_conditional_hit_probability(tmp_path):
    from prediction_review import build_scenario_calibration
    history = tmp_path / "history.jsonl"
    rows = [("a", True), ("a", True), ("a", False), ("b", True)]
    for index, (scenario_id, hit) in enumerate(rows):
        pid = f"p{index}"
        append_prediction(history, {
            "prediction_id": pid, "report_date": f"2026-08-{10+index:02d}",
            "primary_scenario_id": scenario_id,
            "scenario_plans": [{"scenario_id": scenario_id, "probability": 0.5}],
        })
        append_outcome(history, pid, "t3", {"triggered": True, "hit": hit, "actual": {"breadth_ratio": 0.6}})
    calibration = build_scenario_calibration(history, min_samples=1)
    a = calibration["scenarios"]["a"]
    b = calibration["scenarios"]["b"]
    assert round(a["scenario_prior_probability"] + b["scenario_prior_probability"], 6) == 1.0
    assert a["scenario_prior_probability"] != a["calibrated_probability"]


def test_posterior_prefers_prior_probability_over_conditional_success_probability():
    from market_snapshot import build_phase_snapshot
    from scenario_posterior import build_scenario_posterior_timeline
    plans = [
        {"scenario_id": "a", "probability": 0.9, "prior_probability": 0.2},
        {"scenario_id": "b", "probability": 0.1, "prior_probability": 0.8},
    ]
    result = build_scenario_posterior_timeline(plans, [build_phase_snapshot(
        report_date="2026-08-19", phase="close", metrics={"breadth_ratio": 0.5},
        captured_at="2026-08-19T15:00:00+08:00",
    ).to_dict()])
    assert result["timeline"][0]["top_scenario_id"] == "b"


def test_daily_flow_does_not_build_legacy_abcd_scenarios():
    import ast
    from pathlib import Path
    source = Path("src/主线强度追踪.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "build_scenario_probabilities" not in names


def test_daily_flow_persists_close_phase_snapshot_for_posterior_baseline():
    import ast
    from pathlib import Path
    source = Path("src/主线强度追踪.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "build_phase_snapshot" in names
    assert "append_phase_snapshot_once" in names


def test_reconciliation_maintenance_uses_authoritative_calendar(tmp_path):
    import pandas as pd
    from outcome_maintenance import run_reconciliation
    history = tmp_path / "history.jsonl"
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    calendar = tmp_path / "calendar.csv"
    append_prediction(history, {
        "prediction_id": "p1", "report_date": "2026-08-14",
        "primary_scenario_id": "high_level_retreat",
        "market_snapshot": {"breadth_ratio": 0.7, "limit_up": 50, "limit_down": 2},
    })
    for day in ("2026-08-17", "2026-08-18", "2026-08-19"):
        (snapshots / f"{day}.json").write_text(json.dumps({
            "report_date": day, "breadth_ratio": 0.4, "limit_up": 20, "limit_down": 10,
        }), encoding="utf-8")
    result = run_reconciliation(
        history_path=history, snapshots_dir=snapshots, calendar_cache=calendar,
        calendar_source=lambda: pd.DataFrame({"trade_date": pd.to_datetime([
            "2026-08-14", "2026-08-17", "2026-08-18", "2026-08-19",
        ])}),
    )
    assert result["appended"] == 2
    assert result["calendar_source"] == "calendar_provider"


def test_ci_runs_reconciliation_and_persists_prediction_state():
    from pathlib import Path
    workflow = Path(".github/workflows/daily_run.yml").read_text(encoding="utf-8")
    assert "python tools/reconcile_prediction_outcomes.py" in workflow
    assert "data/report_prediction_history.jsonl" in workflow
    assert "data/report_daily_snapshots" in workflow
    assert "data/trading_calendar_cache.csv" in workflow


def test_prediction_review_exposes_per_scenario_trigger_hit_and_brier_metrics(tmp_path):
    history = tmp_path / "history.jsonl"
    for index, hit in enumerate((True, False), 1):
        pid = f"p{index}"
        append_prediction(history, {
            "prediction_id": pid, "report_date": f"2026-08-{10+index:02d}",
            "primary_scenario_id": "repair",
            "scenario_plans": [{"scenario_id": "repair", "probability": 0.75}],
        })
        append_outcome(history, pid, "t3", {"triggered": True, "hit": hit})
    review = build_prediction_review(history)
    row = review["metrics_by_scenario"]["repair"]["t3"]
    assert row["trigger_rate"] == 1.0
    assert row["hit_rate"] == 0.5
    assert round(row["brier_score"], 4) == round((0.25**2 + 0.75**2) / 2, 4)


def test_outcome_triggered_is_derived_from_structured_scenario_rules():
    prediction = {
        "primary_scenario_id": "custom",
        "scenario_plans": [{
            "scenario_id": "custom",
            "trigger_rules": {
                "auction": [], "early_0935": [],
                "confirm_1000": [{
                    "rule_id": "custom:confirm:relay", "metric": "promotion_rate",
                    "operator": "gte", "value": 0.6, "required": True,
                }],
                "afternoon": [],
            },
        }],
    }
    result = evaluate_prediction_outcome(
        prediction,
        previous_snapshot={"breadth_ratio": 0.5, "promotion_rate": 0.4, "limit_up": 30, "limit_down": 5},
        next_snapshot={
            "report_date": "2026-08-18", "breadth_ratio": 0.7, "promotion_rate": 0.7,
            "limit_up": 50, "limit_down": 2,
            "phase_snapshots": {
                "confirm_1000": {"metrics": {"promotion_rate": 0.5}},
            },
        },
        horizon="t1",
    )
    assert result["triggered"] is False
    assert result["trigger_evidence"]["invalidating_rule_ids"] == ["custom:confirm:relay"]


def test_conditional_hit_rate_excludes_scenarios_that_never_triggered(tmp_path):
    history = tmp_path / "history.jsonl"
    for pid, report_date, triggered, hit in (
        ("p1", "2026-08-14", True, True), ("p2", "2026-08-15", False, False),
    ):
        append_prediction(history, {
            "prediction_id": pid, "report_date": report_date,
            "primary_scenario_id": "repair",
            "scenario_plans": [{"scenario_id": "repair", "probability": 0.5}],
        })
        append_outcome(history, pid, "t3", {"triggered": triggered, "hit": hit})
    row = build_prediction_review(history)["metrics_by_scenario"]["repair"]["t3"]
    assert row["trigger_rate"] == 0.5
    assert row["scored_count"] == 1
    assert row["hit_rate"] == 1.0


def test_calibration_uses_metric_specific_direction_for_risk_counts(tmp_path):
    from prediction_review import build_scenario_calibration
    history = tmp_path / "history.jsonl"
    samples = [(2, True), (3, True), (10, False), (15, False)]
    for index, (limit_down, hit) in enumerate(samples, 1):
        pid = f"p{index}"
        append_prediction(history, {
            "prediction_id": pid, "report_date": f"2026-08-{10+index:02d}",
            "primary_scenario_id": "mainline_continuation",
        })
        append_outcome(history, pid, "t3", {
            "triggered": True, "hit": hit,
            "actual": {"breadth_ratio": 0.7, "promotion_rate": 0.65, "limit_down": limit_down},
        })
    row = build_scenario_calibration(history, min_samples=4)["scenarios"]["mainline_continuation"]
    assert row["thresholds"]["limit_down"]["operator"] == "lte"
    assert row["thresholds"]["limit_down"]["value"] == 3


def test_outcome_vector_uses_primary_plan_position_boundary():
    result = evaluate_prediction_outcome(
        {
            "primary_scenario_id": "repair_after_breadth_only",
            "scenario_plans": [{
                "scenario_id": "repair_after_breadth_only",
                "position_floor": 0.1, "position_ceiling": 0.4,
            }],
        },
        previous_snapshot={"breadth_ratio": 0.5, "promotion_rate": 0.3, "limit_up": 30, "limit_down": 5},
        next_snapshot={
            "report_date": "2026-08-18", "breadth_ratio": 0.6, "promotion_rate": 0.4,
            "limit_up": 45, "limit_down": 3, "executed_position": 0.3,
        },
        horizon="t1",
    )
    assert result["outcome_vector"]["position_boundary"] == "within"


def test_prediction_snapshot_selects_primary_scenario_by_calibrated_prior():
    from prediction_review import build_prediction_snapshot
    payload = build_prediction_snapshot(
        report_date="2026-08-19", as_of_phase="close", prediction_version="v2",
        market_thesis={}, market_snapshot={}, focus_pool=[], facts_fingerprint="abc123",
        scenario_plans=[
            {"scenario_id": "first", "prior_probability": 0.2},
            {"scenario_id": "likely", "prior_probability": 0.8},
        ],
    )
    assert payload["primary_scenario_id"] == "likely"


def test_reconcile_attaches_intraday_phase_snapshots_for_trigger_scoring(tmp_path):
    history = tmp_path / "history.jsonl"
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    append_prediction(history, {
        "prediction_id": "p1", "report_date": "2026-08-14", "primary_scenario_id": "custom",
        "scenario_plans": [{
            "scenario_id": "custom",
            "trigger_rules": {
                "auction": [], "early_0935": [],
                "confirm_1000": [{
                    "rule_id": "relay", "metric": "promotion_rate", "operator": "gte", "value": 0.6,
                }],
                "afternoon": [],
            },
        }],
        "market_snapshot": {"breadth_ratio": 0.5, "limit_up": 30, "limit_down": 5},
    })
    for day in ("2026-08-17", "2026-08-18", "2026-08-19"):
        (snapshots / f"{day}.json").write_text(json.dumps({
            "report_date": day, "breadth_ratio": 0.7, "promotion_rate": 0.7,
            "limit_up": 50, "limit_down": 2,
        }), encoding="utf-8")
    phase_rows = [build_phase_snapshot(
        report_date="2026-08-14", trade_date="2026-08-17", phase="confirm_1000",
        metrics={"promotion_rate": 0.5}, captured_at="2026-08-17T10:00:00+08:00",
    ).to_dict()]
    result = reconcile_prediction_outcomes(
        history, snapshots,
        trading_days=["2026-08-14", "2026-08-17", "2026-08-18", "2026-08-19"],
        phase_snapshots=phase_rows,
    )
    review = build_prediction_review(history)
    assert result["appended"] == 2
    assert review["predictions"]["p1"]["outcomes"]["t1"]["triggered"] is False
    assert "relay" in review["predictions"]["p1"]["outcomes"]["t1"]["trigger_evidence"]["invalidating_rule_ids"]


def test_outcome_identifies_realized_scenario_from_all_structured_trigger_rules():
    prediction = {
        "primary_scenario_id": "weak",
        "scenario_plans": [
            {"scenario_id": "strong", "trigger_rules": {
                "auction": [{"rule_id": "s", "metric": "breadth_ratio", "operator": "gte", "value": 0.6}],
                "early_0935": [], "confirm_1000": [], "afternoon": [],
            }},
            {"scenario_id": "weak", "trigger_rules": {
                "auction": [{"rule_id": "w", "metric": "breadth_ratio", "operator": "lt", "value": 0.6}],
                "early_0935": [], "confirm_1000": [], "afternoon": [],
            }},
        ],
    }
    result = evaluate_prediction_outcome(
        prediction,
        previous_snapshot={"breadth_ratio": 0.5, "limit_up": 30, "limit_down": 5},
        next_snapshot={
            "report_date": "2026-08-20", "breadth_ratio": 0.7, "limit_up": 50, "limit_down": 2,
            "phase_snapshots": {"auction": {"metrics": {"breadth_ratio": 0.7}}},
        },
        horizon="t1",
    )
    assert result["realized_scenario_id"] == "strong"
    assert result["scenario_trigger_states"]["strong"]["triggered"] is True
    assert result["scenario_trigger_states"]["weak"]["triggered"] is False


def test_calibration_occurrence_prior_uses_realized_scenario_not_predicted_primary(tmp_path):
    from prediction_review import build_scenario_calibration
    history = tmp_path / "history.jsonl"
    for index in range(4):
        pid = f"p{index}"
        append_prediction(history, {
            "prediction_id": pid, "report_date": f"2026-08-{10+index:02d}",
            "primary_scenario_id": "predicted_a",
            "scenario_plans": [
                {"scenario_id": "predicted_a", "probability": 0.5},
                {"scenario_id": "realized_b", "probability": 0.5},
            ],
        })
        append_outcome(history, pid, "t1", {"triggered": True, "hit": True, "realized_scenario_id": "realized_b"})
        append_outcome(history, pid, "t3", {"triggered": True, "hit": True, "actual": {"breadth_ratio": 0.7}})
    calibration = build_scenario_calibration(history, min_samples=1)
    assert calibration["scenarios"]["realized_b"]["scenario_prior_probability"] > calibration["scenarios"]["predicted_a"]["scenario_prior_probability"]


def test_same_day_reruns_collapse_into_one_prediction_sample(tmp_path):
    """同一交易日重跑 N 次只算一个样本 (2026-09-02: 18 天被算成 72 个样本的根因)。"""
    history = tmp_path / "history.jsonl"
    previous = None
    for index in range(3):
        pid = f"2026-09-01:close:v2:fingerprint{index}"
        append_prediction(history, {
            "prediction_id": pid, "report_date": "2026-09-01",
            "as_of_phase": "close", "prediction_version": "v2",
            "generated_at": f"2026-09-01T1{index}:00:00+08:00",
            "supersedes_prediction_id": previous,
            "primary_scenario_id": "repair",
            "scenario_plans": [{"scenario_id": "repair", "probability": 0.5}],
        })
        previous = pid
    review = build_prediction_review(history)
    assert review["revision_count"] == 3
    assert review["prediction_count"] == 1
    assert review["superseded_count"] == 2
    assert list(review["predictions"]) == ["2026-09-01:close:v2:fingerprint2"]


def test_collapsed_revisions_keep_outcomes_backfilled_on_earlier_revisions(tmp_path):
    """T+1/T+3 回填在旧修订上, 之后又重跑一次 —— 结局必须跟着存活修订走, 不能变孤儿。"""
    history = tmp_path / "history.jsonl"
    old_pid = "2026-08-20:close:v2:aaaaaaaaaaaa"
    append_prediction(history, {
        "prediction_id": old_pid, "report_date": "2026-08-20",
        "as_of_phase": "close", "prediction_version": "v2",
        "generated_at": "2026-08-20T16:30:00+08:00",
        "primary_scenario_id": "repair",
        "scenario_plans": [{"scenario_id": "repair", "probability": 0.5}],
    })
    append_outcome(history, old_pid, "t1", {"triggered": True, "hit": True})
    append_outcome(history, old_pid, "t3", {"triggered": True, "hit": True})
    new_pid = "2026-08-20:close:v2:bbbbbbbbbbbb"
    append_prediction(history, {
        "prediction_id": new_pid, "report_date": "2026-08-20",
        "as_of_phase": "close", "prediction_version": "v2",
        "generated_at": "2026-08-20T20:30:00+08:00",
        "supersedes_prediction_id": old_pid,
        "primary_scenario_id": "repair",
        "scenario_plans": [{"scenario_id": "repair", "probability": 0.5}],
    })
    review = build_prediction_review(history)
    assert review["prediction_count"] == 1
    assert review["orphan_outcome_count"] == 0
    assert review["status_counts"]["matured"] == 1
    assert set(review["predictions"][new_pid]["outcomes"]) == {"t1", "t3"}


def test_parallel_revision_chains_keep_the_latest_run(tmp_path):
    """CI 与本地各自接同一个父修订 (并行链), 存活的必须是时间上最新那条。"""
    history = tmp_path / "history.jsonl"
    parent = "2026-08-31:close:v2:parentaaaaaa"
    append_prediction(history, {
        "prediction_id": parent, "report_date": "2026-08-31",
        "as_of_phase": "close", "prediction_version": "v2",
        "generated_at": "2026-08-31T16:21:55+00:00",
    })
    for pid, generated_at in (
        ("2026-08-31:close:v2:localbranch1", "2026-09-01T14:56:32+08:00"),
        ("2026-08-31:close:v2:cibranch00001", "2026-09-01T06:15:31+00:00"),
    ):
        append_prediction(history, {
            "prediction_id": pid, "report_date": "2026-08-31",
            "as_of_phase": "close", "prediction_version": "v2",
            "generated_at": generated_at, "supersedes_prediction_id": parent,
        })
    review = build_prediction_review(history)
    assert review["prediction_count"] == 1
    # 06:15 UTC = 14:15 北京, 早于 14:56 北京 —— 跨时区必须按绝对时间比。
    assert list(review["predictions"]) == ["2026-08-31:close:v2:localbranch1"]


def test_survivor_keeps_its_own_outcome_when_a_superseded_revision_has_a_different_one(tmp_path):
    """折叠继承结局只是兜底: 存活修订自己已有的那条不许被旧修订的盖掉。

    同日重跑事实会变 (阶段层收尾日差一天就能让主升段收益从 +0.86 翻成 -0.16),
    两条修订的 delta 因此可能不同 —— 挂在存活样本上的必须是它自己那套。
    """
    history = tmp_path / "history.jsonl"
    old_pid, new_pid = "2026-08-20:close:v2:old000000000", "2026-08-20:close:v2:new000000000"
    for pid, generated_at, parent in (
        (old_pid, "2026-08-20T16:30:00+08:00", None),
        (new_pid, "2026-08-20T20:30:00+08:00", old_pid),
    ):
        append_prediction(history, {
            "prediction_id": pid, "report_date": "2026-08-20",
            "as_of_phase": "close", "prediction_version": "v2",
            "generated_at": generated_at, "supersedes_prediction_id": parent,
            "primary_scenario_id": "repair",
            "scenario_plans": [{"scenario_id": "repair", "probability": 0.5}],
        })
    append_outcome(history, new_pid, "t1", {"triggered": True, "hit": True, "note": "own"})
    append_outcome(history, old_pid, "t1", {"triggered": True, "hit": False, "note": "inherited"})
    append_outcome(history, old_pid, "t3", {"triggered": True, "hit": False, "note": "inherited"})

    review = build_prediction_review(history)
    outcomes = review["predictions"][new_pid]["outcomes"]
    assert outcomes["t1"]["note"] == "own"          # 自己那条赢
    assert outcomes["t3"]["note"] == "inherited"    # 自己没有才继承
    assert review["orphan_outcome_count"] == 0


def test_backfill_only_writes_outcomes_for_the_surviving_revision(tmp_path):
    """回填不再逐条修订写一遍 T+1/T+3 —— 留痕 7.6KB/行, 重跑 4 次就是 4 倍垃圾。"""
    history = tmp_path / "history.jsonl"
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    for day, breadth in (("2026-08-20", 0.8), ("2026-08-21", 0.9)):
        (snapshots / f"{day}.json").write_text(json.dumps({
            "report_date": day, "breadth_ratio": breadth, "promotion_rate": 0.5,
            "limit_up": 60, "limit_down": 2, "max_height": 5,
        }, ensure_ascii=False), encoding="utf-8")
    previous = None
    for index in range(3):
        pid = f"2026-08-20:close:v2:fingerprint{index}"
        append_prediction(history, {
            "prediction_id": pid, "report_date": "2026-08-20",
            "as_of_phase": "close", "prediction_version": "v2",
            "generated_at": f"2026-08-20T1{index}:00:00+08:00",
            "supersedes_prediction_id": previous,
            "scenario_id": "repair_after_breadth_only",
            "primary_scenario_id": "repair_after_breadth_only",
            "market_snapshot": {"breadth_ratio": 0.8, "promotion_rate": 0.5,
                                "limit_up": 60, "limit_down": 2, "max_height": 5},
        })
        previous = pid
    result = reconcile_prediction_outcomes(
        history, snapshots, trading_days=["2026-08-20", "2026-08-21"])

    assert result["superseded_skipped"] == 2
    written = [
        json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event_type") == "outcome"
    ]
    assert {row["prediction_id"] for row in written} == {"2026-08-20:close:v2:fingerprint2"}
