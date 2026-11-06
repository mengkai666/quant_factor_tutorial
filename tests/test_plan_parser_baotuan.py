# -*- coding: utf-8 -*-
"""验证 APP 端预案解析层 (plan_parser) 固化挂载抱团个股与转债预案."""
import pytest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from plan_parser import (
    DEFAULT_BAOTUAN_STOCKS,
    DEFAULT_BAOTUAN_CBS,
    parse_plan_html,
    synthesize_plan,
    get_plan_for_date,
)


def test_default_constants():
    """验证系统预案标准常量完整性."""
    assert len(DEFAULT_BAOTUAN_STOCKS) == 4
    roles = [s["role"] for s in DEFAULT_BAOTUAN_STOCKS]
    assert any("空间龙" in r for r in roles)
    assert any("梯队先锋" in r for r in roles)
    assert any("核心产业大中军" in r for r in roles)
    assert any("禁买出清黑名单" in r for r in roles)

    assert len(DEFAULT_BAOTUAN_CBS) == 4
    cb_names = [c["bond_name"] for c in DEFAULT_BAOTUAN_CBS]
    assert "福新转债" in cb_names
    assert "奥普转债" in cb_names
    assert "精测转2" in cb_names
    assert "澳弘转债" in cb_names


def test_synthesize_plan():
    """验证自动合成预案包含抱团个股与转债预案及晨会内参."""
    plan = synthesize_plan("2026-11-06", 15, "极度冰点", [], dt_count=18)
    assert "baotuan_stock_targets" in plan
    assert len(plan["baotuan_stock_targets"]) == 4
    assert "baotuan_cb_targets" in plan
    assert len(plan["baotuan_cb_targets"]) == 4

    card = plan.get("morning_card", "")
    assert "【重点抱团个股 · 实战预案指令】" in card
    assert "【重点抱团可转债 · 明日 T+0 深度作战预案】" in card
    assert "福新转债" in card


def test_get_plan_for_date():
    """验证全系统预案入口统一返回抱团预案字段."""
    plan = get_plan_for_date(REPO_ROOT, "2026-11-06", 20, "冰点", [])
    assert "baotuan_stock_targets" in plan
    assert len(plan["baotuan_stock_targets"]) >= 4
    assert "baotuan_cb_targets" in plan
    assert len(plan["baotuan_cb_targets"]) >= 4
