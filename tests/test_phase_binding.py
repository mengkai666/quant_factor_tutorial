import copy
import json

import pytest

from market_snapshot import build_phase_snapshot, latest_phase_snapshots
from scenario_posterior import build_scenario_posterior_timeline

REPORT = "2026-09-03"
TARGET = "2026-09-04"


def snapshot(phase, *, report=REPORT, trade=None, metrics=None, **kw):
    trade = trade or (report if phase == "close" else TARGET)
    clock = {"close": "15:00:00", "auction": "09:25:00", "early_0935": "09:35:00", "confirm_1000": "10:00:00"}[phase]
    return build_phase_snapshot(
        report_date=report, trade_date=trade, phase=phase,
        captured_at=f"{trade}T{clock}+08:00", metrics=metrics or {"breadth_ratio": .7},
        source_lineage=kw.pop("source_lineage", {"source": "fixture_feed"}),
        quality=kw.pop("quality", {"status": "ok"}), **kw,
    ).to_dict()


def plans(baseline=False):
    rule = {"rule_id": "breadth", "metric": "breadth_ratio", "operator": "gte", "value": .6}
    if baseline:
        rule.update(operator="gte_baseline", baseline_metric="breadth_ratio")
        rule.pop("value")
    return [{"scenario_id": "repair", "trigger_rules": {"auction": [rule], "early_0935": [rule]},
             "invalidation_rules": []}]


def test_explicit_trade_date_must_match_observed_local_day():
    with pytest.raises(ValueError, match="trade_date"):
        build_phase_snapshot(report_date=REPORT, trade_date=TARGET, phase="auction",
            captured_at="2026-09-03T09:25:00+08:00", metrics={})


@pytest.mark.parametrize("clock,phase", [("09:31:00", "early_0935"), ("09:46:00", "confirm_1000"), ("14:59:00", "close")])
def test_phase_name_cannot_claim_an_observation_before_that_phase(clock, phase):
    with pytest.raises(ValueError):
        build_phase_snapshot(report_date=REPORT, phase=phase, captured_at=f"{REPORT}T{clock}+08:00")


def test_preassigned_id_cannot_bypass_timestamp_validation():
    tampered = snapshot("auction")
    tampered["captured_at"] = TARGET + "T15:00:00+08:00"
    tampered["quality"].update(timestamp_status="valid", phase_window_valid=True)
    assert latest_phase_snapshots([tampered]) == {}


def test_quality_is_part_of_snapshot_identity_and_cannot_forge_time_validation():
    good = snapshot("auction")
    bad = snapshot("auction", quality={"status": "degraded"})
    assert good["snapshot_id"] != bad["snapshot_id"]
    missing = build_phase_snapshot(report_date=REPORT, phase="auction",
        quality={"timestamp_status": "valid", "phase_window_valid": True}).to_dict()
    assert missing["quality"]["timestamp_status"] == "missing"
    assert missing["quality"]["phase_window_valid"] is None


def test_bound_timeline_keeps_baseline_and_isolates_wrong_reports_and_dates():
    rows = [snapshot("close", metrics={"breadth_ratio": .6}), snapshot("auction"),
            snapshot("auction", trade="2026-09-05", metrics={"breadth_ratio": .1}),
            snapshot("early_0935", report="2026-09-02")]
    result = build_scenario_posterior_timeline(plans(True), rows, report_date=REPORT, trade_date=TARGET)
    assert result["phases_observed"] == ["close", "auction"]
    assert result["timeline"][-1]["decision_scenario_id"] == "repair"
    assert result["timeline"][-1]["confirmation_eligible"] is True
    assert len(result["rejected_snapshots"]) == 2


@pytest.mark.parametrize("kwargs", [
    {"quality": {"status": "unknown"}}, {"quality": {"status": "ok", "used_stale": True}},
    {"source_lineage": {}},
])
def test_unqualified_observation_cannot_activate_bound_plan(kwargs):
    result = build_scenario_posterior_timeline(plans(), [snapshot("close"), snapshot("auction", **kwargs)],
                                             report_date=REPORT, trade_date=TARGET)
    assert result["phases_observed"] == ["close"]
    assert result["rejected_snapshots"]
    assert result["timeline"][-1]["decision_scenario_id"] is None


def test_missing_target_date_does_not_borrow_a_later_observation():
    result = build_scenario_posterior_timeline(plans(), [snapshot("close"), snapshot("auction")],
                                             report_date=REPORT)
    assert result["phases_observed"] == ["close"]
    assert result["binding_status"] == "target_date_missing"


