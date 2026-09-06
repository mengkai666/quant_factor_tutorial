from __future__ import annotations

import copy
import json

import pytest

import trade_plan_review as journal


def _plan():
    return {
        "position": "2 成",
        "scenario_status": "active",
        "decision_scenario_id": "confirmed-mainline",
        "priority": {
            "primary": {
                "code": "sh600001", "name": "条件股", "sector": "AI算力",
                "role": "attack", "action": "条件确认后执行",
                "trigger": "量价联动", "invalid": "跌破确认位",
            },
            "alternates": [],
        },
    }


def _ready():
    return {
        "data": {"status": "ready"},
        "strategy": {"status": "applicable"},
        "signal": {"status": "met"},
        "action": {
            "status": "enter_plan", "reason_code": "confirmed_plan",
            "reason": "门禁与当前时点已确认；不是成交。",
        },
        "recheck_conditions": ["目标交易日重新确认，条件失效则撤销。"],
        "plan_permitted": True,
        "execution_allowed": False,
        "execution_ready": False,
    }


def _daily(plan=None, readiness=None, **kwargs):
    return journal.build_daily_decision_record(
        _plan() if plan is None else plan,
        report_date=kwargs.pop("report_date", "2026-09-03"),
        readiness=_ready() if readiness is None else readiness,
        **kwargs,
    )


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    "data,strategy,action,reason_code,candidates,scenario,signal,permitted,expected",
    [
        ("missing", "unverified", "no_new_positions", "data_unavailable", False, "no_valid_scenario", "not_evaluable", False, "no_trade_data_unavailable"),
        ("expired", "applicable", "enter_plan", "confirmed_plan", True, "active", "met", True, "no_trade_data_unavailable"),
        ("ready", "unverified", "no_new_positions", "qualification_incomplete", True, "active", "met", False, "no_trade_strategy_unverified"),
        ("ready", "not_applicable", "no_new_positions", "market_no_trade", True, "active", "met", False, "no_trade_market_defensive"),
        ("ready", "not_applicable", "no_new_positions", "no_candidates", False, "active", "met", False, "no_trade_no_candidate"),
        ("ready", "applicable", "wait_confirmation", "signal_pending", True, "awaiting_confirmation", "not_triggered", True, "wait_confirmation"),
        ("ready", "applicable", "enter_plan", "confirmed_plan", True, "no_valid_scenario", "met", True, "wait_confirmation"),
        ("ready", "applicable", "enter_plan", "confirmed_plan", True, "active", "invalidated", True, "wait_confirmation"),
        ("ready", "applicable", "no_new_positions", "existing_gate", True, "active", "met", False, "no_trade_strategy_unverified"),
        ("ready", "applicable", "enter_plan", "confirmed_plan", True, "active", "met", True, "conditional_plan"),
    ],
)
def test_daily_decision_keeps_every_no_trade_or_conditional_day(
    tmp_path, data, strategy, action, reason_code, candidates, scenario, signal,
    permitted, expected,
):
    plan, ready = _plan(), _ready()
    if not candidates:
        plan["priority"] = {}
    plan["scenario_status"] = scenario
    ready.update(
        data={"status": data}, strategy={"status": strategy}, signal={"status": signal},
        action={"status": action, "reason_code": reason_code, "reason": "保留真实判断原因"},
        plan_permitted=permitted,
    )
    record = _daily(plan, ready)
    assert record["event_type"] == "daily_decision"
    assert record["status"] == expected
    assert record["reason"] == "保留真实判断原因"
    assert record["recovery_conditions"] == ready["recheck_conditions"]
    assert record["execution_allowed"] is False
    path = tmp_path / "journal.jsonl"
    assert journal.append_daily_decision_once(path, record)["appended"] is True
    assert len(_rows(path)) == 1
    assert journal.build_trade_plan_review(path)["plan_count"] == 0


def test_daily_decision_does_not_invent_missing_qualifications_or_trading_date():
    record = journal.build_daily_decision_record(None, report_date="2026-09-06")
    assert record["status"] == "no_trade_data_unavailable"
    assert record["trading_date"] is None
    assert record["decision_scenario_id"] is None
    assert record["scenario_status"] == "no_valid_scenario"
    assert record["plan_permitted"] is False
    assert record["execution_allowed"] is False
    assert record["reason"]
    assert record["recovery_conditions"]


def test_daily_decision_preserves_explicit_context_and_future_decision_axis():
    ready = _ready()
    ready["decision"] = {
        "status": "wait_confirmation", "reason_code": "intraday_confirmation_pending",
        "reason": "等待目标交易日竞价确认。",
    }
    record = _daily(
        readiness=ready, trading_date="2026-09-07", plan_version="approved-plan-2",
        candidate_funnel={"fingerprint": "funnel-17", "eligible_candidates": [{"code": "sh600001"}]},
    )
    assert record["report_date"] == "2026-09-03"
    assert record["trading_date"] == "2026-09-07"
    assert record["status"] == "wait_confirmation"
    assert record["reason"] == "等待目标交易日竞价确认。"
    assert record["plan_version"] == "approved-plan-2"
    assert record["candidate_funnel_fingerprint"] == "funnel-17"
    assert record["scenario_status"] == "active"
    assert record["decision_scenario_id"] == "confirmed-mainline"


@pytest.mark.parametrize("scenario_status", ["awaiting_confirmation", "no_valid_scenario"])
def test_daily_decision_never_promotes_top_rank_or_stale_active_id(scenario_status):
    plan = _plan()
    plan.update(
        scenario_status=scenario_status, decision_scenario_id=None,
        active_scenario_id="stale-active", top_scenario_id="rank-only",
        ranked=[{"scenario_id": "rank-only", "rank": 1}],
    )
    ready = _ready()
    ready["signal"]["scenario_id"] = "rank-only"
    record = _daily(plan, ready)
    assert record["status"] == "wait_confirmation"
    assert record["decision_scenario_id"] is None
    assert record["scenario_status"] == scenario_status


def test_daily_decision_requires_an_explicit_effective_scenario():
    plan = _plan()
    plan.pop("decision_scenario_id")
    plan["top_scenario_id"] = "rank-only"
    record = _daily(plan)
    assert record["status"] == "wait_confirmation"
    assert record["decision_scenario_id"] is None


def test_daily_decision_versions_keep_transitions_changes_and_reversions(tmp_path):
    path = tmp_path / "journal.jsonl"
    original = _daily()
    first = journal.append_daily_decision_once(path, original)
    prefix = path.read_bytes()
    retry = {**original, "recorded_at": "2099-01-01T00:00:00+08:00"}
    assert journal.append_daily_decision_once(path, retry)["appended"] is False
    assert journal.append_daily_decision_once(path, first)["appended"] is False
    assert path.read_bytes() == prefix

    ready = _ready()
    ready["signal"]["status"] = "not_triggered"
    ready["action"].update(status="wait_confirmation", reason="条件尚未确认。")
    second = journal.append_daily_decision_once(path, _daily(readiness=ready))
    third = journal.append_daily_decision_once(path, original)
    assert [row["version"] for row in _rows(path)] == [1, 2, 3]
    assert first["decision_id"] == second["decision_id"] == third["decision_id"]
    assert len({first["revision_id"], second["revision_id"], third["revision_id"]}) == 3
    assert second["previous_revision_id"] == first["revision_id"]
    assert second["status_transition"] == {"from": "conditional_plan", "to": "wait_confirmation"}
    assert second["change_summary"]["reason"] == {"before": original["reason"], "after": "条件尚未确认。"}
    assert third["content_fingerprint"] == first["content_fingerprint"]
    assert third["status_transition"] == {"from": "wait_confirmation", "to": "conditional_plan"}
    assert path.read_bytes().startswith(prefix)


