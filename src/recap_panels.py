"""Compact evidence-first recap panels shared by both HTML report surfaces."""
from __future__ import annotations

from html import escape
from typing import Any

_PHASES = {"close": "收盘基准", "auction": "竞价", "early_0935": "9:35", "confirm_1000": "10:00", "afternoon": "午后"}
_DAILY = {
    "no_trade_data_unavailable": "行情不足，不能判断", "no_trade_strategy_unverified": "策略资格尚未验证",
    "no_trade_market_defensive": "市场规则防守，不开新仓", "no_trade_no_candidate": "没有合格候选",
    "wait_confirmation": "等待确认 / 重新评估", "conditional_plan": "进入条件计划（未代表成交）",
}
_PLAN = {"conditional": "条件计划", "awaiting_confirmation": "待确认", "confirmed": "规则已确认", "invalidated": "已失效", "suspended": "已暂停"}
_OUTCOME = {"pending": "待回填结果", "unknown": "未知", "not_triggered": "明确未触发", "triggered_not_filled": "触发未成交", "filled": "明确成交", "cancelled": "已取消"}
_PANEL = "margin:12px 0;padding:14px 16px;background:#161b22;border:1px solid #30363d;border-radius:7px;color:#c9d1d9;font-size:12px;line-height:1.65;min-width:0"
_MUTED = "color:#8b949e;font-size:11px"


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, (list, tuple)):
        return "、".join(_text(item) for item in value) or "无"
    return str(value)


def _esc(value: Any) -> str:
    return escape(_text(value), quote=True)


def render_decision_changes(changes: dict | None) -> str:
    changes = _dict(changes)
    if not changes:
        return ""
    if not changes.get("has_previous"):
        body = "首条决策记录；还没有可对照的上一条记录。"
    else:
        rows = []
        for item in changes.get("changes") or []:
            before, after = item.get("before"), item.get("after")
            if item.get("field") == "status":
                before, after = _DAILY.get(before, before), _DAILY.get(after, after)
            rows.append(f'<li><b>{_esc(item.get("label"))}</b>：{_esc(before)} → {_esc(after)}</li>')
        body = (f'对照 {_esc(changes.get("previous_report_date"))} 的上一条记录'
                + ('<ul style="margin:6px 0;padding-left:18px">' + ''.join(rows[:3]) + '</ul>' if rows else '：操作与候选未变。'))
        if len(rows) > 3:
            body += '<details><summary>其余变化</summary><ul>' + ''.join(rows[3:]) + '</ul></details>'
    return f'<section class="decision-changes" style="{_PANEL}"><b>相比上一条计划，改变了什么</b><div>{body}</div></section>'


def _rule_text(rule: dict) -> str:
    metric = str(rule.get("metric") or "待核验字段")
    label = {"breadth_ratio": "上涨占比", "promotion_rate": "晋级率", "limit_down": "跌停家数", "mainline_diffusion": "主线扩散度"}.get(metric, metric)
    operator = str(rule.get("operator") or "")
    symbol = {"gte": "≥", "gt": ">", "lte": "≤", "lt": "<", "eq": "="}.get(operator.replace("_baseline", ""), operator)
    value = "报告日同口径值" if operator.endswith("_baseline") else rule.get("value", "待核验")
    if isinstance(value, (int, float)) and metric in {"breadth_ratio", "promotion_rate", "mainline_diffusion"}:
        value = f"{value:.0%}"
    return f"{label} {symbol} {value}"


