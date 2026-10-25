"""动态跟踪表：日期回放、新老身份、自动周期与可追溯状态；纯离线HTML。"""
from __future__ import annotations
import html


def _e(value):
    return html.escape(str(value if value is not None else '—'), quote=True)


TRACKING_CSS = '''
.tracking-summary{display:flex;flex-wrap:wrap;gap:12px;border-left:3px solid var(--new);padding:10px 16px;background:#142a30;margin:16px 0}
.tracking-summary span{font:13px/1.8 Consolas,"Microsoft YaHei",sans-serif}.tracking-controls{display:flex;flex-wrap:wrap;align-items:end;gap:12px;margin:18px 0}.tracking-controls label{display:grid;gap:4px;font-size:12px;color:var(--muted)}.tracking-controls input,.tracking-controls select{background:var(--bg);border:1px solid var(--edge);color:var(--text);padding:7px 10px;border-radius:4px;max-width:100%}
.tracking-table{min-width:1550px;table-layout:fixed}.tracking-table th:nth-child(9){width:270px}.tracking-table th:nth-child(8){width:190px}.tracking-table td{white-space:normal;vertical-align:top;font-size:12px}.tracking-table th{white-space:nowrap}.tracking-table .status-unknown{color:var(--muted)}.tracking-table .tracking-name{padding:0;background:none;border:0;color:var(--new);cursor:pointer;font-weight:bold;text-align:left}.tracking-table small{display:block;color:var(--muted);font-size:11px}.tracking-table details{margin:8px 0 0;padding:0;border:0}.tracking-history{display:flex;gap:3px;flex-wrap:wrap;min-width:170px}.tracking-history span{border-bottom:3px solid var(--new);padding:2px 3px;font:11px/1.5 Consolas,monospace;background:var(--bg)}.tracking-history .unknown{border-color:var(--muted)}.tracking-history .risk{border-color:var(--risk)}.tracking-history .old{border-color:var(--old)}
@media(max-width:650px){.tracking-controls label{min-width:0;flex:1 1 140px}.tracking-controls input,.tracking-controls select{width:100%}.tracking-summary{gap:5px 14px}}
'''
HEADERS = ['个股 / 点击看路径', '新老身份 / 当前阶段', '窗口首见 / 首次入池', '首次周期 / 最近匹配',
           '连续支持 / 本轮入池累计', '最近1 / 5 / 20日', '距20日高点 / 证据日', '最近5次状态', '变化依据 / 留档']


