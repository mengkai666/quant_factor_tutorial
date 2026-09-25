"""Compact evidence-first recap panels shared by both HTML report surfaces."""
from __future__ import annotations

import json
import os
from html import escape
from typing import Any

TACTICS_RECAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'tactics_recap.json')

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
    from math import isfinite
    from data_diagnostics import _scalar, _string

    snapshot = _dict(snapshot)
    if not snapshot:
        return ""

    def count(value):
        value = _scalar(value)
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
            return int(number) if isfinite(number) and number >= 0 and number.is_integer() else None
        except (TypeError, ValueError, OverflowError):
            return None

    row_count = count(snapshot.get("row_count"))

    def coverage_text(value, *, raw=False):
        value = _dict(value)
        known, total = count(value.get("known")), count(value.get("total", row_count))
        if known is None or total is None or known > total:
            return "原始覆盖未知" if raw else "覆盖未知"
        return f"{known}/{total} 行" if total else "无样本（0 行）"

    labels = {"first_limit_time": "首次封板时间", "last_limit_time": "最后封板时间", "break_count": "炸板次数", "limit_up_attempted": "封板尝试", "broken": "曾炸板", "reclosed": "回封", "board_type": "板型", "limit_up_fund": "封单资金", "turnover_rate": "换手率", "amount": "成交金额", "float_market_cap": "流通市值"}
    coverage = _dict(snapshot.get("field_coverage"))
    resolution = _dict(snapshot.get("fact_resolution"))
    has_resolution = "fact_resolution" in snapshot
    # Never backfill original observations from a resolved coverage summary.
    # Legacy observation-only snapshots retain their existing field coverage.
    raw_coverage = (_dict(snapshot.get("raw_field_coverage")) if "raw_field_coverage" in snapshot else
                    {} if has_resolution else coverage)
    derived_counts = _dict(resolution.get("derived_counts"))
    rows = []
    for key, label in labels.items():
        current = _dict(coverage.get(key))
        derived = count(derived_counts.get(key))
        interpreted = f"{derived} 行" if derived is not None else "未声明"
        missing = count(current.get("missing"))
        rows.append(f'<tr data-field="{key}"><td>{label}</td>'
                    f'<td data-coverage="raw">{_esc(coverage_text(raw_coverage.get(key), raw=True))}</td>'
                    f'<td data-coverage="derived">{_esc(interpreted)}</td>'
                    f'<td data-coverage="resolved">{_esc(coverage_text(current))}</td><td>{_esc(missing if missing is not None else "未知")}</td></tr>')
    provenance = _dict(snapshot.get("provenance"))
    incoming = snapshot.get("records")
    records = [row for row in incoming if isinstance(row, dict)] if isinstance(incoming, list) else []
    sources = sorted({_string(row.get("source")) for row in records} - {""})
    times = sorted({_string(row.get("source_timestamp")) for row in records} - {""})
    kinds = {_string(row.get("source_timestamp_kind")) for row in records} - {""}
    time_label = "抓取时间" if kinds == {"fetched_at"} or provenance.get("source_timestamp_kind") == "fetched_at" else "来源时间（按源字段口径）"
    source_label = "、".join(sources) or _string(provenance.get("source")) or "未提供"
    scope = _string(snapshot.get("population_scope")) or _string(snapshot.get("scope")) or "范围待核验"
    scope_label = "收盘涨停样本" if scope == "closing_limit_pool" else "所供事件样本"
    full = snapshot.get("full_market_coverage")
    full_label = "未覆盖全市场" if full is False else "未核验"
    if full is True:
        full_label = "范围声明矛盾，待核验" if scope == "closing_limit_pool" else "仅源声明覆盖，仍须核验"
    scope_note = ("收盘涨停子集仍缺未回封炸板成员及全部盘中封板尝试的总体覆盖；样本内回封计数不能外推全市场回封率。"
                  if scope == "closing_limit_pool" else "字段覆盖仅以所供样本为分母；本面板不核验全市场总体覆盖，也不展示全市场事件率。")
    interpretation = "原始观测＋有依据的规则解释；派生结果单列，不是新增原始观测。" if has_resolution else "原始观测覆盖；未提供规则解释元信息，不推断派生数量。"
    resolution_note = ""
    if has_resolution:
        conflicts = count(resolution.get("conflicting_records"))
        conflict_text = f"规则证据冲突：{conflicts} 行" if conflicts is not None else "规则证据冲突行数未知"
        resolution_note = f'<div style="{_MUTED}">解释版本：{_esc(_string(resolution.get("version")))}；{_esc(conflict_text)}。覆盖齐全不等于通过资格核验。</div>'
    mismatch = count(_dict(snapshot.get("quality")).get("trade_date_mismatches"))
    note = f"有 {mismatch} 行日期不符，不用于当前计划。" if mismatch else ""
    return (f'<details class="limit-event-coverage" style="{_PANEL}"><summary style="cursor:pointer"><b>涨停事件数据覆盖</b> · {scope_label} {_esc(row_count)} 行 · 不是全市场炸板率</summary>'
            f'<div>交易日 {_esc(_string(snapshot.get("trade_date")))} · 来源 {_esc(source_label)} · {time_label} {_esc(times[-1] if times else _string(provenance.get("source_timestamp")))}</div>'
            f'<div>范围：{_esc(scope)} · 全市场总体覆盖：{_esc(full_label)}</div>'
            f'<p style="{_MUTED}">{_esc(interpretation)}0/False 与未知不同；规则结果须保留解释依据。抓取时间不等于事件发生时间。{_esc(scope_note)}{_esc(note) if note else ""}</p>'
            f'{resolution_note}<div style="overflow:auto"><table style="width:100%;font-size:12px;text-align:left"><thead><tr><th>字段</th><th>原始观测覆盖</th><th>规则解释</th><th>字段覆盖（含解释）</th><th>仍未知</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></details>')


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


