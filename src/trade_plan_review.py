from __future__ import annotations

import hashlib
import json
import math
import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

KNOWN_OUTCOME_STATUSES = {
    "not_triggered", "triggered_not_filled", "filled", "unknown", "cancelled",
}


def _json_snapshot(value: Any) -> Any:
    """Detach JSON data and represent non-finite numeric observations as null."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str), parse_constant=lambda _: None)


def _append(path: str | Path, event: dict[str, Any]) -> dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_snapshot(event)
    payload.setdefault("recorded_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    with target.open("ab+") as handle:
        # A legacy/crash tail may lack its final newline. Preserve those bytes,
        # but isolate the next event rather than concatenating two JSON objects.
        handle.seek(0, os.SEEK_END)
        if handle.tell():
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                handle.write(b"\n")
        handle.write((json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    return payload


def _events(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    rows = []
    for raw in target.read_text(encoding="utf-8-sig").splitlines():
        try:
            value = json.loads(raw, parse_constant=lambda _: None)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


# Revision metadata is never part of business-content identity. Versions are
# allocated by append, not by the pure builders; callers should keep its return.
_REVISION_FIELDS = {
    "schema_version", "recorded_at", "appended", "version", "revision_id",
    "previous_revision_id", "content_fingerprint", "status_transition", "change_summary",
    "unassigned_reason",
}
DAILY_DECISION_STATUSES = (
    "no_trade_data_unavailable", "no_trade_strategy_unverified",
    "no_trade_market_defensive", "no_trade_no_candidate",
    "wait_confirmation", "conditional_plan",
)
_DAILY_EXPLANATIONS = {
    "no_trade_data_unavailable": (
        "核心行情缺失、过期或资格未知，不能形成可靠的交易判断。",
        "补齐目标交易日核心行情并重新校验数据资格。",
    ),
    "no_trade_strategy_unverified": (
        "策略资格或既有计划门禁未通过，不把资格不足解释为没有机会。",
        "完成既有策略资格与计划门禁校验后重新评估。",
    ),
    "no_trade_market_defensive": (
        "既有市场或仓位规则要求防守，不开新仓。",
        "重新校验既有市场与仓位规则；未恢复资格前不开新仓。",
    ),
    "no_trade_no_candidate": (
        "当前筛选没有合格候选，不使用风险锚补位。",
        "按原有规则重新筛选；获得合格候选后重新评估。",
    ),
    "wait_confirmation": (
        "当前有效情景或必要信号尚未确认，等待确认而不是成交。",
        "确认有效决策情景及目标交易日必要信号；失效时撤销计划。",
    ),
    "conditional_plan": (
        "保留已获准的条件计划；计划、信号满足均不代表已经成交。",
        "执行前重新确认目标交易日门禁与触发条件，成交由真实结果单独确认。",
    ),
}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _fingerprint(content: dict) -> str:
    encoded = json.dumps(_json_snapshot(content), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _event_content(event: dict) -> dict:
    return {key: value for key, value in event.items() if key not in _REVISION_FIELDS}


def _changes(before: dict, after: dict, prefix: str = "") -> dict:
    changes = {}
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(old, dict) and isinstance(new, dict):
            changes.update(_changes(old, new, name))
        else:
            changes[name] = {"before": deepcopy(old), "after": deepcopy(new)}
    return changes


def _append_revision(
    path: str | Path, record: dict, history: list[dict], *, identity: str,
    status_field: str = "status", content_fn: Callable[[dict], dict] = _event_content,
    comparison_history: list[dict] | None = None,
) -> dict[str, Any]:
    """Append changed content, including A -> B -> A; retrying latest is a no-op.

    Like the existing JSONL writer this assumes one writing process. It never
    rewrites old rows and does not start a service, scheduler, or network call.
    """
    content = content_fn(record)
    comparison = history if comparison_history is None else comparison_history
    previous = comparison[-1] if comparison else {}
    before = content_fn(previous) if previous else {}
    fingerprint = _fingerprint(content)
    if previous and _fingerprint(before) == fingerprint:
        return {**previous, "appended": False}
    version = len(history) + 1
    return {**_append(path, {
        **_event_content(record), "schema_version": record["schema_version"],
        "version": version, "content_fingerprint": fingerprint,
        "revision_id": f"{identity}:r{version}:{fingerprint[:16]}",
        "previous_revision_id": previous.get("revision_id"),
        "status_transition": {"from": before.get(status_field), "to": content.get(status_field)},
        "change_summary": _changes(before, content),
    }), "appended": True}


def _candidate_pairs(plan: dict) -> list[tuple[str, dict]]:
    priority = _dict(plan.get("priority"))
    primary = _dict(priority.get("primary"))
    pairs = [("primary", primary)] if primary else []
    pairs.extend(("alternate", row) for row in priority.get("alternates") or [] if isinstance(row, dict))
    return [(label, row) for label, row in pairs if _text(row.get("code"))]


def _scenario(
    plan: dict, readiness: dict, *, scenario_status: str | None = None,
    decision_scenario_id: str | None = None,
) -> tuple[str, str | None]:
    sources = (plan, readiness, _dict(readiness.get("signal")))
    if decision_scenario_id is None:
        # An explicit null decision ID vetoes the legacy active-ID fallback.
        decision_scenario_id = next(
            (source["decision_scenario_id"] for source in sources if "decision_scenario_id" in source),
            plan.get("active_scenario_id"),
        )
    scenario_id = _text(decision_scenario_id) or None
    if scenario_status is None:
        scenario_status = next(
            (source["scenario_status"] for source in sources if "scenario_status" in source),
            "active" if scenario_id else "no_valid_scenario",
        )
    status = _text(scenario_status).lower()
    if status not in {"active", "awaiting_confirmation", "no_valid_scenario"}:
        status = "no_valid_scenario"
    # top_scenario_id/ranked/signal.scenario_id are deliberately never consulted.
    return status, scenario_id if status == "active" else None


def _daily_status(plan: dict, ready: dict, decision: dict, *, has_candidates: bool,
                  scenario_status: str, scenario_id: str | None) -> str:
    reason = _text(decision.get("reason_code"))
    action = _text(decision.get("status"))
    data = _text(_dict(ready.get("data")).get("status"))
    strategy = _text(_dict(ready.get("strategy")).get("status"))
    if data != "ready" or reason in {"data_unavailable", "data_expired"} or action == "no_trade_data_unavailable":
        return "no_trade_data_unavailable"
    if (strategy not in {"applicable", "not_applicable"} or reason == "qualification_incomplete"
            or action == "no_trade_strategy_unverified"):
        return "no_trade_strategy_unverified"
    # Position may have been zeroed by scenario/candidate/snapshot qualification.
    # Its explicit cause outranks the bare-zero market-defense heuristic.
    if reason in {"no_valid_scenario", "signal_snapshot_unqualified"}:
        return "wait_confirmation"
    if reason == "no_candidates" or action == "no_trade_no_candidate":
        return "no_trade_no_candidate"
    position = _text(plan.get("position"))
    if (reason == "market_no_trade" or action == "no_trade_market_defensive"
            or position == "空仓" or re.fullmatch(r"0(?:\.0+)?\s*成", position)):
        return "no_trade_market_defensive"
    if not has_candidates or reason == "no_candidates" or action == "no_trade_no_candidate":
        return "no_trade_no_candidate"
    if scenario_status != "active" or not scenario_id:
        return "wait_confirmation"
    if not ready.get("plan_permitted") or reason == "existing_gate":
        return "no_trade_strategy_unverified"
    if (_dict(ready.get("signal")).get("status") != "met"
            or action not in {"enter_plan", "conditional_plan"}):
        return "wait_confirmation"
    return "conditional_plan"


def build_daily_decision_record(
    plan: dict | None = None, *, report_date: str, readiness: dict | None = None,
    trading_date: str | None = None, candidate_funnel: dict | None = None,
    candidate_funnel_fingerprint: str | None = None,
    plan_version: str | int | None = None, scenario_status: str | None = None,
    decision_scenario_id: str | None = None,
) -> dict[str, Any]:
    """Build one daily event even when there is no permitted candidate plan.

    Pure snapshot only: no gate changes, ranking-based scenario selection, order
    or fill inference. Supports readiness.decision and the legacy .action axis.
    The event execution_allowed flag mirrors readiness.execution_ready only;
    absence is conservative, never a fallback to action-plan permission.
    Dates are copied, never inferred from a calendar. plan_version is an optional
    upstream revision label; absent one, a content hash identifies the plan.
    append_daily_decision_once assigns this event's separate journal version.
    """
    plan, ready = _dict(plan), _dict(readiness)
    report_date = _text(report_date)
    if not report_date:
        raise ValueError("daily decision requires report_date")
    decision = _dict(ready.get("decision")) or _dict(ready.get("action"))
    funnel = _dict(candidate_funnel) if candidate_funnel is not None else _dict(plan.get("candidate_funnel"))
    candidates = [row for _, row in _candidate_pairs(plan)]
    if "eligible_candidates" in funnel:
        candidates = [row for row in funnel.get("eligible_candidates") or []
                      if isinstance(row, dict) and _text(row.get("code"))]
    has_candidates = bool(candidates)
    status, scenario_id = _scenario(
        plan, ready, scenario_status=scenario_status, decision_scenario_id=decision_scenario_id,
    )
    decision_status = _daily_status(
        plan, ready, decision, has_candidates=has_candidates,
        scenario_status=status, scenario_id=scenario_id,
    )
    default_reason, default_recovery = _DAILY_EXPLANATIONS[decision_status]
    recovery = ready.get("recovery_conditions") or ready.get("recheck_conditions") or [default_recovery]
    if isinstance(recovery, str):
        recovery = [recovery]
    trading_date = trading_date or plan.get("trading_date") or ready.get("trading_date")
    trading_date = trading_date or plan.get("target_trading_date") or ready.get("target_trading_date")
    plan_snapshot = {key: plan[key] for key in ("position", "priority") if key in plan}
    plan_snapshot.update(scenario_status=status, decision_scenario_id=scenario_id,
                         trading_date=_text(trading_date) or None)
    if plan_version is None:
        plan_version = plan.get("plan_version", plan.get("version"))
    if plan_version is None and plan:
        plan_version = _fingerprint(plan_snapshot)
    record = {
        "event_type": "daily_decision", "schema_version": "daily-decision/v1",
        "decision_id": f"daily-decision:{report_date}", "report_date": report_date,
        "trading_date": _text(trading_date) or None,
        "status": decision_status, "reason_code": _text(decision.get("reason_code")) or decision_status,
        "reason": _text(decision.get("reason")) or default_reason,
        "recovery_conditions": list(recovery), "readiness": ready,
        "scenario_status": status, "decision_scenario_id": scenario_id,
        "candidate_funnel_fingerprint": (
            _text(candidate_funnel_fingerprint) or _text(funnel.get("fingerprint"))
            or _fingerprint(funnel if funnel else {"candidates": candidates})
        ),
        "candidate_codes": [_text(row.get("code")) for row in candidates],
        "plan_version": plan_version, "plan_snapshot": plan_snapshot,
        "plan_permitted": bool(ready.get("plan_permitted")),
        "execution_allowed": ready.get("execution_ready") is True,
    }
    record["content_fingerprint"] = _fingerprint(_event_content(record))
    return _json_snapshot(record)


def append_daily_decision_once(path: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    """Persist a daily snapshot or return the identical latest revision unchanged."""
    decision_id = _text(record.get("decision_id"))
    if not decision_id or not _text(record.get("report_date")):
        raise ValueError("daily decision requires decision_id and report_date")
    if record.get("status") not in DAILY_DECISION_STATUSES:
        raise ValueError("unknown daily decision status")
    history = [event for event in _events(path)
               if event.get("event_type") == "daily_decision" and event.get("decision_id") == decision_id]
    return _append_revision(path, {**record, "event_type": "daily_decision", "schema_version": "daily-decision/v1"},
                            history, identity=decision_id)


def _date_matches(value: Any, report_date: str | None, from_report_date: str | None,
                  through_report_date: str | None) -> bool:
    if not (report_date or from_report_date or through_report_date):
        return True
    day = _text(value)
    return bool(day and (not report_date or day == report_date)
                and (not from_report_date or day >= from_report_date)
                and (not through_report_date or day <= through_report_date))


def _check_date_filters(report_date: str | None, from_report_date: str | None,
                        through_report_date: str | None) -> None:
    if report_date and (from_report_date or through_report_date):
        raise ValueError("report_date is exact; use either it or the inclusive history date range")
    if from_report_date and through_report_date and from_report_date > through_report_date:
        raise ValueError("from_report_date must not exceed through_report_date")


def build_daily_decision_review(
    path: str | Path, *, report_date: str | None = None,
    from_report_date: str | None = None, through_report_date: str | None = None,
) -> dict[str, Any]:
    """Review latest decisions per report day; retain all revisions in each item.

    Omit dates for all history, or pass through_report_date for cumulative history.
    report_date retains exact-day semantics; ranges are inclusive ISO dates.
    """
    _check_date_filters(report_date, from_report_date, through_report_date)
    histories: dict[str, list[dict]] = {}
    for event in _events(path):
        if (event.get("event_type") == "daily_decision" and event.get("decision_id")
                and _date_matches(event.get("report_date"), report_date, from_report_date, through_report_date)):
            histories.setdefault(str(event["decision_id"]), []).append(event)
    items = sorted(({**rows[-1], "revisions": rows} for rows in histories.values()),
                   key=lambda row: (row["report_date"], row["decision_id"]))
    counts = {status: 0 for status in DAILY_DECISION_STATUSES}
    for item in items:
        status = item.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "schema_version": "daily-decision-review/v1", "report_date": _text(report_date),
        "from_report_date": from_report_date, "through_report_date": through_report_date,
        "decision_count": len(items), "revision_count": sum(len(rows) for rows in histories.values()),
        "status_counts": counts, "items": items,
    }


def _logical_plan_id(record: dict) -> str:
    explicit = _text(record.get("logical_plan_id"))
    if explicit:
        return explicit
    day, code = _text(record.get("report_date")), _text(record.get("code"))
    return f"{day}:{code}" if day and code else _text(record.get("plan_id"))


def _plan_content(record: dict) -> dict:
    content = _event_content(record)
    for key in ("plan_id", "logical_plan_id", "plan_version"):
        content.pop(key, None)
    # Missing v1 context is unknown, not a new revision merely due to migration.
    content.setdefault("trading_date", None)
    content.setdefault("scenario_status", "no_valid_scenario")
    content.setdefault("decision_scenario_id", None)
    return content


def _plan_histories(events: list[dict]) -> dict[str, list[dict]]:
    histories: dict[str, list[dict]] = {}
    for event in events:
        if event.get("event_type") != "trade_plan" or not _text(event.get("plan_id")):
            continue
        identity = _logical_plan_id(event)
        history = histories.setdefault(identity, [])
        version = len(history) + 1
        fingerprint = _fingerprint(_plan_content(event))
        history.append({
            **event, "logical_plan_id": identity, "version": version, "plan_version": version,
            "content_fingerprint": fingerprint,
            "revision_id": event.get("revision_id") or f"{identity}:r{version}:{fingerprint[:16]}",
        })
    return histories


def build_trade_plan_records(
    plan: dict | None, *, report_date: str, readiness: dict | None = None,
) -> list[dict[str, Any]]:
    """Build candidate plans only when permitted, retaining the legacy call/ID.

    plan_id keeps the v1 spelling for existing callers; logical_plan_id is stable
    across priority changes. The builder provides a content fingerprint, while
    append_trade_plan_once assigns plan_version and a unique revision_id. New
    scenario context is copied, never inferred from scenario ranking.
    """
    plan, readiness = _dict(plan), _dict(readiness)
    if not readiness.get("plan_permitted"):
        return []
    scenario_status, scenario_id = _scenario(plan, readiness)
    trading_date = (plan.get("trading_date") or readiness.get("trading_date")
                    or plan.get("target_trading_date") or readiness.get("target_trading_date"))
    records = []
    for priority_label, row in _candidate_pairs(plan):
        code = _text(row.get("code"))
        record = {
            "event_type": "trade_plan", "schema_version": "trade-plan/v2",
            "plan_id": f"{report_date}:{code}:{priority_label}:v1",
            "logical_plan_id": f"{report_date}:{code}",
            "report_date": str(report_date), "trading_date": _text(trading_date) or None,
            "code": code, "name": str(row.get("name") or ""),
            "sector": str(row.get("sector") or ""), "role": str(row.get("role") or ""),
            "priority": priority_label, "planned_action": str(row.get("action") or ""),
            "trigger": str(row.get("trigger") or ""), "invalid": str(row.get("invalid") or ""),
            "position_cap": str(plan.get("position") or ""), "plan_status": "conditional",
            "scenario_status": scenario_status, "decision_scenario_id": scenario_id,
        }
        record["content_fingerprint"] = _fingerprint(_plan_content(record))
        records.append(record)
    return records


def append_trade_plan_once(path: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    """Append a changed revision, without rewriting an earlier candidate plan."""
    plan_id = _text(record.get("plan_id"))
    if not plan_id:
        raise ValueError("trade plan requires plan_id")
    identity = _logical_plan_id(record)
    history = _plan_histories(_events(path)).get(identity, [])
    return _append_revision(path, {
        **record, "plan_id": plan_id, "logical_plan_id": identity,
        "plan_version": len(history) + 1, "event_type": "trade_plan", "schema_version": "trade-plan/v2",
    }, history, identity=identity, status_field="plan_status", content_fn=_plan_content)


def _plan_aliases(plans: dict[str, list[dict]]) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for identity, history in plans.items():
        for alias in {identity, *(str(plan["plan_id"]) for plan in history)}:
            candidates.setdefault(alias, set()).add(identity)
    return {alias: next(iter(identities)) for alias, identities in candidates.items() if len(identities) == 1}


def _outcome_status(event: dict) -> str:
    status = _text(event.get("status")).lower()
    return status if status in KNOWN_OUTCOME_STATUSES else "unknown"


def _outcome_content(record: dict) -> dict:
    content = _event_content(record)
    content.pop("plan_id", None)
    content.pop("logical_plan_id", None)
    content.setdefault("plan_revision_id", None)
    content.setdefault("plan_version", None)
    content["status"] = _outcome_status(record)
    content["actual"] = _dict(record.get("actual"))
    return content


def _outcome_histories(
    events: list[dict], plans: dict[str, list[dict]],
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Project safe references; retain every other result separately, without a plan.

    An orphan writer's logical_plan_id == plan_id is provisional when it has no
    revision binding. It may resolve through a unique reference when the parent
    arrives. A different, explicitly recorded logical identity is not replaced
    by an unrelated alias. This projection never rewrites the journal.
    """
    references: dict[str, set[str]] = {}
    revision_owners: dict[str, set[str]] = {}
    for identity, rows in plans.items():
        for alias in {identity, *(str(row["plan_id"]) for row in rows)}:
            references.setdefault(alias, set()).add(identity)
        for row in rows:
            revision_owners.setdefault(row["revision_id"], set()).add(identity)
    histories: dict[str, list[dict]] = {}
    unassigned: list[dict] = []
    unassigned_versions: dict[str, int] = {}
    for event in events:
        if event.get("event_type") != "trade_plan_outcome":
            continue
        declared = _text(event.get("logical_plan_id"))
        reference = _text(event.get("plan_id"))
        revision = _text(event.get("plan_revision_id"))
        provisional = declared == reference and not revision
        identity = None
        reason = "no_matching_plan"
        if declared in plans and not provisional:
            identity = declared
        elif declared and declared != reference:
            reason = "unknown_logical_plan"
        else:
            candidates = references.get(reference, set())
            if revision:
                candidates = candidates & revision_owners.get(revision, set())
            if len(candidates) == 1:
                identity = next(iter(candidates))
            elif len(candidates) > 1:
                reason = "ambiguous_plan_reference"
        if identity is not None:
            history = histories.setdefault(identity, [])
            version = len(history) + 1
            event_identity = identity
        else:
            event_identity = declared or reference or "unassigned"
            version = unassigned_versions.get(event_identity, 0) + 1
            unassigned_versions[event_identity] = version
        fingerprint = _fingerprint(_outcome_content(event))
        projected = {
            **event, "status": _outcome_status(event), "version": version,
            "content_fingerprint": fingerprint,
            "revision_id": event.get("revision_id") or f"{event_identity}:outcome:r{version}:{fingerprint[:16]}",
        }
        if identity is not None:
            histories[identity].append({**projected, "logical_plan_id": identity})
        else:
            unassigned.append({**projected, "unassigned_reason": reason})
    return histories, unassigned


