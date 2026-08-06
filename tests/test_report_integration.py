# -*- coding: utf-8 -*-
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))



def test_publish_site_excludes_reports_after_report_date_from_index(tmp_path):
    from publish_site import publish

    site_dir = tmp_path / "site"
    reports_dir = site_dir / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "2026-08-05.html").write_text("old", encoding="utf-8")
    (reports_dir / "2026-08-07.html").write_text("future", encoding="utf-8")
    current_report = tmp_path / "report.html"
    current_report.write_text("current", encoding="utf-8")

    publish(
        str(current_report),
        str(site_dir),
        report_date=datetime(2026, 8, 6),
    )

    index_html = (site_dir / "index.html").read_text(encoding="utf-8")
    assert "2026-08-06" in index_html
    assert "2026-08-05" in index_html
    assert "2026-08-07" not in index_html
    assert 'href="reports/2026-08-06.html"' in index_html

def test_stock_level_progression_chain_and_height_summary():
    from review_metrics import build_progression_chain

    previous = [
        {"code": "600001", "name": "甲", "height": 3},
        {"code": "000002", "name": "乙", "height": 2},
        {"code": "920003", "name": "丙", "height": 2},
    ]
    current = [
        {"code": "600001", "name": "甲", "height": 4, "pct_change": 10.0},
        {"code": "000002", "name": "乙", "height": 0, "pct_change": 2.1},
        {"code": "920003", "name": "丙", "height": 0, "pct_change": -8.2},
    ]
    result = build_progression_chain(previous, current)
    by_code = {row["code"]: row for row in result["rows"]}
    assert by_code["sh600001"]["status"] == "promoted"
    assert by_code["sz000002"]["status"] == "broken_positive"
    assert by_code["bj920003"]["status"] == "broken_negative"
    h2 = result["by_height"][2]
    assert h2["sample_size"] == 2
    assert h2["promoted"] == 0
    assert h2["negative_feedback"] == 1
    assert h2["promotion_rate"] == 0


def test_daily_delta_snapshot_selects_material_changes():
    from review_metrics import build_daily_delta_snapshot

    current = {"max_height": 7, "limit_up": 88, "limit_down": 7, "breadth_ratio": 0.62, "ladder_integrity": 0.75, "concentration": 0.42, "promotion_rate": 0.31, "mainline_rank": ["机器人", "算力"]}
    previous = {"max_height": 5, "limit_up": 45, "limit_down": 12, "breadth_ratio": 0.41, "ladder_integrity": 0.52, "concentration": 0.30, "promotion_rate": 0.18, "mainline_rank": ["算力", "机器人"]}
    snapshot = build_daily_delta_snapshot(current, previous)
    assert snapshot["available"] is True
    assert snapshot["metrics"]["max_height"]["delta"] == 2
    assert snapshot["metrics"]["mainline_rank"]["changed"] is True
    assert 3 <= len(snapshot["highlights"]) <= 5


def test_daily_delta_snapshot_explains_missing_previous_day():
    from review_metrics import build_daily_delta_snapshot

    snapshot = build_daily_delta_snapshot({"max_height": 6}, None)
    assert snapshot["available"] is False
    assert "上一交易日" in snapshot["reason"]


def test_previous_daily_snapshot_ignores_same_day_and_future_files(tmp_path, monkeypatch):
    import 主线强度追踪 as report

    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    (snapshots / "2026-08-04.json").write_text('{"max_height": 4}', encoding="utf-8")
    (snapshots / "2026-08-06.json").write_text('{"max_height": 6}', encoding="utf-8")
    (snapshots / "2026-08-07.json").write_text('{"max_height": 7}', encoding="utf-8")
    monkeypatch.setattr(report, "DAILY_SNAPSHOT_DIR", str(snapshots))

    assert report._load_previous_daily_snapshot("2026-08-06") == {"max_height": 4}


