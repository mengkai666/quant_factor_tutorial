# -*- coding: utf-8 -*-
import json

import pytest

from market_snapshot import (
    SNAPSHOT_SCHEMA,
    append_phase_snapshot_once,
    build_phase_snapshot,
    latest_phase_snapshots,
    load_phase_snapshots,
    normalize_phase,
)
from scenario_posterior import build_scenario_posterior_timeline
from prediction_review import append_prediction


def test_phase_snapshot_normalizes_time_alias_and_preserves_missing_metrics():
    snapshot = build_phase_snapshot(
        report_date="2026-08-18",
        phase="09:35",
        metrics={"breadth_ratio": 0.52},
        source_lineage={"source": "intraday_feed"},
    )

    assert snapshot.phase == "early_0935"
    assert snapshot.metrics == {"breadth_ratio": 0.52}
    assert "limit_up" not in snapshot.metrics
    assert snapshot.to_dict()["snapshot_schema"] == SNAPSHOT_SCHEMA


def test_phase_snapshot_rejects_unknown_phase_and_invalid_business_date():
    with pytest.raises(ValueError):
        normalize_phase("盘中随便看看")
    with pytest.raises(ValueError):
        build_phase_snapshot(report_date="2026/08/18", phase="close", metrics={})


def test_phase_snapshot_is_append_only_and_idempotent(tmp_path):
    path = tmp_path / "phase_snapshots.jsonl"
    close = build_phase_snapshot(
        report_date="2026-08-18", phase="close",
        metrics={"breadth_ratio": 0.70}, captured_at="2026-08-18T15:05:00+08:00",
    )
    auction = build_phase_snapshot(
        report_date="2026-08-18", phase="auction",
        metrics={"breadth_ratio": 0.62}, captured_at="2026-08-19T09:25:00+08:00",
    )

    first = append_phase_snapshot_once(path, close)
    duplicate = append_phase_snapshot_once(path, close)
    append_phase_snapshot_once(path, auction)

    assert first["appended"] is True
    assert duplicate["appended"] is False
    assert len(load_phase_snapshots(path, report_date="2026-08-18")) == 2
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_latest_phase_snapshots_does_not_synthesize_missing_intraday_phases():
    rows = [
        build_phase_snapshot(
            report_date="2026-08-18", phase="close", metrics={"breadth_ratio": 0.7},
            captured_at="2026-08-18T15:00:00+08:00",
        ).to_dict(),
        build_phase_snapshot(
            report_date="2026-08-18", phase="auction", metrics={"breadth_ratio": 0.6},
            captured_at="2026-08-19T09:25:00+08:00",
        ).to_dict(),
    ]

    latest = latest_phase_snapshots(rows)
    assert list(latest) == ["close", "auction"]
    assert "early_0935" not in latest
    assert "confirm_1000" not in latest


def test_posterior_updates_only_after_observed_phase_and_keeps_probability_auditable():
    plans = [
        {"scenario_id": "mainline_continuation", "probability": 0.6},
        {"scenario_id": "high_level_retreat", "probability": 0.4},
    ]
    snapshots = [
        build_phase_snapshot(
            report_date="2026-08-18", phase="close",
            metrics={"breadth_ratio": 0.7, "promotion_rate": 0.65, "limit_down": 2},
            captured_at="2026-08-18T15:00:00+08:00",
        ).to_dict(),
        build_phase_snapshot(
            report_date="2026-08-19", phase="auction",
            metrics={"breadth_ratio": 0.72, "promotion_rate": 0.68, "limit_down": 1},
            captured_at="2026-08-19T09:25:00+08:00",
        ).to_dict(),
    ]

    result = build_scenario_posterior_timeline(plans, snapshots)

    assert result["prior_available"] is True
    assert result["phases_observed"] == ["close", "auction"]
    auction = next(row for row in result["timeline"] if row["phase"] == "auction")
    continuation = next(row for row in auction["scenarios"] if row["scenario_id"] == "mainline_continuation")
    retreat = next(row for row in auction["scenarios"] if row["scenario_id"] == "high_level_retreat")
    assert continuation["posterior_probability"] > continuation["prior_probability"]
    assert retreat["posterior_probability"] < retreat["prior_probability"]
    assert continuation["supporting_evidence_ids"]
    assert retreat["invalidating_evidence_ids"]


def test_posterior_does_not_publish_percentages_without_complete_priors():
    result = build_scenario_posterior_timeline(
        [{"scenario_id": "breadth_repair"}],
        [build_phase_snapshot(
            report_date="2026-08-18", phase="close", metrics={"breadth_ratio": 0.4},
        ).to_dict()],
    )

    assert result["prior_available"] is False
    row = result["timeline"][0]["scenarios"][0]
    assert row["posterior_probability"] is None


