"""Render actionable research plans without making or changing trading decisions.

The caller owns screening, price arithmetic, maturity checks and decision counts.
This module only presents those supplied facts, including empty candidate lists.
All percentages are already percentage points; no unit conversion is performed.
"""
from __future__ import annotations

from html import escape
import math
from pathlib import Path
from urllib.parse import urlsplit
from playbook_status import plan_status, trigger_status


DEFAULT_FILES = {
    "abnormal": "abnormal_playbook.html",
    "stock": "stock_baotuan_playbook.html",
    "cb": "cb_baotuan_playbook.html",
}
KIND_NAMES = {"abnormal": "异动与二波", "stock": "股票抱团", "cb": "转债抱团"}
LABELS = {
    "label": "分组", "n": "事件数", "n5": "5日完整样本", "n10": "10日完整样本",
    "median5": "5日收益中位数（%）", "median10": "10日收益中位数（%）",
    "win_count": "上涨样本", "loss_count": "下跌样本", "notes": "说明",
    "code": "代码", "name": "名称", "reason": "原因", "date": "日期",
    "as_of": "数据截至", "setup": "形态", "tier": "层级", "rule": "规则",
    "title": "名称", "trigger": "触发条件", "exit": "退出条件",
    "entry": "入场条件", "invalidation": "失效条件", "evidence": "依据",
    "market_summary": "市场环境", "data_quality": "数据质量", "conditions": "逐项确认条件",
    "source": "来源", "status": "状态", "price_as_of": "价格日期",
    "candidate_count": "候选数", "complete": "完整", "missing": "缺失项",
    "sample_size": "样本数", "win_rate": "上涨比例（%）", "volume_ratio": "量比",
    "reference_price": "参考收盘", "close": "收盘", "count": "数量",
    "market_risk": "市场状态", "market_breadth_pct": "上涨家数占比（%）",
    "limit_up": "涨停家数", "limit_down": "跌停家数", "max_height": "最高连板",
    "market_date": "市场数据日期", "market_scope": "市场口径", "quote_date": "行情日期",
    "returned": "返回数量", "dated_active": "同日有效交易数", "complete_pagination": "分页完整",
    "amount_yi": "成交额（亿元）", "top5_amount_share_pct": "成交额前5占比（%）",
    "declining": "下跌数", "rising": "上涨数", "history_bonds": "历史债券数",
    "history_events": "历史事件数", "mature_events": "完整后验事件数",
    "horizon": "后验交易日", "group": "分组", "median_pct": "中位收益（%）",
    "q25_pct": "25%分位收益（%）", "q75_pct": "75%分位收益（%）",
    "positive_path_fraction": "上涨路径比例", "path": "走势过程",
    "forward_5d_return_pct": "后5日收益（%）", "announcement_knowledge_date": "公告已知截至",
    "raw_price_basis": "价格口径", "errors": "数据缺口", "refresh_requested": "本次联网刷新",
    "history_range": "历史覆盖区间", "execution_backtest": "是否完成可成交回测",
    "intraday_confirmed": "盘中条件已确认", "passed": "已通过", "raw_dir": "证据目录",
    "recheck": "重新评估条件", "pct_change": "日涨跌（%）", "stock_name": "正股",
    "premium_pct": "转股溢价（%）", "blocking_gates": "仍被拦截的条件",
    "detailed_bonds": "逐只细核数量", "amount_rank": "成交额排名",
}


def _text(value) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.4f}".rstrip('0').rstrip('.')
    if isinstance(value, (list, tuple)):
        return "；".join(_text(x) for x in value) or "—"
    if isinstance(value, dict):
        return "；".join(f"{LABELS.get(k, k)}：{_text(v)}" for k, v in value.items()) or "—"
    return str(value)


def _e(value) -> str:
    return escape(_text(value), quote=True)


def _rich(value) -> str:
    if isinstance(value, (list, tuple)):
        return "<ul>" + "".join(f"<li>{_e(x)}</li>" for x in value) + "</ul>" if value else "<p>—</p>"
    return f"<p>{_e(value)}</p>"


def _num(value, suffix="", digits=2) -> str:
    if value is None or isinstance(value, bool):
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _text(value)
    return f"{number:.{digits}f}{suffix}" if math.isfinite(number) else "—"