def test_scenario_calibration_hides_probability_for_small_sample():
    from scenario_calibration import calibrate_scenario

    small = calibrate_scenario([1, 0, 1], predicted_probability=0.7, baseline_probability=0.5)
    assert small["publish_probability"] is False
    assert small["hit_rate"] is None

    enough = calibrate_scenario([1, 0, 1, 1, 0, 1, 1, 1, 0, 1, 1, 0], predicted_probability=0.7, baseline_probability=0.5)
    assert enough["publish_probability"] is True
    assert enough["sample_size"] == 12
    assert enough["confidence_interval"]["lower"] is not None
    assert enough["brier_score"] is not None


def test_ai_gate_skips_facts_only_and_sanitizes_observation():
    from report_logic import ReportPolicy
    from ai_rebound import run_guarded_ai

    calls = []
    def caller(payload):
        calls.append(payload)
        return {"facts": ["上涨家数 3000"], "observations": ["强度改善"], "conditions": ["覆盖完整"], "risks": [], "decision": "建议7成仓位加仓"}

    facts = run_guarded_ai({"breadth": 0.6}, ReportPolicy.from_mode("facts_only"), caller=caller)
    assert facts["status"] == "skipped"
    assert calls == []

    observation = run_guarded_ai({"breadth": 0.6}, ReportPolicy.from_mode("observation"), caller=caller)
    assert observation["status"] == "sanitized"
    assert observation["output"]["decision"] == ""
    assert calls[0]["schema_version"] == "report-facts/v1"


def test_ai_gate_preserves_retry_failure_reason(monkeypatch):
    import ai_rebound
    from report_logic import ReportPolicy

    calls = []

    class Response:
        status_code = 503
        text = "service unavailable"

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr(ai_rebound, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai_rebound.requests, "post", post)
    monkeypatch.setattr(ai_rebound.time, "sleep", lambda *_: None)

    result = ai_rebound.run_guarded_ai(
        {"breadth": 0.6}, ReportPolicy.from_mode("observation"), timeout=1,
    )

    assert len(calls) == 3
    assert result["status"] == "fallback"
    assert result["reason"] == "上游接口连续 3 次返回 503"
    assert result["lineage"]["attempt_count"] == 3
    assert result["lineage"]["http_status"] == 503

def test_audit_json_is_atomic_and_contains_lineage(tmp_path):
    from report_audit import write_report_audit

    target = tmp_path / "audit" / "2026-08-06.json"
    payload = write_report_audit(target, report_date="2026-08-06", context={"publication_mode": "facts_only"}, lineage={"universe": {"source": "eastmoney", "covered": 5200}})
    assert target.exists()
    assert not list(target.parent.glob("*.tmp"))
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["report_date"] == "2026-08-06"
    assert loaded["lineage"]["universe"]["source"] == "eastmoney"
    assert payload["fingerprint"] == loaded["fingerprint"]


def test_prediction_review_is_append_only_and_fills_t1_t3(tmp_path):
    from prediction_review import append_prediction, append_outcome, build_prediction_review

    path = tmp_path / "prediction_history.jsonl"
    prediction = append_prediction(path, {"prediction_id": "p1", "report_date": "2026-08-03", "probability": 0.6, "focus_pool": ["甲"]})
    append_outcome(path, "p1", "t1", {"market_up": True, "focus_hits": ["甲"]})
    append_outcome(path, "p1", "t3", {"market_up": False, "focus_hits": []})
    append_prediction(path, {"prediction_id": "p2", "report_date": "2026-08-06", "probability": 0.55, "focus_pool": []})
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["event_type"] for row in rows] == ["prediction", "outcome", "outcome", "prediction"]
    review = build_prediction_review(path)
    assert review["predictions"]["p1"]["outcomes"]["t1"]["market_up"] is True
    assert review["predictions"]["p1"]["outcomes"]["t3"]["market_up"] is False
    assert review["matured_count"] == 1
    assert review["pending_count"] == 1
    assert prediction["event_type"] == "prediction"


