import pandas as pd

from data_sources.market_regime import classify_market_regime


def test_strong_breadth_with_broken_high_ladder_is_one_regime():
    result = classify_market_regime(
        {"up": 3584, "down": 1270, "zt": 89, "dt": 1},
        pd.DataFrame({"up": [1200, 2600, 3584], "down": [3000, 2200, 1270]}),
        [{"height": "8连板"}, {"height": "4连板"}, {"height": "3连板"}, {"height": "首板"}],
    )

    assert result["code"] == "BROAD_STRONG_HIGH_GAP"
    assert result["title"] == "普涨反弹 · 高位分化"
    assert result["action"] == "只做前排确认，不追孤峰"


def test_incomplete_data_takes_precedence_over_market_signal():
    result = classify_market_regime(
        {"up": 3584, "down": 1270, "ad_incomplete": True},
        None,
        [{"height": "8连板"}],
    )

    assert result["code"] == "DATA_UNCERTAIN"
    assert result["title"] == "数据未确认"
    assert "发布" in result["action"]


def test_balanced_breadth_is_structural_watch():
    result = classify_market_regime(
        {"up": 2500, "down": 2500, "zt": 50, "dt": 5},
        None,
        [{"height": "3连板"}, {"height": "2连板"}, {"height": "首板"}],
    )

    assert result["code"] == "STRUCTURAL_WATCH"


def test_dashboard_uses_unified_regime_title_over_timing_scene():
    from decision_dashboard import build_dashboard_ctx, generate_dashboard_html

    ctx = build_dashboard_ctx(
        timing={"scene": "中性震荡", "action": "结构博弈", "level": "中性"},
        advance_decline={"up": 3500, "down": 1500},
        regime={
            "code": "BROAD_STRONG_HIGH_GAP",
            "title": "普涨反弹 · 高位分化",
            "color": "#ff8800",
            "action": "只做前排确认，不追孤峰",
        },
        report_date="2026-08-05",
    )

    html = generate_dashboard_html(ctx)

    assert "普涨反弹 · 高位分化" in html
    assert "只做前排确认，不追孤峰" in html


def test_default_scenarios_do_not_claim_fixed_probabilities():
    from decision_dashboard import _default_scenarios

    scenarios = _default_scenarios(8, 7, focus_df=None)

    assert all("%" not in str(item["prob"]) for item in scenarios)


def test_market_stance_uses_regime_as_display_truth():
    from market_stance import classify_market_stance

    regime = {
        "title": "普涨反弹 · 高位分化",
        "color": "#ff8800",
        "action": "只做前排确认，不追孤峰",
        "reason": "上涨占比强但梯队断层",
    }
    result = classify_market_stance(
        {"up": 3500, "down": 1500, "zt": 90, "dt": 2},
        pd.DataFrame({"up": [2500, 3000, 3500], "down": [2500, 2000, 1500]}),
        [{"height": "8连板"}, {"height": "4连板"}, {"height": "首板"}],
        regime=regime,
    )

    assert result["stance"] == regime["title"]
    assert result["play"] == regime["action"]
