from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

KNOWN_OUTCOME_STATUSES = {
    "not_triggered", "triggered_not_filled", "filled", "unknown", "cancelled",
}


def _append(path: str | Path, event: dict[str, Any]) -> dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(event)
    payload.setdefault("recorded_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return payload


def _events(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    rows = []
    for raw in target.read_text(encoding="utf-8-sig").splitlines():
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def build_trade_plan_records(
    plan: dict | None, *, report_date: str, readiness: dict | None = None,
) -> list[dict[str, Any]]:
    """Create immutable plan records only for primary/alternate candidates."""
    plan = plan if isinstance(plan, dict) else {}
    readiness = readiness if isinstance(readiness, dict) else {}
    priority = plan.get("priority") if isinstance(plan.get("priority"), dict) else {}
    primary = priority.get("primary") if isinstance(priority.get("primary"), dict) else None
    alternates = [item for item in priority.get("alternates") or [] if isinstance(item, dict)]
    candidates = ([primary] if primary else []) + alternates
    if not candidates or not readiness.get("plan_permitted"):
        return []
    records = []
    for index, row in enumerate(candidates):
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        priority_label = "primary" if index == 0 else "alternate"
        records.append({
            "event_type": "trade_plan",
            "plan_id": f"{report_date}:{code}:{priority_label}:v1",
            "report_date": str(report_date),
            "code": code,
            "name": str(row.get("name") or ""),
            "sector": str(row.get("sector") or ""),
            "role": str(row.get("role") or ""),
            "priority": priority_label,
            "planned_action": str(row.get("action") or ""),
            "trigger": str(row.get("trigger") or ""),
            "invalid": str(row.get("invalid") or ""),
            "position_cap": str(plan.get("position") or ""),
            "plan_status": "conditional",
            "schema_version": "trade-plan/v1",
        })
    return records


def append_trade_plan_once(path: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    plan_id = str(record.get("plan_id") or "").strip()
    if not plan_id:
        raise ValueError("trade plan requires plan_id")
    for event in _events(path):
        if event.get("event_type") == "trade_plan" and event.get("plan_id") == plan_id:
            return {**event, "appended": False}
    return {**_append(path, {**record, "event_type": "trade_plan"}), "appended": True}


def append_trade_plan_outcome_once(
    path: str | Path, plan_id: str, status: str, *, actual: dict | None = None,
) -> dict[str, Any]:
    normalized = str(status or "").strip().lower()
    if normalized not in KNOWN_OUTCOME_STATUSES:
        raise ValueError(f"unknown trade plan outcome status: {status}")
    plan_id = str(plan_id or "").strip()
    if not plan_id:
        raise ValueError("trade plan outcome requires plan_id")
    for event in _events(path):
        if (event.get("event_type") == "trade_plan_outcome"
                and event.get("plan_id") == plan_id):
            return {**event, "appended": False}
    return {**_append(path, {
        "event_type": "trade_plan_outcome", "plan_id": plan_id,
        "status": normalized, "actual": dict(actual or {}),
        "schema_version": "trade-plan-outcome/v1",
    }), "appended": True}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def build_trade_plan_review(path: str | Path, *, report_date: str | None = None) -> dict[str, Any]:
    plans = {}
    outcomes = {}
    for event in _events(path):
        if event.get("event_type") == "trade_plan":
            if report_date and str(event.get("report_date")) != str(report_date):
                continue
            plans[str(event.get("plan_id") or "")] = event
        elif event.get("event_type") == "trade_plan_outcome":
            outcomes[str(event.get("plan_id") or "")] = event
    outcome_rows = [outcomes[plan_id] for plan_id in plans if plan_id in outcomes]
    status_counts = {status: 0 for status in KNOWN_OUTCOME_STATUSES}
    pnl_values = []
    for event in outcome_rows:
        status = str(event.get("status") or "unknown").lower()
        if status not in status_counts:
            status = "unknown"
        status_counts[status] += 1
        actual = event.get("actual") if isinstance(event.get("actual"), dict) else {}
        pnl = _number(actual.get("net_pnl", actual.get("pnl")))
        if pnl is not None and status == "filled":
            pnl_values.append(pnl)
    filled_count = status_counts["filled"]
    return {
        "schema_version": "trade-plan-review/v1",
        "report_date": str(report_date or ""),
        "plan_count": len(plans),
        "outcome_count": len(outcome_rows),
        "pending_count": len(plans) - len(outcome_rows),
        "status_counts": status_counts,
        "triggered_count": status_counts["triggered_not_filled"] + filled_count,
        "filled_count": filled_count,
        "pnl_known_count": len(pnl_values),
        "net_pnl": sum(pnl_values) if pnl_values else None,
        "has_realized_trade_result": bool(pnl_values),
        "note": (
            "暂无明确成交收益结果；未触发、未成交和未知不会计入交易胜率。"
            if not pnl_values else "收益仅统计明确标记为 filled 且提供净收益的记录。"
        ),
    }
