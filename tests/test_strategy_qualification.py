from copy import deepcopy
import json

import pytest


def plan(sid="selective_mainline_hold"):
    from scenario_plan import build_scenario_plans
    ps = build_scenario_plans(report_date="2026-09-03", market_thesis={"breadth_relay_state": {"breadth": "weak", "relay": "strong"}})
    return next(p.to_dict() for p in ps if p.scenario_id == sid)


def record(p=None):
    from strategy_qualification import rule_fingerprint, RULE_VERSION, STRATEGY_OUTCOME
    p = p or plan()
    fingerprint = rule_fingerprint(p)
    return {"schema_version": "strategy-validation/v1", "strategy_id": p["scenario_id"],
        "rule_version": RULE_VERSION, "rule_fingerprint": fingerprint,
        "outcome_definition_id": STRATEGY_OUTCOME, "status": "passed", "method": "out_of_sample",
        "source": "fixture_independent_research", "evidence_ref": "fixture://strategy-validation-report",
        "trained_through": "2026-07-01", "evaluated_at": "2026-08-25T16:00:00+08:00",
        "valid_from": "2026-08-26", "valid_until": "2026-10-01", "sample_size": 10,
        "samples": [{"sample_id": f"sample-{i}", "strategy_id": p["scenario_id"],
            "rule_fingerprint": fingerprint, "outcome_definition_id": STRATEGY_OUTCOME,
            "trade_date": f"2026-08-{i:02d}", "outcome_date": f"2026-08-{i+3:02d}",
            "net_return": .01, "costs_included": True, "source": "fixture_backtest"} for i in range(1, 11)]}


def test_matching_independent_validation_contract_is_usable_without_legacy_counts():
    from strategy_qualification import assess_validation
    p = plan()
    got = assess_validation(p, record(p), report_date="2026-09-03", target_trade_date="2026-09-04")
    assert got["status"] == "validated"
    assert got["sample_size"] == 10
    assert got["source"] == "fixture_independent_research"


@pytest.mark.parametrize("patch", [
    {"strategy_id": "mainline_continuation"}, {"rule_version": "old"}, {"rule_fingerprint": "other"},
    {"outcome_definition_id": "market-thesis/v1"}, {"sample_size": 100}, {"sample_size": True},
    {"status": "unverified"}, {"method": "in_sample"}, {"source": "unknown"}, {"evidence_ref": ""},
    {"valid_until": "2026-09-03"}, {"evaluated_at": "2026-09-05T16:00:00+08:00"},
    {"evaluated_at": "2026-08-25 16:00:00"}, {"trained_through": "2026-08-10"},
])
def test_mismatched_or_unproven_validation_cannot_authorize(patch):
    from strategy_qualification import assess_validation
    p = plan(); r = record(p); r.update(patch)
    got = assess_validation(p, r, report_date="2026-09-03", target_trade_date="2026-09-04")
    assert got["status"] != "validated"
    assert got["issues"]


@pytest.mark.parametrize("field,value", [("strategy_id", "other"), ("rule_fingerprint", "old"),
    ("outcome_definition_id", "market-thesis/v1"), ("net_return", None), ("net_return", float("nan")),
    ("outcome_date", "2026-09-06"), ("costs_included", False), ("source", "unknown")])
def test_each_sample_must_match_and_have_mature_known_outcome(field, value):
    from strategy_qualification import assess_validation
    r = record(); r["samples"][0][field] = value
    assert assess_validation(plan(), r, report_date="2026-09-03", target_trade_date="2026-09-04")["status"] != "validated"


def test_duplicate_samples_and_too_few_samples_are_not_validation():
    from strategy_qualification import assess_validation
    r = record(); r["samples"][1] = deepcopy(r["samples"][0])
    assert assess_validation(plan(), r, report_date="2026-09-03")["status"] != "validated"
    r = record(); r["samples"] = r["samples"][:9]; r["sample_size"] = 9
    assert assess_validation(plan(), r, report_date="2026-09-03")["status"] != "validated"


def test_legacy_statistics_are_descriptive_not_a_validation_certificate():
    from strategy_qualification import assess_validation, descriptive_statistics
    p = plan()
    assert assess_validation(p, {"historical_samples": 999, "win_rate_sample_size": 999}, report_date="2026-09-03")["status"] != "validated"
    stats = descriptive_statistics({"scene": "BROAD", "win_rate_sample_size": 25, "win_rate": .6})
    assert stats["sample_size"] == 25
    assert stats["qualification"] == "descriptive_only"