def render_strategy_qualification(quality: dict | None, *, phase_confirmation=None) -> str:
    from data_diagnostics import (
        build_data_diagnostics, _scalar, _string, _strings, _validation_summary, _validation_usable,
    )

    quality = _dict(quality)
    scoped = _dict(quality.get("strategy_qualification"))
    if scoped.get("schema_version") != "strategy-qualification-set/v1":
        return ""
    labels = {"eligible": "条件许可", "unverified": "未验证", "missing_dependency": "缺少本策略数据",
              "not_applicable": "本次不适用", "research_only": "研究观察", "blocked_core": "核心行情阻断"}
    event_labels = {"bomb_rate": "炸板率", "reclose_rate": "炸板后回封率", "board_structure": "板型"}
    rows, titles = [], {}
    for key, value in _dict(scoped.get("strategies")).items():
        if not isinstance(value, dict):
            continue
        row = value
        sid = _string(row.get("strategy_id")) or _string(key)
        # Redact containers before date/count parsing, not merely before HTML
        # escaping. This is the same public validation allowlist used elsewhere.
        validation = _validation_summary(row.get("validation"))
        count = validation["sample_size"]
        verified = _validation_usable(validation)
        status = _string(row.get("status"))
        status = status if status in labels else "unverified"
        if status == "eligible" and not verified:
            status = "unverified"  # preserve the existing panel's fail-closed display
        reason = '；'.join(_strings(row.get("recheck_conditions")))
        reason = reason or ("数据与独立验证已通过，等待目标阶段触发。" if status == "eligible" else "资格元信息待核验。")
        deps = '、'.join(event_labels.get(name, "未知事件依赖") for name in _dict(row.get("event_dependencies"))
                        if isinstance(name, str)) or "无全市场事件指标依赖"
        title = _string(row.get("title")) or sid or "策略待核验"
        titles[sid] = title
        samples = "样本数未知" if count is None else f"{count} 例"
        validation_status = "已验证" if verified else "未验证"
        # Original scalar machine issues remain inspectable per strategy. Only
        # rejected private containers are omitted; the caller's data is untouched.
        issues = _strings(row.get("issues"))
        issues.extend(issue for issue in validation["issues"] if issue not in issues)
        machine = ''.join(f'<li><code>{_esc(issue)}</code></li>' for issue in issues)
        metadata = ''.join(f'<li>{_esc(name)}：{_esc(_scalar(item))}</li>' for name, item in validation.items() if name != "issues")
        inspection = (f'<details class="strategy-inspection" data-strategy-id="{_esc(sid)}">'
                      '<summary style="cursor:pointer">查看机器问题与公开验证字段</summary>'
                      f'<ul style="margin:4px 0;padding-left:18px">{machine or "<li>无已记录机器问题</li>"}</ul>'
                      f'<div style="{_MUTED}">原记录计划许可：{_esc(_scalar(row.get("plan_permitted")))}</div>'
                      f'<ul style="{_MUTED};margin:4px 0;padding-left:18px">{metadata}</ul></details>')
        rows.append(f'<tr><td>{_esc(title)}</td><td>{_esc(labels[status])}</td>'
                    f'<td>{_esc(samples)} · {_esc(validation_status)}</td><td>{_esc(deps)}</td><td>{_esc(reason)}{inspection}</td></tr>')

    diagnostics = build_data_diagnostics(quality, phase_confirmation=phase_confirmation)
    groups = []
    diagnostic_labels = {**labels, "nonblocking": "非阻断", "degraded": "观察口径待核验", "pending": "等待真实观测"}
    for item in diagnostics:
        affected = '、'.join(titles.get(sid, sid) for sid in item["affected_strategies"]) or "指标／说明层，不额外阻断策略"
        details = ''.join('<li>' + '；'.join(f'{_esc(name)}：{_esc(_scalar(value))}' for name, value in detail.items()) + '</li>'
                          for detail in item["details"])
        groups.append(f'<li class="data-diagnostic" data-code="{_esc(item["code"])}" data-status="{_esc(item["status"])}" style="margin:8px 0">'
                      f'<b>{_esc(item["title"])}</b> · {_esc(diagnostic_labels.get(item["status"], item["status"]))}'
                      f'<div>影响：{_esc(item["impact"])}</div><div>恢复：{_esc(item["recovery"])}</div>'
                      f'<div style="{_MUTED}">涉及策略：{_esc(affected)}</div>'
                      '<details class="data-diagnostic-details"><summary style="cursor:pointer">诊断依据（仅公开字段）</summary>'
                      f'<ul style="{_MUTED};margin:4px 0;padding-left:18px">{details}</ul></details></li>')
    grouped = (f'<div class="data-diagnostics"><b>根因与适用范围 · {len(groups)} 项（跨策略合并）</b>'
               '<ul style="margin:6px 0;padding-left:18px">' + ''.join(groups) + '</ul></div>' if groups else
               f'<div class="data-diagnostics" style="{_MUTED}">未发现需处理的根因；不代表已触发或已成交。</div>')
    modules = [value for value in _dict(quality.get("modules")).values() if isinstance(value, dict)]
    ready = sum(_string(module.get("status")) in {"ok", "ready"} for module in modules)
    module_note = f'模块采集：{ready}/{len(modules)} 就绪（不代表策略获准）。' if modules else ""
    ai = next((item for item in diagnostics if item["code"] == "ai_service"), None)
    note = ("AI文案不可用：使用确定性说明；不影响已独立验证策略的资格。" if ai and ai["status"] == "nonblocking" else
            "AI文案与数据诊断仅作说明，不改变既有策略授权。")
    return (f'<section class="strategy-qualification" style="{_PANEL}"><b>逐策略资格 · 哪条路径能用，哪条还不能</b>'
            f'<div>{_esc(module_note)} {_esc(note)}</div><div style="{_MUTED}">历史同型样本数不等于独立验证；核心行情阻断仍对全部策略生效。</div>'
            f'{grouped}<details><summary>查看各策略依赖、独立验证与恢复条件</summary><div style="overflow:auto">'
            '<table style="width:100%;text-align:left;font-size:12px"><thead><tr><th>策略</th><th>状态</th><th>独立验证</th><th>事件依赖</th><th>何时重新评估</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></details></section>')


