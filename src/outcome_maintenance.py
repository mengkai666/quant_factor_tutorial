# -*- coding: utf-8 -*-
"""Authoritative-calendar maintenance entrypoint for prediction outcomes."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Callable

from data_sources.calendar_provider import CalendarProvider
from market_snapshot import load_phase_snapshots
from outcome_definition import reconcile_prediction_outcomes
from paths import CALENDAR_CACHE, DAILY_SNAPSHOT_DIR, PHASE_SNAPSHOT_HISTORY, PREDICTION_HISTORY


def _date_bounds(history_path: Path, snapshots_dir: Path) -> tuple[str, str]:
    report_dates: list[str] = []
    if history_path.exists():
        for raw in history_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("event_type") == "prediction":
                text = str(event.get("report_date") or "")
                try:
                    date.fromisoformat(text)
                except ValueError:
                    continue
                report_dates.append(text)
    snapshot_dates: list[str] = []
    for path in snapshots_dir.glob("*.json"):
        try:
            snapshot_dates.append(date.fromisoformat(path.stem).isoformat())
        except ValueError:
            continue
    today = date.today().isoformat()
    return min(report_dates or snapshot_dates or [today]), max(snapshot_dates or report_dates or [today])


def run_reconciliation(
    *, history_path: str | Path = PREDICTION_HISTORY,
    snapshots_dir: str | Path = DAILY_SNAPSHOT_DIR,
    calendar_cache: str | Path = CALENDAR_CACHE,
    phase_snapshot_path: str | Path = PHASE_SNAPSHOT_HISTORY,
    calendar_source: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    history = Path(history_path)
    snapshots = Path(snapshots_dir)
    start, end = _date_bounds(history, snapshots)
    calendar = CalendarProvider(source=calendar_source, cache_path=calendar_cache)
    trading_days = calendar.trading_days(start, end)
    phase_rows = load_phase_snapshots(phase_snapshot_path)
    result = reconcile_prediction_outcomes(
        history, snapshots, trading_days=trading_days,
        calendar_source="calendar_provider", phase_snapshots=phase_rows,
    )
    status = getattr(calendar.last_result, "status", None)
    result.update({
        "calendar_status": getattr(status, "value", str(status or "unknown")),
        "calendar_start": start,
        "calendar_end": end,
        "trading_day_count": len(trading_days),
    })
    return result
