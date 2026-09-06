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


def _exact_raw_closes(price_df: Any, report_date: str | None) -> dict[str, dict[str, Any]]:
    rows = _as_rows(price_df)
    target = str(report_date or "")[:10]
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
