# -*- coding: utf-8 -*-
"""Append an intraday phase observation and recompute the linked scenario posterior."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from market_snapshot import append_phase_snapshot_once, build_phase_snapshot, load_phase_snapshots
from scenario_posterior import build_scenario_posterior_timeline
from data_sources.calendar_provider import CalendarProvider
from paths import CALENDAR_CACHE


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


def record_phase_observation(
    *, history_path: str | Path, phase_snapshot_path: str | Path,
    report_date: str, trade_date: str, phase: str, metrics: dict[str, Any],
    captured_at: str, run_id: str | None = None,
    source_lineage: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
    calendar_cache: str | Path = CALENDAR_CACHE,
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
    posterior = build_scenario_posterior_timeline(plans, rows, report_date=report_date, trade_date=target_trade_date,
                                                  prediction_id=prediction.get("prediction_id"))
    result = {"snapshot": saved, "posterior": posterior, "prediction_id": prediction.get("prediction_id")}
    context = prediction.get("decision_context")
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
