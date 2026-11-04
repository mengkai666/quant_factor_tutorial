"""结论优先的只读摘要；不改变分段、入池规则或交易资格。"""
from __future__ import annotations

from collections import Counter
import html


CONCLUSIONS_CSS = """
.conclusion-lead{border-left:4px solid var(--new);padding:22px 26px;background:var(--panel);margin:18px 0}
.conclusion-stage{font-size:clamp(24px,3vw,34px);line-height:1.4;font-weight:700;margin:8px 0;color:var(--new)}
.conclusion-clock{display:flex;flex-wrap:wrap;gap:8px 24px;color:var(--muted);font-size:12px}
.conclusion-links{display:flex;flex-wrap:wrap;gap:12px 24px;margin-top:16px;font-size:13px}
.conclusion-list{list-style:none;margin:12px 0 0;padding:0}.conclusion-list li{border-top:1px solid var(--edge);padding:12px 0}
.conclusion-list li:last-child{padding-bottom:0}.conclusion-list strong{display:block}.conclusion-list small{display:block;color:var(--muted);font-size:12px}
.conclusion-note{font-size:13px;color:var(--muted)}.analysis-divider{border-top:2px solid var(--edge);padding-top:26px}
@media(max-width:650px){.conclusion-lead{padding:18px}.conclusion-links{gap:8px 16px}.conclusion-clock{gap:4px 16px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
"""


def _e(value):
    return html.escape(str(value if value is not None else '—'), quote=True)


def _latest(records, cutoff):
    visible = [r for r in records if r.get('date') and r['date'] <= cutoff]
    return max(visible, key=lambda r: r['date'], default={})


def build_conclusion_summary(result: dict) -> dict:
    """摘要取各层最后可见快照；缺价与退出记录不冒充当前有效跟踪。"""
    cutoff = result.get('requested_date') or ''
    phases = result.get('automatic_phases') or {}
    phase = _latest(phases.get('daily') or [], cutoff)
    if not phase:
        phase = _latest([phases.get('latest') or {}], cutoff)
    tracking = result.get('tracking') or {}
    tracked = _latest(tracking.get('daily') or [], cutoff)
    rows = tracked.get('rows') or []
    coverage_ok = tracked.get('coverage_ok') is True
    usable = [r for r in rows if coverage_ok and r.get('evidence_ok') is True
              and r.get('metrics_as_of') == tracked.get('date')]
    old_active = [r for r in usable if r.get('identity') == '旧团' and r.get('active')]
    old_pending = [r for r in usable if r.get('identity') == '旧团'
                   and not r.get('active') and r.get('status') == '旧团修复待验证']
    new_active = [r for r in usable if r.get('identity') == '新候选/新团' and r.get('active')]
    new_pending = [r for r in usable if r.get('identity') == '新候选/新团'
                   and not r.get('active') and r.get('status') == '新生候选']
    boundary = [r for r in usable if r.get('identity') == '边界存量' and r.get('active')]
    old_exited = [r for r in rows if r.get('identity') == '旧团' and r.get('status') == '已退出']
    unknown = [r for r in rows if r.get('status') == '数据不足'
               or (r.get('active') and r not in usable)]
    cohorts = phases.get('cohorts') or []
    active = next((c for c in cohorts if c.get('id') == phase.get('active_cohort_id')), {})
    names = {r['code']: r.get('name') or r['code'] for r in rows if r.get('code')}
    names.update({c['anchor_code']: c.get('anchor_name') or c['anchor_code']
                  for c in cohorts if c.get('anchor_code')})
    proxy = phase.get('confirmed_proxy') or phase.get('pending_proxy')
    groups = [g for g in result.get('groups') or []
              if (g.get('assessment') or {}).get('date')
              and g['assessment']['date'] <= cutoff]
    replay = result.get('screening_replay') or {}
    window = (replay.get('windows') or {}).get('2') or {}
    replay_day = _latest(window.get('days') or [], cutoff)
    selected = (replay_day.get('selected') or {}) if replay_day.get('coverage_ok') is True else {}
    pools = Counter(row.get('pool') or (replay_day.get('pools') or {}).get(code, 'new')
                    for code, row in selected.items())
    return dict(phase=phase, proxy_name=names.get(proxy, proxy), active_cohort=active,
                price_date=(result.get('sources_as_of') or {}).get('prices') or result.get('market_as_of'),
                tracking_date=tracked.get('date'), tracking_coverage_ok=coverage_ok,
                old_active=old_active, old_pending=old_pending, old_exited_count=len(old_exited),
                new_active=new_active, new_pending=new_pending, boundary=boundary,
                unknown_count=len(unknown), groups=groups, replay_date=replay_day.get('date'),
                replay_coverage_ok=replay_day.get('coverage_ok') is True,
                replay_selected_count=len(selected), replay_pools=dict(pools))


