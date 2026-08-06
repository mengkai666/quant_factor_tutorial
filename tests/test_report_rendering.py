from decision_dashboard import build_dashboard_ctx, generate_dashboard_html, generate_dashboard_section
from data_sources.models import FetchResult
from data_sources.name_resolver import NameResolution
from data_sources.quality_gate import QualityIssue, QualityReport


def _context():
    return build_dashboard_ctx(
        timing={"scene": "旧场景", "action": "旧动作", "position": "5成仓位"},
        advance_decline={"up": 3500, "down": 1500, "zt": 90, "dt": 2},
        regime={
            "code": "BROAD_STRONG", "title": "普涨反弹 · 梯队健康",
            "action": "只做主线确认，后排不追", "color": "#f85149",
            "reason": "上涨占比 0.700",
        },
        data_quality={
            "ok": False,
            "name_conflicts": 1,
            "limit_pool_status": "partial",
            "limit_pool_source": "ZT:akshare_em|CHECK:ZT:eastmoney_push2ex",
            "notes": ["涨停数量差异"],
        },
        ladder_review={
            "distribution": {1: 60, 2: 25, 3: 1, 4: 2, 8: 1},
            "promotions": {1: {"eligible": 121, "advanced": 25, "rate": 0.207}},
            "high_break_count": 1,
            "missing_heights": [5, 6, 7],
        },
        report_date="2026-08-05",
    )


def test_dashboard_surfaces_quality_before_research_evidence():
    html = generate_dashboard_html(_context())

    assert "数据可信度" in html
    assert "名称冲突 1" in html
    assert "partial" in html
    assert "ZT:akshare_em" in html
    assert "晋级率" in html
    assert "20.7%" in html
    assert html.index("普涨反弹 · 梯队健康") < html.index("数据佐证")


def test_embedded_dashboard_contains_same_quality_summary():
    html = generate_dashboard_section(_context())

    assert "数据可信度" in html
    assert "涨停数量差异" in html


def test_legacy_quality_metadata_combines_gate_names_and_limit_source():
    import legacy_tracker

    quality = QualityReport("2026-08-05", [
        QualityIssue("warning", "sample", "warning note"),
    ])
    limit_result = FetchResult.partial(
        dataset="limit_pool", date="2026-08-05", source="ZT:akshare_em|CHECK:ZT:eastmoney_push2ex",
        expected_count=90, actual_count=90, message="count drift",
    )
    names = NameResolution(
        names={"sz003032": "传智教育"}, sources={"sz003032": "limit_pool"},
        conflicts=[{"code": "sz003032"}],
    )

    result = legacy_tracker._report_quality_metadata(quality, names, limit_result)

    assert result["ok"] is False
    assert result["name_conflicts"] == 1
    assert result["limit_pool_status"] == "partial"
    assert "count drift" in result["notes"]


def test_full_report_collapses_detailed_research_layer(monkeypatch, tmp_path):
    import legacy_tracker
    import pandas as pd

    output = tmp_path / "report.html"
    monkeypatch.setattr(legacy_tracker, "OUTPUT_HTML", str(output))
    empty = pd.DataFrame()
    legacy_tracker.generate_html(
        ml_strength=empty, sub_strength=empty, ml_ma={}, sub_ma={},
        ml_thresh={}, sub_thresh={}, leaders={}, dates=["20260805"],
        ratings={}, sub_ratings={}, echelon=[], top30_data={},
        advance_decline={"up": 2500, "down": 2500, "zt": 0, "dt": 0},
        sentiment_df=empty, classified_df=empty, price_df=empty,
        data_quality={"ok": True, "name_conflicts": 0},
    )

    html = output.read_text(encoding="utf-8")
    assert '<details class="research-layer">' in html
    assert "展开研究证据" in html
    assert "min-width:420px" not in html
    assert ".research-body {" in html


def test_dashboard_does_not_render_static_probability_claims():
    html = generate_dashboard_html(_context())

    forbidden = (
        "胜率 <40%", "次日崩塌概率 39%", "2板仅 33%", "晋级率 45-50%",
        "周四高潮易引周五崩(56%)", "崩塌 35%", "周一 66% 概率反弹",
        "历史 T+3 破新高 55%", "胜率均为经验概率",
    )
    assert not [phrase for phrase in forbidden if phrase in html]


def test_outcome_rate_is_not_labeled_as_next_day_scenario_probability():
    ctx = _context()
    ctx["scenario_stats"] = {
        "breakout": {"sample_count": 80, "win_rate": 0.3, "horizon": 3, "min_samples": 10},
        "continuation": {"sample_count": 10, "win_rate": 0.7, "horizon": 3, "min_samples": 10},
    }

    html = generate_dashboard_html(ctx)

    assert "明日基准情形" not in html
    assert "基准情形已高亮" not in html
    assert "重点 · 当前策略" in html
