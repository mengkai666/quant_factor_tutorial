# -*- coding: utf-8 -*-
"""分模块数据质量与全局发布门禁。"""
from __future__ import annotations

from typing import Any, Iterable

from .models import ModuleQuality

CRITICAL_MODULES = {"universe", "price_raw", "breadth", "limit_pool"}
DECISION_MODULES = CRITICAL_MODULES | {"echelon", "sector", "history"}


def _dedupe(values: Iterable[Any] | None) -> list[str]:
    result: list[str] = []
    for value in values or ():
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def build_module_quality(
    name: str,
    *,
    total: Any = 0,
    covered: Any = 0,
    source: str = "",
    source_timestamp: str = "",
    missing_fields: Iterable[Any] | None = None,
    errors: Iterable[Any] | None = None,
    lineage: dict[str, Any] | None = None,
    critical: bool | None = None,
    ok_threshold: float = 0.98,
    usable_threshold: float = 0.80,
) -> dict[str, Any]:
    try:
        total_i = max(0, int(float(total or 0)))
        covered_i = max(0, int(float(covered or 0)))
    except (TypeError, ValueError):
        total_i, covered_i = 0, 0
    effective_covered = min(covered_i, total_i)
    coverage = effective_covered / total_i if total_i else (1.0 if effective_covered else 0.0)
    raw_coverage = covered_i / total_i if total_i else (1.0 if covered_i else 0.0)
    missing = _dedupe(missing_fields)
    error_list = _dedupe(errors)
    overflow = covered_i > total_i
    if overflow:
        error_list.append(
            f"COVERAGE_OVERFLOW: 有效覆盖数 {covered_i} 超过市场总数 {total_i}"
        )
    error_list = _dedupe(error_list)
    is_critical = name in CRITICAL_MODULES if critical is None else bool(critical)
    if overflow:
        status = "blocked" if is_critical else "unavailable"
    elif error_list and (not covered_i or coverage < usable_threshold):
        status = "blocked" if is_critical else "unavailable"
    elif total_i and coverage < usable_threshold:
        status = "blocked" if is_critical else "unavailable"
    elif error_list or missing or (total_i and coverage < ok_threshold):
        status = "degraded"
    elif covered_i or not total_i:
        status = "ok"
    else:
        status = "unknown"
    item = ModuleQuality(
        name=name, status=status, total=total_i, covered=effective_covered,
        coverage_pct=round(coverage * 100, 2), source=str(source or ""),
        source_timestamp=str(source_timestamp or ""), missing_fields=missing,
        errors=error_list, lineage=dict(lineage or {}), critical=is_critical,
        raw_covered=covered_i, raw_coverage_pct=round(raw_coverage * 100, 2),
    )
    return item.to_dict()


def aggregate_report_quality(modules: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    normalized = {str(k): dict(v or {}) for k, v in (modules or {}).items()}
    critical_blocked = [name for name, item in normalized.items() if (item.get("critical") or name in CRITICAL_MODULES) and item.get("status") in {"blocked", "unavailable", "unknown"}]
    decision_degraded = [name for name, item in normalized.items() if name in DECISION_MODULES and item.get("status") != "ok"]
    errors = _dedupe(msg for item in normalized.values() for msg in item.get("errors", []))
    missing = _dedupe(msg for item in normalized.values() for msg in item.get("missing_fields", []))
    if critical_blocked:
        status, mode = "blocked", "facts_only"
    elif decision_degraded:
        status, mode = "degraded", "observation"
    else:
        status, mode = "ok", "decision"
    return {
        "status": status,
        "publication_mode": mode,
        "modules": normalized,
        "critical_blocked": critical_blocked,
        "decision_degraded": decision_degraded,
        "errors": errors,
        "missing_fields": missing,
        "allow_strong_conclusion": mode == "decision",
    }