def test_daily_decision_funnel_and_plan_changes_are_not_swallowed(tmp_path):
    path = tmp_path / "journal.jsonl"
    funnel = {"eligible_candidates": [{"code": "sh600001"}], "rejected_counts": {"not_mainline": 1}}
    first = journal.append_daily_decision_once(path, _daily(candidate_funnel=funnel))
    same_funnel = dict(reversed(list(funnel.items())))
    assert journal.append_daily_decision_once(path, _daily(candidate_funnel=same_funnel))["appended"] is False
    funnel["rejected_counts"]["not_mainline"] = 2
    second = journal.append_daily_decision_once(path, _daily(candidate_funnel=funnel))
    assert second["version"] == 2
    assert first["candidate_funnel_fingerprint"] != second["candidate_funnel_fingerprint"]
    plan = _plan()
    plan["priority"]["primary"]["trigger"] = "竞价与量价同时确认"
    third = journal.append_daily_decision_once(path, _daily(plan, candidate_funnel=funnel))
    assert third["version"] == 3
    assert second["plan_version"] != third["plan_version"]


def test_daily_decision_is_a_detached_snapshot():
    plan, ready = _plan(), _ready()
    record = _daily(plan, ready)
    before = copy.deepcopy(record)
    ready["action"]["reason"] = "后续改动"
    ready["recheck_conditions"].append("后续改动")
    plan["priority"]["primary"]["trigger"] = "后续改动"
    assert record == before
    json.dumps(record, ensure_ascii=False, allow_nan=False)


def test_daily_decision_review_includes_history_but_counts_each_day_once(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal.append_daily_decision_once(path, _daily(report_date="2026-09-01"))
    journal.append_daily_decision_once(path, _daily(plan={}, report_date="2026-09-02"))
    journal.append_daily_decision_once(path, _daily(report_date="2026-09-02"))
    journal.append_daily_decision_once(path, _daily(report_date="2026-09-08"))
    review = journal.build_daily_decision_review(path, through_report_date="2026-09-03")
    assert review["decision_count"] == 2
    assert review["revision_count"] == 3
    assert review["status_counts"]["conditional_plan"] == 2
    assert review["status_counts"]["no_trade_no_candidate"] == 0
    assert [item["report_date"] for item in review["items"]] == ["2026-09-01", "2026-09-02"]
    assert len(review["items"][1]["revisions"]) == 2
    assert journal.build_daily_decision_review(path, report_date="2026-09-02")["decision_count"] == 1



def _saved_plan(path, *, report_date="2026-09-03", code="sh600001", plan=None):
    plan = copy.deepcopy(_plan() if plan is None else plan)
    if plan.get("priority", {}).get("primary"):
        plan["priority"]["primary"]["code"] = code
    record = journal.build_trade_plan_records(plan, report_date=report_date, readiness=_ready())[0]
    return journal.append_trade_plan_once(path, record)


def test_plan_revisions_keep_legacy_plan_id_and_stable_logical_identity(tmp_path):
    path = tmp_path / "journal.jsonl"
    original = journal.build_trade_plan_records(_plan(), report_date="2026-09-03", readiness=_ready())[0]
    first = journal.append_trade_plan_once(path, original)
    prefix = path.read_bytes()
    assert first["plan_id"] == "2026-09-03:sh600001:primary:v1"
    assert first["logical_plan_id"] == "2026-09-03:sh600001"
    assert first["plan_version"] == first["version"] == 1
    assert journal.append_trade_plan_once(path, first)["appended"] is False

    changed_plan = _plan()
    changed_plan["priority"]["primary"]["trigger"] = "新触发条件"
    changed = journal.build_trade_plan_records(changed_plan, report_date="2026-09-03", readiness=_ready())[0]
    assert changed["plan_id"] == original["plan_id"]
    assert changed["logical_plan_id"] == original["logical_plan_id"]
    assert changed["content_fingerprint"] != original["content_fingerprint"]
    second = journal.append_trade_plan_once(path, changed)
    third = journal.append_trade_plan_once(path, original)
    assert second["plan_version"] == second["version"] == 2
    assert third["plan_version"] == third["version"] == 3
    assert second["previous_revision_id"] == first["revision_id"]
    assert second["change_summary"]["trigger"] == {"before": "量价联动", "after": "新触发条件"}
    assert path.read_bytes().startswith(prefix)
    assert len(_rows(path)) == 3
    review = journal.build_trade_plan_review(path)
    assert review["plan_count"] == 1
    assert review["plan_revision_count"] == 3
    assert review["items"][0]["plan"]["trigger"] == "量价联动"
    assert len(review["items"][0]["revisions"]) == 3


def test_priority_change_is_a_revision_not_an_extra_trade_and_keeps_old_outcome_link(tmp_path):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    journal.append_trade_plan_outcome_once(path, first["plan_id"], "not_triggered")
    plan = _plan()
    row = plan["priority"].pop("primary")
    plan["priority"]["alternates"] = [row]
    record = journal.build_trade_plan_records(plan, report_date="2026-09-03", readiness=_ready())[0]
    assert record["priority"] == "alternate"
    second = journal.append_trade_plan_once(path, record)
    assert second["logical_plan_id"] == first["logical_plan_id"]
    assert second["plan_version"] == 2
    review = journal.build_trade_plan_review(path)
    assert review["plan_count"] == 1
    assert review["outcome_count"] == 1
    assert review["status_counts"]["not_triggered"] == 1
    assert review["items"][0]["plan"]["priority"] == "alternate"
    assert review["items"][0]["outcome_matches_latest_revision"] is False


def test_plan_records_preserve_supplied_trading_day_and_effective_scenario():
    plan = _plan()
    plan["trading_date"] = "2026-09-07"
    record = journal.build_trade_plan_records(plan, report_date="2026-09-06", readiness=_ready())[0]
    assert record["trading_date"] == "2026-09-07"
    assert record["scenario_status"] == "active"
    assert record["decision_scenario_id"] == "confirmed-mainline"


@pytest.mark.parametrize("confirmed_status", ["not_triggered", "triggered_not_filled", "filled", "cancelled"])
def test_unknown_outcome_can_be_confirmed_without_overwriting_history(tmp_path, confirmed_status):
    path = tmp_path / "journal.jsonl"
    plan = _saved_plan(path)
    first = journal.append_trade_plan_outcome_once(path, plan["plan_id"], "unknown")
    prefix = path.read_bytes()
    confirmed = journal.append_trade_plan_outcome_once(
        path, plan["plan_id"], confirmed_status, actual={"source": "manual_confirmation", "note": "如实确认"},
    )
    assert confirmed["appended"] is True
    assert confirmed["version"] == 2
    assert confirmed["previous_revision_id"] == first["revision_id"]
    assert confirmed["plan_revision_id"] == plan["revision_id"]
    assert confirmed["status_transition"] == {"from": "unknown", "to": confirmed_status}
    assert journal.append_trade_plan_outcome_once(
        path, plan["plan_id"], confirmed_status, actual={"note": "如实确认", "source": "manual_confirmation"},
    )["appended"] is False
    assert journal.append_trade_plan_outcome_once(path, plan["plan_id"], "unknown")["appended"] is False
    assert path.read_bytes().startswith(prefix)
    review = journal.build_trade_plan_review(path)
    assert review["outcome_count"] == 1
    assert review["outcome_revision_count"] == 2
    assert review["status_counts"][confirmed_status] == 1
    assert review["status_counts"]["unknown"] == 0


def test_only_an_explicit_outcome_may_advance_triggered_to_filled(tmp_path):
    path = tmp_path / "journal.jsonl"
    plan = _saved_plan(path)
    journal.append_daily_decision_once(path, _daily())
    assert journal.build_trade_plan_review(path)["filled_count"] == 0
    journal.append_trade_plan_outcome_once(path, plan["plan_id"], "triggered_not_filled")
    review = journal.build_trade_plan_review(path)
    assert review["triggered_count"] == 1
    assert review["filled_count"] == 0
    assert review["net_pnl"] is None
    confirmed = journal.append_trade_plan_outcome_once(path, plan["plan_id"], "filled", actual={"quantity": 100})
    assert confirmed["appended"] is True
    assert journal.build_trade_plan_review(path)["filled_count"] == 1
    assert journal.build_trade_plan_review(path)["pnl_known_count"] == 0


def test_outcome_can_target_a_historical_revision_without_claiming_latest_plan_filled(tmp_path):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    changed = _plan()
    changed["priority"]["primary"]["trigger"] = "修订后的条件"
    second = _saved_plan(path, plan=changed)
    outcome = journal.append_trade_plan_outcome_once(
        path, first["plan_id"], "filled", plan_revision_id=first["revision_id"],
        actual={"quantity": 100},
    )
    assert outcome["plan_revision_id"] == first["revision_id"]
    assert outcome["plan_version"] == 1
    review = journal.build_trade_plan_review(path)
    assert review["filled_count"] == 1
    item = review["items"][0]
    assert item["plan"]["revision_id"] == second["revision_id"]
    assert item["outcome"]["plan_revision_id"] == first["revision_id"]
    assert item["outcome_matches_latest_revision"] is False


def test_outcome_rejects_an_unrelated_revision_without_writing(tmp_path):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    other = _saved_plan(path, code="sz000002")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="revision"):
        journal.append_trade_plan_outcome_once(
            path, first["plan_id"], "filled", plan_revision_id=other["revision_id"],
        )
    assert path.read_bytes() == before


