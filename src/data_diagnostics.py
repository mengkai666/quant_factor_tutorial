"""Presentation-only root causes from existing assessments, never authorization.

No files, clocks, services or raw samples are consulted. Details are flat,
scalar-only public records; original machine issues stay in the caller-owned
quality payload. An absent assessment is not proof of a missing physical file,
and an event-field gap is not proof of absent raw data.
"""
from __future__ import annotations

import math
from typing import Any

from strategy_qualification import (
    MIN_VALIDATION_SAMPLES, CORE_MODULES, STRUCTURAL_MODULES, public_validation_summary,
)

_EVENT_LABELS = {"bomb_rate": "炸板率", "reclose_rate": "炸板后回封率", "board_structure": "板型"}
_INTRADAY = ("auction", "early_0935", "confirm_1000", "afternoon")
_USABLE_EVENTS = {"ready", "not_applicable"}
_VALIDATION_FIELDS = (
    "schema_version", "strategy_id", "rule_version", "rule_fingerprint", "outcome_definition_id",
    "status", "source", "evidence_ref", "sample_size", "declared_sample_size",
    "evaluated_at", "valid_from", "valid_until",
)


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, (list, tuple)) else []


def _scalar(value: Any):
    if type(value) in (str, int, bool) or value is None:
        return value
    return value if type(value) is float and math.isfinite(value) else None


def _validation_summary(value: Any) -> dict:
    # The shared allowlist parses dates/counts. Remove containers BEFORE that
    # parsing, so even malformed cached values are never str()/repr()-formatted.
    raw = _dict(value)
    safe = {key: _scalar(raw.get(key)) for key in _VALIDATION_FIELDS}
    safe["issues"] = raw.get("issues")  # the shared helper rejects non-string issues
    return public_validation_summary(safe)


def _validation_usable(summary: dict) -> bool:
    """The existing panel's public-summary check, not a new qualification gate."""
    count = summary.get("sample_size")
    return bool(summary.get("status") == "validated" and not summary.get("issues")
                and count is not None and count >= MIN_VALIDATION_SAMPLES
                and count == summary.get("declared_sample_size")
                and all(summary.get(key) for key in ("source", "evidence_ref", "evaluated_at", "valid_from", "valid_until")))


def _detail(**fields) -> dict:
    return {key: _scalar(value) for key, value in fields.items()}


def _issue_details(issues: Any, **context) -> list[dict]:
    return [_detail(**context, issue=issue) for issue in _strings(issues)]


def _validation_details(sid: str, row: dict, summary: dict) -> list[dict]:
    details = [_detail(strategy_id=sid, field=key, value=summary.get(key)) for key in _VALIDATION_FIELDS]
    details.extend(_issue_details(summary.get("issues"), strategy_id=sid))
    details.extend(_issue_details([issue for issue in _strings(row.get("issues"))
                                  if issue.startswith("validation_") or issue in {
                                      "invalid_validation_dates", "invalid_report_or_target_date", "strategy_not_registered",
                                  }], strategy_id=sid))
    return details


def _diagnostic(code: str, status: str, title: str, impact: str, recovery: str,
                affected: list[str], details: list[dict]) -> dict:
    # Scalar records can be deduplicated without formatting private objects.
    unique = list({tuple(item.items()): item for item in details}.values())
    return {"code": code, "status": status, "title": title, "impact": impact, "recovery": recovery,
            "affected_strategies": list(dict.fromkeys(affected)), "details": unique}