def render_tracking_section(tracking: dict | None) -> str:
    if not tracking:
        return ''
    days, cycles, rules = tracking['daily'], tracking['cycles'], tracking['rules']
    date = days[-1]['date'] if days else None
    last = days[-1] if days else dict(rows=[])
    dates = ''.join(f'<option value="{_e(d["date"])}" {"selected" if d["date"] == date else ""}>{_e(d["date"])}</option>' for d in days)
    cycle_options = ''.join(f'<option value="{_e(c["id"])}">{_e(c["start"])} · {_e(c["anchor_name"] or "候选竞争")}</option>' for c in cycles)
    cycle_rows = ''.join('<tr>' + ''.join(f'<td>{_e(v)}</td>' for v in
        [c['start'] + ' — ' + c['end'], c['anchor_name'] or '暂无核心 / 候选竞争', c['kind'],
         ('窗口边界截断' if c['left_censored'] else '核心换位 / 旧核心失效') + '；确认 ' + (c['confirmed_on'] or '待确认'), c['status'], len(c['admission_codes'])]) + '</tr>' for c in cycles)
    watch_rows = [r for r in last['rows'] if r['active'] or r.get('last_known_status', r['status']) in
                  ('新生候选', '旧团修复待验证', '存量待验证')]
    initial = ''.join('<tr>' + ''.join(f'<td>{_e(v)}</td>' for v in [
        row['name'] + ' / ' + row['code'], row['identity'] + ' / ' + row['status'],
        row['first_seen'] + ' / ' + (row['first_admitted'] or '尚未入池'),
        (row['first_cycle_id'] or '待确认') + ' / ' + (row['matched_cycle_id'] or '—'),
        f'{row["support_streak"]} / {row["episode_support_days"]}',
        ' / '.join('—' if row['metrics'].get(k) is None else f'{row["metrics"][k]:+.2f}%' for k in ('r1', 'r5', 'r20')),
        str(row['metrics'].get('dd20')) + ' / ' + str(row['metrics_as_of']),
        '启用脚本后可切换日期查看', row['reason']]) + '</tr>' for row in watch_rows)
    if not initial:
        initial = f'<tr><td colspan="{len(HEADERS)}">无满足跟踪条件的个股；检查价格窗口和截面覆盖，不用历史名单补位。</td></tr>'
    return f'''<section id="dynamic-tracking"><div class="section-head"><h2>动态新老抱团追踪 · 两个月回放</h2><span class="badge">不预填个股名单</span></div>
<p class="muted">同一套规则逐日回放，每只股票保留首次入池、对应观察周期、退出与重新入池记录。旧团跨周期延续或修复，不改名为新团；失败样本不删除。</p>
<div class="panel"><details><summary>入选条件、周期边界与新老口径</summary>
<p class="tiny">① 既有价格候选：5日收益&gt;4%、20日收益&gt;8%，近5日至少2日的5日收益处于全市场前15%。<br>
② 候选池内排名前{rules['top_fraction']:.0%}（向上取整、至少1名、边界同分保留），收盘不低于MA5，距20日收盘高点回撤不超过{rules['max_drawdown_pct']:g}%。<br>
③ 连续{rules['admission_days']}个完整交易日满足条件才入池，连续{rules['strengthening_days']}日支持标为强化；连续{rules['exit_days']}日不满足才退出，单日落后先观察。缺价打断确认，不计为退出。<br>
④ 旧核心价格失效或核心换位启动下一轮；“候选竞争→首次核心确认”属于同一轮阶段推进，不把刚确认的核心误归旧团。缺数不另开新轮。这是价格观察分段，不把全市场强行认成一条资金周期。<br>
⑤ 首次入池属本观察段为新候选/新团；曾在旧段入池而仍持续、或退出后重新入池为旧团。窗口起点已强势的个股单列边界存量，不能宣称新生。</p>
<p class="tiny muted">分段核心沿用原价格代理规则；追踪入池额外要求相对排名、MA5和回撤，因此核心不一定进入更严格的追踪池。全市场候选不代表同一板块的真实资金抱团；点击个股路径时只显示至所选观察日。</p>
<p class="tiny muted">规则 { _e(tracking['rule_id']) } · 参数可通过构建工具JSON文件配置，不按事后赢家调参。首见仅指本窗口；历史身份和数据不足均保留。阈值尚未校准，不能代表交易胜率。</p></details>
<div class="tracking-controls"><label>观察日期<select id="tracking-date" aria-label="跟踪观察日期">{dates}</select></label>
<label>追踪分组<select id="tracking-pool" aria-label="跟踪分组"><option value="watch">在跟踪 / 待确认</option><option value="new">新候选 / 新团</option><option value="old">老团延续 / 修复</option><option value="boundary">窗口边界存量</option><option value="unknown">跟踪池缺数待验证</option><option value="exited">退出 / 未通过留档</option><option value="all">全部历史留档</option></select></label>
<label>对应观察周期<select id="tracking-cycle" aria-label="对应观察周期"><option value="all">全部周期</option>{cycle_options}</select></label>
<label>查找个股<input id="tracking-search" type="search" aria-label="查找跟踪个股" placeholder="名称或代码"></label></div>
<div id="tracking-summary" class="tracking-summary" aria-live="polite">观察日 {_e(date)} · 在跟踪/待确认 {len(watch_rows)} 只；历史留档 {len(last['rows'])} 只（非当前名单）；缺数时沿用历史身份，当前资格未知。</div>
<div class="table-scroll"><table class="tracking-table"><thead><tr>{''.join(f'<th>{_e(h)}</th>' for h in HEADERS)}</tr></thead><tbody id="tracking-rows">{initial}</tbody></table></div>
<p class="tiny muted">表格可横向滚动。默认显示在跟踪/待确认个股；退出、未通过样本在历史留档内，不算当前新团数量。不截断为前六名，按日期查看当时状态。灰色轨迹为未知，橙色为分歧/退出，紫色为旧团，青色为新候选。缺数时显示最后有效证据，不将它当成当前价格。</p>
<details><summary>两个月自动观察周期与入池对应</summary><div class="table-scroll"><table><thead><tr>{''.join(f'<th>{_e(h)}</th>' for h in ['观察范围','核心价格代理','分段类型','边界依据','状态','本轮曾入池个股数（去重）'])}</tr></thead><tbody>{cycle_rows}</tbody></table></div></details>
<p class="tiny muted">{_e(tracking['limitation'])} 保存的是当前缓存重建，known_at为空，不是历史事前选股证据。</p></div></section>'''