def test_legacy_plan_can_be_retried_then_revised_and_unknown_confirmed(tmp_path):
    path = tmp_path / "journal.jsonl"
    plan = _plan()
    plan.pop("scenario_status")
    plan.pop("decision_scenario_id")
    legacy = {
        "event_type": "trade_plan", "schema_version": "trade-plan/v1",
        "plan_id": "2026-09-03:sh600001:primary:v1", "report_date": "2026-09-03",
        "code": "sh600001", "name": "条件股", "sector": "AI算力", "role": "attack",
        "priority": "primary", "planned_action": "条件确认后执行", "trigger": "量价联动",
        "invalid": "跌破确认位", "position_cap": "2 成", "plan_status": "conditional",
    }
    legacy_unknown = {
        "event_type": "trade_plan_outcome", "schema_version": "trade-plan-outcome/v1",
        "plan_id": legacy["plan_id"], "status": "unknown", "actual": {},
    }
    path.write_text(json.dumps(legacy) + "\n" + json.dumps(legacy_unknown) + "\n", encoding="utf-8")
    prefix = path.read_bytes()
    rebuilt = journal.build_trade_plan_records(plan, report_date="2026-09-03", readiness=_ready())[0]
    assert journal.append_trade_plan_once(path, rebuilt)["appended"] is False
    plan["priority"]["primary"]["invalid"] = "新的失效条件"
    changed = _saved_plan(path, plan=plan)
    assert changed["plan_version"] == 2
    with pytest.raises(ValueError, match="ambiguous.*plan_revision_id"):
        journal.append_trade_plan_outcome_once(path, legacy["plan_id"], "cancelled")
    confirmation = journal.append_trade_plan_outcome_once(path, legacy["plan_id"], "cancelled", plan_revision_id=changed["revision_id"])
    assert confirmation["appended"] is True
    assert path.read_bytes().startswith(prefix)
    review = journal.build_trade_plan_review(path)
    assert review["plan_count"] == 1
    assert review["plan_revision_count"] == 2
    assert review["status_counts"]["cancelled"] == 1


@pytest.mark.parametrize("actual", [
    {"net_pnl": 12.5},
    {"net_pnl": 12.5, "currency": "CNY"},
    {"net_pnl": 12.5, "pnl_basis": "realized_after_fees_and_taxes"},
    {"net_pnl": 12.5, "currency": " ", "pnl_basis": "realized_after_fees_and_taxes"},
    {"net_pnl": 12.5, "currency": "CNY", "pnl_basis": "unknown"},
    {"net_pnl": float("inf"), "currency": "CNY", "pnl_basis": "realized_after_fees_and_taxes"},
    {"net_pnl": float("nan"), "currency": "CNY", "pnl_basis": "realized_after_fees_and_taxes"},
    {"net_pnl": True, "currency": "CNY", "pnl_basis": "realized_after_fees_and_taxes"},
    {"net_pnl": "not-a-number", "currency": "CNY", "pnl_basis": "realized_after_fees_and_taxes"},
    {"pnl": 12.5, "currency": "CNY", "pnl_basis": "gross"},
])
def test_unqualified_net_pnl_is_unknown_but_explicit_fill_statistics_survive(tmp_path, actual):
    path = tmp_path / "journal.jsonl"
    plan = _saved_plan(path)
    journal.append_trade_plan_outcome_once(path, plan["plan_id"], "filled", actual={"source": "manual_confirmation", **actual})
    review = journal.build_trade_plan_review(path)
    assert review["filled_count"] == 1
    assert review["triggered_count"] == 1
    assert review["status_counts"]["filled"] == 1
    assert review["pnl_known_count"] == 0
    assert review["pnl_unknown_count"] == 1
    assert review["net_pnl"] is None
    assert review["items"][0]["pnl_status"] == "unknown"
    assert review["has_realized_trade_result"] is False


