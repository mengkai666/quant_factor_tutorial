# -*- coding: utf-8 -*-
"""连板逐股链路和昨日变化指标。"""
from __future__ import annotations

from typing import Any, Iterable

from report_logic import binomial_confidence_interval, normalize_stock_code


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _height(row: dict[str, Any]) -> int:
    import re
    value = row.get("height", row.get("level", row.get("连板高度", row.get("连板数", 0))))
    if "首板" in str(value or ""):
        return 1
    match = re.search(r"(\d+)", str(value or ""))
    return int(match.group(1)) if match else 0


def build_progression_chain(previous_rows: Iterable[dict[str, Any]] | None, current_rows: Iterable[dict[str, Any]] | None) -> dict[str, Any]:
    current = {normalize_stock_code(row.get("code", row.get("代码", ""))): dict(row) for row in (current_rows or ()) if normalize_stock_code(row.get("code", row.get("代码", "")))}
    rows: list[dict[str, Any]] = []
    for source in previous_rows or ():
        before = dict(source)
        code = normalize_stock_code(before.get("code", before.get("代码", "")))
        if not code:
            continue
        prev_h = _height(before)
        today = current.get(code)
        if today is None:
            status = "missing"
            curr_h, pct = 0, None
        else:
            curr_h, pct = _height(today), _num(today.get("pct_change", today.get("涨跌幅", 0)))
            raw_status = str(today.get("status", "") or "").lower()
            if raw_status in {"suspended", "停牌"} or today.get("suspended") is True:
                status = "suspended"
            elif pct <= -9.5 or raw_status in {"limit_down", "跌停"}:
                status = "limit_down"
            elif curr_h >= prev_h + 1:
                status = "promoted"
            elif pct >= 0:
                status = "broken_positive"
            else:
                status = "broken_negative"
        rows.append({
            "code": code, "name": str((today or before).get("name", (today or before).get("名称", "")) or ""),
            "previous_height": prev_h, "current_height": curr_h, "pct_change": pct, "status": status,
        })
    by_height: dict[int, dict[str, Any]] = {}
    for height in sorted({row["previous_height"] for row in rows if row["previous_height"] > 0}):
        group = [row for row in rows if row["previous_height"] == height]
        promoted = sum(row["status"] == "promoted" for row in group)
        negative = sum(row["status"] in {"broken_negative", "limit_down"} for row in group)
        interval = binomial_confidence_interval(promoted, len(group))
        by_height[height] = {
            "height": height, "sample_size": len(group), "promoted": promoted,
            "broken": sum(row["status"].startswith("broken") for row in group),
            "negative_feedback": negative, "limit_down": sum(row["status"] == "limit_down" for row in group),
            "suspended": sum(row["status"] == "suspended" for row in group),
            "missing": sum(row["status"] == "missing" for row in group),
            "promotion_rate": promoted / len(group) if group else None,
            "confidence_interval": interval,
        }
    total_promoted = sum(row["status"] == "promoted" for row in rows)
    overall = binomial_confidence_interval(total_promoted, len(rows))
    return {"rows": rows, "by_height": by_height, "sample_size": len(rows), "promoted": total_promoted, "promotion_rate": overall.get("rate"), "confidence_interval": overall, "available": bool(rows)}


def _pool_delta_row(code: str, current: dict[str, Any] | None, previous: dict[str, Any] | None) -> dict[str, Any]:
    source = current or previous or {}
    return {
        "code": code,
        "name": str(source.get("name", source.get("名称", "")) or ""),
        "previous_height": _height(previous or {}),
        "current_height": _height(current or {}),
    }


