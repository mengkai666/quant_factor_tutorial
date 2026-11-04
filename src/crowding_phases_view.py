"""自动阶段独立展示：日期回放、确认时钟、冻结篮子及并行旧团状态。"""
from __future__ import annotations

import html


def e(value):
    return html.escape(str(value if value is not None else "—"), quote=True)


PHASE_CSS = """
.auto-phase-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:16px 0}
.auto-phase-summary>div{padding:12px 16px;border-left:3px solid var(--new);background:#142a30}
.auto-phase-summary b{display:block;font-size:19px}.auto-phase-summary small{color:var(--muted)}
.auto-phase-controls{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:16px 0}
.auto-phase-controls select{background:var(--bg);color:var(--text);border:1px solid var(--edge);padding:8px}
.auto-phase-table{min-width:1000px}.auto-cohort-table{min-width:1000px}
.auto-phase-day[hidden],.auto-phase-table tr[hidden]{display:none}
.auto-phase-day .branch{margin-bottom:16px}.auto-phase-rules{font-size:13px}
@media(max-width:650px){.auto-phase-summary{grid-template-columns:1fr}.auto-phase-controls{align-items:flex-start}}
"""
PHASE_SCRIPT = """
(function(){
  const select=document.getElementById('auto-phase-date');
  if(!select)return;
  function refresh(){
    const date=select.value;
    document.querySelectorAll('.auto-phase-day').forEach(p=>{p.hidden=p.dataset.date!==date;});
    document.querySelectorAll('.auto-phase-table tbody tr').forEach(row=>{
      row.hidden=row.dataset.start>date;
      row.querySelector('[data-end-cell]').textContent=row.dataset.end<date?row.dataset.end:date;
      row.querySelector('[data-status-cell]').textContent=row.dataset.next&&row.dataset.next<=date?'已结束':'截至所选日 / 观察中';
    });
  }
  select.addEventListener('change',refresh);refresh();
})();
"""


