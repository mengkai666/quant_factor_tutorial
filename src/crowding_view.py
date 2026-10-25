"""抱团周期离线页面：标准库HTML/SVG，无远程脚本与图表CDN依赖。"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path


def e(value) -> str:
    return html.escape(str(value if value is not None else '—'), quote=True)


def pct(value) -> str:
    return '—' if value is None else f'{value:+.2f}%'


def table(headers, rows) -> str:
    return '<div class="table-scroll"><table><thead><tr>' + ''.join(f'<th>{e(h)}</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def render_crowding_teaser_html(report_date: str = '', *, site_mode: bool = False) -> str:
    link = '../crowding/latest.html' if site_mode else '抱团周期分析_最新.html'
    return f'''<section class="crowding-teaser" style="margin:18px 0;padding:18px;border:1px solid #796bb7;border-left:5px solid #ad9deb;border-radius:10px;background:#172132;color:#e6edf3">
      <strong>抱团 · 证据核验与周期试错</strong><p style="font-size:13px">断板 ≠ 瓦解 ≠ 出清 ≠ 新周期确认。分周期复盘 · 固定旧核心篮子 · 新候选验证 · 条件战法</p>
      <a href="{link}" style="color:#c9b9ff">进入抱团研究模块 →</a><small style="margin-left:14px;color:#a2b0c5">研究观察，不是买卖信号 · {e(report_date)}</small></section>'''


CSS = '''
:root{color-scheme:dark;--bg:#101825;--panel:#172334;--edge:#30425a;--text:#e4ebf6;--muted:#a4b4cb;--old:#b9a5f4;--new:#72d2c5;--risk:#f2b572;--up:#fa9da1;--down:#75cead}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.75 "Microsoft YaHei","PingFang SC",sans-serif}a{color:var(--new);text-decoration:none}a:hover{text-decoration:underline}button,select,input{font:inherit}button,a,input,select{touch-action:manipulation}button:focus-visible,a:focus-visible,select:focus-visible,input:focus-visible{outline:3px solid var(--new);outline-offset:3px}
.top-nav{position:sticky;top:0;z-index:5;display:flex;gap:18px;flex-wrap:wrap;background:#101825f5;border-bottom:1px solid var(--edge);padding:14px max(24px,calc((100vw - 1260px)/2));font-size:13px}.top-nav .active{color:var(--old);font-weight:bold}main{max-width:1260px;margin:auto;padding:40px 26px 70px}.eyebrow{color:var(--new);font:12px/1.5 Consolas,monospace;letter-spacing:2px}h1{font-size:clamp(32px,4.4vw,54px);line-height:1.2;margin:15px 0;font-weight:750;letter-spacing:-1px}h1 span{display:block;color:var(--old);font-size:.67em;line-height:1.65}h2{font-size:24px;margin:0 0 12px}h3{font-size:18px;margin:0 0 9px}h4{font-size:15px;margin:16px 0 5px}p{margin:8px 0 16px}.intro{max-width:820px;color:var(--muted);font-size:16px}.hero{display:grid;grid-template-columns:1.6fr 1fr;gap:28px;align-items:center;margin-bottom:30px}.clock{border-left:2px solid var(--new);padding:8px 0 8px 22px}.clock div{display:flex;justify-content:space-between;gap:16px;font-size:13px;padding:5px 0}.clock b{font-family:Consolas,monospace;font-weight:500;color:var(--text)}.alert{border:1px solid #82603e;background:#2a2522;color:#ffd5a6;padding:15px 20px;border-radius:8px}.muted{color:var(--muted)}.tiny{font-size:12px}.badge{display:inline-block;padding:2px 9px;border:1px solid var(--edge);border-radius:4px;color:var(--new);font-size:12px;white-space:nowrap}.badge.old{color:var(--old)}.badge.risk{color:var(--risk)}section{scroll-margin-top:85px;margin-top:38px}.section-head{display:flex;justify-content:space-between;align-items:baseline;gap:18px}.panel{background:var(--panel);border:1px solid var(--edge);border-radius:10px;padding:23px;margin:16px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.grid .panel{margin:0}.steps{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-top:18px}.step{padding:16px 11px;background:var(--panel);border-top:3px solid var(--old)}.step:nth-child(4),.step:nth-child(5){border-color:var(--risk)}.step:last-child{border-color:var(--new)}.step b{font-size:16px}.step p{font-size:12px;color:var(--muted);margin:6px 0 0}.branch{margin-top:14px;padding:11px 18px;border-left:3px solid var(--risk);background:#242831;color:#e4cfae;font-size:13px}.lane{display:grid;grid-template-columns:180px 1fr;gap:12px;align-items:center;margin:14px 0}.lane-label{font-size:13px;color:var(--muted)}.lane-track{height:33px;border-radius:5px;background:repeating-linear-gradient(90deg,#253147 0,#253147 1px,transparent 1px,transparent 10%);position:relative}.lane-bar{position:absolute;top:1px;height:31px;min-width:7px;background:#685986;color:#f4edff;line-height:31px;padding-left:10px;border-radius:4px;font-size:12px;overflow:hidden;white-space:nowrap}.lane-bar.trial{background:#285d5b;color:#d8fcf4}.lane-axis{display:flex;justify-content:space-between;font:12px Consolas,monospace;color:var(--muted);margin-left:192px}.controls{display:flex;flex-wrap:wrap;gap:9px;align-items:center;margin:18px 0}.controls button{border:1px solid var(--edge);background:var(--panel);color:var(--muted);border-radius:5px;padding:9px 14px;cursor:pointer}.controls button[aria-pressed=true]{background:#3f3659;color:#e9ddff;border-color:var(--old)}select,input{background:#111b29;border:1px solid var(--edge);border-radius:5px;padding:8px;color:var(--text);max-width:100%}.period[hidden]{display:none}.period-summary{border-left:3px solid var(--old);padding-left:18px;margin-bottom:20px}.table-scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}th{text-align:left;color:var(--muted);font-weight:500;background:#131e2e}th,td{padding:11px 12px;border-bottom:1px solid #2b3b51;vertical-align:top}td.wrap{white-space:normal}td .muted{font-size:11px}.up{color:var(--up)}.down{color:var(--down)}.stock-name{color:var(--text);font-weight:600}.state-line{padding:10px 0;display:flex;justify-content:space-between;gap:15px;border-bottom:1px solid var(--edge)}.state-line b{color:var(--old)}.chart-wrap{border:1px solid var(--edge);border-radius:8px;background:#111c2b;padding:18px}.chart-wrap svg{display:block;width:100%;height:auto;min-height:170px}.legend{display:flex;gap:17px;flex-wrap:wrap;font-size:12px;color:var(--muted)}.tactics article{border-top:3px solid var(--new)}.tactics p{font-size:13px;margin-bottom:10px}.tactics b{color:var(--new)}details{border-top:1px solid var(--edge);padding:14px 0}summary{cursor:pointer;font-weight:bold}.source-list{padding-left:20px;color:var(--muted);font-size:13px}.source-list li{margin:9px 0}footer{margin-top:40px;padding-top:20px;border-top:1px solid var(--edge);font-size:12px;color:var(--muted)}.note-list{padding-left:21px;color:var(--muted);font-size:13px}.method{white-space:pre-wrap;font:12px/1.9 Consolas,"Microsoft YaHei",sans-serif;color:var(--muted)}
@media(max-width:780px){main{padding:25px 16px}.hero,.grid{grid-template-columns:1fr}.top-nav{position:static;padding:12px 16px;gap:10px 15px}.steps{grid-template-columns:repeat(2,1fr)}.lane{grid-template-columns:115px 1fr;gap:8px}.lane-axis{margin-left:123px}.lane-label{font-size:11px}.panel{padding:17px}.section-head{display:block}.clock{padding-left:14px}.lane-bar{font-size:10px;padding-left:4px}h2{font-size:22px}table{font-size:12px}th,td{padding:9px}.controls button{padding:8px 9px}section{scroll-margin-top:14px}}
@media print{.top-nav,.controls{display:none}.period[hidden]{display:block}body{background:white;color:black}.panel{break-inside:avoid}a{color:inherit}}
'''


def generate_crowding_html(result: dict, *, is_site_mode: bool = False) -> str:
    r = result
    from crowding_tracking_view import TRACKING_CSS, TRACKING_SCRIPT, render_tracking_section
    tracking_section = render_tracking_section(r.get('tracking'))
    from crowding_replay_view import REPLAY_CSS, REPLAY_SCRIPT, render_replay_section
    replay_section = render_replay_section(r.get('screening_replay'))
    from crowding_external_view import EXTERNAL_CSS, EXTERNAL_SCRIPT, render_external_section
    external_section = render_external_section(r.get('external_evidence'))
    home = '../index.html' if is_site_mode else '本地导航入口.html'
    main = '../latest.html' if is_site_mode else '主线强度追踪.html'
    groups = r['groups']
    clock = ''.join(f'<div><span>{e(label)}</span><b>{e(value)}</b></div>' for label, value in [
        ('研究截止', r['requested_date']), ('回顾起点', r['window_start']),
        ('全市场价格', r['sources_as_of']['prices']), ('涨停 / 广度', str(r['sources_as_of']['limits']) + ' / ' + str(r['sources_as_of']['breadth'])),
        ('补充个股行情', r['sources_as_of']['reference_quotes'])])
    gap = r['lag_trading_days']
    if gap:
        alert = f'全市场价格落后 {gap} 个交易日：个股补充更新不等于全市场更新。当前只能研究候选，不能确认新周期。'
    elif r['quality'] in ('stale', 'partial'):
        alert = '价格可能已更新，但涨跌停或市场广度缺失/不同步。请逐项查看数据时钟，不能据局部证据确认新周期。'
    else:
        alert = '研究代理：价格强势不能证明真实资金抱团；成交与承接证据不足时，不升级为买卖信号。'
    lanes = ''
    import datetime
    start = datetime.date.fromisoformat(r['window_start'])
    end = datetime.date.fromisoformat(r['requested_date'])
    span = max((end - start).days, 1)
    for g in groups:
        a, b = max(start, datetime.date.fromisoformat(g['start'])), min(end, datetime.date.fromisoformat(g['end']))
        if b < a:
            continue
        left, width = (a-start).days/span*100, max((b-a).days/span*100, 1)
        lanes += f'<div class="lane"><div class="lane-label">{e(g["title"])}</div><div class="lane-track"><div class="lane-bar {"trial" if g["id"]=="trial" else ""}" style="left:{left:.2f}%;width:{width:.2f}%">{e(a.strftime("%m.%d"))} — {e(b.strftime("%m.%d"))}</div></div></div>'
    if not groups:
        lanes = '<p class="muted">本期不加载2026-10-08事后截图注释；仍可查看按当日价格生成的滚动观察与代理换位。</p>'
    period_buttons, periods = '', ''
    active_period = next((i for i in range(len(r['periods']) - 1, -1, -1)
                          if r['periods'][i].get('period_kind') == 'rolling'), 0)
    for i, p in enumerate(r['periods']):
        kind = '全窗口滚动' if p.get('period_kind') == 'rolling' else '历史案例'
        period_buttons += f'<button type="button" data-period="{i}" aria-pressed="{"true" if i==active_period else "false"}" aria-controls="period-{i}">{kind} · {e(p["start"][5:])} · {e(p["title"])}</button>'
        rows = [[e(m['name']), e(m.get('base_date')), pct(m.get('return_pct')), pct(m.get('max_drawdown_pct')),
                 e(f'{m["count"]}/{m["expected_count"]}'), '完整' if m.get('complete') else '缺基准/数据，仅局部观察'] for m in p['members']]
        height = ' · '.join(f'{e(x["name"])} {e(x.get("max_height", "—"))}板' for x in p['leaders']) or '暂无可靠记录'
        if p.get('period_kind') == 'rolling':
            height = '全市场价格持续性候选（不是连板高度）：' + (' · '.join(
                f'{e(x["name"])} / 5日 {pct(x.get("r5"))}' for x in p['leaders']) or '暂无合格候选，请核对截面完整性')
        else:
            height = '本段连板高度样本（不是抱团认定；不同涨跌幅制度不可直接比较）：' + height
        periods += f'''<article class="period panel" id="period-{i}" {'hidden' if i != active_period else ''}>
          <div class="period-summary"><span class="eyebrow">{e(p['start'])} / {e(p['end'])}</span><h3>{e(p['title'])}</h3><p>{e(p['thesis'])}</p><span class="badge old">{e(p['boundary_source'])}</span></div>
          <p class="tiny muted">{height}</p>
          {table(['个股','收益起算基准日','本段涨跌','区间最大回撤','有效/应有日','完整性'], rows) if rows else '<p class="muted">本段无截图指定篮子，请看下方全市场代理。</p>'}
          <p class="tiny muted">区间收益从本段首日前一交易日收盘起算；缺基准不补算。最大回撤仅基于可见收盘价，不含盘中风险。</p></article>'''
    cohort_cards = ''
    all_members = []
    for g in groups:
        all_members.extend(g['members'])
        rows = []
        for m in g['members']:
            mt = m['metrics']
            selection = m.get('source_selection', {})
            source_details = f'{e(m["source"])} / {e(m.get("price_basis"))}<br>缺失{len(selection.get("missing_trade_dates", []))}个交易日<br>{e(selection.get("selection_reason"))}'
            rows.append([f'<span class="stock-name">{e(m["name"])}</span><br><span class="muted">{e(m["code"])}</span>',
                         pct(mt['r1']), pct(mt['r5']), pct(mt['dd20']), e(m['as_of']),
                         f'<span class="tiny">{source_details}</span>', f'<span class="tiny">{e(m["role"])}</span>'])
        history = ' → '.join(f'{x["date"][5:]} {x["stage"]}' for j,x in enumerate(g['history']) if j==0 or x['stage'] != g['history'][j-1]['stage'])
        assessment = g['assessment']
        evidence = '；'.join(assessment.get('supporting_evidence', []) + assessment.get('opposing_evidence', []))
        missing = '；'.join(assessment.get('missing_evidence', []))
        dimensions = g.get('dimensions', {})
        cohort_cards += f'''<article class="panel"><div class="section-head"><h3>{e(g['title'])}</h3><span class="badge {'risk' if g['assessment']['stage']=='瓦解' else 'old'}">{e(g['assessment']['stage'])}</span></div>
          <p class="tiny muted">{e(g['origin'])}<br>固定篮子 {e(g.get('basket_id'))} / v{e(g.get('basket_version'))} · 研究注释可见日 {e(g.get('known_at'))}</p>{table(['成员','最近一日','5日收益','距20日收盘高点','个股截止','实际来源 / 缺口','身份边界'], rows)}
          <p class="tiny muted">{e(assessment['reason'])}<br>价格强度 / 脆弱占比：{e(dimensions.get('price_strength_share'))} / {e(dimensions.get('price_fragility_share'))} · 固定分母 {e(dimensions.get('denominator'))}；非成交集中度。<br>证据：{e(evidence)}<br>缺失：{e(missing)}<br>下一验证：{e(assessment.get('next_condition'))}</p><details><summary>固定篮子状态轨迹（价格代理）</summary><p class="tiny muted">{e(history or '无足够历史')}</p></details></article>'''
    gate_cards = ''
    for item in r.get('trial_gates', []):
        checks = ''.join(f'<div class="state-line"><span>{e(g["name"])}</span><b>{e(g["status"])}</b></div><p class="tiny muted">{e(g["note"])}</p>' for g in item['gates'])
        gate_cards += f'<article class="panel"><h3>{e(item["name"])}</h3><span class="badge">{e(item["label"])}</span><p class="tiny muted">{e(item["reference_date"])}收盘后至{e(item["as_of"])}：{pct(item["since_reference"])}（不是策略收益）</p>{checks}<p class="tiny">{e(item["action"])}</p></article>'
    gate_section = f'<section id="trial-gates"><h2>历史9/28试错案例 · 四道证据门</h2><p class="muted">价格支持只占一项；其余任一关键证据缺失，都不能叫“新周期确认”。</p><div class="grid">{gate_cards}</div></section>' if gate_cards else ''
    event_rows = []
    for event in r['events']:
        details = '；'.join(f'{x["name"]} {pct(x["r1"])} / {",".join(x["limit_types"]) or "无涨跌停记录"}' for x in event['evidence']) or '需指数与市场广度验证，不能仅靠个股K线'
        event_rows.append([e(event['date']), e(event['claim']), e(details)])
    succession = table(['确认日（收盘后）','原代理','新代理','解释'], [[e(x['date']),e(x['old_name']),e(x['new_name']),e(x['kind'])] for x in r['succession']])
    latest = r['daily'][-1] if r['daily'] else {}
    latest_top = latest.get('top', [])
    proxy_summary = f'当前有效核心：{e(latest.get("confirmed_proxy"))} · 状态：{e(latest.get("proxy_state"))} · 历史最近确认：{e(latest.get("historical_confirmed_proxy"))}<br>待确认：{e(latest.get("pending_proxy"))} · {e(latest.get("invalidation_reason"))}<br>候选集合：{e(latest.get("candidate_set_id"))} · 共{e(latest.get("candidate_count"))}只（表格仅展示前6名）'
    ranks = table(['个股','5日','20日','距20日高点','相对排名分'], [[e(x['name']), pct(x['r5']), pct(x['r20']), pct(x['dd20']),e(x['score'])] for x in latest_top])
    tactics = ''.join(f'''<article class="panel"><span class="badge">{e(t['stage'])}</span><h3 style="margin-top:13px">{e(t['title'])}</h3>
       <p><b>先看什么</b>　{e(t['observe'])}</p><p><b>验证条件</b>　{e(t['trigger'])}</p><p><b>不做什么</b>　{e(t['reject'])}</p><p><b>如何证伪</b>　{e(t['exit'])}</p><p class="muted">{e(t['example'])}</p></article>''' for t in r['tactics'])
    sources = ''.join(f'<li><span class="badge">{e(s["kind"])}</span> ' + (f'<a href="{e(s["url"])}" target="_blank" rel="noopener noreferrer">{e(s["title"])}</a>' if s.get('url') else e(s['title'])) + f' · {e(s["date"])}<br>{e(s["note"])}</li>' for s in r['sources'])
    reference_chains = '''<div class="grid"><div class="panel"><h3>截图给出的研究链</h3><p>农业：万向德农 → 亚盛 / 金健<br>消费：国芳 → 百大 → 桂林</p><p class="muted tiny">这些箭头是同方向换位假说，不意味着每一棒都成功。楚天龙、电力、次新是并行方向，不能强接成一条资金链。</p></div><div class="panel"><h3>9/28后不能全叫“新生”</h3><p>时代万恒 / 尚水智能 / 善水科技：候选竞争<br>近岸蛋白：既有趋势延续候选</p><p class="muted tiny">候选名单在10/8截图中被看到，不是9/28事前交易记录。市场转暖、持续性与成交条件未齐，不认定新周期完成接替。</p></div></div>''' if r['reference_visible'] else '<p class="muted">本期只展示价格代理；不使用未来或其他时期的截图名单。</p>'
    names = {m['code']:m['name'] for m in all_members}
    names.update({stock['code']:stock['name'] for stock in r.get('tracking', {}).get('stocks', [])})
    payload = json.dumps(dict(paths=r['paths'], names=names, tracking=r.get('tracking'), screening_replay=r.get('screening_replay')), ensure_ascii=False, allow_nan=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    options = ''.join(f'<option value="{e(k)}">{e(v)} · {e(k)}</option>' for k,v in names.items())
    warnings = ''.join(f'<li>{e(w)}</li>' for w in r['warnings'])
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="report-date" content="{e(r['requested_date'])}"><title>抱团周期分析 · {e(r['requested_date'])}</title><style>{CSS}{TRACKING_CSS}{REPLAY_CSS}{EXTERNAL_CSS}</style></head>
<body><nav class="top-nav" aria-label="主导航"><a href="{home}">导航首页</a><a href="{main}">主线追踪</a><a class="active" href="#">抱团周期</a><a href="#dynamic-tracking">动态新老追踪</a><a href="#screening-replay">分类每日推演</a><a href="#external-evidence">外部证据</a><a href="#cycles">分周期复盘</a><a href="#cohorts">抱团个股</a><a href="#tactics">条件战法</a><a href="#evidence">证据边界</a></nav>
<main><header class="hero"><div><div class="eyebrow">CROWDING CYCLES / RESEARCH DESK</div><h1>价格强度 × 脆弱性，<span>新旧群体可以并行。</span></h1><p class="intro">把“谁涨得多”与“谁在接替”分开。识别旧核心的修复与失效，记录新候选的竞争；新团不必等待旧团出清，不把一根阳线写成新周期。</p><span class="badge">两个月复盘</span> <span class="badge old">多周期并行</span> <span class="badge risk">非交易信号</span></div><div class="clock">{clock}</div></header>
<div class="alert" role="status">{e(alert)}</div>
<section id="logic"><h2>六个状态，不是一条必然上涨的路径</h2><p class="muted">抱团股：在分歧中仍反复获得相对强势与承接的辨识度标的。仅凭涨幅、连板数或同一题材，不能证明资金真的集中。</p>
<div class="steps">{''.join(f'<div class="step"><b>{e(a)}</b><p>{e(b)}</p></div>' for a,b in [('形成','从分散到聚焦，持续性初现'),('强化','核心与跟随获得多日验证'),('分歧','有人修复，有人掉队'),('瓦解','多核心破位，亏钱效应扩散'),('出清观察','连续止跌仍不是出清完成'),('试错 / 再确认','竞争可失败，也可回到等待')])}</div>
<div class="branch">断板是个股事件；瓦解是固定核心篮子的共振失效；出清是抛压释放的过程。试错可以失败，旧团也会反抽，多个方向可以同时处于不同状态。</div></section>
{tracking_section}
{replay_section}
{external_section}
<section id="cycles"><div class="section-head"><h2>并行周期泳道</h2><span class="tiny muted">分段来自研究注释，不是资金流向证明</span></div><div class="panel">{lanes}<div class="lane-axis"><span>{e(r['window_start'])}</span><span>{e(r['requested_date'])}</span></div></div>
<p class="tiny muted">历史案例分段与全窗口滚动观察独立展示。滚动层覆盖全部可用观察日，缺失日仍保留数据不足状态；5交易日边界不是已确认的真实周期。</p><div class="controls" aria-label="选择复盘周期">{period_buttons}</div>{periods}</section>
<section id="cohorts"><h2>历史案例固定篮子 · 与动态追踪分开</h2><p class="muted">以下名单来自历史研究注释，不是最近自动入池名单。当前动态新老个股看上方追踪表；不删除失败成员，也不倒推赢家。</p>{cohort_cards}
<div class="panel"><h3>逐股价格路径</h3><div class="controls"><label for="stock-select">选择个股</label><select id="stock-select">{options}</select></div><div class="chart-wrap"><div id="chart-caption" class="legend"></div><svg id="price-chart" viewBox="0 0 1080 300" role="img" aria-label="个股价格归一化走势图"></svg></div><p class="tiny muted">首个可见收盘=100，仅用于形态比较；缺价断线。横轴按观测日期，不将复权价格作为真实下单价。动态池个股路径使用同一全市场前复权缓存，历史篮子表的单源行情可能不同；点击追踪个股仅显示至所选观察日。</p></div></section>
<section id="succession"><h2>接替谱系：观点与证据分层</h2>{reference_chains}
<div class="panel"><h3>不预填股票名单的滚动代理换位</h3><p class="tiny muted">仅取全市场缓存至 {e(r['market_as_of'])}；连续2日居首且相对仍有效的旧核心至少高2分才换位。缺少连续价格或当日覆盖不足即过期，不满足筛选门槛即失效。历史核心不等于当前有效核心。</p><p>{proxy_summary}</p>{succession}<details><summary>最近全市场价格候选</summary>{ranks}</details><p class="tiny muted">候选版本是当前缓存重建的研究集合，导出时保存不可覆盖的修订快照；保存时刻不等于历史上已知时刻，不能当作真实事前回测。</p></div></section>
{gate_section}
<section id="tactics"><h2>战法卡 · 条件、否决、证伪</h2><p class="muted">规则是研究框架，不是已经证明有效的策略。每次记录“为什么入选、何时失效、是否可成交”，保留失败、未成交和未确认样本。</p><div class="grid tactics">{tactics}</div>
<div class="branch">执行纪律：观察 → 限定风险预算的试错 → 证据齐全后才升级。单笔数量只能在事先明确风险预算、有效失效价和成交条件后计算；跳空、跌停与A股T+1会使实际损失超出计划，模块不默认仓位或收益目标。</div></section>
<section id="evidence"><h2>截图节点，逐条对照</h2><p class="tiny muted">ZT/DT为缓存中的涨/跌停收盘记录；记录缺失不能推断全天未触板。盘中快讯与收盘事实分开。</p><div class="panel">{table(['截图日期','原研究节点','本地收盘证据'],event_rows)}<p class="tiny muted">被红叉标记的AI应用、液冷、PCB/CPO等不纳入已确认抱团；图2/3只算一份观点证据。“出清”不能由某个涨跌幅直接证实。</p></div>
<details open><summary>计算口径 / 数据限制</summary><ul class="note-list">{warnings}</ul><p class="method">筛选代理：5日收益 &gt; 4%，20日收益 &gt; 8%，近5日至少2天处于5日收益前15%。\n排名分 = 40×5日百分位 + 40×20日百分位 + 4×持续天数（0—100相对分，不是胜率）。\n瓦解代理：固定篮子至少60%成员距20日收盘高点回撤≥10%且在MA5下，当日收益中位数&lt;-2%。\n出清观察：已出现瓦解后，所有成员连续2日不创新近3日低点；有缺价立即阻断升级。\n修复路径：同一固定篮子至少70%成员恢复MA5且5日收益为正、弱势占比低于60%；连续2日为修复待验证，3日为旧团价格修复；缺价重置连续计数。\n阈值是未经校准的研究参数，不同板块与涨跌幅制度需独立检验。前复权修订、样本覆盖与低流动性偏差尚未消除。</p></details>
<details><summary>引用来源与证据等级</summary><ol class="source-list">{sources}</ol></details></section>
<footer>量化研究与技术交流，不构成投资建议。当前执行资格：{e(r['execution']['status'])}。{e(r['execution']['backtest'])}</footer></main>
<script id="crowding-data" type="application/json">{payload}</script>
<script>
'use strict';
const data=JSON.parse(document.getElementById('crowding-data').textContent);
document.querySelectorAll('[data-period]').forEach(button=>button.addEventListener('click',()=>{{
 document.querySelectorAll('[data-period]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
 document.querySelectorAll('.period').forEach(p=>p.hidden=p.id!=='period-'+button.dataset.period);
}}));
const svg=document.getElementById('price-chart');
function node(tag,attrs,text){{const n=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,String(v)));if(text!==undefined)n.textContent=text;svg.appendChild(n);return n;}}
function draw(maxDate){{
 const symbol=document.getElementById('stock-select').value, rows=(data.paths[symbol]||[]).filter(r=>typeof maxDate!=='string'||r.date<=maxDate), valid=rows.filter(r=>r.close!==null&&r.close>0); svg.replaceChildren();
 const caption=document.getElementById('chart-caption');caption.textContent=(data.names[symbol]||'暂无个股')+' · '+(valid.length?valid[0].date+' — '+valid[valid.length-1].date:'无足够行情');
 if(valid.length<2){{node('text',{{x:40,y:80,fill:'#a4b4cb','font-size':20}},'行情不足，不能绘制连续路径');return;}}
 const base=valid[0].close, values=valid.map(r=>r.close/base*100);let lo=Math.min(...values)*.97,hi=Math.max(...values)*1.03;if(hi===lo)hi=lo+1;
 const x=i=>68+i/Math.max(rows.length-1,1)*975,y=v=>255-(v-lo)/(hi-lo)*218;
 for(let k=0;k<5;k++){{const v=lo+(hi-lo)*k/4;node('line',{{x1:68,y1:y(v),x2:1043,y2:y(v),stroke:'#30425a','stroke-dasharray':'3 5'}});node('text',{{x:13,y:y(v)+4,fill:'#a4b4cb','font-size':12}},v.toFixed(1));}}
 let d='',connected=false;rows.forEach((r,i)=>{{if(r.close===null||r.close<=0){{connected=false;return;}}d+=(connected?'L':'M')+x(i).toFixed(2)+' '+y(r.close/base*100).toFixed(2)+' ';connected=true;}});
 node('path',{{d,fill:'none',stroke:'#b9a5f4','stroke-width':2.8,'stroke-linejoin':'round'}});
 node('text',{{x:68,y:285,fill:'#a4b4cb','font-size':12}},rows[0].date);node('text',{{x:950,y:285,fill:'#a4b4cb','font-size':12}},rows[rows.length-1].date);
 node('text',{{x:72,y:20,fill:'#e4ebf6','font-size':13}},'归一化收盘路径 / 首个可见日=100');
}}
document.getElementById('stock-select').addEventListener('change',draw);draw();
{TRACKING_SCRIPT}
{REPLAY_SCRIPT}
{EXTERNAL_SCRIPT}
</script></body></html>'''


def generate_crowding_markdown(r: dict) -> str:
    lines = ['# 抱团周期与新周期试错', '', f'研究截止：{r["requested_date"]}；回顾起点：{r["window_start"]}。',
             f'全市场行情：{r["market_as_of"]}；补充个股：{r["sources_as_of"]["reference_quotes"]}。', '',
             '## 核心理解', '断板是个股事件，瓦解是核心群体失效，出清是抛压释放过程，试错不是新周期确认。',
             '旧团修复、新团试错与趋势延续可以并存。截图2与3仅算一份证据；红叉科技分支不算已确认主线。', '', '## 数据边界']
    lines += [f'- {w}' for w in r['warnings']]
    latest = r['daily'][-1] if r['daily'] else {}
    lines += ['', '## 当前核心与候选版本',
              f'有效核心：{e(latest.get("confirmed_proxy"))}；历史最近确认：{e(latest.get("historical_confirmed_proxy"))}；状态：{e(latest.get("proxy_state"))}。',
              f'失效原因：{e(latest.get("invalidation_reason"))}；候选集合：{e(latest.get("candidate_set_id"))}。',
              '当前缓存重建，不是历史事前记录；导出保存不可覆盖的修订版本。']
    for group in r['groups']:
        lines += ['', f'### {group["title"]} · {group["assessment"]["stage"]}',
                  f'固定篮子：{group.get("basket_id")} / v{group.get("basket_version")}；固定分母：{len(group["members"])}。',
                  group['assessment']['reason'], f'下一验证：{group["assessment"].get("next_condition", "—")}']
        lines += [f'- {member["name"]}：{member["source"]} / {member.get("price_basis")}；截止 {member["as_of"]}；缺失 {len(member.get("source_selection", {}).get("missing_trade_dates", []))} 个交易日。' for member in group['members']]
    for p in r['periods']:
        lines += ['', f'## {p["start"]}—{p["end"]}：{p["title"]}', p['thesis'],
                  '| 个股 | 本段涨跌 | 最大收盘回撤 | 覆盖 |', '|---|---:|---:|---|']
        lines += [f'| {m["name"]} | {pct(m["return_pct"])} | {pct(m["max_drawdown_pct"])} | {m["count"]}/{m["expected_count"]} |' for m in p['members']]
    replay = r.get('screening_replay') or {}
    if replay.get('windows'):
        lines += ['', '## 分类筛选与每日推演（研究条件，不替换正式追踪）', '', replay['limitation']]
        for months, window in replay['windows'].items():
            snap = window['days'][-1]
            lines += ['', f"### 最近{months}个月 · {window['actual_start']}—{snap['date']}",
                      f"观察{window['observations']}日 / 完整截面{window['complete_days']}日 / 自动核心代理{len(window['cycles'])}段；当日价格资格{len(snap['selected'])}只。", '',
                      '| 分类 | 当日条件 | 成熟事件 / 入池日 | 标签截至 |', '|---|---|---|---|']
            lines += [f"| {pool} | {choice['rule_id']} | {choice['samples']} / {choice['entry_days']} | {choice['latest_label_date']} |" for pool, choice in snap['choices'].items()]
        lines += ['', '在HTML的“分类每日推演”切换窗口/日期，输入任意名称或代码查看每项不通过原因。标签只在后续5日成熟后供研究比较，严格早于观察日；窗口末20%冻结检验，非交易胜率。']
    tracking = r.get('tracking')
    if tracking:
        rules = tracking['rules']
        latest_tracking = tracking['daily'][-1] if tracking['daily'] else dict(date='—', rows=[])
        def md(value):
            return str(value if value is not None else '—').replace('|', '\\|').replace('\n', ' ')
        lines += ['', '## 动态新老抱团追踪 · 两个月逐日回放',
                  f'窗口 {tracking["window_start"]}—{tracking["requested_date"]}；版本 {tracking["rule_id"]}。',
                  '既有候选：5日收益>4%、20日收益>8%，近5日至少2日位于全市场5日收益前15%。',
                  f'候选内前{rules["top_fraction"]:.0%}（至少1名、边界同分保留），收盘≥MA5、20日高点回撤≤{rules["max_drawdown_pct"]:g}%。',
                  f'连续{rules["admission_days"]}日入池、{rules["strengthening_days"]}日强化、连续{rules["exit_days"]}日不满足退出；缺数打断确认，不计退出。',
                  '核心换位或旧核心失效开启下一轮；候选竞争到首次核心确认属于同一轮阶段推进。新老身份由首次入池轮、延续及重新入池记录决定。窗口边界强势股单列存量。',
                  tracking['limitation'],
                  'knowledge_status=reconstructed_from_current_cache；known_at为空，保存不代表当时已知。',
                  '', '### 自动观察段', '| 起止 | 核心代理 | 类型 | 本段入池数 |', '|---|---|---|---:|']
        lines += [f'| {c["start"]}—{c["end"]} | {md(c["anchor_name"])} | {md(c["kind"])} | {len(c["admission_codes"])} |' for c in tracking['cycles']]
        lines += ['', f'### 最近观察日 {latest_tracking["date"]} · 全量留档（非当前买入名单）',
                  '| 个股 | 身份 / 阶段 | 首见 / 入池 | 首次周期 / 最近匹配 | 连续支持 / 不满足 | 证据日 |',
                  '|---|---|---|---|---:|---|']
        lines += [f'| {md(x["name"])} / {x["code"]} | {x["identity"]} / {x["status"]} | {x["first_seen"]} / {md(x["first_admitted"])} | {md(x["first_cycle_id"])} / {md(x["matched_cycle_id"])} | {x["support_streak"]} / {x["failed_streak"]} | {md(x["metrics_as_of"])} |' for x in latest_tracking['rows']]
    lines += ['', '## 条件战法（未做可成交回测）']
    for t in r['tactics']:
        lines += ['', f'### {t["title"]}'] + [f'- {label}：{t[key]}' for label,key in [('观察','observe'),('验证','trigger'),('否决','reject'),('证伪','exit'),('示例','example')]]
    lines += ['', '## 风险纪律', '不生成买卖指令或默认仓位。盘后信号最早只能在后续可成交时点验证，不能使用同日收盘作为保证成交价。A股T+1、跳空、涨跌停均可能使计划失效。', '', '## 来源']
    lines += [f'- {s["title"]}（{s["kind"]}，{s["date"]}）：{s["note"]}' + (f' {s["url"]}' if s.get('url') else '') for s in r['sources']]
    from crowding_external_view import external_markdown
    lines += external_markdown(r.get('external_evidence'))
    return '\n'.join(lines) + '\n'


def write_crowding_latest(path: Path, document: str, stamp: str) -> str:
    """历史重跑只写日期归档，不把更晚的研究latest倒退。"""
    if path.exists():
        previous = re.search(r'<meta name="report-date" content="(\d{4}-\d{2}-\d{2})">', path.read_text(encoding='utf-8'))
        if previous and previous.group(1) > stamp:
            return previous.group(1)
    path.write_text(document, encoding='utf-8')
    return stamp


def archive_candidate_snapshots(result: dict, output_dir: str | Path) -> Path:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    root = Path(output_dir) / 'crowding_candidate_snapshots'
    root.mkdir(parents=True, exist_ok=True)
    observed_at = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    for record in result['daily']:
        target = root / record['date'] / f'{record["candidate_version"]}.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        snapshot = dict(observed_at=observed_at, known_at=None, report_requested_date=result['requested_date'],
                        source_clocks=result['sources_as_of'], candidate_snapshot=record,
                        knowledge_status='reconstructed_from_current_cache',
                        limitation='保存时刻不等于历史已知时刻；不作为真实事前候选或可成交回测。')
        try:
            with target.open('x', encoding='utf-8') as handle:
                handle.write(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False))
        except FileExistsError:
            saved = json.loads(target.read_text(encoding='utf-8'))
            if saved['candidate_snapshot'] != record:
                raise ValueError(f'候选版本冲突，拒绝覆盖：{target}')
    return root


def archive_replay_snapshots(result: dict, output_dir: str | Path) -> Path:
    """Persist causal policy artifacts only; never overwrite a prior reconstruction."""
    replay = result.get('screening_replay') or {}
    root = Path(output_dir) / 'crowding_replay_snapshots'
    for window, record in replay.get('windows', {}).items():
        for snapshot in record['days']:
            folder = root / window / snapshot['date']
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / (snapshot['version'] + '.json')
            content = dict(schema=replay['schema'], config_id=replay['config_id'],
                           knowledge_status=replay['knowledge_status'], known_at=None,
                           window_start=record['requested_start'], hypotheses=replay['hypotheses'],
                           label_definition=replay['label'], day=snapshot)
            if path.exists():
                saved = json.loads(path.read_text(encoding='utf-8'))
                if saved['day'] != snapshot or saved['config_id'] != replay['config_id']:
                    raise ValueError('分类推演快照版本冲突，拒绝覆盖')
                continue
            with path.open('x', encoding='utf-8') as stream:
                json.dump(content, stream, ensure_ascii=False, allow_nan=False, indent=2)
    return root.resolve()


def archive_tracking_snapshots(result: dict, output_dir: str | Path) -> Path:
    """不可变逐日状态档案；版本含参数与当日证据，保存时点非历史已知时点。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    root = Path(output_dir) / 'crowding_tracking_snapshots'
    root.mkdir(parents=True, exist_ok=True)
    tracking = result.get('tracking') or {}
    observed_at = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    for record in tracking.get('daily', []):
        target = root / record['date'] / f'{record["version"]}.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        snapshot = dict(observed_at=observed_at, known_at=None, knowledge_status='reconstructed_from_current_cache',
                        report_requested_date=result['requested_date'], source_clocks=result['sources_as_of'],
                        rule_id=tracking['rule_id'], rules=tracking['rules'], tracking_snapshot=record,
                        limitation=tracking['limitation'])
        try:
            with target.open('x', encoding='utf-8') as handle:
                handle.write(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False))
        except FileExistsError:
            saved = json.loads(target.read_text(encoding='utf-8'))
            if saved['tracking_snapshot'] != record or saved['rules'] != tracking['rules']:
                raise ValueError(f'跟踪版本冲突，拒绝覆盖：{target}')
    return root


