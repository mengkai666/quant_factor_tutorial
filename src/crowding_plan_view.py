"""抱团研究与次日条件预案的共用视图；只消费快照，不访问行情服务。"""
from __future__ import annotations

from html import escape
import math


def e(value):
    return escape(str(value if value is not None else ''))


def number(value, digits=2, suffix='', signed=False):
    try:
        v = float(value)
        if not math.isfinite(v):
            return '—'
        return f'{v:+.{digits}f}{suffix}' if signed else f'{v:.{digits}f}{suffix}'
    except (TypeError, ValueError):
        return '—'


def pct(value):
    return number(value, suffix='%', signed=True)


def lines(values):
    if not values:
        return '<p class="np-muted">暂无同日记录</p>'
    if isinstance(values, str):
        values = [values]
    return '<ul>' + ''.join(f'<li>{e(v)}</li>' for v in values) + '</ul>'


CSS = """
.np-plan{--np-bg:#0d1117;--np-card:#161b22;--np-line:#30363d;--np-muted:#9da7b3;color:#e6edf3;background:var(--np-bg);font:14px/1.7 'Microsoft YaHei','PingFang SC',sans-serif;max-width:1220px;margin:24px auto;padding:28px;border:1px solid var(--np-line);border-radius:14px;box-sizing:border-box;overflow-wrap:anywhere}
.np-plan *{box-sizing:border-box}.np-plan h1,.np-plan h2,.np-plan h3,.np-plan p{margin:0}.np-plan h1{font-size:30px;line-height:1.3;letter-spacing:.02em}.np-plan h2{font-size:21px;margin:0 0 12px}.np-plan h3{font-size:16px;margin-bottom:8px}.np-plan a{color:#79b8ff;text-decoration:none}.np-plan a:hover{text-decoration:underline}.np-plan .np-topline{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:20px;font-size:12px;color:var(--np-muted)}
.np-plan .np-muted{color:var(--np-muted);font-size:12px}.np-plan .np-eyebrow{color:#79b8ff;font-size:12px;letter-spacing:.12em;margin-bottom:9px}.np-plan .np-lead{font-size:17px;margin-top:12px;max-width:940px}.np-plan .np-status{margin-top:18px;border-left:3px solid #d29922;background:#d2992210;padding:14px 16px}.np-plan .np-status strong{color:#e3b341}.np-plan .np-section{border-top:1px solid var(--np-line);padding-top:24px;margin-top:28px}.np-plan .np-section-title{display:flex;align-items:baseline;justify-content:space-between;gap:14px;flex-wrap:wrap}.np-plan .np-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.np-plan .np-card{background:var(--np-card);border:1px solid var(--np-line);border-radius:9px;padding:16px;min-width:0}.np-plan .np-card p+p{margin-top:7px}.np-plan .np-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:20px}.np-plan .np-kpi{border-top:2px solid #384657;padding:12px;background:var(--np-card)}.np-plan .np-kpi b{font-size:27px;font-variant-numeric:tabular-nums;display:block;font-weight:600}.np-plan .np-kpi span{font-size:12px;color:var(--np-muted)}
.np-plan .np-scroll{overflow-x:auto;margin-top:12px;border:1px solid var(--np-line);border-radius:8px}.np-plan table{width:100%;border-collapse:collapse;margin:0;font-size:12px;line-height:1.65}.np-plan th{background:#1b2330;text-align:left;color:#b8c5d6;font-weight:500;white-space:nowrap}.np-plan td,.np-plan th{padding:12px 13px;border-bottom:1px solid #283240;vertical-align:top}.np-plan tr:last-child td{border-bottom:none}.np-plan td.np-num{font-variant-numeric:tabular-nums;white-space:nowrap}.np-plan .np-wide{min-width:810px}.np-plan .np-sector-table{min-width:640px}.np-plan .np-code{font-size:11px;color:var(--np-muted);white-space:nowrap}.np-plan .np-pill{display:inline-block;font-size:11px;padding:1px 7px;border:1px solid #435366;border-radius:4px;color:#b8c5d6;margin:5px 0}.np-plan .np-up{color:#ff8f88}.np-plan .np-down{color:#7bd694}.np-plan .np-neutral{color:var(--np-muted)}.np-plan .np-watch{border-left:2px solid #58a6ff;padding-left:10px;margin-top:10px}.np-plan .np-invalid{border-left:2px solid #d29922;padding-left:10px;margin-top:8px;color:#d2b883}.np-plan ul{padding-left:18px;margin:8px 0}.np-plan li{margin:5px 0}.np-plan details{background:var(--np-card);border:1px solid var(--np-line);padding:12px 16px;border-radius:8px;margin-top:12px}.np-plan summary{cursor:pointer;color:#b8c5d6}.np-plan details[open]>summary{margin-bottom:12px}.np-plan .np-time{color:#79b8ff;font-weight:600}.np-plan .np-footer{font-size:12px;color:var(--np-muted);margin-top:22px}.np-plan .np-nav{display:flex;gap:16px;flex-wrap:wrap;margin-top:14px;font-size:12px}.np-plan .np-empty{padding:18px;background:#d2992210;border:1px dashed #705c32;color:#d2b883;border-radius:8px}.np-plan .np-timeline{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.np-plan .np-tagline{padding:10px 0;color:#b8c5d6}.np-plan .np-summary{font-size:13px;color:#b8c5d6;margin-bottom:12px}
@media(max-width:760px){.np-plan{margin:12px auto;padding:18px 14px;border-radius:10px}.np-plan h1{font-size:24px}.np-plan h2{font-size:19px}.np-plan .np-grid{grid-template-columns:1fr}.np-plan .np-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.np-plan .np-timeline{grid-template-columns:repeat(2,minmax(0,1fr))}.np-plan .np-lead{font-size:15px}.np-plan .np-section{margin-top:24px;padding-top:20px}}
@media print{.np-plan{max-width:none;background:white;color:#111;border:0}.np-plan .np-card,.np-plan th,.np-plan details{background:#f6f6f6;color:#111}.np-plan .np-muted{color:#555}.np-plan .np-scroll{overflow:visible}.np-plan .np-nav{display:none}}
"""


