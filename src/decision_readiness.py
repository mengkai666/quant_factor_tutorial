"""Explain data, strategy, signal and action readiness without relaxing any gate.

This module does not select stocks, recompute market thresholds or set positions.
A conditional plan's permission and confirmation at a recorded snapshot are
separate: neither is an order, a fill, or a signal for a future trading session.
"""
from __future__ import annotations

from html import escape
import re
from typing import Any

from report_logic import resolve_publication_mode


_CORE_MODULES = ("universe", "price_raw", "breadth", "limit_pool")
_MODULES = {
    "universe": ("证券主数据", "core_market", "补齐报告日有效证券与交易状态后重新校验。"),
    "price_raw": ("未复权行情", "core_market", "补齐报告日及上一交易日未复权行情，核对日期与覆盖率。"),
    "breadth": ("市场宽度", "core_market", "使用同口径、同交易日行情重算上涨下跌家数。"),
    "limit_pool": ("涨跌停事实", "core_market", "取得报告日完整涨跌停池并通过日期、证券代码校验。"),
    "echelon": ("连板梯队", "strategy_data", "补齐逐股连板高度，与完整涨停事实池核对。"),
    "sector": ("主线归因", "strategy_data", "补齐候选题材归因并核对覆盖范围。"),
    "price_qfq": ("复权历史行情", "strategy_data", "补齐所需区间的复权行情并校验复权口径。"),
    "daily_delta": ("昨日逐股反馈", "strategy_data", "补齐上一交易日全部涨停成员的当日结果，区分断板与真正缺失。"),
    "history": ("历史样本", "validation", "补齐历史样本及已到期结果，通过统计验证后重新评估。"),
    "bomb_metrics": (
        "炸板、回封与板型", "strategy_data",
        "补齐完整封板尝试样本、开板/回封记录与板型，再重算指标；不能只用收盘涨停名单代替。",
    ),
    "ai": (
        "AI文案", "narrative",
        "恢复AI服务并重新校验；AI依赖分离未经验证前，保留现有发布门禁。",
    ),
    "run_id_consistency": ("数据批次一致性", "core_market", "统一数据批次并重新校验，不能混用不同批次事实。"),
}
_INTRADAY_PHASES = ("auction", "early_0935", "confirm_1000", "afternoon")
_PHASE_LABELS = {
    "auction": "竞价", "early_0935": "9:35",
    "confirm_1000": "10:00", "afternoon": "午后", "close": "收盘",
}
_LABELS = {
    "data": {"ready": "通过", "missing": "缺失", "expired": "过期"},
    "strategy": {"applicable": "适用", "not_applicable": "不适用", "unverified": "尚未验证"},
    "signal": {"met": "已满足", "not_triggered": "未触发", "invalidated": "已失效", "not_evaluable": "不可评估"},
    "action": {"no_new_positions": "不开新仓", "wait_confirmation": "等待确认", "enter_plan": "进入可执行计划"},
}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _axis(axis: str, status: str, **fields: Any) -> dict:
    return {"status": status, "label": _LABELS[axis][status], **fields}