def export_crowding_reports(result: dict, output_dir: str | Path, *, site_dir: str | Path | None = None) -> dict:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp = result['requested_date']
    document = generate_crowding_html(result)
    paths = {'local_latest': root / '抱团周期分析_最新.html', 'local_dated': root / f'抱团周期分析_{stamp}.html',
             'json': root / f'抱团周期分析_{stamp}.json', 'markdown': root / f'抱团周期战法_{stamp}.md'}
    write_crowding_latest(paths['local_latest'], document, stamp)
    paths['local_dated'].write_text(document, encoding='utf-8')
    paths['json'].write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    paths['markdown'].write_text(generate_crowding_markdown(result), encoding='utf-8')
    paths['candidate_snapshots'] = archive_candidate_snapshots(result, root)
    paths['tracking_snapshots'] = archive_tracking_snapshots(result, root)
    paths['replay_snapshots'] = archive_replay_snapshots(result, root)
    if site_dir is not None:
        site = Path(site_dir) / 'crowding'
        site.mkdir(parents=True, exist_ok=True)
        site_document = generate_crowding_html(result, is_site_mode=True)
        paths.update(site_latest=site / 'latest.html', site_dated=site / f'{stamp}.html')
        write_crowding_latest(paths['site_latest'], site_document, stamp)
        paths['site_dated'].write_text(site_document, encoding='utf-8')
    return {k: str(v.resolve()) for k,v in paths.items()}