def build_limit_pool_delta(
    previous_rows: Iterable[dict[str, Any]] | None,
    current_rows: Iterable[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Compare two structured limit-pool snapshots by security code.

    ``None`` means the snapshot was not captured; an empty list is a valid
    snapshot for a day with no rows.  This distinction prevents the report
    from manufacturing changes from missing data.
    """
    if previous_rows is None or current_rows is None:
        return {
            "available": False,
            "reason": "缺少昨日或今日涨停池逐股结构化快照",
            "previous_count": 0,
            "current_count": 0,
            "new": [],
            "new_first_board": [],
            "promoted": [],
            "broken": [],
            "missing": [],
            "unchanged": [],
            "counts": {"new": 0, "new_first_board": 0, "promoted": 0, "broken": 0, "missing": 0, "unchanged": 0},
        }

    previous_by_code = {
        normalize_stock_code(row.get("code", row.get("代码", ""))): dict(row)
        for row in previous_rows
        if isinstance(row, dict) and normalize_stock_code(row.get("code", row.get("代码", "")))
    }
    current_by_code = {
        normalize_stock_code(row.get("code", row.get("代码", ""))): dict(row)
        for row in current_rows
        if isinstance(row, dict) and normalize_stock_code(row.get("code", row.get("代码", "")))
    }

    new: list[dict[str, Any]] = []
    new_first_board: list[dict[str, Any]] = []
    promoted: list[dict[str, Any]] = []
    broken: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for code in sorted(set(previous_by_code) | set(current_by_code)):
        previous = previous_by_code.get(code)
        current = current_by_code.get(code)
        if previous is None:
            row = _pool_delta_row(code, current, None)
            new.append(row)
            if row["current_height"] == 1:
                new_first_board.append(row)
            continue
        if current is None:
            missing.append(_pool_delta_row(code, None, previous))
            continue

        previous_height = _height(previous)
        current_height = _height(current)
        row = _pool_delta_row(code, current, previous)
        if current_height > previous_height:
            promoted.append(row)
        elif current_height < previous_height:
            broken.append(row)
        else:
            unchanged.append(row)

    counts = {
        "new": len(new),
        "new_first_board": len(new_first_board),
        "promoted": len(promoted),
        "broken": len(broken),
        "missing": len(missing),
        "unchanged": len(unchanged),
    }
    return {
        "available": True,
        "reason": "",
        "previous_count": len(previous_by_code),
        "current_count": len(current_by_code),
        "new": new,
        "new_first_board": new_first_board,
        "promoted": promoted,
        "broken": broken,
        "missing": missing,
        "unchanged": unchanged,
        "counts": counts,
    }


DELTA_LABELS = {
    "max_height": "空间高度", "limit_up": "涨停家数", "limit_down": "跌停家数",
    "breadth_ratio": "上涨占比", "ladder_integrity": "梯队完整度",
    "concentration": "主线集中度", "promotion_rate": "晋级率", "mainline_rank": "主线排名",
}


def build_daily_delta_snapshot(current: dict[str, Any] | None, previous: dict[str, Any] | None) -> dict[str, Any]:
    current, previous = dict(current or {}), dict(previous or {})
    if not previous:
        return {
            "available": False,
            "reason": "缺少上一交易日结构化快照",
            "metrics": {},
            "highlights": [],
            "limit_pool": build_limit_pool_delta(None, None),
        }
    metrics: dict[str, dict[str, Any]] = {}
    ranked: list[tuple[float, str]] = []
    scale = {"max_height": 1, "limit_up": 20, "limit_down": 10, "breadth_ratio": .1, "ladder_integrity": .1, "concentration": .1, "promotion_rate": .1}
    for key, label in DELTA_LABELS.items():
        today, yesterday = current.get(key), previous.get(key)
        if today is None or yesterday is None:
            metrics[key] = {"label": label, "available": False, "reason": "当日或昨日值缺失"}
            continue
        if isinstance(today, (int, float)) and isinstance(yesterday, (int, float)):
            delta = today - yesterday
            changed = abs(delta) > 1e-12
            magnitude = abs(delta) / scale.get(key, 1)
        else:
            delta = None
            changed = today != yesterday
            magnitude = 1.0 if changed else 0.0
        metrics[key] = {"label": label, "available": True, "current": today, "previous": yesterday, "delta": delta, "changed": changed}
        if changed:
            ranked.append((magnitude, key))
    ranked.sort(reverse=True)
    highlights = [metrics[key] | {"key": key} for _, key in ranked[:5]]
    if len(highlights) < 3:
        stable = [item | {"key": key} for key, item in metrics.items() if item.get("available") and key not in {h["key"] for h in highlights}]
        highlights.extend(stable[: 3 - len(highlights)])
    previous_pool = previous.get("limit_pool_rows")
    current_pool = current.get("limit_pool_rows")
    if previous_pool is None and isinstance(previous.get("limit_pool"), list):
        previous_pool = previous.get("limit_pool")
    if current_pool is None and isinstance(current.get("limit_pool"), list):
        current_pool = current.get("limit_pool")
    return {
        "available": True,
        "reason": "",
        "metrics": metrics,
        "highlights": highlights[:5],
        "limit_pool": build_limit_pool_delta(previous_pool, current_pool),
    }
