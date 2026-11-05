# -*- coding: utf-8 -*-
"""验证抱团个股与抱团转债明日操作预案的动态性与指令完整性."""
import pytest
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from next_day_plan import (
    _select_tomorrow_cb_action_targets,
    _select_tomorrow_focus_targets,
    generate_plan_html,
    build_next_day_plan,
)


def test_select_tomorrow_cb_action_targets():
    """验证重点抱团转债动态预案提取与指令完整性."""
    cb_targets = _select_tomorrow_cb_action_targets("2026-11-05")
    assert len(cb_targets) > 0, "2026-11-05 应该成功提取到抱团转债预案"
    
    first_cb = cb_targets[0]
    assert "bond_name" in first_cb
    assert "bond_code" in first_cb
    assert "action" in first_cb
    assert "stop" in first_cb
    assert "stock_name" in first_cb
    assert "T+0" in first_cb["action"] or "指令" in first_cb["action"]
    assert "止损" in first_cb["stop"] or "撤销" in first_cb["stop"]


def test_select_tomorrow_focus_targets_includes_cb():
    """验证 focus_targets 包含个股与转债预案."""
    plan_mock = {
        "report_date": "2026-11-05",
        "ladder": [
            {"height": 5, "stocks": ["龙头股份"]},
            {"height": 3, "stocks": ["先锋科技"]},
        ],
        "decision": {
            "candidates": [{"name": "中军容量", "code": "600000", "trigger": "均线低吸", "invalid": "破位止损"}]
        },
        "avoid": [{"name": "退潮妖股", "height": 6, "code": "000001"}],
    }
    target_data = _select_tomorrow_focus_targets("2026-11-05", plan_mock)
    assert "focus_targets" in target_data
    assert "avoid_targets" in target_data
    assert "cb_targets" in target_data

    focus = target_data["focus_targets"]
    assert any("龙头股份" in f["name"] for f in focus)
    assert any("先锋科技" in f["name"] for f in focus)

    avoid = target_data["avoid_targets"]
    assert any("退潮妖股" in a["name"] for a in avoid)

    cbs = target_data["cb_targets"]
    assert len(cbs) > 0


def test_plan_html_renders_cb_and_stock_action_plan():
    """验证生成的预案 HTML 中包含抱团个股与抱团转债作战表格和动态晨会内参."""
    plan_mock = {
        "report_date": "2026-11-05",
        "target_date": "2026-11-06",
        "facts": {"breadth_ratio": 0.55, "limit_up": 60, "limit_down": 3, "promotion_rate": 0.35},
        "decision": {
            "position": "1.5~2.0成",
            "candidates": [{"name": "中军大票", "code": "600111", "trigger": "低吸", "invalid": "止损"}],
            "gates": [{"title": "第一道开关", "headline": "定仓位", "detail": "分批试错", "check": "确认承接"}],
        },
        "ladder": [{"height": 3, "stocks": ["标杆A"]}],
        "avoid": [{"name": "断板B", "height": 4, "code": "000222"}],
    }
    html = generate_plan_html(plan_mock, is_site_mode=False)
    
    # 验证抱团个股清单与操作指令
    assert "🎯 明日核心重点关注标的清单" in html
    assert "明确买入执行结果指令" in html
    assert "标杆A" in html

    # 验证重点抱团转债 T+0 实战作战预案表格
    assert "重点抱团可转债 · 明日 T+0 深度实战作战预案" in html
    assert "联动正股" in html
    assert "明日竞价预期与 T+0 操盘指令" in html
    assert "硬性止损与撤退底线" in html

    # 验证晨会内参一键复制脚本包含动态标的
    assert "function copyMorningPlanDigest" in html
    assert "标杆A" in html or "中军大票" in html
