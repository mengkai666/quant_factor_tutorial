import copy

import pytest
from bs4 import BeautifulSoup


def _ready_inputs():
    core = ("universe", "price_raw", "breadth", "limit_pool")
    return {
        "quality": {
            "status": "ok", "publication_mode": "decision",
            "modules": {name: {"status": "ok", "critical": True} for name in core},
            "critical_blocked": [], "decision_degraded": [],
        },
        "market_state": {
            "publication_mode": "decision",
            "statistics_layer": {"status": "ok"},
            "decision_layer": {"status": "ready"},
        },
        "action_plan": {
            "publication_mode": "decision", "execution_allowed": True,
            "position": "2 成", "active_scenario_id": "mainline_continuation",
            "groups": [{"code": "attack", "rows": [{
                "name": "测试候选", "code": "sz000001", "role": "attack",
                "sector": "AI算力", "execution_allowed": True,
                "action": "条件确认后执行", "trigger": "按结构化规则确认", "invalid": "规则失效",
            }]}],
        },
        "scenario_posterior": {"timeline": [{
            "phase": "early_0935", "top_scenario_id": "mainline_continuation",
            "scenarios": [{"scenario_id": "mainline_continuation", "state": "supported"}],
        }]},
        "report_date": "2026-09-03",
    }


def _assess(inputs):
    from decision_readiness import build_decision_readiness
    return build_decision_readiness(**inputs)


def test_confirmed_plan_requires_data_strategy_and_signal_to_be_ready():
    got = _assess(_ready_inputs())
    assert [got[key]["status"] for key in ("data", "strategy", "signal", "action")] == [
        "ready", "applicable", "met", "enter_plan",
    ]
    assert got["execution_ready"] is True
    assert got["signal"]["phase"] == "early_0935"


def test_ai_failure_does_not_mean_core_quotes_are_missing_or_release_the_gate():
    inputs = _ready_inputs()
    inputs["quality"].update(status="degraded", publication_mode="observation", decision_degraded=["ai"])
    inputs["quality"]["modules"]["ai"] = {
        "status": "unavailable", "critical": False, "missing_fields": ["ai_judgement"],
        "errors": ["HTTP 503"],
    }
    before = copy.deepcopy(inputs)
    got = _assess(inputs)
    assert got["data"]["status"] == "ready"
    assert got["strategy"]["status"] == "unverified"
    assert got["action"]["status"] == "no_new_positions"
    assert got["execution_ready"] is False
    issue = next(row for row in got["issues"] if row["module"] == "ai")
    assert issue["scope"] == "narrative"
    assert "AI" in issue["recheck"]
    assert inputs == before


def test_missing_bomb_data_is_a_strategy_dependency_not_missing_all_market_data():
    inputs = _ready_inputs()
    inputs["quality"].update(status="degraded", publication_mode="observation", decision_degraded=["bomb_metrics"])
    inputs["quality"]["modules"]["bomb_metrics"] = {
        "status": "unavailable", "critical": False,
        "missing_fields": ["bomb_rate", "reclose_rate", "board_structure"],
    }
    got = _assess(inputs)
    assert got["data"]["status"] == "ready"
    assert got["strategy"]["status"] == "unverified"
    assert got["action"]["reason_code"] == "qualification_incomplete"
    issue = next(row for row in got["issues"] if row["module"] == "bomb_metrics")
    assert issue["scope"] == "strategy_data"
    assert "封板尝试" in issue["recheck"]
    assert "回封" in issue["recheck"]


def test_missing_statistics_is_not_reported_as_missing_quotes():
    inputs = _ready_inputs()
    inputs["market_state"]["statistics_layer"] = {"status": "unverified", "sample_size": 0}
    got = _assess(inputs)
    assert got["data"]["status"] == "ready"
    assert got["strategy"]["status"] == "unverified"
    assert any(row["scope"] == "validation" for row in got["issues"])
    assert got["execution_ready"] is False


@pytest.mark.parametrize("status", ["unavailable", "unknown", "blocked", "degraded"])
def test_bad_core_data_cannot_be_overridden_by_a_positive_plan(status):
    inputs = _ready_inputs()
    inputs["quality"]["modules"]["price_raw"]["status"] = status
    got = _assess(inputs)
    assert got["data"]["status"] == "missing"
    assert got["signal"]["status"] == "not_evaluable"
    assert got["action"]["reason_code"] == "data_unavailable"
    assert got["execution_ready"] is False


def test_expired_data_is_distinct_from_missing_data():
    inputs = _ready_inputs()
    inputs["quality"]["used_stale"] = True
    got = _assess(inputs)
    assert got["data"]["status"] == "expired"
    assert got["action"]["reason_code"] == "data_expired"
    assert got["execution_ready"] is False


