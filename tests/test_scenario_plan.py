# -*- coding: utf-8 -*-
from scenario_plan import ScenarioPlan, build_scenario_plans


def test_scenario_plan_requires_time_bucket_triggers_and_position_bounds():
    plans = build_scenario_plans(
        report_date="2026-08-17",
        market_thesis={
            "breadth_relay_state": {"state": "breadth_strong_relay_weak", "breadth": "strong", "relay": "weak"},
            "dimensions": {"high_level_feedback": {"state": "positive"}},
        },
        market_snapshot={"breadth_ratio": 0.80, "limit_up": 106, "limit_down": 1, "max_height": 4},
    )

    assert 2 <= len(plans) <= 4
    assert all(isinstance(plan, ScenarioPlan) for plan in plans)
    for plan in plans:
        assert plan.auction_triggers
        assert plan.early_session_triggers
        assert plan.confirmation_triggers
        assert plan.afternoon_triggers
        assert plan.invalidation_conditions
        assert 0 <= plan.position_floor <= plan.position_ceiling <= 1
        assert plan.observation_roles
        assert plan.trade_candidates == () or isinstance(plan.trade_candidates, tuple)


def test_strong_breadth_but_weak_relay_prioritizes_repair_and_low_position_ceiling():
    plans = build_scenario_plans(
        report_date="2026-08-17",
        market_thesis={
            "breadth_relay_state": {"state": "breadth_strong_relay_weak", "breadth": "strong", "relay": "weak"},
            "dimensions": {
                "high_level_feedback": {"state": "positive"},
                "mainline_structure": {"state": "concentrated"},
            },
        },
        market_snapshot={"breadth_ratio": 0.80, "limit_up": 106, "limit_down": 1, "max_height": 4},
    )

    ids = {plan.scenario_id for plan in plans}
    assert "repair_after_breadth_only" in ids
    repair = next(plan for plan in plans if plan.scenario_id == "repair_after_breadth_only")
    assert repair.position_ceiling <= 0.5
    assert any("晋级" in trigger for trigger in repair.confirmation_triggers)
    assert "observation_pool" in repair.observation_roles


def test_weak_breadth_and_relay_emits_retreat_scenario_and_no_unconditional_trade_pool():
    plans = build_scenario_plans(
        report_date="2026-08-17",
        market_thesis={
            "breadth_relay_state": {"state": "breadth_weak_relay_weak", "breadth": "weak", "relay": "weak"},
            "dimensions": {"high_level_feedback": {"state": "negative"}},
        },
        market_snapshot={"breadth_ratio": 0.20, "limit_up": 8, "limit_down": 20, "max_height": 3},
    )

    retreat = next(plan for plan in plans if plan.scenario_id == "high_level_retreat")
    assert retreat.position_ceiling <= 0.2
    assert retreat.trade_candidates == ()
    assert any("跌停" in trigger or "负反馈" in trigger for trigger in retreat.invalidation_conditions)


def test_scenario_plans_share_the_versioned_outcome_definition():
    plans = build_scenario_plans(
        report_date="2026-08-17",
        market_thesis={
            "breadth_relay_state": {
                "state": "breadth_strong_relay_weak",
                "breadth": "strong",
                "relay": "weak",
            },
        },
        market_snapshot={"breadth_ratio": 0.8, "limit_up": 80, "limit_down": 2},
    )

    assert plans
    assert {plan.outcome_definition_id for plan in plans} == {"market-thesis/v1"}


def test_scenario_plan_exposes_machine_readable_rules_and_separate_pools():
    focus = [
        {"code": "sh600001", "name": "观察一"},
        {"code": "sz000002", "name": "观察二"},
        {"code": "sz000003", "name": "观察三"},
    ]
    plans = build_scenario_plans(
        report_date="2026-08-19",
        market_thesis={
            "breadth_relay_state": {"breadth": "strong", "relay": "strong"},
            "dimensions": {"high_level_feedback": {"state": "positive"}},
        },
        market_snapshot={"breadth_ratio": 0.7, "promotion_rate": 0.65, "limit_down": 2},
        focus_pool=focus,
    )
    for plan in plans:
        payload = plan.to_dict()
        assert set(payload["trigger_rules"]) == {"auction", "early_0935", "confirm_1000", "afternoon"}
        assert all(isinstance(rule, dict) and rule.get("metric") and rule.get("operator")
                   for rules in payload["trigger_rules"].values() for rule in rules)
        assert payload["invalidation_rules"]
        assert payload["position_adjustment_rules"]
        assert payload["observation_pool"] == focus
        assert payload["observation_pool"] is not payload["trade_candidates"]
        trade_codes = {row["code"] for row in payload["trade_candidates"]}
        assert trade_codes <= {row["code"] for row in payload["observation_pool"]}


def test_missing_limit_down_is_not_rendered_as_zero_in_scenario_premise():
    plans = build_scenario_plans(
        report_date="2026-08-19",
        market_thesis={
            "breadth_relay_state": {"breadth": "weak", "relay": "weak"},
            "dimensions": {"high_level_feedback": {"state": "unknown"}},
        },
        market_snapshot={},
    )
    risk = next(plan for plan in plans if plan.scenario_id == "risk_off_observation")
    assert all("约0家" not in text for text in risk.premise)
    assert any("缺失" in text for text in risk.premise)


def test_scenario_plan_applies_historical_probability_and_threshold_calibration():
    plans = build_scenario_plans(
        report_date="2026-08-19",
        market_thesis={"breadth_relay_state": {"breadth": "strong", "relay": "strong"}},
        market_snapshot={"breadth_ratio": 0.7, "promotion_rate": 0.65, "limit_down": 2},
        probabilities={"mainline_continuation": 0.72},
        prior_probabilities={"mainline_continuation": 0.61},
        threshold_adjustments={
            "mainline_continuation": {
                "sample_size": 20,
                "thresholds": {"breadth_ratio": {"operator": "gte", "value": 0.68}},
            }
        },
    )
    plan = next(plan for plan in plans if plan.scenario_id == "mainline_continuation")
    assert plan.probability == 0.72
    assert plan.prior_probability == 0.61
    assert plan.probability_source == "historical_calibration"
    assert plan.calibration_sample_size == 20
    breadth_rules = [rule for rules in plan.trigger_rules.values() for rule in rules if rule["metric"] == "breadth_ratio"]
    assert breadth_rules
    assert {rule.get("value") for rule in breadth_rules} == {0.68}