def _performance(value):
    try:
        tone = 'np-up' if float(value) > 0 else 'np-down' if float(value) < 0 else 'np-neutral'
    except (TypeError, ValueError):
        tone = 'np-neutral'
    return f'<span class="{tone}">{pct(value)}</span>'


def _block_status(block):
    status = block.get('status', 'missing')
    label = {'ok':'同日数据','ready':'同日数据','partial':'部分字段待补','missing':'同日数据缺失',
             'unavailable':'同日数据缺失','stale':'数据过期','invalid':'数据未通过校验'}.get(status, status)
    return f'{e(block.get("as_of") or "未确认日期")} · {e(label)}'


def _empty(block):
    return f'<div class="np-empty">{e(block.get("summary") or "缺少同日证据，本期不形成观察名单。")}</div>'


def _sectors(block, mainline):
    rows = block.get('rows') or []
    if not rows:
        return _empty(block)
    body = []
    for row in rows[:5]:
        names = '、'.join(str(s.get('name') or '') for s in row.get('stocks', [])[:4])
        history = ' → '.join(f'{str(h.get("date", ""))[5:]} {number(h.get("limit_up_count"),0)}'
                             for h in row.get('history', [])[-3:])
        label = '规则主线' if row.get('name') == mainline else '比较观察'
        body.append(f'''<tr><td><b>{e(row.get('name'))}</b><br><span class="np-pill">{label}</span></td>
          <td class="np-num"><b>{number(row.get('limit_up_count'),0)} 家</b><br>较前日 {number(row.get('change'),0,signed=True)}<br><span class="np-muted">{e(history)}</span></td>
          <td>连板 {number(row.get('streak_count'),0)} 家<br>最高 {number(row.get('max_height'),0)} 板<br><span class="np-muted">{e(names)}</span></td>
          <td>{e(row.get('reason'))}<div class="np-watch">跟踪：{e(row.get('watch'))}</div><div class="np-invalid">失效：{e(row.get('invalidation'))}</div></td></tr>''')
    return ('<div class="np-scroll"><table class="np-sector-table"><thead><tr><th>题材与地位</th><th>涨停扩散 · 近三日</th>'
            '<th>梯队与代表股</th><th>持续性判断</th></tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>'
            '<p class="np-muted">口径为项目题材归类中的涨停样本。“连板家数”不是板块连续强势天数；家数变化不代表净流入。</p>')


