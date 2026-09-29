import pytest
from pullback_tracker import (
    PULLBACK_QUANT_RULES,
    CURRENT_PULLBACK_CANDIDATES,
    render_pullback_tracker_panel,
)


def test_pullback_quant_rules_are_complete():
    """验证四大定量规则完整性与核心参数."""
    assert "rule_impulse" in PULLBACK_QUANT_RULES
    assert "rule_pullback" in PULLBACK_QUANT_RULES
    assert "rule_health_levels" in PULLBACK_QUANT_RULES
    assert "rule_execution" in PULLBACK_QUANT_RULES

    # 规则一放量主升参数
    r1 = PULLBACK_QUANT_RULES["rule_impulse"]
    assert r1["min_relative_strength_rs"] >= 115.0
    assert r1["volume_expansion_ratio"] >= 1.30

    # 规则二回调一笔参数
    r2 = PULLBACK_QUANT_RULES["rule_pullback"]
    assert r2["pullback_min_pct"] <= 4.0
    assert r2["pullback_max_pct"] >= 10.0
    assert r2["volume_shrink_min_pct"] >= 30.0

    # 规则三健康等级三级分类
    levels = PULLBACK_QUANT_RULES["rule_health_levels"]["levels"]
    level_names = [l["level"] for l in levels]
    assert "Level A" in level_names
    assert "Level B" in level_names
    assert "Level C" in level_names


def test_candidates_have_required_fields_and_valid_health_levels():
    """验证当前跟踪池板块字段与健康评级有效性."""
    assert len(CURRENT_PULLBACK_CANDIDATES) >= 3
    valid_levels = {"Level A", "Level B", "Level C"}

    for cand in CURRENT_PULLBACK_CANDIDATES:
        assert cand["sector_name"]
        assert cand["health_level"] in valid_levels
        assert cand["matched_tactics"]
        assert cand["action_conclusion"]
        assert cand["core_stocks"]


def test_render_pullback_tracker_panel_returns_html_and_escapes():
    """验证 HTML 组件渲染，包含各模块且转义安全."""
    html = render_pullback_tracker_panel(report_date="2026-09-28")
    assert "<section class=\"pullback-tactic-tracker\"" in html
    assert "放量主升甄别规则" in html
    assert "健康缩量回调一笔规则" in html
    assert "Level A" in html
    assert "Level B" in html
    assert "Level C" in html
    assert "AI算力硬件" in html
    assert "雪龙集团" in html


def test_generate_pullback_standalone_html():
    """验证独立报告页面生成器渲染完整 HTML 文档与导航条."""
    from pullback_tracker import generate_pullback_standalone_html

    local_html = generate_pullback_standalone_html("2026-09-28", is_site_mode=False)
    assert "<!doctype html>" in local_html
    assert "强势板块·放量主升回调一笔·量化跟踪与战法结合" in local_html
    assert "本地导航入口.html" in local_html
    assert "今日复盘与明日预案_最新.html" in local_html
    assert "临盘分时触发与熔断检查表" in local_html

    site_html = generate_pullback_standalone_html("2026-09-28", is_site_mode=True)
    assert "../index.html" in site_html
    assert "../dashboards/latest.html" in site_html


def test_export_pullback_standalone_reports(tmp_path):
    """验证独立报告导出生成各目标文件."""
    from pullback_tracker import export_pullback_standalone_reports

    out_dir = str(tmp_path / "output")
    res = export_pullback_standalone_reports("2026-09-28", output_dir=out_dir)
    assert "local_latest" in res
    assert "site_latest" in res
    import os
    assert os.path.exists(res["local_latest"])
    assert os.path.exists(res["site_latest"])