def render_scenario_checkpoint(ctx: dict, readiness: dict) -> str:
    posterior = _dict(ctx.get("scenario_posterior"))
    plans = [row for row in ctx.get("scenario_plans") or [] if isinstance(row, dict)]
    if not posterior and not plans:
        return ""
    timeline = [row for row in posterior.get("timeline") or [] if isinstance(row, dict)]
    latest = timeline[-1] if timeline else {}
    signal, confirmation = _dict(readiness.get("signal")), _dict(readiness.get("phase_confirmation"))
    names = {row.get("scenario_id"): row.get("title") or row.get("scenario_id") for row in plans}
    ranked = latest.get("top_ranked_scenario_id", latest.get("top_scenario_id"))
    active = signal.get("scenario_id") if signal.get("status") != "not_evaluable" else None
    decided = signal.get("decision_scenario_id") if signal.get("status") == "met" else None
    observed = set(confirmation.get("observed_phases") or [])
    rail = []
    for phase, label in _PHASES.items():
        state = "observed" if phase in observed else "pending"
        color = "#c9d1d9" if state == "observed" else "#d29922"
        status = "已记录" if state == "observed" else "待确认"
        rail.append(f'<li data-phase="{phase}" data-state="{state}" style="list-style:none;padding:6px 10px;border-bottom:2px solid {color};color:{color}">{label} · {status}</li>')
    title = "全部情景已失效，先重建计划" if signal.get("status") == "invalidated" else "逐阶段验证，不把排名当确认"
    next_steps = ""
    last_index = max((list(_PHASES).index(phase) for phase in observed), default=0)
    next_phase = next((p for p in list(_PHASES)[last_index + 1:] if p not in observed), None)
    selected = next((p for p in plans if p.get("scenario_id") == active), None) if active else None
    if selected is None:
        # A labelled recheck path is not the active plan; prefer a recovery
        # branch over a zero-position risk branch when no scenario is valid yet.
        selected = next((p for p in plans if "repair" in str(p.get("scenario_id")) or p.get("scenario_type") in {"分歧修复", "宽度修复"}),
                        next((p for p in plans if isinstance(p.get("position_ceiling"), (int, float)) and p["position_ceiling"] > 0),
                             next((p for p in plans if p.get("scenario_id") == ranked), plans[0] if plans else {})))
    if next_phase and selected and signal.get("status") != "invalidated":
        rules = [row for row in _dict(selected.get("trigger_rules")).get(next_phase) or [] if isinstance(row, dict) and row.get("required", True) is not False]
        invalid = [row for row in selected.get("invalidation_rules") or [] if isinstance(row, dict)]
        if rules:
            next_steps = f'<div style="{_MUTED}">待验证路径（不代表已生效）：{_esc(selected.get("title") or selected.get("scenario_id"))}</div><div><b>下一观察点 · {_PHASES[next_phase]}：</b>{_esc("；".join(_rule_text(rule) for rule in rules))}</div>'
        if invalid:
            next_steps += f'<div><b>该分支撤销条件：</b>{_esc("；".join(_rule_text(rule) for rule in invalid))}</div>'
    issues = confirmation.get("validation_issues") or posterior.get("rejected_snapshots") or []
    warning = f'<div style="color:#d29922">有 {len(issues)} 条阶段输入未通过校验；不能补作已确认。</div>' if issues else ""
    return (f'<section class="scenario-checkpoint" style="{_PANEL}"><b>{title}</b>'
            f'<div style="{_MUTED}">目标交易日 {_esc(confirmation.get("target_trade_date") or ctx.get("next_trade_date"))} · 缺阶段保持待确认</div>'
            f'<ol style="display:flex;flex-wrap:wrap;gap:7px;padding:0;margin:8px 0">{"".join(rail)}</ol>'
            f'<div>最高排名（非操作结论）：{_esc(names.get(ranked, ranked))}</div>'
            f'<div>有效情景：{_esc(names.get(active, active) or "尚未确定")}　·　规则确认：{_esc(names.get(decided, decided) or "尚未确认")}</div>'
            f'{next_steps}{warning}<div style="{_MUTED}">规则确认仅覆盖结构化字段；文字观察项、实时价格和T+1限制仍须执行前核对。</div></section>')


def render_limit_event_coverage(snapshot: dict | None) -> str:
    snapshot = _dict(snapshot)
    if not snapshot:
        return ""
    labels = {"first_limit_time": "首次封板时间", "last_limit_time": "最后封板时间", "break_count": "炸板次数", "limit_up_attempted": "封板尝试", "broken": "曾炸板", "reclosed": "回封", "board_type": "板型", "limit_up_fund": "封单资金", "turnover_rate": "换手率", "amount": "成交金额", "float_market_cap": "流通市值"}
    rows = []
    coverage = _dict(snapshot.get("field_coverage"))
    for key, label in labels.items():
        value = _dict(coverage.get(key))
        known, total = value.get("known", 0), value.get("total", snapshot.get("row_count", 0))
        state = "未提供" if not known else f"已提供 {known}/{total} 行"
        rows.append(f'<tr><td>{label}</td><td>{_esc(state)}</td><td>{_esc(value.get("missing", total))}</td></tr>')
    provenance = _dict(snapshot.get("provenance"))
    records = [row for row in snapshot.get("records") or [] if isinstance(row, dict)]
    sources = sorted({str(row["source"]) for row in records if row.get("source")})
    times = sorted({str(row["source_timestamp"]) for row in records if row.get("source_timestamp")})
    kinds = {row.get("source_timestamp_kind") for row in records}
    time_label = "抓取时间" if kinds == {"fetched_at"} or provenance.get("source_timestamp_kind") == "fetched_at" else "来源时间（按源字段口径）"
    source_label = "、".join(sources) or provenance.get("source") or "未提供"
    quality = _dict(snapshot.get("quality"))
    mismatch = quality.get("trade_date_mismatches") or 0
    note = f'；有 {mismatch} 行日期不符，不用于当前计划' if mismatch else ""
    return (f'<details class="limit-event-coverage" style="{_PANEL}"><summary style="cursor:pointer"><b>涨停事件数据覆盖</b> · 收盘涨停样本 {_esc(snapshot.get("row_count", 0))} 行 · 不是全市场炸板率</summary>'
            f'<div>交易日 {_esc(snapshot.get("trade_date"))} · 来源 {_esc(source_label)} · {time_label} {_esc(times[-1] if times else provenance.get("source_timestamp"))}</div>'
            f'<p style="{_MUTED}">0/False 表示明确观测；未提供保持未知。抓取时间不等于事件发生时间。字段覆盖分母仅为上述样本，不含所有盘中封板尝试{_esc(note) if note else ""}。</p>'
            f'<div style="overflow:auto"><table style="width:100%;font-size:12px;text-align:left"><thead><tr><th>字段</th><th>字段覆盖</th><th>缺失行数</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></details>')


