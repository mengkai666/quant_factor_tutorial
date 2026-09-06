from bs4 import BeautifulSoup


def test_both_reports_separate_rank_from_active_and_show_pending_stages():
    from decision_dashboard import generate_dashboard_html, generate_dashboard_section
    from test_recap_scenario_closure import _confirmed_context
    ctx = _confirmed_context()
    for render in (generate_dashboard_html, generate_dashboard_section):
        html = render(ctx)
        soup = BeautifulSoup(html, "html.parser")
        panel = soup.select_one(".scenario-checkpoint")
        assert panel is not None
        text = panel.get_text(" ", strip=True)
        assert "最高排名" in text and "非操作结论" in text
        assert "有效情景" in text and "规则确认" in text
        assert "2026-09-04" in text and "10:00" in text
        assert panel.select('[data-phase="confirm_1000"]')[0]["data-state"] == "pending"


def test_empty_trade_plan_day_still_has_daily_journal_panel(tmp_path):
    from decision_dashboard import build_today_decision, generate_dashboard_html
    from report_closure import persist_decision_review
    ctx = {"date_str": "2026-09-03", "publication_mode": "facts_only"}
    review = persist_decision_review(tmp_path / "history.jsonl", build_today_decision(ctx))
    ctx.update(review)
    panel = BeautifulSoup(generate_dashboard_html(ctx), "html.parser").select_one(".daily-decision-journal")
    assert panel is not None
    assert "行情不足" in panel.get_text()
    assert "2026-09-03" in panel.get_text()


def test_event_panel_explains_scope_and_unknown_values_not_market_zero():
    from limit_events import build_limit_event_snapshot
    from recap_panels import render_limit_event_coverage
    snapshot = build_limit_event_snapshot([{ "code": "sz000001", "date": "2026-09-04", "broken": False,
        "break_count": 0, "source": "fixture", "source_timestamp": "2026-09-04T15:05:00+08:00",
        "source_timestamp_kind": "fetched_at"}], trade_date="2026-09-04")
    soup = BeautifulSoup(render_limit_event_coverage(snapshot), "html.parser")
    text = soup.get_text(" ", strip=True)
    assert "收盘涨停样本" in text and "不是全市场炸板率" in text
    assert "抓取时间" in text and "未提供" in text
    assert soup.select_one("details") is not None


def test_nonselected_candidates_are_collapsed_and_not_written_as_buy_actions():
    from decision_dashboard import generate_dashboard_html
    from test_recap_scenario_closure import _confirmed_context
    soup = BeautifulSoup(generate_dashboard_html(_confirmed_context()), "html.parser")
    visible = soup.select(".action-plan .candidate-card")
    assert len(visible) == 1
    assert "Z主线" in visible[0].get_text()
    audit = soup.select_one(".candidate-audit")
    assert audit is not None and not audit.has_attr("open")
    assert "其他题材" in audit.get_text()


def test_new_panels_escape_untrusted_content():
    from recap_panels import render_decision_changes
    html = render_decision_changes({"has_previous": True, "previous_report_date": "2026-09-02", "changes": [
        {"field": "reason", "label": "原因", "before": "待确认", "after": "<script>bad()</script>"}]})
    assert BeautifulSoup(html, "html.parser").find("script") is None
    assert "&lt;script&gt;" in html


def test_blocked_plan_does_not_show_positive_position_as_current_permission():
    from decision_dashboard import build_today_decision, build_today_focus_rows, _action_plan_html
    from test_recap_scenario_closure import _confirmed_context
    ctx = _confirmed_context()
    ctx.update(publication_mode="observation", data_quality={"status": "degraded", "publication_mode": "observation"})
    decision = build_today_decision(ctx)
    text = BeautifulSoup(_action_plan_html(decision["action_plan"]), "html.parser").get_text(" ", strip=True)
    assert "未授权" in text
    assert "参考区间" in text
    assert "计划仓位 2-4 成" not in text
    assert all(row["建议仓位"] == "不新增仓位" for row in build_today_focus_rows(ctx))
    assert all(row["模型参考仓位"] for row in build_today_focus_rows(ctx))


def test_latest_plan_result_is_not_replaced_by_old_filled_summary():
    from recap_panels import render_trade_history
    review = {"plan_count": 1, "items": [{"report_date": "2026-09-03", "plan": {"name": "测试", "plan_status": "invalidated", "plan_version": 2},
        "outcome": {"status": "filled"}, "latest_outcome": None, "latest_outcome_status": "unknown",
        "historical_fill_outcomes": [{"status": "filled"}], "has_historical_fill": True, "outcome_matches_latest_revision": False}],
        "pnl_by_currency_and_basis": [{"net_pnl": 10, "currency": "CNY", "pnl_basis": "realized"}], "pnl_aggregation_status": "ambiguous"}
    text = BeautifulSoup(render_trade_history({"trade_plan_review": review}), "html.parser").get_text(" ", strip=True)
    assert "最新结果" in text and "未知" in text
    assert "历史成交" in text and "仅已知部分" in text


def test_no_candidate_conclusion_cannot_coexist_with_independent_buy_playbook():
    from decision_dashboard import generate_dashboard_html, generate_dashboard_section, build_today_decision
    from test_recap_scenario_closure import _confirmed_context
    ctx = _confirmed_context(groups=[])
    ctx.update(curr_h=6, h5=1, zt=80, breadth_ratio=.7)
    assert build_today_decision(ctx)["readiness"]["action"]["reason_code"] == "no_candidates"
    for render in (generate_dashboard_html, generate_dashboard_section):
        html = render(ctx)
        assert "今日操作口令" not in html
        assert "可放胆做题材" not in html


def test_no_active_plan_shows_recovery_path_even_when_original_ceiling_is_zero():
    from recap_panels import render_scenario_checkpoint
    ctx = {"scenario_plans": [
        {"scenario_id": "risk_off_observation", "title": "防守", "position_ceiling": 0},
        {"scenario_id": "repair_confirmation", "title": "修复再评估", "position_ceiling": 0,
         "trigger_rules": {"auction": [{"metric": "breadth_ratio", "operator": "gte", "value": .5}]}}],
        "scenario_posterior": {"timeline": [{"phase": "close", "top_ranked_scenario_id": "risk_off_observation"}]}}
    ready = {"signal": {"status": "not_evaluable"}, "phase_confirmation": {"observed_phases": ["close"]}}
    text = BeautifulSoup(render_scenario_checkpoint(ctx, ready), "html.parser").get_text(" ", strip=True)
    assert "最高排名（非操作结论）：防守" in text
    assert "待验证路径（不代表已生效）：修复再评估" in text


def test_unassigned_results_stay_visible_without_inventing_a_plan():
    from recap_panels import render_trade_history
    text = BeautifulSoup(render_trade_history({"trade_plan_review": {"plan_count": 0,
        "unassigned_outcome_count": 1, "unassigned_outcomes": [{"plan_id": "unbound", "status": "filled"}],
        "pnl_aggregation_status": "ambiguous"}}), "html.parser").get_text(" ", strip=True)
    assert "未归属" in text
    assert "unbound" in text