def build_decision_readiness(
    *, quality: dict | None = None, market_state: dict | None = None,
    action_plan: dict | None = None, scenario_posterior: dict | None = None,
    phase_snapshots: list | None = None, report_date: str | None = None,
) -> dict[str, Any]:
    """Return an explanatory, conservative projection of existing decisions.

    Data qualification refers to core market facts, not to every optional module.
    AI may leave those facts usable, but never grants an exemption from the
    existing observation/facts-only gate. Unknown signal evidence is not a buy.
    """
    quality, state, plan = _dict(quality), _dict(market_state), _dict(action_plan)
    modules = {str(k): _dict(v) for k, v in _dict(quality.get("modules")).items()}
    mode = resolve_publication_mode(plan.get("publication_mode"), quality=quality, market_state=state)
    issues: list[dict] = []

    def add_issue(module: str, status: str, *, scope: str | None = None,
                  label: str | None = None, recheck: str | None = None) -> None:
        if any(item["module"] == module for item in issues):
            return
        spec = _MODULES.get(module, (module, "strategy_data", "补齐该模块的数据并重新校验。"))
        item = modules.get(module, {})
        issues.append({
            "module": module, "label": label or spec[0], "scope": scope or spec[1],
            "status": status, "missing_fields": list(item.get("missing_fields") or []),
            "recheck": recheck or spec[2],
        })

    if modules:
        for name in _CORE_MODULES:
            if modules.get(name, {}).get("status") != "ok":
                add_issue(name, str(modules.get(name, {}).get("status") or "unknown"))
        for name, item in modules.items():
            if item.get("status") != "ok":
                add_issue(name, str(item.get("status") or "unknown"),
                          scope="core_market" if item.get("critical") else None)
    elif str(quality.get("status") or "") != "ok" and mode != "decision":
        add_issue("core_market", "unknown", scope="core_market", label="基础行情校验",
                  recheck="取得报告日核心行情校验结果后重新评估，不能把未知状态当作市场结论。")

    for name in quality.get("critical_blocked") or []:
        add_issue(str(name), "blocked", scope="core_market")
    for name in quality.get("decision_degraded") or []:
        add_issue(str(name), str(modules.get(str(name), {}).get("status") or "degraded"))

    expired = bool(quality.get("used_stale") or quality.get("freshness_level") in {"stale", "expired"})
    expired = expired or any(
        item.get("status") in {"stale", "expired"} or item.get("used_stale")
        or _dict(item.get("lineage")).get("used_stale")
        for name, item in modules.items() if name in _CORE_MODULES or item.get("critical")
    )
    if expired:
        add_issue("freshness", "expired", scope="core_market", label="核心行情时效",
                  recheck="更新到目标交易日的数据，核对来源时间与有效期后重新校验。")
    core_issues = [item for item in issues if item["scope"] == "core_market"]
    data_status = "expired" if expired else ("missing" if core_issues else "ready")

    statistics = _dict(state.get("statistics_layer"))
    if statistics.get("status") not in {None, "", "ok"}:
        add_issue("statistics", str(statistics["status"]), scope="validation", label="统计验证",
                  recheck="补齐同型可评分样本与到期结果，通过既有统计验证后重新评估。")
    decision = _dict(state.get("decision_layer"))
    if decision.get("status") not in {None, "", "ready"} and not issues:
        add_issue("strategy_validation", str(decision["status"]), scope="validation", label="策略资格验证",
                  recheck="完成既有策略资格校验，不能把条件性结论当作已验证策略。")
    if mode != "decision" and not issues:
        add_issue("publication_gate", mode, scope="validation", label="现行发布门禁",
                  recheck="核对现行门禁阻断原因并重新运行校验，不手动把观察模式改为可执行。")

    candidates = [
        row for group in plan.get("groups") or [] if isinstance(group, dict)
        for row in group.get("rows") or [] if isinstance(row, dict)
        and str(row.get("role") or group.get("code") or "") in {"attack", "confirm"}
    ]
    position = str(plan.get("position") or "").strip()
    zero_position = position == "空仓" or bool(re.fullmatch(r"0(?:\.0+)?\s*成", position))
    qualification_missing = data_status != "ready" or bool(issues) or mode != "decision"
    if qualification_missing:
        strategy_status = "unverified"
    elif zero_position or not candidates:
        strategy_status = "not_applicable"
    else:
        strategy_status = "applicable"

    timeline = [item for item in _dict(scenario_posterior).get("timeline") or [] if isinstance(item, dict)]
    latest = timeline[-1] if timeline else {}
    phase_rows = timeline + [item for item in (phase_snapshots or []) if isinstance(item, dict)]
    observed_phases = []
    for item in phase_rows:
        phase = str(item.get("phase") or "").strip().lower()
        if phase in {"close", *_INTRADAY_PHASES} and phase not in observed_phases:
            observed_phases.append(phase)
    intraday_observed = [phase for phase in observed_phases if phase in _INTRADAY_PHASES]
    phase_confirmation = {
        "status": "intraday_observed" if intraday_observed else "post_close_plan",
        "plan_type": "盘中确认" if intraday_observed else "盘后条件计划",
        "observed_phases": observed_phases,
        "pending_phases": [phase for phase in _INTRADAY_PHASES if phase not in observed_phases],
    }
    active_id = str(plan.get("active_scenario_id") or latest.get("top_scenario_id") or "")
    signal_row = next((
        item for item in latest.get("scenarios") or []
        if isinstance(item, dict) and active_id and str(item.get("scenario_id") or "") == active_id
    ), {})
    raw_signal = str(signal_row.get("state") or "unknown").lower()
    signal_status = {"supported": "met", "neutral": "not_triggered", "invalidated": "invalidated"}.get(raw_signal, "not_evaluable")
    if data_status != "ready" or (signal_status == "met" and signal_row.get("missing_fields")):
        signal_status = "not_evaluable"

    plan_permitted = bool(plan.get("execution_allowed") and mode == "decision"
                          and strategy_status == "applicable" and data_status == "ready")
    if data_status != "ready":
        action_status = "no_new_positions"
        reason_code = "data_expired" if expired else "data_unavailable"
        reason = "核心行情已过期，不能用旧快照判断当前机会。" if expired else "核心行情缺失或未通过校验，暂不能形成可靠的交易判断。"
    elif strategy_status == "unverified":
        action_status, reason_code = "no_new_positions", "qualification_incomplete"
        names = "、".join(item["label"] for item in issues[:3])
        reason = f"基础行情可用，但{names or '策略资格'}尚未通过；这是判断资格不足，不等于市场没有机会。"
    elif zero_position:
        action_status, reason_code = "no_new_positions", "market_no_trade"
        reason = "数据与资格已通过，但既有市场/仓位规则给出零仓位：判断后决定不买。"
    elif not candidates:
        action_status, reason_code = "no_new_positions", "no_candidates"
        reason = "现有筛选没有合格的新机会，风险锚不属于可执行标的。"
    elif signal_status == "invalidated":
        action_status, reason_code = "no_new_positions", "signal_invalidated"
        reason = "当前计划的信号已经失效，不能沿用原有条件。"
    elif not plan_permitted:
        action_status, reason_code = "no_new_positions", "existing_gate"
        reason = "既有执行门禁尚未允许该计划；本状态说明不会提高原有交易权限。"
    elif signal_status != "met":
        action_status, reason_code = "wait_confirmation", "signal_pending"
        reason = "条件计划已获准，但尚未取得完整触发确认；未确认不执行。"
    elif phase_confirmation["status"] == "post_close_plan":
        action_status, reason_code = "wait_confirmation", "intraday_confirmation_pending"
        pending = "、".join(_PHASE_LABELS[phase] for phase in phase_confirmation["pending_phases"])
        reason = f"当前是盘后条件计划，收盘判断不能当作盘中已触发；待{pending or '目标交易日时点'}确认。"
    else:
        action_status, reason_code = "enter_plan", "confirmed_plan"
        reason = "现有门禁、候选与目标时点的规则确认已通过；只进入条件计划，不代表已成交。"

    recheck = [item["recheck"] for item in issues]
    if reason_code == "market_no_trade":
        recheck.append("重新计算既有市场/仓位规则；只有规则允许非零仓位且候选合格时才重新评估。")
    elif reason_code == "no_candidates":
        recheck.append("按既有筛选规则重新获得合格候选；不使用风险锚或其他题材填满名单。")
    elif reason_code == "signal_invalidated":
        recheck.append("失效条件解除后重新生成并验证计划，不复用旧信号。")
    elif reason_code == "signal_pending":
        recheck.append("采集目标交易日与时点的竞价、9:35等快照，核对全部必要触发条件后重新评估。")
    elif reason_code == "existing_gate":
        recheck.append("核对原计划的执行门禁与当前情景，不用展示层越过阻断。")
    elif reason_code == "confirmed_plan":
        recheck.append("目标交易日仍需重新确认；数据过期、信号失效或仓位规则改变时撤销计划资格。")

    return {
        "schema_version": "decision-readiness/v1", "report_date": str(report_date or ""),
        "publication_mode": mode,
        "data": _axis("data", data_status, scope="核心行情"),
        "strategy": _axis("strategy", strategy_status, scope="新机会策略"),
        "signal": _axis("signal", signal_status, phase=str(latest.get("phase") or ""),
                        scenario_id=active_id, scope="报告快照时点"),
        "phase_confirmation": phase_confirmation,
        "action": _axis("action", action_status, reason_code=reason_code, reason=reason),
        "issues": issues, "recheck_conditions": list(dict.fromkeys(recheck)),
        "plan_permitted": plan_permitted,
        "execution_ready": bool(plan_permitted and action_status == "enter_plan"),
    }