def _stock_rows(rows):
    body = []
    roles = {'core':'持续核心观察','continuation':'延续观察','candidate':'新候选待确认','risk':'分歧风险对照'}
    for r in rows:
        support = number(r.get('support_streak'),0)
        body.append(f'''<tr><td><b>{e(r.get('name'))}</b><br><span class="np-code">{e(r.get('code'))}</span>
         <br><span class="np-pill">{e(roles.get(r.get('role'),r.get('identity') or '研究观察'))}</span><br>{e(r.get('identity') or '')}</td>
         <td class="np-num">{number(r.get('close'))}<br>{_performance(r.get('r1'))}<br><span class="np-muted">MA5 {number(r.get('ma5'))}</span></td>
         <td class="np-num">5日 {_performance(r.get('r5'))}<br>10日 {_performance(r.get('r10'))}<br>20日 {_performance(r.get('r20'))}</td>
         <td>连续支持 {support} 日<br><span class="np-muted">20日回撤 {pct(r.get('dd20'))}</span><br>{e(r.get('reason'))}</td>
         <td><div class="np-watch">{e(r.get('watch'))}</div><div class="np-invalid">{e(r.get('invalidation'))}</div></td></tr>''')
    return ('<div class="np-scroll"><table class="np-wide"><thead><tr><th>股票 · 当日身份</th><th>收盘 / 单日</th>'
            '<th>阶段涨幅</th><th>持续性证据</th><th>次日观察 / 失效条件</th></tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>')


def _stocks(block):
    rows = block.get('rows') or []
    if not rows:
        return _empty(block)
    main = rows[:3]
    risk = next((r for r in rows[3:] if r.get('role') == 'risk'), None)
    if risk:
        main = main + [risk]
    rest = [r for r in rows if r not in main]
    return _stock_rows(main) + (f'<details><summary>其余研究对象（{len(rest)} 只）</summary>{_stock_rows(rest)}</details>' if rest else '') + (
        '<p class="np-muted">量价与持续性仅作为抱团代理，不能据此确认机构持仓。股票价格和均线为同日版本前复权研究口径，'
        '下单前须核对未复权行情；这些价格不作为入场或止损委托价。</p>')


def _cb_rows(rows):
    body = []
    for r in rows:
        body.append(f'''<tr><td><b>{e(r.get('name'))}</b><br><span class="np-code">{e(r.get('code'))}</span><br>{e(r.get('stock_name'))}<br><span class="np-pill">{e(r.get('classification') or '债股联动观察')}</span></td>
          <td class="np-num">{number(r.get('close'),3)}<br>债 {_performance(r.get('r1'))}<br>股 {_performance(r.get('stock_r1'))}<br>5日 {_performance(r.get('r5'))}</td>
          <td class="np-num">溢价 {number(r.get('premium_pct'),2,'%')}<br>余额 {number(r.get('remaining_issue_yi'),3)} 亿<br>成交 {number(r.get('amount_yi'),2)} 亿</td>
          <td>{e(r.get('reason'))}<div class="np-muted">转股起始 {e(r.get('conversion_start') or '待核验')}<br>{e(r.get('redemption_status') or '强赎公告待核验')}</div></td>
          <td><div class="np-watch">{e(r.get('watch'))}</div><div class="np-invalid">{e(r.get('invalidation'))}</div></td></tr>''')
    return ('<div class="np-scroll"><table class="np-wide"><thead><tr><th>转债 / 正股</th><th>收盘与联动</th>'
            '<th>溢价 / 余额 / 成交</th><th>结构与条款</th><th>次日观察 / 失效条件</th></tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>')