def test_zero_position_with_valid_data_means_decided_not_to_trade():
    inputs = _ready_inputs()
    inputs["action_plan"].update(position="0 成", execution_allowed=False)
    got = _assess(inputs)
    assert got["data"]["status"] == "ready"
    assert got["strategy"]["status"] == "not_applicable"
    assert got["action"]["reason_code"] == "market_no_trade"
    assert "仓位" in got["action"]["reason"]
    assert got["execution_ready"] is False


@pytest.mark.parametrize("state, signal_status", [("neutral", "not_triggered"), ("unknown", "not_evaluable")])
def test_permitted_plan_without_confirmation_waits_instead_of_claiming_it_can_execute(state, signal_status):
    inputs = _ready_inputs()
    inputs["scenario_posterior"]["timeline"][0]["scenarios"][0]["state"] = state
    got = _assess(inputs)
    assert got["strategy"]["status"] == "applicable"
    assert got["signal"]["status"] == signal_status
    assert got["action"]["status"] == "wait_confirmation"
    assert got["plan_permitted"] is True
    assert got["execution_ready"] is False


def test_invalidated_signal_never_releases_a_permitted_plan():
    inputs = _ready_inputs()
    inputs["scenario_posterior"]["timeline"][0]["scenarios"][0]["state"] = "invalidated"
    got = _assess(inputs)
    assert got["signal"]["status"] == "invalidated"
    assert got["action"]["reason_code"] == "signal_invalidated"
    assert got["execution_ready"] is False


def test_other_scenarios_confirmation_is_not_used_for_the_active_plan():
    inputs = _ready_inputs()
    inputs["action_plan"]["active_scenario_id"] = "different_scenario"
    got = _assess(inputs)
    assert got["signal"]["status"] == "not_evaluable"
    assert got["execution_ready"] is False


def test_risk_anchors_alone_are_not_a_tradeable_candidate_set():
    inputs = _ready_inputs()
    inputs["action_plan"]["groups"][0].update(code="risk")
    inputs["action_plan"]["groups"][0]["rows"][0].update(role="risk", execution_allowed=False)
    got = _assess(inputs)
    assert got["strategy"]["status"] == "not_applicable"
    assert got["action"]["reason_code"] == "no_candidates"
    assert got["execution_ready"] is False


def test_empty_context_fails_closed_without_inventing_a_market_conclusion():
    from decision_readiness import build_decision_readiness
    got = build_decision_readiness()
    assert got["data"]["status"] == "missing"
    assert got["strategy"]["status"] == "unverified"
    assert got["action"]["reason_code"] == "data_unavailable"
    assert got["execution_ready"] is False


def _dashboard_context(inputs):
    from decision_dashboard import build_dashboard_ctx
    return build_dashboard_ctx(
        advance_decline={"up": 3500, "down": 1500, "zt": 60, "dt": 2, "zt_max_height": 3},
        echelon=[{"height": "2连板", "stock_details": [{"name": "测试候选", "code": "sz000001", "ml": "AI算力"}]}],
        report_date=inputs["report_date"],
        report_context={
            "report_date": inputs["report_date"],
            "publication_mode": inputs["quality"]["publication_mode"],
            "quality": inputs["quality"],
            "facts": {"market_state": inputs["market_state"]},
            "scenario_posterior": inputs["scenario_posterior"],
        },
    )


def test_main_dashboard_and_embedded_dashboard_show_the_same_four_axes():
    from decision_dashboard import generate_dashboard_html, generate_dashboard_section
    inputs = _ready_inputs()
    inputs["quality"].update(status="degraded", publication_mode="observation", decision_degraded=["bomb_metrics"])
    inputs["quality"]["modules"]["bomb_metrics"] = {"status": "unavailable", "critical": False}
    ctx = _dashboard_context(inputs)
    for html in (generate_dashboard_html(ctx), generate_dashboard_section(ctx)):
        panel = BeautifulSoup(html, "html.parser").select_one(".decision-readiness")
        assert panel is not None
        assert panel["data-action-status"] == "no_new_positions"
        assert panel.select_one('[data-axis="data"]')["data-status"] == "ready"
        assert panel.select_one('[data-axis="strategy"]')["data-status"] == "unverified"
        assert "重新评估" in panel.get_text()