def render_decision_readiness(readiness: dict | None) -> str:
    """Shared, shallow disclosure panel for homepage and both report surfaces."""
    payload = _dict(readiness)
    if not payload:
        return ""
    action = _dict(payload.get("action"))
    esc = lambda value: escape(str(value or ""), quote=True)
    titles = {"data": "数据资格", "strategy": "策略资格", "signal": "信号状态", "action": "操作结论"}
    cells = []
    for axis, title in titles.items():
        value = _dict(payload.get(axis))
        scope = value.get("scope") or ("门禁与信号分别判断" if axis == "action" else "")
        cells.append(
            f'<div data-axis="{axis}" data-status="{esc(value.get("status"))}" style="min-width:0;padding:10px 0">'
            f'<div style="font-size:11px;color:#8b949e">{title}</div>'
            f'<strong style="display:block;font-size:15px;line-height:1.5;color:#e6edf3">{esc(value.get("label"))}</strong>'
            f'<div style="font-size:10px;color:#8b949e">{esc(scope)}</div></div>'
        )
    conditions = list(payload.get("recheck_conditions") or [])
    recheck = "".join(f"<li>{esc(item)}</li>" for item in conditions[:3])
    overflow = ""
    if len(conditions) > 3:
        remaining = "".join(f"<li>{esc(item)}</li>" for item in conditions[3:])
        overflow = f'<details style="margin-top:6px"><summary>其余校验条件（{len(conditions) - 3}项）</summary><ul>{remaining}</ul></details>'
    phase = _dict(payload.get("signal")).get("phase")
    phase_label = _PHASE_LABELS.get(phase, phase or "时点未提供")
    confirmation = _dict(payload.get("phase_confirmation"))
    phase_status = str(confirmation.get("plan_type") or "时点状态未提供")
    pending_phases = "、".join(_PHASE_LABELS.get(item, item) for item in confirmation.get("pending_phases") or [])
    return (
        f'<section class="decision-readiness" data-action-status="{esc(action.get("status"))}" '
        'style="margin:14px 0;padding:16px 18px;background:#161b22;border-left:3px solid #d29922;border-radius:6px;color:#c9d1d9">'
        '<div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap">'
        f'<strong style="font-size:18px;color:#f0f6fc">操作结论 · {esc(action.get("label"))}</strong>'
        f'<span style="font-size:11px;color:#8b949e">快照 {esc(payload.get("report_date"))} · {esc(phase_label)} · {esc(phase_status)}</span></div>'
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin:6px 0">'
        + "".join(cells) + '</div>'
        f'<p class="readiness-reason" style="margin:8px 0;font-size:13px;line-height:1.65"><b>为什么：</b>{esc(action.get("reason"))}</p>'
        '<div style="font-size:12px;line-height:1.65"><b>何时重新评估：</b>'
        f'<ul style="margin:4px 0;padding-left:20px">{recheck}</ul>{overflow}</div>'
        + (f'<div style="font-size:11px;color:#d29922;margin-top:6px">待确认阶段：{esc(pending_phases)}</div>' if pending_phases else '')
        + '<div style="font-size:10px;color:#8b949e;margin-top:8px">核心行情通过 ≠ 全部策略数据齐全；快照信号 ≠ 下一交易日已触发。</div></section>'
    )