def test_fingerprint_changes_with_rule_not_with_candidate_or_date():
    from strategy_qualification import rule_fingerprint
    p = plan(); changed = deepcopy(p)
    changed["trade_candidates"] = [{"code": "sz000001"}]
    changed["report_date"] = "2026-09-04"
    assert rule_fingerprint(p) == rule_fingerprint(changed)
    changed["trigger_rules"]["auction"][0]["value"] = .99
    assert rule_fingerprint(p) != rule_fingerprint(changed)


def test_validation_file_is_read_only_and_bad_or_missing_files_fail_closed(tmp_path):
    from strategy_qualification import load_validation_records
    path = tmp_path / "validation.json"
    assert load_validation_records(path)["records"] == []
    path.write_text("broken", encoding="utf-8")
    assert load_validation_records(path)["status"] == "invalid"
    assert path.read_text(encoding="utf-8") == "broken"
    path.write_text(json.dumps({"schema_version": "strategy-validation-set/v1", "records": [record()]}), encoding="utf-8")
    got = load_validation_records(path)
    assert got["status"] == "loaded" and len(got["records"]) == 1


def quality():
    names = ("universe", "price_raw", "breadth", "limit_pool", "echelon", "sector", "daily_delta", "price_qfq")
    return {"status": "degraded", "publication_mode": "observation", "run_id": "run", "modules": {
        **{name: {"status": "ok", "run_id": "run", "source_timestamp": "2026-09-03",
                  "lineage": {"report_date": "2026-09-03"}} for name in names},
        "ai": {"status": "unavailable"}, "bomb_metrics": {"status": "unavailable"}},
        "critical_blocked": [], "decision_degraded": ["ai", "bomb_metrics"]}


def event_metrics():
    from report_logic import compute_ladder_metrics
    m = compute_ladder_metrics([{"code": "sz000001", "height": 2, "limit_up_attempted": True,
        "broken": False, "reclosed": False, "board_type": "turnover"}])
    m["event_population"] = {"scope": "full_market_limit_up_attempts", "complete": True,
                             "report_date": "2026-09-03", "source": "fixture_events"}
    return m


def test_ai_and_unrelated_market_reclose_do_not_block_validated_core_strategy():
    from strategy_qualification import qualify_strategies
    p = plan(); q = quality()
    got = qualify_strategies([p], quality=q, validation_records=[record(p)], event_metrics=event_metrics(),
                             report_date="2026-09-03", target_trade_date="2026-09-04")
    assert got["strategies"][p["scenario_id"]]["status"] == "eligible"
    assert got["publication_mode"] == "decision"
    assert "ai" in got["nonblocking_modules"]
    assert q == quality()


@pytest.mark.parametrize("change", ["missing_core", "stale", "mixed_batch", "critical", "invalid_core"])
def test_core_market_problem_cannot_be_bypassed_by_validation(change):
    from strategy_qualification import qualify_strategies
    q = quality()
    if change == "missing_core": del q["modules"]["breadth"]
    elif change == "stale": q["modules"]["price_raw"]["used_stale"] = True
    elif change == "mixed_batch": q["modules"]["breadth"]["run_id"] = "other"
    elif change == "critical": q["critical_blocked"] = ["run_id_consistency"]
    else: q["modules"]["price_raw"]["status"] = "degraded"
    got = qualify_strategies([plan()], quality=q, validation_records=[record()], event_metrics=event_metrics(), report_date="2026-09-03", target_trade_date="2026-09-04")
    assert got["publication_mode"] == "facts_only"
    assert not got["strategies"]["selective_mainline_hold"]["plan_permitted"]


def test_event_not_applicable_is_strategy_specific_not_missing_global_data():
    from scenario_plan import build_scenario_plans
    from strategy_qualification import qualify_strategies
    ps = [p.to_dict() for p in build_scenario_plans(report_date="2026-09-03", market_thesis={"breadth_relay_state":{"breadth":"strong","relay":"strong"}})]
    got = qualify_strategies(ps, quality=quality(), validation_records=[record(p) for p in ps], event_metrics=event_metrics(), report_date="2026-09-03", target_trade_date="2026-09-04")
    assert got["strategies"]["mainline_continuation"]["status"] == "eligible"
    repair = got["strategies"]["intraday_divergence_repair"]
    assert repair["status"] == "not_applicable"
    assert "reclose_rate:no_events" in repair["issues"]
    assert got["publication_mode"] == "decision"


def test_closing_subset_never_qualifies_market_wide_event_strategy():
    from scenario_plan import build_scenario_plans
    from strategy_qualification import qualify_strategies
    p = build_scenario_plans(report_date="2026-09-03", market_thesis={"breadth_relay_state":{"breadth":"strong","relay":"strong"}})[0].to_dict()
    metrics = event_metrics(); metrics["event_population"] = {"scope": "closing_limit_pool", "complete": True}
    got = qualify_strategies([p], quality=quality(), validation_records=[record(p)], event_metrics=metrics, report_date="2026-09-03", target_trade_date="2026-09-04")
    assert got["strategies"][p["scenario_id"]]["status"] == "missing_dependency"
    assert not got["strategies"][p["scenario_id"]]["plan_permitted"]


