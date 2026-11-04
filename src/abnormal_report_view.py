"""Self-contained, responsive HTML and Markdown from the same research payload."""
from __future__ import annotations
from html import escape
import math
from pathlib import Path


def text(v):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return '—'
    if isinstance(v, bool):
        return '是' if v else '否'
    return str(v)


def num(v, digits=2):
    try:
        f = float(v)
        return f'{f:.{digits}f}' if math.isfinite(f) else '—'
    except (ValueError, TypeError):
        return '—'


def pct(v):
    return num(v) + '%' if num(v) != '—' else '—'


def clock_text(value):
    s = text(value)
    return s.replace('T', ' ')[:19] if 'T' in s else s


def ratio(r, key):
    n = r.get(key + '_n')
    s = r.get(key + '_success')
    return f'{s}/{n} · {s/n*100:.1f}%' if n and s is not None else '—'


def table(headers, rows, filters=None):
    head = '<thead><tr>' + ''.join(f'<th scope="col">{escape(h)}</th>' for h in headers) + '</tr></thead>'
    body = []
    for i, values in enumerate(rows):
        attrs = ''
        if filters:
            board, search = filters[i]
            attrs = f' data-filterable="true" data-board="{escape(text(board), quote=True)}" data-search="{escape(text(search), quote=True)}"'
        body.append('<tr' + attrs + '>' + ''.join(f'<td>{escape(text(v))}</td>' for v in values) + '</tr>')
    if not rows:
        body = [f'<tr><td colspan="{len(headers)}">暂无满足口径的数据</td></tr>']
    return '<div class="table-wrap" tabindex="0"><table>' + head + '<tbody>' + ''.join(body) + '</tbody></table></div>'


def md_table(headers, rows):
    safe = lambda v: text(v).replace('|', '／').replace('\n', ' ')
    return '\n'.join(['|' + '|'.join(headers) + '|', '|' + '|'.join(['---'] * len(headers)) + '|'] +
                     ['|' + '|'.join(safe(v) for v in row) + '|' for row in rows]) + '\n\n'