def test_prediction_is_not_duplicated_for_same_prediction_id(tmp_path):
    from prediction_review import append_prediction_once

    path = tmp_path / "prediction_history.jsonl"
    first = append_prediction_once(path, {
        "prediction_id": "2026-08-06:base",
        "report_date": "2026-08-06",
        "scene": "base",
    })
    second = append_prediction_once(path, {
        "prediction_id": "2026-08-06:base",
        "report_date": "2026-08-06",
        "scene": "base",
    })
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert first["appended"] is True
    assert second["appended"] is False
    assert len(rows) == 1


def test_html_policy_sanitizer_removes_blocked_semantics():
    from report_logic import sanitize_html_for_policy, scan_forbidden_semantics

    text = "概率 仓位 买入 卖出 加仓 减仓 清仓 锁仓 追高 空仓观望 离场 可追 回避"
    cleaned = sanitize_html_for_policy(text, "facts_only")
    assert scan_forbidden_semantics(cleaned, "facts_only") == []


def test_dashboard_reuses_report_context_without_reassessing(monkeypatch):
    import decision_dashboard
    from report_logic import ReportContext, ReportPolicy

    monkeypatch.setattr(
        decision_dashboard,
        "assess_data_quality",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("不得重复评估质量")),
    )
    context = ReportContext(
        report_date="2026-08-06",
        policy=ReportPolicy.from_mode("facts_only"),
        quality={"status": "blocked", "publication_mode": "facts_only"},
        facts={"market_state": {"publication_mode": "facts_only"}},
        daily_delta={"available": False, "reason": "缺少上一交易日结构化快照"},
        prediction_review={"prediction_count": 0},
        lineage={"universe": {"source": "eastmoney"}},
    ).to_dict()
    ctx = decision_dashboard.build_dashboard_ctx(
        advance_decline={"up": 1, "down": 1},
        report_context=context,
    )
    assert ctx["data_quality"] == context["quality"]
    assert ctx["publication_mode"] == "facts_only"
    assert ctx["date_str"] == "2026-08-06"
    assert ctx["daily_delta"] == context["daily_delta"]
    assert ctx["lineage"] == context["lineage"]


def test_dashboard_renders_review_closure_sections_from_report_context():
    import decision_dashboard
    from report_logic import ReportContext, ReportPolicy

    context = ReportContext(
        report_date="2026-08-06",
        policy=ReportPolicy.from_mode("facts_only"),
        quality={
            "status": "blocked",
            "publication_mode": "facts_only",
            "modules": {
                "universe": {"status": "ok", "coverage_pct": 100, "source": "eastmoney"},
                "breadth": {"status": "blocked", "coverage_pct": 62.5, "source": "cache"},
            },
        },
        facts={
            "market_state": {"publication_mode": "facts_only", "label": "数据待核验"},
            "progression_chain": {
                "available": True,
                "rows": [
                    {"code": "sh600001", "name": "甲", "previous_height": 3, "current_height": 4, "status": "promoted"},
                    {"code": "sz000002", "name": "乙", "previous_height": 2, "current_height": 0, "status": "broken_negative"},
                ],
                "by_height": {3: {"sample_size": 1, "promoted": 1, "promotion_rate": 1.0}},
            },
        },
        daily_delta={
            "available": True,
            "highlights": [
                {"label": "空间高度", "current": 4, "previous": 3, "delta": 1},
                {"label": "涨停家数", "current": 66, "previous": 52, "delta": 14},
            ],
        },
        prediction_review={"prediction_count": 2, "matured_count": 1, "pending_count": 1},
        lineage={"universe": {"source": "eastmoney", "coverage_pct": 100}},
    ).to_dict()
    ctx = decision_dashboard.build_dashboard_ctx(
        timing={"scene": "测试"},
        advance_decline={"up": 2000, "down": 3000, "zt": 66, "dt": 8},
        report_context=context,
    )
    html = decision_dashboard.generate_dashboard_html(ctx)

    assert "今日相对昨日" in html
    assert "昨日连板反馈" in html
    assert "数据来源与质量" in html
    assert "历史预测复盘" in html
    assert "甲" in html and "晋级" in html
    assert "eastmoney" in html