def _safe_href(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if any(ord(c) < 32 for c in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in ("", "http", "https") or (not parsed.scheme and parsed.netloc):
        return None
    return value


def _link(label, href) -> str:
    safe = _safe_href(href)
    return f'<a href="{_e(safe)}">{_e(label)}</a>' if safe else _e(label)


def _tier(value) -> tuple[str, str]:
    original = _text(value)
    if original in ("priority", "优先", "优先观察", "条件优先"):
        return "priority", "优先候选"
    if original in ("avoid", "回避", "剔除"):
        return "avoid", "回避"
    if original in ("watch", "观察", "等待", "待确认"):
        return "watch", "观察"
    return "other", original


def _filename(value) -> str:
    name = str(value)
    if Path(name).name != name or "/" in name or "\\" in name or name in ("", ".", ".."):
        raise ValueError("filename must be a file name inside the output directory")
    return str(Path(name).with_suffix(".html"))


def _md(value) -> str:
    return _text(value).replace("|", "／").replace("\r", "").replace("\n", " ")


def _table(rows: list[dict]) -> tuple[str, str]:
    if not rows:
        return '<p class="empty">本次没有满足口径的记录。</p>', "本次没有满足口径的记录。\n\n"
    normalized = [r if isinstance(r, dict) else {"notes": r} for r in rows]
    keys = list(dict.fromkeys(k for r in normalized for k in r))
    html = '<div class="table-wrap" tabindex="0"><table><thead><tr>'
    html += "".join(f'<th scope="col">{_e(LABELS.get(k, k))}</th>' for k in keys) + "</tr></thead><tbody>"
    md = "|" + "|".join(_md(LABELS.get(k, k)) for k in keys) + "|\n|" + "|".join("---" for _ in keys) + "|\n"
    for row in normalized:
        html += "<tr>" + "".join(f"<td>{_e(row.get(k))}</td>" for k in keys) + "</tr>"
        md += "|" + "|".join(_md(row.get(k)) for k in keys) + "|\n"
    return html + "</tbody></table></div>", md + "\n"


STYLE = r"""
:root{--paper:#fff;--bg:#f3f6fa;--ink:#18283b;--muted:#53687c;--blue:#315f87;--teal:#278575;--red:#bc4754;--line:#dce4ed;--soft:#edf3f8}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:var(--ink);background:var(--bg);font:15px/1.7 "Microsoft YaHei","Segoe UI",sans-serif}main{max-width:1280px;margin:auto;padding:26px 28px 60px}h1,h2,h3,p{margin:0}h1{font:700 34px/1.35 "SimSun","Songti SC",serif;margin:12px 0}h2{font-size:22px;margin:0 0 14px}h3{font-size:19px;line-height:1.4}p+p{margin-top:8px}a{color:var(--blue);text-underline-offset:3px}p,li,dd,td,a{overflow-wrap:anywhere}ul{margin:7px 0;padding-left:22px}li+li{margin-top:5px}.eyebrow{font:12px/1.5 Consolas,"Microsoft YaHei",monospace;color:var(--blue);letter-spacing:1px}.subtle{color:var(--muted);font-size:13px}.report-nav{display:flex;flex-wrap:wrap;gap:7px 18px;border-bottom:1px solid var(--line);padding:10px 0 13px}.report-nav a{font-size:13px;text-decoration:none}.clock{display:flex;gap:14px 28px;flex-wrap:wrap;margin:16px 0 21px;font-size:12px;color:var(--muted)}.clock b{display:block;color:var(--ink);font:14px/1.6 Consolas,"Microsoft YaHei",monospace}.decision{background:var(--paper);border:1px solid var(--line);border-left:5px solid var(--blue);border-radius:8px;padding:23px 25px}.decision .lead{font-size:19px;font-weight:600;line-height:1.7}.decision .lead ul{font-weight:500;font-size:17px}.counts{display:flex;gap:24px;flex-wrap:wrap;border-top:1px solid var(--line);margin-top:18px;padding-top:14px}.counts div{font-size:12px;color:var(--muted)}.counts b{font:700 25px/1.4 Consolas,"Microsoft YaHei",monospace;color:var(--ink);margin-right:5px}.shortlist{margin-top:18px;display:grid;gap:9px}.shortlist div{display:grid;grid-template-columns:90px 1fr;gap:12px;font-size:13px}.shortlist strong{font-weight:500;color:var(--muted)}section{margin-top:30px;scroll-margin-top:18px;min-width:0}.section-head{display:flex;justify-content:space-between;align-items:baseline;gap:18px;flex-wrap:wrap}.filters{display:flex;gap:14px;align-items:end;flex-wrap:wrap;margin:0 0 18px}.filters label{display:grid;gap:5px;color:var(--muted);font-size:12px}input,select,button{font:14px "Microsoft YaHei",sans-serif;color:var(--ink);border:1px solid #aabbcb;background:white;border-radius:5px;padding:9px 12px;min-height:40px}input{width:240px}button{cursor:pointer}.filter-count{color:var(--muted);font-size:12px;padding-bottom:10px}.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.candidate{background:white;border:1px solid var(--line);border-radius:9px;min-width:0;overflow:hidden}.card-top{padding:19px 21px 15px;border-bottom:1px solid var(--line)}.card-title{display:flex;justify-content:space-between;gap:12px;align-items:start}.identity{min-width:0}.code{font:12px/1.6 Consolas,monospace;color:var(--muted)}.badge{font-size:12px;white-space:nowrap;border-radius:4px;background:var(--soft);color:var(--blue);padding:3px 8px}.badge.priority{background:#eaf5f2;color:#236e61}.badge.avoid{background:#fbedef;color:#a63d49}.setup{margin:10px 0 5px;font-size:16px;font-weight:600}.why{font-size:13px;color:var(--muted)}.status{font-size:12px;margin-top:8px;color:var(--blue)}.actions{padding:17px 21px;display:grid;gap:10px}.action{border-left:3px solid var(--blue);background:#f3f7fb;padding:10px 13px;font-size:13px}.action.enter{border-color:var(--teal);background:#eff8f5}.action.exit{border-color:var(--red);background:#fff3f4}.action strong{display:block;font-size:12px;margin-bottom:4px}.action p{line-height:1.65}.levels{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;padding:0 21px 18px;margin:0}.levels div{min-width:0}.levels dt{color:var(--muted);font-size:11px}.levels dd{margin:4px 0 0;font:600 15px/1.5 Consolas,"Microsoft YaHei",monospace}.scenarios{border-top:1px solid var(--line);padding:15px 21px;font-size:13px}.scenarios>p{font-size:12px;color:var(--muted);margin-bottom:8px}.scenario{display:grid;grid-template-columns:55px 1fr;gap:10px;margin:8px 0}.scenario b{font-size:12px;color:var(--blue)}.candidate details{margin:0 21px 16px;font-size:12px}.candidate summary{font-size:12px}.evidence{padding:12px 0}.evidence h4{margin:10px 0 4px;font-size:12px}.holding{font-size:13px;padding:0 21px 17px}.holding strong{color:var(--muted);font-size:12px}.empty{background:white;border:1px dashed #aabbcb;border-radius:7px;padding:20px;color:var(--muted)}.research{background:white;border:1px solid var(--line);border-radius:7px;padding:0 20px}.research>details+details{border-top:1px solid var(--line)}details{padding:14px 0}summary{cursor:pointer;color:var(--blue);font-weight:600}.details-body{margin-top:15px}.table-wrap{max-width:100%;overflow:auto;border:1px solid var(--line);border-radius:4px;margin:12px 0}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:9px 11px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line);min-width:80px}th{background:var(--soft);color:var(--muted);white-space:nowrap}td:last-child{min-width:170px}.source-list{padding-left:22px;font-size:12px}.sources{font-size:12px}.limitations{font-size:13px;color:var(--muted)}.rule{border-left:3px solid var(--blue);padding:10px 15px;margin-bottom:13px;background:#f5f8fb}.rule h3{font-size:15px;margin-bottom:6px}.rule dl{font-size:13px;margin:0}.rule dt{font-weight:600;margin-top:6px}.rule dd{margin:2px 0 0}.index-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin:25px 0}.index-card{background:white;border:1px solid var(--line);border-radius:8px;padding:23px;display:flex;flex-direction:column;gap:14px;min-width:0}.index-card h2{font:700 24px/1.4 "SimSun","Songti SC",serif;margin:0}.index-card .open{align-self:start;margin-top:auto;border-bottom:2px solid var(--blue);padding:6px 0;text-decoration:none}.index-card .shortlist div{grid-template-columns:70px 1fr}.index-card .lead{font-size:14px}.index-card .counts{gap:12px}.index-card .counts b{font-size:21px}footer{margin-top:30px;border-top:1px solid var(--line);padding-top:14px;color:var(--muted);font-size:12px}[hidden]{display:none!important}:focus-visible{outline:3px solid var(--blue);outline-offset:3px}
@media(max-width:960px){.index-grid{grid-template-columns:1fr}.index-card{gap:12px}.index-card .shortlist{margin:0}.cards{grid-template-columns:1fr}}
.cards>.candidate:only-child{grid-column:1/-1}
@media(max-width:480px){main{padding:18px 12px 40px}h1{font-size:28px}.decision{padding:18px 15px}.decision .lead{font-size:16px}.decision .lead ul{font-size:15px}.counts{gap:10px 18px}.counts b{font-size:21px}.card-top,.actions,.scenarios{padding-left:16px;padding-right:16px}.levels{padding-left:16px;padding-right:16px;grid-template-columns:repeat(2,minmax(0,1fr))}.filters label{flex:1 1 130px}input,select{width:100%;max-width:100%}.filter-count{flex-basis:100%;padding:0}.card-title{gap:8px}h3{font-size:18px}.badge{font-size:11px}.shortlist div{grid-template-columns:72px 1fr}.research{padding:0 13px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
@media print{body{background:white}main{max-width:none;padding:0}.report-nav,.filters,.print-button{display:none}.cards,.index-grid{display:block}.candidate,.index-card{break-inside:avoid;margin:14px 0}.candidate[hidden]{display:block!important}.decision{border-color:#bbb}.table-wrap{overflow:visible}table{table-layout:fixed;font-size:9px}th,td{min-width:0;white-space:normal;padding:5px;overflow-wrap:anywhere}.research{border:0;padding:0}details>summary{list-style:none}details>.details-body,details>.evidence{display:block!important}.clock{gap:15px}.levels{grid-template-columns:repeat(3,minmax(0,1fr))}a{color:inherit;text-decoration:none}}
"""


SCRIPT = r"""
(()=>{
 const search=document.getElementById('stock-search'), tier=document.getElementById('tier-filter');
 const cards=Array.from(document.querySelectorAll('[data-candidate]'));
 const count=document.getElementById('filter-count'), empty=document.getElementById('filter-empty');
 function filter(){
  const q=(search?.value||'').trim().toLowerCase(), t=tier?.value||'all';
  let n=0; cards.forEach(card=>{const show=(!q||card.dataset.search.toLowerCase().includes(q))&&(t==='all'||card.dataset.tier===t);card.hidden=!show;if(show)n++;});
  if(count)count.textContent=`显示 ${n} / ${cards.length} 个标的`;if(empty)empty.hidden=n!==0;
 }
 search?.addEventListener('input',filter);tier?.addEventListener('change',filter);filter();
 document.querySelectorAll('[data-list-search]').forEach(input=>{
  input.addEventListener('input',()=>{
   const q=input.value.trim().toLowerCase();
   const rows=document.querySelectorAll(`#${input.dataset.listSearch} tbody tr`);
   rows.forEach(row=>row.hidden=!row.textContent.toLowerCase().includes(q));
  });
 });
 const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 const hour=Number(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',hour:'2-digit',hourCycle:'h23'}).format(new Date()));
 document.querySelectorAll('[data-plan-date]').forEach(el=>{
  const day=el.dataset.planDate;
  if(day && (today>day || (today===day && hour>=15))) el.textContent=`${day}计划日已结束；以下为历史条件，不能沿用为下一交易日买单。盘中结果见复盘核验状态。`;
 });
 document.getElementById('print-report')?.addEventListener('click',()=>window.print());
 let closed=[];window.addEventListener('beforeprint',()=>{closed=Array.from(document.querySelectorAll('details:not([open])'));closed.forEach(d=>d.open=true);});
 window.addEventListener('afterprint',()=>{closed.forEach(d=>d.open=false);closed=[];});
})();
"""


def _document(title: str, body: str) -> str:
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_e(title)}</title><style>{STYLE}</style></head><body><main>{body}'
            f'</main><script>{SCRIPT}</script></body></html>')


