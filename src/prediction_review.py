# -*- coding: utf-8 -*-
"""预测与 T+1/T+3 结果的 append-only 事件日志。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def _append(path: str | Path, event: dict[str, Any]) -> dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(event)
    payload.setdefault("recorded_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return payload


def append_prediction(path: str | Path, prediction: dict[str, Any]) -> dict[str, Any]:
    payload = dict(prediction)
    payload["event_type"] = "prediction"
    if not payload.get("prediction_id"):
        payload["prediction_id"] = f"{payload.get('report_date', 'unknown')}:{payload.get('scenario', payload.get('scene', 'base'))}"
    return _append(path, payload)



def append_prediction_once(path: str | Path, prediction: dict[str, Any]) -> dict[str, Any]:
    """按 prediction_id 幂等追加预测，避免同一交易日重跑产生重复样本。"""
    payload = dict(prediction)
    if not payload.get("prediction_id"):
        payload["prediction_id"] = f"{payload.get('report_date', 'unknown')}:{payload.get('scenario', payload.get('scene', 'base'))}"
    prediction_id = str(payload["prediction_id"])
    target = Path(path)
    if target.exists():
        for raw in target.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event.get("event_type") == "prediction" and str(event.get("prediction_id")) == prediction_id:
                return {**event, "appended": False}
    saved = append_prediction(target, payload)
    return {**saved, "appended": True}
def append_outcome(path: str | Path, prediction_id: str, horizon: str, actual: dict[str, Any]) -> dict[str, Any]:
    normalized = str(horizon).lower().replace("+", "")
    if normalized not in {"t1", "t3"}:
        raise ValueError("horizon 必须是 t1 或 t3")
    return _append(path, {"event_type": "outcome", "prediction_id": prediction_id, "horizon": normalized, "actual": dict(actual)})


def build_prediction_review(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    predictions: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    if target.exists():
        for raw in target.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            events.append(event)
            pid = str(event.get("prediction_id", ""))
            if event.get("event_type") == "prediction":
                predictions[pid] = {**event, "outcomes": {}}
            elif event.get("event_type") == "outcome":
                predictions.setdefault(pid, {"prediction_id": pid, "outcomes": {}}).setdefault("outcomes", {})[str(event.get("horizon"))] = event.get("actual", {})
    completed = sum({"t1", "t3"}.issubset(set(row.get("outcomes", {}))) for row in predictions.values())
    prediction_count = len(predictions)
    return {
        "predictions": predictions,
        "events": events,
        "prediction_count": prediction_count,
        "completed_count": completed,
        "matured_count": completed,
        "pending_count": max(0, prediction_count - completed),
    }