def render_daily_journal(ctx: dict) -> str:
    review = _dict(ctx.get("daily_decision_review"))
    items = [row for row in review.get("items") or [] if isinstance(row, dict)]
    if not items and isinstance(ctx.get("daily_decision"), dict) and ctx["daily_decision"]:
        items = [ctx["daily_decision"]]
    if not items:
        return ""
    recent = items[-20:]
    rows = [f'<tr><td>{_esc(row.get("report_date"))}</td><td>{_esc(_DAILY.get(row.get("status"), row.get("status")))}</td><td>{_esc(row.get("version"))}</td><td>{_esc(row.get("reason"))}</td></tr>' for row in reversed(recent)]
    return (f'<section class="daily-decision-journal" style="{_PANEL}"><b>每日决策留痕 · 包括不交易日</b>'
            f'<div>已记录 {_esc(review.get("decision_count", len(items)))} 天 / {_esc(review.get("revision_count", len(items)))} 条修订；不把资格不足当作市场空仓。</div>'
            '<details><summary style="cursor:pointer">查看最近20个报告日的结论与原因</summary>'
            '<div style="overflow:auto;max-height:280px"><table style="width:100%;font-size:12px;text-align:left"><thead><tr><th>报告日</th><th>操作分类</th><th>版本</th><th>主要原因</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></details></section>')


def render_trade_history(ctx: dict) -> str:
    review = _dict(ctx.get("trade_plan_review"))
    if not review.get("plan_count") and not review.get("unassigned_outcome_count"):
        return ""
    counts = _dict(review.get("status_counts"))
    lines = [f'计划数 {_esc(review.get("plan_count"))}', f'计划修订 {_esc(review.get("plan_revision_count"))}',
             f'已记录结果 {_esc(review.get("outcome_count", 0))}', f'未触发 {_esc(counts.get("not_triggered", 0))}',
             f'触发未成交 {_esc(counts.get("triggered_not_filled", 0))}', f'已成交 {_esc(review.get("filled_count", 0))}',
             f'未知 {_esc(counts.get("unknown", 0))}', f'待回填 {_esc(review.get("pending_count", 0))}']
    groups = review.get("pnl_by_currency_and_basis") or []
    subtotal = '仅已知部分；完整收益未知' if review.get('pnl_aggregation_status') in {'unknown', 'ambiguous'} else '已明确且可比的结果'
    pnl = ''.join(f'<div>{subtotal} · 明确成交净收益 {_esc(group.get("net_pnl"))} {_esc(group.get("currency"))} · 口径 {_esc(group.get("pnl_basis"))}</div>' for group in groups)
    if not pnl:
        pnl = '<div>暂无明确成交收益结果；缺少来源、币种或口径时保持未知。</div>'
    rows = []
    for item in reversed((review.get("items") or [])[-20:]):
        plan = _dict(item.get("plan"))
        if "latest_outcome" in item:
            outcome = _dict(item.get("latest_outcome"))
        else:
            legacy = _dict(item.get("outcome"))
            outcome = legacy if plan.get("revision_id") and legacy.get("plan_revision_id") == plan["revision_id"] else {}
        status = outcome.get("status") or item.get("latest_outcome_status") or "unknown"
        historical = item.get("historical_fill_outcomes") or []
        revision_note = "（历史成交另列，不代表本修订成交）" if historical and status != "filled" else ""
        rows.append(f'<tr><td>{_esc(item.get("report_date"))}</td><td>{_esc(plan.get("name") or plan.get("code"))}</td><td>{_esc(_PLAN.get(plan.get("plan_status"), plan.get("plan_status")))}</td><td>{_esc(_OUTCOME.get(status, status))}{revision_note}</td><td>{len(historical)} 条修订有成交记录</td><td>{_esc(plan.get("plan_version"))}</td></tr>')
    unassigned = [row for row in review.get("unassigned_outcomes") or [] if isinstance(row, dict)]
    extra = ""
    if unassigned:
        entries = ''.join(f'<li>{_esc(row.get("plan_id"))} · {_esc(_OUTCOME.get(row.get("status"), row.get("status")))}</li>' for row in unassigned[-20:])
        extra = (f'<details><summary>未归属结果 {len(unassigned)} 条：计划日期/归属待核验，不计作新计划</summary>'
                 f'<ul>{entries}</ul><div style="{_MUTED}">保留原记录，不猜归属或收益；请用明确的计划修订ID确认。</div></details>')
    return (f'<section class="trade-plan-history" style="{_PANEL}"><b>交易计划复盘（非市场场景命中率）</b>'
            f'<div>{" · ".join(lines)}</div>{pnl}<div style="{_MUTED}">不同币种或口径不合计。信号不是成交；空结果不等于未触发。{_esc(review.get("note") or "")}</div>'
            '<details><summary style="cursor:pointer">查看历史计划、修订与结果（最近20条）</summary>'
            '<div style="overflow:auto;max-height:280px"><table style="width:100%;font-size:12px;text-align:left"><thead><tr><th>报告日</th><th>标的</th><th>计划状态</th><th>最新结果</th><th>历史成交</th><th>版本</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></details>{extra}</section>')