def test_units_can_be_confirmed_later_and_corrections_count_only_once(tmp_path):
    path = tmp_path / "journal.jsonl"
    plan = _saved_plan(path)
    journal.append_trade_plan_outcome_once(path, plan["plan_id"], "filled", actual={"net_pnl": 12.5, "source": "manual_confirmation"})
    actual = {"net_pnl": 12.5, "currency": "CNY", "pnl_basis": "realized_after_fees_and_taxes", "source": "manual_confirmation"}
    assert journal.append_trade_plan_outcome_once(path, plan["plan_id"], "filled", actual=actual)["appended"] is True
    assert journal.append_trade_plan_outcome_once(path, plan["plan_id"], "filled", actual=actual)["appended"] is False
    review = journal.build_trade_plan_review(path)
    assert review["filled_count"] == review["pnl_known_count"] == 1
    assert review["pnl_unknown_count"] == 0
    assert review["net_pnl"] == 12.5
    assert review["net_pnl_currency"] == "CNY"
    assert review["net_pnl_basis"] == "realized_after_fees_and_taxes"


def test_return_summary_never_adds_incompatible_currencies_or_bases(tmp_path):
    path = tmp_path / "journal.jsonl"
    cases = [
        ("sh600001", 12.0, "CNY", "realized_after_fees_and_taxes"),
        ("sh600002", -2.0, "cny", "realized_after_fees_and_taxes"),
        ("sh600003", 5.0, "USD", "realized_after_fees_and_taxes"),
        ("sh600004", 7.0, "CNY", "realized_after_fees"),
    ]
    for code, pnl, currency, basis in cases:
        plan = _saved_plan(path, code=code)
        journal.append_trade_plan_outcome_once(
            path, plan["plan_id"], "filled", actual={"net_pnl": pnl, "currency": currency, "pnl_basis": basis, "source": "manual_confirmation"},
        )
    review = journal.build_trade_plan_review(path)
    assert review["filled_count"] == review["pnl_known_count"] == 4
    assert review["net_pnl"] is None
    assert review["net_pnl_currency"] is None
    assert review["net_pnl_basis"] is None
    grouped = {(group["currency"], group["pnl_basis"]): (group["net_pnl"], group["count"])
               for group in review["pnl_by_currency_and_basis"]}
    assert grouped == {
        ("CNY", "realized_after_fees_and_taxes"): (10.0, 2),
        ("USD", "realized_after_fees_and_taxes"): (5.0, 1),
        ("CNY", "realized_after_fees"): (7.0, 1),
    }


def test_unfilled_and_unknown_outcomes_never_contribute_to_return_summary(tmp_path):
    path = tmp_path / "journal.jsonl"
    actual = {"net_pnl": 999.0, "currency": "CNY", "pnl_basis": "realized_after_fees_and_taxes", "source": "manual_confirmation"}
    for code, status in [("sh600001", "triggered_not_filled"), ("sh600002", "unknown"), ("sh600003", "not_triggered")]:
        plan = _saved_plan(path, code=code)
        journal.append_trade_plan_outcome_once(path, plan["plan_id"], status, actual=actual)
    review = journal.build_trade_plan_review(path)
    assert review["triggered_count"] == 1
    assert review["filled_count"] == review["pnl_known_count"] == 0
    assert review["net_pnl"] is None


def test_review_selects_historical_plan_dates_and_accepts_late_confirmation(tmp_path):
    path = tmp_path / "journal.jsonl"
    old = _saved_plan(path, report_date="2026-08-31")
    recent = _saved_plan(path, report_date="2026-09-02")
    _saved_plan(path, report_date="2026-09-08")
    journal.append_trade_plan_outcome_once(path, old["plan_id"], "unknown")
    journal.append_trade_plan_outcome_once(path, old["plan_id"], "filled", actual={
        "confirmed_at": "2026-09-06T15:00:00+08:00", "net_pnl": 20.0, "source": "manual_confirmation",
        "currency": "CNY", "pnl_basis": "realized_after_fees_and_taxes",
    })
    journal.append_trade_plan_outcome_once(path, recent["plan_id"], "not_triggered")
    review = journal.build_trade_plan_review(path, through_report_date="2026-09-03")
    assert review["plan_count"] == 2
    assert review["outcome_count"] == 2
    assert review["filled_count"] == 1
    assert review["net_pnl"] == 20.0
    assert [item["report_date"] for item in review["items"]] == ["2026-08-31", "2026-09-02"]
    assert journal.build_trade_plan_review(path, report_date="2026-09-03")["plan_count"] == 0
    assert journal.build_trade_plan_review(path, from_report_date="2026-09-01", through_report_date="2026-09-03")["plan_count"] == 1
    assert journal.build_trade_plan_review(path)["plan_count"] == 3


@pytest.mark.parametrize("builder", ["build_trade_plan_review", "build_daily_decision_review"])
def test_review_rejects_conflicting_exact_day_and_history_filters(tmp_path, builder):
    with pytest.raises(ValueError, match="report_date"):
        getattr(journal, builder)(tmp_path / "missing.jsonl", report_date="2026-09-03", through_report_date="2026-09-03")



@pytest.mark.parametrize("status", ["no_trade_data_unavailable", "no_trade_strategy_unverified"])
def test_explicit_decision_block_is_not_relabeled_as_waiting(status):
    ready = _ready()
    ready["decision"] = {"status": status, "reason": "现有结论仍阻断计划。"}
    assert _daily(readiness=ready)["status"] == status


def test_legacy_plan_permission_is_not_promoted_to_execution_permission():
    plan, ready = _plan(), _ready()
    plan["execution_allowed"] = True  # Legacy plan qualification, not a fill or live execution grant.
    ready.pop("execution_allowed")
    ready["execution_ready"] = False
    record = _daily(plan, ready)
    assert record["plan_permitted"] is True
    assert record["execution_allowed"] is False


def test_retried_legacy_result_does_not_fill_a_subsequent_revision(tmp_path):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    actual = {"quantity": 100, "confirmation_id": "manual-fill-1"}
    filled = journal.append_trade_plan_outcome_once(path, first["plan_id"], "filled", actual=actual)
    changed = _plan()
    changed["priority"]["primary"]["trigger"] = "新条件"
    second = _saved_plan(path, plan=changed)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="ambiguous.*plan_revision_id"):
        journal.append_trade_plan_outcome_once(path, first["plan_id"], "filled", actual=actual)
    assert path.read_bytes() == before
    retry = journal.append_trade_plan_outcome_once(path, first["plan_id"], "filled", actual=actual, plan_revision_id=first["revision_id"])
    assert retry["appended"] is False
    assert retry["revision_id"] == filled["revision_id"]
    item = journal.build_trade_plan_review(path)["items"][0]
    assert item["plan"]["revision_id"] == second["revision_id"]
    assert item["outcome_matches_latest_revision"] is False


