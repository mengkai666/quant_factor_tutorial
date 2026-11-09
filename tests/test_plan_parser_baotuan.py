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


def test_np_plan_extracts_concrete_sectors_stocks_and_conditional_tactics():
    """验证新版 np-plan 预案能直指具体主线板块、逐股深度战法、条件开仓仓位与严重异动雷达."""
    plan = get_plan_for_date(REPO_ROOT, "2026-11-06", 25, "极度冰点期", [], dt_count=15)
    assert plan.get("report_date") == "2026-11-06"
    assert plan.get("target_date") == "2026-11-09"
    assert "成" in plan["recommended_position"] and not plan["recommended_position"].startswith("0 仓位")

    stocks = plan["baotuan_stock_targets"]
    assert len(stocks) == 4
    for idx, card in enumerate(stocks):
        assert card.get("sector"), f"missing sector in {card}"
        assert card.get("name"), f"missing stock name in {card}"
        assert card.get("targets") and len(card["targets"]) >= 1
        assert card.get("stock_details") and len(card["stock_details"]) >= 1
        if idx < 3:
            assert "成" in card.get("pos", "")
        else:
            assert "0 仓位" in card.get("pos", "")

    assert "新能源电网" in stocks[0]["sector"]
    assert "闽发铝业" in stocks[0]["name"]
    assert any(t["code"] == "sz002578" for t in stocks[0]["targets"])
    assert "5.01" in stocks[0]["stock_details"][0]["metrics"]

    # 验证严重异动雷达直接内建返回（奥雅股份 10日+92.21% 为 danger）
    radar = plan.get("abnormal_radar") or []
    assert len(radar) >= 4
    assert any(r["name"] == "奥雅股份" and r["risk_level"] == "danger" for r in radar)

    cbs = plan["baotuan_cb_targets"]
    assert len(cbs) >= 4
    assert cbs[0]["bond_name"] == "福新转债"
    assert "成交" in cbs[0].get("bond_metrics", "")
    assert "成" in cbs[0].get("pos", "")