def _counts(report: dict) -> str:
    supplied = report.get("decision_counts", {})
    labels = (("priority", "筛选优先候选"), ("watch", "等待观察"), ("avoid", "回避"))
    count, status = trigger_status(report)
    return '<div class="counts">' + "".join(
        f'<div><b>{_e(supplied.get(k))}</b>{label}</div>' for k, label in labels
    ) + f'<div><b>{_e(count)}</b>{_e(status)}</div></div>'


def _status_banner(report):
    return ('<section class="decision"><p data-plan-date="' + _e(report.get('plan_date')) + '">'
            + _e(plan_status(report)) + '</p>'
            + ('<p class="subtle">' + _e(report['revision_note']) + '</p>' if report.get('revision_note') else '')
            + '</section>')


def _compact_rows(rows):
    keys = ('name', 'code', 'tier', 'stock_name', 'pct_change', 'reason', 'recheck')
    return [{key: row[key] for key in keys if key in row} if isinstance(row, dict) else {'notes': row}
            for row in rows]


def _shortlist(report: dict) -> str:
    groups = {"priority": [], "watch": [], "avoid": []}
    for c in report.get("candidates", []):
        key, _ = _tier(c.get("tier"))
        if key in groups:
            groups[key].append(f'{_text(c.get("name"))} {_text(c.get("code"))}')
    parts = []
    for key, label in (("priority", "先看这些"), ("watch", "等信号"), ("avoid", "暂时回避")):
        names = " / ".join(groups[key]) if groups[key] else "本次未列入"
        supplied_count = report.get('decision_counts', {}).get(key)
        if not groups[key] and isinstance(supplied_count, (int, float)) and supplied_count > 0:
            names = f'{supplied_count}只未列入逐只预案，原因见下方未入选名单'
        parts.append(f'<div><strong>{label}</strong><span>{_e(names)}</span></div>')
    return '<div class="shortlist">' + ''.join(parts) + '</div>'