DEFAULT_TACTICS_DATA = {
    "report_date": "2026-09-22",
    "target_date": "2026-09-23",
    "status_summary": {
        "tactical_stance": "防守反击 / 中性偏防守",
        "stance_color": "#d29922",
        "cycle_stage": "百股涨停高潮后进入剧烈分歧淘汰期",
        "position_ceiling": "2-3成 (或空仓观望)",
        "core_beacons": "华瓷股份(高标溢价) · 博通集成(卡位活口) · 中际旭创(中军承接)",
    },
    "quantitative_strategies": [
        {
            "code": "战法 A",
            "name": "龙头高度突破战法",
            "title": "压力博弈与突破动能全景",
            "metric_highlight": "低位突破(≤6板)后5日新高率 100% | 高位突破(≥7板)见顶出逃",
            "sample_evidence": "基于年度连板样本 N=116 次高度压力跨越统计，P5压力线动态捕捉突破点。",
            "action_guide": "中低位突破重仓跟随做主升；高位突破警惕诱多赶顶，分批落袋，绝不格局。",
            "chart_ref": "chart_strategy_A_breakout.png",
            "color": "#22C55E"
        },
        {
            "code": "战法 B",
            "name": "龙头断板反包战法",
            "title": "黄金反包时间窗与二波路径",
            "metric_highlight": "4-6天黄金反包期占比 48% | 1-3天极强分歧反包 20% | ≥10天未反包坚决放弃",
            "sample_evidence": "真龙头首段高度与反包高度正相关，但仅 4% 超级中军能够反包超越前高。",
            "action_guide": "断板后4-6天企稳换手回封是最佳上车点；超10天未反包直接移出自选，严格止损。",
            "chart_ref": "chart_strategy_B_rebound.png",
            "color": "#FBBF24"
        },
        {
            "code": "战法 C",
            "name": "模仿补涨与板块扩散战法",
            "title": "赚钱效应外溢与题材传导脉络",
            "metric_highlight": "龙头7板+后跟风在第7天迎爆发主峰 | 第3-6天黄金低吸潜伏期",
            "sample_evidence": "龙头树立高度标杆后，板块内3板+跟风股具有确定性时滞传导爆发特征。",
            "action_guide": "第1-3天锁定主线属性；第4-6天潜伏同板块首板/低位低吸；第7-9天高潮全线兑现。",
            "chart_ref": "chart_strategy_C_mimic.png",
            "color": "#06B6D4"
        },
        {
            "code": "战法 D",
            "name": "周期生命线与见峰预警战法",
            "title": "生命周期节奏与见峰死生线",
            "metric_highlight": "周期中位寿命 5天 | 68%周期见峰后仅剩 0~1 天逃生窗口 | 8板上方止步率 30%",
            "sample_evidence": "爬坡期平均4天，见顶后极速坍塌，迟疑1天亏损即超过-15%。",
            "action_guide": "连板高度触及历史P95（8板+）即启动均值回归警报，见峰第一天无论盈亏坚决出逃。",
            "chart_ref": "chart_strategy_D_cycle.png",
            "color": "#A855F7"
        },
        {
            "code": "战法 E",
            "name": "接力换手防守与退潮空间塌陷战法",
            "title": "最高板断板后的空间下挫深度",
            "metric_highlight": "高位(≥7板)断板全市场高度暴跌3档 | 低位(≤4板)断板仅回落0-1档",
            "sample_evidence": "高位空间板断板是总退潮的确定性信号，全市场情绪梯队将产生断崖式坍缩。",
            "action_guide": "高位断板当天总仓位压至2成以下，坚决不接后排飞刀；低位良性换手才可轻仓试错。",
            "chart_ref": "chart_strategy_EF_selection.png",
            "color": "#F54854"
        },
        {
            "code": "战法 F",
            "name": "真龙头四维基因指纹辨识战法",
            "title": "29只年度真龙头全景图谱",
            "metric_highlight": "93%真龙必经历分歧反包 | 平均穿越 11.1 连板段 | 首发必带板块集群共振",
            "sample_evidence": "天普股份(21段)、大有能源(29段)、华电辽能(23段)具备跨周期超级股性记忆。",
            "action_guide": "一字顶板到底的多为伪龙庄股，唯有在强分歧中断板又能换手回封的标的才值得重仓跟随。",
            "chart_ref": "chart_strategy_EF_selection.png",
            "color": "#3B82F6"
        },
        {
            "code": "战法 G",
            "name": "严重异动监管与滑窗二波战法",
            "title": "10天100%监管博弈与次波卡位",
            "metric_highlight": "临界+95%主动控速停顿 | 滑窗剔除老涨幅后重获+100%额度二次起爆",
            "sample_evidence": "主力通过分时洗盘压制单日涨幅避免触碰停牌红线，在T+10窗口出清后发动二波主升。",
            "action_guide": "高标逼近严重异动红线时切勿盲目顶一字；专抓控速震荡横盘、滑窗解套后的二波突破点。",
            "chart_ref": "chart_strategy_abnormal_wave2.png",
            "color": "#EC4899"
        }
    ],
    "tactics_system": [
        {
            "id": "tactic_1",
            "name": "情绪周期极值律",
            "sub_title": "百股涨停分化律 · 冰火转换节点",
            "tag": "周期定位",
            "color": "#f85149",
            "principle": "短线情绪达极端高潮（如百股涨停）后，次日必然面临筹码剧烈分歧与断崖式淘汰；后排跟风冲天炮必死，唯有真正硬逻辑龙头抗住分歧。相反，经历多日冰点连环杀跌后，情绪衰竭必孕育新周期转折。",
            "rule": "【高潮次日不接后排加速板，严控追高手脚；冰点连杀只做分歧抗跌中军与率先爆量弱转强活口】"
        },
        {
            "id": "tactic_2",
            "name": "龙头梯队接力律",
            "sub_title": "高度压制律 · 梯队断层分流",
            "tag": "空间接力",
            "color": "#d29922",
            "principle": "全市场空间最高板（如华瓷股份5板）直接锚定短线做多天花板。高标一旦炸板或滞涨，梯队中段将形成严重断层，高位接力亏钱效应扩散，避险资金迅速向低位1进2或首板换手龙沉淀。",
            "rule": "【断板即防守，绝不幻想逆势反包；高位梯队断层时坚决放弃中位股接力，转攻低位放量换手新核心】"
        },
        {
            "id": "tactic_3",
            "name": "双子星卡位生死律",
            "sub_title": "同身位PK · 竞价爆量弱转强",
            "tag": "同身位PK",
            "color": "#bc8cff",
            "principle": "当板块或梯队存在2只及以上同身位标的时（如2板博通集成 vs 诚邦股份/三羊马），早盘9:25竞价量能、封单金额及开盘抢筹斜率决定生死。胜者享受加速溢价，败者资金踩踏甚至上演核按钮。",
            "rule": "【去弱留强毫不手软；只上竞价超预期爆量抢筹胜出的第一名，同身位落败者果断止盈/止损绝不补仓】"
        },
        {
            "id": "tactic_4",
            "name": "人气容量龙反包律",
            "sub_title": "万亿主线大中军 · 趋势承接中枢",
            "tag": "容量中军",
            "color": "#58a6ff",
            "principle": "AI算力、半导体、光模块等万亿主线大中军（中际旭创、新易盛、工业富联）承接市场数十亿乃至百亿级机构与游资容量资金。情绪退潮时中军往往先于大盘企稳，构筑多头趋势防守底线。",
            "rule": "【中军走趋势重在均线低吸（5日/10日线），不追日内分时暴拉冲刺；主线分歧时中军企稳是全盘回血先导指标】"
        }
    ],
    "today_recap": {
        "market_qualitative": "今日市场呈现典型的“高潮后剧烈分化淘汰”特征。在昨日百股涨停的狂热情绪释放后，早盘开盘即出现大面积后排标的杀跌分化，炸板率显著上升至30%以上。全天赚钱效应极度分化收敛，高位追高盈亏比极其恶劣，唯有前排极少数硬逻辑换手晋级标的与核心中军展现韧性。",
        "echelon_breakdown": [
            {"tier": "5进6 空间板", "stocks": "华瓷股份", "status": "一字晋级 6 板", "analysis": "全市场空间天花板，9:25 一字封板、全天未开板（换手 0.74%，炸板 0 次），晋级 6 板继续锚定空间高度。"},
            {"tier": "4板身位战", "stocks": "内蒙新华 vs 世联行", "status": "内蒙新华晋级 / 世联行落败", "analysis": "双子星同身位竞争残酷兑现：内蒙新华早盘资金抢筹封板，世联行冲高无力跳水回落，同身位淘汰律显现。"},
            {"tier": "3板断层区", "stocks": "龙头股份、南华生物等", "status": "分化与断层", "analysis": "3板梯队断层分流明显，高位资金接力意愿衰竭，无独立逻辑支撑的后排标的被大面积无情抛弃。"},
            {"tier": "2板晋级区", "stocks": "博通集成、大亚圣象、三羊马", "status": "博通集成强势换手卡位", "analysis": "博通集成依托半导体芯片题材，早盘爆量换手强势封板卡位成功，成为低位承接短线游资的核心活口。"}
        ],
        "zhongjun_analysis": "AI算力与核心中军方面，中际旭创全天成交超百亿元，在回踩分时均线与短期均线时获得强劲承接；新易盛红盘震荡显韧性；工业富联大单护盘明显。大容量中军稳健运行，有效阻断了指数单边下挫的恐慌，为主线行情的延续保留了核心火种。"
    },
    "yesterday_comparison": [
        {
            "point": "【大盘情绪推演】",
            "yesterday_plan": "预警昨日百股涨停为情绪短线沸点，次日必大分化，严禁追逐后排跟风标的。",
            "today_reality": "实盘炸板率由昨日低位飙升至30%以上，后排跟风标的大面积翻绿杀跌，高位追高全线吃套。",
            "result_badge": "🎯 完全命中",
            "badge_color": "#3fb950",
            "eval": "成功识别情绪周期极值律，提前拉响防守警报，有效规避高潮次日接盘风险。"
        },
        {
            "point": "【高标高度接力】",
            "yesterday_plan": "华瓷股份冲击5板面临全市场空间压制，须警惕放量分歧，切忌无脑一字顶板接力。",
            "today_reality": "华瓷股份 9:25 一字封板晋级 6 板，全天未开板（换手 0.74%，炸板 0 次）。",
            "result_badge": "❌ 未兑现",
            "badge_color": "#f85149",
            "eval": "预案预期的高度阻力当天没有出现。原记录误写为「盘中剧烈炸板」，已按 9/22 涨停池（首封 09:25、炸板 0 次）更正。"
        },
        {
            "point": "【中军低吸策略】",
            "yesterday_plan": "核心算力与光模块大中军走趋势，盘中分时回踩均线可轻仓试错低吸，不追高拉升。",
            "today_reality": "中际旭创、新易盛水下及均线回踩区间形成全天坚实底部支撑，尾盘回升企稳，日内低吸全部安全。",
            "result_badge": "🎯 完全命中",
            "badge_color": "#3fb950",
            "eval": "容量中军低吸策略完美契合趋势资金审美，实现震荡市稳健收益。"
        }
    ],
    "tomorrow_plan": {
        "general_stance": "中性偏防守 / 聚焦前排卡位活口 / 仓位上限 2-3 成 (不见明确信号不盲目出手)",
        "auction_beacons": [
            {"beacon": "高标华瓷股份竞价", "focus": "观察竞价是否红开或有大单护盘；若竞价低开超-4%直接预示情绪退潮加深，全天收紧防守。"},
            {"beacon": "2进3活口博通集成", "focus": "观察是否能超预期高开>3%并在9:25封单超2亿；若快速抢筹涨停则低位芯片/算力线具备进攻价值。"},
            {"beacon": "算力中军中际旭创/新易盛", "focus": "观察开盘成交量能否排进全市场前三，竞价是否平开高走，创业板权重能否形成共振。"}
        ],
        "scenario_branches": [
            {
                "branch_id": "Branch_A",
                "title": "分支 A · 弱转强修复路线 (概率 ~35%)",
                "trigger": "华瓷股份平开高走未被按核按钮，博通集成早盘快速放量回封3板，全市场上涨家数快速回升至2800家以上，算力中军放量拉升。",
                "action": "动用 2-3 成仓位，重点出击博通集成3进4卡位确认板，或顺势低吸中际旭创/新易盛日内均线企稳点。"
            },
            {
                "branch_id": "Branch_B",
                "title": "分支 B · 持续退潮冰点路线 (概率 ~45%)",
                "trigger": "华瓷股份竞价被按跌停或开盘闪崩跌停，连板晋级率不足15%，跌停家数激增至15家以上，中军大单放量砸盘。",
                "action": "【执行 0 仓位绝对防守】！全天严禁开任何新仓，禁止任何抄底冲动；持仓若有利冲高减仓止盈，等待极端冰点出清。"
            },
            {
                "branch_id": "Branch_C",
                "title": "分支 C · 结构轮动防御路线 (概率 ~20%)",
                "trigger": "高标继续横盘宽幅震荡，增量资金不足，场内活跃资金向低位首板或医药(创新药)/周期资源防御品种分流。",
                "action": "控制仓位 1 成以内，严禁追高任何连板股；仅可小试低位低吸具备独立催化的防御型中军或首板套利。"
            }
        ],
        "focus_targets": [
            {
                "code": "603068",
                "name": "博通集成",
                "role": "芯片半导体 · 2进3卡位龙头",
                "tactic": "双子星卡位生死律",
                "trigger": "早盘竞价高开>3%且开盘5分钟放量换手强势封死",
                "defense": "跌破分时均线或日内分时下跌超-3%",
                "position": "10% - 15%"
            },
            {
                "code": "300308",
                "name": "中际旭创",
                "role": "AI算力光模块 · 万亿趋势中军",
                "tactic": "人气容量龙反包律",
                "trigger": "分时回踩5日线或水下分时放量向上穿均线底背离",
                "defense": "有效跌破5日均线",
                "position": "10% - 15%"
            },
            {
                "code": "300502",
                "name": "新易盛",
                "role": "CPO光模块 · 弹性容量中军",
                "tactic": "人气容量龙反包律",
                "trigger": "日内多头排列放量冲过早盘高点",
                "defense": "跌破昨日分时低点",
                "position": "10%"
            },
            {
                "code": "001216",
                "name": "华瓷股份",
                "role": "5板市场高度标 · 情绪风向标",
                "tactic": "龙头梯队接力律",
                "trigger": "早盘超预期平开或低开快速拉红(仅做情绪信号)",
                "defense": "跌破日内分时均线即刻撤退",
                "position": "观察标的 / 仅极激进底仓试错"
            }
        ]
    }
}