def test_id_only_legacy_records_keep_non_return_statistics_in_all_history(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal.append_trade_plan_once(path, {"plan_id": "legacy-manual-1"})
    journal.append_trade_plan_outcome_once(path, "legacy-manual-1", "triggered_not_filled")
    review = journal.build_trade_plan_review(path)
    assert review["plan_count"] == review["outcome_count"] == review["triggered_count"] == 1
    assert review["filled_count"] == 0
    assert journal.build_trade_plan_review(path, report_date="2026-09-03")["plan_count"] == 0


def test_nonfinite_actuals_are_json_null_and_still_idempotent(tmp_path):
    path = tmp_path / "journal.jsonl"
    plan = _saved_plan(path)
    actual = {"net_pnl": float("inf"), "currency": "CNY", "pnl_basis": "realized_after_fees_and_taxes", "source": "manual_confirmation"}
    result = journal.append_trade_plan_outcome_once(path, plan["plan_id"], "filled", actual=actual)
    assert result["actual"]["net_pnl"] is None
    assert "Infinity" not in path.read_text(encoding="utf-8")
    assert journal.append_trade_plan_outcome_once(path, plan["plan_id"], "filled", actual=actual)["appended"] is False
    json.dumps(journal.build_trade_plan_review(path), allow_nan=False)


def test_appending_after_an_unterminated_tail_does_not_lose_either_record(tmp_path):
    path = tmp_path / "journal.jsonl"
    first = _daily(report_date="2026-09-01")
    path.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
    prefix = path.read_bytes()
    journal.append_daily_decision_once(path, _daily(report_date="2026-09-02"))
    assert path.read_bytes().startswith(prefix)
    assert journal.build_daily_decision_review(path)["decision_count"] == 2


def test_empty_authoritative_funnel_cannot_be_replaced_by_a_stale_priority():
    record = _daily(candidate_funnel={"fingerprint": "no-eligible", "eligible_candidates": []})
    assert record["status"] == "no_trade_no_candidate"



@pytest.mark.parametrize("scenario_status", ["awaiting_confirmation", "no_valid_scenario"])
def test_waiting_scenario_with_eligible_funnel_is_not_a_missing_candidate_or_strategy(scenario_status):
    plan, ready = _plan(), _ready()
    plan.update(priority={}, scenario_status=scenario_status, decision_scenario_id=None)
    ready.update(plan_permitted=False)
    ready["action"] = {"status": "wait_confirmation", "reason_code": "scenario_confirmation_pending", "reason": "候选存在，等待有效情景。"}
    record = _daily(plan, ready, candidate_funnel={"fingerprint": "has-eligible", "eligible_candidates": [{"code": "sh600001"}]})
    assert record["status"] == "wait_confirmation"
    assert record["plan_permitted"] is False
    assert record["execution_allowed"] is False
    assert record["candidate_codes"] == ["sh600001"]
    assert journal.build_trade_plan_records(plan, report_date="2026-09-03", readiness=ready) == []


def test_target_trading_date_change_versions_the_plan_snapshot():
    first = _daily(trading_date="2026-09-07")
    second = _daily(trading_date="2026-09-08")
    assert first["plan_version"] != second["plan_version"]



def _confirmed_pnl(value, *, trade_id=None, source="broker_export"):
    actual = {"net_pnl": value, "currency": "CNY", "pnl_basis": "realized_after_fees_and_taxes", "source": source}
    if trade_id is not None:
        actual["trade_id"] = trade_id
    return actual


def _second_revision(path, *, alternate=False):
    plan = _plan()
    plan["priority"]["primary"]["trigger"] = "修订后的触发条件"
    if alternate:
        plan["priority"]["alternates"] = [plan["priority"].pop("primary")]
    return _saved_plan(path, plan=plan)


def test_late_historical_nonfill_does_not_erase_another_revision_fill(tmp_path):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    journal.append_trade_plan_outcome_once(path, first["plan_id"], "unknown")
    second = _second_revision(path)
    journal.append_trade_plan_outcome_once(path, second["plan_id"], "filled", plan_revision_id=second["revision_id"], actual=_confirmed_pnl(10))
    journal.append_trade_plan_outcome_once(path, first["plan_id"], "not_triggered", plan_revision_id=first["revision_id"])
    review = journal.build_trade_plan_review(path)
    assert review["filled_count"] == 1
    assert review["net_pnl"] == 10
    assert review["filled_revision_count"] == 1
    assert review["latest_status_counts"]["filled"] == 1
    item = review["items"][0]
    assert item["latest_outcome"]["plan_revision_id"] == second["revision_id"]
    assert item["latest_outcome_status"] == "filled"
    assert [row["status"] for row in item["revision_outcomes"]] == ["not_triggered", "filled"]


def test_latest_plan_nonfill_remains_separate_from_historical_fill(tmp_path):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    journal.append_trade_plan_outcome_once(path, first["plan_id"], "filled", actual=_confirmed_pnl(10))
    second = _second_revision(path)
    journal.append_trade_plan_outcome_once(path, second["plan_id"], "not_triggered", plan_revision_id=second["revision_id"])
    review = journal.build_trade_plan_review(path)
    assert review["filled_count"] == review["triggered_count"] == 1
    assert review["net_pnl"] == 10
    assert review["latest_status_counts"]["not_triggered"] == 1
    item = review["items"][0]
    assert item["latest_plan_status"] == "conditional"
    assert item["latest_outcome_status"] == "not_triggered"
    assert item["has_historical_fill"] is True
    assert item["historical_fill_outcomes"][0]["plan_revision_id"] == first["revision_id"]
    assert item["outcome_matches_latest_revision"] is False


def test_correction_retracts_only_its_target_revision_fill(tmp_path):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    journal.append_trade_plan_outcome_once(path, first["plan_id"], "filled", actual=_confirmed_pnl(10, trade_id="trade-1"))
    second = _second_revision(path)
    journal.append_trade_plan_outcome_once(path, second["plan_id"], "filled", plan_revision_id=second["revision_id"], actual=_confirmed_pnl(20, trade_id="trade-2"))
    journal.append_trade_plan_outcome_once(path, first["plan_id"], "cancelled", plan_revision_id=first["revision_id"])
    review = journal.build_trade_plan_review(path)
    assert review["filled_count"] == 1
    assert review["filled_revision_count"] == 1
    assert review["net_pnl"] == 20
    assert [row["status"] for row in review["items"][0]["revision_outcomes"]] == ["cancelled", "filled"]


@pytest.mark.parametrize("first_trade_id", [None, "trade-1"])
def test_multiple_filled_revisions_without_trade_ids_do_not_imply_multiple_trades(tmp_path, first_trade_id):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    journal.append_trade_plan_outcome_once(path, first["plan_id"], "filled", actual=_confirmed_pnl(10, trade_id=first_trade_id))
    second = _second_revision(path)
    journal.append_trade_plan_outcome_once(path, second["plan_id"], "filled", plan_revision_id=second["revision_id"], actual=_confirmed_pnl(20))
    review = journal.build_trade_plan_review(path)
    assert review["filled_count"] == 1  # Logical plans with a fill, not an invented trade count.
    assert review["filled_revision_count"] == 2
    assert review["net_pnl"] is None
    assert review["pnl_known_count"] == 0
    assert review["pnl_unknown_count"] == 1
    assert review["pnl_aggregation_status"] == "ambiguous"
    item = review["items"][0]
    assert item["has_historical_fill"] is True
    assert len(item["historical_fill_outcomes"]) == 2
    assert item["pnl_status"] == "ambiguous"
    assert "missing_trade_id_for_multiple_fills" in item["pnl_ambiguity_reasons"]
    assert [row["pnl_status"] for row in item["revision_outcomes"]] == ["known", "known"]


@pytest.mark.parametrize("second_id,second_amount,total,result_count", [
    ("trade-1", 10, 10, 1),
    ("trade-2", 20, 30, 2),
])
def test_explicit_trade_ids_distinguish_deduplication_from_distinct_results(tmp_path, second_id, second_amount, total, result_count):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    journal.append_trade_plan_outcome_once(path, first["plan_id"], "filled", actual=_confirmed_pnl(10, trade_id="trade-1"))
    second = _second_revision(path)
    journal.append_trade_plan_outcome_once(path, second["plan_id"], "filled", plan_revision_id=second["revision_id"], actual=_confirmed_pnl(second_amount, trade_id=second_id))
    review = journal.build_trade_plan_review(path)
    assert review["net_pnl"] == total
    assert review["filled_count"] == 1
    assert review["filled_revision_count"] == 2
    assert review["pnl_known_count"] == 1
    assert review["pnl_known_result_count"] == result_count
    assert review["pnl_by_currency_and_basis"][0]["count"] == result_count
    assert review["pnl_aggregation_status"] == "known"


@pytest.mark.parametrize("second_amount,expected_status,expected_total", [(10, "known", 10), (20, "ambiguous", None)])
def test_trade_id_is_not_double_counted_or_silently_resolved_across_logical_plans(tmp_path, second_amount, expected_status, expected_total):
    path = tmp_path / "journal.jsonl"
    for code, value in [("sh600001", 10), ("sh600002", second_amount)]:
        plan = _saved_plan(path, code=code)
        journal.append_trade_plan_outcome_once(path, plan["plan_id"], "filled", actual=_confirmed_pnl(value, trade_id="account-1:trade-1"))
    review = journal.build_trade_plan_review(path)
    assert review["filled_count"] == 2
    assert review["net_pnl"] == expected_total
    assert review["pnl_aggregation_status"] == expected_status
    if expected_status == "known":
        assert review["pnl_known_result_count"] == 1
    else:
        assert review["pnl_ambiguous_plan_count"] == 2
        assert all("conflicting_trade_id_results" in item["pnl_ambiguity_reasons"] for item in review["items"])


def test_unique_historical_plan_alias_binds_to_that_revision_not_latest(tmp_path):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    journal.append_trade_plan_outcome_once(path, first["plan_id"], "unknown")
    second = _second_revision(path, alternate=True)
    confirmation = journal.append_trade_plan_outcome_once(path, first["plan_id"], "filled", actual=_confirmed_pnl(10))
    assert confirmation["plan_revision_id"] == first["revision_id"]
    assert confirmation["plan_version"] == 1
    item = journal.build_trade_plan_review(path)["items"][0]
    assert item["plan"]["revision_id"] == second["revision_id"]
    assert item["latest_outcome"] is None
    assert item["latest_outcome_status"] == "pending"
    assert item["has_historical_fill"] is True


@pytest.mark.parametrize("address", ["plan_id", "logical_plan_id"])
def test_reused_plan_alias_requires_explicit_revision_without_writing(tmp_path, address):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    _second_revision(path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="ambiguous.*plan_revision_id"):
        journal.append_trade_plan_outcome_once(path, first[address], "filled", actual=_confirmed_pnl(10))
    assert path.read_bytes() == before
    exact = journal.append_trade_plan_outcome_once(path, first[address], "filled", plan_revision_id=first["revision_id"], actual=_confirmed_pnl(10))
    assert exact["plan_revision_id"] == first["revision_id"]


def test_explicit_revision_result_corrections_allow_a_b_a_and_only_retry_latest(tmp_path):
    path = tmp_path / "journal.jsonl"
    plan = _saved_plan(path)
    results = [journal.append_trade_plan_outcome_once(
        path, plan["plan_id"], "filled", plan_revision_id=plan["revision_id"], actual=_confirmed_pnl(amount),
    ) for amount in (10, 20, 10)]
    assert all(result["appended"] is True for result in results)
    assert [result["version"] for result in results] == [1, 2, 3]
    assert results[2]["previous_revision_id"] == results[1]["revision_id"]
    before = path.read_bytes()
    assert journal.append_trade_plan_outcome_once(path, plan["plan_id"], "filled", plan_revision_id=plan["revision_id"], actual=_confirmed_pnl(10))["appended"] is False
    assert path.read_bytes() == before
    assert journal.build_trade_plan_review(path)["net_pnl"] == 10


def test_interleaved_revision_results_compare_only_the_target_revision_content(tmp_path):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    original = journal.append_trade_plan_outcome_once(path, first["plan_id"], "filled", actual=_confirmed_pnl(10, trade_id="trade-1"))
    second = _second_revision(path)
    journal.append_trade_plan_outcome_once(path, second["plan_id"], "filled", plan_revision_id=second["revision_id"], actual=_confirmed_pnl(20, trade_id="trade-2"))
    retry = journal.append_trade_plan_outcome_once(path, first["plan_id"], "filled", plan_revision_id=first["revision_id"], actual=_confirmed_pnl(10, trade_id="trade-1"))
    assert retry["appended"] is False
    correction = journal.append_trade_plan_outcome_once(path, first["plan_id"], "filled", plan_revision_id=first["revision_id"], actual=_confirmed_pnl(30, trade_id="trade-1"))
    assert correction["previous_revision_id"] == original["revision_id"]
    assert correction["change_summary"]["actual.net_pnl"] == {"before": 10, "after": 30}
    assert "plan_revision_id" not in correction["change_summary"]


@pytest.mark.parametrize("source", [None, "", "unknown", " UNKNOWN ", "未知", {}, 123, False])
def test_net_pnl_with_missing_or_unknown_source_is_not_known(tmp_path, source):
    path = tmp_path / "journal.jsonl"
    plan = _saved_plan(path)
    actual = _confirmed_pnl(10, source=source)
    if source is None:
        actual.pop("source")
    journal.append_trade_plan_outcome_once(path, plan["plan_id"], "filled", actual=actual)
    review = journal.build_trade_plan_review(path)
    assert review["filled_count"] == 1
    assert review["pnl_known_count"] == 0
    assert review["pnl_unknown_count"] == 1
    assert review["net_pnl"] is None
    assert review["items"][0]["pnl_status"] == "unknown"


@pytest.mark.parametrize("source", ["broker_export", "manual_confirmation"])
def test_net_pnl_with_explicit_source_and_units_remains_known(tmp_path, source):
    path = tmp_path / "journal.jsonl"
    plan = _saved_plan(path)
    journal.append_trade_plan_outcome_once(path, plan["plan_id"], "filled", actual=_confirmed_pnl(10, source=source))
    review = journal.build_trade_plan_review(path)
    assert review["pnl_known_count"] == 1
    assert review["net_pnl"] == 10


@pytest.mark.parametrize("reason_code,expected", [
    ("no_valid_scenario", "wait_confirmation"),
    ("no_candidates", "no_trade_no_candidate"),
    ("signal_snapshot_unqualified", "wait_confirmation"),
])
def test_zero_position_does_not_override_the_explicit_non_market_reason(reason_code, expected):
    plan, ready = _plan(), _ready()
    plan["position"] = "0 成"
    ready["strategy"]["status"] = "not_applicable"
    ready["plan_permitted"] = False
    ready["action"] = {"status": "no_new_positions", "reason_code": reason_code, "reason": "不是市场防守结论。"}
    record = _daily(plan, ready)
    assert record["status"] == expected
    assert record["reason_code"] == reason_code
    assert record["execution_allowed"] is False


@pytest.mark.parametrize("execution_ready,legacy_ready_flag,plan_flag,expected", [
    (True, False, False, True),
    (False, True, True, False),
    (None, True, True, False),
])
def test_daily_execution_permission_uses_only_production_execution_ready(execution_ready, legacy_ready_flag, plan_flag, expected):
    plan, ready = _plan(), _ready()
    plan["execution_allowed"] = plan_flag
    ready["execution_allowed"] = legacy_ready_flag
    if execution_ready is None:
        ready.pop("execution_ready", None)
    else:
        ready["execution_ready"] = execution_ready
    assert _daily(plan, ready)["execution_allowed"] is expected



def test_explicit_revision_with_a_colliding_alias_stays_on_its_logical_plan(tmp_path):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    other_record = journal.build_trade_plan_records(_plan(), report_date="2026-09-04", readiness=_ready())[0]
    other_record["plan_id"] = first["plan_id"]
    other = journal.append_trade_plan_once(path, other_record)
    with pytest.raises(ValueError, match="ambiguous.*plan_revision_id"):
        journal.append_trade_plan_outcome_once(path, first["plan_id"], "filled", actual=_confirmed_pnl(10))
    result = journal.append_trade_plan_outcome_once(path, first["plan_id"], "filled", plan_revision_id=first["revision_id"], actual=_confirmed_pnl(10))
    assert result["logical_plan_id"] == first["logical_plan_id"]
    items = {item["logical_plan_id"]: item for item in journal.build_trade_plan_review(path)["items"]}
    assert items[first["logical_plan_id"]]["latest_outcome_status"] == "filled"
    assert items[other["logical_plan_id"]]["latest_outcome_status"] == "pending"
    assert items[other["logical_plan_id"]]["has_historical_fill"] is False


def test_ambiguous_legacy_fill_is_retained_without_claiming_latest_revision_filled(tmp_path):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    legacy = {"event_type": "trade_plan_outcome", "schema_version": "trade-plan-outcome/v1",
              "plan_id": first["plan_id"], "status": "filled", "actual": _confirmed_pnl(10)}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(legacy) + "\n")
    prefix = path.read_bytes()
    _second_revision(path)
    review = journal.build_trade_plan_review(path)
    assert review["filled_count"] == 1
    assert review["latest_pending_count"] == 1
    assert review["unbound_filled_outcome_count"] == 1
    assert review["net_pnl"] is None
    assert review["pnl_aggregation_status"] == "ambiguous"
    item = review["items"][0]
    assert item["latest_outcome"] is None
    assert item["has_historical_fill"] is True
    assert "unbound_filled_result" in item["pnl_ambiguity_reasons"]
    assert path.read_bytes().startswith(prefix)


