"""Semantic regressions for the recap -> scenario -> execution chain."""
import copy
import json

import pandas as pd


def _groups():
    return [{"height": "2连板", "stock_details": [
        *[{"name": f"A{i}其他题材", "code": f"sz00000{i}", "ml": "机器人"} for i in range(1, 6)],
        {"name": "Z主线", "code": "sz000006", "ml": "AI算力"},
    ]}]


def test_mainline_candidate_survives_display_truncation():
    from decision_dashboard import build_today_decision
    decision = build_today_decision({
        "date_str": "2026-09-04", "publication_mode": "observation",
        "mainline_review": {"top1": "AI算力"}, "echelon": _groups(),
    })
    assert "Z主线" in decision["watch_items"][1]["detail"]


def test_funnel_preserves_observations_with_explicit_rejection_reasons():
    from candidate_funnel import build_candidate_funnel
    groups = _groups()
    groups[0]["stock_details"] += [
        {"name": "*ST测试", "code": "sz000007", "ml": "AI算力"},
        {"name": "停牌", "code": "sz000008", "ml": "AI算力", "suspended": True},
    ]
    original = copy.deepcopy(groups)
    funnel = build_candidate_funnel(echelon=groups, mainline="AI算力", report_date="2026-09-04")
    assert groups == original
    assert [r["code"] for r in funnel["eligible_candidates"]] == ["sz000006"]
    assert funnel["rejected_counts"]["off_mainline"] == 5
    assert funnel["rejected_counts"]["not_tradeable"] == 2
    assert funnel["input_count"] == 8
    assert len(funnel["observations"]) == 6
    assert funnel["fingerprint"]


def test_funnel_matches_sector_tokens_not_substrings_and_risk_overrides_attack():
    from candidate_funnel import build_candidate_funnel
    groups = [{"height": "2板", "stock_details": [
        {"code": "sz000001", "name": "一", "ml": "AI算力设备"},
        {"code": "sz000002", "name": "二", "ml": "AI算力"},
    ]}]
    f = build_candidate_funnel(echelon=groups, mainline="AI算力", progression_chain={"rows": [
        {"code": "sz000002", "name": "二", "status": "broken_negative", "previous_height": 2},
    ]})
    assert f["eligible_candidates"] == []
    assert f["rejected_counts"]["off_mainline"] == 1
    assert f["risk_anchors"][0]["code"] == "sz000002"


def test_scenario_and_dashboard_use_the_same_funnel_even_in_observation_mode():
    from candidate_funnel import build_candidate_funnel
    from scenario_plan import build_scenario_plans
    from decision_dashboard import build_dashboard_ctx, build_today_decision
    f = build_candidate_funnel(echelon=_groups(), mainline="AI算力", report_date="2026-09-04")
    plans = build_scenario_plans(report_date="2026-09-04", candidate_funnel=f,
        market_thesis={"breadth_relay_state": {"breadth": "weak", "relay": "weak"}})
    assert all(p.observation_pool for p in plans)
    assert all(not p.trade_candidates for p in plans)
    ctx = build_dashboard_ctx(echelon=[], report_context={
        "report_date": "2026-09-04", "publication_mode": "observation",
        "quality": {"status": "degraded"},
        "facts": {"candidate_funnel": f, "mainline_review": {"top1": "AI算力"}},
        "scenario_plans": [p.to_dict() for p in plans],
    })
    d = build_today_decision(ctx)
    assert d["candidate_funnel"]["fingerprint"] == f["fingerprint"]
    assert {r["code"] for r in d["candidates"]} == {r["code"] for r in f["observations"]}
    assert not d["execution_allowed"]


def test_csv_preserves_enriched_price_reference_and_priority(tmp_path):
    import csv
    from decision_dashboard import write_today_focus_pool
    ctx = {
        "date_str": "2026-09-04", "publication_mode": "decision",
        "data_quality": {"status": "ok", "publication_mode": "decision"},
        "market_state": {"publication_mode": "decision"},
        "mainline_review": {"top1": "AI算力"},
        "price_df": pd.DataFrame([{"code": "sz000006", "date": "2026-09-04", "close_raw": 12.34}]),
    }
    plan = {"position": "2 成", "execution_allowed": True, "publication_mode": "decision", "groups": [
        {"code": "attack", "rows": [{"code": "sz000006", "name": "Z主线", "sector": "AI算力", "role": "attack", "execution_allowed": True}]}
    ]}
    output = tmp_path / "focus.csv"
    write_today_focus_pool(ctx, output, action_plan=plan)
    row = next(csv.DictReader(output.open(encoding="utf-8-sig")))
    assert row["优先级"] == "首选"
    assert row["报告日收盘参考"] == "12.34"
    assert row["可执行"] == "否"


def _snapshot(phase, metrics, day="2026-09-04"):
    from market_snapshot import build_phase_snapshot
    time = {"close": "15:00:00", "auction": "09:25:00", "early_0935": "09:35:00", "confirm_1000": "10:00:00"}[phase]
    actual_day = "2026-09-03" if phase == "close" else day
    return build_phase_snapshot(report_date="2026-09-03", trade_date=actual_day,
        phase=phase, metrics=metrics, captured_at=actual_day+"T"+time+"+08:00",
        quality={"status": "ok"}, source_lineage={"source": "test"}).to_dict()