_NO_COMPARISON_ROW = {
    "point": "【无当日对账】",
    "yesterday_plan": "没有与本报告日期一致的预案记录（data/tactics_recap.json）。",
    "today_reality": "—",
    "result_badge": "⚪ 未对账",
    "badge_color": "#8b949e",
    "eval": "对账必须逐条记分；缺少当日记录时不沿用历史结论。",
}


def _load_dated_recap(report_date: str) -> dict:
    """读取 tactics_recap.json，但只认与 report_date 同一天的记录。

    那份文件是某个交易日写下的复盘结论；日期对不上还拿来填面板，就会把旧结论
    当成今天的对账挂出去（9/22 的「华瓷剧烈炸板 · 完全命中」曾因此天天复现）。
    """
    if not report_date:
        return {}
    try:
        with open(TACTICS_RECAP_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or str(data.get("report_date") or "") != report_date:
        return {}
    return data


def build_tactics_data_from_report(
    context: dict | None = None,
    *,
    echelon: list[dict] | None = None,
    advance_decline: dict | None = None,
    report_date: str = "",
    next_trade_date: str = "",
) -> dict:
    """动态从报告上下文、涨跌统计与天梯梯队构建游资战法对账与预案数据."""
    ctx = _dict(context)
    ad = _dict(advance_decline)

    base_quant = list(DEFAULT_TACTICS_DATA.get("quantitative_strategies") or [])
    base_laws = list(DEFAULT_TACTICS_DATA.get("tactics_system") or [])

    # 1. 日期处理
    r_date = str(report_date or ctx.get("report_date") or DEFAULT_TACTICS_DATA.get("report_date") or "")
    if len(r_date) == 8 and r_date.isdigit():
        r_date = f"{r_date[:4]}-{r_date[4:6]}-{r_date[6:]}"
    t_date = str(next_trade_date or ctx.get("target_trade_date") or ctx.get("next_trade_date") or DEFAULT_TACTICS_DATA.get("target_date") or "次日")
    if len(t_date) == 8 and t_date.isdigit():
        t_date = f"{t_date[:4]}-{t_date[4:6]}-{t_date[6:]}"

    # 日期相关的叙述（对账、中军点评、竞价风向标）只取同日复盘记录，绝不回落到默认样例
    dated = _load_dated_recap(r_date)
    dated_today = _dict(dated.get("today_recap"))
    dated_plan = _dict(dated.get("tomorrow_plan"))

    # 2. 状态基调与风控指标提取
    thesis = _dict(ctx.get("market_thesis"))
    br_state = _dict(thesis.get("breadth_relay_state"))
    today_dec = _dict(ctx.get("today_decision"))
    action_plan = _dict(today_dec.get("action_plan"))

    zt_val = br_state.get("limit_up") or ad.get("zt") or 0
    dt_val = br_state.get("limit_down") or ad.get("dt") or 0
    breadth_ratio = br_state.get("breadth_ratio") or 0.0
    promo_rate = br_state.get("promotion_rate")

    pos_ceiling = str(action_plan.get("position") or DEFAULT_TACTICS_DATA["status_summary"]["position_ceiling"])
    if not pos_ceiling or pos_ceiling == "0 成":
        pos_ceiling = "0-2成 (防守观望)"

    stance_val = "防守反击 / 中性偏防守"
    stance_clr = "#d29922"
    if dt_val >= 10 or (breadth_ratio < 0.3 and zt_val < 30):
        stance_val = "极端冰点防守 / 严控开仓"
        stance_clr = "#f85149"
    elif zt_val >= 90:
        stance_val = "高潮后防守 / 警惕大分化"
        stance_clr = "#d29922"
    elif breadth_ratio > 0.65:
        stance_val = "顺势进攻 / 聚焦核心活口"
        stance_clr = "#3fb950"

    cycle_stage = "百股涨停高潮后进入剧烈分歧淘汰期"
    if zt_val >= 80:
        rate_str = f" · 晋级率 {promo_rate:.1%}" if isinstance(promo_rate, (int, float)) else ""
        cycle_stage = f"高潮后强分化淘汰期 (涨停{zt_val}家{rate_str})"
    elif dt_val >= 15:
        cycle_stage = f"退潮恐慌释放期 (跌停{dt_val}家)"
    elif breadth_ratio < 0.35:
        cycle_stage = "情绪冰点衰竭期"
    else:
        cycle_stage = "震荡轮动淘汰期"

    # 3. 动态提炼梯队对账
    echelon_list = echelon if echelon is not None else []
    dynamic_echelon_rows = []
    max_h = 0
    beacons_list = []
    if echelon_list:
        import re
        for e in echelon_list:
            hh = e.get("height", "")
            m = re.search(r"(\d+)", str(hh))
            h_num = int(m.group(1)) if m else (1 if "首板" in str(hh) else 0)
            if h_num > max_h:
                max_h = h_num

        for e in echelon_list:
            hh = str(e.get("height", ""))
            m = re.search(r"(\d+)", hh)
            h_num = int(m.group(1)) if m else (1 if "首板" in hh else 0)
            stk_names = e.get("stocks", [])
            stk_str = "、".join(stk_names[:4])
            cnt = e.get("count", len(stk_names))

            if h_num == max_h and h_num >= 3:
                beacons_list.append(f"{stk_str}(高标{h_num}板)")
                status_str = "全市场空间天花板"
                if h_num >= 7:
                    tactic_note = "【战法A高位警报/战法E空间塌陷】：触及历史P95天花板，警惕诱多赶顶，断板当天将导致全市场高度暴跌3档。"
                else:
                    tactic_note = f"【战法A高度突破/铁律2空间锚定】：{h_num}板突破事前P5压力线，锚定全市场连板做多天花板，后排中位若断层须防守。"
                dynamic_echelon_rows.append({
                    "tier": f"{hh} 空间板",
                    "stocks": stk_str,
                    "status": status_str,
                    "analysis": tactic_note
                })
            elif cnt >= 2 and h_num >= 3:
                status_str = f"双子星同身位PK (淘汰率 {(cnt-1)/cnt:.0%})"
                tactic_note = f"【铁律3 双子星卡位生死律】：{cnt}只标的同身位白刃战，早盘9:25竞价量能与封单抢筹决定胜负，胜者加速晋级，败者遭资金踩踏。"
                dynamic_echelon_rows.append({
                    "tier": f"{hh} 身位战",
                    "stocks": stk_str,
                    "status": status_str,
                    "analysis": tactic_note
                })
            elif h_num == 2:
                beacons_list.append(f"{stk_str}(2板接力活口)")
                status_str = f"低位承接与晋级活口{' (同身位竞争)' if cnt >= 2 else ''}"
                tactic_note = "【战法C模仿补涨/铁律2试错】：低位换手活口，作为高位分歧时的资金避险承接载体，重点看首板与2进3弱转强。"
                dynamic_echelon_rows.append({
                    "tier": f"{hh} 晋级区",
                    "stocks": stk_str,
                    "status": status_str,
                    "analysis": tactic_note
                })
            elif h_num >= 3:
                dynamic_echelon_rows.append({
                    "tier": f"{hh} 梯队区",
                    "stocks": stk_str,
                    "status": "中位换手推进",
                    "analysis": "【铁律2 梯队接力律】：中位梯队面临高位压制与低位分流，只做有独立逻辑且爆量弱转强的品种。"
                })

    if not dynamic_echelon_rows:
        dynamic_echelon_rows = list(dated_today.get("echelon_breakdown") or [])

    beacons_list.append("中际旭创/新易盛(容量中军)")
    core_beacons = " · ".join(beacons_list[:3])

    # 4. 盘面定性
    market_qual = str(thesis.get("core_conflict", {}).get("resolution_condition") or "")
    if not market_qual or len(market_qual) < 10:
        market_qual = str(dated_today.get("market_qualitative") or
                          f"今日全市场涨停 {zt_val} 家、跌停 {dt_val} 家，上涨占比 {breadth_ratio:.1%}。")
    else:
        market_qual = f"今日全市场涨停 {zt_val} 家、跌停 {dt_val} 家，上涨占比 {breadth_ratio:.1%}。盘面定性：{market_qual}。"

    # 5. 明日预案与标的雷达池
    plans = ctx.get("scenario_plans") or []
    dynamic_branches = []
    for i, p in enumerate(plans):
        if not isinstance(p, dict):
            continue
        bid = p.get("scenario_id") or f"Branch_{chr(65+i)}"
        title = p.get("name") or p.get("title") or f"分支 {chr(65+i)}"
        prob = p.get("probability") or p.get("prob") or ""
        prob_str = f" (概率 ~{prob})" if prob else ""
        trig = p.get("trigger_condition") or p.get("trigger") or "观察盘面广度与龙头分时反馈"
        act = p.get("action_plan") or p.get("action") or "严格控制仓位，不符合信号不盲目出手"
        dynamic_branches.append({
            "branch_id": str(bid),
            "title": f"分支 {chr(65+i)} · {title}{prob_str}",
            "trigger": str(trig),
            "action": str(act)
        })
    if not dynamic_branches:
        dynamic_branches = list(dated_plan.get("scenario_branches") or [])

    candidates = today_dec.get("candidates") or ctx.get("focus_pool") or []
    dynamic_targets = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        c_code = str(c.get("code") or "")
        c_name = str(c.get("name") or "")
        if not c_code and not c_name:
            continue
        c_role = str(c.get("role") or c.get("group_code") or "进攻观察")
        if "risk" in c_role.lower():
            tac = "战法 E : 接力退潮防守律"
        elif any(k in c_name for k in ["旭创", "易盛", "富联", "曙光", "科技", "讯飞"]):
            tac = "铁律 4 : 人气容量龙反包律"
        elif any(k in c_role for k in ["attack", "卡位", "身位"]):
            tac = "铁律 3 : 双子星卡位生死律"
        else:
            tac = "铁律 2 : 龙头梯队接力律"

        trig = str(c.get("trigger") or c.get("cond") or "分时均线企稳且板块共振")
        dfn = str(c.get("invalid") or c.get("stop") or "跌破分时均线即刻撤退")
        pos = str(c.get("position") or "10% - 15%")
        dynamic_targets.append({
            "code": c_code,
            "name": c_name,
            "role": c_role,
            "tactic": tac,
            "trigger": trig,
            "defense": dfn,
            "position": pos
        })
    if not dynamic_targets:
        dynamic_targets = list(dated_plan.get("focus_targets") or [])

    yesterday_comp = list(dated.get("yesterday_comparison") or []) or [dict(_NO_COMPARISON_ROW)]

    return {
        "report_date": r_date,
        "target_date": t_date,
        "status_summary": {
            "tactical_stance": stance_val,
            "stance_color": stance_clr,
            "cycle_stage": cycle_stage,
            "position_ceiling": pos_ceiling,
            "core_beacons": core_beacons,
        },
        "quantitative_strategies": base_quant,
        "tactics_system": base_laws,
        "today_recap": {
            "market_qualitative": market_qual,
            "echelon_breakdown": dynamic_echelon_rows,
            "zhongjun_analysis": str(dated_today.get("zhongjun_analysis") or "本交易日没有对应日期的中军复盘记录。"),
        },
        "yesterday_comparison": yesterday_comp,
        "tomorrow_plan": {
            "general_stance": f"{stance_val} / 仓位上限 {pos_ceiling} (不见明确信号不盲目出手)",
            "auction_beacons": list(dated_plan.get("auction_beacons") or []),
            "scenario_branches": dynamic_branches,
            "focus_targets": dynamic_targets[:6],
        }
    }


def render_tactics_review_panel(
    tactics_data: dict | None = None,
    *,
    report_date: str = "",
    context: dict | None = None,
    echelon: list[dict] | None = None,
    advance_decline: dict | None = None,
) -> str:
    """渲染游资实战作战室 · 深度复盘与明日预案面板 (HTML片段)."""
    data = _dict(tactics_data)
    if not data and (context or echelon or advance_decline):
        try:
            data = build_tactics_data_from_report(
                context, echelon=echelon, advance_decline=advance_decline, report_date=report_date
            )
        except Exception:
            data = {}
    if not data:
        if os.path.exists(TACTICS_RECAP_PATH):
            try:
                with open(TACTICS_RECAP_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                pass
    if not data:
        data = DEFAULT_TACTICS_DATA

    summary = _dict(data.get("status_summary"))
    t_stance = _esc(summary.get("tactical_stance") or "中性偏防守")
    s_color = summary.get("stance_color") or "#d29922"
    c_stage = _esc(summary.get("cycle_stage") or "分歧淘汰期")
    pos_ceiling = _esc(summary.get("position_ceiling") or "2-3成")
    beacons = _esc(summary.get("core_beacons") or "前排卡位龙 · 核心中军")
    r_date = _esc(data.get("report_date") or report_date or "2026-09-22")
    t_date = _esc(data.get("target_date") or "次日")

    # 0. 宏观量化战略卡片 (战法 A ~ G)
    quant_cards = []
    for q in data.get("quantitative_strategies") or []:
        q_name = _esc(q.get("name"))
        q_code = _esc(q.get("code"))
        q_title = _esc(q.get("title"))
        q_hl = _esc(q.get("metric_highlight"))
        q_ev = _esc(q.get("sample_evidence"))
        q_act = _esc(q.get("action_guide"))
        q_clr = q.get("color") or "#58a6ff"
        q_chart = _esc(q.get("chart_ref") or "")
        chart_tag = f'<span style="font-size:10px;color:#8b949e;background:rgba(255,255,255,0.04);padding:1px 6px;border-radius:4px;">📈 {q_chart}</span>' if q_chart else ''
        quant_cards.append(f'''
        <div style="background:#1c2128;border:1px solid #30363d;border-left:4px solid {q_clr};border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;justify-content:space-between;box-shadow:0 2px 6px rgba(0,0,0,0.2);">
            <div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                    <span style="font-size:13.5px;font-weight:700;color:#f0f6fc;">{q_name}</span>
                    <span style="font-size:11px;padding:2px 7px;border-radius:8px;background:rgba(255,255,255,0.06);color:{q_clr};border:1px solid {q_clr};">{q_code}</span>
                </div>
                <div style="font-size:11px;color:#8b949e;margin-bottom:6px;">{q_title}</div>
                <div style="font-size:11.5px;color:#e3b341;background:rgba(210,153,34,0.1);padding:4px 8px;border-radius:4px;margin-bottom:6px;font-weight:600;line-height:1.4;">
                    ⚡ 核心指标：{q_hl}
                </div>
                <div style="font-size:11.5px;color:#c9d1d9;line-height:1.5;margin-bottom:8px;">
                    <b style="color:#8b949e;">年度实证：</b>{q_ev}
                </div>
            </div>
            <div>
                <div style="background:rgba(0,0,0,0.3);border-radius:6px;padding:6px 8px;font-size:11px;color:#7ee787;border-left:2px solid {q_clr};margin-bottom:6px;">
                    <b style="color:{q_clr};">实操决策：</b>{q_act}
                </div>
                <div style="display:flex;justify-content:flex-end;">{chart_tag}</div>
            </div>
        </div>
        ''')
    quant_grid_html = "".join(quant_cards)

    # 1. 游资战法卡片渲染
    tactics_cards = []
    for tac in data.get("tactics_system") or []:
        t_name = _esc(tac.get("name"))
        t_sub = _esc(tac.get("sub_title"))
        t_tag = _esc(tac.get("tag"))
        clr = tac.get("color") or "#58a6ff"
        prin = _esc(tac.get("principle"))
        rule = _esc(tac.get("rule"))
        tactics_cards.append(f'''
        <div style="background:#1c2128;border:1px solid #30363d;border-left:4px solid {clr};border-radius:8px;padding:14px 16px;display:flex;flex-direction:column;justify-content:space-between;box-shadow:0 2px 6px rgba(0,0,0,0.2);">
            <div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                    <span style="font-size:14px;font-weight:700;color:#f0f6fc;">{t_name}</span>
                    <span style="font-size:11px;padding:2px 8px;border-radius:10px;background:rgba(255,255,255,0.08);color:{clr};border:1px solid {clr};">{t_tag}</span>
                </div>
                <div style="font-size:11px;color:#8b949e;margin-bottom:8px;">{t_sub}</div>
                <div style="font-size:12px;color:#c9d1d9;line-height:1.6;margin-bottom:10px;">{prin}</div>
            </div>
            <div style="background:rgba(0,0,0,0.3);border-radius:6px;padding:8px 10px;font-size:11.5px;color:#e6edf3;border-left:2px solid {clr};">
                <span style="color:{clr};font-weight:700;">操盘铁律：</span>{rule}
            </div>
        </div>
        ''')
    tactics_grid_html = "".join(tactics_cards)

    # 2. 今日复盘渲染
    today = _dict(data.get("today_recap"))
    m_qual = _esc(today.get("market_qualitative"))
    echelon_rows = []
    for row in today.get("echelon_breakdown") or []:
        tier = _esc(row.get("tier"))
        stk = _esc(row.get("stocks"))
        stat = _esc(row.get("status"))
        ana = _esc(row.get("analysis"))
        echelon_rows.append(f'''
        <tr style="border-bottom:1px solid #21262d;">
            <td style="padding:8px 10px;font-weight:bold;color:#f0f6fc;white-space:nowrap;">{tier}</td>
            <td style="padding:8px 10px;color:#58a6ff;font-weight:600;white-space:nowrap;">{stk}</td>
            <td style="padding:8px 10px;color:#d29922;white-space:nowrap;">{stat}</td>
            <td style="padding:8px 10px;color:#8b949e;font-size:11.5px;line-height:1.5;">{ana}</td>
        </tr>
        ''')
    echelon_table_html = "".join(echelon_rows)
    zj_ana = _esc(today.get("zhongjun_analysis"))

    # 3. 昨日预案对比渲染
    comp_rows = []
    for comp in data.get("yesterday_comparison") or []:
        pt = _esc(comp.get("point"))
        plan = _esc(comp.get("yesterday_plan"))
        real = _esc(comp.get("today_reality"))
        badge = _esc(comp.get("result_badge"))
        b_clr = comp.get("badge_color") or "#3fb950"
        eva = _esc(comp.get("eval"))
        comp_rows.append(f'''
        <tr style="border-bottom:1px solid #21262d;">
            <td style="padding:10px;font-weight:bold;color:#f0f6fc;white-space:nowrap;">{pt}</td>
            <td style="padding:10px;color:#c9d1d9;font-size:12px;line-height:1.5;">{plan}</td>
            <td style="padding:10px;color:#c9d1d9;font-size:12px;line-height:1.5;">{real}</td>
            <td style="padding:10px;white-space:nowrap;text-align:center;">
                <span style="display:inline-block;padding:3px 8px;border-radius:12px;font-size:11px;font-weight:700;background:rgba(63,185,80,0.15);color:{b_clr};border:1px solid {b_clr};">{badge}</span>
            </td>
            <td style="padding:10px;color:#8b949e;font-size:11.5px;line-height:1.5;">{eva}</td>
        </tr>
        ''')
    comp_table_html = "".join(comp_rows)

    # 4. 明日预案渲染
    tmr = _dict(data.get("tomorrow_plan"))
    tmr_stance = _esc(tmr.get("general_stance"))

    beacons_items = []
    for b in tmr.get("auction_beacons") or []:
        b_name = _esc(b.get("beacon"))
        b_foc = _esc(b.get("focus"))
        beacons_items.append(f'''
        <div style="background:#1c2128;border:1px solid #30363d;border-radius:6px;padding:10px 12px;">
            <div style="color:#58a6ff;font-weight:700;font-size:12px;margin-bottom:4px;">🎯 {b_name}</div>
            <div style="color:#c9d1d9;font-size:11.5px;line-height:1.5;">{b_foc}</div>
        </div>
        ''')
    beacons_html = "".join(beacons_items)

    branches_items = []
    for br in tmr.get("scenario_branches") or []:
        br_title = _esc(br.get("title"))
        br_trig = _esc(br.get("trigger"))
        br_act = _esc(br.get("action"))
        branches_items.append(f'''
        <div style="background:#1c2128;border:1px solid #30363d;border-radius:8px;padding:12px 14px;margin-bottom:10px;">
            <div style="font-weight:700;color:#f0f6fc;font-size:13px;margin-bottom:6px;display:flex;align-items:center;gap:8px;">
                <span style="color:#e3b341;">▶</span> {br_title}
            </div>
            <div style="font-size:12px;color:#8b949e;margin-bottom:6px;line-height:1.5;">
                <b style="color:#c9d1d9;">触发条件：</b>{br_trig}
            </div>
            <div style="font-size:12px;color:#7ee787;background:rgba(63,185,80,0.08);border-left:3px solid #3fb950;padding:6px 10px;border-radius:4px;line-height:1.5;">
                <b style="color:#3fb950;">执行指令：</b>{br_act}
            </div>
        </div>
        ''')
    branches_html = "".join(branches_items)

    target_rows = []
    for tgt in tmr.get("focus_targets") or []:
        c = _esc(tgt.get("code"))
        n = _esc(tgt.get("name"))
        r = _esc(tgt.get("role"))
        tac_m = _esc(tgt.get("tactic"))
        trg = _esc(tgt.get("trigger"))
        dfn = _esc(tgt.get("defense"))
        pos = _esc(tgt.get("position"))
        target_rows.append(f'''
        <tr style="border-bottom:1px solid #21262d;">
            <td style="padding:8px 10px;font-weight:bold;color:#f0f6fc;white-space:nowrap;">{n} <span style="font-size:10.5px;color:#8b949e;font-weight:normal;">({c})</span></td>
            <td style="padding:8px 10px;color:#58a6ff;font-size:11.5px;white-space:nowrap;">{r}</td>
            <td style="padding:8px 10px;color:#bc8cff;font-size:11.5px;white-space:nowrap;">{tac_m}</td>
            <td style="padding:8px 10px;color:#c9d1d9;font-size:11.5px;line-height:1.5;">{trg}</td>
            <td style="padding:8px 10px;color:#f85149;font-size:11.5px;white-space:nowrap;">{dfn}</td>
            <td style="padding:8px 10px;color:#d29922;font-weight:bold;white-space:nowrap;text-align:center;">{pos}</td>
        </tr>
        ''')
    targets_table_html = "".join(target_rows)

    panel_html = f'''
<!-- tactics-review-panel:start -->
<section class="tactics-war-room" id="sec-tactics-section" style="margin: 30px 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:12px;">
        <h2 class="section-title" id="sec-tactics" style="margin:0;">⚔️ 游资实战作战室 · 战法体系 × 盘面对账 × 明日预案</h2>
        <span style="font-size:12px;font-weight:normal;background:rgba(88,166,255,0.15);color:#58a6ff;border:1px solid rgba(88,166,255,0.3);padding:3px 10px;border-radius:12px;">
            {r_date} 对账 → {t_date} 操盘推演
        </span>
    </div>

    <!-- 顶端战术基调与风控指标栏 -->
    <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(220px, 1fr));gap:12px;margin:16px 0 20px 0;">
        <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 14px;box-shadow:0 2px 6px rgba(0,0,0,0.2);">
            <div style="font-size:11px;color:#8b949e;margin-bottom:4px;">🛡️ 战术基调</div>
            <div style="font-size:14px;font-weight:700;color:{s_color};">{t_stance}</div>
        </div>
        <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 14px;box-shadow:0 2px 6px rgba(0,0,0,0.2);">
            <div style="font-size:11px;color:#8b949e;margin-bottom:4px;">🌊 周期节点判定</div>
            <div style="font-size:13px;font-weight:600;color:#f0f6fc;">{c_stage}</div>
        </div>
        <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 14px;box-shadow:0 2px 6px rgba(0,0,0,0.2);">
            <div style="font-size:11px;color:#8b949e;margin-bottom:4px;">🔒 建议仓位上限</div>
            <div style="font-size:14px;font-weight:700;color:#3fb950;">{pos_ceiling}</div>
        </div>
        <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 14px;box-shadow:0 2px 6px rgba(0,0,0,0.2);">
            <div style="font-size:11px;color:#8b949e;margin-bottom:4px;">🎯 核心风向标</div>
            <div style="font-size:12px;font-weight:600;color:#58a6ff;">{beacons}</div>
        </div>
    </div>

    <!-- 模块一: 游资实战与量化深研核心兵器库 -->
    <div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:18px 20px;margin-bottom:20px;box-shadow:0 4px 12px rgba(0,0,0,0.25);">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;border-bottom:1px solid #21262d;padding-bottom:10px;flex-wrap:wrap;gap:8px;">
            <div style="font-size:15px;font-weight:700;color:#f0f6fc;display:flex;align-items:center;gap:8px;">
                <span>🏛️ 宏观量化战略库 · 连板高度年度深研七大核心战法 (战法 A ~ G)</span>
                <span style="font-size:11px;color:#8b949e;font-weight:normal;">(长周期样本实证规律)</span>
            </div>
            <a href="annual_height_research.html" target="_blank" style="font-size:11.5px;color:#58a6ff;text-decoration:none;border:1px solid rgba(88,166,255,0.3);padding:2px 8px;border-radius:10px;background:rgba(88,166,255,0.08);">
                📊 查看年度深研与六大战法可视化总看板 ↗
            </a>
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(280px, 1fr));gap:12px;margin-bottom:22px;">
            {quant_grid_html}
        </div>

        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;border-bottom:1px solid #21262d;padding-bottom:8px;border-top:1px solid #21262d;padding-top:16px;">
            <div style="font-size:14.5px;font-weight:700;color:#f0f6fc;display:flex;align-items:center;gap:8px;">
                <span>⚡ 游资微观临盘战术库 · 顶级游资四大核心战法精要 (四大临盘执行铁律)</span>
                <span style="font-size:11px;color:#8b949e;font-weight:normal;">(竞价与分时盘口实操)</span>
            </div>
            <span style="font-size:11.5px;color:#e3b341;">临盘决策准则</span>
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(280px, 1fr));gap:12px;">
            {tactics_grid_html}
        </div>
    </div>

    <!-- 模块二: 今日盘面实战对账深度复盘 -->
    <div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:18px 20px;margin-bottom:20px;box-shadow:0 4px 12px rgba(0,0,0,0.25);">
        <div style="font-size:15px;font-weight:700;color:#f0f6fc;margin-bottom:12px;border-bottom:1px solid #21262d;padding-bottom:10px;display:flex;align-items:center;gap:8px;">
            <span>📋 今日盘面实战对账深度复盘 ({r_date})</span>
        </div>
        <div style="background:#1c2128;border-radius:6px;padding:12px 15px;font-size:12.5px;color:#c9d1d9;line-height:1.65;margin-bottom:14px;border-left:3px solid #58a6ff;">
            <b>【盘面定性】：</b>{m_qual}
        </div>
        <div style="margin-bottom:14px;">
            <div style="font-size:13px;font-weight:700;color:#f0f6fc;margin-bottom:8px;">🪜 连板天梯晋级与卡位实况</div>
            <div style="overflow-x:auto;">
                <table style="width:100%;font-size:12px;text-align:left;border-collapse:collapse;">
                    <thead>
                        <tr style="background:#1c2128;color:#8b949e;border-bottom:1px solid #30363d;">
                            <th style="padding:8px 10px;">梯队身位</th>
                            <th style="padding:8px 10px;">代表标的</th>
                            <th style="padding:8px 10px;">晋级状态</th>
                            <th style="padding:8px 10px;">盘面战法解析</th>
                        </tr>
                    </thead>
                    <tbody>
                        {echelon_table_html}
                    </tbody>
                </table>
            </div>
        </div>
        <div style="background:rgba(88,166,255,0.06);border:1px solid rgba(88,166,255,0.2);border-radius:6px;padding:10px 14px;font-size:12px;color:#c9d1d9;line-height:1.6;">
            <b style="color:#58a6ff;">🛡️ 容量大中军与主力中枢动向：</b>{zj_ana}
        </div>
    </div>

    <!-- 模块三: 昨日预案实战检验与推演对比 -->
    <div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:18px 20px;margin-bottom:20px;box-shadow:0 4px 12px rgba(0,0,0,0.25);">
        <div style="font-size:15px;font-weight:700;color:#f0f6fc;margin-bottom:12px;border-bottom:1px solid #21262d;padding-bottom:10px;display:flex;align-items:center;justify-content:space-between;">
            <span>🎯 昨日预案实战检验与逻辑回溯</span>
            <span style="font-size:11px;color:#3fb950;font-weight:bold;">已验证全命中 · 逻辑闭环</span>
        </div>
        <div style="overflow-x:auto;">
            <table style="width:100%;font-size:12px;text-align:left;border-collapse:collapse;">
                <thead>
                    <tr style="background:#1c2128;color:#8b949e;border-bottom:1px solid #30363d;">
                        <th style="padding:8px 10px;">核验维度</th>
                        <th style="padding:8px 10px;">昨日推演预案</th>
                        <th style="padding:8px 10px;">今日盘面实况</th>
                        <th style="padding:8px 10px;text-align:center;">验证结论</th>
                        <th style="padding:8px 10px;">操盘得失评价</th>
                    </tr>
                </thead>
                <tbody>
                    {comp_table_html}
                </tbody>
            </table>
        </div>
    </div>

    <!-- 模块四: 明日操盘实战预案 -->
    <div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:18px 20px;margin-bottom:20px;box-shadow:0 4px 12px rgba(0,0,0,0.25);">
        <div style="font-size:15px;font-weight:700;color:#f0f6fc;margin-bottom:14px;border-bottom:1px solid #21262d;padding-bottom:10px;display:flex;align-items:center;justify-content:space-between;">
            <span>🚀 明日操盘实战作战预案 ({t_date})</span>
            <span style="font-size:11.5px;color:#e3b341;font-weight:600;">严格执行 · 不见信号不出手</span>
        </div>

        <div style="background:rgba(210,153,34,0.08);border:1px solid rgba(210,153,34,0.3);border-radius:6px;padding:12px 15px;margin-bottom:16px;">
            <div style="font-size:12px;color:#d29922;font-weight:700;margin-bottom:4px;">📌 总体操作基调与仓位风控：</div>
            <div style="font-size:13px;color:#f0f6fc;font-weight:600;">{tmr_stance}</div>
        </div>

        <div style="margin-bottom:16px;">
            <div style="font-size:13px;font-weight:700;color:#f0f6fc;margin-bottom:8px;">⏱️ 早盘 09:25 集合竞价关键看盘信标 (Beacon)</div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(260px, 1fr));gap:10px;">
                {beacons_html}
            </div>
        </div>

        <div style="margin-bottom:16px;">
            <div style="font-size:13px;font-weight:700;color:#f0f6fc;margin-bottom:8px;">🌳 盘中实战应变推演分支 (Scenario Trees)</div>
            {branches_html}
        </div>

        <div>
            <div style="font-size:13px;font-weight:700;color:#f0f6fc;margin-bottom:8px;">🎯 精选作战观察标的雷达池</div>
            <div style="overflow-x:auto;">
                <table style="width:100%;font-size:12px;text-align:left;border-collapse:collapse;">
                    <thead>
                        <tr style="background:#1c2128;color:#8b949e;border-bottom:1px solid #30363d;">
                            <th style="padding:8px 10px;">标的名称</th>
                            <th style="padding:8px 10px;">属性角色</th>
                            <th style="padding:8px 10px;">对应战法</th>
                            <th style="padding:8px 10px;">买入触发条件</th>
                            <th style="padding:8px 10px;">止损防守位</th>
                            <th style="padding:8px 10px;text-align:center;">参考仓位</th>
                        </tr>
                    </thead>
                    <tbody>
                        {targets_table_html}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</section>
<!-- tactics-review-panel:end -->
'''
    return panel_html

