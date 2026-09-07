# -*- coding: utf-8 -*-
"""基于阶段快照更新明日推演场景后验。"""
from __future__ import annotations

import math
from typing import Any, Iterable

from market_snapshot import PHASE_ORDER, latest_phase_snapshots, select_bound_phase_snapshots, snapshot_qualification_issues


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "" or isinstance(value, bool):
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _plan_dict(plan: Any) -> dict[str, Any]:
    if hasattr(plan, "to_dict"):
        value = plan.to_dict()
    elif isinstance(plan, dict):
        value = dict(plan)
    else:
        value = {}
    return value


def _prior_probabilities(plans: list[dict[str, Any]]) -> dict[str, float] | None:
    values: dict[str, float] = {}
    for plan in plans:
        scenario_id = str(plan.get("scenario_id") or "")
        probability = _number(plan.get("prior_probability"))
        if "prior_probability" not in plan:
            # Legacy-only field; an explicit missing occurrence prior is not
            # a license to substitute conditional success probability.
            probability = _number(plan.get("probability"))
        if not scenario_id or probability is None or not 0 <= probability <= 1:
            return None
        values[scenario_id] = probability
    total = sum(values.values())
    if not values or total <= 0:
        return None
    return {key: value / total for key, value in values.items()}


def _metric(metrics: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(metrics.get(key))
        if value is not None:
            return value
    return None


def _rule_metric(metrics: dict[str, Any], metric: str) -> float | None:
    aliases = {
        "promotion_rate": ("promotion_rate", "relay_quality_score"),
        "breadth_ratio": ("breadth_ratio",),
        "limit_down": ("limit_down",),
        "limit_up": ("limit_up",),
        "mainline_diffusion": ("mainline_diffusion",),
    }
    value = _metric(metrics, *aliases.get(metric, (metric,)))
    if value is None:
        return None
    if metric in {"breadth_ratio", "promotion_rate", "mainline_diffusion", "reopen_rate", "炸板率"} and not 0 <= value <= 1:
        return None
    if metric in {"limit_down", "limit_up", "up_count", "down_count", "flat_count"} and (value < 0 or not value.is_integer()):
        return None
    return value


def _evaluate_rule(
    rule: dict[str, Any], metrics: dict[str, Any], baseline_metrics: dict[str, Any],
) -> tuple[bool | None, str | None]:
    metric = str(rule.get("metric") or "").strip()
    operator = str(rule.get("operator") or "").strip().lower()
    if not metric or not operator:
        return None, "invalid_rule"
    actual = _rule_metric(metrics, metric)
    if actual is None:
        return None, metric
    if operator.endswith("_baseline"):
        baseline_metric = str(rule.get("baseline_metric") or metric)
        expected = _rule_metric(baseline_metrics, baseline_metric)
        if expected is None:
            return None, f"close.{baseline_metric}"
        operator = operator.removesuffix("_baseline")
    else:
        expected = _number(rule.get("value"))
        if expected is None:
            return None, f"rule_value:{metric}"
    comparisons = {
        "gte": actual >= expected, "lte": actual <= expected,
        "gt": actual > expected, "lt": actual < expected,
        "eq": actual == expected,
    }
    return comparisons.get(operator), None if operator in comparisons else f"operator:{operator}"


def _evidence_from_rules(
    plan: dict[str, Any], metrics: dict[str, Any], baseline: dict[str, Any] | None, phase: str,
) -> dict[str, Any] | None:
    trigger_map = plan.get("trigger_rules")
    if not isinstance(trigger_map, dict):
        return None
    baseline_metrics = (baseline or {}).get("metrics") if isinstance(baseline, dict) else {}
    baseline_metrics = baseline_metrics if isinstance(baseline_metrics, dict) else {}
    support: list[str] = []
    opposing: list[str] = []
    hard_invalidations: list[str] = []
    unmet_required: list[str] = []
    missing: list[str] = []
    missing_required: list[str] = []
    score = 0.0
    for rules, is_invalidation in (
        (trigger_map.get(phase, ()) or (), False),
        (plan.get("invalidation_rules", ()) or (), True),
    ):
        for raw_rule in rules:
            if not isinstance(raw_rule, dict):
                missing.append("invalid_rule")
                missing_required.append("invalid_rule")
                continue
            rule_id = str(raw_rule.get("rule_id") or f"{phase}:unnamed")
            required = raw_rule.get("required", True) is not False
            matched, missing_field = _evaluate_rule(raw_rule, metrics, baseline_metrics)
            if matched is None:
                field = missing_field or "invalid_rule"
                missing.append(field)
                if required:
                    missing_required.append(field)
                continue
            weight = _number(raw_rule.get("weight"))
            weight = weight if weight is not None and weight >= 0 else 1.0
            if is_invalidation:
                if matched:
                    hard_invalidations.append(rule_id)
                    opposing.append(rule_id)
                    score -= weight
            elif matched:
                support.append(rule_id)
                score += weight
            else:
                opposing.append(rule_id)
                score -= weight
                if required:
                    unmet_required.append(rule_id)
    return {
        "evidence_score": score,
        "supporting_evidence_ids": support,
        "invalidating_evidence_ids": opposing,
        "hard_invalidation_evidence_ids": hard_invalidations,
        "unmet_required_evidence_ids": unmet_required,
        "missing_fields": sorted(set(missing)),
        "missing_required_fields": sorted(set(missing_required)),
    }


def _evidence_for_plan(
    scenario_id: str, metrics: dict[str, Any], baseline: dict[str, Any] | None,
    phase: str,
) -> tuple[float, list[str], list[str], list[str]]:
    """返回 score、支持证据、失效证据、缺失字段。"""
    support: list[str] = []
    invalidating: list[str] = []
    missing: list[str] = []
    breadth = _metric(metrics, "breadth_ratio")
    promotion = _metric(metrics, "promotion_rate", "relay_quality_score")
    limit_down = _metric(metrics, "limit_down")
    diffusion = _metric(metrics, "mainline_diffusion")
    baseline_metrics = (baseline or {}).get("metrics") if isinstance(baseline, dict) else {}
    baseline_metrics = baseline_metrics if isinstance(baseline_metrics, dict) else {}
    breadth_base = _metric(baseline_metrics, "breadth_ratio")
    promotion_base = _metric(baseline_metrics, "promotion_rate", "relay_quality_score")
    limit_down_base = _metric(baseline_metrics, "limit_down")

    if breadth is None:
        missing.append("breadth_ratio")
    if promotion is None:
        missing.append("promotion_rate")

    if scenario_id in {"mainline_continuation", "selective_mainline_hold"}:
        if breadth is not None:
            (support if breadth >= 0.65 else invalidating).append(f"{phase}:breadth_{'strong' if breadth >= 0.65 else 'weak'}")
        if promotion is not None:
            (support if promotion >= 0.60 else invalidating).append(f"{phase}:relay_{'strong' if promotion >= 0.60 else 'weak'}")
        if diffusion is not None:
            (support if diffusion >= 0.50 else invalidating).append(f"{phase}:mainline_diffusion_{'present' if diffusion >= 0.50 else 'weak'}")
        if limit_down is not None and limit_down_base is not None:
            (support if limit_down <= limit_down_base else invalidating).append(f"{phase}:limit_down_{'contained' if limit_down <= limit_down_base else 'expanded'}")
    elif scenario_id in {"repair_after_breadth_only", "breadth_repair", "repair_confirmation", "intraday_divergence_repair"}:
        if breadth is not None and breadth_base is not None:
            (support if breadth >= breadth_base else invalidating).append(f"{phase}:breadth_{'repaired' if breadth >= breadth_base else 'deteriorated'}")
        if promotion is not None and promotion_base is not None:
            (support if promotion >= promotion_base else invalidating).append(f"{phase}:relay_{'held' if promotion >= promotion_base else 'deteriorated'}")
        if diffusion is not None:
            (support if diffusion >= 0.50 else invalidating).append(f"{phase}:mainline_diffusion_{'present' if diffusion >= 0.50 else 'absent'}")
        if breadth_base is None:
            missing.append("close.breadth_ratio")
        if promotion_base is None:
            missing.append("close.promotion_rate")
    elif scenario_id in {"high_level_retreat", "risk_off_observation"}:
        if breadth is not None and breadth_base is not None:
            (support if breadth < breadth_base else invalidating).append(f"{phase}:breadth_{'weaker' if breadth < breadth_base else 'not_weaker'}")
        if promotion is not None and promotion_base is not None:
            (support if promotion < promotion_base else invalidating).append(f"{phase}:relay_{'weaker' if promotion < promotion_base else 'not_weaker'}")
        if limit_down is not None and limit_down_base is not None:
            (support if limit_down > limit_down_base else invalidating).append(f"{phase}:limit_down_{'expanded' if limit_down > limit_down_base else 'contained'}")
        if breadth_base is None:
            missing.append("close.breadth_ratio")
        if promotion_base is None:
            missing.append("close.promotion_rate")
    else:
        # 未配置规则的场景不被默认判定为支持或失效。
        missing.append(f"rule:{scenario_id}")

    score = float(len(support) - len(invalidating))
    return score, support, invalidating, sorted(set(missing))


def build_scenario_posterior_timeline(
    plans: Iterable[Any], snapshots: Iterable[dict[str, Any]],
    *, report_date: str | None = None, trade_date: str | None = None, prediction_id: str | None = None,
    eligible_strategy_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """按 close → auction → 9:35 → 10:00 → afternoon 更新场景后验。

    若场景没有完整、可审计的先验概率，仍返回证据和相对分数，但不发布
    伪造的百分比。这样可以先接入盘中状态机，再等历史样本或规则校准后
    开启概率展示。
    """
    plan_rows = [_plan_dict(plan) for plan in plans]
    plan_rows = [row for row in plan_rows if row.get("scenario_id")]
    # Unbound legacy calculations remain useful for analysis, not execution.
    rejected: list[dict[str, Any]] = []
    snapshots = list(snapshots)
    if prediction_id is not None:
        linked = []
        for raw in snapshots:
            lineage = raw.get("source_lineage") if isinstance(raw, dict) else {}
            if isinstance(raw, dict) and raw.get("phase") != "close" and (lineage or {}).get("prediction_id") != prediction_id:
                rejected.append({"snapshot_id": raw.get("snapshot_id"), "phase": raw.get("phase"),
                                 "report_date": raw.get("report_date"), "trade_date": raw.get("trade_date"),
                                 "issues": ["prediction_id_mismatch"], "selected_latest": False})
            else:
                linked.append(raw)
        snapshots = linked
    if report_date is not None:
        by_phase, invalid_snapshots = select_bound_phase_snapshots(snapshots, report_date=report_date, trade_date=trade_date)
        rejected.extend(invalid_snapshots)
    else:
        by_phase = latest_phase_snapshots(snapshots)
    binding_status = "unbound" if report_date is None else "bound" if trade_date else "target_date_missing"
    baseline = by_phase.get("close")
    latest_qualified_order = max((PHASE_ORDER.index(phase) for phase in by_phase), default=0)
    confirmation_blocked = any(
        row.get("selected_latest") and row.get("report_date") == report_date
        and row.get("trade_date") == trade_date and row.get("phase") in PHASE_ORDER[1:]
        and PHASE_ORDER.index(row["phase"]) >= latest_qualified_order for row in rejected
    )
    evidence_history: dict[str, list[dict[str, Any]]] = {}
    for raw in snapshots:
        normalized = latest_phase_snapshots([raw])
        for phase, observation in normalized.items():
            if phase == "close" or (report_date is not None and snapshot_qualification_issues(
                    observation, report_date=report_date, trade_date=trade_date)):
                continue
            evidence_history.setdefault(phase, []).append(observation)
    priors = _prior_probabilities(plan_rows)
    weights = dict(priors or {str(row["scenario_id"]): 1.0 for row in plan_rows})
    timeline: list[dict[str, Any]] = []
    allowed = set(eligible_strategy_ids) if eligible_strategy_ids is not None else None
    previous_top: str | None = None
    previous_active: str | None = None
    invalidated: dict[str, list[str]] = {}

    for phase in PHASE_ORDER:
        # Historical hard invalidations survive even when a newer bad record
        # makes this phase unavailable. Accumulate independently of display.
        for plan in plan_rows:
            scenario_id = str(plan["scenario_id"])
            history = invalidated.setdefault(scenario_id, [])
            for observation in evidence_history.get(phase, []):
                earlier = _evidence_from_rules(plan, observation.get("metrics") or {}, baseline, phase)
                if earlier:
                    history.extend(item for item in earlier["hard_invalidation_evidence_ids"] if item not in history)
        record = by_phase.get(phase)
        if record is None:
            continue
        phase_rows: list[dict[str, Any]] = []
        for plan in plan_rows:
            scenario_id = str(plan["scenario_id"])
            metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
            # A report-day close is the baseline for tomorrow, never tomorrow's confirmation.
            evidence = _evidence_from_rules(plan, metrics, baseline, phase) if phase != "close" else None
            if evidence is None:
                score, support, opposing, missing = (
                    _evidence_for_plan(scenario_id, metrics, baseline, phase)
                    if phase != "close" else (0.0, [], [], [])
                )
                evidence = {
                    "evidence_score": score, "supporting_evidence_ids": support,
                    "invalidating_evidence_ids": opposing, "missing_fields": missing,
                    "missing_required_fields": missing, "unmet_required_evidence_ids": [],
                    "hard_invalidation_evidence_ids": opposing if score < 0 else [],
                }
            score = evidence["evidence_score"]
            history = invalidated.setdefault(scenario_id, [])
            history.extend(item for item in evidence["hard_invalidation_evidence_ids"] if item not in history)
            evidence["invalidation_history_ids"] = list(history)
            if phase == "close":
                state = "neutral"
            elif history:
                state = "invalidated"
            elif evidence["missing_required_fields"]:
                state = "unknown"
            elif evidence["unmet_required_evidence_ids"]:
                state = "neutral"
            else:
                state = "supported" if score > 0 else "neutral"
            if phase != "close" and score:
                weights[scenario_id] *= math.exp(max(-100.0, min(100.0, 0.55 * score)))
            phase_rows.append({
                "scenario_id": scenario_id,
                "prior_probability": priors.get(scenario_id) if priors else None,
                **evidence,
                "state": state,
            })
        total = sum(weights.values())
        top = max(weights, key=weights.get) if weights else None
        top_row = next((row for row in phase_rows if row["scenario_id"] == top), {})
        applicable = [row for row in phase_rows if allowed is None or row["scenario_id"] in allowed]
        valid = [row for row in applicable if row["state"] != "invalidated"]
        confirmed = [row for row in valid if row["state"] == "supported"]
        active = max(valid, key=lambda row: weights[row["scenario_id"]])["scenario_id"] if valid and phase != "close" else None
        decision = max(confirmed, key=lambda row: weights[row["scenario_id"]])["scenario_id"] if confirmed else None
        scenario_status = (
            "baseline_only" if phase == "close" else
            "no_valid_scenario" if applicable and not valid else
            "strategy_unavailable" if allowed is not None and not applicable else
            "active" if decision else "awaiting_confirmation"
        )
        transition_from = previous_top if previous_top and top != previous_top else None
        timeline.append({
            "phase": phase,
            "snapshot_id": record.get("snapshot_id"),
            "report_date": record.get("report_date"),
            "trade_date": record.get("trade_date"),
            "captured_at": record.get("captured_at"),
            "target_trade_date": trade_date, "prediction_id": prediction_id,
            "confirmation_eligible": bool(binding_status == "bound" and phase != "close"),
            "snapshot_quality": record.get("quality"),
            "source_lineage": record.get("source_lineage"),
            "top_scenario_id": top,  # Legacy display-only alias; NOT execution permission.
            "top_ranked_scenario_id": top,
            "top_ranked_scenario_state": top_row.get("state"),
            "active_scenario_id": active,
            "decision_scenario_id": decision,
            "plan_scenario_id": max(applicable, key=lambda row: weights[row["scenario_id"]])["scenario_id"] if applicable and phase == "close" else active,
            "eligible_strategy_ids": sorted(allowed) if allowed is not None else None,
            "scenario_status": scenario_status,
            "transition_from": transition_from,
            "active_transition_from": previous_active if active != previous_active else None,
            "transition_reason": (
                "全部情景已失效，不开新仓" if scenario_status == "no_valid_scenario" else
                "有效情景发生变化" if previous_active and active != previous_active else
                "阶段证据改变场景排序" if transition_from else "暂无场景迁移"
            ),
            "probabilities_available": bool(priors),
            "scenarios": [
                {
                    **row,
                    "posterior_probability": (weights[row["scenario_id"]] / total if priors and total else None),
                }
                for row in phase_rows
            ],
        })
        previous_top = top
        previous_active = active

    return {
        "schema_version": "scenario-posterior/v2",
        "report_date": report_date, "target_trade_date": trade_date, "prediction_id": prediction_id,
        "binding_status": binding_status, "rejected_snapshots": rejected,
        "confirmation_blocked": confirmation_blocked,
        "prior_available": bool(priors),
        "phases_observed": [row["phase"] for row in timeline],
        "missing_phases": [phase for phase in PHASE_ORDER if phase not in by_phase],
        "timeline": timeline,
    }


def scenario_selection(
    phase: dict[str, Any] | None, *, preferred_scenario_id: str | None = None,
) -> dict[str, Any]:
    """Choose valid evidence, never treating the highest rank as permission.

    Old records use a single top ID. A caller's existing plan still controls
    that legacy selection. Modern records explicitly distinguish active and
    confirmed IDs; an explicit null must not fall back to an obsolete rank.
    """
    phase = phase if isinstance(phase, dict) else {}
    rows = [row for row in phase.get("scenarios") or [] if isinstance(row, dict)]
    modern = any(key in phase for key in (
        "active_scenario_id", "decision_scenario_id", "scenario_status",
    ))
    ranked = phase.get("top_ranked_scenario_id", phase.get("top_scenario_id"))
    selected = (
        phase.get("decision_scenario_id") or phase.get("active_scenario_id")
        if modern else preferred_scenario_id or phase.get("top_scenario_id")
    )
    row = next((item for item in rows if item.get("scenario_id") == selected), {})
    no_valid = phase.get("scenario_status") == "no_valid_scenario" or bool(
        modern and rows and all(item.get("state") == "invalidated" for item in rows)
    )
    blocked = no_valid or row.get("state") == "invalidated"
    if phase.get("scenario_status") == "baseline_only":
        selected, row = None, {}
    elif blocked:
        selected = None
    return {
        "scenario_id": str(selected or ""), "row": row,
        "no_valid_scenario": no_valid, "blocked": blocked,
        "ranked_scenario_id": ranked,
    }


__all__ = ["build_scenario_posterior_timeline", "scenario_selection"]
