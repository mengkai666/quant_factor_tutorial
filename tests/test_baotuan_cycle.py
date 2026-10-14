# -*- coding: utf-8 -*-
"""针对抱团周期与新周期试错分析模块 (baotuan_cycle) 的单元测试。"""
import os
import sys
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, '..', 'src'))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from baotuan_cycle import (
    build_baotuan_cycle,
    render_baotuan_teaser_html,
    generate_baotuan_html,
    export_baotuan_standalone_reports,
    BAOTUAN_HISTORICAL_CYCLES,
)


def test_build_baotuan_cycle_structure():
    """测试 build_baotuan_cycle 核心模型返回结构与字段。"""
    res = build_baotuan_cycle()
    assert isinstance(res, dict)
    assert res.get("status") == "ok"
    assert "report_date" in res
    assert "current_stage" in res
    assert "stage_badge" in res
    assert "tactical_guidance" in res
    assert "cycles" in res
    assert len(res["cycles"]) >= 4
    assert "stats" in res
    assert isinstance(res["stats"], dict)


def test_historical_cycles_content():
    """测试历史周期知识库中各周期的核心要素。"""
    assert len(BAOTUAN_HISTORICAL_CYCLES) >= 4
    # 验证周期一：消费农业接力
    cycle_c = [c for c in BAOTUAN_HISTORICAL_CYCLES if c["id"] == "cycle_consumer_agri"][0]
    assert "国芳集团" in str(cycle_c)
    assert "百大集团" in str(cycle_c)
    assert "桂林旅游" in str(cycle_c)
    assert "判死刑" in str(cycle_c["discarded_sectors"])

    # 验证周期三：9.28老抱团出清与新周期试错
    cycle_trial = [c for c in BAOTUAN_HISTORICAL_CYCLES if c["id"] == "cycle_current_chaos_trial"][0]
    assert "时代万恒" in str(cycle_trial)
    assert "善水科技" in str(cycle_trial)
    assert "近岸蛋白" in str(cycle_trial)
    assert "环境太差" in str(cycle_trial["anchor_event"])


def test_render_baotuan_teaser_html():
    """测试主报告入口卡渲染。"""
    res = build_baotuan_cycle()
    teaser = render_baotuan_teaser_html(res)
    assert isinstance(teaser, str)
    assert "抱团周期演进与新周期试错雷达" in teaser
    assert "baotuan/latest.html" in teaser
    assert "操盘心法与定性" in teaser


def test_generate_baotuan_html():
    """测试独立专题页 HTML 渲染。"""
    res = build_baotuan_cycle()
    html_page = generate_baotuan_html(res, is_site_mode=True)
    assert isinstance(html_page, str)
    assert "<!DOCTYPE html>" in html_page
    assert "抱团周期全景演进" in html_page
    assert "9.28 瓦解出清后的新周期试错方向全景矩阵" in html_page
    assert "科技类为何画大红叉" in html_page
    assert "时代万恒" in html_page
    assert "善水科技" in html_page
    assert "近岸蛋白" in html_page


def test_export_baotuan_standalone_reports(tmp_path):
    """测试独立报告导出功能。"""
    out_dir = str(tmp_path / "output")
    site_dir = str(tmp_path / "site")
    paths = export_baotuan_standalone_reports(
        output_dir=out_dir,
        site_dir=site_dir,
    )
    assert os.path.exists(paths["local_report"])
    assert os.path.exists(paths["site_latest"])
    assert os.path.exists(paths["site_dated"])