def test_latest_revoked_validation_cannot_fall_back_to_old_pass():
    from strategy_qualification import qualify_strategies
    good = record(); revoked = deepcopy(good); revoked["status"] = "revoked"
    got = qualify_strategies([plan()], quality=quality(), validation_records=[good, revoked], event_metrics=event_metrics(), report_date="2026-09-03", target_trade_date="2026-09-04")
    assert got["strategies"]["selective_mainline_hold"]["status"] == "unverified"


def test_event_denominator_counts_attempts_not_explicit_non_attempts():
    from report_logic import compute_ladder_metrics
    got=compute_ladder_metrics([
        {"code":"sz000001","height":2,"limit_up_attempted":True,"broken":False,"reclosed":False,"board_type":"turnover"},
        {"code":"sz000002","height":1,"limit_up_attempted":False,"broken":False,"reclosed":False,"board_type":"turnover"},
    ])
    assert got["bomb_rate"]["trials"]==1
    assert got["event_input_coverage"]["event_counts"]["attempted"]==1
    assert got["event_input_coverage"]["event_counts"]["broken"]==0


def test_candidate_board_dependency_uses_its_own_records_not_unrelated_stock_coverage():
    from strategy_qualification import build_strategy_event_input,qualify_strategies
    from limit_events import build_limit_event_snapshot
    p=plan();p['trade_candidates']=[{'code':'sz000001','name':'核心'}]
    snapshot=build_limit_event_snapshot([
        {'code':'sz000001','date':'2026-09-03','board_type':'turnover','source':'fixture','source_timestamp':'2026-09-03T15:01:00+08:00'},
        {'code':'sz000002','date':'2026-09-03','board_type':None,'source':'fixture','source_timestamp':'2026-09-03T15:01:00+08:00'}],trade_date='2026-09-03')
    event_input=build_strategy_event_input(snapshot,report_date='2026-09-03')
    got=qualify_strategies([p],quality=quality(),validation_records=[record(p)],event_metrics=event_input,report_date='2026-09-03',target_trade_date='2026-09-04')
    assert got['strategies'][p['scenario_id']]['plan_permitted']
    p['trade_candidates']=[{'code':'sz000003','name':'不存在的覆盖'}]
    got=qualify_strategies([p],quality=quality(),validation_records=[record(p)],event_metrics=event_input,report_date='2026-09-03',target_trade_date='2026-09-04')
    assert not got['strategies'][p['scenario_id']]['plan_permitted']
    assert 'board_structure:missing' in got['strategies'][p['scenario_id']]['issues']


def test_sidecar_closing_events_never_claim_full_market_attempt_population():
    from strategy_qualification import build_strategy_event_input
    from limit_events import build_limit_event_snapshot
    snap=build_limit_event_snapshot([{'code':'sz000001','date':'2026-09-03','broken':False}],trade_date='2026-09-03')
    built=build_strategy_event_input(snap,report_date='2026-09-03')
    assert built['event_population']['scope']=='closing_limit_pool'
    assert built['event_population']['complete'] is False


@pytest.mark.parametrize("evaluated_at", ["2026-08-25T00:00:00+08:00", "2026-08-25T16:00:00+08:00"])
def test_same_day_date_only_outcome_is_not_yet_proven_mature(evaluated_at):
    from strategy_qualification import assess_validation
    r = record()
    r["evaluated_at"] = evaluated_at
    for sample in r["samples"]:
        sample["trade_date"] = sample["outcome_date"] = "2026-08-25"
    result = assess_validation(plan(), r, report_date="2026-09-03")
    assert result["status"] == "unverified"
    assert result["sample_size"] == 0


@pytest.mark.parametrize("matured_at,expected", [
    ("2026-08-25T15:00:00+08:00", "validated"),
    ("2026-08-25T07:00:00Z", "validated"),
    ("2026-08-25T16:00:00+08:00", "validated"),
    ("2026-08-25T16:00:01+08:00", "unverified"),
    ("2026-08-25T15:00:00", "unverified"),
    ("2026-08-24T15:00:00+08:00", "unverified"),
    (None, "unverified"),
])
def test_same_day_outcome_requires_known_timezone_aware_maturity(matured_at, expected):
    from strategy_qualification import assess_validation
    r = record()
    for sample in r["samples"]:
        sample["trade_date"] = sample["outcome_date"] = "2026-08-25"
        sample["outcome_matured_at"] = matured_at
    assert assess_validation(plan(), r, report_date="2026-09-03")["status"] == expected