def test_dashboard_explains_limit_pool_reconciliation_and_degraded_scope():
    import decision_dashboard
    from report_logic import ReportContext, ReportPolicy

    modules = {
        "universe": {
            "status": "ok", "total": 5538, "covered": 5538,
            "coverage_pct": 100, "source": "security_master",
        },
        "price_raw": {
            "status": "degraded", "total": 5538, "covered": 5190,
            "coverage_pct": 93.72, "source": "tencent_close_snapshot",
        },
        "breadth": {
            "status": "degraded", "total": 5538, "covered": 5015,
            "coverage_pct": 90.56, "source": "price_cache",
        },
        "limit_pool": {
            "status": "ok", "total": 79, "covered": 79,
            "coverage_pct": 100, "source": "fupan_ladder",
            "lineage": {
                "reconciliation": {
                    "authoritative_count": 79,
                    "classified_count": 59,
                    "matched_count": 53,
                    "fupan_only_count": 26,
                    "cls_only_count": 6,
                    "classification_coverage_pct": 67.09,
                }
            },
        },
        "sector": {
            "status": "unavailable", "total": 79, "covered": 53,
            "coverage_pct": 67.09, "source": "CLS+Eastmoney concepts",
        },
        "echelon": {
            "status": "ok", "total": 79, "covered": 79,
            "coverage_pct": 100, "source": "fupan_ladder+CLS_attribution",
        },
        "history": {
            "status": "unavailable", "source": "report_daily_snapshots",
            "errors": ["缺少上一交易日结构化快照"],
        },
        "price_qfq": {
            "status": "unavailable", "total": 5538, "covered": 0,
            "coverage_pct": 0, "source": "legacy_price_cache",
            "errors": ["历史价格缓存仍为 raw/qfq 混合单列，禁止声明为完整前复权价格"],
        },
        "ai": {
            "status": "unavailable", "total": 1, "covered": 0,
            "coverage_pct": 0, "source": "guarded_ai",
            "errors": ["上游接口连续 3 次返回 503"],
        },
    }
    context = ReportContext(
        report_date="2026-08-06",
        policy=ReportPolicy.from_mode("observation"),
        quality={
            "status": "degraded",
            "publication_mode": "observation",
            "market_total": 5538,
            "market_covered": 5190,
            "coverage_pct": 93.72,
            "primary_source": "tencent_close_snapshot",
            "market_scope": "沪深北全A",
            "modules": modules,
        },
        facts={
            "market_state": {
                "publication_mode": "observation",
                "label": "数据降级",
                "reason": "部分非关键模块覆盖不足",
            },
        },
    ).to_dict()
    ctx = decision_dashboard.build_dashboard_ctx(
        timing={"scene": "测试"},
        advance_decline={"up": 2000, "down": 3000, "zt": 79, "dt": 8},
        report_context=context,
    )

    html = decision_dashboard.generate_dashboard_html(ctx)

    assert "当前可用范围：观察与条件触发" in html
    assert "涨停事实池：79 只" in html
    assert "题材归因命中：53 / 79（67.09%）" in html
    assert "尚未归因：26 只" in html
    assert "分类池独有：6 只" in html
    assert "事实池完整不等于题材归因完整" in html
    for label in ("证券主数据", "报告日价格", "市场宽度", "涨停事实池", "题材归因", "连板梯队", "前复权价格", "AI 研判"):
        assert label in html
    assert "AI 研判未生成" in html
    assert "连续 3 次返回 503" in html
    assert "不以规则文本冒充 AI 输出" in html
    assert "缺少上一交易日结构化快照；历史 HTML 不作为计算源" in html
    assert "<b>limit_pool</b>" not in html
    assert "<b>price_raw</b>" not in html
def test_report_entrypoint_passes_one_context_to_all_report_surfaces():
    source = (ROOT / "src" / "主线强度追踪.py").read_text(encoding="utf-8")
    assert "report_context=None" in source
    assert source.count("report_context=_report_context") >= 2
    assert "sanitize_html_for_policy(html, policy)" in source
    assert "write_report_audit(" in source
    assert "append_prediction_once(" in source


