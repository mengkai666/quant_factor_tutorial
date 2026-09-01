"""Single market-regime classification shared by report surfaces."""
from __future__ import annotations

import re

from ad_breadth import MIN_MARKET_BREADTH  # 单一真源, 别在本文件另写一份


def _height_set(echelon) -> set[int]:
    heights = set()
    for item in echelon or []:
        text = str(item.get("height", ""))
        if "首板" in text:
            heights.add(1)
            continue
        match = re.search(r"(\d+)", text)
        if match:
            heights.add(int(match.group(1)))
    return heights


def classify_market_regime(advance_decline=None, sentiment_df=None,
                           echelon=None, quality_ok=True) -> dict:
    """Return one auditable regime used by timing, rebound and dashboard views."""
    advance_decline = advance_decline or {}
    up = float(advance_decline.get("up", 0) or 0)
    down = float(advance_decline.get("down", 0) or 0)
    breadth = up + down
    ad_ratio = up / breadth if breadth else None
    heights = _height_set(echelon)
    max_height = max(heights, default=0)
    missing = sorted(set(range(1, max_height)) - heights)
    high_gap = max_height >= 6 and len(missing) >= 2

    if (not quality_ok or advance_decline.get("ad_incomplete")
            or breadth < MIN_MARKET_BREADTH):
        return {
            "code": "DATA_UNCERTAIN",
            "title": "数据未确认",
            "color": "#8b949e",
            "action": "暂停发布确定性策略，先补齐数据",
            "reason": "A/D 或核心来源覆盖不足",
            "ad_ratio": ad_ratio,
            "max_height": max_height,
            "missing_heights": missing,
        }

    if ad_ratio is not None and ad_ratio >= 0.65:
        if high_gap:
            title = "普涨反弹 · 高位分化"
            action = "只做前排确认，不追孤峰"
            code = "BROAD_STRONG_HIGH_GAP"
            color = "#ff8800"
            reason = f"上涨占比 {ad_ratio:.3f}，但最高 {max_height} 板缺少中间承接"
        else:
            title = "普涨反弹 · 梯队健康"
            action = "只做主线确认，后排不追"
            code = "BROAD_STRONG"
            color = "#f85149"
            reason = f"上涨占比 {ad_ratio:.3f}，连板梯队未出现明显断层"
    elif ad_ratio is not None and ad_ratio < 0.35:
        title = "普跌弱势 · 防守优先"
        action = "控制回撤，等待右侧确认"
        code = "BROAD_WEAK"
        color = "#58a6ff"
        reason = f"上涨占比 {ad_ratio:.3f}，市场广度偏弱"
    else:
        title = "结构震荡 · 等待确认"
        action = "轻仓观察，不押单一方向"
        code = "STRUCTURAL_WATCH"
        color = "#d29922"
        reason = f"上涨占比 {ad_ratio:.3f}" if ad_ratio is not None else "A/D 未就位"

    return {
        "code": code,
        "title": title,
        "color": color,
        "action": action,
        "reason": reason,
        "ad_ratio": ad_ratio,
        "max_height": max_height,
        "missing_heights": missing,
    }
