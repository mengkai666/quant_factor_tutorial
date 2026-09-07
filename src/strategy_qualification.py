"""Explicit, strategy-scoped validation contracts; counts alone never authorize.

Market-thesis hit rates describe historical scenarios. An independently assessed
rule strategy has a different outcome definition and a version-bound evidence
package. This module verifies that package; it does not invent a backtest or
approve a strategy based on sample count or positive returns alone.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
from typing import Any

RULE_VERSION = "scenario-rules/v1"
STRATEGY_OUTCOME = "trade-plan-net-return/v1"
MIN_VALIDATION_SAMPLES = 10
CORE_MODULES = ("universe", "price_raw", "breadth", "limit_pool")
STRUCTURAL_MODULES = ("echelon", "sector", "daily_delta", "price_qfq")
# Dependencies follow the existing strategy semantics, not whichever feed is
# easiest to make green. Research/defensive scenarios never become new buys.
STRATEGIES = {
    "mainline_continuation": {"events": {"bomb_rate": False, "reclose_rate": True, "board_structure": False}, "trade": True},
    "intraday_divergence_repair": {"events": {"bomb_rate": False, "reclose_rate": False, "board_structure": False}, "trade": True},
    "repair_after_breadth_only": {"events": {"bomb_rate": False, "reclose_rate": False}, "trade": True},
    "selective_mainline_hold": {"events": {"board_structure": False}, "trade": True},
    "low_level_diffusion": {"events": {"reclose_rate": False}, "trade": False},
    "breadth_repair": {"events": {}, "trade": False},
    "high_level_retreat": {"events": {}, "trade": False},
    "risk_off_observation": {"events": {}, "trade": False},
    "repair_confirmation": {"events": {"bomb_rate": False, "reclose_rate": False}, "trade": False},
}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _known(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() not in {"", "unknown", "none", "null", "n/a", "未知", "未提供"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo and parsed.utcoffset() is not None else None
    except (ValueError, TypeError):
        return None


def rule_fingerprint(plan: dict) -> str:
    """Bind machine thresholds, position rules and execution/dependency version.

    Daily stock names, prose and candidate ordering are not a new rule version.
    A changed calibrated threshold is, and must be independently revalidated.
    """
    sid = str(plan.get("scenario_id") or "")
    payload = {key: plan.get(key) for key in (
        "scenario_id", "trigger_rules", "invalidation_rules", "position_adjustment_rules", "position_floor", "position_ceiling",
    )}
    payload.update(rule_version=RULE_VERSION, dependency_contract=STRATEGIES.get(sid),
                   execution_rule_version="candidate-entry/v1", outcome_definition_id=STRATEGY_OUTCOME)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def descriptive_statistics(timing: dict | None) -> dict:
    timing = _dict(timing)
    count = _number(timing.get("win_rate_sample_size"))
    rate = _number(timing.get("win_rate"))
    return {"schema_version": "descriptive-statistics/v1", "strategy_id": timing.get("scene"),
            "sample_size": int(count) if count is not None and count >= 0 and count.is_integer() else 0,
            "historical_rate": rate if rate is not None and 0 <= rate <= 1 else None,
            "qualification": "descriptive_only", "source_field": "win_rate_sample_size"}


def assess_validation(plan: dict, record: dict | None, *, report_date: str,
                      target_trade_date: str | None = None) -> dict:
    r = _dict(record)
    sid = str(plan.get("scenario_id") or "")
    fingerprint = rule_fingerprint(plan)
    report, target = _day(report_date), _day(target_trade_date or report_date)
    issues = []
    expected = {"schema_version": "strategy-validation/v1", "strategy_id": sid,
                "rule_version": RULE_VERSION, "rule_fingerprint": fingerprint,
                "outcome_definition_id": STRATEGY_OUTCOME, "status": "passed", "method": "out_of_sample"}
    for key, value in expected.items():
        if r.get(key) != value:
            issues.append(f"validation_{key}_mismatch")
    if sid not in STRATEGIES:
        issues.append("strategy_not_registered")
    for key in ("source", "evidence_ref"):
        if not _known(r.get(key)):
            issues.append(f"validation_{key}_missing")
    trained, start, end = (_day(r.get(key)) for key in ("trained_through", "valid_from", "valid_until"))
    evaluated = _timestamp(r.get("evaluated_at"))
    assessed = evaluated.astimezone(timezone(timedelta(hours=8))).date() if evaluated else None
    if not report or not target or target < report:
        issues.append("invalid_report_or_target_date")
    if not trained or not assessed or not start or not end or start > end:
        issues.append("invalid_validation_dates")
    elif not report or not target or assessed > report or start < assessed or not start <= report <= target <= end:
        issues.append("validation_not_effective")
    samples = r.get("samples") if isinstance(r.get("samples"), list) else []
    count = _number(r.get("sample_size"))
    if count is None or not count.is_integer() or count != len(samples) or count < MIN_VALIDATION_SAMPLES:
        issues.append("validation_sample_count_invalid")
    seen = set()
    valid_samples = 0
    for sample in samples:
        sample = _dict(sample)
        sample_id = sample.get("sample_id")
        bad = not _known(sample_id) or sample_id in seen
        if _known(sample_id):
            seen.add(sample_id)
        bad = bad or any(sample.get(key) != value for key, value in {
            "strategy_id": sid, "rule_fingerprint": fingerprint, "outcome_definition_id": STRATEGY_OUTCOME,
        }.items())
        observed, outcome = _day(sample.get("trade_date")), _day(sample.get("outcome_date"))
        bad = bad or not (trained and observed and outcome and assessed and trained < observed <= outcome <= assessed)
        if "outcome_matured_at" in sample:
            matured = _timestamp(sample.get("outcome_matured_at"))
            bad = bad or not (
                matured and evaluated and outcome
                and matured.astimezone(timezone(timedelta(hours=8))).date() == outcome
                and matured <= evaluated
            )
        else:
            # A date alone cannot prove that an outcome was known earlier that
            # same day. Do not invent a closing/settlement time for the evidence.
            bad = bad or not (outcome and assessed and outcome < assessed)
        bad = bad or _number(sample.get("net_return")) is None or sample.get("costs_included") is not True or not _known(sample.get("source"))
        if bad:
            issues.append("validation_sample_invalid")
        else:
            valid_samples += 1
    issues = list(dict.fromkeys(issues))
    return {"schema_version": "strategy-validation-assessment/v1", "strategy_id": sid,
            "rule_version": RULE_VERSION, "rule_fingerprint": fingerprint, "outcome_definition_id": STRATEGY_OUTCOME,
            "status": "validated" if not issues else "unverified", "issues": issues,
            "sample_size": valid_samples, "declared_sample_size": count,
            "source": r.get("source") if _known(r.get("source")) else None,
            "evidence_ref": r.get("evidence_ref") if _known(r.get("evidence_ref")) else None,
            "evaluated_at": evaluated.isoformat() if evaluated else None,
            "valid_from": start.isoformat() if start else None,
            "valid_until": end.isoformat() if end else None}


def load_validation_records(path: str | Path) -> dict:
    """Read an explicitly supplied evidence set, never create/approve one."""
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or payload.get("schema_version") != "strategy-validation-set/v1":
            raise ValueError("validation set schema must be strategy-validation-set/v1")
        rows = payload.get("records")
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError("validation records must be a list of objects")
        return {"status": "loaded", "source_path": str(target), "records": rows, "issues": []}
    except FileNotFoundError:
        return {"status": "missing", "source_path": str(target), "records": [], "issues": ["validation_file_missing"]}
    except (OSError, ValueError) as exc:
        return {"status": "invalid", "source_path": str(target), "records": [], "issues": [str(exc)]}


def _module_provenance_issues(name: str, row: dict, *, expected_run: str, report_date: str | None) -> list[str]:
    """Validate asserted input applicability independently of collection time.

    Canonical report_date/data_as_of fields are mandatory for scoped plans.
    Date-only source_timestamp values on the legacy daily-data modules also
    assert an as-of date, but never substitute for the canonical contract.
    Security-master collection timestamps deliberately are not report dates.
    """
    lineage = _dict(row.get("lineage"))
    issues = []
    if row.get("status") != "ok":
        issues.append(name + ":not_ready")
    for metadata in (row, lineage):
        if metadata.get("used_stale") or str(metadata.get("freshness_level") or "").lower() in {"stale", "expired", "blocked"}:
            issues.append(name + ":expired")
    # An explicitly missing primary batch must not borrow a lineage value.
    rid = row.get("run_id") if "run_id" in row else lineage.get("run_id")
    if not _known(rid) or not _known(expected_run):
        issues.append(name + ":batch_missing")
    elif rid.strip() != expected_run.strip():
        issues.append(name + ":batch_mismatch")
    nested_run = lineage.get("run_id")
    if "run_id" in lineage:
        if not _known(nested_run):
            issues.append(name + ":batch_missing")
        elif not _known(rid) or nested_run.strip() != rid.strip():
            issues.append(name + ":batch_mismatch")
    if report_date is not None:
        target = _day(report_date)
        dates = [metadata[key] for metadata in (row, lineage) for key in ("report_date", "data_as_of")
                 if key in metadata and metadata[key] not in (None, "")]
        if not dates:
            issues.append(name + ":report_date_missing")
        elif target is None or any(_day(value) != target for value in dates):
            issues.append(name + ":report_date_mismatch")
        # These legacy producers explicitly used an ISO date (not a capture
        # timestamp) to identify their report-day input. Reject contradictions.
        legacy_as_of = _day(row.get("source_timestamp"))
        if name in {"price_raw", "price_qfq", "breadth", "limit_pool", "echelon", "sector", "daily_delta"} and legacy_as_of and legacy_as_of != target:
            issues.append(name + ":report_date_mismatch")
    return list(dict.fromkeys(issues))


def core_issues(quality: dict | None, *, report_date: str | None = None) -> list[str]:
    q = _dict(quality)
    modules = _dict(q.get("modules"))
    issues = []
    if q.get("status") in {"blocked", "non_trading_day"} or q.get("publication_mode") == "facts_only":
        issues.append("core_market_blocked")
    for metadata in (q, _dict(q.get("lineage"))):
        if metadata.get("used_stale") or str(metadata.get("freshness_level") or "").lower() in {"stale", "expired", "blocked"}:
            issues.append("core_market_expired")
    issues.extend(str(name) + ":critical_blocked" for name in q.get("critical_blocked") or [])
    required = set(CORE_MODULES) | {name for name, row in modules.items() if _dict(row).get("critical")}
    expected = q.get("run_id")
    if not _known(expected):
        issues.append("core_batch_missing")
    run_ids = set()
    for name in sorted(required):
        row = _dict(modules.get(name))
        issues.extend(_module_provenance_issues(name, row, expected_run=expected, report_date=report_date))
        rid = row.get("run_id", _dict(row.get("lineage")).get("run_id"))
        if _known(rid):
            run_ids.add(rid.strip())
    if len(run_ids) > 1:
        issues.append("core_batch_mismatch")
    return list(dict.fromkeys(issues))


def _dependency_issues(spec: dict, quality: dict, event_assessment: dict, *, report_date: str) -> tuple[list[str], list[str]]:
    modules = _dict(quality.get("modules"))
    event_rows = _dict(event_assessment.get("metrics"))
    population = _dict(event_assessment.get("population"))
    missing, inapplicable = [], []
    for module in STRUCTURAL_MODULES:
        missing.extend(_module_provenance_issues(module, _dict(modules.get(module)),
            expected_run=quality.get("run_id"), report_date=report_date))
    for metric, allow_no_events in spec.get("events", {}).items():
        state = _dict(event_rows.get(metric)).get("status")
        if state == "not_applicable":
            if not allow_no_events:
                inapplicable.append(metric + ":no_events")
        elif state != "ready":
            missing.append(metric + ":" + str(state or "missing"))
        if population.get("report_date") != report_date or not _known(population.get("source")) or population.get("used_stale"):
            missing.append(metric + ":provenance_unverified")
        if metric in {"bomb_rate", "reclose_rate"}:
            if population.get("scope") != "full_market_limit_up_attempts" or population.get("complete") is not True:
                missing.append(metric + ":population_unverified")
        elif population.get("scope") not in {"closing_limit_pool", "full_market_limit_up_attempts", "strategy_candidate_pool"}:
            missing.append(metric + ":population_unverified")
    return list(dict.fromkeys(missing)), list(dict.fromkeys(inapplicable))


def _strategy_assessment(plan: dict, quality: dict, validation: dict, event_assessment: dict, *, report_date: str) -> dict:
    sid = str(plan.get("scenario_id") or "")
    spec = STRATEGIES.get(sid)
    core = core_issues(quality, report_date=report_date)
    missing, inapplicable = _dependency_issues(spec or {}, quality, event_assessment, report_date=report_date)
    if core:
        status = "blocked_core"
    elif spec is None:
        status = "unverified"
        missing.append("strategy_not_registered")
    elif not spec["trade"]:
        status = "research_only"
    elif validation.get("status") != "validated":
        status = "unverified"
    elif missing:
        status = "missing_dependency"
    elif inapplicable:
        status = "not_applicable"
    else:
        status = "eligible"
    issues = list(dict.fromkeys(core + list(validation.get("issues") or []) + missing + inapplicable))
    recovery = []
    if core:
        recovery.append("修复报告日核心行情、日期与批次；不以其他策略资格覆盖。")
    if validation.get("status") != "validated":
        recovery.append("提供该策略、当前规则指纹和结果口径的独立样本外验证报告；历史天数/同型命中率不能代替。")
    if missing:
        recovery.append("补齐该策略必要数据：" + "、".join(missing) + "；收盘子集不能替代全市场尝试样本。")
    if inapplicable:
        recovery.append("当前没有该策略所需的事件；等待真实事件出现后再核验，不强行制造回封率。")
    if status == "research_only":
        recovery.append("此分支仅用于观察或防守，不生成新的交易候选。")
    return {"strategy_id": sid, "title": plan.get("title") or sid, "status": status,
        "plan_permitted": status == "eligible", "required_modules": list(CORE_MODULES + STRUCTURAL_MODULES),
        "event_dependencies": deepcopy((spec or {}).get("events", {})), "validation": validation,
        "issues": issues, "recheck_conditions": recovery,
        "rule_fingerprint": validation.get("rule_fingerprint"), "rule_version": RULE_VERSION}


def _qualification_set(strategies: dict, quality: dict, event_assessment: dict, *, report_date: str,
                       target_trade_date: str | None) -> dict:
    core = core_issues(quality, report_date=report_date)
    modules = _dict(quality.get("modules"))
    eligible = [sid for sid, row in strategies.items() if row["plan_permitted"]]
    return {"schema_version": "strategy-qualification-set/v1", "report_date": report_date,
        "target_trade_date": target_trade_date, "core_ready": not core, "core_issues": core,
        "strategies": strategies, "eligible_strategy_ids": eligible,
        "publication_mode": "facts_only" if core else "decision" if eligible else "observation",
        "event_qualification": event_assessment,
        "nonblocking_modules": [name for name in ("ai", "history") if _dict(modules.get(name)).get("status") != "ok"]}


def build_strategy_event_input(snapshot: dict | None, *, report_date: str) -> dict:
    """Keep closing-pool observations separate from market-wide attempts.

    This does not update market-thesis scores or weaken the existing core data
    gates. Individual board dependencies can use same-day candidate records;
    strategies requiring the entire attempt population still remain blocked.
    """
    from report_logic import compute_ladder_metrics
    snap = _dict(snapshot)
    rows = [deepcopy(row) for row in snap.get("records") or [] if isinstance(row, dict)]
    current = [row for row in rows if row.get("trade_date") == report_date]
    metrics = compute_ladder_metrics(current)
    provenance = _dict(snap.get("provenance"))
    sources = sorted({str(row["source"]) for row in current if _known(row.get("source"))})
    times = sorted({str(row["source_timestamp"]) for row in current if _known(row.get("source_timestamp"))})
    metrics["event_population"] = {
        "scope": "closing_limit_pool", "complete": False, "report_date": snap.get("trade_date"),
        "source": "|".join(sources) or provenance.get("source"),
        "source_timestamp": times[-1] if times else provenance.get("source_timestamp"),
    }
    metrics["candidate_event_rows"] = current
    return metrics


def _event_assessment_for_plan(plan: dict, metrics: dict | None, default: dict) -> dict:
    from event_qualification import assess_event_metrics
    from report_logic import compute_ladder_metrics, normalize_stock_code
    source = _dict(metrics)
    if "candidate_event_rows" not in source:
        return default
    candidates = plan.get("trade_candidates") or []
    codes = {normalize_stock_code(row.get("code", row.get("代码"))) for row in candidates if isinstance(row, dict)}
    codes.discard("")
    if not codes:
        return default
    population = _dict(source.get("event_population"))
    by_code = {normalize_stock_code(row.get("code")): row for row in source.get("candidate_event_rows") or []
               if isinstance(row, dict) and row.get("trade_date") == population.get("report_date")}
    rows = [by_code.get(code, {"code": code}) for code in sorted(codes)]
    local = compute_ladder_metrics(rows)
    assessment = assess_event_metrics(local)
    board = assessment["metrics"]["board_structure"]
    if board["status"] == "ready" and not all(
            _known(row.get("source")) and _timestamp(row.get("source_timestamp")) is not None for row in rows):
        board = {**board, "status": "missing", "reason": "候选板型来源或采集时刻未提供"}
    return {**default, "metrics": {**default["metrics"], "board_structure": board}, "candidate_codes": sorted(codes)}


def qualify_strategies(plans, *, quality: dict, validation_records=None,
                       event_metrics: dict | None = None, report_date: str,
                       target_trade_date: str | None = None) -> dict:
    """Assess explicit independent evidence once; never publish its raw samples."""
    from event_qualification import assess_event_metrics
    records = [r for r in validation_records or [] if isinstance(r, dict)]
    event_assessment = assess_event_metrics(event_metrics)
    results = {}
    for item in plans:
        p = item.to_dict() if hasattr(item, "to_dict") else _dict(item)
        sid = str(p.get("scenario_id") or "")
        record = next((r for r in reversed(records) if r.get("strategy_id") == sid), None)
        validation = assess_validation(p, record, report_date=report_date, target_trade_date=target_trade_date)
        per_plan_events = _event_assessment_for_plan(p, event_metrics, event_assessment)
        results[sid] = _strategy_assessment(p, quality, validation, per_plan_events, report_date=report_date)
        results[sid]["event_qualification"] = per_plan_events
    return _qualification_set(results, quality, event_assessment, report_date=report_date, target_trade_date=target_trade_date)


def public_validation_summary(value: dict) -> dict:
    """Allowlist public scalars even for a malformed, previously cached summary."""
    result = {key: value.get(key) if _known(value.get(key)) else None for key in (
        "schema_version", "strategy_id", "rule_version", "rule_fingerprint",
        "outcome_definition_id", "status", "source", "evidence_ref",
    )}
    original_issues = value.get("issues")
    result["issues"] = ([item for item in original_issues if isinstance(item, str)]
                        if isinstance(original_issues, list) else [])
    if original_issues is not None and (not isinstance(original_issues, list)
            or len(result["issues"]) != len(original_issues)):
        result["issues"].append("validation_summary_invalid")
    for key in ("sample_size", "declared_sample_size"):
        number = _number(value.get(key))
        result[key] = int(number) if number is not None and number >= 0 and number.is_integer() else None
    evaluated = _timestamp(value.get("evaluated_at"))
    result["evaluated_at"] = evaluated.isoformat() if evaluated else None
    for key in ("valid_from", "valid_until"):
        day = _day(value.get(key))
        result[key] = day.isoformat() if day else None
    return result


def refresh_strategy_qualification(quality: dict, plans, *, report_date: str,
                                  target_trade_date: str | None, event_metrics: dict | None = None) -> dict | None:
    """Recheck a pinned permission's date, rules and dependencies; never upgrade.

    Public/replay contexts carry assessment summaries, not private backtest
    samples. A new validation decision is made only by qualify_strategies with
    an explicit evidence file. Rendering can only revoke that decision.
    """
    from event_qualification import assess_event_metrics
    original = scoped_qualification(quality)
    if original is None:
        return None
    results = {}
    original_rows = _dict(original.get("strategies"))
    event_assessment = assess_event_metrics(event_metrics) if event_metrics is not None else _dict(original.get("event_qualification"))
    for item in plans:
        p = item.to_dict() if hasattr(item, "to_dict") else _dict(item)
        sid = str(p.get("scenario_id") or "")
        previous = _dict(original_rows.get(sid))
        validation = public_validation_summary(_dict(previous.get("validation")))
        issues = list(validation.get("issues") or [])
        for field in ("source", "evidence_ref"):
            if not _known(validation.get(field)):
                issues.append("validation_" + field + "_missing")
        if (validation.get("sample_size") is None or validation["sample_size"] < MIN_VALIDATION_SAMPLES
                or validation.get("declared_sample_size") != validation.get("sample_size")):
            issues.append("validation_sample_count_invalid")
        required = {"schema_version": "strategy-validation-assessment/v1", "strategy_id": sid,
                    "rule_fingerprint": rule_fingerprint(p), "rule_version": RULE_VERSION,
                    "outcome_definition_id": STRATEGY_OUTCOME}
        if any(validation.get(key) != value for key, value in required.items()):
            issues.append("validation_rules_changed")
        if original.get("report_date") != report_date or original.get("target_trade_date") != target_trade_date:
            issues.append("validation_plan_dates_changed")
        report, target = _day(report_date), _day(target_trade_date or report_date)
        start, end = _day(validation.get("valid_from")), _day(validation.get("valid_until"))
        evaluated = _timestamp(validation.get("evaluated_at"))
        assessed = evaluated.astimezone(timezone(timedelta(hours=8))).date() if evaluated else None
        if not (report and target and assessed and start and end and assessed <= start <= report <= target <= end):
            issues.append("validation_not_effective")
        if issues:
            validation.update(status="unverified", issues=list(dict.fromkeys(issues)))
        per_plan_events = (_event_assessment_for_plan(p, event_metrics, event_assessment) if event_metrics is not None
                           else _dict(previous.get("event_qualification")) or event_assessment)
        result = _strategy_assessment(p, quality, validation, per_plan_events, report_date=report_date)
        result["event_qualification"] = per_plan_events
        if result["plan_permitted"] and not previous.get("plan_permitted"):
            result.update(status="unverified", plan_permitted=False)
            result["issues"].append("not_authorized_in_original_plan")
        results[sid] = result
    return _qualification_set(results, quality, event_assessment, report_date=report_date, target_trade_date=target_trade_date)


def scoped_qualification(quality: dict | None) -> dict | None:
    value = _dict(_dict(quality).get("strategy_qualification"))
    return value if value.get("schema_version") == "strategy-qualification-set/v1" else None


def qualified_publication_mode(quality: dict | None) -> str | None:
    scoped = scoped_qualification(quality)
    if scoped is None:
        return None
    if core_issues(quality, report_date=scoped.get("report_date")) or not scoped.get("core_ready"):
        return "facts_only"
    approved = [r for r in _dict(scoped.get("strategies")).values() if isinstance(r, dict)
                and r.get("status") == "eligible" and r.get("plan_permitted")
                and _dict(r.get("validation")).get("status") == "validated"]
    return "decision" if approved else "observation"