TRACKING_SCRIPT = r'''
const tracking=data.tracking;
if(tracking){
 const dates=document.getElementById('tracking-date'),pool=document.getElementById('tracking-pool'),cycle=document.getElementById('tracking-cycle'),search=document.getElementById('tracking-search');
 const stocks=new Map(tracking.stocks.map(s=>[s.code,s]));
 const cycleName=id=>{const c=tracking.cycles.find(c=>c.id===id);return c?c.start.slice(5)+' · '+(c.confirmed_on&&c.confirmed_on<=dates.value?c.anchor_name:'候选竞争'):'待确认';};
 const fmt=v=>v===null||v===undefined?'—':(v>=0?'+':'')+Number(v).toFixed(2)+'%';
 const watching=r=>r.active||['新生候选','旧团修复待验证','存量待验证'].includes(r.last_known_status||r.status);
 function renderTracking(){
  const index=tracking.daily.findIndex(d=>d.date===dates.value),snapshot=tracking.daily[index],body=document.getElementById('tracking-rows');body.replaceChildren();
  if(!snapshot){document.getElementById('tracking-summary').textContent='无可用价格观察日；补齐全市场缓存与交易日历后重建。';return;}
  const query=search.value.trim().toLowerCase();
  for(const option of cycle.options){const c=tracking.cycles.find(c=>c.id===option.value);option.hidden=!!c&&c.start>snapshot.date;if(c)option.textContent=cycleName(c.id);}
  if(cycle.selectedOptions[0]?.hidden)cycle.value='all';
  const matches=snapshot.rows.filter(r=>(pool.value==='all'||(pool.value==='exited'?['已退出','候选未通过'].includes(r.last_known_status||r.status):watching(r)&&(pool.value==='watch'||(pool.value==='unknown'?!r.evidence_ok:r.pool===pool.value))))&&(cycle.value==='all'||r.first_cycle_id===cycle.value||r.first_seen_cycle_id===cycle.value||r.matched_cycle_id===cycle.value)&&(r.name+' '+r.code).toLowerCase().includes(query));
  const watch=snapshot.rows.filter(watching),counts=key=>watch.filter(r=>r.pool===key).length,unknown=watch.filter(r=>!r.evidence_ok).length;
  document.getElementById('tracking-summary').textContent=snapshot.date+' · 跟踪/待确认：新 '+counts('new')+' / 旧 '+counts('old')+' / 边界 '+counts('boundary')+' · 其中缺数 '+unknown+' · 显示 '+matches.length+' · 历史留档 '+snapshot.rows.length+'（非当前名单）'+(!snapshot.coverage_ok?'；截面不足，身份保留但当前资格未知':'；当日满足 '+snapshot.qualified_count+'/'+snapshot.candidate_count+' 候选，只确认价格条件');
  for(const r of matches){
   const tr=document.createElement('tr');tr.dataset.code=r.code;tr.dataset.pool=r.pool;tr.dataset.status=r.status;body.appendChild(tr);
   const cell=(text,sub)=>{const td=document.createElement('td');if(text!==undefined)td.textContent=text;if(sub){const small=document.createElement('small');small.textContent=sub;td.appendChild(small);}tr.appendChild(td);return td;};
   const name=cell(),button=document.createElement('button');button.type='button';button.className='tracking-name';button.textContent=r.name;name.appendChild(button);const code=document.createElement('small');code.textContent=r.code;name.appendChild(code);
   button.addEventListener('click',()=>{const select=document.getElementById('stock-select');select.value=r.code;draw(snapshot.date);document.getElementById('price-chart').scrollIntoView({block:'center'});});
   const status=cell(r.identity+' / '+r.status,!r.evidence_ok?'最后已知：'+(r.last_known_status||'—'):null);if(!r.evidence_ok)status.className='status-unknown';
   cell('首见 '+r.first_seen,'入池 '+(r.first_admitted||'尚未确认'));
   cell(cycleName(r.first_cycle_id), '最近匹配 '+cycleName(r.matched_cycle_id));
   cell(r.support_streak+'日连续 / '+r.episode_support_days+'日本轮累计','连续不满足 '+r.failed_streak+'日');
   cell(['r1','r5','r20'].map(k=>fmt(r.metrics[k])).join(' / '));
   cell(fmt(r.metrics.dd20),'证据 '+(r.metrics_as_of||'—')+(!r.evidence_ok?'（非当前）':''));
   const trail=cell(),strip=document.createElement('div');strip.className='tracking-history';trail.appendChild(strip);
   for(const d of tracking.daily.slice(Math.max(0,index-4),index+1)){const past=d.rows.find(p=>p.code===r.code),chip=document.createElement('span');chip.textContent=d.date.slice(5);chip.title=d.date+' '+(past?past.status:'尚未首见');chip.className=!past||!past.evidence_ok?'unknown':['已退出','候选未通过','分歧观察'].includes(past.status)?'risk':past.pool==='old'?'old':'';strip.appendChild(chip);}
   const details=cell(),note=document.createElement('div');note.textContent=r.reason;details.appendChild(note);
   const stock=stocks.get(r.code),events=stock?stock.events.filter(e=>e.date<=snapshot.date):[];
   const disclosure=document.createElement('details'),summary=document.createElement('summary');summary.textContent='变更记录 '+events.length+' 条';disclosure.appendChild(summary);
   for(const event of events){const p=document.createElement('p');p.className='tiny muted';p.textContent=event.date+' '+event.status+' · '+event.reason;disclosure.appendChild(p);}details.appendChild(disclosure);
  }
  if(!matches.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=9;td.textContent='当前日期或筛选无匹配；可切换日期/分组。没有合格候选时不以历史名单补位。';tr.appendChild(td);body.appendChild(tr);}
 }
 for(const control of [dates,pool,cycle])control.addEventListener('change',renderTracking);search.addEventListener('input',renderTracking);renderTracking();
}
'''
