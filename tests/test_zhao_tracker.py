"""测试属于zhao的个股追踪专属模块 (zhao_tracker.py)."""
import os
from pathlib import Path
import pytest

from paths import OUTPUT_DIR, SITE_DIR
from zhao_tracker import (
    ZHAO_PORTFOLIO_CONFIG,
    generate_zhao_tracker_data,
    generate_zhao_standalone_html,
    export_zhao_standalone_reports,
    render_zhao_teaser_html,
)


def test_zhao_portfolio_config_completeness():
    """验证持仓股票配置完整覆盖 6 只核心自选标的且字段齐全."""
    assert len(ZHAO_PORTFOLIO_CONFIG) == 6
    expected_codes = {"sh603980", "sz000536", "sz000612", "sh601139", "sz002826", "sz300798"}
    actual_codes = {item["code"] for item in ZHAO_PORTFOLIO_CONFIG}
    assert actual_codes == expected_codes

    for item in ZHAO_PORTFOLIO_CONFIG:
        assert item["name"]
        assert item["industry"]
        assert item["mainline"]
        assert item["theme"]
        assert item["tactic_model"]
        assert item["core_role"]
        assert item["defensive_support"] > 0
        assert item["stop_loss_line"] > 0
        assert item["key_resistance"] > item["defensive_support"]


def test_generate_zhao_tracker_data_structure():
    """验证数据生成与缠论/战法指标结构合规."""
    data = generate_zhao_tracker_data("2026-09-28")
    assert "report_date" in data
    assert "dossiers" in data
    assert len(data["dossiers"]) == 6

    for dos in data["dossiers"]:
        assert "config" in dos
        assert "quote" in dos
        assert "chan" in dos
        assert "review_summary" in dos
        assert "chan_detailed" in dos
        assert "tactic_detailed" in dos
        assert "scenarios" in dos
        assert len(dos["scenarios"]) == 3
        assert "action_plan" in dos
        assert len(dos["action_plan"]["operation_matrix"]) == 3


def test_generate_zhao_standalone_html_content():
    """验证生成的 HTML 包含完整复盘推演、缠论与操作预案卡片."""
    html_content = generate_zhao_standalone_html("2026-09-28", is_site_mode=False)
    assert "<!doctype html>" in html_content
    assert "属于zhao的个股追踪" in html_content
    assert "每日走势深度复盘" in html_content
    assert "缠论多级别结构透视" in html_content
    assert "游资量价战法攻防" in html_content
    assert "明日三大走势推演" in html_content
    assert "实战操盘预案矩阵" in html_content
    assert "copyMorningBrief" in html_content

    # 验证6只股票均存在
    for item in ZHAO_PORTFOLIO_CONFIG:
        assert item["name"] in html_content
        assert item["code"] in html_content


def test_export_zhao_standalone_reports_generation(tmp_path):
    """验证导出函数生成本地和站点独立 HTML 文件."""
    out_dir = tmp_path / "output"
    site_dir = tmp_path / "site"
    res = export_zhao_standalone_reports("2026-09-28", output_dir=str(out_dir), site_dir=str(site_dir))

    assert Path(res["local_latest"]).exists()
    assert Path(res["local_dated"]).exists()
    assert Path(res["site_latest"]).exists()
    assert Path(res["site_dated"]).exists()

    assert Path(res["local_latest"]).stat().st_size > 5000
    assert Path(res["site_latest"]).stat().st_size > 5000


def test_chan_structure_is_computed_not_preset():
    """缠论结论来自 chan_engine 计算: 笔/中枢/买卖点气泡都与手写 chan_verdict 无关 (离线)."""
    import math
    import pandas as pd
    from zhao_tracker import HAVE_CHAN, build_stock_chart_dataset, compute_chan_structure, preset_agrees

    assert HAVE_CHAN, "缠论内核必须可导入 (原 czsc 路径不存在导致一直走兜底)"
    n = 250
    df = pd.DataFrame({
        'dt': pd.bdate_range('2025-01-02', periods=n),
        'close': [100 + 8 * math.sin(i / 4) + 0.05 * i for i in range(n)],
    })
    df['open'] = df['close'].shift(1).fillna(df['close'])
    df['high'] = df[['open', 'close']].max(axis=1) + 0.6
    df['low'] = df[['open', 'close']].min(axis=1) - 0.6
    df['vol'] = 1e5
    chan = compute_chan_structure(df, 'sh603980')
    assert chan is not None and chan['n_bi'] >= 10 and chan['stage']

    cfg = dict(ZHAO_PORTFOLIO_CONFIG[0])
    cfg['chan_verdict'] = '日线标准三买确立'          # 预设写什么都不应影响图上的结构
    chart = build_stock_chart_dataset(df, cfg, {}, chan=chan)
    assert chart['chan_source'] == 'computed'
    assert len(chart['dates']) == 120 and len(chart['bi_points']) >= 4
    assert all(p[0] >= chart['dates'][0] for p in chart['bi_points'])
    for m in chart['mark_points']:
        assert m['value'] in chan['stage']            # 气泡只来自计算阶段

    assert preset_agrees('日线标准三买确立', '三买·主升确认')
    assert not preset_agrees('日线中枢下轨二买', '三卖·下跌确认')
    assert not preset_agrees('三买蓄势回抽 (中枢上沿)', '中枢震荡·下半')   # "中枢"泛词不算一致
    assert not preset_agrees('一买底背驰', '顶背驰·减仓区')               # 底背驰 ≠ 顶背驰


def test_render_zhao_teaser_html():
    """验证主报告入口卡片渲染."""
    teaser = render_zhao_teaser_html("2026-09-28")
    assert "属于zhao的个股追踪" in teaser
    assert "zhao个股追踪_最新.html" in teaser
    assert "吉华集团" in teaser
