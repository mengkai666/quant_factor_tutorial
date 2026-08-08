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


def _binary_outcome(actual: Any) -> bool | None:
    """Extract a scored binary outcome without treating missing values as False."""
    if not isinstance(actual, dict):
        return None
    for key in ("hit", "success", "market_up", "focus_pool_hit"):
        value = actual.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
    direction = str(actual.get("market_direction", "") or "").strip().lower()
    if direction in {"up", "上涨", "positive", "bull"}:
        return True
    if direction in {"down", "下跌", "negative", "bear"}:
        return False
    return None


def _wilson_interval(successes: int, trials: int, z: float = 1.96) -> dict[str, Any] | None:
    if trials <= 0:
        return None
    n = float(trials)
    p = float(successes) / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denominator
    margin = z * ((p * (1.0 - p) / n + z * z / (4.0 * n * n)) ** 0.5) / denominator
    return {"lower": max(0.0, centre - margin), "upper": min(1.0, centre + margin), "successes": int(successes), "trials": int(trials)}


def build_prediction_review(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    predictions: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    orphan_outcomes = 0
    if target.exists():
        for raw in target.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            events.append(event)
            pid = str(event.get("prediction_id", ""))
            if event.get("event_type") == "prediction" and pid:
                predictions[pid] = {**event, "outcomes": {}}

        # Join outcomes only after the prediction inventory is known. Orphan
        # outcomes remain auditable but must not inflate the prediction sample.
        for event in events:
            if event.get("event_type") != "outcome":
                continue
            pid = str(event.get("prediction_id", ""))
            if pid not in predictions:
                orphan_outcomes += 1
                continue
            horizon = str(event.get("horizon", "")).lower().replace("+", "")
            if horizon in {"t1", "t3"}:
                predictions[pid].setdefault("outcomes", {})[horizon] = event.get("actual", {})

    status_counts = {"pending": 0, "incomplete": 0, "matured": 0}
    matured_ids: list[str] = []
    t3_scored: list[bool] = []
    brier_values: list[float] = []
    for pid, row in predictions.items():
        outcomes = row.get("outcomes") if isinstance(row.get("outcomes"), dict) else {}
        present = {key for key in ("t1", "t3") if key in outcomes}
        if present == {"t1", "t3"}:
            status = "matured"
            matured_ids.append(pid)
        elif present:
            status = "incomplete"
        else:
            status = "pending"
        row["outcome_status"] = status
        status_counts[status] += 1

        # T+3 is the primary terminal outcome. Score only explicit outcomes
        # and explicit probabilities; absence is not a zero score.
        if status == "matured":
            actual = _binary_outcome(outcomes.get("t3"))
            probability = row.get("probability", row.get("predicted_probability"))
            try:
                probability = float(probability) if probability is not None else None
            except (TypeError, ValueError):
                probability = None
            if actual is not None:
                t3_scored.append(actual)
                if probability is not None and 0.0 <= probability <= 1.0:
                    brier_values.append((probability - float(actual)) ** 2)

    scored_trials = len(t3_scored)
    scored_successes = sum(t3_scored)
    prediction_count = len(predictions)
    completed = status_counts["matured"]
    return {
        "predictions": predictions,
        "events": events,
        "prediction_count": prediction_count,
        "completed_count": completed,
        "matured_count": completed,
        "pending_count": status_counts["pending"],
        "incomplete_count": status_counts["incomplete"],
        "status_counts": status_counts,
        "matured_ids": matured_ids,
        "scored_count": scored_trials,
        "scored_successes": scored_successes,
        "hit_rate": (scored_successes / scored_trials) if scored_trials else None,
        "confidence_interval": _wilson_interval(scored_successes, scored_trials),
        "brier_score": (sum(brier_values) / len(brier_values)) if brier_values else None,
        "brier_sample_count": len(brier_values),
        "orphan_outcome_count": orphan_outcomes,
    }