def _bonds(block):
    rows = block.get('rows') or []
    if not rows:
        return _empty(block)
    return _cb_rows(rows[:3]) + (f'<details><summary>其余活跃债与风险对照（{len(rows)-3} 只）</summary>{_cb_rows(rows[3:])}</details>' if len(rows)>3 else '') + (
        '<p class="np-muted">成交额是交易活跃度，不能代替净流入；余额与发行规模分开。未进入转股期时，理论转股溢价不代表即时套利空间。</p>')


def _candidates(plan):
    decision = plan.get('decision') or {}
    selected = [c for c in decision.get('candidates',[]) if c.get('candidate_eligible') is True
                and c.get('scenario_candidate_eligible') is True and c.get('mainline_match') is True]
    if not selected:
        return '<p class="np-muted">当前无通过候选筛选的规则观察标的，抱团研究榜单不自动补位。</p>'
    cards = []
    for c in selected[:3]:
        x = c.get('execution') or {}
        label = '仅观察，未获开仓许可' if not decision.get('execution_allowed') or c.get('execution_allowed') is not True else '条件核验后按规则执行'
        cards.append(f'''<article class="np-card"><h3>{e(c.get('name'))} <span class="np-code">{e(c.get('code'))}</span></h3>
        <p class="np-muted">{label}</p><p>未复权收盘参考 {number(x.get('reference_close'))} · {e(x.get('reference_date') or '日期待核验')}</p>
        <div class="np-watch">触发：{e(c.get('trigger') or '等待完整条件')}</div><div class="np-invalid">失效：{e(c.get('invalid') or '等待完整条件')}</div></article>''')
    return '<div class="np-grid">'+''.join(cards)+'</div><p class="np-muted">收盘参考不是入场价。未提供实际持仓，本页不生成个人账户的加减仓数量。</p>'


def _conditional_matrix(plan):
    mainline = (plan.get('decision') or {}).get('mainline') or '候选主线'
    stock_rows = ((plan.get('crowding') or {}).get('stocks') or {}).get('rows') or []
    anchors = '、'.join(str(r.get('name')) for r in stock_rows if r.get('role') != 'risk')
    anchors = '、'.join(anchors.split('、')[:2]) or '同日股票观察对象'
    permission = '仅记录条件，保持不开新仓；资格通过后再复核信号。' if not (plan.get('decision') or {}).get('execution_allowed') else '仅对已获许可的候选核验入场条件，放弃无成交把握的追价。'
    cases = [
        ('强势延续', f'{mainline}核心承接稳定，第二梯队同步转强，避免只有单股高开。',
         f'{anchors}回落后保持承接；实际下单仅考虑规则筛选候选。',
         '债价与正股同步走强，溢价未继续单边放大；急拉后先观察回落承接。',
         '板块跟随消失、核心跌破早盘承接区，取消延续判断。'),
        ('分歧修复', '先观察回落，再等主线核心首次有效回封与至少两只同题材保持红盘。',
         '新候选须同时满足个股触发和市场、主线、风险开关；仅有拉升不算确认。',
         '正股先止跌，转债再重回盘中成交均价附近并站稳；只记录可验证的共振。',
         '10:00仍无扩散、回封再开或正股继续走弱，取消修复判断。'),
        ('转弱防守', '主线中位股连续负反馈、核心承接破坏或跌停反馈扩大。',
         '取消新开仓观察触发。已有可卖持仓按原风险规则评估，新买股票受T+1约束。',
         '债强股弱、溢价扩张或流动性转差时停止追价；已有仓位按预设日内风险限额处理。',
         '只有风险锚止跌、主线扩散恢复并重新通过资格核验，才转回观察。'),
    ]
    body = ''.join(f'<tr><td><b>{e(a)}</b></td><td>{e(b)}</td><td>{e(c)}</td><td>{e(d)}</td><td>{e(f)}</td></tr>' for a,b,c,d,f in cases)
    return f'<p class="np-summary">以下是根据当前证据制定的条件预案，盘中结果尚未发生。{e(permission)}</p><div class="np-scroll"><table class="np-wide"><thead><tr><th>情景</th><th>板块确认</th><th>股票应对</th><th>转债应对</th><th>失效 / 切换</th></tr></thead><tbody>{body}</tbody></table></div>'


