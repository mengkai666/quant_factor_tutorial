# -*- coding: utf-8 -*-
"""报表层纯逻辑契约测试。"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))


def test_observation_policy_allows_bounded_actions_without_probabilities_or_legacy_pool():
    from report_logic import ReportPolicy, scan_forbidden_semantics

    policy = ReportPolicy.from_mode("observation")

    assert policy.allow_positions is True
    assert policy.allow_actions is True
    assert policy.allow_probabilities is False
    assert policy.allow_focus_pool is False
    assert scan_forbidden_semantics("建议仓位 2-4 成，回封买入", policy) == []
    assert "概率" in scan_forbidden_semantics("上涨概率 70%", policy)


def test_market_ratios_keep_breadth_and_advance_decline_distinct():
    from report_logic import compute_market_ratios
    got = compute_market_ratios(2000, 3000)
    assert got["breadth_ratio"] == 0.4
    assert round(got["advance_decline_ratio"], 3) == 0.667
    assert got["up"] == 2000 and got["down"] == 3000


def test_data_quality_exposes_source_coverage_and_fallback():
    from report_logic import assess_data_quality
    got = assess_data_quality(
        report_date="2026-08-06", trade_day=True, market_total=5000,
        market_covered=4920, primary_source="eastmoney",
        fallback_source="akshare", used_fallback=True,
        used_stale=False, missing_fields=["turnover"],
    )
    assert got["coverage_pct"] == 98.4
    assert got["primary_source"] == "eastmoney"
    assert got["fallback_source"] == "akshare"
    assert got["used_fallback"] is True
    assert got["status"] in {"partial", "degraded"}
    assert "turnover" in got["missing_fields"]


def test_data_credibility_summary_caps_display_coverage_but_preserves_overflow():
    from report_logic import build_data_credibility_summary

    got = build_data_credibility_summary({
        "market_scope": "沪深北全A",
        "market_total": 2467,
        "market_covered": 2467,
        "status": "blocked",
        "modules": {
            "price_raw": {
                "total": 2467,
                "covered": 5190,
                "status": "ok",
            }
        },
    })

    module = got["modules"]["price_raw"]
    assert module["covered"] == 2467
    assert module["raw_covered"] == 5190
    assert module["coverage_pct"] == 100.0
    assert module["raw_coverage_pct"] > 100.0
    assert module["status"] == "blocked"
    assert any("COVERAGE_OVERFLOW" in reason for reason in got["reasons"])


def test_data_credibility_summary_derives_status_when_module_status_is_missing():
    from report_logic import build_data_credibility_summary

    got = build_data_credibility_summary({
        "market_total": 3,
        "modules": {
            "price_raw": {"total": 3, "covered": 3},
            "price_qfq": {"total": 3, "covered": 0},
        },
    })

    assert got["modules"]["price_raw"]["status"] == "ok"
    assert got["modules"]["price_qfq"]["status"] == "unavailable"


def test_date_only_source_timestamp_matching_report_date_is_fresh_for_eod_report():
    from report_logic import assess_data_quality

    got = assess_data_quality(
        report_date="2026-08-06",
        trade_day=True,
        market_total=5538,
        market_covered=5538,
        primary_source="eod_snapshot",
        source_timestamp="2026-08-06",
        report_generated_at="2026-08-07T01:45:00+08:00",
    )

    assert got["freshness_level"] == "fresh"
    assert got["source_age_minutes"] == 0.0
    assert "报告日期一致" in got["freshness_reason"]


def test_scenario_model_weights_are_dynamic_but_not_published_without_history():
    from report_logic import build_scenario_probabilities
    weak = build_scenario_probabilities(
        scene="C_高位分歧", ad_ratio=0.35, zt=42, dt=18,
        curr_h=6, pressure_5d=7, ladder=4, h5=0,
    )
    strong = build_scenario_probabilities(
        scene="E_主升加速", ad_ratio=0.78, zt=140, dt=2,
        curr_h=8, pressure_5d=6, ladder=18, h5=3,
    )
    assert all(item["probability"] is None for item in weak + strong)
    assert round(sum(item["model_weight"] for item in weak), 6) == 1.0
    assert round(sum(item["model_weight"] for item in strong), 6) == 1.0
    assert weak[0]["model_weight"] != strong[0]["model_weight"]
    assert max(weak, key=lambda x: x["model_weight"])["code"] != max(
        strong, key=lambda x: x["model_weight"]
    )["code"]


def test_normalize_catalyst_never_leaks_python_none():
    from report_logic import normalize_catalyst
    assert normalize_catalyst(None) == {"tag": "无近期催化", "text": "", "url": ""}
    got = normalize_catalyst({"tag": None, "text": None, "url": None})
    assert "None" not in json.dumps(got, ensure_ascii=False)
    assert got["tag"] == "无近期催化"


def test_ladder_metrics_expose_progression_and_gap():
    from report_logic import compute_ladder_metrics
    got = compute_ladder_metrics([
        {"height": "6连板"}, {"height": "4连板"}, {"height": "3连板"}
    ])
    assert got["height"] == 6
    assert got["ladder"] == 7
    assert got["gap_heights"] == [5]
    assert got["gap_risk"] is True


def test_ladder_metrics_separates_missing_suspended_and_limit_down_transitions():
    from report_logic import compute_ladder_metrics

    got = compute_ladder_metrics(
        [
            {"code": "sh600001", "height": 3},
            {"code": "sh600002", "height": 2},
            {"code": "sh600003", "height": 0, "status": "limit_down"},
            {"code": "sh600004", "height": 0, "status": "suspended"},
        ],
        previous_echelon=[
            {"code": "sh600001", "height": 2},
            {"code": "sh600002", "height": 3},
            {"code": "sh600003", "height": 2},
            {"code": "sh600004", "height": 2},
            {"code": "sh600005", "height": 2},
        ],
    )

    assert got["transition_status_counts"] == {
        "promoted": 1,
        "continued": 0,
        "broken_positive": 1,
        "broken_negative": 0,
        "limit_down": 1,
        "suspended": 1,
        "missing": 1,
    }
    assert got["streak_pool_raw_sample_size"] == 5
    assert got["streak_pool_observed_sample_size"] == 2
    assert got["streak_pool_promotion"]["text"] == "1/2（50%）"
    assert got["broken_rate"]["text"] == "1/2（50%）"
    assert got["streak_pool_limit_down_count"] == 1
    assert got["streak_pool_missing_count"] == 1


def test_prediction_snapshot_round_trip_and_evaluation(tmp_path):
    from report_logic import evaluate_prediction, load_prediction_snapshots, save_prediction_snapshot
    path = tmp_path / "prediction_snapshots.jsonl"
    row = save_prediction_snapshot(
        path, report_date="2026-08-06", scene="C_高位分歧",
        stance="防御档 · 逆指数", base_scenario="D", probability=0.46,
        focus_pool=["甲公司"], entry_conditions=["弱转强"],
        invalidation_conditions=["跌破均线"],
    )
    assert load_prediction_snapshots(path) == [row]
    result = evaluate_prediction(
        row, actual={"max_height": 7, "focus_hits": ["甲公司"], "market_up": True}
    )
    assert result["evaluated"] is True
    assert result["hit"] is True
    assert result["report_date"] == "2026-08-06"


def test_prediction_snapshot_is_idempotent_by_report_date(tmp_path):
    from report_logic import load_prediction_snapshots, save_prediction_snapshot

    path = tmp_path / "prediction_snapshots.jsonl"
    first = save_prediction_snapshot(path, report_date="2026-08-06", scene="A")
    second = save_prediction_snapshot(path, report_date="2026-08-06", scene="B")
    rows = load_prediction_snapshots(path)
    assert len(rows) == 1
    assert rows[0]["scene"] == "B"
    assert second["saved_at"] == rows[0]["saved_at"]
    assert first["scene"] == "A"


def test_prediction_snapshot_can_keep_same_day_rerun_when_requested(tmp_path):
    from report_logic import load_prediction_snapshots, save_prediction_snapshot

    path = tmp_path / "prediction_snapshots.jsonl"
    save_prediction_snapshot(path, report_date="2026-08-06", scene="A")
    save_prediction_snapshot(path, report_date="2026-08-06", scene="B", replace_existing=False)
    assert [row["scene"] for row in load_prediction_snapshots(path)] == ["A", "B"]


def test_prediction_evaluation_reports_partial_horizon_completion():
    from report_logic import evaluate_prediction

    prediction = {"report_date": "2026-08-06", "focus_pool": ["甲公司"]}
    pending = evaluate_prediction(prediction, actual={})
    assert pending["evaluated"] is False
    assert pending["completion_status"] == "数据未就位"

    t1_only = evaluate_prediction(
        prediction,
        actual={"t1": {"market_up": True, "focus_hits": ["甲公司"], "max_height": 6}},
    )
    assert t1_only["evaluated"] is True
    assert t1_only["completed"] is False
    assert t1_only["completion_status"] == "仅T+1已完成"
    assert t1_only["t1_hit"] is True
    assert t1_only["t3_hit"] is None

    both = evaluate_prediction(
        prediction,
        actual={
            "t1": {"market_up": True, "focus_hits": ["甲公司"], "max_height": 6},
            "t3": {"market_direction": "up", "focus_hits": ["甲公司"], "max_height": 7},
        },
    )
    assert both["completed"] is True
    assert both["completion_status"] == "T+1/T+3均已完成"
    assert both["t1_hit"] is True
    assert both["t3_hit"] is True


def test_prediction_evaluation_accepts_legacy_flat_actual_and_empty_focus_pool():
    from report_logic import evaluate_prediction

    result = evaluate_prediction(
        {"report_date": "2026-08-06", "focus_pool": []},
        actual={"market_up": True, "max_height": 5},
    )
    assert result["t1"]["evaluated"] is True
    assert result["t1"]["focus_pool_hit"] is True
    assert result["t1"]["hit"] is True


def test_dashboard_uses_distinct_ratios_quality_and_dynamic_scenarios():
    from decision_dashboard import build_dashboard_ctx, generate_dashboard_html

    ctx = build_dashboard_ctx(
        timing={
            "scene": "E_主升加速", "action": "锁仓主升", "level": "进攻",
            "color": "#ff4444", "position": "7成仓位", "win_rate": 0.7,
            "desc": "强势延续",
        },
        advance_decline={
            "up": 4000, "down": 1000, "zt": 140, "dt": 2,
            "zt_prev": 120, "zt_max_height": 8, "zt_max_height_prev": 7,
            "primary_source": "fupan", "fallback_source": "tencent",
            "used_fallback": False, "used_stale": False,
            "market_total": 5000, "market_covered": 5000,
        },
        echelon=[{"height": "8连板"}, {"height": "6连板"}, {"height": "5连板"}],
        report_date="2026-08-06",
    )

    assert ctx["breadth_ratio"] == 0.8
    assert ctx["advance_decline_ratio"] == 4.0
    assert ctx["data_quality"]["status"] == "ok"
    assert len(ctx["scenarios"]) == 4
    assert all(s["probability"] is None for s in ctx["scenarios"])
    assert round(sum(s["model_weight"] for s in ctx["scenarios"]), 6) == 1.0

    html = generate_dashboard_html(ctx)
    assert "上涨占比" in html
    assert "涨跌比" in html
    assert "数据已更新" in html
    assert "沪深北全A" in html
    assert "数据源" not in html
    assert "used_fallback" not in html
    assert "概率 20%" not in html
    assert "None" not in html



def test_catalyst_attribution_never_emits_none_label():
    from catalyst_attribution import _pick_top_catalyst

    got = _pick_top_catalyst({
        "announcements": [{"type": None, "title": "公告标题", "date": "2026-08-06"}],
        "news": [{"source": None, "title": "新闻标题", "time": "2026-08-06"}],
    })
    assert got["tag"] == "公告 · 公告"
    assert "None" not in got["tag"]

def test_dashboard_catalyst_none_is_normalized_in_both_renderers():
    from decision_dashboard import generate_dashboard_html, generate_dashboard_section

    ctx = {
        "date_str": "2026-08-06", "scene": "C_高位分歧", "action": "防守",
        "level": "中性", "color": "#d29922", "position": "3成仓位",
        "win_rate": None, "desc": "分歧观察",
        "curr_h": 6, "prev_h": 6, "pressure_5d": 7, "zt": 60,
        "dt": 18, "zt_prev": 80, "breadth_ratio": 0.35,
        "advance_decline_ratio": 0.54, "up": 1800, "down": 3300,
        "ladder": 4, "h3": 0, "h4": 1, "h5": 0, "h6p": 1,
        "scenarios": [], "focus_df": None,
        "focus_catalysts": {"测试股": {"catalyst": {"tag": None, "text": None, "url": None}}},
        "data_quality": {"status": "degraded", "coverage_pct": 80.0,
                          "primary_source": "fupan", "fallback_source": "tencent",
                          "used_fallback": True, "used_stale": False,
                          "market_total": 5000, "market_covered": 4000,
                          "missing_fields": [], "errors": []},
        "ladder_metrics": {"height": 6, "ladder": 4, "counts": {3: 0, 4: 1, 5: 0, 6: 1},
                            "gap_heights": [5], "gap_risk": True, "gap_text": "缺5板", "gap_risk_label": "高"},
    }
    html = generate_dashboard_html(ctx) + generate_dashboard_section(ctx)
    assert "None" not in html
    assert "无近期催化" not in html
    assert "观察名单" not in html
    assert "空间结构" in html

def test_market_state_blocks_strong_conclusions_when_quality_is_incomplete():
    from report_logic import assess_data_quality, build_market_state

    quality = assess_data_quality(
        report_date="2026-08-06", trade_day=True, market_total=5000,
        market_covered=3200, primary_source="tencent",
        ad_incomplete=True, market_scope="沪深北全A",
    )
    state = build_market_state(quality, scene="E_主升加速")
    assert quality["status"] == "blocked"
    assert state["can_publish"] is False
    assert state["allow_strong_conclusion"] is False
    assert state["label"] == "数据阻断"
    assert "覆盖率" in state["reason"]


def test_stock_code_normalization_covers_sh_sz_bj_formats():
    from report_logic import normalize_stock_code

    assert normalize_stock_code("600000.SH") == "sh600000"
    assert normalize_stock_code("SZ.000001") == "sz000001"
    assert normalize_stock_code("bj920001") == "bj920001"
    assert normalize_stock_code("920001.BJ") == "bj920001"
    assert normalize_stock_code("430047") == "bj430047"


def test_scenario_probabilities_are_suppressed_when_quality_is_blocked():
    from report_logic import build_scenario_probabilities

    rows = build_scenario_probabilities(
        scene="E_主升加速", ad_ratio=0.78, zt=140, dt=2,
        curr_h=8, pressure_5d=6, ladder=18, h5=3,
        data_quality={"status": "blocked"}, historical_samples=2,
    )
    assert len(rows) == 4
    assert all(row["probability"] is None for row in rows)
    assert all(row["confidence"] == "低" for row in rows)
    assert all("数据未就位" in row["prob"] for row in rows)


def test_blocked_scenarios_still_render_without_historical_rate():
    from decision_dashboard import build_dashboard_ctx, generate_dashboard_html

    ctx = build_dashboard_ctx(
        timing={"scene": "E_主升加速", "win_rate": None},
        advance_decline={
            "trade_day": True,
            "market_total": 5000,
            "market_covered": 3200,
            "ad_incomplete": True,
            "market_scope": "沪深北全A",
            "zt": 60,
            "dt": 18,
            "zt_max_height": 6,
            "zt_max_height_prev": 6,
            "up": 1800,
            "down": 3300,
        },
        report_date="2026-08-06",
    )

    html = generate_dashboard_html(ctx)
    assert "基础事实有限" in html
    assert "明日验证路径" not in html
    assert "historical_rate" not in html


def test_ladder_metrics_expose_progression_and_isolated_leader():
    from report_logic import compute_ladder_metrics

    got = compute_ladder_metrics(
        [{"height": "7连板", "sealed": True}, {"height": "3连板", "sealed": True}],
        previous_echelon=[{"height": "6连板"}, {"height": "2连板"}, {"height": "1连板"}],
    )
    assert got["progression_rate"] == 0.5
    assert got["isolated_leader"] is True
    assert got["broken_count"] == 0


def test_tradeable_pool_filters_status_and_liquidity():
    from report_logic import filter_tradeable_pool

    rows = [
        {"code": "600000.SH", "name": "正常股", "turnover": 2_000_000},
        {"code": "000001.SZ", "name": "*ST风险", "turnover": 8_000_000},
        {"code": "920001.BJ", "name": "北交所", "turnover": 2_000_000},
        {"code": "600002.SH", "name": "停牌股", "turnover": 9_000_000, "suspended": True},
        {"code": "600003.SH", "name": "流动性不足", "turnover": 10_000},
    ]
    got = filter_tradeable_pool(rows, min_turnover=100_000, include_bj=True)
    assert [row["code"] for row in got] == ["sh600000", "bj920001"]

def test_market_universe_summary_reports_real_sh_sz_bj_coverage():
    from report_logic import summarize_market_universe

    got = summarize_market_universe([
        "600000.SH", "000001.SZ", "920001.BJ", "920001.BJ", "bad-code",
    ])
    assert got["market_total"] == 3
    assert got["market_prefixes"] == ["bj", "sh", "sz"]
    assert got["market_scope"] == "沪深北全A"
    assert got["errors"]


def test_ladder_metrics_explain_progression_denominator_and_missing_previous_data():
    from report_logic import compute_ladder_metrics

    with_previous = compute_ladder_metrics(
        [{"height": "7连板"}, {"height": "3连板"}],
        previous_echelon=[{"height": "6连板"}, {"height": "2连板"}],
    )
    assert with_previous["progression_label"] == "突破昨日最高高度占比"
    assert with_previous["progression_definition"]
    assert with_previous["progression_denominator"] == "今日有效梯队个股数"
    assert with_previous["progressed_count"] == 1
    assert with_previous["progression_rate"] == 0.5

    without_previous = compute_ladder_metrics([{"height": "7连板"}])
    assert without_previous["progression_rate"] is None
    assert without_previous["progression_text"] == "样本不足"


def test_timing_signal_does_not_show_fixed_win_rate_without_historical_stats():
    from timing_signal import generate_timing_signal

    result = generate_timing_signal(
        sentiment_df=None,
        advance_decline={
            "up": 4000, "down": 1000, "zt": 140, "dt": 2,
            "zt_max_height": 8, "zt_max_height_prev": 7,
        },
    )
    assert result["win_rate"] is None
    assert "历史同型样本未加载" in result["desc"]
    assert result["desc"].count("前排逢高兑现") <= 1


def test_timing_signal_uses_supplied_historical_stats():
    from timing_signal import generate_timing_signal

    result = generate_timing_signal(
        sentiment_df=None,
        advance_decline={
            "up": 4000, "down": 1000, "zt": 140, "dt": 2,
            "zt_max_height": 8, "zt_max_height_prev": 7,
            "historical_stats": {"A+_突破共振": {"sample_size": 12, "t3_hit_rate": 0.75}},
        },
        echelon=[{"height": "8连板"}, {"height": "6连板"}, {"height": "5连板"}, {"height": "4连板"}],
    )
    assert result["win_rate"] == 0.75
    assert "历史同型 12 例" in result["desc"]


def test_binomial_confidence_interval_is_explicit_about_sample_size_and_bounds():
    from report_logic import binomial_confidence_interval

    empty = binomial_confidence_interval(0, 0)
    assert empty["rate"] is None
    assert empty["lower"] is None
    assert empty["upper"] is None
    assert empty["text"] == "样本不足"
    assert empty["sufficient_sample"] is False

    got = binomial_confidence_interval(8, 10)
    assert got["successes"] == 8
    assert got["trials"] == 10
    assert got["rate"] == 0.8
    assert 0 <= got["lower"] <= got["rate"] <= got["upper"] <= 1
    assert "95% CI" in got["text"]
    assert got["sufficient_sample"] is True


def test_scenario_probability_uses_real_history_without_fabricating_ci():
    from report_logic import build_scenario_probabilities

    rows = build_scenario_probabilities(
        scene="E_主升加速", ad_ratio=0.78, zt=140, dt=2,
        curr_h=8, pressure_5d=6, ladder=18, h5=3,
        historical_stats={"A": {"successes": 8, "trials": 10}},
    )
    row_a = next(row for row in rows if row["code"] == "A")
    row_b = next(row for row in rows if row["code"] == "B")
    assert row_a["probability_kind"] == "historical_rate"
    assert row_a["probability"] == 0.8
    assert row_a["sample_size"] == 10
    assert row_a["confidence_interval"]["trials"] == 10
    assert row_a["confidence_interval_text"] != "样本不足"
    assert row_b["sample_size"] == 0
    assert row_b["probability_kind"] == "insufficient_history"
    assert row_b["probability"] is None
    assert row_b["confidence_interval"] is None
    assert row_b["confidence_interval_text"] == "样本不足"


def test_data_freshness_and_three_layer_state_are_separated():
    from report_logic import assess_data_quality, build_market_state

    common = dict(
        report_date="2026-08-06", trade_day=True, market_total=5000,
        market_covered=5000, primary_source="test",
        market_prefixes=["sh", "sz", "bj"],
        report_generated_at="2026-08-06T10:00:00+08:00",
    )
    fresh = assess_data_quality(**common, source_timestamp="2026-08-06T09:45:00+08:00")
    delayed = assess_data_quality(**common, source_timestamp="2026-08-06T09:00:00+08:00")
    stale = assess_data_quality(**common, source_timestamp="2026-08-06T05:00:00+08:00")
    unknown = assess_data_quality(**common)
    blocked_common = dict(common, market_prefixes=["sh", "sz"])
    blocked = assess_data_quality(**blocked_common)
    assert fresh["freshness_level"] == "fresh"
    assert delayed["freshness_level"] == "delayed"
    assert stale["freshness_level"] == "stale"
    assert unknown["freshness_level"] == "unknown"
    assert blocked["status"] == "blocked"
    assert blocked["freshness_level"] == "blocked"

    ready = build_market_state(fresh, historical_samples=30)
    insufficient = build_market_state(fresh, historical_samples=3)
    blocked_state = build_market_state(blocked, historical_samples=30)
    assert ready["data_layer"]["status"] == "ok"
    assert ready["statistics_layer"]["status"] == "ok"
    assert ready["decision_layer"]["status"] == "ready"
    assert insufficient["statistics_layer"]["status"] == "insufficient_sample"
    assert insufficient["decision_layer"]["status"] == "conditional"
    assert insufficient["allow_strong_conclusion"] is False
    assert blocked_state["data_layer"]["status"] == "blocked"
    assert blocked_state["statistics_layer"]["status"] == "blocked"
    assert blocked_state["decision_layer"]["status"] == "blocked"


def test_prediction_failure_attribution_is_split_by_t1_and_t3():
    from report_logic import evaluate_prediction

    prediction = {
        "report_date": "2026-08-05",
        "market_direction": "up",
        "focus_pool": ["A"],
        "space_height_target": 8,
    }
    actual = {
        "t1": {"market_direction": "down", "focus_hits": ["B"], "max_height": 0},
        "t3": {"market_direction": "up", "focus_hits": ["A"], "max_height": 8},
    }
    got = evaluate_prediction(prediction, actual)
    assert got["completed"] is True
    assert got["t1_hit"] is False
    assert got["t3_hit"] is True
    assert "MARKET_DIRECTION_MISMATCH" in got["failure_codes_by_horizon"]["t1"]
    assert "FOCUS_POOL_MISS" in got["failure_codes_by_horizon"]["t1"]
    assert "HEIGHT_NOT_CONFIRMED" in got["failure_codes_by_horizon"]["t1"]
    assert got["failure_codes_by_horizon"]["t3"] == []
    assert got["success_factors_by_horizon"]["t3"]


def test_dashboard_surfaces_evidence_and_layered_quality_in_both_views():
    from decision_dashboard import (
        build_dashboard_ctx, generate_dashboard_html, generate_dashboard_section,
    )

    ctx = build_dashboard_ctx(
        timing={
            "scene": "A+_突破共振", "action": "测试", "level": "测试",
            "color": "#ff4444", "position": "4成", "win_rate": 0.75,
            "desc": "测试", "historical_samples": 12,
            "historical_stats": {"A": {"successes": 8, "trials": 10}},
            "win_rate_sample_size": 12,
            "win_rate_confidence_interval": {
                "text": "75%（95% CI 46%～92%）",
            },
        },
        advance_decline={
            "trade_day": True, "market_total": 5000, "market_covered": 5000,
            "up": 4000, "down": 1000, "zt": 140, "dt": 2,
            "zt_max_height": 8, "zt_max_height_prev": 7,
            "market_prefixes": ["sh", "sz", "bj"],
            "primary_source": "test",
            "source_timestamp": "2026-08-06T09:30:00+08:00",
            "report_generated_at": "2026-08-06T09:31:00+08:00",
        },
        echelon=[
            {"height": "8连板"}, {"height": "6连板"},
            {"height": "5连板"}, {"height": "4连板"},
        ],
        report_date="2026-08-06",
    )
    html = generate_dashboard_html(ctx)
    section = generate_dashboard_section(ctx)
    for rendered in (html, section):
        assert "数据佐证" in rendered
        assert "明日验证路径" not in rendered
        assert "等待验证信号" not in rendered
    for token in ("新鲜度", "数据层", "统计层", "决策层", "数据源", "run_id"):
        assert token not in html
        assert token not in section


def _dashboard_fixture_with_quality(market_prefixes, *, used_fallback=False):
    from decision_dashboard import build_dashboard_ctx
    import pandas as pd

    return build_dashboard_ctx(
        timing={
            "scene": "E_主升加速", "action": "锁仓主升 / 去弱留强",
            "level": "强进攻", "color": "#ff4444", "position": "7成仓位",
            "win_rate": 0.8, "desc": "测试动作不应在降级报告中泄漏",
            "historical_samples": 30,
        },
        advance_decline={
            "trade_day": True, "market_total": 5000, "market_covered": 5000,
            "up": 3800, "down": 1200, "zt": 100, "dt": 4,
            "zt_max_height": 8, "zt_max_height_prev": 7,
            "market_prefixes": market_prefixes,
            "primary_source": "primary", "fallback_source": "cache",
            "used_fallback": used_fallback,
            "source_timestamp": "2026-08-06T09:30:00+08:00",
            "report_generated_at": "2026-08-06T09:31:00+08:00",
        },
        echelon=[
            {"height": "8连板", "stocks": ["测试龙头"],
             "stock_details": [{"name": "测试龙头", "code": "sh600001"}],
             "primary": "测试主线"},
        ],
        focus_df=pd.DataFrame([{
            "股票": "测试龙头", "板块": "测试主线", "策略池": "【主升接力池】",
            "入场条件": "若放量承接再考虑", "防守位": "跌破前低",
        }]),
        focus_catalysts={"测试龙头": {"catalyst": {"tag": None, "text": None, "url": None}}},
        report_date="2026-08-06",
    )


def test_blocked_dashboard_only_shows_facts_and_withholds_decisions():
    from decision_dashboard import generate_dashboard_html, generate_dashboard_section

    ctx = _dashboard_fixture_with_quality(["sh", "sz"])
    html = generate_dashboard_html(ctx)
    section = generate_dashboard_section(ctx)
    for rendered in (html, section):
        assert "盘面事实" in rendered
        assert "股票池未发布" not in rendered
        assert "锁仓" not in rendered
        assert "立即清仓" not in rendered
        assert "建议仓位" not in rendered
        assert "明日核心股票池" not in rendered
        assert "公告 · None" not in rendered


def test_degraded_dashboard_is_observation_only_and_has_no_unconditional_action():
    from decision_dashboard import generate_dashboard_html

    ctx = _dashboard_fixture_with_quality(["sh", "sz", "bj"], used_fallback=True)
    html = generate_dashboard_html(ctx)
    assert "盘面判断" in html
    assert "明日执行计划" in html
    assert "测试龙头" in html
    assert "不追；断板减仓" in html
    assert "建议仓位" in html
    assert "数据状态" in html
    assert "观察模式" not in html
    for forbidden in ("锁仓主升", "立即清仓", "确定性买入"):
        assert forbidden not in html


def test_focus_pool_generation_excludes_st_and_missing_codes():
    import pandas as pd
    from screener import generate_focus_pool

    echelon = [{
        "height": "3连板",
        "primary": "AI算力",
        "secondary": "",
        "stocks": ["*ST传智", "正常股份"],
        "stock_details": [
            {"name": "*ST传智", "code": "sz003032"},
            {"name": "正常股份", "code": "sz000001"},
        ],
    }]
    got = generate_focus_pool(
        pd.DataFrame([[1.0]], columns=["AI算力"]),
        echelon,
        {},
        pd.DataFrame(),
    )
    assert "*ST传智" not in got.get("股票", []).tolist()
    assert "正常股份" in got.get("股票", []).tolist()
    assert got["代码"].notna().all()
    assert (got["代码"].astype(str).str.len() > 0).all()



def test_facts_only_scenario_payload_contains_only_facts_layer():
    from decision_dashboard import _sanitize_scenarios_for_publication

    rows = [{"code": "A", "name": "上涨延续", "probability": 0.6, "probability_pct": 60}]
    got = _sanitize_scenarios_for_publication(rows, "facts_only")
    assert len(got) == 1
    assert got[0]["code"] == "FACTS"
    assert got[0]["probability"] is None
    assert got[0]["probability_pct"] is None
    assert "概率" not in str(got)


def test_dashboard_renders_ladder_quality_and_mainline_concentration():
    from decision_dashboard import generate_dashboard_html, generate_dashboard_section

    ctx = {
        "date_str": "2026-08-06", "scene": "中性", "action": "观察",
        "level": "中性", "color": "#d29922", "position": "—",
        "curr_h": 4, "prev_h": 3, "pressure_5d": 3, "zt": 10, "dt": 2,
        "zt_prev": 8, "breadth_ratio": 0.6, "advance_decline_ratio": 1.5,
        "up": 3000, "down": 2000, "ladder": 6, "h3": 2, "h4": 1,
        "h5": 0, "h6p": 0, "scenarios": [],
        "ladder_metrics": {
            "height": 4, "ladder": 6, "height_count": 3,
            "progression_text": "1/3", "progression_label": "突破昨日最高高度占比",
            "gap_text": "无明显断层", "gap_risk": False, "gap_risk_label": "低",
            "progression_rates": {1: {"label": "首板→二板晋级率", "rate": 0.5, "numerator": 1, "denominator": 2}},
            "broken_rate": 0.25, "explosion_rate": 0.1, "re封_rate": 0.5,
            "one_word_count": 1, "turnover_count": 2, "quality_score": 72,
            "quality_sample_size": 4,
        },
        "mainline_concentration": {
            "top_mainline": "AI算力", "top_share": 0.5, "hhi": 0.38,
            "sample_size": 4, "distribution": {"AI算力": 2, "机器人": 1},
        },
        "data_quality": {"status": "ok", "coverage_pct": 100.0,
            "market_total": 5000, "market_covered": 5000, "primary_source": "test",
            "fallback_source": "cache", "market_scope": "沪深北全A",
            "market_prefixes": ["sh", "sz", "bj"], "missing_fields": [], "errors": []},
    }
    for html in (generate_dashboard_html(ctx), generate_dashboard_section(ctx)):
        assert "连板质量" not in html
        assert "首板→二板：" not in html
        assert "炸板率" not in html
        assert "主线集中度" not in html
        assert "二进三" in html
        assert "空间结构" in html
        assert "领先主线" in html
        assert "AI算力" in html
        assert "0.0%" not in html


def test_dashboard_facts_only_does_not_render_probability_or_strategy_sections():
    from decision_dashboard import generate_dashboard_html

    ctx = {
        "date_str": "2026-08-06", "scene": "E_主升加速", "action": "锁仓主升",
        "level": "强进攻", "color": "#ff4444", "position": "7成仓位",
        "win_rate": 0.8, "desc": "测试动作", "curr_h": 8, "prev_h": 7,
        "pressure_5d": 6, "zt": 100, "dt": 2, "zt_prev": 90,
        "breadth_ratio": 0.8, "advance_decline_ratio": 4.0, "up": 4000, "down": 1000,
        "ladder": 18, "h3": 2, "h4": 2, "h5": 1, "h6p": 1, "scenarios": [],
        "data_quality": {"status": "blocked", "coverage_pct": 80.0,
            "market_total": 5000, "market_covered": 4000, "primary_source": "test",
            "fallback_source": "cache", "market_scope": "沪深北全A",
            "market_prefixes": ["sh", "sz"], "missing_fields": [], "errors": ["AD_RECONCILIATION_FAILED"]},
    }
    html = generate_dashboard_html(ctx)
    assert "基础事实有限" in html
    assert "概率" not in html
    assert "情景决策树" not in html
    assert "锁仓主升" not in html



def test_main_report_timing_radar_facts_only_hides_position_and_actions():
    from 主线强度追踪 import _render_timing_radar_html

    timing = {
        "action": "兑现减仓 / 高位不接",
        "desc": "前排逢高兑现, 空仓等新主线.",
        "level": "强防御",
        "position": "3-4成仓位",
        "color": "#ff4444",
    }
    html = _render_timing_radar_html(timing, {"publication_mode": "facts_only"})
    assert "盘面判断" in html
    assert "基础事实有限" in html
    for forbidden in ("建议仓位", "兑现减仓", "高位不接", "前排逢高兑现", "空仓等新主线"):
        assert forbidden not in html


def test_main_report_timing_radar_observation_hides_position_and_unconditional_action():
    from 主线强度追踪 import _render_timing_radar_html

    timing = {
        "action": "锁仓主升 / 去弱留强",
        "desc": "测试动作不应在降级报告中泄漏",
        "level": "强进攻",
        "position": "7成仓位",
        "color": "#ff4444",
    }
    html = _render_timing_radar_html(timing, {"publication_mode": "observation"})
    assert "盘面判断" in html
    assert "观察模式" in html
    assert "满足触发条件后再评估" in html
    for forbidden in ("建议仓位", "锁仓主升", "去弱留强", "7成仓位"):
        assert forbidden not in html


def test_tradeable_filter_uses_security_state_and_excludes_st_delisted_suspended():
    from report_logic import filter_tradeable_pool

    rows = [
        {"code": "sz000001", "name": "平安银行", "turnover": 1},
        {"code": "sz000002", "name": "*ST传智", "turnover": 1},
        {"code": "sz000003", "name": "普通股票", "status": "suspended", "turnover": 1},
        {"code": "bj430001", "name": "退市样本", "status": "delisted", "turnover": 1},
    ]
    got = filter_tradeable_pool(rows)
    assert [row["code"] for row in got] == ["sz000001"]


def test_main_report_rebound_facts_only_hides_trade_actions(monkeypatch):
    import pandas as pd
    import 主线强度追踪 as report

    monkeypatch.setattr(
        report,
        "_analyze_active_mainlines",
        lambda: (
            "<span>主动反弹 (可追)</span>",
            "<span>跟随反弹 (减亏离场)</span>",
            [("AI算力", 3, 4)],
            [("机器人", 1)],
        ),
    )
    html = report.generate_rebound_analysis(
        {"up": 3200, "down": 1500},
        pd.DataFrame({"up": [1800, 2200, 3200]}),
        [{"height": "5连板"}, {"height": "3连板"}, {"height": "1连板"}],
        {"publication_mode": "facts_only"},
    )

    assert "主动主线事实" in html
    assert "跟随主线事实" in html
    for forbidden in ("可追", "减亏离场", "回避追高", "建议仓位", "立即买入"):
        assert forbidden not in html


def test_mainline_ladder_excludes_non_tradeable_security_names():
    import pandas as pd
    import 主线强度追踪 as report

    dates = [f"2026-07-{day:02d}" for day in range(1, 22)]
    price_rows = []
    for index, date in enumerate(dates):
        price_rows.extend([
            {"date": date, "code": "sz000001", "close": 100 + index * 2},
            {"date": date, "code": "sz000002", "close": 100 + index * 3},
        ])
    price_df = pd.DataFrame(price_rows)
    classified = pd.DataFrame([
        {"代码": "sz000001", "名称": "正常股份", "细分板块": "AI应用", "大主线": "AI应用"},
        {"代码": "sz000002", "名称": "*ST传智", "细分板块": "AI应用", "大主线": "AI应用"},
    ])

    ladder = report.build_mainline_ladder(price_df, classified)
    names = [row["name"] for rows in ladder.values() for row in rows]

    assert "正常股份" in names
    assert "*ST传智" not in names


def test_latest_completed_date_excludes_premarket_future_day():
    from datetime import datetime
    from time_utils import select_latest_completed_date

    got = select_latest_completed_date(
        ["20260805", "20260806", "20260807"],
        now=datetime(2026, 8, 7, 0, 56),
    )
    assert got.strftime("%Y%m%d") == "20260806"


def test_latest_completed_date_honors_explicit_report_date():
    from datetime import datetime
    from time_utils import select_latest_completed_date

    got = select_latest_completed_date(
        ["20260805", "20260806", "20260807"],
        now=datetime(2026, 8, 7, 16, 30),
        report_date="2026-08-06",
    )
    assert got.strftime("%Y%m%d") == "20260806"
def test_completed_rows_filter_excludes_future_cache_rows():
    import pandas as pd
    from time_utils import filter_completed_rows

    frame = pd.DataFrame({
        "日期": ["20260805", "2026-08-06", "20260807", "invalid"],
        "up": [1000, 2000, 3000, 4000],
    })

    got = filter_completed_rows(frame, "日期", report_date="2026-08-06")

    assert got["up"].tolist() == [1000, 2000]
    assert got["日期"].tolist() == ["20260805", "2026-08-06"]
def test_report_price_cache_excludes_rows_after_report_date(tmp_path, monkeypatch):
    import pandas as pd
    import 主线强度追踪 as report

    cache = tmp_path / "price.csv"
    pd.DataFrame([
        {"date": "2026-08-06", "code": "sh600000", "close": 10.0},
        {"date": "2026-08-07", "code": "sh600000", "close": 11.0},
    ]).to_csv(cache, index=False)
    monkeypatch.setattr(report, "PRICE_CACHE", str(cache))
    monkeypatch.setenv("REPORT_DATE", "2026-08-06")

    got = report.load_price_cache()
    raw = report.load_price_cache(include_future=True)

    assert got["date"].tolist() == ["2026-08-06"]
    assert raw["date"].tolist() == ["2026-08-06", "2026-08-07"]

def test_phase_index_fetch_respects_report_date_cutoff(monkeypatch):
    import pandas as pd
    import phase_resonance

    class FakeAk:
        @staticmethod
        def stock_zh_index_daily(symbol):
            return pd.DataFrame([
                {"date": "2026-08-06", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
                {"date": "2026-08-07", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 2},
            ])

    monkeypatch.setitem(__import__("sys").modules, "akshare", FakeAk())
    monkeypatch.setenv("REPORT_DATE", "2026-08-06")

    got = phase_resonance.fetch_index(lookback=20)

    assert [row["date"] for row in got] == ["2026-08-06"]

def test_report_price_consumers_share_report_date_cutoff(tmp_path, monkeypatch):
    import pandas as pd
    import limit_ratio_factor
    import phase_resonance
    import stock_representatives

    price_cache = tmp_path / "price.csv"
    pd.DataFrame([
        {"date": "2026-08-05", "code": "sh600000", "close": 10.0},
        {"date": "2026-08-06", "code": "sh600000", "close": 11.0},
        {"date": "2026-08-07", "code": "sh600000", "close": 12.0},
    ]).to_csv(price_cache, index=False)
    zt_cache = tmp_path / "zt.csv"
    pd.DataFrame([
        {"日期": "20260806", "代码": "600000", "类型": "ZT", "连板数": 1},
        {"日期": "20260807", "代码": "600000", "类型": "ZT", "连板数": 2},
    ]).to_csv(zt_cache, index=False, encoding="utf-8-sig")

    monkeypatch.setenv("REPORT_DATE", "2026-08-06")
    monkeypatch.setattr(limit_ratio_factor, "PRICE_CACHE_FILE", str(price_cache))
    monkeypatch.setattr(limit_ratio_factor, "ZT_CACHE_FILE", str(zt_cache))
    monkeypatch.setattr(phase_resonance, "PRICE_CACHE", str(price_cache))
    monkeypatch.setattr(stock_representatives, "PRICE_CACHE", str(price_cache))
    monkeypatch.setattr(stock_representatives, "ZT_CACHE_FILE", str(zt_cache))

    factor = limit_ratio_factor.MarketSentimentFactor()
    assert "20260807" not in factor._load_ad_cache()
    assert "20260807" not in factor._load_zt_cache()

    phases = {"底部至今": ("2026-08-05", "2026-08-07")}
    breadth = phase_resonance.market_breadth({"phases": phases})
    assert breadth["底部至今"]["median"] == 10.0

    returns = stock_representatives._phase_returns(phases)
    assert round(float(returns.loc["sh600000", "底部至今"]), 2) == 10.0
    stats = stock_representatives._zt_stats("20260805")
    assert stats.loc[0, "涨停次数"] == 1

def test_price_cache_breadth_calibration_updates_coverage_and_lineage():
    from report_logic import apply_price_cache_breadth_calibration

    original = {
        "up": 2789,
        "down": 2590,
        "market_covered": 0,
        "primary_source": "fupan",
        "source_chain": ["fupan"],
        "errors": [],
    }
    got = apply_price_cache_breadth_calibration(
        original,
        {"up": 1778, "down": 3237, "date": "20260806"},
        source_timestamp="2026-08-06",
    )

    assert got["up"] == 1778 and got["down"] == 3237
    assert got["market_covered"] == 5015
    assert got["primary_source"] == "price_cache"
    assert got["calibration_source"] == "price_cache"
    assert got["source_chain"] == ["fupan", "price_cache"]
    assert got["used_fallback"] is True
    assert got["source_timestamp"] == "2026-08-06"
    assert got["flat"] is None
    assert got["ad_reconciliation_enabled"] is False
    assert original["market_covered"] == 0


def test_reconcile_limit_pool_separates_fupan_facts_from_classification_coverage():
    from report_logic import reconcile_limit_pool

    ladder = {
        "category": {
            "3板及以上": [
                {"code": "600001", "name": "沪市样本", "level": 3},
            ],
            "2板": [
                {"code": "000002.SZ", "name": "深市样本", "level": 2},
            ],
            "首板": [
                {"code": "430001", "name": "北交样本", "level": 1},
            ],
        },
        "sector_summary": {},
    }
    classified = [
        {"代码": "sh600001", "名称": "沪市样本", "大主线": "算力"},
        {"代码": "sz000003", "名称": "归因池独有", "大主线": "机器人"},
    ]

    got = reconcile_limit_pool(ladder, classified)

    assert got["authoritative_count"] == 3
    assert got["classified_count"] == 2
    assert got["matched_count"] == 1
    assert got["fupan_only_count"] == 2
    assert got["cls_only_count"] == 1
    assert got["classification_coverage_pct"] == 33.33
    assert got["authoritative_codes"] == ["bj430001", "sh600001", "sz000002"]
    assert got["fupan_only_codes"] == ["bj430001", "sz000002"]
    assert got["cls_only_codes"] == ["sz000003"]
    assert got["source"] == "fupan_ladder"
    assert got["classification_source"] == "classified_limit_pool"
    assert ladder["category"]["首板"][0]["code"] == "430001"


def test_reconcile_limit_pool_requires_same_trade_date_before_matching():
    from report_logic import reconcile_limit_pool

    ladder = {
        "date": "20260807",
        "category": {"首板": [{"code": "600001", "name": "样本", "level": 1}]},
    }
    classified = [
        {"日期": "20260806", "代码": "600001", "大主线": "旧日期"},
        {"日期": "20260807", "代码": "600001", "大主线": "当日"},
        {"代码": "000001", "大主线": "无日期"},
    ]

    got = reconcile_limit_pool(ladder, classified, expected_date="20260807")

    assert got["date_aligned"] is True
    assert got["authoritative_date"] == "20260807"
    assert got["classification_date"] == "20260807"
    assert got["date_mismatch_count"] == 1
    assert got["date_missing_count"] == 1
    assert got["matched_count"] == 1
    assert got["cls_only_count"] == 0


def test_reconcile_limit_pool_rejects_unverified_classification_date():
    from report_logic import reconcile_limit_pool

    ladder = {"category": {"首板": [{"code": "600001", "level": 1}]}}
    got = reconcile_limit_pool(
        ladder,
        [{"代码": "600001", "大主线": "未声明日期"}],
        expected_date="20260807",
    )

    assert got["date_aligned"] is False
    assert got["matched_count"] == 0
    assert got["date_missing_count"] == 1

def test_reconcile_limit_pool_rejects_unverified_fupan_as_authoritative():
    from report_logic import reconcile_limit_pool

    got = reconcile_limit_pool(
        {
            "source": "fupan_ladder",
            "date": "20260807",
            "date_verified": False,
            "category": {
                "首板": [
                    {"code": "600001", "name": "样本", "level": 1},
                    {"code": "600002", "name": "样本2", "level": 1},
                ]
            },
        },
        [
            {
                "日期": "20260807",
                "代码": "sh600001",
                "名称": "样本",
                "连板数": 1,
            }
        ],
        expected_date="20260807",
    )

    assert got["observed_count"] == 2
    assert got["authoritative_count"] == 0
    assert got["date_verified"] is False
    assert got["date_aligned"] is False
    assert "FuPan日期未验证" in " ".join(got["warnings"])


def test_build_echelon_table_normalizes_cls_and_fupan_codes_before_attribution():
    import pandas as pd
    import 主线强度追踪 as report

    cls_data = {
        "plate_stock": [{
            "secu_name": "算力",
            "stock_list": [{"secu_code": "600001.SH", "up_tags": ["算力"]}],
        }],
    }
    zt_today = pd.DataFrame([
        {"代码": "sh600001", "名称": "沪市样本", "连板数": 3},
    ])

    got = report.build_echelon_table(cls_data, zt_today)

    assert got[0]["count"] == 1
    assert got[0]["primary"].startswith("算力")
    assert got[0]["stock_details"][0]["code"] == "sh600001"
def test_data_credibility_summary_separates_module_states_and_legacy_price_coverage():
    from report_logic import build_data_credibility_summary

    quality = {
        "status": "degraded",
        "publication_mode": "observation",
        "market_scope": "沪深北全A",
        "market_total": 5538,
        "market_covered": 5015,
        "modules": {
            "universe": {
                "status": "ok", "total": 5538, "covered": 5538,
                "coverage_pct": 100, "source": "security_master",
            },
            "price_raw": {
                "status": "ok", "total": 5538, "covered": 5538,
                "coverage_pct": 100, "source": "price_cache",
                "lineage": {"price_basis": "legacy_mixed"},
            },
            "price_qfq": {
                "status": "unavailable", "total": 5538, "covered": 0,
                "coverage_pct": 0, "errors": ["close_qfq 缺失"],
            },
            "breadth": {
                "status": "degraded", "total": 5538, "covered": 5015,
                "coverage_pct": 90.56, "errors": ["部分证券无行情"],
            },
        },
        "errors": ["部分证券无行情"],
        "missing_fields": ["close_qfq"],
    }

    got = build_data_credibility_summary(
        quality,
        report_date="2026-08-06",
        report_generated_at="2026-08-06T18:00:00+08:00",
    )

    assert got["report_date"] == "2026-08-06"
    assert got["market_scope"] == "沪深北全A"
    assert got["market_total"] == 5538
    assert got["market_covered"] == 5015
    assert got["modules"]["price_raw"]["effective_coverage_pct"] == 0.0
    assert "price_raw" in got["degraded_modules"]
    assert "price_qfq" in got["unavailable_modules"]
    assert got["source_failure"] >= 1
    assert got["stale"] == 0
    assert got["reasons"]


def test_lianban_review_reports_board_counts_and_explicit_denominators():
    from report_logic import build_lianban_review, compute_ladder_metrics

    metrics = compute_ladder_metrics(
        [
            {"code": "sh000001", "height": 1},
            {"code": "sh000002", "height": 1},
            {"code": "sh000003", "height": 2},
            {"code": "sh000004", "height": 3},
        ],
        previous_echelon=[
            {"code": "sh000001", "height": 1},
            {"code": "sh000002", "height": 1},
            {"code": "sh000003", "height": 1},
            {"code": "sh000004", "height": 2},
        ],
    )
    got = build_lianban_review(metrics)

    assert got["first_board_count"] == 2
    assert got["second_board_count"] == 1
    assert got["first_board_to_second"]["successes"] == 1
    assert got["first_board_to_second"]["trials"] == 3
    assert got["streak_pool_sample_size"] == 1
    assert got["streak_pool_trials"] == 1
    assert got["streak_pool_observed_sample_size"] == 1
    assert got["streak_pool_current_count"] == 1
    assert got["confidence_interval"]["trials"] == 3
    assert got["negative_feedback"]


def test_lianban_review_marks_missing_previous_pool_as_insufficient():
    from report_logic import build_lianban_review, compute_ladder_metrics

    got = build_lianban_review(compute_ladder_metrics([{"height": 1}, {"height": 2}]))

    assert got["status"] == "insufficient"
    assert got["first_board_to_second"]["text"] == "样本不足"
    assert got["streak_pool_sample_size"] == 0
    assert "样本不足" in got["conclusion"]


def test_mainline_review_limits_conclusion_when_attribution_coverage_is_low():
    from report_logic import build_mainline_review, compute_mainline_concentration

    metrics = compute_mainline_concentration(
        [
            {"mainline": "算力", "height": 4},
            {"mainline": "算力", "height": 3},
            {"mainline": "机器人", "height": 3},
        ],
        authoritative_count=10,
        attributed_count=3,
    )
    got = build_mainline_review(
        metrics,
        limit_up_count=10,
        attribution_source="CLS+Eastmoney concepts",
    )

    assert got["top1"] == "算力"
    assert len(got["top3"]) == 2
    assert got["hhi"] == metrics["hhi"]
    assert got["limit_up_count"] == 10
    assert got["lianban_count"] == 3
    assert got["attribution_coverage_pct"] == 30.0
    assert got["authoritative_count"] == 10
    assert got["attributed_count"] == 3
    assert got["unattributed_count"] == 7
    assert got["conclusion_level"] == "insufficient"
    assert "已归因样本" in got["conclusion"]


def test_assess_data_quality_exposes_overflow_without_publishing_invalid_coverage():
    from report_logic import assess_data_quality

    got = assess_data_quality(
        report_date="2026-08-06",
        market_total=2467,
        market_covered=5190,
        primary_source="price_cache",
    )

    assert got["market_covered"] == 2467
    assert got["coverage_pct"] == 100.0
    assert got["raw_market_covered"] == 5190
    assert got["raw_coverage_pct"] == round(5190 / 2467 * 100, 1)
    assert got["status"] == "blocked"
    assert any("COVERAGE_OVERFLOW" in error for error in got["errors"])


def test_observation_mood_card_keeps_analysis_without_position_advice():
    import 主线强度追踪 as report

    html = report._render_observation_mood_card(
        latest_text="52.8%",
        latest_state="⚖️ 震荡分歧",
        direction="偏多",
        emoji="📈",
        range_lo=45,
        range_hi=65,
        stars_html="★★★☆☆",
        reasons_html="• 情绪连续上行<br>• 上涨家数回暖",
        color="#d29922",
    )

    assert "最新情绪值" in html
    assert "当前状态" in html
    assert "倾向方向" in html
    assert "规则观察区间" in html
    assert "信号强度" in html
    assert "情绪连续上行" in html
    assert "仓位建议" not in html
    assert "加仓" not in html


def test_observation_market_stance_keeps_reasons_and_triggers_without_actions():
    from market_stance import render_stance_html

    result = {
        "stance": "观望档 · 轻仓",
        "color": "#d29922",
        "head": "A/D 1.08 处分歧区，梯队结构仍需确认。",
        "play": "不押方向，空仓等破位。",
        "ad_series": [0.92, 1.01, 1.08],
        "zt": 83,
        "dt": 4,
        "max_h": 4,
        "triggers": [{
            "name": "扳机③ 右侧转攻",
            "cond": "A/D 连续2日≥1.05 + 梯队不断档",
            "hit": True,
        }],
    }

    html = render_stance_html(result, observation_only=True)

    assert "A/D 1.08 处分歧区" in html
    assert "近3日 A/D 比值" in html
    assert "右侧转攻" in html
    assert "已触发" in html
    assert "操作:" not in html
    assert "空仓等破位" not in html


def test_price_matrix_stitches_legacy_history_to_partial_qfq_series():
    import pandas as pd
    import 主线强度追踪 as report

    prices = pd.DataFrame([
        {"date": "2026-08-04", "code": "sz301251", "close_legacy": 30.0, "close_qfq": None},
        {"date": "2026-08-05", "code": "sz301251", "close_legacy": 33.0, "close_qfq": None},
        {"date": "2026-08-06", "code": "sz301251", "close_legacy": 36.0, "close_qfq": 39.6},
        {"date": "2026-08-07", "code": "sz301251", "close_legacy": 39.6, "close_qfq": 43.56},
    ])

    matrix = report._price_matrix(prices, "qfq", allow_legacy=True)

    assert matrix.loc["2026-08-04", "sz301251"] == 33.0
    assert matrix.loc["2026-08-05", "sz301251"] == 36.3
    assert matrix.loc["2026-08-06", "sz301251"] == 39.6
    assert matrix.loc["2026-08-07", "sz301251"] == 43.56
    returns = matrix["sz301251"].pct_change().dropna()
    assert (returns.abs() > 0.001).all()


def test_turning_stock_leaders_rank_stitched_qfq_and_use_current_names(tmp_path, monkeypatch):
    import pandas as pd
    import stock_representatives
    from data_sources.name_resolver import NameResolution

    prices = pd.DataFrame([
        {"date": "2026-07-17", "code": "sh600001", "close_legacy": 10.0, "close_qfq": None},
        {"date": "2026-08-07", "code": "sh600001", "close_legacy": 15.0, "close_qfq": 30.0},
        {"date": "2026-07-17", "code": "sz000002", "close_legacy": 20.0, "close_qfq": None},
        {"date": "2026-08-07", "code": "sz000002", "close_legacy": 24.0, "close_qfq": 24.0},
    ])
    path = tmp_path / "prices.csv"
    prices.to_csv(path, index=False)
    monkeypatch.setattr(stock_representatives, "PRICE_CACHE", str(path))
    names = NameResolution(
        names={"sh600001": "领涨股份", "sz000002": "传智教育"},
        sources={"sh600001": "universe", "sz000002": "universe"},
        conflicts=[],
    )

    result = stock_representatives.build_turning_stock_leaders(
        {"底部至今": ("2026-07-17", "2026-08-07")},
        name_resolution=names,
        expected_universe_size=2,
    )

    assert result["usable"] is True
    assert result["coverage"] == 1.0
    assert [row["name"] for row in result["rows"]] == ["领涨股份", "传智教育"]
    assert result["rows"][0]["return"] == 50.0
    assert result["rows"][1]["st"] is False


def test_turning_name_resolution_prefers_security_master_over_stale_industry(tmp_path, monkeypatch):
    import pandas as pd
    import stock_representatives

    master = tmp_path / "security_master.csv"
    industry = tmp_path / "industry_cache.csv"
    pd.DataFrame([
        {"code": "sz003032", "name": "传智教育", "is_st": False},
    ]).to_csv(master, index=False)
    pd.DataFrame([
        {"code": "sz003032", "name": "*ST传智", "industry": "教育"},
    ]).to_csv(industry, index=False)
    monkeypatch.setattr(stock_representatives, "SECURITY_MASTER_CACHE", str(master), raising=False)
    monkeypatch.setattr(stock_representatives, "UNIVERSE_CACHE", str(tmp_path / "missing.csv"))
    monkeypatch.setattr(stock_representatives, "INDUSTRY_CACHE", str(industry))

    names = stock_representatives._load_name_resolution()

    assert names.names["sz003032"] == "传智教育"
    assert names.sources["sz003032"] == "universe"


def test_turning_summary_uses_major_bottom_and_top_three_sector_returns():
    import pandas as pd
    from phase_resonance import build_turning_summary

    det = {
        "shape": "箱体突破 (箱体 3768~3941, 振幅 4.6%, 收在上沿)",
        "bottom": {"date": "2026-07-17", "close": 3764.0},
        "latest": {"date": "2026-08-07", "close": 3940.0},
        "index_series": [
            {"date": "2026-07-17", "close": 3764.0},
            {"date": "2026-07-20", "close": 3800.0},
            {"date": "2026-08-07", "close": 3940.0},
        ],
    }
    table = pd.DataFrame([
        {"板块": "教育", "底部至今": 25.56},
        {"板块": "贵金属", "底部至今": 35.09},
        {"板块": "能源金属", "底部至今": 17.38},
        {"板块": "软件开发", "底部至今": 16.10},
    ])
    stocks = {
        "usable": True,
        "coverage": 0.93,
        "rows": [
            {"code": "sh600001", "name": "领涨股份", "return": 50.0, "st": False}
        ],
    }

    summary = build_turning_summary(det, table, stocks)

    assert summary["current_phase"]["label"] == "箱体突破"
    assert summary["current_phase"]["turning_date"] == "2026-07-17"
    assert summary["current_phase"]["trading_days"] == 3
    assert summary["current_phase"]["index_return"] == 4.68
    assert [row["name"] for row in summary["turning_leaders"]["sectors"]] == [
        "贵金属", "教育", "能源金属"
    ]


def test_phase_micro_cycle_failure_does_not_remove_major_phase(monkeypatch):
    import phase_resonance

    monkeypatch.setattr(
        phase_resonance,
        "detect_micro_cycle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("fixture")),
        raising=False,
    )
    major = {
        "current_phase": {"label": "箱体突破"},
        "turning_leaders": {"sectors": [], "stocks": []},
    }
    result = phase_resonance._attach_micro_cycle(
        major,
        {
            "bottom": {"date": "2026-07-17", "close": 3764.0},
            "latest": {"date": "2026-08-07", "close": 3940.0},
            "index_series": [],
        },
        {},
    )

    assert result["current_phase"]["label"] == "箱体突破"
    assert result["micro_cycle"] == {}
    assert result["micro_chain"] == {}
    assert result["micro_resonance"] == {}
