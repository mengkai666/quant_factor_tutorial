"""Shared report/explicit-phase assembly of daily decisions and plan revisions.

This layer persists computed decisions only. It never synthesizes orders, fills,
market facts or P&L, and it never widens the existing publication gates.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trade_plan_review import (
    append_daily_decision_once, append_trade_plan_once, build_daily_decision_record,
    build_daily_decision_review, build_trade_plan_records, build_trade_plan_review,
)


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _priority_codes(record: dict) -> list[str]:
    priority = _dict(_dict(record.get("plan_snapshot")).get("priority"))
    rows = [priority.get("primary"), *(priority.get("alternates") or [])]
    return [str(row["code"]) for row in rows if isinstance(row, dict) and row.get("code")]


def build_decision_changes(review: dict) -> dict[str, Any]:
    items = [row for row in review.get("items") or [] if isinstance(row, dict)]
    if not items:
        return {"has_previous": False, "changes": []}
    current = items[-1]
    revisions = current.get("revisions") or []
    previous = revisions[-2] if len(revisions) > 1 else items[-2] if len(items) > 1 else None
    if previous is None:
        return {"has_previous": False, "current_report_date": current.get("report_date"), "changes": []}
    fields = [
        ("status", "操作分类", lambda r: r.get("status")),
        ("reason", "结论原因", lambda r: r.get("reason")),
        ("scenario", "可决策情景", lambda r: r.get("decision_scenario_id")),
        ("position", "计划仓位", lambda r: _dict(r.get("plan_snapshot")).get("position")),
        ("candidates", "首选与备选", _priority_codes),
        ("recovery", "重新评估条件", lambda r: r.get("recovery_conditions")),
    ]
    changes = [{"field": key, "label": label, "before": get(previous), "after": get(current)}
               for key, label, get in fields if get(previous) != get(current)]
    return {"has_previous": True, "previous_report_date": previous.get("report_date"),
            "current_report_date": current.get("report_date"),
            "previous_version": previous.get("version"), "current_version": current.get("version"),
            "changes": changes}


def persist_decision_review(history_path: str | Path, decision: dict) -> dict[str, Any]:
    """Journal every report day, including no-trade days, outside candidate loops."""
    report_date = str(decision.get("report_date") or "").strip()
    if not report_date:
        raise ValueError("decision requires report_date")
    plan = _dict(decision.get("action_plan"))
    ready = _dict(decision.get("readiness"))
    signal = _dict(ready.get("signal"))
    scenario_status = plan.get("scenario_status") or signal.get("scenario_status") or "awaiting_confirmation"
    if scenario_status == "baseline_only":
        scenario_status = "awaiting_confirmation"
    scenario_id = plan.get("decision_scenario_id") or signal.get("decision_scenario_id")
    trading_date = plan.get("trading_date") or ready.get("target_trade_date")
    funnel = _dict(decision.get("candidate_funnel"))
    daily = append_daily_decision_once(history_path, build_daily_decision_record(
        plan, report_date=report_date, readiness=ready, trading_date=trading_date,
        candidate_funnel=funnel, scenario_status=scenario_status, decision_scenario_id=scenario_id,
    ))
    existing = build_trade_plan_review(history_path, report_date=report_date)
    records = build_trade_plan_records(plan, report_date=report_date, readiness=ready)
    current_ids = set()
    saved = []
    for record in records:
        record.update(
            plan_status="confirmed" if ready.get("execution_ready") else "awaiting_confirmation",
            scenario_status=scenario_status, decision_scenario_id=scenario_id,
            trading_date=trading_date, candidate_funnel_fingerprint=funnel.get("fingerprint"),
            reason_code=_dict(ready.get("action")).get("reason_code"),
        )
        current_ids.add(record["logical_plan_id"])
        saved.append(append_trade_plan_once(history_path, record))
    for item in existing.get("items") or []:
        if item["logical_plan_id"] in current_ids:
            continue
        # No longer permitted/selected: revise the same old plan, do not leave
        # its last confirmed state looking live and do not record a fill.
        old = dict(item["plan"])
        old.update(
            plan_status="invalidated" if signal.get("status") == "invalidated" else "suspended",
            planned_action="不开新仓；当前条件或候选资格不再成立",
            scenario_status=scenario_status, decision_scenario_id=scenario_id,
            trading_date=trading_date, candidate_funnel_fingerprint=funnel.get("fingerprint"),
            reason_code=_dict(ready.get("action")).get("reason_code"),
        )
        saved.append(append_trade_plan_once(history_path, old))
    daily_review = build_daily_decision_review(history_path, through_report_date=report_date)
    return {
        "daily_decision": daily, "trade_plan_records": saved,
        "daily_decision_review": daily_review, "decision_changes": build_decision_changes(daily_review),
        "trade_plan_review": build_trade_plan_review(history_path, through_report_date=report_date),
    }


def build_decision_replay_context(ctx: dict, decision: dict) -> dict[str, Any]:
    """Pin the forecast's original gates/candidates for explicit phase imports.

    Never store live dataframes, a nested report context or invented entry/stop
    prices. Only already-observed report-close references are carried forward.
    """
    keys = (
        "date_str", "publication_mode", "data_quality", "market_state", "market_thesis",
        "mainline_review", "mainline_concentration", "progression_chain", "echelon",
        "scenario_plans", "next_trade_date", "breadth_ratio", "ladder", "dt", "event_metrics",
    )
    payload = {key: ctx[key] for key in keys if key in ctx}
    effective = decision.get("strategy_qualification")
    if isinstance(effective, dict):
        from strategy_qualification import scoped_qualification
        # Use only renderer-sanitized metadata. Original policy/permission bits
        # remain ceilings, but none of its old validation payload is copied back.
        original = scoped_qualification(ctx.get("data_quality")) or {}
        effective = {**effective, "strategies": {
            sid: dict(row) for sid, row in _dict(effective.get("strategies")).items()
            if isinstance(row, dict)
        }}
        modes = ("facts_only", "observation", "decision")
        ceilings = [str(layer.get("publication_mode") or "").strip().lower() for layer in (
            ctx, _dict(ctx.get("data_quality")), _dict(ctx.get("market_state")),
            original, effective, _dict(decision.get("readiness")),
        )]
        mode = min((value for value in ceilings if value in modes), key=modes.index, default="observation")
        if original and original.get("core_ready") is not True:
            effective["core_ready"] = False
            effective["core_issues"] = list(dict.fromkeys([
                *effective.get("core_issues", []), "original_core_not_ready",
            ]))
        original_ids = original.get("eligible_strategy_ids")
        original_ids = original_ids if isinstance(original_ids, list) else []
        for sid, row in effective["strategies"].items():
            previous = _dict(_dict(original.get("strategies")).get(sid))
            permitted = (
                mode == "decision" and original.get("core_ready") is True
                and original.get("publication_mode") == "decision" and sid in original_ids
                and previous.get("plan_permitted") is True and previous.get("status") == "eligible"
                and previous.get("strategy_id") == sid
                and all(previous.get(key) == row.get(key) for key in ("rule_version", "rule_fingerprint"))
            )
            if row.get("plan_permitted") and not permitted:
                row.update(plan_permitted=False, status="unverified")
                row["issues"] = list(dict.fromkeys([*row.get("issues", []), "not_authorized_in_original_plan"]))
        allowed = [sid for sid, row in effective["strategies"].items() if row.get("plan_permitted")]
        effective["eligible_strategy_ids"] = allowed
        qualified_mode = "facts_only" if not effective.get("core_ready") else "decision" if allowed else "observation"
        mode = min((mode, qualified_mode), key=modes.index)
        effective["publication_mode"] = mode
        payload["data_quality"] = {
            **_dict(payload.get("data_quality")), "strategy_qualification": effective,
            "publication_mode": mode,
        }
        payload["publication_mode"] = mode
        payload["market_state"] = {**_dict(payload.get("market_state")), "publication_mode": mode}
    payload["candidate_funnel"] = decision.get("candidate_funnel") or {}
    references = []
    for row in decision.get("candidates") or []:
        execution = _dict(row.get("execution"))
        if row.get("code") and execution.get("reference_close") is not None:
            references.append({"code": row["code"], "date": decision["report_date"], "close_raw": execution["reference_close"]})
    payload["reference_prices"] = references
    return json.loads(json.dumps(payload, ensure_ascii=False, default=str), parse_constant=lambda _: None)


def merge_limit_event_observations(rows: list[dict], snapshot: dict, *, report_date: str) -> list[dict]:
    """Attach same-day event evidence without changing members, names or height.

    The event archive remains a closing-pool observation, not the universe of
    attempted limits; this merge is for audit/immutable snapshots, not gates.
    """
    from limit_events import EVENT_PROVENANCE_FIELDS, LIMIT_EVENT_FIELDS
    from report_logic import normalize_stock_code
    if snapshot.get("trade_date") != report_date:
        return [dict(row) for row in rows]
    events = {normalize_stock_code(row.get("code")): row for row in snapshot.get("records") or []
              if isinstance(row, dict) and row.get("trade_date") == report_date and normalize_stock_code(row.get("code"))}
    fields = (*LIMIT_EVENT_FIELDS, *EVENT_PROVENANCE_FIELDS, "event_evidence")
    return [{**row, **{field: events.get(normalize_stock_code(row.get("code")), {})[field]
                      for field in fields if field in events.get(normalize_stock_code(row.get("code")), {})}}
            for row in rows]