def _rules(plan):
    cards=[]
    for s in plan.get('scenarios', []):
        phases=''.join(f'<p><b>{e(label)}</b></p>{lines(items)}' for label,items in s.get('phases',[]))
        cards.append(f'<details><summary>{e(s.get("title"))} · 规则引擎原始条件</summary>{lines(s.get("premise"))}{phases}<b>失效条件</b>{lines(s.get("invalidation"))}<p class="np-muted">上述为情景假设，仓位调整条款须在资格与信号通过后重新评估。</p></details>')
    return ''.join(cards)


def _scorecard(plan):
    sc = plan.get('scorecard')
    if not sc:
        return '<p class="np-muted">缺少上一交易日预案或价格，暂不评分。</p>'
    body=''.join(f'<tr><td>{e(r.get("name"))} <span class="np-code">{e(r.get("code"))}</span></td><td>{e(r.get("role"))}</td><td>{_performance(r.get("pct"))}</td><td>{e(r.get("status"))}</td></tr>' for r in sc.get('rows',[]))
    return f'<p>{e(sc.get("prev_day"))} · {e(sc.get("summary"))}</p><p class="np-muted">观察池收盘表现，不等于策略成交收益或胜率。</p><div class="np-scroll"><table><thead><tr><th>标的</th><th>当时角色</th><th>单日表现</th><th>结果</th></tr></thead><tbody>{body}</tbody></table></div>'


def _research_judgments(plan):
    """从已显示的收盘证据形成可复核判断，不输出资金身份或成功概率。"""
    crowding = plan.get('crowding') or {}
    sectors = (crowding.get('sectors') or {}).get('rows') or []
    stocks = (crowding.get('stocks') or {}).get('rows') or []
    bonds = (plan.get('cb') or {}).get('rows') or []
    mainline = (plan.get('decision') or {}).get('mainline')
    main = next((r for r in sectors if r.get('name') == mainline), None)
    sector_text = '缺少同日板块样本，主线持续性待核验。'
    if main:
        sector_text = f'{mainline}涨停 {number(main.get("limit_up_count"),0)} 家，较前日 {number(main.get("change"),0,signed=True)} 家。'
        sector_text += '扩散能否延续，需要核心与第二梯队共同承接。'
    shrinking = [r for r in sectors if isinstance(r.get('change'), (int,float)) and r['change'] < 0]
    if shrinking:
        weak = min(shrinking, key=lambda r:r['change'])
        sector_text += f'{weak["name"]}样本减少 {abs(weak["change"])} 家，先按收缩后的修复观察。'
    core = next((r for r in stocks if r.get('role') == 'core'), None)
    stock_text = '缺少同日持续性证据，不确认抱团核心。'
    if core:
        stock_text = f'{core["name"]}连续支持 {number(core.get("support_streak"),0)} 日，5日 {pct(core.get("r5"))}，保留为持续性观察锚。'
    risk = next((r for r in stocks if r.get('role') == 'risk'), None)
    if risk:
        stock_text += f'{risk["name"]}当日 {pct(risk.get("r1"))}，连续支持仍为 {number(risk.get("support_streak"),0)} 日；价格反弹与跟踪资格恢复分开判断。'
    known = [r for r in bonds if isinstance(r.get('r1'),(int,float))]
    bond_text = '缺少同日转债及正股数据，暂不判断联动。'
    if known:
        leader = max(known,key=lambda r:r['r1'])
        bond_text = f'{leader["name"]}单日 {pct(leader.get("r1"))}、5日 {pct(leader.get("r5"))}；继续观察正股承接与溢价变化。'
        bounce = next((r for r in known if r['r1']>0 and isinstance(r.get('r5'),(int,float)) and r['r5']<0),None)
        if bounce:
            bond_text += f'{bounce["name"]}日涨但5日仍 {pct(bounce["r5"])}，按反弹延续待验证。'
    return '<div class="np-grid" style="margin-top:16px">'+''.join(
        f'<article class="np-card"><h3>{e(title)}</h3><p>{e(text)}</p></article>' for title,text in [
            ('板块 / 扩散与收缩',sector_text),('股票 / 核心与旧团',stock_text),('转债 / 延续与反弹',bond_text)])+'</div>'