def _source_html(sources: list) -> str:
    parts = []
    for source in sources:
        if isinstance(source, dict):
            label = source.get("title", source.get("url", "资料来源"))
            date = f' · {_e(source["date"])}' if source.get("date") else ""
            parts.append(f'<li>{_link(label, source.get("url"))}{date}</li>')
        else:
            parts.append(f'<li>{_e(source)}</li>')
    return '<ul class="source-list">' + "".join(parts) + '</ul>' if parts else '<p class="subtle">未提供独立来源。</p>'


def _source_md(sources: list) -> str:
    lines = []
    for source in sources:
        if isinstance(source, dict):
            label = _text(source.get("title", "资料来源"))
            href = _safe_href(source.get("url"))
            lines.append(f'- {label}' + (f'：{href}' if href else '') + (f' · {_text(source["date"])}' if source.get('date') else ''))
        else:
            lines.append('- ' + _text(source))
    return '\n'.join(lines) + '\n\n'


def _candidate(candidate: dict, kind: str | None = None) -> tuple[str, str]:
    c = candidate
    tier, tier_name = _tier(c.get("tier"))
    name, code = _text(c.get("name")), _text(c.get("code"))
    heading = f'{name} {code}'
    html = (f'<article class="candidate" data-candidate="true" data-tier="{tier}" data-search="{_e(heading)}">'
            '<div class="card-top"><div class="card-title"><div class="identity">'
            f'<h3>{_e(name)}</h3><span class="code">{_e(code)}</span></div>'
            f'<span class="badge {tier}">{_e(tier_name)}</span></div>'
            f'<p class="setup">{_e(c.get("setup"))}</p><div class="why">{_rich(c.get("why"))}</div>'
            f'<p class="status">当前信号：{_e(c.get("actual_status"))}</p></div>')
    md = f'### {heading} · {tier_name}\n\n{_text(c.get("setup"))}\n\n入选依据：{_text(c.get("why"))}\n\n当前信号：{_text(c.get("actual_status"))}\n\n'
    html += '<div class="actions">'
    long_target = isinstance(c.get('target_reference'), str) and len(c['target_reference']) > 35
    actions = (("等到什么才考虑买", "entry_trigger", "enter"),
               ("允许介入区域", "entry_zone", ""),
               ("这些情况不买", "not_buy", ""),
               ("跌破哪 / 何时退出", "stop_price", "exit"))
    for label, key, style in actions:
        value = _num(c.get(key)) if key == "stop_price" else c.get(key)
        html += f'<div class="action {style}"><strong>{label}</strong>{_rich(value)}</div>'
        md += f'**{label}：**{_text(value)}\n\n'
    if long_target:
        html += '<div class="action"><strong>压力与兑现参考</strong>' + _rich(c['target_reference']) + '</div>'
        md += '**压力与兑现参考：**' + _text(c['target_reference']) + '\n\n'
    html += '</div><dl class="levels">'
    reference = c.get("reference_price", c.get("reference", c.get("price")))
    levels = [("参考收盘", _num(reference, digits=3 if kind == 'cb' else 2))]
    if not long_target:
        levels.append(("前高 / 压力参考", _num(c.get("target_reference"), digits=3 if kind == 'cb' else 2)))
    levels.extend((("至压力空间", _num(c.get("upside_pct"), "%")),
                         ("至失效位风险", _num(c.get("risk_pct"), "%")),
                         ("几何空间 / 风险", _num(c.get("rr"), " 倍"))))
    for label, value in levels:
        html += f'<div><dt>{label}</dt><dd>{_e(value)}</dd></div>'
        md += f'- {label}：{_text(value)}\n'
    html += '</dl><div class="holding"><strong>已经持有怎么办</strong>' + _rich(c.get('holding')) + '</div>'
    md += '\n已经持有怎么办：' + _text(c.get('holding')) + '\n\n'
    html += '<div class="scenarios"><p>后续三种走法 · 按条件切换动作</p>'
    for label, key in (("转强", "scenario_up"), ("横盘", "scenario_flat"), ("走弱", "scenario_down")):
        value = c.get(key, c.get(key.removeprefix('scenario_')))
        html += f'<div class="scenario"><b>{label}</b><div>{_rich(value)}</div></div>'
        md += f'- {label}：{_text(value)}\n'
    html += '</div><details><summary>查看入选证据、缺口与来源</summary><div class="evidence">'
    for label, key in (("逐项确认条件", "conditions"), ("历史与当前证据", "evidence"), ("仍需确认", "missing")):
        html += f'<h4>{label}</h4>{_rich(c.get(key, []))}'
        md += f'\n{label}：{_text(c.get(key))}\n'
    html += _source_html(c.get('sources', [])) + '</div></details></article>'
    md += '\n' + _source_md(c.get('sources', []))
    return html, md