def _prediction_file(tmp_path):
    history = tmp_path / "history.jsonl"
    history.write_text(json.dumps({"event_type": "prediction", "prediction_id": "p1",
        "report_date": REPORT, "target_trade_date": TARGET, "scenario_plans": plans(True)}) + "\n", encoding="utf-8")
    return history


def test_monitor_preserves_report_day_close_for_target_day_baseline_rules(tmp_path):
    from phase_monitor import record_phase_observation
    from market_snapshot import append_phase_snapshot_once
    history = _prediction_file(tmp_path)
    archive = tmp_path / "phases.jsonl"
    append_phase_snapshot_once(archive, snapshot("close", metrics={"breadth_ratio": .6}))
    result = record_phase_observation(history_path=history, phase_snapshot_path=archive,
        report_date=REPORT, trade_date=TARGET, phase="auction", metrics={"breadth_ratio": .7},
        captured_at=TARGET + "T09:25:00+08:00", source_lineage={"source": "fixture_feed"}, quality={"status": "ok"})
    assert result["posterior"]["phases_observed"] == ["close", "auction"]
    phase = result["posterior"]["timeline"][-1]
    # Preserve baseline analysis without treating an unregistered legacy plan
    # as independent strategy authorization.
    assert next(row for row in phase["scenarios"] if row["scenario_id"] == "repair")["state"] == "supported"
    assert phase["active_scenario_id"] is None
    assert phase["decision_scenario_id"] is None


def test_monitor_rejects_wrong_target_date_before_writing(tmp_path):
    from phase_monitor import record_phase_observation
    history = _prediction_file(tmp_path)
    archive = tmp_path / "phases.jsonl"
    with pytest.raises(ValueError, match="目标交易日|target"):
        record_phase_observation(history_path=history, phase_snapshot_path=archive,
            report_date=REPORT, trade_date="2026-09-05", phase="auction", metrics={"breadth_ratio": .7},
            captured_at="2026-09-05T09:25:00+08:00", source_lineage={"source": "fixture_feed"}, quality={"status": "ok"})
    assert not archive.exists()


def test_monitor_does_not_invent_quality_or_a_market_data_source(tmp_path):
    from phase_monitor import record_phase_observation
    with pytest.raises(ValueError, match="source|quality|来源|质量"):
        record_phase_observation(history_path=_prediction_file(tmp_path), phase_snapshot_path=tmp_path / "phases.jsonl",
            report_date=REPORT, trade_date=TARGET, phase="auction", metrics={"breadth_ratio": .7},
            captured_at=TARGET + "T09:25:00+08:00")


def test_hard_invalidated_plan_cannot_reappear_at_next_phase_without_new_plan():
    rows = plans()
    rows[0]["invalidation_rules"] = [{"rule_id": "risk", "metric": "limit_down", "operator": "gt", "value": 10}]
    result = build_scenario_posterior_timeline(rows, [
        snapshot("auction", metrics={"breadth_ratio": .7, "limit_down": 12}),
        snapshot("early_0935", metrics={"breadth_ratio": .8, "limit_down": 1}),
    ], report_date=REPORT, trade_date=TARGET)
    assert result["timeline"][-1]["scenario_status"] == "no_valid_scenario"
    assert result["timeline"][-1]["scenarios"][0]["invalidation_history_ids"]


def readiness_inputs():
    row = snapshot("early_0935")
    phase = {**row, "snapshot_quality": row["quality"], "confirmation_eligible": True,
        "active_scenario_id": "repair", "decision_scenario_id": "repair", "top_ranked_scenario_id": "repair",
        "scenario_status": "active", "scenarios": [{"scenario_id": "repair", "state": "supported"}]}
    return {"quality": {"status": "ok", "publication_mode": "decision"},
        "market_state": {"publication_mode": "decision", "statistics_layer": {"status": "ok"}},
        "action_plan": {"publication_mode": "decision", "position": "2 成", "execution_allowed": True,
            "groups": [{"code": "attack", "rows": [{"code": "sz000001", "role": "attack", "execution_allowed": True}]}]},
        "scenario_posterior": {"report_date": REPORT, "target_trade_date": TARGET, "binding_status": "bound", "timeline": [phase]},
        "report_date": REPORT}


def test_ready_signal_requires_bound_qualified_observation():
    from decision_readiness import build_decision_readiness
    assert build_decision_readiness(**readiness_inputs())["execution_ready"]