def test_explicit_correction_can_resolve_conflicting_trade_id_observations(tmp_path):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    journal.append_trade_plan_outcome_once(path, first["plan_id"], "filled", actual=_confirmed_pnl(10, trade_id="trade-1"))
    second = _second_revision(path)
    journal.append_trade_plan_outcome_once(path, second["plan_id"], "filled", plan_revision_id=second["revision_id"], actual=_confirmed_pnl(20, trade_id="trade-1"))
    assert journal.build_trade_plan_review(path)["pnl_aggregation_status"] == "ambiguous"
    journal.append_trade_plan_outcome_once(path, second["plan_id"], "filled", plan_revision_id=second["revision_id"], actual=_confirmed_pnl(10, trade_id="trade-1"))
    review = journal.build_trade_plan_review(path)
    assert review["pnl_aggregation_status"] == "known"
    assert review["net_pnl"] == 10
    assert review["filled_revision_count"] == 2
    assert review["pnl_known_result_count"] == 1
    assert review["outcome_revision_count"] == 3



@pytest.mark.parametrize("b_amount,expected_subtotal,expected_known_count", [(20, 30, 1), (10, 40, 2)])
def test_ambiguous_plan_keeps_its_known_trade_id_in_conflict_evidence(tmp_path, b_amount, expected_subtotal, expected_known_count):
    path = tmp_path / "journal.jsonl"
    a1 = _saved_plan(path)
    journal.append_trade_plan_outcome_once(path, a1["plan_id"], "filled", actual=_confirmed_pnl(10, trade_id="T"))
    a2 = _second_revision(path)
    journal.append_trade_plan_outcome_once(path, a2["plan_id"], "filled", plan_revision_id=a2["revision_id"], actual=_confirmed_pnl(5))
    b = _saved_plan(path, code="sh600002")
    journal.append_trade_plan_outcome_once(path, b["plan_id"], "filled", actual=_confirmed_pnl(b_amount, trade_id="T"))
    c = _saved_plan(path, code="sh600003")
    journal.append_trade_plan_outcome_once(path, c["plan_id"], "filled", actual=_confirmed_pnl(30, trade_id="U"))
    review = journal.build_trade_plan_review(path)
    assert review["pnl_by_currency_and_basis"] == [{
        "currency": "CNY", "pnl_basis": "realized_after_fees_and_taxes",
        "count": expected_known_count, "net_pnl": expected_subtotal,
    }]
    assert review["pnl_known_result_count"] == expected_known_count
    assert review["net_pnl"] is None
    assert review["pnl_aggregation_status"] == "ambiguous"
    assert review["filled_count"] == 3
    items = {item["logical_plan_id"]: item for item in review["items"]}
    assert "missing_trade_id_for_multiple_fills" in items[a1["logical_plan_id"]]["pnl_ambiguity_reasons"]
    if b_amount == 20:
        assert "conflicting_trade_id_results" in items[b["logical_plan_id"]]["pnl_ambiguity_reasons"]
        assert items[b["logical_plan_id"]]["pnl_status"] == "ambiguous"