def render_playbook(report: dict, out: Path, filename: str) -> None:
    """Write a standalone HTML and equivalent Markdown plan to ``out``."""
    out = Path(out)
    filename = _filename(filename)
    title = _text(report.get("title", "每日条件预案"))
    html = '<nav class="report-nav" aria-label="报告导航">' + _link('三份预案总览', 'index.html')
    for label, href in report.get('links', {}).items():
        html += _link(label, href)
    html += '</nav><header><div class="eyebrow">每日预案 / ' + _e(KIND_NAMES.get(report.get('kind'), '交易研究')) + '</div>'
    html += f'<h1>{_e(title)}</h1><p class="subtle">{_e(report.get("market_status"))}</p><div class="clock">'
    md = f'# {title}\n\n{_text(report.get("market_status"))}\n\n'
    for label, key in (("收盘数据截至", "as_of"), ("预案适用交易日", "plan_date"), ("报告生成", "generated_at")):
        html += f'<div>{label}<b>{_e(report.get(key))}</b></div>'
        md += f'- {label}：{_text(report.get(key))}\n'
    html += '</div></header>' + _status_banner(report) + '<section class="decision" aria-labelledby="decision-title"><h2 id="decision-title">现在怎么做</h2>'
    html += '<div class="lead">' + _rich(report.get('decision_summary')) + '</div>' + _counts(report) + _shortlist(report)
    html += '<p class="subtle" style="margin-top:15px">优先候选表示观察顺序；未取得盘中确认数据时，触发数为未核验。日线涨幅不是策略收益。</p></section>'
    md += '\n' + plan_status(report) + '\n\n' + report.get('revision_note', '') + '\n\n## 现在怎么做\n\n' + _text(report.get('decision_summary')) + '\n\n'
    md += '\n'.join(f'- {label}：{_text(report.get("decision_counts", {}).get(key))}' for key, label in (
        ('priority', '筛选优先候选'), ('watch', '等待观察'), ('avoid', '回避'))) + '\n\n'
    count, trigger_label = trigger_status(report)
    md += f'- {trigger_label}：{_text(count)}\n\n'
    if report.get('summary'):
        html += '<section>' + _rich(report['summary']) + '</section>'
        md += '\n'.join('- ' + _text(x) for x in report['summary']) + '\n\n'
    if report.get('market_summary'):
        html += '<section><h2>先看市场是否允许出手</h2>' + _rich(report['market_summary']) + '</section>'
        md += '## 先看市场是否允许出手\n\n' + _text(report['market_summary']) + '\n\n'
    if report.get('screening_audit'):
        audit = report['screening_audit']
        checked, total = audit['quote_gain_ge_1_checked'], audit['quote_gain_ge_1']
        audit_text = (f"同日有效成交{audit['dated_active']}只；债涨≥1%的{total}只中，已细核{checked}只。"
                      f"完整筛选通过{audit['qualified']}只；另有{audit['recovery_reserves']}只候补等待收盘修复。")
        html += '<section><h2>还有其他转债可选吗</h2><p>' + _e(audit_text) + '</p>'
        html += '<p class="subtle">' + _e(audit['note']) + '</p></section>'
        md += '## 还有其他转债可选吗\n\n' + audit_text + '\n\n' + audit['note'] + '\n\n'
    if report.get('reserve_candidates'):
        html += '<section id="reserves"><h2>候补跟踪：重新收盘确认后再筛选</h2><div class="cards">'
        md += '## 候补跟踪：重新收盘确认后再筛选\n\n'
        for reserve in report['reserve_candidates']:
            html += '<article class="index-card"><div class="eyebrow">当前未入选 / 收盘后重筛</div>'
            html += '<h3>' + _e(reserve['name']) + ' · ' + _e(reserve['code']) + '</h3>'
            html += f"<p>基准收盘 {reserve['reference_price']:.3f}元 · 债涨 {reserve['pct_change']:.2f}% · 正股 {_e(reserve['stock_name'])}涨 {reserve['stock_change_pct']:.2f}%</p>"
            html += f"<p>溢价 {reserve['premium_pct']:.2f}% · 收盘位于日内区间 {reserve['close_location_pct']:.1f}%（当前要求≥40%）</p>"
            html += '<p>' + _e(reserve['reason']) + '</p><p>' + _e(reserve['recheck']) + '</p>'
            html += '<p class="subtle">' + _e(reserve['actual_status']) + '</p></article>'
            md += '### ' + reserve['name'] + '\n\n' + _text(reserve) + '\n\n'
        html += '</div></section>'
    if report.get('repair_watchlist'):
        repair_html, repair_md = _table(_compact_rows(report['repair_watchlist']))
        html += '<section id="repair-watch"><h2>活跃债回撤修复观察</h2><p>按原基准日成交额排序，不是推荐顺序。这些标的未通过当前完整筛选；这里只记录修复条件，不新增买入指令。</p><details><summary>展开' + str(len(report['repair_watchlist'])) + '只完整修复观察</summary><label>搜索修复名单 <input type="search" data-list-search="repair-watch" aria-label="搜索修复名单"></label>' + repair_html + '</details></section>'
        md += '## 活跃债回撤修复观察\n\n未通过完整筛选；不新增买入指令。\n\n' + repair_md
    html += '<section id="candidates"><div class="section-head"><h2>逐只预案：选谁、等什么、何时退出</h2><button class="print-button" id="print-report" type="button">打印 / 保存 PDF</button></div>'
    html += '<p class="subtle">以下搜索仅限逐只预案卡片；其余观察与未入选标的可在下方完整名单中搜索。</p>'
    html += '<div class="filters"><label>搜索预案卡片<input type="search" id="stock-search" placeholder="名称 / 代码" autocomplete="off"></label><label>卡片层级<select id="tier-filter"><option value="all">全部卡片</option>'
    present_tiers = {_tier(c.get('tier'))[0] for c in report.get('candidates', [])}
    html += ''.join(f'<option value="{key}">{label}</option>' for key, label in [('priority', '仅优先候选'), ('watch', '仅观察'), ('avoid', '仅回避')] if key in present_tiers)
    html += '</select></label><span class="filter-count" id="filter-count" role="status" aria-live="polite"></span></div><div class="cards">'
    md += '## 逐只预案\n\n'
    candidates = report.get('candidates', [])
    for candidate in candidates:
        card, card_md = _candidate(candidate, report.get('kind'))
        html += card
        md += card_md
    html += '</div><p class="empty" id="filter-empty"' + (' hidden' if candidates else '') + '>当前没有符合筛选的标的；无优先候选时，执行等待，保留现金，不补凑名单。</p></section>'
    if not candidates:
        md += '当前无候选；执行等待，不补凑名单。\n\n'
    html += '<section id="rules"><h2>这套战法如何执行</h2>'
    md += '## 这套战法如何执行\n\n'
    for rule in report.get('rules', []):
        if isinstance(rule, dict):
            rule_title = rule.get('title', rule.get('name', rule.get('label', '执行规则')))
            html += f'<article class="rule"><h3>{_e(rule_title)}</h3><dl>'
            md += f'### {_text(rule_title)}\n\n'
            for key, value in rule.items():
                if key in ('title', 'name', 'label'):
                    continue
                label = LABELS.get(key, key)
                html += f'<dt>{_e(label)}</dt><dd>{_rich(value)}</dd>'
                md += f'- {label}：{_text(value)}\n'
            html += '</dl></article>'
            md += '\n'
        else:
            html += '<div class="rule">' + _rich(rule) + '</div>'
            md += _text(rule) + '\n\n'
    if not report.get('rules'):
        html += '<p class="subtle">以各标的卡片所列条件为准。</p>'
    html += '</section><section class="research" id="research">'
    research_sections = []
    if report.get('history_stats'):
        research_sections.append(('历史分组统计：包含失败路径', 'history_stats'))
    research_sections.append(('历史复盘与统计依据', 'history'))
    if report.get('flat_scenarios'):
        research_sections.append(('横盘后监管空间：条件情景测算', 'flat_scenarios'))
    research_sections.append(('未入选名单与原因', 'excluded'))
    for label, key in research_sections:
        rows = report.get(key, [])
        if key == 'history' and report.get('history_stats'):
            rows = [row for row in rows if row not in report['history_stats']]
        if key == 'excluded':
            rows = _compact_rows(rows)
        table_html, table_md = _table(rows)
        search = '<label>搜索完整未入选名单 <input type="search" data-list-search="excluded-list" aria-label="搜索完整未入选名单"></label><p class="subtle">精简列展示；完整指标和原始字段保留于同名JSON。</p>' if key == 'excluded' else ''
        ident = ' id="excluded-list"' if key == 'excluded' else ''
        html += f'<details{ident}><summary>{label}（{len(rows)}）</summary><div class="details-body">{search}{table_html}</div></details>'
        md += f'## {label}\n\n' + table_md
    html += '<details><summary>研究范围、限制与来源</summary><div class="details-body"><h3>本次研究范围</h3>'
    html += _rich(report.get('scope')) + '<div class="limitations">' + _rich(report.get('limitations', [])) + '</div>'
    if report.get('data_quality'):
        html += '<h3>数据完整性</h3>' + _rich(report['data_quality'])
        md += '## 数据完整性\n\n' + _text(report['data_quality']) + '\n\n'
    html += _source_html(report.get('sources', [])) + '</div></details></section>'
    md += '## 研究范围与限制\n\n' + _text(report.get('scope')) + '\n\n'
    md += '\n'.join('- ' + _text(x) for x in report.get('limitations', [])) + '\n\n## 来源\n\n' + _source_md(report.get('sources', []))
    html += '<footer>本页是生成时快照，不会自行更新。价格位置与历史统计用于条件预案；涨跌分支取决于后续实际行情。</footer>'
    out.mkdir(parents=True, exist_ok=True)
    (out / filename).write_text(_document(title, html), encoding='utf-8')
    (out / Path(filename).with_suffix('.md')).write_text(md, encoding='utf-8')


