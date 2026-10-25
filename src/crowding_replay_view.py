"""Offline interactive classified screening replay, separate from formal tracking."""
from __future__ import annotations

import html
from crowding_replay import POOL_NAMES, audit_stock

REPLAY_CSS = """
.replay-controls{display:flex;flex-wrap:wrap;gap:12px;align-items:end}.replay-controls label{display:grid;gap:5px;font-size:13px;color:#b7c8da}.replay-controls input,.replay-controls select{max-width:100%;min-height:38px;background:#111e2d;color:#eef4fa;border:1px solid #48617b;border-radius:6px;padding:7px}.replay-controls button,.replay-link{background:#15384a;color:#b7eee5;border:1px solid #357768;border-radius:6px;padding:8px;cursor:pointer}.replay-controls button:disabled{opacity:.4;cursor:default}.replay-link{text-align:left}.replay-rules{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}.replay-rules article{border-top:3px solid #69baaa}.replay-rules h3{font-size:16px}.replay-rules p{overflow-wrap:anywhere}.replay-audit{margin-top:20px;border-left:4px solid #69baaa}.replay-pass{color:#9cddc5}.replay-fail{color:#f0a8a3}.replay-unknown{color:#dfc78c}.replay-chart{width:100%;height:200px}.replay-controls :focus-visible,.replay-link:focus-visible{outline:3px solid #f0cc7e;outline-offset:3px}#screening-replay{scroll-margin-top:80px}#replay-history{max-height:330px;overflow:auto}@media(max-width:900px){.replay-rules{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:520px){.replay-rules{grid-template-columns:1fr}.replay-controls label{width:100%}.replay-controls input,.replay-controls select{width:100%}}
"""


def render_replay_section(replay):
    if not replay or not replay.get('windows'):
        return '<section id="screening-replay"><h2>分类筛选 · 每日推演</h2><p>没有全市场价格窗口，暂不能验选。</p></section>'
    window = replay['windows'].get('2') or next(iter(replay['windows'].values()))
    latest = window['days'][-1]
    def esc(x):
        return html.escape(str(x), quote=True)
    options = ''.join(f'<option value="{d["date"]}">{d["date"]}</option>' for d in reversed(window['days']))
    selected_rows = ''
    for code in latest['selected']:
        a = audit_stock(replay, window['months'], latest['date'], code)
        selected_rows += f'<tr><td>{esc(a["name"])} / {esc(code)}</td><td>{esc(POOL_NAMES[a["pool"]])}</td><td>{esc(a["policy"]["rule_id"])}</td><td>{a["support"]}</td></tr>'
    return f'''<section id="screening-replay"><div class="section-head"><h2>分类筛选 · 每日推演</h2><span class="badge old">两个月 / 半年 · 收盘条件研究</span></div>
<p class="muted">从窗口起点逐日正向重建状态，再在页面倒退查验。新生、老团、修复、边界存量分别比较8组条件；只使用观察日前已到期的5日价格标签。它不是用今天的赢家名单倒填历史，也不是自动买卖信号。</p>
<div class="panel"><div class="replay-controls"><label>研究窗口<select id="replay-window"><option value="2">最近两个月</option><option value="6">最近半年</option></select></label><label>观察日（收盘后）<select id="replay-date">{options}</select></label><button id="replay-prev" type="button">← 倒退一天</button><button id="replay-next" type="button">前进一天 →</button><label>分类池<select id="replay-pool"><option value="all">全部分类</option>{''.join(f'<option value="{k}">{v}</option>' for k,v in POOL_NAMES.items())}</select></label><label>查任意个股（含从未入选）<input id="replay-search" placeholder="名称 / 6位代码" aria-label="查任意个股"></label><label>审核个股<select id="replay-stock" aria-label="审核个股"></select></label><button id="replay-first" type="button">倒退到首次可选日</button></div><p id="replay-coverage" class="tiny muted" aria-live="polite">{latest['date']} · {window['observations']}个观察日</p></div>
<div id="replay-rules" class="replay-rules"></div><div class="panel"><h3 id="replay-list-title">当日价格条件可选 · {len(latest['selected'])}只</h3><p class="tiny muted">每日资格不是新增买入次数。各组条件独立维护确认状态，切换条件不等于可成交组合持仓；研究池不改写上方正式动态追踪表。</p><div class="table-scroll"><table><thead><tr><th>个股 / 代码</th><th>当日分类</th><th>当日研究条件</th><th>连续支持</th></tr></thead><tbody id="replay-list">{selected_rows}</tbody></table></div></div>
<div class="panel replay-audit"><h3 id="replay-audit-title">个股逐项审核</h3><p id="replay-audit-summary" aria-live="polite"></p><div id="replay-gates"></div><svg id="replay-chart" class="replay-chart" viewBox="0 0 900 190" role="img" aria-label="只显示到观察日的个股价格"></svg><p id="replay-chart-caption" class="tiny muted"></p><details><summary>事后5日标签（不参与当日条件；非成交盈亏）</summary><p id="replay-outcome"></p></details><details><summary>该股逐日资格与不通过项 · 可点日期倒退</summary><div id="replay-history" class="table-scroll"></div></details></div>
<details class="panel"><summary>窗口末20%冻结检验 · 与原条件对照（仅价格标签）</summary><p id="replay-validation-note" class="tiny muted"></p><div id="replay-validation"></div><p class="tiny muted">前80%时段选择参数，标签必须在冻结日之前成熟；末20%不重新择优。下表不是交易胜率，同股多次入池和同期行情相关，不能据此认定统计显著。缺失或未满5日样本不计通过率。</p></details>
<details class="panel"><summary>所有预声明条件、样本边界与自动选择方法</summary><p>候选排名按当天全量股票截面计算，同分全保留；8组参数逐日独立模拟。每分类池至少20个成熟入池事件且覆盖6个不同入池日，才按价格标签通过率的Wilson保守下界作描述性排序；不足时回退原条件。不是每日强制换参数。</p><div id="replay-hypotheses"></div><p class="tiny muted">标签定义：{esc(replay['label']['description'])}。{esc(replay['limitation'])}</p><p class="tiny muted">半年参照周期由每日核心代理换位自动分段；上方历史截图案例不参与筛选训练。边界存量不声称窗口内新生；窗口外首次出生不明。每日版本是本次缓存重建版本，不是当时真实发布快照。</p></details></section>'''