def spark(case):
    rows = [r for r in case.get('rows', []) if r.get('close') is not None][-24:]
    if len(rows) < 2:
        return '<p>收盘走势图数据不足</p>'
    lo, hi = min(r['low'] for r in rows), max(r['high'] for r in rows)
    pad = max((hi - lo) * .12, .05)
    lo, hi = lo - pad, hi + pad
    X = lambda i: 54 + i * 614 / (len(rows)-1)
    Y = lambda p: 28 + (hi-p)/(hi-lo)*158
    out = ['<svg class="price-chart" viewBox="0 0 700 224" role="img" aria-label="' + escape(case['name']) + '最近24个交易日收盘与涨停标记">']
    for j in range(4):
        price = lo + (hi-lo)*j/3
        out.append(f'<path d="M54 {Y(price):.1f}H674" stroke="#DCE4ED"/><text x="4" y="{Y(price)+4:.1f}" class="svg-label">{price:.2f}</text>')
    for event in case.get('official_events', []):
        for i, r in enumerate(rows):
            if r['date'] == event['date']:
                out.append(f'<path d="M{X(i):.1f} 20V192" stroke="#A96920" stroke-dasharray="3 4"><title>{escape(event.get("title", "公告"))}</title></path>')
    out.append('<polyline fill="none" stroke="#315F87" stroke-width="2.5" points="' + ' '.join(f'{X(i):.1f},{Y(r["close"]):.1f}' for i, r in enumerate(rows)) + '"/>')
    for i, r in enumerate(rows):
        if r.get('is_limit') or r.get('touched_limit'):
            color = '#BC4754' if r.get('is_limit') else '#A96920'
            out.append(f'<circle cx="{X(i):.1f}" cy="{Y(r["close"]):.1f}" r="4" fill="{color}"><title>{escape(r["date"] + " " + r.get("limit_label", "") + " 收" + num(r["close"]))}</title></circle>')
        if i in (0, len(rows)//3, len(rows)*2//3, len(rows)-1):
            out.append(f'<text x="{X(i):.1f}" y="211" text-anchor="middle" class="svg-label">{escape(r["date"][5:])}</text>')
    return ''.join(out) + '</svg><p class="caption">蓝线：未复权收盘 · 红点：收盘涨停 · 橙点：触板未封 · 虚线：公告/监控节点</p>'


STYLE = '''
:root{--bg:#F5F7FA;--paper:#fff;--ink:#18283B;--muted:#53687C;--blue:#315F87;--green:#278575;--red:#BC4754;--amber:#A96920;--edge:#DCE4ED}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.75 "Microsoft YaHei","Segoe UI",sans-serif}a{color:var(--blue);text-underline-offset:3px}main{max-width:1320px;margin:auto;padding:30px 28px 70px}h1,h2{font-family:"SimSun","Songti SC",serif}h1{font-size:34px;line-height:1.35;margin:10px 0}h2{font-size:25px;margin:0 0 14px}h3{font-size:18px;margin:0 0 8px}p{margin:8px 0 14px}.eyebrow{color:var(--blue);font:12px/1.4 Consolas,monospace;letter-spacing:1px}.subtitle,.caption{color:var(--muted)}.caption{font-size:12px;line-height:1.65}.clock{display:grid;grid-template-columns:repeat(4,1fr);gap:0;border-block:1px solid var(--edge);margin:22px 0}.clock div{padding:13px 18px;border-right:1px solid var(--edge)}.clock div:first-child{padding-left:0}.clock span{display:block;color:var(--muted);font-size:12px}.clock b{font:14px/1.8 Consolas,"Microsoft YaHei",monospace;overflow-wrap:anywhere}.note{border-left:4px solid var(--amber);background:#FFF9EF;padding:14px 18px;margin:20px 0}.note p{margin:4px 0}.overview{padding-left:23px}.overview li{margin:7px 0}nav{display:flex;gap:20px;flex-wrap:wrap;padding:12px 0;border-bottom:1px solid var(--edge)}nav a{text-decoration:none;font-size:13px}section{scroll-margin-top:20px;margin-top:35px;min-width:0}.case-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.case{min-width:0;background:var(--paper);border:1px solid var(--edge);padding:23px;border-radius:10px}.case-head{display:flex;align-items:start;justify-content:space-between;gap:12px}.code{font:12px Consolas,monospace;color:var(--muted)}.badge{font-size:12px;padding:4px 9px;border-radius:4px;background:#EDF3F8;color:var(--blue)}.thesis{font-weight:600;font-size:18px;line-height:1.6;margin:16px 0}.price-chart{display:block;width:100%;height:auto}.svg-label{font:11px Consolas,"Microsoft YaHei",sans-serif;fill:#53687C}.actions{display:grid;gap:11px;margin-top:16px}.action{padding:12px 15px;border-left:3px solid var(--blue);background:#F3F7FB}.action.sell{border-color:var(--red);background:#FDF4F5}.action strong{display:block;font-size:13px}.action p{font-size:13px;margin:4px 0}.timeline{display:flex;gap:10px;overflow:auto;padding:8px 0 14px}.step{flex:0 0 132px;border-top:3px solid var(--edge);background:#F5F7FA;padding:9px;font-size:12px}.step.limit{border-color:var(--red)}.step.touch{border-color:var(--amber)}.step b,.step span{display:block}.step small{display:block;color:var(--muted);line-height:1.5}.table-wrap{overflow:auto;max-width:100%;border:1px solid var(--edge);border-radius:5px;margin:12px 0 20px;background:white}table{border-collapse:collapse;width:100%;font-size:12px}th{background:#EDF3F8;color:var(--muted);white-space:nowrap;font-weight:600}td,th{padding:10px 12px;border-bottom:1px solid var(--edge);text-align:left;vertical-align:top}td{min-width:72px}td:first-child{white-space:nowrap}td:last-child{min-width:120px}.filters{display:flex;gap:14px;align-items:end;flex-wrap:wrap;margin:16px 0}.filters label{display:grid;gap:5px;font-size:12px;color:var(--muted)}select,input{font:14px "Microsoft YaHei",sans-serif;border:1px solid #AABBCB;border-radius:5px;padding:9px 12px;background:white;color:var(--ink);min-height:40px}input{width:230px}details{margin:14px 0;border-top:1px solid var(--edge);padding:12px 0}summary{cursor:pointer;font-weight:600;color:var(--blue)}.download{display:flex;gap:15px;flex-wrap:wrap;font-size:13px}.sources li{margin:8px 0;overflow-wrap:anywhere}footer{font-size:12px;color:var(--muted);border-top:1px solid var(--edge);margin-top:35px;padding-top:15px}[hidden]{display:none!important}:focus-visible{outline:3px solid var(--blue);outline-offset:3px}.count{font:12px Consolas,"Microsoft YaHei",monospace;color:var(--muted)}
@media(max-width:900px){.case-grid{grid-template-columns:1fr}.clock{grid-template-columns:repeat(2,1fr)}.clock div:nth-child(3){padding-left:0}}
@media(max-width:480px){main{padding:18px 12px 40px}h1{font-size:27px}h2{font-size:22px}.case{padding:16px}.clock b{font-size:12px}.clock div{padding:10px}.case-head{display:block}.badge{display:inline-block;margin-top:9px}.filters label{flex:1 1 130px}input,select{width:100%;max-width:100%}.thesis{font-size:16px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
@media print{body{background:white}main{padding:0;max-width:none}.filters,nav,.download{display:none}.case-grid{display:block}.case{break-inside:avoid;margin:15px 0}.table-wrap{overflow:visible;border:0}table{font-size:9px}td,th{padding:5px}.timeline{flex-wrap:wrap}a{color:inherit}.note{background:white}}
'''


def render_report(payload: dict, out: Path):
    out = Path(out)
    meta = payload.get('meta', {})
    html, md = [], []
    def para(s, cls=''):
        html.append(f'<p class="{cls}">{escape(text(s))}</p>')
        md.append(text(s) + '\n\n')
    def heading(title, ident):
        html.append(f'<section id="{ident}"><h2>{escape(title)}</h2>')
        md.append('## ' + title + '\n\n')
    def addtable(headers, rows, filters=None):
        html.append(table(headers, rows, filters)); md.append(md_table(headers, rows))
    title = '异动之后，观察能否再走强'
    html.append('<header><div class="eyebrow">异动与连板 · 研究观察 v2</div><h1>' + title + '</h1>')
    md.append('# ' + title + '\n\n')
    para('凡拓数创与莱美药业案例 · 连板、整理、反包与失效条件', 'subtitle')
    clocks = [('收盘价格截止', meta.get('price_as_of')), ('指数截止', meta.get('index_as_of')),
              ('开盘啦提醒（北京）', clock_text(meta.get('kpl_timestamp') or meta.get('kpl_as_of'))), ('本次生成（北京）', clock_text(meta.get('generated_at')))]
    html.append('<div class="clock">' + ''.join('<div><span>' + a + '</span><b>' + escape(text(b)) + '</b></div>' for a, b in clocks) + '</div></header>')
    md.append(md_table(['数据时钟', '时间'], clocks))
    html.append('<nav><a href="#cases">两股案例</a><a href="#current">当前观察</a><a href="#history">历史统计</a><a href="#rules">条件战法</a><a href="#audit">口径与来源</a></nav>')
    html.append('<div class="note">')
    para(meta.get('method'))
    para('盘中提醒与已完成收盘证据分别呈现；本页是生成时快照，不会自行刷新。')
    html.append('</div><ul class="overview">')
    for s in payload.get('overview', []):
        html.append('<li>' + escape(s) + '</li>'); md.append('- ' + s + '\n')
    html.append('</ul>'); md.append('\n')

    heading('两股案例：公告确认之后，价格发生了什么', 'cases')
    html.append('<div class="case-grid">')
    for case in payload.get('cases', []):
        html.append('<article class="case"><div class="case-head"><div><h3>' + escape(case['name']) + '</h3><span class="code">' + escape(case['code']) + '</span></div><span class="badge">' + escape(case.get('regulation_status', '未核验')) + '</span></div>')
        md.append('### ' + case['name'] + ' ' + case['code'] + '\n\n' + case.get('regulation_status', '') + '\n\n')
        para(case.get('headline'), 'thesis')
        html.append(spark(case))
        para(case.get('case_summary'))
        last = case.get('latest', {})
        para(f"截至{case.get('as_of')}：收盘 {num(last.get('close'))} ｜ MA5 {num(last.get('ma5'))} ｜ MA10 {num(last.get('ma10'))} ｜ 成交量/前5日均量 {num(last.get('volume_ratio5'))}倍", 'caption')
        html.append('<div class="timeline" tabindex="0">')
        for r in case.get('key_dates', []):
            cls = 'limit' if r.get('is_limit') else ('touch' if r.get('touched_limit') else '')
            html.append(f'<div class="step {cls}"><b>{escape(r["date"])}</b><span>{num(r.get("close"))} · {escape(r.get("limit_label", ""))}</span><small>{escape(r.get("annotation", ""))}</small></div>')
        html.append('</div>')
        md.append(md_table(['日期', '收盘', '板型', '节点'], [[r['date'], num(r.get('close')), r.get('limit_label'), r.get('annotation')] for r in case.get('key_dates', [])]))
        html.append('<div class="actions">')
        for label, key, cls in [('转强/买入观察条件', 'buy_condition', ''), ('失效/减仓退出评估', 'sell_condition', 'sell')]:
            html.append(f'<div class="action {cls}"><strong>{label}</strong><p>{escape(text(case.get(key)))}</p></div>')
            md.append(f'**{label}：**{text(case.get(key))}\n\n')
        html.append('</div><details><summary>查看关键价位、公告后收益与逐日行情</summary>')
        addtable(['参考位', '价格', '含义'], [[x.get('label'), num(x.get('price')), x.get('meaning')] for x in case.get('levels', [])])
        addtable(['事件基准', '后续窗口', '已观察日', '收益', '成熟'],
                 [[r['event_date'], str(r['horizon'])+'日', r.get('available_sessions'), pct(r.get('return_pct')), r.get('complete')] for r in case.get('announcement_returns', [])])
        para('收益以公告日收盘为基准描述路径；未满对应交易日留空，不代表公告后能按该价成交。', 'caption')
        addtable(['日期', '开', '高', '低', '收', '成交量（手）', '板型'],
                 [[r['date'], num(r.get('open')), num(r.get('high')), num(r.get('low')), num(r.get('close')), num(r.get('volume'), 0), r.get('limit_label')] for r in case.get('rows', [])[-24:]])
        for s in case.get('missing', []): para(s, 'caption')
        html.append('</details></article>')
    html.append('</div></section>')

    heading('当前观察：用什么条件升级或降级', 'current')
    html.append('<div class="filters"><label>上市板别<select id="board-filter"><option value="all">全部</option><option value="主板">主板</option><option value="创业板">创业板</option></select></label><label>个股检索<input id="stock-search" type="search" placeholder="名称或代码"></label><span class="count" id="result-count"></span></div>')
    cur = payload.get('current', [])
    headers = ['个股', '上市板别', '监管状态', '近10日板数', '连板/断板轨迹', '整理天数', '当前动作']
    addtable(headers, [[r.get('name', r.get('code')), r.get('board'), r.get('regulation_status'), '—' if r.get('boards10') is None else f"10天{r['boards10']}板", r.get('sequence'), r.get('box_days'), r.get('action')] for r in cur], [(r.get('board'), str(r.get('name'))+str(r.get('code'))) for r in cur])
    para('区间值与均线使用固定前复权收盘价；上方案例卡使用未复权报价。“上市板别”不是题材，机器人、医药等同题材共振尚需核验。', 'caption')
    html.append('<details><summary>展开每只股票的触发条件、缺项与历史对照</summary>')
    for r in cur:
        html.append('<article class="watch-detail" data-current-detail="true" data-board="' + escape(text(r.get('board')), quote=True) + '" data-search="' + escape(text(r.get('name'))+text(r.get('code')), quote=True) + '"><h3>' + escape(text(r.get('name'))+' '+text(r.get('code'))) + '</h3>')
        md.append('### ' + text(r.get('name')) + ' ' + text(r.get('code')) + '\n\n')
        para(f"价格日 {r.get('price_as_of', '—')} ｜ 提醒日 {r.get('alert_as_of', '—')} ｜ 重建10日偏离 {pct(r.get('dev10'))} ｜ 重建30日偏离 {pct(r.get('dev30'))}", 'caption')
        para('开盘啦原文：' + text(r.get('trigger_text')) + '；触发参考价 ' + num(r.get('trigger_price')) + '。该百分比为当日/次日累计触发涨幅，非从现价还需上涨。', 'caption')
        para('转强条件：' + text(r.get('buy_condition')))
        para('失效条件：' + text(r.get('sell_condition')))
        n = r.get('analog_n', 0)
        para(f"历史对照 {n}只：" + ('样本不足，不显示上涨空间预测。' if n < 5 else f"后5日中位 {pct(r.get('median5'))}，后10日中位 {pct(r.get('median10'))}，P25—P75 {pct(r.get('p25_10'))} 至 {pct(r.get('p75_10'))}；含失败样本，非目标涨幅。"))
        para('待核：' + text(r.get('missing')), 'caption'); html.append('</article>')
    html.append('</details></section>')

    heading('历史统计：先固定观察日，再看后来结果', 'history')
    para(f"价格重建入组事件 {meta.get('event_count')} 个；后10日完整 {meta.get('complete_10_count')} 个。分母按指标成熟度分别展示。")
    heads = ['上市板别', '分组', '事件数', '5日成熟', '10日成熟', '后5日中位', '后10日中位', '再连2板', '收盘新高', '跌幅≥15%']
    def stats(rows):
        return [[r.get('board'), r.get('kind'), r.get('n'), r.get('n5'), r.get('n10'), pct(r.get('median5')), pct(r.get('median10')), ratio(r,'reboard'), ratio(r,'newhigh'), ratio(r,'breakdown')] for r in rows]
    addtable(heads, stats(payload.get('summary', [])))
    para('首次越线后统计单独从数值首次越线日计算；与上面的预警事件重叠，不能相加，也不等同于正式监管名单。')
    addtable(heads, stats(payload.get('trigger_summary', [])))
    para('横盘2/3/5日：使用当时已知的首轮整理条件入组，收益从整理观察日重新起算。分位数同时包含继续上涨和失败破位；不同天数组有重叠。')
    addtable(['上市板别','横盘日数','样本','5日成熟','10日成熟','后5日中位','后10日中位','后10日上涨','P25—P75'],
             [[r.get('board'),r.get('days'),r.get('n'),r.get('n5'),r.get('n10'),pct(r.get('median5')),pct(r.get('median10')),ratio(r,'win10'),pct(r.get('p25_10'))+' 至 '+pct(r.get('p75_10'))] for r in payload.get('sideways_summary', [])])
    html.append('<details><summary>展开全部入组事件</summary>')
    addtable(['日期','个股','板别','事件','10日偏离','30日偏离','轨迹','已观察日','后5日','后10日'],
             [[r.get('event_date'),r.get('name'),r.get('board'),r.get('kind'),pct(r.get('dev10')),pct(r.get('dev30')),r.get('sequence'),r.get('observed_days'),pct(r.get('return5')),pct(r.get('return10'))] for r in payload.get('events', [])])
    html.append('</details></section>')

    heading('条件战法：入场确认、持有与失效', 'rules')
    para('主板按每日恰好10%计算，7板累计约94.87%，8板约114.36%。是否触及严重异常还取决于对应指数、窗口起点及其他异常次数条件；断板一天不会自动把累计涨幅清零。')
    para('以下为条件化观察规则，尚未完成成交约束下的独立前向验证。收盘信号只能在收盘后确认；次日能否成交需另行判断。')
    addtable(['形态','先决条件','买入观察/升级','持有条件','失效/退出评估','缺项或边界'],
             [[r.get(k) for k in ('name','setup','confirm','hold','fail','missing')] for r in payload.get('rules', [])])
    html.append('</section>')
    heading('口径、质量与原始来源', 'audit')
    for s in meta.get('limitations', []): para(s, 'caption')
    html.append('<details><summary>查看计算检查</summary>')
    for s in payload.get('audit', []): para(s, 'caption')
    html.append('</details><ul class="sources">'); md.append('### 来源\n\n')
    seen = set()
    for s in payload.get('sources', []):
        url = s.get('url', '')
        if url in seen or not url.startswith(('https://', 'http://')): continue
        seen.add(url)
        html.append(f'<li><a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(s.get("title", "来源"))}</a></li>')
        md.append('- [' + s.get('title','来源') + '](' + url + ')\n')
    html.append('</ul><div class="download">')
    for name, label in [('abnormal_lianban_study.md','完整文字报告'),('study_payload.json','结构化研究数据'),('abnormal_threshold_events.csv','预警事件CSV'),('first_crossing_events.csv','首次越线CSV'),('sideways_landmarks.csv','横盘观察样本CSV'),('current_case_analysis.csv','当前观察CSV'),('target_case_paths.csv','两股OHLC与涨停明细')]:
        html.append(f'<a href="{name}" download>{label}</a>')
    html.append('</div></section><footer>更新方式：重新运行生成脚本后刷新页面。旧版备份保存在 legacy_v1；旧的赢家空间预测已停用。</footer>')
    script = '''<script>
const board=document.getElementById('board-filter'),search=document.getElementById('stock-search'),count=document.getElementById('result-count');
function applyFilter(){let shown=0;const q=search.value.trim().toLowerCase();document.querySelectorAll('[data-filterable],[data-current-detail]').forEach(el=>{const keep=(board.value==='all'||el.dataset.board===board.value)&&(!q||el.dataset.search.toLowerCase().includes(q));el.hidden=!keep;if(keep&&el.hasAttribute('data-filterable'))shown++});count.textContent='当前显示 '+shown+' 只';}board.addEventListener('change',applyFilter);search.addEventListener('input',applyFilter);applyFilter();
window.addEventListener('beforeprint',()=>{document.querySelectorAll('details').forEach(el=>{el.dataset.wasOpen=el.open?'1':'0';el.open=true})});window.addEventListener('afterprint',()=>{document.querySelectorAll('details').forEach(el=>{el.open=el.dataset.wasOpen==='1'})});
</script>'''
    doc = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>异动与连板 · 战法观察</title><style>' + STYLE + '</style></head><body><main>' + ''.join(html) + '</main>' + script + '</body></html>'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'abnormal_lianban_study.html').write_text(doc, encoding='utf-8')
    (out / 'abnormal_lianban_study.md').write_text(''.join(md), encoding='utf-8')