@pytest.mark.parametrize("change", [
    {"report_date": "2026-09-02"}, {"trade_date": "2026-09-03"},
    {"captured_at": None}, {"confirmation_eligible": False}, {"source_lineage": {}},
])
def test_invalid_phase_metadata_cannot_release_readiness(change):
    from decision_readiness import build_decision_readiness
    args = readiness_inputs()
    args["scenario_posterior"]["timeline"][0].update(change)
    result = build_decision_readiness(**args)
    assert not result["execution_ready"]
    assert result["signal"]["status"] == "not_evaluable"
    assert result["phase_confirmation"]["validation_issues"]


def test_legacy_signal_without_provenance_is_display_only_not_a_confirmation():
    from decision_readiness import build_decision_readiness
    args = readiness_inputs()
    args["scenario_posterior"] = {"timeline": [{"phase": "early_0935", "top_scenario_id": "repair",
        "scenarios": [{"scenario_id": "repair", "state": "supported"}]}]}
    result = build_decision_readiness(**args)
    assert not result["execution_ready"]
    assert result["signal"]["status"] == "not_evaluable"


def test_cached_next_day_never_guesses_a_weekday_or_calls_network(tmp_path):
    from data_sources.calendar_provider import CalendarProvider
    cache = tmp_path / "calendar.csv"
    cache.write_text("trade_date\n2026-09-04\n2026-09-07\n", encoding="utf-8")
    provider = CalendarProvider(cache_path=cache, source=lambda: pytest.fail("network not allowed"))
    assert provider.cached_next_trading_day("2026-09-04") == "2026-09-07"
    assert provider.cached_next_trading_day("2026-09-07") is None
    assert provider.cached_next_trading_day("2026-09-05") is None


def test_cli_requires_provenance_and_imports_real_json_only(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    history = _prediction_file(tmp_path)
    archive = tmp_path / "phases.jsonl"
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "record_market_phase.py"),
        "--report-date", REPORT, "--trade-date", TARGET, "--phase", "auction",
        "--captured-at", TARGET + "T09:25:00+08:00", "--metrics-json", '{"breadth_ratio":0.7}',
        "--history", str(history), "--phase-history", str(archive)]
    rejected = subprocess.run(command, text=True, capture_output=True, encoding="utf-8")
    assert rejected.returncode != 0
    assert not archive.exists()
    imported = subprocess.run(command + ["--source", "fixture_feed", "--quality-status", "ok"],
                              text=True, capture_output=True, encoding="utf-8")
    assert imported.returncode == 0, imported.stderr
    result = json.loads(imported.stdout)
    assert result["snapshot"]["source_lineage"]["source"] == "fixture_feed"
    assert result["snapshot"]["trade_date"] == TARGET


def test_latest_bad_quality_does_not_fall_back_to_older_confirmed_observation():
    good = snapshot("auction")
    bad = snapshot("auction", quality={"status": "degraded"})
    bad["captured_at"] = TARGET + "T09:26:00+08:00"
    result = build_scenario_posterior_timeline(plans(), [snapshot("close"), good, bad], report_date=REPORT, trade_date=TARGET)
    assert result["phases_observed"] == ["close"]
    assert result["confirmation_blocked"] is True


def test_corrected_quality_can_be_rechecked_without_permanently_poisoning_a_phase():
    bad = snapshot("auction", quality={"status": "degraded"})
    good = snapshot("auction")
    good["captured_at"] = TARGET + "T09:26:00+08:00"
    result = build_scenario_posterior_timeline(plans(), [bad, good], report_date=REPORT, trade_date=TARGET)
    assert result["timeline"][-1]["decision_scenario_id"] == "repair"
    assert result["confirmation_blocked"] is False


def test_hard_invalidation_latches_across_two_snapshots_in_the_same_phase():
    plan = plans()
    plan[0]["invalidation_rules"] = [{"rule_id": "risk", "metric": "limit_down", "operator": "gt", "value": 10}]
    first = snapshot("auction", metrics={"breadth_ratio": .8, "limit_down": 12})
    later = snapshot("auction", metrics={"breadth_ratio": .8, "limit_down": 1})
    later["captured_at"] = TARGET + "T09:26:00+08:00"
    result = build_scenario_posterior_timeline(plan, [first, later], report_date=REPORT, trade_date=TARGET)
    assert result["timeline"][-1]["scenario_status"] == "no_valid_scenario"