def test_invalid_or_timezone_less_capture_time_is_not_strictly_valid():
    from datetime import datetime, timezone
    with pytest.raises(ValueError):
        build_phase_snapshot(
            report_date="2026-08-19", phase="auction", metrics={},
            captured_at="2026-08-19 09:25:00",
        )
    with pytest.raises(ValueError):
        build_phase_snapshot(
            report_date="2026-08-19", phase="auction", metrics={},
            captured_at="not-a-time",
        )
    with pytest.raises(ValueError):
        build_phase_snapshot(
            report_date="2026-08-19", phase="auction", metrics={},
            captured_at=(datetime.now(timezone.utc).replace(year=2030)).isoformat(),
        )


def test_latest_phase_snapshots_orders_timezone_aware_capture_times():
    early = build_phase_snapshot(
        report_date="2026-08-19", phase="auction", metrics={"x": 1},
        captured_at="2026-08-19T01:25:00+00:00",
    ).to_dict()
    late = build_phase_snapshot(
        report_date="2026-08-19", phase="auction", metrics={"x": 2},
        captured_at="2026-08-19T09:26:00+08:00",
    ).to_dict()
    latest = latest_phase_snapshots([late, early])
    assert latest["auction"]["metrics"]["x"] == 2


def test_posterior_consumes_plan_trigger_rules_instead_of_scenario_id_hardcoding():
    plans = [
        {
            "scenario_id": "custom_strong",
            "probability": 0.5,
            "trigger_rules": {
                "auction": [{"rule_id": "strong", "metric": "breadth_ratio", "operator": "gte", "value": 0.8, "weight": 1}],
                "early_0935": [], "confirm_1000": [], "afternoon": [],
            },
            "invalidation_rules": [],
        },
        {
            "scenario_id": "custom_weak",
            "probability": 0.5,
            "trigger_rules": {
                "auction": [{"rule_id": "weak", "metric": "breadth_ratio", "operator": "lte", "value": 0.8, "weight": 1}],
                "early_0935": [], "confirm_1000": [], "afternoon": [],
            },
            "invalidation_rules": [],
        },
    ]
    snapshots = [
        build_phase_snapshot(
            report_date="2026-08-19", phase="close",
            metrics={"breadth_ratio": 0.65}, captured_at="2026-08-19T15:00:00+08:00",
        ).to_dict(),
        build_phase_snapshot(
            report_date="2026-08-19", phase="auction",
            metrics={"breadth_ratio": 0.70}, captured_at="2026-08-20T09:25:00+08:00",
        ).to_dict(),
    ]
    result = build_scenario_posterior_timeline(plans, snapshots)
    auction = next(row for row in result["timeline"] if row["phase"] == "auction")
    strong = next(row for row in auction["scenarios"] if row["scenario_id"] == "custom_strong")
    weak = next(row for row in auction["scenarios"] if row["scenario_id"] == "custom_weak")
    assert "strong" in strong["invalidating_evidence_ids"]
    assert "weak" in weak["supporting_evidence_ids"]
    assert weak["posterior_probability"] > strong["posterior_probability"]


def test_phase_snapshot_separates_prediction_report_date_from_observed_trade_date():
    snapshot = build_phase_snapshot(
        report_date="2026-08-19", trade_date="2026-08-20", phase="auction",
        metrics={"breadth_ratio": 0.6}, captured_at="2026-08-20T09:25:00+08:00",
    )
    assert snapshot.report_date == "2026-08-19"
    assert snapshot.trade_date == "2026-08-20"
    assert snapshot.to_dict()["trade_date"] == "2026-08-20"


def test_phase_monitor_records_observation_and_updates_prediction_posterior(tmp_path):
    from phase_monitor import record_phase_observation
    history = tmp_path / "history.jsonl"
    phase_path = tmp_path / "phases.jsonl"
    append_prediction(history, {
        "prediction_id": "p1", "report_date": "2026-08-19", "as_of_phase": "close",
        "scenario_plans": [
            {"scenario_id": "strong", "prior_probability": 0.5, "trigger_rules": {
                "auction": [{"rule_id": "strong", "metric": "breadth_ratio", "operator": "gte", "value": 0.6}],
                "early_0935": [], "confirm_1000": [], "afternoon": [],
            }, "invalidation_rules": []},
            {"scenario_id": "weak", "prior_probability": 0.5, "trigger_rules": {
                "auction": [{"rule_id": "weak", "metric": "breadth_ratio", "operator": "lt", "value": 0.6}],
                "early_0935": [], "confirm_1000": [], "afternoon": [],
            }, "invalidation_rules": []},
        ],
    })
    result = record_phase_observation(
        history_path=history, phase_snapshot_path=phase_path,
        report_date="2026-08-19", trade_date="2026-08-20", phase="auction",
        metrics={"breadth_ratio": 0.7}, captured_at="2026-08-20T09:25:00+08:00",
    )
    assert result["snapshot"]["appended"] is True
    assert result["posterior"]["phases_observed"] == ["auction"]
    assert result["posterior"]["timeline"][0]["top_scenario_id"] == "strong"