def _rule(rule_id, field, value, required=True):
    return {"rule_id": rule_id, "metric": field, "operator": "gte", "value": value, "required": required}


def test_hard_invalidation_cannot_be_outvoted_and_rank_is_not_active():
    from scenario_posterior import build_scenario_posterior_timeline
    plans = [
        {"scenario_id": "ranked_first", "prior_probability": .99,
         "trigger_rules": {"auction": [_rule("ok1", "breadth_ratio", .5), _rule("ok2", "promotion_rate", .5), _rule("ok3", "mainline_diffusion", .5)]},
         "invalidation_rules": [_rule("risk", "limit_down", 10)]},
        {"scenario_id": "valid_second", "prior_probability": .01,
         "trigger_rules": {"auction": [_rule("ok", "breadth_ratio", .5)]}, "invalidation_rules": []},
    ]
    got = build_scenario_posterior_timeline(plans, [_snapshot("auction", {
        "breadth_ratio": .8, "promotion_rate": .8, "mainline_diffusion": .8, "limit_down": 12,
    })])["timeline"][0]
    assert got["scenarios"][0]["state"] == "invalidated"
    assert got["top_ranked_scenario_id"] == "ranked_first"
    assert got["active_scenario_id"] == "valid_second"
    assert got["decision_scenario_id"] == "valid_second"


def test_all_invalidated_scenarios_have_no_active_scenario():
    from scenario_posterior import build_scenario_posterior_timeline
    plans = [{"scenario_id": "only", "prior_probability": 1,
              "trigger_rules": {"auction": []}, "invalidation_rules": [_rule("risk", "limit_down", 10)]}]
    row = build_scenario_posterior_timeline(plans, [_snapshot("auction", {"limit_down": 12})])["timeline"][0]
    assert row["top_ranked_scenario_id"] == "only"
    assert row["active_scenario_id"] is None
    assert row["scenario_status"] == "no_valid_scenario"
    from decision_dashboard import _build_action_plan
    plan = _build_action_plan({"publication_mode": "decision", "echelon": _groups(),
        "scenario_plans": plans, "scenario_posterior": {"timeline": [row]}})
    assert plan["execution_allowed"] is False
    assert plan["active_scenario_id"] is None


def test_required_trigger_missing_prevents_confirmation_despite_positive_score():
    from scenario_posterior import build_scenario_posterior_timeline
    plans = [{"scenario_id": "repair", "trigger_rules": {"auction": [
        _rule("breadth", "breadth_ratio", .5), _rule("relay", "promotion_rate", .5),
    ]}, "invalidation_rules": []}]
    got = build_scenario_posterior_timeline(plans, [_snapshot("auction", {"breadth_ratio": .8})])["timeline"][0]
    assert got["scenarios"][0]["state"] == "unknown"
    assert got["decision_scenario_id"] is None


def test_close_baseline_does_not_invalidate_its_own_next_day_forecast():
    from scenario_plan import build_scenario_plans
    from scenario_posterior import build_scenario_posterior_timeline
    plans = build_scenario_plans(report_date="2026-09-04", market_thesis={
        "breadth_relay_state": {"breadth": "weak", "relay": "weak"}})
    result = build_scenario_posterior_timeline(plans, [_snapshot("close", {"breadth_ratio": .3, "promotion_rate": .1, "limit_down": 20})])
    assert all(row["state"] == "neutral" for row in result["timeline"][0]["scenarios"])
    assert result["timeline"][0]["decision_scenario_id"] is None


def test_required_trigger_false_cannot_be_outvoted():
    from scenario_posterior import build_scenario_posterior_timeline
    plans = [{"scenario_id": "repair", "trigger_rules": {"auction": [
        _rule("breadth", "breadth_ratio", .5), _rule("relay", "promotion_rate", .5),
        _rule("optional", "mainline_diffusion", .5, required=False),
    ]}, "invalidation_rules": []}]
    phase = build_scenario_posterior_timeline(plans, [_snapshot("auction", {
        "breadth_ratio": .8, "promotion_rate": .2, "mainline_diffusion": .8,
    })])["timeline"][0]
    assert phase["scenarios"][0]["state"] == "neutral"
    assert phase["decision_scenario_id"] is None


def test_optional_missing_rule_does_not_prevent_confirmation():
    from scenario_posterior import build_scenario_posterior_timeline
    plans = [{"scenario_id": "repair", "trigger_rules": {"auction": [
        _rule("breadth", "breadth_ratio", .5),
        _rule("optional", "mainline_diffusion", .5, required=False),
    ]}, "invalidation_rules": []}]
    phase = build_scenario_posterior_timeline(plans, [_snapshot("auction", {"breadth_ratio": .8})])["timeline"][0]
    row = phase["scenarios"][0]
    assert row["state"] == "supported"
    assert row["missing_required_fields"] == []
    assert row["missing_fields"] == ["mainline_diffusion"]
    assert phase["decision_scenario_id"] == "repair"