def render_index(reports: list, out: Path) -> None:
    """Write the shared three-report entry page (``index.html`` / ``index.md``)."""
    out = Path(out)
    html = '<header><div class="eyebrow">每日预案 / 三类机会，一套执行顺序</div><h1>三战法：按日期查看预案与复盘</h1><p class="subtle">先核对计划日和核验状态，再查看候选的触发、介入与退出条件。</p></header>'
    if reports and reports[0].get('links', {}).get('返回主线追踪报告'):
        html = '<nav class="report-nav">' + _link('返回主线追踪报告', reports[0]['links']['返回主线追踪报告']) + '</nav>' + html
    if reports:
        html += _status_banner(reports[0])
        for label, href in reports[0].get('links', {}).items():
            if '复盘' in label or '原始冻结' in label:
                html += '<p>' + _link(label, href) + '</p>'
    html += '<div class="index-grid">'
    md = '# 每日战法预案总览\n\n先核对计划日和核验状态。\n\n'
    for report in reports:
        filename = _filename(report.get('filename', DEFAULT_FILES.get(report.get('kind'), 'playbook.html')))
        title = _text(report.get('title', KIND_NAMES.get(report.get('kind'), '战法报告')))
        html += f'<article class="index-card"><div class="eyebrow">{_e(KIND_NAMES.get(report.get("kind"), "战法研究"))}</div><h2>{_e(title)}</h2>'
        html += f'<p class="subtle">数据 {_e(report.get("as_of"))} · 预案 {_e(report.get("plan_date"))}</p>'
        html += '<div class="lead">' + _rich(report.get('decision_summary')) + '</div>' + _counts(report) + _shortlist(report)
        if report.get('reserve_candidates'):
            html += '<p class="subtle">候补跟踪：' + _e('、'.join(x['name'] for x in report['reserve_candidates'])) + '；须重新收盘确认，当前未入选。</p>'
        html += f'<a class="open" href="{_e(filename)}">查看逐只预案 →</a></article>'
        md += f'## {title}\n\n数据 {_text(report.get("as_of"))} · 预案 {_text(report.get("plan_date"))}\n\n{_text(report.get("decision_summary"))}\n\n[查看逐只预案]({filename})\n\n'
    joint = '三份报告不是三倍仓位：同一股票只执行一套入场逻辑，不重复计仓。股票抱团做回踩收复，异动报告做突破后回踩，因此同一只股票的候选层级、价位可以不同；未满足所选战法的全部条件就不买。历史统计未证明这些新入场规则有稳定优势，先做模拟跟踪更稳妥。'
    html += '</div><section class="decision"><h2>三份报告怎么一起用</h2><p>' + _e(joint) + '</p></section><footer>优先候选与已触发信号分别计数。三个报告共享研究框架，但各自使用对应资产的交易规则与数据日期。页面为静态快照。</footer>'
    md += '## 三份报告怎么一起用\n\n' + joint + '\n\n'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'index.html').write_text(_document('每日战法预案总览', html), encoding='utf-8')
    (out / 'index.md').write_text(md, encoding='utf-8')