def test_trade_evidence_owned_only_by_an_ambiguous_plan_is_not_a_known_subtotal(tmp_path):
    path = tmp_path / "journal.jsonl"
    first = _saved_plan(path)
    journal.append_trade_plan_outcome_once(path, first["plan_id"], "filled", actual=_confirmed_pnl(10, trade_id="T"))
    second = _second_revision(path)
    journal.append_trade_plan_outcome_once(path, second["plan_id"], "filled", plan_revision_id=second["revision_id"], actual=_confirmed_pnl(5))
    c = _saved_plan(path, code="sh600003")
    journal.append_trade_plan_outcome_once(path, c["plan_id"], "filled", actual=_confirmed_pnl(30, trade_id="U"))
    review = journal.build_trade_plan_review(path)
    assert review["pnl_by_currency_and_basis"][0]["net_pnl"] == 30
    assert review["pnl_known_result_count"] == 1
    assert review["items"][0]["pnl_by_currency_and_basis"] == []


def test_outcome_recorded_before_its_plan_is_retained_then_safely_reprojected(tmp_path):
    path = tmp_path / "journal.jsonl"
    record = journal.build_trade_plan_records(_plan(), report_date="2026-09-03", readiness=_ready())[0]
    first = journal.append_trade_plan_outcome_once(path, record["plan_id"], "filled", actual=_confirmed_pnl(10))
    assert first["logical_plan_id"] == record["plan_id"]
    assert journal.append_trade_plan_outcome_once(path, record["plan_id"], "filled", actual=_confirmed_pnl(10))["appended"] is False
    prefix = path.read_bytes()
    before = journal.build_trade_plan_review(path)
    assert before["unbound_outcome_count"] == 1
    assert before["unassigned_outcome_count"] == before["unassigned_filled_outcome_count"] == 1
    assert before["plan_count"] == before["filled_count"] == 0
    assert before["items"] == []
    assert before["net_pnl"] is None
    assert before["pnl_aggregation_status"] == "ambiguous"
    assert before["unassigned_outcomes"][0]["unassigned_reason"] == "no_matching_plan"
    saved = journal.append_trade_plan_once(path, record)
    after = journal.build_trade_plan_review(path)
    assert after["plan_count"] == after["outcome_count"] == after["filled_count"] == 1
    assert after["unassigned_outcome_count"] == after["unbound_outcome_count"] == 0
    assert after["net_pnl"] == 10
    assert after["items"][0]["latest_outcome"]["plan_revision_id"] == saved["revision_id"]
    assert after["items"][0]["latest_outcome"]["revision_id"] == first["revision_id"]
    assert path.read_bytes().startswith(prefix)
    complete = path.read_bytes()
    assert journal.append_trade_plan_outcome_once(path, record["plan_id"], "filled", actual=_confirmed_pnl(10))["appended"] is False
    assert path.read_bytes() == complete