def test_no_valid_scenario_reason_precedes_zero_position():
    from decision_readiness import build_decision_readiness
    got = build_decision_readiness(
        quality={"status": "ok", "publication_mode": "decision"},
        market_state={"publication_mode": "decision", "statistics_layer": {"status": "ok"}},
        action_plan={"publication_mode": "decision", "position": "0 成", "execution_allowed": False,
                     "active_scenario_id": "obsolete", "groups": []},
        scenario_posterior={"target_trade_date": "2026-09-04", "timeline": [{**_snapshot("early_0935", {}), "scenario_status": "no_valid_scenario",
            "active_scenario_id": None, "decision_scenario_id": None, "top_scenario_id": "obsolete",
            "scenarios": [{"scenario_id": "obsolete", "state": "invalidated"}]}]},
        report_date="2026-09-03",
    )
    assert got["signal"]["status"] == "invalidated"
    assert got["signal"]["scenario_id"] == ""
    assert got["action"]["reason_code"] == "no_valid_scenario"
    assert not got["plan_permitted"]
    assert "情景" in "".join(got["recheck_conditions"])


def _confirmed_context(groups=None, plans=None):
    from scenario_posterior import build_scenario_posterior_timeline
    plans = plans or [{"scenario_id": "ready", "trigger_rules": {"early_0935": [_rule("breadth", "breadth_ratio", .5)]}, "invalidation_rules": []}]
    return {"date_str": "2026-09-03", "next_trade_date": "2026-09-04",
        "data_quality": {"status": "ok", "publication_mode": "decision"},
        "market_state": {"publication_mode": "decision", "statistics_layer": {"status": "ok"}},
        "publication_mode": "decision", "mainline_review": {"top1": "AI算力"},
        "echelon": groups if groups is not None else _groups(), "scenario_plans": plans,
        "scenario_posterior": build_scenario_posterior_timeline(plans, [_snapshot("early_0935", {"breadth_ratio": .8})],
            report_date="2026-09-03", trade_date="2026-09-04")}


def test_only_selected_mainline_candidates_can_be_executable_in_csv():
    from decision_dashboard import build_today_focus_rows
    rows = build_today_focus_rows(_confirmed_context())
    assert [row["代码"] for row in rows if row["可执行"] == "是"] == ["sz000006"]
    assert all(row["条件计划许可"] == "否" for row in rows if row["代码"] != "sz000006")


def test_candidate_outside_top_three_remains_observation_not_an_extra_order():
    from decision_dashboard import build_today_focus_rows
    groups = [{"height": "2板", "stock_details": [
        {"code": f"sz00000{i}", "name": f"候选{i}", "ml": "AI算力"} for i in range(1, 5)
    ]}]
    rows = build_today_focus_rows(_confirmed_context(groups))
    assert len([row for row in rows if row["可执行"] == "是"]) == 3
    assert rows[-1]["可执行"] == "否"


def test_active_scenario_candidate_subset_is_not_expanded_by_dashboard():
    from decision_dashboard import build_today_decision
    groups = [{"height": "2板", "stock_details": [
        {"code": "sz000001", "name": "一", "ml": "AI算力"},
        {"code": "sz000002", "name": "二", "ml": "AI算力"},
    ]}]
    plans = [{"scenario_id": "ready", "trigger_rules": {"early_0935": [_rule("breadth", "breadth_ratio", .5)]},
        "invalidation_rules": [], "trade_candidates": [{"code": "sz000002"}]}]
    decision = build_today_decision(_confirmed_context(groups, plans))
    assert decision["priority"]["primary"]["code"] == "sz000002"
    assert not decision["priority"]["alternates"]


def test_ineligible_heights_and_malformed_codes_are_audited_not_silently_dropped():
    from candidate_funnel import build_candidate_funnel
    funnel = build_candidate_funnel(mainline="AI算力", echelon=[{"height": "首板", "stock_details": [
        {"code": "sz000001", "name": "首板", "ml": "AI算力"},
        {"code": "bad-code", "name": "代码待核验", "ml": "AI算力"},
    ]}])
    assert funnel["input_count"] == 2
    assert not funnel["eligible_candidates"]
    assert len(funnel["rejected"]) == 2


def test_group_theme_percentage_never_grants_an_unattributed_stock_eligibility():
    from candidate_funnel import build_candidate_funnel
    from decision_dashboard import build_today_focus_rows
    groups = [{"height": "2板", "primary": "AI算力50%", "stock_details": [
        {"code": "sz000001", "name": "已归因", "ml": "AI算力"},
        {"code": "sz000002", "name": "未归因", "ml": "", "sub": ""}]}]
    funnel = build_candidate_funnel(echelon=groups, mainline="AI算力")
    assert [row["code"] for row in funnel["eligible_candidates"]] == ["sz000001"]
    assert any(row["code"] == "sz000002" for row in funnel["rejected"])
    rows = build_today_focus_rows(_confirmed_context(groups))
    assert [row["代码"] for row in rows if row["可执行"] == "是"] == ["sz000001"]