REPLAY_SCRIPT = r"""
(()=>{
const R=data.screening_replay;if(!R?.windows||!Object.keys(R.windows).length)return;
const $=id=>document.getElementById('replay-'+id), P={new:'新生 / 新团',old:'老团延续',repair:'旧团修复 / 再验证',boundary:'窗口边界存量'};
const escape=x=>String(x??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])), f=(x,d=2)=>x==null?'—':Number(x).toFixed(d), pc=x=>x==null?'—':f(x*100)+'%';
const H=Object.fromEntries(R.hypotheses.map(h=>[h.id,h]));let W, D, code;
const ruleText=r=>`5日>${r.min_r5}% / 20日>${r.min_r20}% · 近5日前15%至少${r.min_persistence}日 · 候选前${f(r.top_fraction*100,0)}% · ≥MA5 · 回撤≤${r.max_drawdown_pct}% · ${r.admission_days}日确认 / ${r.exit_days}日退出`;
const table=(headers,rows)=>'<div class="table-scroll"><table><thead><tr>'+headers.map(h=>'<th>'+escape(h)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map(v=>'<td>'+v+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
function audit(c,d){
 const pool=d.pools[c]||'new',policy=d.choices[pool],rule=H[policy.rule_id].rules,i=R.dates.indexOf(d.date),v=R.prices[c],seq=v.slice(Math.max(0,i-20),i+1),evidence=seq.length===21&&seq.every(x=>x!=null&&x>0),m={r5:null,r20:null,dd:null,p:null,ma:null};
 if(evidence){m.r5=(v[i]/v[i-5]-1)*100;m.r20=(v[i]/v[i-20]-1)*100;m.dd=(v[i]/Math.max(...v.slice(i-19,i+1))-1)*100;m.p=[...R.top15[c].slice(i-4,i+1)].filter(x=>x==='1').length;const ma=v.slice(i-4,i+1).reduce((a,b)=>a+b,0)/5;m.ma=v[i]>=ma||Math.abs(v[i]-ma)<=1e-12+1e-12*Math.abs(ma);}
 const selected=Object.hasOwn(d.selected,c),support=d.support[c]||0,cutoff=d.cutoffs[policy.rule_id].cutoff,score=R.scores[d.date]?.[c];
 const gates=[['当日全市场截面覆盖',d.coverage_ok],['个股连续21个交易日价格完整',evidence],['5日收益门槛',m.r5>rule.min_r5],['20日收益门槛',m.r20>rule.min_r20],['近5日前15%持续性',m.p>=rule.min_persistence],['候选相对领先排名',score!=null&&cutoff!=null&&score>=cutoff],['收盘不低于MA5',m.ma===true],['20日高点回撤',m.dd!=null&&m.dd>=-rule.max_drawdown_pct],['连续确认 / 已在池后继续达标',selected||support>=rule.admission_days]].map(([label,pass],n)=>({label,pass:n>1&&(!evidence||!d.coverage_ok)?null:pass}));
 return {pool,policy,rule,i,v,evidence,m,selected,support,gates,status:!evidence||!d.coverage_ok?'数据不足 / 不能确认':selected?'当日可选中（价格条件）':'当日未选中'};
}
function chooseStocks(){
 const q=$('search').value.trim().toLowerCase(),matches=Object.keys(R.prices).filter(c=>!q||c.includes(q)||(R.names[c]||'').toLowerCase().includes(q));
 const selected=Object.keys(D.selected);matches.sort((a,b)=>(selected.includes(b)?1:0)-(selected.includes(a)?1:0)||a.localeCompare(b));
 const showing=matches.slice(0,100);if(code&&matches.includes(code)&&!showing.includes(code))showing.unshift(code);
 $('stock').innerHTML=showing.map(c=>`<option value="${escape(c)}">${escape(R.names[c])} · ${escape(c)}</option>`).join('');
 if(!matches.includes(code))code=showing[0];if(code)$('stock').value=code;
}
function chart(a){
 const rows=a.v.map((v,i)=>({v,date:R.dates[i]})).filter(r=>r.date>=W.requested_start&&r.date<=D.date),good=rows.filter(r=>r.v!=null&&r.v>0),svg=$('chart');svg.replaceChildren();
 if(!good.length){$('chart-caption').textContent='观察日前没有可用价格';return;}
 const lo=Math.min(...good.map(r=>r.v)),hi=Math.max(...good.map(r=>r.v)),span=hi-lo||1;let path='',open=false;
 rows.forEach((r,i)=>{if(r.v==null||r.v<=0){open=false;return;}const x=20+860*i/Math.max(rows.length-1,1),y=165-140*(r.v-lo)/span;path+=(open?'L':'M')+x.toFixed(1)+','+y.toFixed(1)+' ';open=true;});
 const el=document.createElementNS('http://www.w3.org/2000/svg','path');el.setAttribute('d',path);el.setAttribute('stroke','#7bd5bd');el.setAttribute('stroke-width','2');el.setAttribute('fill','none');svg.appendChild(el);
 $('chart-caption').textContent=`${good[0].date}—${D.date} · 只显示至观察日 · 前复权缓存、缺价断线，不是下单价`;
}
function renderAudit(){
 if(!code){$('audit-title').textContent='没有匹配个股';$('audit-summary').textContent='请更换名称或代码；不会只搜索历史赢家。';$('gates').innerHTML='';$('history').innerHTML='';$('chart').replaceChildren();$('chart-caption').textContent='没有可审核个股';$('outcome').textContent='没有可审核个股';$('first').disabled=true;return;}
 const a=audit(code,D);$('audit-title').textContent=`${R.names[code]} / ${code} · ${D.date}`;
 $('audit-summary').textContent=`${a.status} · ${P[a.pool]} · 5日 ${f(a.m.r5)}% / 20日 ${f(a.m.r20)}% / 回撤 ${f(a.m.dd)}% · 连续支持${a.support}日 · ${H[a.policy.rule_id].title}`;
 $('gates').innerHTML=table(['条件','审核结果'],a.gates.map(g=>[escape(g.label),`<span class="replay-${g.pass==null?'unknown':g.pass?'pass':'fail'}">${g.pass==null?'未知':g.pass?'通过':'不通过'}</span>`]));
 chart(a);const end=a.i+R.label.horizon,seq=a.v.slice(a.i,end+1);let outcome;
 if(end>=R.dates.length)outcome='尚未满5个交易日，不统计。';else if(!R.complete.slice(a.i,end+1).every(Boolean)||seq.some(x=>x==null||x<=0))outcome='后续窗口缺数，不纳入标签统计。';else{const returns=seq.slice(1).map(x=>(x/seq[0]-1)*100),ret=returns.at(-1),low=Math.min(...returns);outcome=`仅在 ${R.dates[end]} 收盘后才可知：5日收益 ${f(ret)}%，期间最低 ${f(low)}%；${ret>=R.label.min_return_pct&&low>=-R.label.max_adverse_pct?'符合':'不符合'}研究标签。此标签未参与 ${D.date} 当日条件选择。`;}
 $('outcome').textContent=outcome;
 const history=W.days.filter(d=>d.date<=D.date).map(d=>{const x=audit(code,d);return [`<button class="replay-link" data-replay-date="${d.date}">${d.date}</button>`,escape(P[x.pool]),escape(x.status),escape(x.gates.filter(g=>g.pass!==true).map(g=>g.label+(g.pass==null?'（未知）':'')).join('；')||'全部通过')];});
 $('history').innerHTML=table(['观察日','当日分类','资格','未通过 / 未知'],history.reverse());$('first').disabled=!W.days.some(d=>d.date<=D.date&&Object.hasOwn(d.selected,code));
}
function render(){
 D=W.days.find(d=>d.date===$('date').value)||W.days.at(-1);$('date').value=D.date;
 const pos=W.days.indexOf(D);$('prev').disabled=pos===0;$('next').disabled=pos===W.days.length-1;
 $('coverage').textContent=`请求起点 ${W.requested_start} / 实际首观察 ${W.actual_start} · ${W.observations}日 / 完整截面${W.complete_days}日 · ${W.missing_prefix?'起点历史不足，不能视为完整窗口':'含窗口前指标预热'} · 当日截面${D.coverage_ok?'可用':'不足，禁止确认'} · 核心代理段 ${D.cycle_id||'无'} · 版本 ${D.version}`;
 $('rules').innerHTML=Object.entries(D.choices).map(([pool,c])=>`<article class="panel"><h3>${P[pool]}</h3><b>${escape(H[c.rule_id].title)}</b><p class="tiny">${ruleText(H[c.rule_id].rules)}</p><p class="tiny muted">${escape(c.status)} · 成熟${c.samples}例 / ${c.entry_days}个入池日 · 标签截至 ${escape(c.latest_label_date)}（严格早于${D.date}） · 描述性标签通过 ${pc(c.label_rate)}</p></article>`).join('');
 const pool=$('pool').value,q=$('search').value.trim().toLowerCase(),list=Object.keys(D.selected).filter(c=>(pool==='all'||(D.pools[c]||'new')===pool)&&(!q||c.includes(q)||(R.names[c]||'').toLowerCase().includes(q)));
 $('list-title').textContent=`${D.date} · 价格条件可选 ${list.length}只 / 全分类${Object.keys(D.selected).length}只`;
 $('list').innerHTML=list.map(c=>{const x=audit(c,D);return `<tr><td><button class="replay-link" data-replay-code="${escape(c)}">${escape(R.names[c])} / ${escape(c)}</button></td><td>${P[x.pool]}</td><td>${escape(H[x.policy.rule_id].title)}</td><td>${x.support}日</td></tr>`;}).join('')||'<tr><td colspan="4">本日 / 本筛选没有可选个股；可查任意代码查看不通过项。</td></tr>';
 $('validation-note').textContent=`冻结起点 ${W.holdout_start||'窗口不足'} · 使用严格早于冻结日起已到期标签择优 · 半年参照核心代理 ${W.cycles.length}段`;
 $('validation').innerHTML=table(['分类','冻结条件','该条件成熟 / 待定','价格标签通过','原条件成熟 / 待定','原条件标签通过'],Object.entries(W.validation).map(([p,v])=>[P[p],escape(H[v.policy.rule_id].title),`${v.samples} / ${v.pending}`,pc(v.label_rate),`${v.baseline.samples} / ${v.baseline.pending}`,pc(v.baseline.label_rate)]));
 chooseStocks();renderAudit();
}
function changeWindow(){W=R.windows[$('window').value];if(!W)return;$('date').innerHTML=W.days.slice().reverse().map(d=>`<option value="${d.date}">${d.date}</option>`).join('');render();}
$('window').addEventListener('change',changeWindow);$('date').addEventListener('change',render);$('pool').addEventListener('change',render);$('search').addEventListener('input',render);$('stock').addEventListener('change',()=>{code=$('stock').value;renderAudit();});
$('prev').addEventListener('click',()=>{$('date').value=W.days[W.days.indexOf(D)-1].date;render();});$('next').addEventListener('click',()=>{$('date').value=W.days[W.days.indexOf(D)+1].date;render();});
$('first').addEventListener('click',()=>{const first=W.days.find(d=>d.date<=D.date&&Object.hasOwn(d.selected,code));if(first){$('date').value=first.date;render();}});
$('list').addEventListener('click',event=>{const b=event.target.closest('[data-replay-code]');if(!b)return;code=b.dataset.replayCode;$('search').value=code;render();$('audit-title').scrollIntoView({block:'center'});});
$('history').addEventListener('click',event=>{const b=event.target.closest('[data-replay-date]');if(b){$('date').value=b.dataset.replayDate;render();}});
$('hypotheses').innerHTML=table(['研究假设','价格条件'],R.hypotheses.map(h=>[escape(h.title),escape(ruleText(h.rules))]));changeWindow();
})();
"""