def render_panel(plan, *, is_site_mode=False):
    day=plan.get('report_date') or '日期待核验'
    target=plan.get('target_date') or '交易日待确认'
    decision=plan.get('decision') or {}
    readiness=plan.get('readiness') or {}
    crowding=plan.get('crowding') or {}
    stocks=crowding.get('stocks') or {}
    sectors=crowding.get('sectors') or {}
    bonds=plan.get('cb') or {}
    facts=plan.get('facts') or {}
    mainline=decision.get('mainline') or '尚未确认'
    reason=(readiness.get('action') or {}).get('reason') or '开仓前须通过行情、策略与盘中信号核验。'
    state='已通过执行资格，仍须核对盘中条件' if decision.get('execution_allowed') else '当前不开新仓 · 等待资格与盘中确认'
    kpis=[]
    for label,key,ratio in [('上涨占比','breadth_ratio',True),('涨停家数','limit_up',False),('跌停家数','limit_down',False),('晋级率','promotion_rate',True)]:
        value=facts.get(key)
        shown=number(value*100,1,'%') if ratio and isinstance(value,(float,int)) else number(value,0)
        kpis.append(f'<div class="np-kpi"><span>{label}</span><b>{shown}</b></div>')
    gates=decision.get('gates') or []
    gates_html='<div class="np-grid">'+''.join(f'<article class="np-card"><h3>{e(g.get("title"))}</h3><p>{e(g.get("headline"))}</p><p class="np-muted">{e(g.get("check"))}</p></article>' for g in gates)+'</div>' if gates else '<p class="np-muted">三道开关尚未形成完整规则，保持观察。</p>'
    timeline=[('9:25 · 竞价','核对公告、主线核心与风险锚；高开幅度不能单独确认抱团。'),
              ('9:35 · 承接','比较核心与跟随股，记录回落承接、上涨家数和跌停反馈。'),
              ('10:00 · 确认','核对梯队晋级和板块扩散；仍未确认则取消相应触发。'),
              ('午后 · 复核','检查强势是否扩散或回吐；债股背离时降低预期，更新失效记录。')]
    timeline_html='<div class="np-timeline">'+''.join(f'<div class="np-card"><p class="np-time">{e(t)}</p><p>{e(d)}</p></div>' for t,d in timeline)+'</div>'
    warnings=list(plan.get('warnings') or [])+list(crowding.get('warnings') or [])
    for block in [stocks,sectors,bonds]:
        warnings.extend(block.get('warnings') or [])
    warnings=list(dict.fromkeys(str(w) for w in warnings))
    sources=list(crowding.get('sources') or [])+list(bonds.get('sources') or [])
    source_html=''.join(f'<li>{e(s.get("path") or s.get("file") or s.get("source") or s)} {e(s.get("as_of") or s.get("date") or "")}</li>' if isinstance(s,dict) else f'<li>{e(s)}</li>' for s in sources)
    nav=(f'<a href="../reports/{e(day)}.html">返回同日主报告</a><a href="../index.html">报告索引</a>' if is_site_mode else '<a href="本地导航入口.html">本地导航入口</a>')
    avoid=plan.get('avoid') or []
    avoid_text='、'.join(f'{r.get("name")}（昨{r.get("height")}板）' for r in avoid[:12]) if avoid else '同日未形成可核验的断板名单；不等于市场没有风险。'
    return f'''<style>{CSS}</style><section class="np-plan" data-report-date="{e(day)}" data-target-date="{e(target)}">
      <div class="np-topline"><span>主线强度追踪 / 抱团研究与操作预案</span><span>收盘 {e(day)} → 预案 {e(target)}</span></div>
      <div class="np-eyebrow">收盘证据 · 次日条件 · 失效边界</div><h1>今日深度复盘 · 明日实战预案</h1>
      <p class="np-lead">规则主线：<b>{e(mainline)}</b>。先看板块扩散，再核验抱团持续性与债股联动。</p>
      <div class="np-status"><strong>{state}</strong><p>{e(reason)}</p><p class="np-muted">行情：{e((readiness.get('data') or {}).get('label') or '待核验')} · 策略：{e((readiness.get('strategy') or {}).get('label') or '待核验')} · 盘中：{e((readiness.get('signal') or {}).get('label') or '等待次日数据')}。研究对象不自动成为交易候选。</p></div>
      <nav class="np-nav"><a href="#np-sectors">看板块强弱</a><a href="#np-stocks">看抱团个股</a><a href="#np-bonds">看抱团转债</a><a href="#np-execution">看操作条件</a></nav>
      <div class="np-section"><h2>今日盘面事实</h2><p class="np-muted">{e(day)} 收盘快照 · {e(plan.get('micro_cycle'))} · 未知字段显示为 —</p><div class="np-kpis">{''.join(kpis)}</div>{_research_judgments(plan)}<p class="np-muted">以上是收盘研究判断。抱团个股可能跨题材，不能把它们统一归入规则主线。</p></div>
      <div class="np-section" id="np-sectors"><div class="np-section-title"><h2>01 / 抱团板块 · 扩散与收缩</h2><span class="np-muted">{_block_status(sectors)}</span></div><p class="np-summary">{e(sectors.get('summary'))}</p>{_sectors(sectors,mainline)}</div>
      <div class="np-section" id="np-stocks"><div class="np-section-title"><h2>02 / 抱团个股 · 延续与分歧</h2><span class="np-muted">{_block_status(stocks)}</span></div><p class="np-summary">{e(stocks.get('summary'))}</p>{_stocks(stocks)}</div>
      <div class="np-section" id="np-bonds"><div class="np-section-title"><h2>03 / 抱团转债 · 债股与溢价</h2><span class="np-muted">{_block_status(bonds)}</span></div><p class="np-summary">{e(bonds.get('summary'))}</p>{_bonds(bonds)}</div>
      <div class="np-section" id="np-execution"><h2>04 / 明日三道开关</h2>{gates_html}<h3 style="margin-top:20px">规则候选 · 等待确认</h3>{_candidates(plan)}</div>
      <div class="np-section"><h2>05 / 情景推演 · {e(target)}</h2>{_conditional_matrix(plan)}{_rules(plan)}</div>
      <div class="np-section"><h2>盘中执行顺序</h2>{timeline_html}<p class="np-tagline">股票 T+1：新买股票当日不能卖出。转债可日内回转，但需考虑价差、滑点与流动性；隔夜是否持有应由策略风险约束决定。</p></div>
      <details><summary>不追名单 · 断板与证据失效</summary><p>{e(avoid_text)}</p><p class="np-muted">断板仅作为风险观察，不推断次日必跌或任何反弹都为出货。追价前还须排查公告、涨跌停与不可成交风险。</p></details>
      <details><summary>昨日预案对账</summary>{_scorecard(plan)}</details>
      <details><summary>数据来源与缺失说明（{len(warnings)} 项待核验）</summary>{lines(warnings) if warnings else '<p>所列来源与报告日对齐；盘中条件仍待目标交易日核验。</p>'}<ul>{source_html}</ul><p class="np-muted">本页按报告日读取本地快照。不会在渲染时抓取新行情，也不会用后续交易日覆盖历史证据。指标口径及个别条款仍以对应来源为准。</p></details>
      <div class="np-footer">预案有效期：{e(target)}，下一交易日需重新生成。表格中的价格用于复盘参考，盘中触发需要实时行情确认。</div><nav class="np-nav">{nav}</nav>
    </section>'''


def render_document(plan, *, is_site_mode=True):
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{e(plan.get("report_date"))} 今日深度复盘 · 明日实战预案</title>'
            '<style>body{margin:0;background:#090e15;color:#e6edf3}</style></head><body>'
            + render_panel(plan,is_site_mode=is_site_mode) + '</body></html>')