def render_strategy_qualification(quality: dict | None) -> str:
    from strategy_qualification import MIN_VALIDATION_SAMPLES, public_validation_summary

    scoped = _dict(_dict(quality).get("strategy_qualification"))
    if scoped.get("schema_version") != "strategy-qualification-set/v1":
        return ""
    labels = {"eligible": "条件许可", "unverified": "未验证", "missing_dependency": "缺少本策略数据",
              "not_applicable": "本次不适用", "research_only": "研究观察", "blocked_core": "核心行情阻断"}
    event_labels = {"bomb_rate": "炸板率", "reclose_rate": "炸板后回封率", "board_structure": "板型"}
    rows = []
    for value in _dict(scoped.get("strategies")).values():
        row = _dict(value)
        # Escaping a dict is not redaction: old or malformed summaries must
        # cross the same scalar allowlist as decision/replay serialization.
        validation = public_validation_summary(_dict(row.get("validation")))
        count = validation["sample_size"]
        verified = (validation.get("status") == "validated" and not validation.get("issues") and count is not None
                    and count >= MIN_VALIDATION_SAMPLES
                    and count == validation.get("declared_sample_size")
                    and all(validation.get(key) for key in ("source", "evidence_ref", "evaluated_at", "valid_from", "valid_until")))
        status = row.get("status") if isinstance(row.get("status"), str) else "unverified"
        status = status if status in labels else "unverified"
        if status == "eligible" and not verified:
            status = "unverified"
        conditions = row.get("recheck_conditions")
        reason = '；'.join(item for item in conditions if isinstance(item, str)) if isinstance(conditions, list) else ""
        reason = reason or ("数据与独立验证已通过，等待目标阶段触发。" if status == "eligible" else "资格元信息待核验。")
        deps = '、'.join(event_labels.get(key, "未知事件依赖") for key in _dict(row.get("event_dependencies"))) or "无全市场事件指标依赖"
        title = row.get("title") if isinstance(row.get("title"), str) else row.get("strategy_id")
        title = title if isinstance(title, str) else "策略待核验"
        samples = "样本数未知" if count is None else f"{count} 例"
        validation_status = "已验证" if verified else "未验证"
        rows.append(f'<tr><td>{_esc(title)}</td><td>{_esc(labels[status])}</td>'
                    f'<td>{_esc(samples)} · {_esc(validation_status)}</td><td>{_esc(deps)}</td><td>{_esc(reason)}</td></tr>')
    nonblocking = scoped.get("nonblocking_modules")
    nonblocking = nonblocking if isinstance(nonblocking, list) else []
    note = "AI文案不可用：使用确定性说明；不影响已独立验证策略的资格。" if "ai" in nonblocking else "AI文案是增强说明，不承担策略授权。"
    return (f'<section class="strategy-qualification" style="{_PANEL}"><b>逐策略资格 · 哪条路径能用，哪条还不能</b>'
            f'<div>{_esc(note)}</div><div style="{_MUTED}">历史同型样本数不等于独立验证；核心行情阻断仍对全部策略生效。</div>'
            '<details><summary>查看各策略依赖、独立验证与恢复条件</summary><div style="overflow:auto">'
            '<table style="width:100%;text-align:left;font-size:12px"><thead><tr><th>策略</th><th>状态</th><th>独立验证</th><th>事件依赖</th><th>何时重新评估</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></details></section>')
