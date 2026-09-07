import copy


def test_empty_day_is_journaled_and_review_includes_older_days(tmp_path):
    from decision_dashboard import build_today_decision
    from report_closure import persist_decision_review
    path = tmp_path / "history.jsonl"
    for day in ("2026-09-02", "2026-09-03"):
        result = persist_decision_review(path, build_today_decision({"date_str": day, "publication_mode": "facts_only"}))
    assert result["daily_decision_review"]["decision_count"] == 2
    assert result["daily_decision"]["status"] == "no_trade_data_unavailable"
    assert result["trade_plan_review"]["plan_count"] == 0
    same = persist_decision_review(path, build_today_decision({"date_str": "2026-09-03", "publication_mode": "facts_only"}))
    assert not same["daily_decision"]["appended"]
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_intraday_changes_revise_existing_plan_without_inventing_results(tmp_path):
    from decision_dashboard import build_today_decision
    from report_closure import persist_decision_review
    from test_recap_scenario_closure import _confirmed_context
    ctx = _confirmed_context()
    decision = build_today_decision(ctx)
    path = tmp_path / "history.jsonl"
    confirmed = persist_decision_review(path, decision)
    original_plan = confirmed["trade_plan_review"]["items"][0]
    assert original_plan["plan"]["plan_status"] == "confirmed"
    blocked = copy.deepcopy(decision)
    blocked["readiness"]["signal"]["status"] = "invalidated"
    blocked["readiness"]["action"].update(status="no_new_positions", reason_code="no_valid_scenario", reason="全部情景失效")
    blocked["readiness"].update(plan_permitted=False, execution_ready=False)
    blocked["action_plan"].update(execution_allowed=False, scenario_status="no_valid_scenario", decision_scenario_id=None)
    blocked["action_plan"]["priority"].update(primary=None, alternates=[])
    after = persist_decision_review(path, blocked)
    assert after["trade_plan_review"]["plan_count"] == 1
    item = after["trade_plan_review"]["items"][0]
    assert item["logical_plan_id"] == original_plan["logical_plan_id"]
    assert item["plan"]["plan_status"] == "invalidated"
    assert len(item["revisions"]) == 2
    assert item["outcome"] is None
    assert after["decision_changes"]["has_previous"]
    assert after["decision_changes"]["changes"]
    assert after["daily_decision"]["execution_allowed"] is False


def test_replay_context_retains_gates_and_real_price_references_only():
    import pandas as pd
    from decision_dashboard import build_today_decision
    from report_closure import build_decision_replay_context
    from test_recap_scenario_closure import _confirmed_context
    ctx = _confirmed_context()
    ctx["price_df"] = pd.DataFrame([{"code": "sz000006", "date": "2026-09-03", "close_raw": 12.34}])
    result = build_decision_replay_context(ctx, build_today_decision(ctx))
    assert result["data_quality"] == ctx["data_quality"]
    assert result["publication_mode"] == ctx["publication_mode"]
    assert result["reference_prices"] == [{"code": "sz000006", "date": "2026-09-03", "close_raw": 12.34}]
    assert "price_df" not in result
    assert "report_context" not in result


def test_phase_import_reuses_original_gates_and_revises_the_same_plan(tmp_path):
    import json
    from decision_dashboard import build_today_decision
    from report_closure import build_decision_replay_context
    from phase_monitor import record_phase_observation
    from trade_plan_review import build_trade_plan_review
    from test_strategy_qualification_integration import context
    from test_strategy_qualification import record, event_metrics
    from strategy_qualification import qualify_strategies
    ctx = context()
    ctx["scenario_plans"][0]["invalidation_rules"] = [{"rule_id": "weak", "metric": "breadth_ratio", "operator": "lt", "value": .5}]
    # This is a new, explicitly qualified fixture revision. A legacy history
    # entry with only descriptive counts is no longer allowed to open a plan.
    evidence = record(ctx["scenario_plans"][0])
    qualified = qualify_strategies(ctx["scenario_plans"], quality=ctx["data_quality"],
        validation_records=[evidence], event_metrics=event_metrics(),
        report_date="2026-09-03", target_trade_date="2026-09-04")
    ctx["data_quality"]["strategy_qualification"] = qualified
    ctx["data_quality"]["publication_mode"] = qualified["publication_mode"]
    ctx["publication_mode"] = qualified["publication_mode"]
    validation_file = tmp_path / "validation.json"
    validation_file.write_text(json.dumps({"schema_version": "strategy-validation-set/v1",
        "records": [evidence]}), encoding="utf-8")
    history = tmp_path / "history.jsonl"
    history.write_text(json.dumps({"event_type": "prediction", "prediction_id": "p1", "report_date": "2026-09-03",
        "target_trade_date": "2026-09-04", "scenario_plans": ctx["scenario_plans"],
        "decision_context": build_decision_replay_context(ctx, build_today_decision(ctx))}) + "\n", encoding="utf-8")
    args = dict(history_path=history, phase_snapshot_path=tmp_path / "phases.jsonl",
        validation_path=validation_file,
        report_date="2026-09-03", trade_date="2026-09-04", phase="early_0935",
        source_lineage={"source": "fixture_feed"}, quality={"status": "ok"})
    first = record_phase_observation(**args, metrics={"breadth_ratio": .8, "promotion_rate": .7, "limit_down": 2}, captured_at="2026-09-04T09:35:00+08:00")
    assert first["daily_decision"]["status"] == "conditional_plan"
    last = record_phase_observation(**args, metrics={"breadth_ratio": .2, "promotion_rate": .7, "limit_down": 2}, captured_at="2026-09-04T09:36:00+08:00")
    assert not last["decision"]["readiness"]["execution_ready"]
    review = build_trade_plan_review(history)
    assert review["plan_count"] == 1
    assert review["items"][0]["plan"]["plan_status"] == "invalidated"
    assert review["items"][0]["outcome"] is None