def _stock_list(rows, empty, limit=8):
    items = ''.join(f'<li><strong>{_e(r.get("name"))} <span class="badge">{_e(r.get("status"))}</span></strong>'
                    f'<small>{_e(r.get("code"))} · 连续支持 {_e(r.get("support_streak"))} 日'
                    f' · 证据 {_e(r.get("metrics_as_of"))}</small></li>' for r in rows[:limit])
    if not items:
        return f'<p class="conclusion-note">{_e(empty)}</p>'
    tail = f'<p class="conclusion-note">这里只列前{limit}条，共{len(rows)}条；全量状态与退出记录见后方跟踪详情。</p>' if len(rows) > limit else ''
    return f'<ul class="conclusion-list">{items}</ul>{tail}'


def render_conclusions(result: dict) -> str:
    s = build_conclusion_summary(result)
    p = s['phase']
    stage = p.get('stage') or '尚无自动阶段结论'
    reason = p.get('reason') or '缺少可用的自动阶段快照，不用历史名单补位。'
    cohort = s['active_cohort']
    proxy_label = '自动阶段对应的价格核心代理' if p.get('confirmed_proxy') else '自动阶段的待确认候选'
    proxy = f'{proxy_label}：{_e(s["proxy_name"])}。' if s['proxy_name'] else '尚无可用价格核心代理。'
    basket = f'对应 {_e(cohort.get("formed_on"))} 建立的 {len(cohort.get("codes") or [])} 只固定篮子。' if cohort else ''
    history_items = ''.join(
        f'<li><strong>{_e(g.get("title"))} · {_e(g["assessment"].get("stage"))}</strong>'
        f'<small>证据 {_e(g["assessment"].get("date"))} · {_e(g["assessment"].get("reason"))}</small></li>'
        for g in s['groups'])
    history = f'<ul class="conclusion-list">{history_items}</ul>' if history_items else '<p class="conclusion-note">本期没有可见的历史研究篮子；不预填以前的股票名单。</p>'
    old = _stock_list(s['old_active'], '没有证据完整的旧团持续跟踪股；不把归档退出股当作当前有效。')
    repair = _stock_list(s['old_pending'], '没有新增的旧团修复待验证记录。')
    new = _stock_list(s['new_active'], '没有证据完整的新候选持续跟踪股；这不代表全市场没有机会。')
    boundary = '；'.join(f'{r.get("name")}（{r.get("status")}）' for r in s['boundary']) or '暂无证据完整的边界存量跟踪股'
    pending_names = '、'.join(r.get('name') or r.get('code') or '未命名' for r in s['new_pending'][:6])
    pending = f'列出前{min(6, len(s["new_pending"]))}条：{pending_names}。' if pending_names else '不拿历史赢家补充观察名单。'
    pool_labels = {'new': '新生 / 新团', 'old': '老团延续', 'repair': '旧团修复 / 再验证', 'boundary': '窗口边界存量'}
    pool_counts = '；'.join(f'{pool_labels.get(pool, pool)} {count}只' for pool, count in s['replay_pools'].items())
    replay_note = (f'最近两个月分类研究条件通过 {s["replay_selected_count"]} 只：{pool_counts or "暂无"}。'
                   if s['replay_coverage_ok'] else '最近两个月分类截面缺失或不足，暂停给出条件通过结论。')
    tracking_note = '' if s['tracking_coverage_ok'] else '<p class="alert">动态跟踪截面缺失或不足：暂停给出当前有效名单，历史状态仅供留档。</p>'
    return f'''<section id="conclusions" aria-labelledby="conclusions-title"><h2 id="conclusions-title">结论先行</h2>
<div class="conclusion-clock"><span>报告截止 {_e(result.get('requested_date'))}</span><span>全市场价格证据 {_e(s['price_date'])}</span></div>
<div class="conclusion-lead"><div class="eyebrow">最近可见价格阶段 · {_e(p.get('date'))}</div><p class="conclusion-stage">{_e(stage)}</p>
<p>{_e(reason)}</p><p>{proxy}{basket}这是价格研究阶段，不是资金抱团或新周期交易确认。</p>
<div class="conclusion-clock"><span>阶段生效 {_e(p.get('stage_since'))}</span><span>开始观察 {_e(p.get('observed_since'))}</span><span>阶段确认 {_e(p.get('confirmed_on') or '待确认')}</span></div></div>
<p class="conclusion-note">阅读要点：旧团延续、修复与新候选竞争分别看；新篮子聚焦不代表旧篮子已经出清。</p>
<p class="conclusion-note">本页前方是最近可见日的固定摘要；切换后方历史回放不会改写这里，请按各区标注的证据日阅读。</p>
<div class="conclusion-links"><a href="#previous-tracking">先看此前抱团</a><a href="#recent-observations">再看最近观察</a><a href="#analysis-details">查看分析依据 ↓</a></div></section>
<section id="previous-tracking"><h2>此前抱团跟踪</h2><p class="conclusion-note">先回答“以前跟踪的现在怎么样”。历史研究篮子和自动识别的旧团不是同一套名单，分开报告。</p>
<div class="grid"><article class="panel"><h3>此前研究篮子 · 最新复核</h3><p class="tiny muted">来自历史研究注释，仅复核状态，不认定为当前有效抱团。</p>{history}</article>
<article class="panel"><h3>自动旧团 · 当前跟踪结论</h3><p class="tiny muted">动态观察日 {_e(s['tracking_date'])} · 旧团持续跟踪 {len(s['old_active'])} 只，修复待验证 {len(s['old_pending'])} 只。</p>{tracking_note}{old}
<h4>修复待验证 · 不算已恢复入池</h4>{repair}<p class="conclusion-note">另有 {s['old_exited_count']} 条旧团已退出记录保留在归档中，不列为当前有效；数据不足记录 {s['unknown_count']} 条（全跟踪范围）。</p>
<a href="#dynamic-tracking">查看旧团延续、退出与重入详情 →</a></article></div></section>
<section id="recent-observations"><h2>最近观察</h2><p class="conclusion-note">观察日 {_e(s['tracking_date'])}；只描述价格支持与待验证状态，不给出买入名单。</p>
<div class="grid"><article class="panel"><h3>新候选 · 持续跟踪</h3><p class="tiny muted">当前 {len(s['new_active'])} 只；分歧观察仍需继续核对，不等于当天条件全部通过。</p>{new}</article>
<article class="panel"><h3>仍在等待验证</h3><p>新生候选 {len(s['new_pending'])} 只，尚未完成动态入池确认。</p><p class="conclusion-note">{_e(pending)}</p>
<h4>边界存量 · 不声称本期新生</h4><p class="conclusion-note">{_e(boundary)}</p>
<h4>分类研究条件 · 独立观察口径</h4><p class="conclusion-note">证据 {_e(s['replay_date'])}。{_e(replay_note)}不改写正式动态跟踪，也不代表可成交。</p>
<a href="#screening-replay">查看最近观察的逐项条件 →</a></article></div>
<p class="alert">{_e((result.get('execution') or {}).get('reason') or '缺少完整成交与承接证据，只作研究观察。')} 本页不生成自动交易信号。</p></section>'''