def _table(headers, rows, cls):
    return '<div class="table-scroll"><table class="' + cls + '"><thead><tr>' + ''.join(
        '<th>' + e(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'


def render_automatic_phases(phases):
    if not phases:
        return ''
    daily, segments = phases['daily'], phases['segments']
    cohort_by_id = {c['id']: c for c in phases['cohorts']}
    options = ''.join(f'<option value="{e(r["date"])}" {"selected" if i==len(daily)-1 else ""}>{e(r["date"])}</option>'
                      for i, r in enumerate(daily))
    panels = []
    for i, record in enumerate(daily):
        active = cohort_by_id.get(record['active_cohort_id'], {})
        rows = []
        for s in record['cohorts']:
            c = cohort_by_id[s['cohort_id']]
            cells = [e(c['formed_on']), e(c['anchor_name']) + '<small> / ' + e(c['id']) + '</small>',
                     e('当前观察篮子' if s['cohort_id']==record['active_cohort_id'] else '并行旧篮子'),
                     e(s['stage']), e(f'{s["coverage"]}/{s["denominator"]}'),
                     e('—' if s['strong_share'] is None else f'{s["strong_share"]:.0%}'),
                     e('—' if s['weak_share'] is None else f'{s["weak_share"]:.0%}'),
                     e(f'{s["core_failures"]}次 / ' + ('有瓦解历史' if s['was_broken'] else '未确认瓦解')),
                     e(' · '.join(c['codes'])), e(s['reason'])]
            rows.append('<tr>' + ''.join('<td>' + cell + '</td>' for cell in cells) + '</tr>')
        panels.append(f'''<article class="auto-phase-day panel" data-date="{e(record['date'])}" {"hidden" if i!=len(daily)-1 else ""}>
          <div class="auto-phase-summary">
            <div><small>截至 {e(record['date'])} · 价格规则阶段</small><b>{e(record['stage'])}</b><small>阶段开始 {e(record['stage_since'])}</small></div>
            <div><small>首次观察到 / 规则确认日</small><b>{e(record['observed_since'])}</b><small>确认 {e(record['confirmed_on'] or '尚未确认 / 暂停')}</small></div>
            <div><small>当前冻结篮子 / 并行旧篮子</small><b>{e(active.get('anchor_name', '暂无群体'))}</b><small>{len(record['parallel_cohort_ids'])}个并行旧篮子 · {record['parallel_risk_count']}个旧篮子有价格风险</small></div>
          </div>
          <p>{e(record['reason'])}</p><p class="tiny muted">价格证据日 {e(record['evidence_as_of'])}；上一有效阶段 {e(record['last_known_stage'])}。篮子名称使用冻结时核心作为标识，不意味着该个股一直有效。</p>
          {_table(['冻结日','篮子标识','身份','篮子阶段','完整成员','强势占比','深回撤占比','失效 / 历史','冻结成员（不剔除失败者）','判定依据'], rows, 'auto-cohort-table') if rows else '<p class="muted">尚无满足最小成员数与多日确认条件的冻结群体，继续等待。</p>'}
        </article>''')
    segment_rows = []
    for i, s in enumerate(segments):
        c = cohort_by_id.get(s['cohort_id'], {})
        following = segments[i + 1]['start'] if i + 1 < len(segments) else ''
        cells = [f'<td>{e(s["start"])}</td>', f'<td data-end-cell>{e(s["end"])}</td>',
                 f'<td>{e(s["title"])}</td>', f'<td>{e(c.get("anchor_name"))}</td>',
                 f'<td>{e(s["observed_since"])}</td>', f'<td>{e(s["confirmed_on"] or "未确认 / 暂停")}</td>',
                 f'<td>{e("窗口左边界截断；原起点 " + s["origin_start"] if s["left_censored"] else "当日可见迁移")}</td>',
                 '<td data-status-cell>观察中</td>', f'<td>{e(s["start_reason"])}</td>']
        segment_rows.append(f'<tr data-start="{e(s["start"])}" data-end="{e(s["end"])}" data-next="{e(following)}">' + ''.join(cells) + '</tr>')
    rules = phases['rules']
    return f'''<section id="automatic-phases"><div class="section-head"><h2>自动时间阶段划分</h2><span class="badge">{e(phases['rule_version'])}</span></div>
      <p class="muted">沿用之前的阶段含义，日期由逐日状态迁移自动生成，不使用历史案例固定日期，也不是每5日切一段。</p>
      <div class="branch">{e(phases['limitation'])} 阶段开始日不倒填：首次观察到与规则确认日分开；出清观察≠出清完成，核心失效≠断板。</div>
      <div class="auto-phase-controls"><label for="auto-phase-date">回放日期</label><select id="auto-phase-date">{options}</select><span class="tiny muted">按所选日查看当时可见阶段及并行旧篮子；不会用后续阶段覆盖前一天。</span></div>
      {''.join(panels) if panels else '<p class="panel muted">无可用逐日价格记录，无法自动划分阶段。</p>'}
      <h3>自动阶段时间表</h3>
      {_table(['阶段开始','截至日期','阶段','冻结篮子','首次观察到','规则确认','边界','状态','开始依据'], segment_rows, 'auto-phase-table')}
      <details class="panel auto-phase-rules"><summary>阶段规则与证据边界</summary>
        <p>核心首次确认时冻结当日排名前{rules['basket_size']}个候选（含核心）；至少{rules['min_members']}名完整成员，不从未来补赢家。旧篮子内换位不新建群体，篮子外确认代理可建立并行群体。</p>
        <ul><li>聚焦强化：强势成员≥70%、深回撤成员&lt;60%，连续2个完整交易日。</li>
        <li>瓦解压力 / 瓦解：深回撤（距20日高点≤−10%且在MA5下）占比≥60%；当日成员收益中位数≤−2%则记价格瓦解。多次核心失效且同伴走弱也记压力，不把单股失效等同群体瓦解。</li>
        <li>出清观察：有瓦解历史的同一篮子连续2日无人创前3日收盘新低；不是资金出清完成。</li>
        <li>旧团修复：同一篮子多数连续3日恢复MA5及正5日收益，保留此前瓦解记录。新旧篮子并行状态分别展示，不能由旧团反抽推断新主线。</li>
        <li>缺价 / 截面不足 / 无交易日历：停止确认，连续计数归零，保留上一有效阶段及冻结成员。规则未经盈利或成交验证。</li></ul>
      </details>
    </section>'''


def automatic_phases_markdown(phases):
    if not phases:
        return []
    latest = phases.get('latest')
    lines = ['', '## 自动时间阶段划分', phases['limitation'],
             f"规则版本：{phases['rule_version']}；人工历史分段仍只作对照。"]
    if latest:
        lines += [f"截至 {latest['date']}：**{latest['stage']}**；阶段开始 {latest['stage_since']}；首次观察到 {latest['observed_since']}；规则确认 {latest['confirmed_on'] or '未确认 / 暂停'}。", latest['reason']]
    lines += ['', '| 阶段开始 | 截至 | 阶段 | 首次观察到 | 规则确认 | 边界 |', '|---|---|---|---|---|---|']
    lines += [f"| {s['start']} | {s['end']} | {s['title']} | {s['observed_since']} | {s['confirmed_on'] or '未确认 / 暂停'} | {'左边界截断' if s['left_censored'] else '当日状态迁移'} |" for s in phases['segments']]
    lines += ['', '阶段开始日以当日可见规则结论为准，不回填到确认前观察日。多个冻结篮子可并行，详见HTML日期回放。']
    return lines
