"""Per-candidate execution metadata with conservative missing-data semantics."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _as_rows(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if hasattr(frame, "to_dict"):
        try:
            rows = frame.to_dict("records")
            return [dict(row) for row in rows if isinstance(row, dict)]
        except Exception:
            return []
    if isinstance(frame, dict):
        return [dict(frame)]
    if isinstance(frame, (list, tuple)):
        return [dict(row) for row in frame if isinstance(row, dict)]
    return []


def _code(value: Any) -> str:
    text = str(value or "").strip().lower().replace(".", "")
    if text.endswith("sh") and text[:-2].isdigit():
        text = "sh" + text[:-2]
    elif text.endswith("sz") and text[:-2].isdigit():
        text = "sz" + text[:-2]
    elif text.endswith("bj") and text[:-2].isdigit():
        text = "bj" + text[:-2]
    elif text.isdigit() and len(text) == 6:
        text = ("sh" if text.startswith(("5", "6", "688")) else "sz") + text
    return text


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


_CODE_COLUMNS = ("code", "代码")
_DATE_COLUMNS = ("date", "日期")
_CLOSE_COLUMNS = ("close_raw",)


def _target_day_frame(price_df: Any, target: str) -> Any:
    """把价格表**先按报告日切片**再往下游传。

    为什么不直接交给 `_as_rows` 做整表 `to_dict("records")`:
        生产里的价量表是 100 万行 × 8 列的全市场价格缓存, 而这里只要报告日当天
        那十几只股票的一列收盘价。整表 materialize 的代价是秒级的, 而且渲染层每张
        卡片都会重建一次决策 —— 2026-09-12 cProfile: `_exact_raw_closes` 被
        `build_execution_contract` 调用 5 次, 合计 83.5s, 其中 pandas `to_dict`
        占 29.8s。按日切片是向量化操作, 把它换成毫秒级。

    语义上与原实现完全一致: 下游仍按 (code, date, close_raw) 逐行判定, 切片只是
    提前丢掉必然被 `date != target` 过滤掉的行。非 DataFrame / 缺日期列 / 切片
    抛错都原样退回原对象, 不会因为优化而丢数据。
    """
    columns = getattr(price_df, "columns", None)
    if columns is None or not target or not hasattr(price_df, "loc"):
        return price_df
    date_col = next((name for name in _DATE_COLUMNS if name in columns), None)
    if date_col is None:
        return price_df
    try:
        day = price_df[date_col].astype(str).str.slice(0, 10)
        return price_df.loc[day == target]
    except Exception:
        return price_df


def _exact_raw_closes(price_df: Any, report_date: str | None) -> dict[str, dict[str, Any]]:
    target = str(report_date or "")[:10]
    rows = _as_rows(_target_day_frame(price_df, target))
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = _code(row.get("code", row.get("代码")))
        date = str(row.get("date", row.get("日期", "")))[:10]
        close = _number(row.get("close_raw"))
        if not code or date != target or close is None:
            continue
        result[code] = {"date": date, "close_raw": close}
    return result


def _holding_status(holdings: Any) -> str:
    if holdings is None or holdings == {} or holdings == [] or holdings == ():
        return "not_provided"
    return "provided"


def build_execution_contract(
    plan: dict | None = None, *, price_df: Any = None,
    holdings: Any = None, report_date: str | None = None,
    next_trade_date: str | None = None,
) -> dict[str, Any]:
    """Attach honest execution limits without creating trade prices or orders."""
    plan = plan if isinstance(plan, dict) else {}
    closes = _exact_raw_closes(price_df, report_date)
    holding_status = _holding_status(holdings)
    valid_until = str(next_trade_date or "").strip() or None
    details: dict[str, dict[str, Any]] = {}
    for group in plan.get("groups") or []:
        if not isinstance(group, dict):
            continue
        for row in group.get("rows") or []:
            if not isinstance(row, dict):
                continue
            code = _code(row.get("code"))
            if not code:
                continue
            close = closes.get(code)
            if close:
                price_status = "close_reference_only"
                price_note = "仅为报告日未复权收盘参考，不是入场价、止损价，也不代表可成交。"
            else:
                price_status = "unavailable"
                price_note = "缺少报告日有效未复权收盘价，不能计算价格参数。"
            details[code] = {
                "code": code,
                "reference_close": close["close_raw"] if close else None,
                "reference_date": close["date"] if close else None,
                "entry_price": None,
                "stop_price": None,
                "price_status": price_status,
                "price_note": price_note,
                "holding_status": holding_status,
                "holding_note": (
                    "已提供持仓上下文，可在后续规则中区分持有/空仓。"
                    if holding_status == "provided"
                    else "未提供持仓，仅输出机会与风险规则，不生成实际加仓/减仓指令。"
                ),
                "t_plus_one": "A股T+1：新买标的当日不可卖出；失效条件不保证即时成交。",
                "valid_until": valid_until,
                "validity_status": "next_session_recheck" if valid_until else "recheck_before_next_session",
            }
    return {
        "schema_version": "execution-contract/v1",
        "report_date": str(report_date or ""),
        "plan_trade_date": valid_until,
        "validity_status": "next_session_recheck" if valid_until else "recheck_before_next_session",
        "holding_status": holding_status,
        "price_basis": "close_raw_reference_only",
        "details": details,
        "summary": {
            "candidate_count": len(details),
            "close_reference_count": sum(1 for item in details.values() if item["price_status"] == "close_reference_only"),
            "price_parameter_count": 0,
            "actual_order_count": 0,
            "note": "本契约不产生委托、成交或保证收益。",
        },
    }