def conclusions_markdown(result: dict) -> list[str]:
    s = build_conclusion_summary(result)
    p = s['phase']
    def md(value):
        return str(value if value is not None else '—').replace('\n', ' ')
    lines = ['', '## 结论先行', f'全市场价格证据：{md(s["price_date"])}；自动观察日：{md(p.get("date"))}。',
             f'最近可见价格阶段：{md(p.get("stage") or "尚无自动阶段结论")}。',
             md(p.get('reason') or '缺少可用阶段快照，不用历史名单补位。'),
             f'自动阶段的{"价格核心代理" if p.get("confirmed_proxy") else "待确认候选"}：{md(s["proxy_name"])}；阶段生效 {md(p.get("stage_since"))}；开始观察 {md(p.get("observed_since"))}；确认 {md(p.get("confirmed_on") or "待确认")}。',
             '价格阶段不是资金抱团或交易确认；新篮子聚焦不代表旧篮子出清。', '', '## 此前抱团跟踪',
             '### 此前研究篮子 · 最新复核（历史注释，不是当前有效名单）']
    lines += [f'- {md(g.get("title"))}：{md(g["assessment"].get("stage"))}；证据 {md(g["assessment"].get("date"))}。{md(g["assessment"].get("reason"))}' for g in s['groups']]
    if not s['groups']:
        lines.append('本期没有可见的历史研究篮子。')
    lines += ['', f'### 自动旧团 · 观察日 {md(s["tracking_date"])}',
              f'持续跟踪 {len(s["old_active"])} 只；修复待验证 {len(s["old_pending"])} 只；已退出留档 {s["old_exited_count"]} 条（非当前有效）。']
    if not s['tracking_coverage_ok']:
        lines.append('动态截面缺失或不足，暂停给出当前有效名单。')
    lines += [f'- {md(r.get("name"))} / {md(r.get("code"))}：{md(r.get("status"))}；连续支持 {md(r.get("support_streak"))} 日；证据 {md(r.get("metrics_as_of"))}。' for r in s['old_active'] + s['old_pending']]
    lines += ['', '## 最近观察', f'动态观察日 {md(s["tracking_date"])}；新候选持续跟踪 {len(s["new_active"])} 只，新生待验证 {len(s["new_pending"])} 只。']
    lines += [f'- {md(r.get("name"))} / {md(r.get("code"))}：{md(r.get("status"))}；连续支持 {md(r.get("support_streak"))} 日；证据 {md(r.get("metrics_as_of"))}。' for r in s['new_active']]
    if s['replay_coverage_ok']:
        lines.append(f'分类研究观察日 {md(s["replay_date"])}：最近两个月条件通过 {s["replay_selected_count"]} 只；独立研究口径，不改写动态入池。')
    else:
        lines.append('分类截面缺失或不足，暂停给出条件通过结论。')
    lines += [f'数据不足记录 {s["unknown_count"]} 条（全跟踪范围）；归档和待验证不算当前有效。',
              md((result.get('execution') or {}).get('reason') or '缺少完整成交与承接证据，只作研究观察。'),
              '结论取最近可见日，不是可成交验证、买卖信号或历史事前记录。']
    return lines
