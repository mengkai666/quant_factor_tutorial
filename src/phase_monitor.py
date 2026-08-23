# -*- coding: utf-8 -*-
"""Append an intraday phase observation and recompute the linked scenario posterior."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from market_snapshot import append_phase_snapshot_once, build_phase_snapshot, load_phase_snapshots
from scenario_posterior import build_scenario_posterior_timeline


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
) -> dict[str, Any]:
    prediction = _latest_prediction(history_path, report_date)
    if prediction is None:
        raise ValueError(f"找不到 {report_date} 的预测快照")
    snapshot = build_phase_snapshot(
        report_date=report_date, trade_date=trade_date, phase=phase,
        metrics=metrics, captured_at=captured_at, run_id=run_id,
        source_lineage=source_lineage or {"source": "phase_monitor"},
        quality=quality or {"status": "ok", "missing_fields": []},
    )
    saved = append_phase_snapshot_once(phase_snapshot_path, snapshot)
    rows = [
        row for row in load_phase_snapshots(phase_snapshot_path, report_date=report_date)
        if str(row.get("trade_date") or "") == str(trade_date)
    ]
    plans = prediction.get("scenario_plans") if isinstance(prediction.get("scenario_plans"), list) else []
    posterior = build_scenario_posterior_timeline(plans, rows)
    return {"snapshot": saved, "posterior": posterior, "prediction_id": prediction.get("prediction_id")}