def test_facts_only_still_explains_missing_data_without_showing_a_stock_plan():
    from decision_dashboard import generate_dashboard_html, generate_dashboard_section
    inputs = _ready_inputs()
    inputs["quality"].update(status="blocked", publication_mode="facts_only", critical_blocked=["price_raw"])
    inputs["quality"]["modules"]["price_raw"]["status"] = "unavailable"
    inputs["market_state"]["publication_mode"] = "facts_only"
    for html in (generate_dashboard_html(_dashboard_context(inputs)), generate_dashboard_section(_dashboard_context(inputs))):
        soup = BeautifulSoup(html, "html.parser")
        assert soup.select_one('.decision-readiness [data-axis="data"]')["data-status"] == "missing"
        assert "明日执行计划" not in soup.get_text()


def test_csv_cannot_call_an_unconfirmed_conditional_plan_executable(tmp_path):
    import csv
    from decision_dashboard import write_today_focus_pool
    inputs = _ready_inputs()
    inputs["scenario_posterior"]["timeline"][0]["scenarios"][0]["state"] = "neutral"
    ctx = _dashboard_context(inputs)
    target = tmp_path / "focus.csv"
    write_today_focus_pool(ctx, target, action_plan=inputs["action_plan"])
    rows = list(csv.DictReader(target.open(encoding="utf-8-sig")))
    assert rows[0]["可执行"] == "否"
    assert rows[0]["条件计划许可"] == "是"
    assert rows[0]["信号状态"] == "未触发"
    assert rows[0]["操作结论"] == "等待确认"


def test_readiness_renderer_escapes_untrusted_issue_details():
    from decision_readiness import render_decision_readiness
    got = _assess(_ready_inputs())
    got["action"]["reason"] = '<img src=x onerror="alert(1)">'
    html = render_decision_readiness(got)
    assert BeautifulSoup(html, "html.parser").find("img") is None
    assert "&lt;img" in html


def test_process_metrics_report_unprovided_fields_instead_of_confusing_them_with_zero_events():
    from report_logic import compute_ladder_metrics
    from data_sources.quality_gate import apply_review_readiness_gates
    metrics = compute_ladder_metrics([{"code": "sz000001", "height": 2, "name": "测试股"}])
    coverage = metrics["event_input_coverage"]
    assert coverage["source_rows"] == 1
    assert set(coverage["missing_fields"]) == {"limit_up_attempted", "broken", "reclosed", "board_type"}
    quality = apply_review_readiness_gates(
        _ready_inputs()["quality"], daily_delta={"available": True},
        ladder_metrics=metrics, ai_result={"status": "ok"},
    )
    assert quality["publication_mode"] == "observation"
    diagnostic = quality["review_readiness"]["bomb_metrics"]
    assert diagnostic["unavailable_reason"] == "fields_not_provided"
    assert "封板尝试" in diagnostic["reason"]
    assert quality["modules"]["bomb_metrics"]["lineage"]["input_coverage"]["source_rows"] == 1


def test_false_event_flags_are_observed_zero_not_missing_fields():
    from report_logic import compute_ladder_metrics
    metrics = compute_ladder_metrics([{
        "code": "sz000001", "height": 2, "limit_up_attempted": True,
        "broken": False, "reclosed": False, "board_type": "turnover",
    }])
    coverage = metrics["event_input_coverage"]
    assert coverage["missing_fields"] == []
    assert coverage["observed"]["broken"] == 1
    assert coverage["observed"]["reclosed"] == 1
    assert metrics["bomb_rate"]["rate"] == 0

def test_close_snapshot_is_a_post_close_plan_with_intraday_phases_pending():
    from decision_readiness import build_decision_readiness

    inputs = _ready_inputs()
    inputs["scenario_posterior"]["timeline"][0]["phase"] = "close"
    got = build_decision_readiness(**inputs)

    assert got["phase_confirmation"]["status"] == "post_close_plan"
    assert got["phase_confirmation"]["observed_phases"] == ["close"]
    assert "auction" in got["phase_confirmation"]["pending_phases"]
    assert got["action"]["status"] == "wait_confirmation"
    assert got["execution_ready"] is False
    assert "盘后" in got["action"]["reason"]


def test_intraday_phase_can_confirm_a_permitted_plan():
    from decision_readiness import build_decision_readiness

    inputs = _ready_inputs()
    inputs["scenario_posterior"]["timeline"][0]["phase"] = "early_0935"
    got = build_decision_readiness(**inputs)

    assert got["phase_confirmation"]["status"] == "intraday_observed"
    assert got["phase_confirmation"]["observed_phases"] == ["early_0935"]
    assert got["phase_confirmation"]["pending_phases"] == ["auction", "confirm_1000", "afternoon"]
    assert got["action"]["status"] == "enter_plan"
    assert got["execution_ready"] is True