def test_scenario_probability_requires_real_minimum_history():
    from report_logic import build_scenario_probabilities

    no_history = build_scenario_probabilities(
        scene="E_主升加速", ad_ratio=0.8, zt=120, dt=2,
        curr_h=7, pressure_5d=6, ladder=15, h5=2,
        data_quality={"status": "ok"},
    )
    assert all(row["probability"] is None for row in no_history)
    assert all(row["probability_kind"] == "insufficient_history" for row in no_history)

    enough = build_scenario_probabilities(
        scene="E_主升加速", ad_ratio=0.8, zt=120, dt=2,
        curr_h=7, pressure_5d=6, ladder=15, h5=2,
        data_quality={"status": "ok"},
        historical_stats={
            "A": {"successes": 8, "trials": 12},
            "B": {"successes": 6, "trials": 12},
            "C": {"successes": 5, "trials": 12},
            "D": {"successes": 3, "trials": 12},
        },
    )
    assert all(row["probability"] is not None for row in enough)
    assert all(row["probability_kind"] == "historical_rate" for row in enough)
    assert enough[0]["probability"] == 8 / 12
    assert enough[0]["model_weight"] is not None

def test_dashboard_non_decision_outputs_are_semantically_clean():
    import decision_dashboard
    from report_logic import ReportContext, ReportPolicy, scan_forbidden_semantics

    for mode, status in (("facts_only", "blocked"), ("observation", "degraded")):
        context = ReportContext(
            report_date="2026-08-06",
            policy=ReportPolicy.from_mode(mode),
            quality={"status": status, "publication_mode": mode},
            facts={"market_state": {"publication_mode": mode, "label": "数据待核验"}},
        ).to_dict()
        ctx = decision_dashboard.build_dashboard_ctx(
            timing={"scene": "测试", "position": "7成仓位", "action": "加仓"},
            advance_decline={"up": 2000, "down": 3000, "zt": 66, "dt": 8},
            report_context=context,
        )
        standalone = decision_dashboard.generate_dashboard_html(ctx)
        embedded = decision_dashboard.generate_dashboard_section(ctx)
        assert scan_forbidden_semantics(standalone, mode) == []
        assert scan_forbidden_semantics(embedded, mode) == []
        expected_copy = "当前仅展示已校验事实" if mode == "facts_only" else "当前展示条件性观察"
        assert expected_copy in standalone
        assert expected_copy in embedded
        assert "不发布" not in standalone
        assert "不发布" not in embedded


def test_dashboard_reuses_canonical_ladder_metrics_from_report_context():
    from decision_dashboard import build_dashboard_ctx, generate_dashboard_html
    from report_logic import compute_ladder_metrics, build_lianban_review

    previous = [
        {"code": "600001", "height": 2},
        {"code": "000002", "height": 3},
    ]
    current = [
        {"code": "600001", "height": 3},
        {"code": "000002", "height": 0},
    ]
    canonical = compute_ladder_metrics(current, previous_echelon=previous)
    report_context = {
        "report_date": "2026-08-06",
        "facts": {
            "ladder_metrics": canonical,
            "lianban_review": build_lianban_review(canonical),
        },
    }

    # 传入空梯队，模拟看板侧拿到裁剪数据；仍应以主报告事实为准。
    ctx = build_dashboard_ctx(
        timing={"scene": "中性震荡", "action": "观察", "level": "观察"},
        advance_decline={
            "up": 3000, "down": 2000, "zt": 50, "dt": 5,
            "zt_max_height": 3, "market_total": 5000, "market_covered": 5000,
        },
        echelon=[], previous_echelon=[], report_date="2026-08-06",
        report_context=report_context,
    )

    assert ctx["ladder_metrics"]["streak_pool_promotion"]["text"] == "1/2（50%）"
    html = generate_dashboard_html(ctx)
    assert "昨日连板池晋级率（高度≥2）：50%（1/2）" in html
    assert "昨日连板池晋级率（高度≥2）：样本不足（0/0）" not in html
