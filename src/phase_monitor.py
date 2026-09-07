# -*- coding: utf-8 -*-
"""Append an intraday phase observation and recompute the linked scenario posterior."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from market_snapshot import append_phase_snapshot_once, build_phase_snapshot, load_phase_snapshots
from scenario_posterior import build_scenario_posterior_timeline
from data_sources.calendar_provider import CalendarProvider
from paths import CALENDAR_CACHE, STRATEGY_VALIDATION_FILE


def _latest_prediction(history_path: str | Path, report_date: str) -> dict[str, Any] | None:
    target = Path(history_path)
    latest: dict[str, Any] | None = None
    if not target.exists():
        return None
    for raw in target.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if (isinstance(event, dict) and event.get("event_type") == "prediction"
                and str(event.get("report_date") or "") == str(report_date)):
            latest = event
    return latest


def _replay_qualification(
    context: dict[str, Any], plans: list, *, report_date: str,
    target_trade_date: str, validation_path: str | Path,
) -> dict[str, Any]:
    """Intersect current evidence with the entire original permission envelope."""
    from strategy_qualification import (
        load_validation_records, qualify_strategies, refresh_strategy_qualification,
        scoped_qualification,
    )

    quality = context.get("data_quality")
    quality = quality if isinstance(quality, dict) else {}
    original = scoped_qualification(quality) or {}
    # Check the old dates/rules/outcome before fresh evidence can replace them.
    pinned = refresh_strategy_qualification(
        quality, plans, report_date=report_date, target_trade_date=target_trade_date,
        event_metrics=context.get("event_metrics"),
    ) or {}
    context_pinned = refresh_strategy_qualification(
        quality, context.get("scenario_plans") or [], report_date=report_date,
        target_trade_date=target_trade_date, event_metrics=context.get("event_metrics"),
    ) or {}
    records = load_validation_records(validation_path)["records"]
    current = qualify_strategies(
        plans, quality=quality, validation_records=records,
        event_metrics=context.get("event_metrics"), report_date=report_date,
        target_trade_date=target_trade_date,
    )

    modes = ("facts_only", "observation", "decision")
    state = context.get("market_state")
    state = state if isinstance(state, dict) else {}
    ceilings = [str(layer.get("publication_mode") or "").strip().lower()
                for layer in (context, quality, state, original)]
    ceiling = min((mode for mode in ceilings if mode in modes), key=modes.index, default="observation")
    envelope_issues = []
    if not original or original.get("publication_mode") != "decision":
        envelope_issues.append("not_authorized_in_original_plan")
    if original and original.get("core_ready") is not True:
        envelope_issues.append("original_core_not_ready")
        ceiling = "facts_only"
    if ceiling != "decision":
        envelope_issues.append("original_publication_mode_" + ceiling)
    if (context.get("date_str") != report_date
            or context.get("next_trade_date") != target_trade_date):
        envelope_issues.append("original_prediction_dates_changed")

    original_rows = original.get("strategies") or {}
    original_ids = original.get("eligible_strategy_ids") or []
    for sid, fresh in current["strategies"].items():
        previous = original_rows.get(sid)
        previous = previous if isinstance(previous, dict) else {}
        bound = (pinned.get("strategies") or {}).get(sid, {})
        context_bound = (context_pinned.get("strategies") or {}).get(sid, {})
        issues = list(envelope_issues)
        if (sid not in original_ids or previous.get("plan_permitted") is not True
                or previous.get("status") != "eligible" or previous.get("strategy_id") != sid):
            issues.append("not_authorized_in_original_plan")
        if any(previous.get(key) != bound.get(key) for key in ("rule_version", "rule_fingerprint")):
            issues.append("validation_rules_changed")
        for checked in (bound, context_bound):
            if not checked.get("plan_permitted"):
                issues.extend(checked.get("issues") or ["original_plan_binding_unverified"])
        if issues:
            # A newly valid record must not certify an edited old plan. Retain
            # the sanitized revoked original assessment when its binding fails.
            revoked = next((row for row in (bound, context_bound)
                            if row and not row.get("plan_permitted")), fresh)
            row = {**revoked, "plan_permitted": False}
            if row.get("status") == "eligible":
                row["status"] = "unverified"
            row["issues"] = list(dict.fromkeys([*row.get("issues", []), *fresh.get("issues", []), *issues]))
            current["strategies"][sid] = row

    allowed = [sid for sid, row in current["strategies"].items() if row["plan_permitted"]]
    current["eligible_strategy_ids"] = allowed
    mode = "facts_only" if not current["core_ready"] else "decision" if allowed else "observation"
    current["publication_mode"] = min((ceiling, mode), key=modes.index)
    return current


def record_phase_observation(
    *, history_path: str | Path, phase_snapshot_path: str | Path,
    report_date: str, trade_date: str, phase: str, metrics: dict[str, Any],
    captured_at: str, run_id: str | None = None,
    source_lineage: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
    calendar_cache: str | Path = CALENDAR_CACHE,
    validation_path: str | Path | None = None,
) -> dict[str, Any]:
    prediction = _latest_prediction(history_path, report_date)
    if prediction is None:
        raise ValueError(f"找不到 {report_date} 的预测快照")
    target_trade_date = prediction.get("target_trade_date") or CalendarProvider(cache_path=calendar_cache).cached_next_trading_day(report_date)
    if not target_trade_date or str(trade_date) != str(target_trade_date):
        raise ValueError(f"目标交易日 target={target_trade_date or '未确定'}，不接受 {trade_date} 的阶段数据")
    if not isinstance(source_lineage, dict) or not str(source_lineage.get("source") or "").strip():
        raise ValueError("必须明确提供市场数据来源 source_lineage.source")
    if not isinstance(quality, dict) or quality.get("status") not in {"ok", "degraded", "unknown", "unavailable", "blocked"}:
        raise ValueError("必须明确提供质量 quality.status，不能把导入成功等同数据完整")
    source_lineage = dict(source_lineage)
    if source_lineage.get("prediction_id") not in {None, prediction.get("prediction_id")}:
        raise ValueError("来源预测ID与当前预测不一致")
    source_lineage["prediction_id"] = prediction.get("prediction_id")
    snapshot = build_phase_snapshot(
        report_date=report_date, trade_date=trade_date, phase=phase,
        metrics=metrics, captured_at=captured_at, run_id=run_id,
        source_lineage=source_lineage,
        quality=quality,
    )
    saved = append_phase_snapshot_once(phase_snapshot_path, snapshot)
    # Keep the report-day close; filtering everything by T+1 discards the baseline.
    rows = load_phase_snapshots(phase_snapshot_path, report_date=report_date)
    plans = prediction.get("scenario_plans") if isinstance(prediction.get("scenario_plans"), list) else []
    context = prediction.get("decision_context")
    # No original scoped permission means no active/executable scenario, even
    # when a legacy context has healthy modules and large descriptive counts.
    allowed = []
    if isinstance(context, dict) and context:
        from copy import deepcopy
        context = deepcopy(context)
        scoped = _replay_qualification(
            context, plans, report_date=report_date, target_trade_date=target_trade_date,
            validation_path=validation_path or STRATEGY_VALIDATION_FILE,
        )
        allowed = scoped["eligible_strategy_ids"]
        quality = context.get("data_quality")
        quality = quality if isinstance(quality, dict) else {}
        context["data_quality"] = {**quality, "strategy_qualification": scoped,
                                   "publication_mode": scoped["publication_mode"]}
        context["publication_mode"] = scoped["publication_mode"]
    posterior = build_scenario_posterior_timeline(plans, rows, report_date=report_date, trade_date=target_trade_date,
                                                  prediction_id=prediction.get("prediction_id"), eligible_strategy_ids=allowed)
    result = {"snapshot": saved, "posterior": posterior, "prediction_id": prediction.get("prediction_id")}
    if isinstance(context, dict) and context:
        from decision_dashboard import build_today_decision
        from report_closure import persist_decision_review
        context = dict(context)
        context.update(scenario_posterior=posterior, phase_snapshots=[], next_trade_date=target_trade_date)
        if context.get("reference_prices"):
            import pandas as pd
            context["price_df"] = pd.DataFrame(context["reference_prices"])
        decision = build_today_decision(context)
        review = persist_decision_review(history_path, decision)
        result.update(decision=decision, daily_decision=review["daily_decision"],
                      trade_plan_records=review["trade_plan_records"], decision_changes=review["decision_changes"])
    else:
        result["decision_context_status"] = "legacy_context_missing"
        result["note"] = "旧预测缺少原始决策门禁上下文：只更新证据，不生成执行许可或交易结果。"
    return result