def _matching_revisions(revisions: list[dict], plan_id: str) -> list[dict]:
    matches = [row for row in revisions if _text(row.get("plan_id")) == plan_id]
    if matches:
        return matches
    return revisions if revisions and revisions[0]["logical_plan_id"] == plan_id else []


def _resolve_outcome_target(
    plans: dict[str, list[dict]], plan_id: str, plan_revision_id: str | None,
) -> tuple[str, dict | None]:
    candidates = {identity: rows for identity, rows in plans.items()
                  if identity == plan_id or _matching_revisions(rows, plan_id)}
    if plan_revision_id is not None:
        matches = [(identity, row) for identity, rows in candidates.items()
                   for row in rows if row["revision_id"] == plan_revision_id]
        if len(matches) != 1:
            raise ValueError("plan revision must belong to the referenced plan")
        return matches[0]
    if not candidates:
        # Preserve legacy orphan/ID-only recording without inventing a binding.
        return plan_id, None
    if len(candidates) == 1:
        identity, revisions = next(iter(candidates.items()))
        matches = _matching_revisions(revisions, plan_id)
        if len(matches) == 1:
            return identity, matches[0]
    raise ValueError("ambiguous plan_id; provide explicit plan_revision_id")


def _outcomes_by_revision(
    revisions: list[dict], history: list[dict],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Resolve explicit IDs or unique legacy aliases, never the latest revision.

    Legacy rows are enriched only in this read projection, not rewritten. An
    ambiguous/unrecognized reference stays in a separate unbound alias group.
    """
    known = {row["revision_id"]: row for row in revisions}
    bound: dict[str, list[dict]] = {identity: [] for identity in known}
    unbound: dict[str, list[dict]] = {}
    for event in history:
        revision_id = _text(event.get("plan_revision_id"))
        if not revision_id:
            matches = _matching_revisions(revisions, _text(event.get("plan_id")))
            if len(matches) == 1:
                revision_id = matches[0]["revision_id"]
        if revision_id in known:
            bound[revision_id].append({
                **event, "plan_revision_id": revision_id,
                "plan_version": known[revision_id]["plan_version"],
            })
        else:
            key = revision_id or "alias:" + _text(event.get("plan_id"))
            unbound.setdefault(key, []).append(event)
    return bound, unbound


def _effective_outcome(history: list[dict]) -> dict | None:
    # Within ONE revision/alias, unknown cannot reverse a confirmed result.
    return next((event for event in reversed(history) if _outcome_status(event) != "unknown"),
                history[-1] if history else None)


def append_trade_plan_outcome_once(
    path: str | Path, plan_id: str, status: str, *, actual: dict | None = None,
    plan_revision_id: str | None = None,
) -> dict[str, Any]:
    """Append an explicit result/correction for one unambiguously bound revision.

    Without plan_revision_id only a unique exact plan_id alias (or single-revision
    logical ID) can bind. Reused aliases raise ValueError before writing; there
    is no latest-revision fallback, even for a retry. Keep saved revision IDs.
    Compare only the latest effective result of that target: identical retries
    do nothing, A -> B -> A corrections append, and unknown never erases a known
    result. No signal or trigger is converted to a fill.
    """
    normalized, plan_id = _text(status).lower(), _text(plan_id)
    if normalized not in KNOWN_OUTCOME_STATUSES:
        raise ValueError(f"unknown trade plan outcome status: {status}")
    if not plan_id:
        raise ValueError("trade plan outcome requires plan_id")
    events = _events(path)
    plans = _plan_histories(events)
    identity, target = _resolve_outcome_target(plans, plan_id, plan_revision_id)
    record = {
        "event_type": "trade_plan_outcome", "schema_version": "trade-plan-outcome/v2",
        "plan_id": plan_id, "logical_plan_id": identity, "status": normalized,
        "actual": _json_snapshot(_dict(actual)),
        "plan_revision_id": target["revision_id"] if target else None,
        "plan_version": target["plan_version"] if target else None,
    }
    histories, unassigned = _outcome_histories(events, plans)
    history = histories.get(identity, []) if target else [
        event for event in unassigned if _text(event.get("plan_id")) == plan_id
        and _text(event.get("logical_plan_id")) in {"", identity}
    ]
    bound, unbound = _outcomes_by_revision(plans.get(identity, []), history)
    scope = bound[target["revision_id"]] if target else unbound.get("alias:" + plan_id, [])
    previous = _effective_outcome(scope)
    if previous and normalized == "unknown" and _outcome_status(previous) != "unknown":
        return {**previous, "appended": False}
    return _append_revision(
        path, record, history, identity=f"{identity}:outcome", content_fn=_outcome_content,
        comparison_history=[previous] if previous else [],
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _known_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.lower() in {"", "unknown", "none", "null", "n/a", "na", "unspecified", "-", "--", "未知", "未提供"}:
        return None
    return text


def _qualified_pnl(outcome: dict | None) -> tuple[float, str, str] | None:
    if not outcome or _outcome_status(outcome) != "filled":
        return None
    actual = _dict(outcome.get("actual"))
    # Generic legacy pnl may be gross/percent; do not reinterpret it as money.
    value = _number(actual.get("net_pnl"))
    source = _known_label(actual.get("source"))
    currency = (_known_label(actual.get("currency")) or "").upper()
    basis = _known_label(actual.get("pnl_basis")) or ""
    if (value is None or source is None or not re.fullmatch(r"[A-Z]{3}", currency)
            or currency in {"UNK", "XXX", "NAN"}
            or basis.lower() in {"", "unknown", "none", "null", "n/a", "gross", "未知"}):
        return None
    return value, currency, basis


def _revision_review(
    revisions: list[dict], history: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    bound, unbound = _outcomes_by_revision(revisions, history)
    rows = []
    for plan in revisions:
        events = bound[plan["revision_id"]]
        outcome = _effective_outcome(events)
        pnl = _qualified_pnl(outcome)
        rows.append({
            "plan_revision_id": plan["revision_id"], "plan_version": plan["plan_version"],
            "plan_status": plan.get("plan_status"),
            "status": _outcome_status(outcome) if outcome else "pending",
            "outcome": outcome, "outcome_revisions": events,
            "pnl_status": "known" if pnl else "unknown", "net_pnl": pnl[0] if pnl else None,
            "net_pnl_currency": pnl[1] if pnl else None, "net_pnl_basis": pnl[2] if pnl else None,
        })
    unbound_events = [event for events in unbound.values() for event in events]
    unbound_results = [result for events in unbound.values()
                       if (result := _effective_outcome(events)) is not None]
    return rows, unbound_events, unbound_results


def _historical_outcome(revision_rows: list[dict], unbound_results: list[dict]) -> dict | None:
    """Compatibility summary: a historical fill/trigger is not a latest-state claim.

    Prefer an explicit bound result over an unbound one, and later PLAN revisions
    over earlier ones. Arrival time across different revisions is irrelevant.
    """
    results = unbound_results + [row["outcome"] for row in revision_rows if row["outcome"]]
    for status in ("filled", "triggered_not_filled"):
        match = next((row for row in reversed(results) if _outcome_status(row) == status), None)
        if match is not None:
            return match
    return next((row for row in reversed(results) if _outcome_status(row) != "unknown"),
                results[-1] if results else None)


def _trade_id(outcome: dict) -> str | None:
    value = _dict(outcome.get("actual")).get("trade_id")
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    return _known_label(value)


def _pnl_groups(values: list[tuple[float, str, str]]) -> list[dict]:
    groups: dict[tuple[str, str], list[float]] = {}
    for value, currency, basis in values:
        groups.setdefault((currency, basis), []).append(value)
    return [{"currency": currency, "pnl_basis": basis, "count": len(amounts),
             "net_pnl": _number(sum(amounts))}
            for (currency, basis), amounts in sorted(groups.items())]


def _aggregate_review_pnl(items: list[dict], unassigned_outcomes: list[dict] | None = None) -> dict:
    """Qualify each effective revision result, then deduplicate monetary observations.

    actual.trade_id must be stable across the journal (namespace by broker/account
    when necessary). Equal IDs with equal qualified amounts/units count once;
    conflicting values never use last-write-wins. Multiple filled revisions need
    IDs on every fill. A single unambiguous revision needs no invented trade ID.
    """
    owners: dict[tuple[str, str], list[tuple[dict | None, dict]]] = {}
    item_keys: dict[str, set[tuple[str, str]]] = {}
    for item in items:
        fills = item["historical_fill_outcomes"]
        reasons = []
        if item["unbound_filled_outcome_count"]:
            reasons.append("unbound_filled_result")
        if len(fills) > 1 and any(_trade_id(outcome) is None for outcome in fills):
            reasons.append("missing_trade_id_for_multiple_fills")
        item["pnl_ambiguity_reasons"] = reasons
        keys: set[tuple[str, str]] = set()
        item_keys[item["logical_plan_id"]] = keys
        for outcome in fills:
            trade_id = _trade_id(outcome)
            if trade_id is None and reasons:
                continue
            key = ("trade", trade_id) if trade_id is not None else ("plan", item["logical_plan_id"])
            # Ineligible plans still contribute every explicitly identified fill
            # to conflict detection; only their contribution to totals is barred.
            owners.setdefault(key, []).append((item, outcome))
            if not reasons:
                keys.add(key)

    unassigned_fills = [event for event in unassigned_outcomes or [] if _outcome_status(event) == "filled"]
    for event in unassigned_fills:
        trade_id = _trade_id(event)
        if trade_id is not None:
            owners.setdefault(("trade", trade_id), []).append((None, event))

    qualified: dict[tuple[str, str], tuple[float, str, str]] = {}
    conflict_found = False
    for key, references in owners.items():
        values = [_qualified_pnl(outcome) for _, outcome in references]
        known_values = {value for value in values if value is not None}
        if len(known_values) > 1:
            conflict_found = True
            for item, _ in references:
                if item is not None and "conflicting_trade_id_results" not in item["pnl_ambiguity_reasons"]:
                    item["pnl_ambiguity_reasons"].append("conflicting_trade_id_results")
        elif known_values and all(value is not None for value in values):
            qualified[key] = next(iter(known_values))

    # Filter only after every key has contributed its conflict evidence. A later
    # conflicting key may disqualify the owner of an earlier otherwise-known key.
    qualified = {key: value for key, value in qualified.items()
                 if any(item is not None and not item["pnl_ambiguity_reasons"] for item, _ in owners[key])}
    for item in items:
        keys = item_keys[item["logical_plan_id"]] if not item["pnl_ambiguity_reasons"] else set()
        values = [qualified[key] for key in sorted(keys) if key in qualified]
        groups = _pnl_groups(values)
        complete = bool(keys) and len(values) == len(keys) and all(group["net_pnl"] is not None for group in groups)
        item["pnl_status"] = "ambiguous" if item["pnl_ambiguity_reasons"] else "known" if complete else "unknown"
        item["pnl_known_result_count"] = len(values)
        item["pnl_by_currency_and_basis"] = groups
        total = groups[0] if item["pnl_status"] == "known" and len(groups) == 1 else {}
        item.update(net_pnl=total.get("net_pnl"), net_pnl_currency=total.get("currency"),
                    net_pnl_basis=total.get("pnl_basis"))

    groups = _pnl_groups(list(qualified.values()))
    filled_items = [item for item in items if item["has_historical_fill"]]
    known_count = sum(item["pnl_status"] == "known" for item in filled_items)
    ambiguous_count = sum(item["pnl_status"] == "ambiguous" for item in filled_items)
    if ambiguous_count or unassigned_fills:
        aggregation_status = "ambiguous"
    elif not filled_items or known_count != len(filled_items) or any(group["net_pnl"] is None for group in groups):
        aggregation_status = "unknown"
    else:
        aggregation_status = "known" if len(groups) == 1 else "mixed_units"
    total = groups[0] if aggregation_status == "known" else {}
    ambiguity_reasons = {reason for item in items for reason in item["pnl_ambiguity_reasons"]}
    if unassigned_fills:
        ambiguity_reasons.add("unassigned_filled_result")
    if conflict_found:
        ambiguity_reasons.add("conflicting_trade_id_results")
    return {
        "pnl_known_count": known_count, "pnl_unknown_count": len(filled_items) - known_count,
        "pnl_known_result_count": len(qualified), "pnl_ambiguous_plan_count": ambiguous_count,
        "pnl_aggregation_status": aggregation_status,
        "pnl_ambiguity_reasons": sorted(ambiguity_reasons),
        "pnl_by_currency_and_basis": groups,
        "net_pnl": total.get("net_pnl"), "net_pnl_currency": total.get("currency"),
        "net_pnl_basis": total.get("pnl_basis"), "has_realized_trade_result": bool(qualified),
    }


def build_trade_plan_review(
    path: str | Path, *, report_date: str | None = None,
    from_report_date: str | None = None, through_report_date: str | None = None,
) -> dict[str, Any]:
    """Review latest plan state separately from effective historical fill facts.

    Dates filter PLAN report dates, not confirmation timestamps: late corrections
    close their exact revision. Each revision has its own latest known outcome.
    The legacy filled_count/status_counts are logical-plan history summaries,
    not trade counts. Use latest_status_counts/latest_pending_count and per-item
    latest_plan_status/latest_outcome for the current revision. The compatibility
    item.outcome is representative history, not necessarily latest_outcome.

    revision_outcomes retains every plan revision and its corrections. Ambiguous
    legacy bindings remain in unbound_outcomes. P&L needs explicit actual.source,
    currency, pnl_basis and finite net_pnl. Multiple filled revisions need stable
    actual.trade_id values; ambiguous/conflicting fills preserve facts but block
    the aggregate. pnl_known_count stays a logical-plan count; the new
    pnl_known_result_count counts qualified, deduplicated monetary observations.
    Grouped subtotals may be partial when pnl_aggregation_status is not known or
    mixed_units. net_pnl is only emitted for a complete, comparable known total.

    unassigned_outcomes retains results with no safe logical-plan association.
    Their plan-report-date scope is unknown, so this separate collection is not
    trimmed by date filters (assigned out-of-range plans are simply excluded).
    No plans/counts are fabricated for them. Their raw log-entry counts are also
    included in unbound_outcome_count/unbound_filled_outcome_count. Unassigned
    fills block a complete total and their identified trades still supply conflict
    evidence; they never supply a monetary subtotal by themselves.
    """
    _check_date_filters(report_date, from_report_date, through_report_date)
    events = _events(path)
    all_plans = _plan_histories(events)
    outcomes, unassigned = _outcome_histories(events, all_plans)
    plans = {identity: rows for identity, rows in all_plans.items()
             if _date_matches(rows[-1].get("report_date"), report_date, from_report_date, through_report_date)}
    status_counts = {status: 0 for status in sorted(KNOWN_OUTCOME_STATUSES)}
    latest_status_counts = dict(status_counts)
    items = []
    outcome_count = 0
    latest_pending_count = 0
    for identity, revisions in sorted(plans.items(), key=lambda pair: (_text(pair[1][-1].get("report_date")), pair[0])):
        plan = revisions[-1]
        history = outcomes.get(identity, [])
        rows, unbound_events, unbound_results = _revision_review(revisions, history)
        latest = rows[-1]["outcome"]
        if latest is None:
            latest_pending_count += 1
        else:
            latest_status_counts[_outcome_status(latest)] += 1
        outcome = _historical_outcome(rows, unbound_results)
        if history:
            outcome_count += 1
        if outcome is not None:
            status_counts[_outcome_status(outcome)] += 1
        bound_fills = [row["outcome"] for row in rows if row["status"] == "filled"]
        unbound_fills = [row for row in unbound_results if _outcome_status(row) == "filled"]
        result_revision = outcome.get("plan_revision_id") if outcome else None
        items.append({
            "plan_id": plan["plan_id"], "logical_plan_id": identity,
            "report_date": _text(plan.get("report_date")), "plan": plan, "revisions": revisions,
            "outcome": outcome, "outcome_revisions": history,
            "outcome_matches_latest_revision": result_revision == plan["revision_id"] if result_revision else None,
            "latest_plan_status": plan.get("plan_status"), "latest_outcome": latest,
            "latest_outcome_status": rows[-1]["status"], "revision_outcomes": rows,
            "has_historical_fill": bool(bound_fills or unbound_fills),
            "historical_fill_outcomes": bound_fills + unbound_fills,
            "filled_revision_count": len(bound_fills), "unbound_outcomes": unbound_events,
            "unbound_filled_outcome_count": len(unbound_fills),
        })
    unassigned_filled_count = sum(_outcome_status(event) == "filled" for event in unassigned)
    pnl_summary = _aggregate_review_pnl(items, unassigned)
    note = "暂无明确成交收益结果；未触发、未成交和未知不会计入交易胜率；收益须来源、币种、口径与有限值均明确。"
    if pnl_summary["pnl_known_result_count"]:
        note = "收益仅统计 filled 且来源、币种、口径与有限值均明确的结果；明确交易标识用于去重。"
    aggregation_status = pnl_summary["pnl_aggregation_status"]
    if aggregation_status == "ambiguous":
        note += "存在无法去重或相互矛盾的成交结果，净收益保持未知；分组只列无歧义部分。"
    elif aggregation_status == "mixed_units":
        note += "不同币种或口径分别列示，不合并求和。"
    elif aggregation_status == "unknown" and pnl_summary["pnl_known_result_count"]:
        note += "仍有结果资格未知，分组仅为已知部分，完整净收益保持未知。"
    if unassigned:
        note += "未分配结果缺少可验证的计划报告日，日期范围不裁剪该集合；其计数是日志条数，不是计划或交易笔数。"
    note += "历史成交事实与最新计划状态分列；修订数、逻辑计划数均不是订单笔数。"
    filled_count = sum(item["has_historical_fill"] for item in items)
    return {
        "schema_version": "trade-plan-review/v2", "report_date": _text(report_date),
        "from_report_date": from_report_date, "through_report_date": through_report_date,
        "plan_count": len(plans), "plan_revision_count": sum(len(rows) for rows in plans.values()),
        "outcome_count": outcome_count,
        "outcome_revision_count": sum(len(outcomes.get(identity, [])) for identity in plans),
        "pending_count": len(plans) - outcome_count, "status_counts": status_counts,
        "triggered_count": status_counts["triggered_not_filled"] + filled_count, "filled_count": filled_count,
        "latest_status_counts": latest_status_counts, "latest_pending_count": latest_pending_count,
        "filled_revision_count": sum(item["filled_revision_count"] for item in items),
        "unbound_outcome_count": sum(len(item["unbound_outcomes"]) for item in items) + len(unassigned),
        "unbound_filled_outcome_count": sum(item["unbound_filled_outcome_count"] for item in items) + unassigned_filled_count,
        "unassigned_outcomes": unassigned, "unassigned_outcome_count": len(unassigned),
        "unassigned_filled_outcome_count": unassigned_filled_count,
        "unassigned_date_scope": "unknown_plan_report_date" if unassigned else None,
        **pnl_summary, "items": items, "note": note,
    }
