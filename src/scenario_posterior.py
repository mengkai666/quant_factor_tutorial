# -*- coding: utf-8 -*-
"""基于阶段快照更新明日推演场景后验。"""
from __future__ import annotations

import math
from typing import Any, Iterable

from market_snapshot import PHASE_ORDER, latest_phase_snapshots


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
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
        if probability is None:
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
    return _metric(metrics, *aliases.get(metric, (metric,)))


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
) -> tuple[float, list[str], list[str], list[str]] | None:
    trigger_map = plan.get("trigger_rules")
    if not isinstance(trigger_map, dict):
        return None
    baseline_metrics = (baseline or {}).get("metrics") if isinstance(baseline, dict) else {}
    baseline_metrics = baseline_metrics if isinstance(baseline_metrics, dict) else {}
    support: list[str] = []
    invalidating: list[str] = []
    missing: list[str] = []
    score = 0.0
    for raw_rule in trigger_map.get(phase, ()) or ():
        if not isinstance(raw_rule, dict):
            continue
        rule_id = str(raw_rule.get("rule_id") or f"{phase}:unnamed")
        matched, missing_field = _evaluate_rule(raw_rule, metrics, baseline_metrics)
        if matched is None:
            if missing_field:
                missing.append(missing_field)
            continue
        weight = _number(raw_rule.get("weight")) or 1.0
        if matched:
            support.append(rule_id)
            score += weight
        else:
            invalidating.append(rule_id)
            score -= weight
    for raw_rule in plan.get("invalidation_rules", ()) or ():
        if not isinstance(raw_rule, dict):
            continue
        rule_id = str(raw_rule.get("rule_id") or f"{phase}:invalidation")
        matched, missing_field = _evaluate_rule(raw_rule, metrics, baseline_metrics)
        if matched is None:
            if missing_field:
                missing.append(missing_field)
            continue
        if matched:
            weight = _number(raw_rule.get("weight")) or 1.0
            invalidating.append(rule_id)
            score -= weight
    return score, support, invalidating, sorted(set(missing))


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
) -> dict[str, Any]:
    """按 close → auction → 9:35 → 10:00 → afternoon 更新场景后验。

    若场景没有完整、可审计的先验概率，仍返回证据和相对分数，但不发布
    伪造的百分比。这样可以先接入盘中状态机，再等历史样本或规则校准后
    开启概率展示。
    """
    plan_rows = [_plan_dict(plan) for plan in plans]
    plan_rows = [row for row in plan_rows if row.get("scenario_id")]
    by_phase = latest_phase_snapshots(snapshots)
    baseline = by_phase.get("close")
    priors = _prior_probabilities(plan_rows)
    weights = dict(priors or {str(row["scenario_id"]): 1.0 for row in plan_rows})
    timeline: list[dict[str, Any]] = []
    previous_top: str | None = None

    for phase in PHASE_ORDER:
        record = by_phase.get(phase)
        if record is None:
            continue
        phase_rows: list[dict[str, Any]] = []
        for plan in plan_rows:
            scenario_id = str(plan["scenario_id"])
            metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
            rule_evidence = _evidence_from_rules(plan, metrics, baseline, phase)
            if rule_evidence is None:
                score, support, invalidating, missing = _evidence_for_plan(
                    scenario_id, metrics, baseline, phase,
                )
            else:
                score, support, invalidating, missing = rule_evidence
            if phase != "close" and score:
                weights[scenario_id] *= math.exp(0.55 * score)
            phase_rows.append({
                "scenario_id": scenario_id,
                "prior_probability": priors.get(scenario_id) if priors else None,
                "evidence_score": score,
                "supporting_evidence_ids": support,
                "invalidating_evidence_ids": invalidating,
                "missing_fields": missing,
                "state": "unknown" if missing and not (support or invalidating) else (
                    "supported" if score > 0 else ("invalidated" if score < 0 else "neutral")
                ),
            })
        total = sum(weights.values())
        top = max(weights, key=weights.get) if weights else None
        transition_from = previous_top if previous_top and top != previous_top else None
        timeline.append({
            "phase": phase,
            "snapshot_id": record.get("snapshot_id"),
            "captured_at": record.get("captured_at"),
            "top_scenario_id": top,
            "transition_from": transition_from,
            "transition_reason": (
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

    return {
        "schema_version": "scenario-posterior/v1",
        "prior_available": bool(priors),
        "phases_observed": [row["phase"] for row in timeline],
        "missing_phases": [phase for phase in PHASE_ORDER if phase not in by_phase],
        "timeline": timeline,
    }


__all__ = ["build_scenario_posterior_timeline"]
