# -*- coding: utf-8 -*-
"""预测与 T+1/T+3 结果的 append-only 事件日志。"""
from __future__ import annotations

import hashlib
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


def build_prediction_snapshot(
    *, report_date: str, as_of_phase: str, prediction_version: str,
    market_thesis: dict[str, Any], scenario_plans: list[dict[str, Any]],
    market_snapshot: dict[str, Any], focus_pool: list[dict[str, Any]],
    facts_fingerprint: str, supersedes_prediction_id: str | None = None,
    generated_at: str | None = None, outcome_definition_id: str = "market-thesis/v1",
    publication_mode: str | None = None, extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the complete immutable prediction snapshot before append."""
    generated_at = generated_at or datetime.now().astimezone().isoformat(timespec="seconds")
    fingerprint = str(facts_fingerprint or "").strip()
    if not fingerprint:
        canonical = json.dumps(
            {"market_thesis": market_thesis, "scenario_plans": scenario_plans, "market_snapshot": market_snapshot},
            ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    payload = {
        "prediction_schema": "market-prediction/v2",
        "prediction_id": f"{report_date}:{as_of_phase}:{prediction_version}:{fingerprint[:12]}",
        "prediction_version": str(prediction_version),
        "report_date": str(report_date),
        "as_of_phase": str(as_of_phase),
        "generated_at": generated_at,
        "supersedes_prediction_id": supersedes_prediction_id,
        "facts_fingerprint": fingerprint,
        "publication_mode": publication_mode,
        "market_thesis": json.loads(json.dumps(market_thesis, ensure_ascii=False, default=str)),
        "scenario_plans": json.loads(json.dumps(scenario_plans, ensure_ascii=False, default=str)),
        "market_snapshot": json.loads(json.dumps(market_snapshot, ensure_ascii=False, default=str)),
        "focus_pool": json.loads(json.dumps(focus_pool, ensure_ascii=False, default=str)),
        "outcome_definition_id": outcome_definition_id,
    }
    if scenario_plans:
        def _plan_rank(plan: dict[str, Any]) -> tuple[int, float]:
            try:
                prior = float(plan.get("prior_probability"))
                if 0 <= prior <= 1:
                    return 2, prior
            except (TypeError, ValueError):
                pass
            try:
                probability = float(plan.get("probability"))
                if 0 <= probability <= 1:
                    return 1, probability
            except (TypeError, ValueError):
                pass
            return 0, 0.0
        primary = max(scenario_plans, key=_plan_rank)
        payload["primary_scenario_id"] = primary.get("scenario_id")
    payload.update(dict(extra or {}))
    return payload


def latest_prediction_id(
    path: str | Path, *, report_date: str, as_of_phase: str,
) -> str | None:
    target = Path(path)
    latest: str | None = None
    if not target.exists():
        return None
    for raw in target.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if (isinstance(event, dict) and event.get("event_type") == "prediction"
                and str(event.get("report_date")) == str(report_date)
                and str(event.get("as_of_phase") or "close") == str(as_of_phase)):
            latest = str(event.get("prediction_id") or "") or latest
    return latest



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


def append_outcome_once(path: str | Path, prediction_id: str, horizon: str, actual: dict[str, Any]) -> dict[str, Any]:
    """按 prediction_id+horizon 幂等追加 outcome。"""
    normalized = str(horizon).lower().replace("+", "")
    if normalized not in {"t1", "t3"}:
        raise ValueError("horizon 必须是 t1 或 t3")
    target = Path(path)
    if target.exists():
        for raw in target.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if (event.get("event_type") == "outcome"
                    and str(event.get("prediction_id")) == str(prediction_id)
                    and str(event.get("horizon")) == normalized):
                return {**event, "appended": False}
    saved = append_outcome(target, prediction_id, normalized, actual)
    return {**saved, "appended": True}


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


def _scenario_probability(prediction: dict[str, Any]) -> float | None:
    direct = prediction.get("probability", prediction.get("predicted_probability"))
    try:
        value = float(direct) if direct is not None else None
    except (TypeError, ValueError):
        value = None
    if value is not None and 0 <= value <= 1:
        return value
    primary = str(prediction.get("primary_scenario_id") or prediction.get("scenario_id") or "")
    plans = prediction.get("scenario_plans")
    if isinstance(plans, list):
        for plan in plans:
            if not isinstance(plan, dict):
                continue
            scenario_id = str(plan.get("scenario_id") or "")
            if primary and scenario_id != primary:
                continue
            try:
                value = float(plan.get("probability"))
            except (TypeError, ValueError):
                continue
            if 0 <= value <= 1:
                return value
    return None


def _triggered(actual: Any) -> bool | None:
    if not isinstance(actual, dict):
        return None
    value = actual.get("triggered")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return None


def _metric_value(actual: dict[str, Any], key: str) -> float | None:
    source = actual.get("actual") if isinstance(actual.get("actual"), dict) else actual
    try:
        value = source.get(key)
        return float(value) if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def build_scenario_calibration(path: str | Path, *, min_samples: int = 8) -> dict[str, Any]:
    """Estimate occurrence priors, conditional hit rates, and rule thresholds."""
    review = build_prediction_review(path)
    buckets: dict[str, list[tuple[dict[str, Any], bool]]] = {}
    occurrence_counts: dict[str, int] = {}
    known_scenarios: set[str] = set()
    for prediction in review["predictions"].values():
        plans = prediction.get("scenario_plans")
        if isinstance(plans, list):
            known_scenarios.update(
                str(plan.get("scenario_id")) for plan in plans
                if isinstance(plan, dict) and plan.get("scenario_id")
            )
        outcomes = prediction.get("outcomes") if isinstance(prediction.get("outcomes"), dict) else {}
        t1 = outcomes.get("t1") if isinstance(outcomes.get("t1"), dict) else {}
        realized = str(t1.get("realized_scenario_id") or "")
        occurrence_id = realized or str(
            prediction.get("primary_scenario_id") or prediction.get("scenario_id") or ""
        )
        if occurrence_id:
            known_scenarios.add(occurrence_id)
            occurrence_counts[occurrence_id] = occurrence_counts.get(occurrence_id, 0) + 1

        actual = outcomes.get("t3")
        hit = _binary_outcome(actual)
        if hit is None or _triggered(actual) is not True:
            continue
        scenario_id = str(
            (actual.get("realized_scenario_id") if isinstance(actual, dict) else None)
            or prediction.get("primary_scenario_id") or prediction.get("scenario_id") or ""
        )
        if not scenario_id and isinstance(plans, list) and plans and isinstance(plans[0], dict):
            scenario_id = str(plans[0].get("scenario_id") or "")
        if scenario_id:
            known_scenarios.add(scenario_id)
            buckets.setdefault(scenario_id, []).append((actual, hit))

    scenarios: dict[str, Any] = {}
    total_occurrences = sum(occurrence_counts.values())
    scenario_count = len(known_scenarios)
    direction = {
        "mainline_continuation": "gte", "selective_mainline_hold": "gte",
        "repair_after_breadth_only": "gte", "breadth_repair": "gte",
        "repair_confirmation": "gte", "intraday_divergence_repair": "gte",
        "low_level_diffusion": "gte", "high_level_retreat": "lte",
        "risk_off_observation": "lte",
    }
    for scenario_id in sorted(known_scenarios):
        samples = buckets.get(scenario_id, [])
        count = len(samples)
        successes = sum(1 for _, hit in samples if hit)
        conditional_probability = (successes + 1.0) / (count + 2.0)
        thresholds: dict[str, Any] = {}
        if count >= min_samples:
            for metric in ("breadth_ratio", "promotion_rate", "limit_down"):
                labelled = [(value, hit) for actual, hit in samples if (value := _metric_value(actual, metric)) is not None]
                if not labelled:
                    continue
                base_operator = direction.get(scenario_id, "gte")
                operator = (
                    ("lte" if base_operator == "gte" else "gte")
                    if metric == "limit_down" else base_operator
                )
                candidates = sorted({value for value, _ in labelled})
                def accuracy(threshold: float) -> float:
                    predicted = [value >= threshold if operator == "gte" else value <= threshold for value, _ in labelled]
                    return sum(int(pred == hit) for pred, (_, hit) in zip(predicted, labelled)) / len(labelled)
                best = max(candidates, key=lambda value: (accuracy(value), -value if operator == "gte" else value))
                thresholds[metric] = {
                    "operator": operator, "value": round(best, 4),
                    "sample_size": len(labelled), "training_accuracy": round(accuracy(best), 6),
                }
        occurrence_count = occurrence_counts.get(scenario_id, 0)
        prior_probability = (
            (occurrence_count + 1.0) / (total_occurrences + scenario_count)
            if total_occurrences and scenario_count else None
        )
        scenarios[scenario_id] = {
            "sample_size": count, "successes": successes,
            "occurrence_count": occurrence_count,
            "scenario_prior_probability": (
                round(prior_probability, 6)
                if prior_probability is not None and total_occurrences >= min_samples else None
            ),
            "calibrated_probability": round(conditional_probability, 6) if count >= min_samples else None,
            "raw_hit_rate": successes / count if count else None,
            "thresholds": thresholds,
            "status": "calibrated" if count >= min_samples else "insufficient_samples",
            "prior_status": "calibrated" if total_occurrences >= min_samples else "insufficient_samples",
        }
    return {
        "schema_version": "scenario-calibration/v2", "min_samples": min_samples,
        "occurrence_sample_size": total_occurrences, "scenarios": scenarios,
    }


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
    def empty_metrics() -> dict[str, Any]:
        return {"outcome_count": 0, "trigger_known": 0, "triggered": 0, "scored_count": 0, "hits": 0, "brier_values": []}

    horizon_metrics = {horizon: empty_metrics() for horizon in ("t1", "t3")}
    scenario_metrics: dict[str, dict[str, dict[str, Any]]] = {}
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

        probability = _scenario_probability(row)
        for horizon in ("t1", "t3"):
            if horizon not in outcomes:
                continue
            actual_row = outcomes[horizon]
            primary_scenario_id = str(row.get("primary_scenario_id") or row.get("scenario_id") or "")
            if not primary_scenario_id:
                plans = row.get("scenario_plans")
                if isinstance(plans, list) and plans and isinstance(plans[0], dict):
                    primary_scenario_id = str(plans[0].get("scenario_id") or "")
            metric_targets = [horizon_metrics[horizon]]
            if primary_scenario_id:
                by_horizon = scenario_metrics.setdefault(
                    primary_scenario_id, {name: empty_metrics() for name in ("t1", "t3")}
                )
                metric_targets.append(by_horizon[horizon])
            triggered = _triggered(actual_row)
            actual_hit = _binary_outcome(actual_row)
            for metrics in metric_targets:
                metrics["outcome_count"] += 1
                if triggered is not None:
                    metrics["trigger_known"] += 1
                    metrics["triggered"] += int(triggered)
                if actual_hit is not None and triggered is True:
                    metrics["scored_count"] += 1
                    metrics["hits"] += int(actual_hit)
                    if probability is not None:
                        metrics["brier_values"].append((probability - float(actual_hit)) ** 2)

        # T+3 is the primary terminal outcome. Score only explicit outcomes
        # and explicit probabilities; absence is not a zero score.
        if status == "matured":
            actual = _binary_outcome(outcomes.get("t3"))
            probability = _scenario_probability(row)
            if actual is not None:
                t3_scored.append(actual)
                if probability is not None and 0.0 <= probability <= 1.0:
                    brier_values.append((probability - float(actual)) ** 2)

    scored_trials = len(t3_scored)
    scored_successes = sum(t3_scored)
    prediction_count = len(predictions)
    completed = status_counts["matured"]
    def finalize_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
        briers = list(metrics.get("brier_values") or ())
        base = {key: value for key, value in metrics.items() if key != "brier_values"}
        return {
            **base,
            "trigger_rate": metrics["triggered"] / metrics["trigger_known"] if metrics["trigger_known"] else None,
            "hit_rate": metrics["hits"] / metrics["scored_count"] if metrics["scored_count"] else None,
            "brier_score": sum(briers) / len(briers) if briers else None,
            "brier_sample_count": len(briers),
        }

    metrics_by_horizon = {horizon: finalize_metrics(metrics) for horizon, metrics in horizon_metrics.items()}
    metrics_by_scenario = {
        scenario_id: {horizon: finalize_metrics(metrics) for horizon, metrics in by_horizon.items()}
        for scenario_id, by_horizon in scenario_metrics.items()
    }

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
        "metrics_by_horizon": metrics_by_horizon,
        "metrics_by_scenario": metrics_by_scenario,
    }


def build_prediction_review_with_reconciliation(
    history_path: str | Path, snapshots_dir: str | Path,
) -> dict[str, Any]:
    """先尝试回填成熟的 T+1/T+3，再构建供报告渲染的复盘摘要。

    后验回填属于增强能力，快照损坏或字段漂移不能阻断日报；失败会被
    保留在 ``outcome_reconciliation`` 中，主复盘仍基于已有事件日志生成。
    """
    try:
        # 局部导入避免 outcome_definition -> prediction_review 的循环依赖。
        from outcome_definition import (
            default_outcome_definition,
            reconcile_prediction_outcomes,
        )

        definition = default_outcome_definition()
        reconciliation = reconcile_prediction_outcomes(
            history_path, snapshots_dir, definition=definition,
        )
        reconciliation = {
            "status": "ok",
            **reconciliation,
        }
    except Exception as exc:  # pragma: no cover - exercised via failure test
        try:
            from outcome_definition import default_outcome_definition
            definition_id = default_outcome_definition().outcome_definition_id
        except Exception:
            definition_id = "unknown"
        reconciliation = {
            "status": "failed",
            "appended": 0,
            "unknown": 0,
            "skipped": 0,
            "definition_id": definition_id,
            "error": str(exc),
        }

    review = build_prediction_review(history_path)
    review["outcome_reconciliation"] = reconciliation
    return review