def test_later_unqualified_phase_suspends_earlier_confirmation():
    from decision_readiness import build_decision_readiness
    args = readiness_inputs()
    args["scenario_posterior"]["confirmation_blocked"] = True
    args["scenario_posterior"]["rejected_snapshots"] = [{"phase": "confirm_1000", "issues": ["quality_not_ok"]}]
    result = build_decision_readiness(**args)
    assert not result["execution_ready"]
    assert result["signal"]["status"] == "not_evaluable"


@pytest.mark.parametrize("value", [True, 1.5, -0.1, float("inf")])
def test_invalid_breadth_values_cannot_be_confirmed_as_ratios(value):
    result = build_scenario_posterior_timeline(plans(), [snapshot("auction", metrics={"breadth_ratio": value})], report_date=REPORT, trade_date=TARGET)
    assert result["timeline"][-1]["scenarios"][0]["state"] == "unknown"
    assert result["timeline"][-1]["decision_scenario_id"] is None


def test_explicit_missing_occurrence_prior_is_not_replaced_by_conditional_hit_rate():
    plan = plans()
    plan[0].update(prior_probability=None, probability=.9)
    result = build_scenario_posterior_timeline(plan, [snapshot("auction")], report_date=REPORT, trade_date=TARGET)
    assert not result["prior_available"]
    assert result["timeline"][-1]["scenarios"][0]["posterior_probability"] is None


def test_phase_api_cannot_import_close_and_claim_an_intraday_update(tmp_path):
    from phase_monitor import record_phase_observation
    history = _prediction_file(tmp_path)
    with pytest.raises(ValueError):
        record_phase_observation(history_path=history, phase_snapshot_path=tmp_path / "phases.jsonl",
            report_date=REPORT, trade_date=TARGET, phase="close", metrics={"breadth_ratio": .8},
            captured_at=TARGET + "T15:00:00+08:00", source_lineage={"source": "fixture"}, quality={"status": "ok"})


def test_rebuilt_prediction_never_uses_other_revisions_intraday_confirmation():
    old = snapshot("auction")
    old["source_lineage"]["prediction_id"] = "old-plan"
    result = build_scenario_posterior_timeline(plans(), [snapshot("close"), old],
        report_date=REPORT, trade_date=TARGET, prediction_id="new-plan")
    assert result["phases_observed"] == ["close"]
    assert result["rejected_snapshots"][0]["issues"] == ["prediction_id_mismatch"]


def test_same_prediction_snapshot_remains_eligible():
    row = snapshot("auction")
    row["source_lineage"]["prediction_id"] = "current-plan"
    result = build_scenario_posterior_timeline(plans(), [row], report_date=REPORT, trade_date=TARGET, prediction_id="current-plan")
    assert result["timeline"][-1]["decision_scenario_id"] == "repair"


def test_equal_or_missing_capture_times_use_revision_order_not_random_hash():
    first = build_phase_snapshot(report_date=REPORT, phase="close", metrics={"breadth_ratio": .1}, snapshot_id="z-older").to_dict()
    revised = build_phase_snapshot(report_date=REPORT, phase="close", metrics={"breadth_ratio": .8}, snapshot_id="a-newer").to_dict()
    assert latest_phase_snapshots([first, revised])["close"]["metrics"]["breadth_ratio"] == .8


def test_latest_bad_phase_cannot_erase_older_qualified_hard_invalidation():
    plan = plans()
    plan[0]["invalidation_rules"] = [{"rule_id": "risk", "metric": "limit_down", "operator": "gt", "value": 10}]
    first = snapshot("auction", metrics={"breadth_ratio": .8, "limit_down": 12})
    bad = snapshot("auction", metrics={"breadth_ratio": .8, "limit_down": 1}, quality={"status": "degraded"})
    bad["captured_at"] = TARGET + "T09:26:00+08:00"
    later = snapshot("early_0935", metrics={"breadth_ratio": .8, "limit_down": 1})
    result = build_scenario_posterior_timeline(plan, [first, bad, later], report_date=REPORT, trade_date=TARGET)
    assert result["timeline"][-1]["scenario_status"] == "no_valid_scenario"
    assert "risk" in result["timeline"][-1]["scenarios"][0]["invalidation_history_ids"]


def test_unqualified_phase_explanation_precedes_a_derived_zero_position():
    from decision_readiness import build_decision_readiness
    args = readiness_inputs()
    args["action_plan"].update(position="0 成", execution_allowed=False)
    args["scenario_posterior"]["timeline"][0]["trade_date"] = "2026-09-02"
    result = build_decision_readiness(**args)
    assert result["action"]["reason_code"] == "signal_snapshot_unqualified"
    assert "判断后决定不买" not in result["action"]["reason"]