def test_legacy_result_with_cross_logical_alias_ambiguity_is_top_level_unassigned(tmp_path):
    path = tmp_path / "journal.jsonl"
    alias = "shared-legacy-alias"
    legacy = {"event_type": "trade_plan_outcome", "schema_version": "trade-plan-outcome/v1",
              "plan_id": alias, "status": "filled", "actual": _confirmed_pnl(10)}
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    prefix = path.read_bytes()
    for code in ("sh600001", "sh600002"):
        plan = _plan()
        plan["priority"]["primary"]["code"] = code
        record = journal.build_trade_plan_records(plan, report_date="2026-09-03", readiness=_ready())[0]
        record["plan_id"] = alias
        journal.append_trade_plan_once(path, record)
    review = journal.build_trade_plan_review(path)
    assert review["unbound_outcome_count"] == 1
    assert review["unassigned_outcome_count"] == review["unassigned_filled_outcome_count"] == 1
    assert review["unassigned_outcomes"][0]["unassigned_reason"] == "ambiguous_plan_reference"
    assert review["unassigned_outcomes"][0]["actual"]["net_pnl"] == 10
    assert review["plan_count"] == 2
    assert review["filled_count"] == review["outcome_count"] == 0
    assert review["latest_pending_count"] == 2
    assert all(not item["has_historical_fill"] for item in review["items"])
    assert review["net_pnl"] is None
    assert review["pnl_aggregation_status"] == "ambiguous"
    assert path.read_bytes().startswith(prefix)


@pytest.mark.parametrize("filters", [
    {}, {"report_date": "2026-09-03"},
    {"from_report_date": "2030-01-01", "through_report_date": "2030-01-31"},
])
def test_unassigned_results_have_unknown_plan_date_and_remain_visible_with_filters(tmp_path, filters):
    path = tmp_path / "journal.jsonl"
    journal.append_trade_plan_outcome_once(path, "2026-09-03:sh600001:primary:v1", "filled", actual=_confirmed_pnl(10))
    review = journal.build_trade_plan_review(path, **filters)
    assert review["unbound_outcome_count"] == 1
    assert review["unassigned_outcome_count"] == 1
    assert review["unassigned_date_scope"] == "unknown_plan_report_date"
    assert review["plan_count"] == 0
    assert review["net_pnl"] is None
    assert "未分配" in review["note"] and "日期" in review["note"]


def test_a_known_out_of_range_plan_is_not_reclassified_as_unassigned(tmp_path):
    path = tmp_path / "journal.jsonl"
    plan = _saved_plan(path, report_date="2026-09-08")
    journal.append_trade_plan_outcome_once(path, plan["plan_id"], "filled", actual=_confirmed_pnl(10))
    review = journal.build_trade_plan_review(path, through_report_date="2026-09-03")
    assert review["plan_count"] == 0
    assert review["unbound_outcome_count"] == review["unassigned_outcome_count"] == 0
    assert review["unassigned_outcomes"] == []


def test_unassigned_known_trade_id_conflict_cannot_enter_the_known_subtotal(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal.append_trade_plan_outcome_once(path, "missing-plan", "filled", actual=_confirmed_pnl(10, trade_id="T"))
    for code, value, trade_id in [("sh600002", 20, "T"), ("sh600003", 30, "U")]:
        plan = _saved_plan(path, code=code)
        journal.append_trade_plan_outcome_once(path, plan["plan_id"], "filled", actual=_confirmed_pnl(value, trade_id=trade_id))
    review = journal.build_trade_plan_review(path)
    assert review["pnl_by_currency_and_basis"][0]["net_pnl"] == 30
    assert review["pnl_known_result_count"] == 1
    assert review["unassigned_outcome_count"] == 1
    assert review["net_pnl"] is None
    assert "unassigned_filled_result" in review["pnl_ambiguity_reasons"]


def test_unknown_recorded_logical_identity_is_not_guessed_from_an_unrelated_alias(tmp_path):
    path = tmp_path / "journal.jsonl"
    plan = _saved_plan(path)
    event = {"event_type": "trade_plan_outcome", "plan_id": plan["plan_id"],
             "logical_plan_id": "explicit-but-not-registered", "status": "filled", "actual": _confirmed_pnl(10)}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")
    review = journal.build_trade_plan_review(path)
    assert review["unbound_outcome_count"] == 1
    assert review["unassigned_outcome_count"] == 1
    assert review["unassigned_outcomes"][0]["unassigned_reason"] == "unknown_logical_plan"
    assert review["items"][0]["latest_outcome"] is None
    assert review["filled_count"] == 0