def _event_diagnostic(q: dict, scoped: dict, rows: list[tuple[str, dict]]) -> dict | None:
    review = _dict(_dict(q.get("review_readiness")).get("bomb_metrics"))
    assessment = (_dict(scoped.get("event_qualification")) or _dict(q.get("event_qualification")) or review)
    metrics, population = _dict(assessment.get("metrics")), _dict(assessment.get("population"))
    affected, details = [], []
    names, field_gaps = set(), set()
    population_gap = False
    closing_population = population.get("scope") == "closing_limit_pool"
    provenance_gap = False

    def add_metric(name: str, metric: dict, sid: str = ""):
        names.add(name)
        details.append(_detail(metric=name, status=_string(metric.get("status")) or "unknown",
                               strategy_id=sid, reason=_string(metric.get("reason")),
                               observed=metric.get("observed"), trials=metric.get("trials")))

    # Supplied metrics outrank an old module badge. Derived canonical flags can
    # make fields ready without establishing the full-market population.
    for name in _EVENT_LABELS:
        if name in metrics and _string(_dict(metrics[name]).get("status")) not in _USABLE_EVENTS:
            field_gaps.add(name)
            add_metric(name, _dict(metrics[name]))
    if population and (population.get("scope") != "full_market_limit_up_attempts" or population.get("complete") is not True):
        for name in ("bomb_rate", "reclose_rate"):
            if name in metrics:
                population_gap = True
                add_metric(name, _dict(metrics[name]))

    for sid, row in rows:
        local = _dict(row.get("event_qualification")) or assessment
        local_metrics = _dict(local.get("metrics"))
        local_population = _dict(local.get("population"))
        dependencies = _dict(row.get("event_dependencies"))
        issues = _strings(row.get("issues"))
        row_affected = False
        for name in _EVENT_LABELS:
            explicit = [issue for issue in issues if issue.startswith(name + ":") and issue != name + ":no_events"]
            if name not in dependencies and not explicit:
                continue
            metric = _dict(local_metrics.get(name))
            missing = name in local_metrics and _string(metric.get("status")) not in _USABLE_EVENTS
            bad_population = bool(local_population) and (
                (name in {"bomb_rate", "reclose_rate"} and (
                    local_population.get("scope") != "full_market_limit_up_attempts" or local_population.get("complete") is not True))
                or (name == "board_structure" and _string(local_population.get("scope")) not in {
                    "closing_limit_pool", "full_market_limit_up_attempts", "strategy_candidate_pool"}))
            if not (missing or bad_population or explicit):
                continue
            row_affected = True
            if bad_population:
                closing_population = closing_population or local_population.get("scope") == "closing_limit_pool"
            add_metric(name, metric, sid)
            details.extend(_issue_details(explicit, strategy_id=sid, metric=name))
            if missing or (not metric and explicit):
                field_gaps.add(name)
            population_gap = population_gap or bad_population or any(issue.endswith(":population_unverified") for issue in explicit)
            provenance_gap = provenance_gap or any(issue.endswith(":provenance_unverified") for issue in explicit)
        if row_affected:
            affected.append(sid)

    module = _dict(_dict(q.get("modules")).get("bomb_metrics"))
    # Fall back to module failure only when no metric evidence was supplied.
    module_gap = not metrics and bool(module) and _string(module.get("status")) not in {"ok", "ready"}
    if not (names or module_gap):
        return None
    details.append(_detail(module="bomb_metrics", status=_string(module.get("status")) or "unknown"))
    details.extend(_issue_details(module.get("errors"), module="bomb_metrics",
                                  basis="汇总模块原记录；不覆盖当前 scoped 指标评估"))
    details.extend(_detail(field="population." + key, value=population.get(key))
                   for key in ("scope", "complete", "report_date", "source", "source_timestamp", "used_stale") if key in population)
    impact = "涉及" + ("、".join(_EVENT_LABELS[name] for name in _EVENT_LABELS if name in names) or "事件指标汇总") + "。"
    if field_gaps:
        impact += "部分规范事件观察尚不可用或相互矛盾。"
    if population_gap:
        impact += "全市场事件总体尚未核验；样本内可计算不等于覆盖全部封板尝试。"
    if provenance_gap:
        impact += "事件来源或报告日期尚未核验。"
    impact += "仅影响相关事件分析与所列策略的依赖核验，不等同核心行情缺失。"
    recovery = ("先核验并解析已有真实字段与规范事件标记，只补采仍未知的观察；核对来源、日期及事件计数。"
                if field_gaps or module_gap else "保留已就绪的事件指标，核对其来源与当前资格记录。")
    if population_gap:
        recovery += ("全市场指标须补齐未回封炸板成员及全部封板尝试总体覆盖，收盘涨停子集不能替代。"
                     if closing_population else "核验并补齐全部封板尝试总体覆盖；不能把局部样本当作全市场。")
    recovery += "重新评估相关策略；不补造事件，也不以字段就绪代替独立验证。"
    only_population = population_gap and not field_gaps and not provenance_gap and not module_gap
    if only_population:
        impact += ("本项缺口是未回封炸板成员／总体覆盖，不是原始炸板次数缺失。" if closing_population else
                   "本项缺口是全市场总体覆盖，不能据此推断某类原始字段或成员缺失。")
    title = "全市场事件总体覆盖不足" if only_population else "事件输入与总体口径待核验"
    return _diagnostic("event_feed", "missing_dependency" if affected else "degraded", title,
                       impact, recovery, affected, details)


