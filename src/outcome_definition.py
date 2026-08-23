# -*- coding: utf-8 -*-
"""可重复计算的 T+1/T+3 结果定义与后验回填。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from prediction_review import append_outcome_once


@dataclass(frozen=True)
class OutcomeDefinition:
    outcome_definition_id: str
    schema_version: str
    t1_open_rules: tuple[str, ...]
    t1_intraday_rules: tuple[str, ...]
    t1_hit_rules: tuple[str, ...]
    t3_persistence_rules: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_outcome_definition() -> OutcomeDefinition:
    return OutcomeDefinition(
        outcome_definition_id="market-thesis/v1",
        schema_version="outcome/v1",
        t1_open_rules=(
            "requires explicit next_snapshot.open_return when scoring open trigger",
            "missing open_return remains unknown",
        ),
        t1_intraday_rules=(
            "breadth_ratio and limit_up/limit_down must be present",
            "repair scenarios require breadth and promotion_rate not to deteriorate",
        ),
        t1_hit_rules=(
            "scenario-specific rule is evaluated only from numeric facts",
            "missing facts are unknown, never False",
        ),
        t3_persistence_rules=(
            "repair/continuation requires breadth_ratio and promotion_rate to hold",
            "retreat requires breadth deterioration or negative limit-pool feedback",
        ),
    )


def _scenario_id(prediction: dict[str, Any]) -> str:
    explicit = prediction.get("primary_scenario_id") or prediction.get("scenario_id")
    if explicit:
        return str(explicit)
    plans = prediction.get("scenario_plans")
    if isinstance(plans, list) and plans:
        first = plans[0]
        if isinstance(first, dict) and first.get("scenario_id"):
            return str(first["scenario_id"])
    return str(prediction.get("scenario") or prediction.get("scene") or "base")


def _number(snapshot: dict[str, Any], key: str) -> float | None:
    value = snapshot.get(key)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _missing(snapshot: dict[str, Any], keys: Iterable[str]) -> list[str]:
    return [key for key in keys if _number(snapshot, key) is None]


def _primary_plan(prediction: dict[str, Any], scenario_id: str) -> dict[str, Any] | None:
    plans = prediction.get("scenario_plans")
    if not isinstance(plans, list):
        return None
    for plan in plans:
        if isinstance(plan, dict) and str(plan.get("scenario_id") or "") == scenario_id:
            return plan
    return None


def _evaluate_rule(
    rule: dict[str, Any], snapshot: dict[str, Any], previous: dict[str, Any],
) -> tuple[bool | None, str | None]:
    metric = str(rule.get("metric") or "").strip()
    operator = str(rule.get("operator") or "").strip().lower()
    actual = _number(snapshot, metric)
    if not metric or not operator:
        return None, "invalid_rule"
    if actual is None:
        return None, metric
    if operator.endswith("_baseline"):
        baseline_metric = str(rule.get("baseline_metric") or metric)
        expected = _number(previous, baseline_metric)
        if expected is None:
            return None, f"previous.{baseline_metric}"
        operator = operator.removesuffix("_baseline")
    else:
        try:
            expected = float(rule.get("value"))
        except (TypeError, ValueError):
            return None, f"rule_value.{metric}"
    comparisons = {
        "gte": actual >= expected, "lte": actual <= expected,
        "gt": actual > expected, "lt": actual < expected, "eq": actual == expected,
    }
    return comparisons.get(operator), None if operator in comparisons else f"operator.{operator}"


def _phase_snapshot_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    source = snapshot.get("phase_snapshots")
    if isinstance(source, dict):
        return {str(phase): row for phase, row in source.items() if isinstance(row, dict)}
    result: dict[str, dict[str, Any]] = {}
    if isinstance(source, list):
        for row in source:
            if isinstance(row, dict) and row.get("phase"):
                result[str(row["phase"])] = row
    return result


def _evaluate_trigger_rules(
    plan: dict[str, Any] | None, snapshot: dict[str, Any], previous: dict[str, Any],
) -> tuple[bool | None, dict[str, Any]]:
    trigger_map = plan.get("trigger_rules") if isinstance(plan, dict) else None
    if not isinstance(trigger_map, dict):
        return None, {"supporting_rule_ids": [], "invalidating_rule_ids": [], "missing_fields": []}
    phase_rows = _phase_snapshot_map(snapshot)
    supporting: list[str] = []
    invalidating: list[str] = []
    missing: list[str] = []
    evaluated = 0
    for phase in ("auction", "early_0935", "confirm_1000", "afternoon"):
        phase_row = phase_rows.get(phase)
        phase_metrics = phase_row.get("metrics") if isinstance(phase_row, dict) else None
        phase_metrics = phase_metrics if isinstance(phase_metrics, dict) else None
        for rule in trigger_map.get(phase, ()) or ():
            if not isinstance(rule, dict) or rule.get("required") is False:
                continue
            rule_id = str(rule.get("rule_id") or f"{phase}:unnamed")
            metric = str(rule.get("metric") or "unknown")
            if phase_metrics is None:
                missing.append(f"{phase}.{metric}")
                continue
            evaluated += 1
            matched, missing_field = _evaluate_rule(rule, phase_metrics, previous)
            if matched is True:
                supporting.append(rule_id)
            elif matched is False:
                invalidating.append(rule_id)
            elif missing_field:
                missing.append(f"{phase}.{missing_field}")
    if invalidating:
        triggered: bool | None = False
    elif evaluated and not missing:
        triggered = True
    else:
        triggered = None
    return triggered, {
        "supporting_rule_ids": supporting,
        "invalidating_rule_ids": invalidating,
        "missing_fields": sorted(set(missing)),
    }


def _scenario_trigger_states(
    prediction: dict[str, Any], snapshot: dict[str, Any], previous: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], str | None]:
    plans = prediction.get("scenario_plans")
    if not isinstance(plans, list):
        return {}, None
    states: dict[str, dict[str, Any]] = {}
    triggered_plans: list[dict[str, Any]] = []
    for plan in plans:
        if not isinstance(plan, dict) or not plan.get("scenario_id"):
            continue
        scenario_id = str(plan["scenario_id"])
        triggered, evidence = _evaluate_trigger_rules(plan, snapshot, previous)
        states[scenario_id] = {"triggered": triggered, **evidence}
        if triggered is True:
            triggered_plans.append(plan)
    def rank(plan: dict[str, Any]) -> tuple[int, float]:
        for priority, key in ((2, "prior_probability"), (1, "probability")):
            try:
                value = float(plan.get(key))
            except (TypeError, ValueError):
                continue
            if 0 <= value <= 1:
                return priority, value
        return 0, 0.0
    realized = max(triggered_plans, key=rank).get("scenario_id") if triggered_plans else None
    return states, str(realized) if realized else None


def evaluate_prediction_outcome(
    prediction: dict[str, Any], *, previous_snapshot: dict[str, Any] | None,
    next_snapshot: dict[str, Any] | None, horizon: str,
    definition: OutcomeDefinition | None = None,
    trigger_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对一个已落地快照打分；缺字段返回 unknown。"""
    definition = definition or default_outcome_definition()
    normalized = str(horizon).lower().replace("+", "")
    if normalized not in {"t1", "t3"}:
        raise ValueError("horizon 必须是 t1 或 t3")
    prev = previous_snapshot if isinstance(previous_snapshot, dict) else {}
    nxt = next_snapshot if isinstance(next_snapshot, dict) else {}
    trigger_facts = trigger_snapshot if isinstance(trigger_snapshot, dict) else nxt
    scenario_id = _scenario_id(prediction)
    primary_plan = _primary_plan(prediction, scenario_id)
    scenario_trigger_states, realized_scenario_id = _scenario_trigger_states(
        prediction, trigger_facts, prev,
    )
    base = {
        "definition_id": definition.outcome_definition_id,
        "schema_version": definition.schema_version,
        "horizon": normalized,
        "scenario_id": scenario_id,
        "realized_scenario_id": realized_scenario_id,
        "scenario_trigger_states": scenario_trigger_states,
        "as_of": nxt.get("report_date"),
    }

    # 开盘触发是独立指标；没有开盘收益时不把盘中事实冒充开盘结果。
    if normalized == "t1" and primary_plan is None and _number(nxt, "open_return") is not None:
        open_return = _number(nxt, "open_return")
        return {**base, "rule_id": "t1_open_return_non_negative", "triggered": True,
                "hit": open_return >= 0, "status": "scored",
                "actual": {"open_return": open_return}, "outcome_vector": {
                    "market_direction": "up" if open_return >= 0 else "down",
                    "breadth": "unknown", "relay_quality": "unknown",
                    "mainline_continuation": "unknown", "high_level_feedback": "unknown",
                    "middle_ladder_contagion": "unknown", "focus_pool_behavior": "unknown",
                    "position_boundary": "unknown",
                }, "missing_fields": []}

    required = ["breadth_ratio", "limit_up", "limit_down"]
    if scenario_id in {"repair_after_breadth_only", "mainline_continuation", "intraday_divergence_repair", "breadth_repair"}:
        required += ["promotion_rate"]
    missing = _missing(nxt, required)
    missing += [f"previous.{key}" for key in _missing(prev, ["breadth_ratio", "limit_up", "limit_down"])]
    if scenario_id in {"repair_after_breadth_only", "mainline_continuation", "intraday_divergence_repair", "breadth_repair"}:
        if _number(prev, "promotion_rate") is None:
            missing.append("previous.promotion_rate")
    if missing:
        return {**base, "rule_id": f"{normalized}_insufficient_facts", "triggered": None,
                "hit": None, "status": "unknown", "actual": {}, "outcome_vector": {},
                "missing_fields": sorted(set(missing))}

    breadth = _number(nxt, "breadth_ratio")
    prev_breadth = _number(prev, "breadth_ratio")
    limit_up = _number(nxt, "limit_up")
    limit_down = _number(nxt, "limit_down")
    prev_limit_down = _number(prev, "limit_down")
    promotion = _number(nxt, "promotion_rate")
    prev_promotion = _number(prev, "promotion_rate")

    if scenario_id in {"repair_after_breadth_only", "mainline_continuation", "intraday_divergence_repair", "breadth_repair"}:
        hit = breadth >= prev_breadth and promotion >= prev_promotion and limit_up > limit_down
        rule_id = f"{normalized}_breadth_relay_hold"
    elif scenario_id in {"high_level_retreat", "risk_off_observation"}:
        hit = breadth < prev_breadth or limit_down > prev_limit_down
        rule_id = f"{normalized}_negative_feedback"
    elif scenario_id == "selective_mainline_hold":
        hit = breadth < prev_breadth and limit_up > limit_down
        rule_id = f"{normalized}_selective_core_hold"
    else:
        hit = breadth >= 0.5 and limit_up > limit_down
        rule_id = f"{normalized}_market_breadth_positive"

    primary_state = scenario_trigger_states.get(scenario_id, {})
    triggered = primary_state.get("triggered")
    trigger_evidence = {
        "supporting_rule_ids": list(primary_state.get("supporting_rule_ids") or ()),
        "invalidating_rule_ids": list(primary_state.get("invalidating_rule_ids") or ()),
        "missing_fields": list(primary_state.get("missing_fields") or ()),
    }
    if triggered is None and primary_plan is None:
        # Legacy predictions did not store machine-readable trigger rules.
        triggered = True

    index_return = _number(nxt, "index_return")
    middle_support = _number(nxt, "middle_tier_support")
    diffusion = _number(nxt, "mainline_diffusion")
    focus_hit = nxt.get("focus_pool_hit") if isinstance(nxt.get("focus_pool_hit"), bool) else None
    executed_position = _number(nxt, "executed_position")
    position_source = primary_plan if isinstance(primary_plan, dict) else prediction
    position_floor = _number(position_source, "position_floor")
    position_ceiling = _number(position_source, "position_ceiling")
    outcome_vector = {
        "market_direction": ("up" if index_return > 0 else "down" if index_return < 0 else "flat") if index_return is not None else "unknown",
        "breadth": "improved" if breadth > prev_breadth else ("weakened" if breadth < prev_breadth else "flat"),
        "relay_quality": "improved" if promotion is not None and prev_promotion is not None and promotion > prev_promotion else (
            "weakened" if promotion is not None and prev_promotion is not None and promotion < prev_promotion else "unknown"
        ),
        "mainline_continuation": "confirmed" if diffusion is not None and diffusion >= .5 else ("failed" if diffusion is not None else "unknown"),
        "high_level_feedback": "negative" if limit_down > prev_limit_down else ("contained" if limit_down <= prev_limit_down else "unknown"),
        "middle_ladder_contagion": "supported" if middle_support is not None and middle_support >= .5 else ("weak" if middle_support is not None else "unknown"),
        "focus_pool_behavior": "hit" if focus_hit is True else ("miss" if focus_hit is False else "unknown"),
        "position_boundary": (
            "within" if executed_position is not None and position_floor is not None and position_ceiling is not None
            and position_floor <= executed_position <= position_ceiling else
            "outside" if executed_position is not None and position_floor is not None and position_ceiling is not None else "unknown"
        ),
    }
    return {
        **base, "rule_id": rule_id, "triggered": triggered, "trigger_evidence": trigger_evidence,
        "hit": bool(hit), "status": "scored",
        "actual": {
            "breadth_ratio": breadth, "limit_up": limit_up, "limit_down": limit_down,
            "promotion_rate": promotion, "previous_breadth_ratio": prev_breadth,
            "previous_promotion_rate": prev_promotion, "index_return": index_return,
            "mainline_diffusion": diffusion, "middle_tier_support": middle_support,
            "focus_pool_hit": focus_hit, "executed_position": executed_position,
        },
        "outcome_vector": outcome_vector,
        "missing_fields": [],
    }


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _snapshot_inventory(directory: str | Path) -> list[tuple[date, dict[str, Any]]]:
    rows: list[tuple[date, dict[str, Any]]] = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        snapshot = _load_json(path)
        if snapshot is not None:
            rows.append((day, snapshot))
    return sorted(rows, key=lambda item: item[0])


