# -*- coding: utf-8 -*-
"""针对抱团周期与新周期试错分析模块 (baotuan_cycle) 的单元测试。"""
import os
import sys
from copy import deepcopy
from pathlib import Path
import pytest
import pandas as pd

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


@pytest.fixture
def result():
    from crowding_cycle import build_crowding_report

    research = build_crowding_report('2026-10-22', prices=pd.DataFrame(), limits=pd.DataFrame(),
                                     sentiment=pd.DataFrame(), calendar=[], quotes={})
    return build_baotuan_cycle(research_report=research)


def test_build_baotuan_cycle_structure(result):
    """测试 build_baotuan_cycle 核心模型返回结构与字段。"""
    res = result
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
    assert res['current_stage'] == '数据不足 / 未知'
    assert res['market_temp_score'] is None
    assert res['cleansing_score'] is None
    assert res['today_trials'] == []
    assert res['today_discarded'] == []
    assert res['known_at'] is None


def test_stale_evidence_does_not_advertise_today_trials(result):
    research = deepcopy(result['research_report'])
    research['quality'] = 'stale'
    research['daily'] = [dict(date='2026-10-22', stage='候选竞争', coverage_ok=True,
                              top=[dict(code='sh600127', name='测试股')])]
    res = build_baotuan_cycle(research_report=research)
    assert res['current_stage'] == '数据不足 / 未知'
    assert res['today_trials'] == []
    # 底层价格研究仍保留，但不能借兼容入口升级成当日结论。
    assert res['research_report']['daily'][0]['top']


def test_compatibility_stage_is_derived_only_from_research_engine(result):
    research = deepcopy(result['research_report'])
    research['quality'] = 'research_only'
    research['daily'] = [dict(date='2026-10-22', stage='等待', coverage_ok=True, top=[])]
    res = build_baotuan_cycle(research_report=research)
    assert res['current_stage'] == '等待'
    assert res['current_cycle'] == {}
    assert res['market_temp_score'] is res['cleansing_score'] is None


def test_historical_case_visibility_is_not_fabricated_publication_time(result):
    original = deepcopy(BAOTUAN_HISTORICAL_CYCLES)
    for case in result['cycles']:
        assert case['visibility_date'] <= result['report_date_fmt']
        assert case['claim_as_of'] <= case['visibility_date']
        assert case['known_at'] is None
        assert case['knowledge_status'] == 'retrospective_visibility_boundary'
        assert '最新' not in case['name']
    research = deepcopy(result['research_report'])
    research['requested_date'] = '2026-09-21'
    assert build_baotuan_cycle(research_report=research)['cycles'] == []
    assert BAOTUAN_HISTORICAL_CYCLES == original


def test_mismatched_research_and_export_dates_fail_before_writes(tmp_path, result):
    with pytest.raises(ValueError, match='截止日'):
        build_baotuan_cycle('2026-10-21', research_report=result['research_report'])
    output_dir = tmp_path / 'output'
    with pytest.raises(ValueError, match='请求日'):
        export_baotuan_standalone_reports('2026-10-21', output_dir=str(output_dir),
                                          site_dir=str(tmp_path / 'site'), baotuan_data=result)
    assert not list(output_dir.glob('*.html'))


def test_older_compatibility_export_does_not_replace_latest(tmp_path, result):
    output_dir, site_dir = tmp_path / 'output', tmp_path / 'site'
    export_baotuan_standalone_reports(output_dir=str(output_dir), site_dir=str(site_dir), baotuan_data=result)
    latest = site_dir / 'baotuan' / 'latest.html'
    original = latest.read_bytes()
    research = deepcopy(result['research_report'])
    research['requested_date'] = '2026-09-21'
    old = build_baotuan_cycle(research_report=research)
    export_baotuan_standalone_reports(output_dir=str(output_dir), site_dir=str(site_dir), baotuan_data=old)
    assert latest.read_bytes() == original
    assert (site_dir / 'baotuan' / '2026-09-21.html').exists()


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


def test_render_baotuan_teaser_html(result):
    """测试主报告入口卡渲染。"""
    res = result
    teaser = render_baotuan_teaser_html(res)
    assert isinstance(teaser, str)
    assert "抱团周期演进与新周期试错雷达" in teaser
    assert "baotuan/latest.html" in teaser
    assert "操盘心法与定性" in teaser


def test_generate_baotuan_html(result):
    """测试独立专题页 HTML 渲染。"""
    res = result
    html_page = generate_baotuan_html(res, is_site_mode=True)
    assert isinstance(html_page, str)
    assert "<!doctype html>" in html_page.lower()
    assert "抱团周期全景演进" in html_page
    assert '历史核心不等于当前有效核心' in html_page
    assert '事后叙事，不参与当日价格判断' in html_page
    assert '推定，非真实发布时点' in html_page
    assert '修复路径' in html_page
    assert "时代万恒" in html_page
    assert "善水科技" in html_page
    assert "近岸蛋白" in html_page


def test_export_baotuan_standalone_reports(tmp_path, result):
    """测试独立报告导出功能。"""
    out_dir = str(tmp_path / "output")
    site_dir = str(tmp_path / "site")
    paths = export_baotuan_standalone_reports(
        output_dir=out_dir,
        site_dir=site_dir,
        baotuan_data=result,
    )
    assert os.path.exists(paths["local_report"])
    assert os.path.exists(paths["site_latest"])
    assert os.path.exists(paths["site_dated"])
    assert os.path.exists(paths["tracking_snapshots"])
    assert "dynamic-tracking" in Path(paths["local_report"]).read_text(encoding="utf-8")