def build_data_diagnostics(quality: dict, *, phase_confirmation=None) -> list[dict]:
    """Group known root causes without mutating inputs or changing permissions.

    Missing optional context does not itself assert a failure. Per-strategy
    event assessments take precedence over a global metric for candidate data.
    Research scope, no applicable events, and absent intraday observations are
    different explanations, not additional missing core market feeds.
    """
    q = _dict(quality)
    scoped = _dict(q.get("strategy_qualification"))
    if scoped.get("schema_version") != "strategy-qualification-set/v1":
        scoped = {}
    rows = []
    for key, value in _dict(scoped.get("strategies")).items():
        if not isinstance(value, dict):
            continue
        sid = _string(value.get("strategy_id")) or _string(key)
        if sid:
            rows.append((sid, value))
    result = []
    core = _strings(scoped.get("core_issues"))
    core.extend(issue for _, row in rows for issue in _strings(row.get("issues"))
                if issue.partition(":")[0] in CORE_MODULES or issue.startswith("core_"))
    core_blocked = bool(core) or scoped.get("core_ready") is False or any(row.get("status") == "blocked_core" for _, row in rows)
    if core_blocked:
        details = _issue_details(core)
        for name, module in _dict(q.get("modules")).items():
            module = _dict(module)
            if _string(name) and (name in CORE_MODULES or module.get("critical") is True):
                details.extend(_issue_details(module.get("errors"), module=name))
        result.append(_diagnostic("core_market", "blocked_core", "既有核心行情检查阻断",
                                 "核心行情的日期、批次或可用性未通过既有检查；全部策略仍受原阻断约束。",
                                 "修复真实核心行情及日期、批次证据后重跑原检查；独立验证或其他策略资格不能覆盖核心阻断。",
                                 [sid for sid, _ in rows], details))

    unverified, validation_details, research, inapplicable, structural, structural_details = [], [], [], [], [], []
    permission_holds, permission_details = [], []
    default_events = (_dict(scoped.get("event_qualification")) or _dict(q.get("event_qualification"))
                      or _dict(_dict(q.get("review_readiness")).get("bomb_metrics")))
    for sid, row in rows:
        status = _string(row.get("status"))
        if status == "research_only":
            research.append(sid)
            continue
        metrics = _dict((_dict(row.get("event_qualification")) or default_events).get("metrics"))
        no_events = any(issue.endswith(":no_events") for issue in _strings(row.get("issues"))) or any(
            name in _EVENT_LABELS and allow is False and _dict(metrics.get(name)).get("status") == "not_applicable"
            for name, allow in _dict(row.get("event_dependencies")).items())
        if status == "not_applicable" or no_events:
            inapplicable.append(sid)
        if status == "not_applicable":
            continue
        summary = _validation_summary(row.get("validation"))
        if not _validation_usable(summary):
            unverified.append(sid)
            validation_details.extend(_validation_details(sid, row, summary))
        if "not_authorized_in_original_plan" in _strings(row.get("issues")):
            permission_holds.append(sid)
            permission_details.append(_detail(strategy_id=sid, issue="not_authorized_in_original_plan"))
        missing = [issue for issue in _strings(row.get("issues")) if issue.partition(":")[0] in STRUCTURAL_MODULES]
        if missing:
            structural.append(sid)
            structural_details.extend(_issue_details(missing, strategy_id=sid))
    if unverified:
        result.append(_diagnostic("independent_validation", "unverified", "尚无可用的独立验证记录",
                                 "所列策略尚不能据此取得条件计划许可；这不是核心行情缺失，元信息不足不说明物理文件是否存在。",
                                 "提供或修复与当前策略、规则指纹及结果口径匹配的独立样本外验证记录，核验有效日期和成熟样本；历史天数或同型命中率不能代替，其他依赖仍须分别通过。",
                                 unverified, validation_details))
    if permission_holds:
        result.append(_diagnostic("original_permission", "unverified", "原计划权限上限仍然生效",
                                 "所列策略未获原计划许可；后续数据或验证恢复不等于历史计划自动获准。",
                                 "沿用原计划权限上限，由既有资格流程复核；不自动升级历史计划权限。",
                                 permission_holds, permission_details))
    event = _event_diagnostic(q, scoped, rows)
    if event:
        result.append(event)
    if structural:
        result.append(_diagnostic("strategy_data", "missing_dependency", "策略所需结构数据待核验",
                                 "所列策略的结构数据或其日期、批次证据未就绪；不扩大为所有策略的核心行情阻断。",
                                 "修复明细中实际涉及的数据及来源、日期和批次后，重跑原策略资格检查；不能只补独立验证。",
                                 structural, structural_details))
    for code, affected, title, impact, recovery in (
        ("research_only", research, "研究／防守分支仅供观察",
         "这些分支本来就不生成新增交易候选，不是缺少核心行情。",
         "继续用于研究或防守观察；不能仅靠补充独立验证启用交易，也不新增或放宽策略。"),
        ("not_applicable", inapplicable, "策略／事件本次不适用",
         "本次策略或事件条件不适用，不代表核心行情缺失；明确无事件时保持不适用，不伪装成零回封率。",
         "等待真实事件或既有适用条件满足后重跑原检查；不能仅靠补充验证制造适用事件或启用交易。"),
    ):
        if affected:
            details = [_detail(strategy_id=sid, status=_string(row.get("status"))) for sid, row in rows if sid in affected]
            details.extend(detail for sid, row in rows if sid in affected
                           for detail in _issue_details(row.get("issues"), strategy_id=sid))
            result.append(_diagnostic(code, code, title, impact, recovery, affected, details))

    ai = _dict(_dict(q.get("modules")).get("ai"))
    ai_review = _dict(_dict(q.get("review_readiness")).get("ai"))
    ai_failed = (bool(ai) and _string(ai.get("status")) not in {"ok", "ready", "sanitized"}) or (not ai and ai_review.get("ready") is False)
    ai_core = ai.get("critical") is True or any(issue.startswith("ai:") for issue in core)
    if ai_failed and not (core_blocked and ai_core):
        optional = not ai_core and ("ai" in _strings(scoped.get("nonblocking_modules")) or ai.get("critical") is False)
        details = [_detail(module="ai", status=_string(ai.get("status")) or _string(ai_review.get("status")) or "unknown")]
        details.extend(_issue_details(ai.get("errors"), module="ai"))
        if _string(ai_review.get("reason")):
            details.append(_detail(module="ai", reason=ai_review["reason"]))
        impact = ("仅缺少服务／文案增强，不是行情缺失；不影响已独立验证策略的资格。" if optional else
                  "AI 服务／文案未就绪；当前输入未将其分类为可选非阻断项，沿用既有资格判定。")
        result.append(_diagnostic("ai_service", "nonblocking" if optional else "degraded", "AI文案服务不可用",
                                 impact, "保留确定性说明；检查或重试既有 AI 服务后恢复文案，不改策略权限。", [], details))

    confirmation = (_dict(phase_confirmation) if phase_confirmation is not None else
                    _dict(q.get("phase_confirmation")) or _dict(_dict(q.get("decision_readiness")).get("phase_confirmation")))
    observed = [phase for phase in _strings(confirmation.get("observed_phases")) if phase in ("close", *_INTRADAY)]
    if confirmation and not any(phase in _INTRADAY for phase in observed) and (
            "observed_phases" in confirmation or confirmation.get("status") == "post_close_plan"):
        details = [_detail(field="observed_phase", value=phase) for phase in observed]
        details.extend(_detail(field="pending_phase", value=phase) for phase in _strings(confirmation.get("pending_phases")) if phase in _INTRADAY)
        details.append(_detail(field="target_trade_date", value=_string(confirmation.get("target_trade_date")) or None))
        rejected = confirmation.get("validation_issues")
        for value in rejected if isinstance(rejected, list) else []:
            row = _dict(value)
            details.extend(_issue_details(row.get("issues"), phase=_string(row.get("phase")), snapshot_id=_string(row.get("snapshot_id"))))
        result.append(_diagnostic("intraday_observations", "pending", "尚无盘中观测",
                                 "当前仅能作为盘后条件计划；收盘基准不等于盘中确认，不能断言后续阶段已经过时或完成。",
                                 "接入目标交易日真实盘中快照及来源、采集时间后重跑阶段核验；不补造时间戳、不把抓取时间冒充事件时间。",
                                 [sid for sid, row in rows if _string(row.get("status")) not in {"research_only", "not_applicable"}], details))
    return result