def reconcile_prediction_outcomes(
    history_path: str | Path, snapshots_dir: str | Path,
    *, definition: OutcomeDefinition | None = None,
    trading_days: Iterable[str] | None = None, calendar_source: str | None = None,
    phase_snapshots: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """扫描已成熟预测并按交易日历幂等追加 T+1/T+3 outcome。

    提供 trading_days 时严格按日历定位目标日；目标日缺快照不会顺延到下一
    个文件。未提供时为兼容旧维护流程，使用现有快照日期作为观测日历。
    """
    definition = definition or default_outcome_definition()
    history = Path(history_path)
    snapshots = _snapshot_inventory(snapshots_dir)
    snapshot_by_day = {day: snap for day, snap in snapshots}
    if trading_days is None:
        calendar_days = sorted(snapshot_by_day)
        resolved_calendar_source = calendar_source or "snapshot_inventory"
    else:
        calendar_days = []
        for value in trading_days:
            try:
                calendar_days.append(date.fromisoformat(str(value)))
            except ValueError:
                continue
        calendar_days = sorted(set(calendar_days))
        resolved_calendar_source = calendar_source or "provided_trading_calendar"

    phase_index: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in phase_snapshots or ():
        if not isinstance(row, dict):
            continue
        report_date = str(row.get("report_date") or "")
        trade_date = str(row.get("trade_date") or report_date)
        phase = str(row.get("phase") or "")
        if report_date and trade_date and phase:
            phase_index.setdefault((report_date, trade_date), {})[phase] = row

    events: list[dict[str, Any]] = []
    existing: set[tuple[str, str]] = set()
    if history.exists():
        for raw in history.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            events.append(event)
            if event.get("event_type") == "outcome":
                existing.add((str(event.get("prediction_id")), str(event.get("horizon"))))

    predictions = [event for event in events if event.get("event_type") == "prediction"]
    appended = unknown = skipped = missing_snapshots = 0
    for prediction in predictions:
        pid = str(prediction.get("prediction_id") or "")
        report_text = str(prediction.get("report_date") or "")
        try:
            report_day = date.fromisoformat(report_text)
        except ValueError:
            skipped += 1
            continue
        future_days = [day for day in calendar_days if day > report_day]
        previous = prediction.get("market_snapshot")
        if not isinstance(previous, dict):
            previous = snapshot_by_day.get(report_day)
        t1_day = future_days[0] if future_days else None
        t1_snapshot = dict(snapshot_by_day.get(t1_day) or {}) if t1_day else {}
        if t1_day:
            phase_map = phase_index.get((report_text, t1_day.isoformat())) or phase_index.get((t1_day.isoformat(), t1_day.isoformat()))
            if phase_map:
                t1_snapshot["phase_snapshots"] = phase_map
        for horizon, offset in (("t1", 1), ("t3", 3)):
            if (pid, horizon) in existing or len(future_days) < offset:
                continue
            expected_day = future_days[offset - 1]
            snap = snapshot_by_day.get(expected_day)
            if snap is None:
                missing_snapshots += 1
                continue
            outcome_snapshot = dict(snap)
            if horizon == "t1" and t1_snapshot.get("phase_snapshots"):
                outcome_snapshot["phase_snapshots"] = t1_snapshot["phase_snapshots"]
            actual = evaluate_prediction_outcome(
                prediction, previous_snapshot=previous, next_snapshot=outcome_snapshot,
                horizon=horizon, definition=definition, trigger_snapshot=t1_snapshot,
            )
            actual.update({
                "expected_trade_date": expected_day.isoformat(),
                "actual_trade_date": expected_day.isoformat(),
                "calendar_source": resolved_calendar_source,
            })
            if actual.get("hit") is None:
                unknown += 1
                continue
            append_outcome_once(history, pid, horizon, actual)
            existing.add((pid, horizon))
            appended += 1
    return {
        "appended": appended, "unknown": unknown, "skipped": skipped,
        "missing_snapshots": missing_snapshots, "calendar_source": resolved_calendar_source,
        "definition_id": definition.outcome_definition_id,
    }

