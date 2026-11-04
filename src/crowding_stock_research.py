"""Read-only, dated stock research alongside (never in place of) formal pool states."""
from __future__ import annotations

import html
import json
import math
import re
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo


def parse_quotes(text: str) -> dict:
    """Parse a bounded Tencent quote snapshot; retain each exchange timestamp."""
    quotes = {}
    for code, payload in re.findall(r'v_((?:sh|sz|bj)\d{6})="([^"]+)";', text):
        fields = payload.split('~')
        if len(fields) <= 38 or not re.fullmatch(r'\d{14}', fields[30]):
            continue
        try:
            values = [float(fields[i]) for i in (3, 4, 32, 33, 34, 37, 38)]
        except (ValueError, IndexError):
            continue
        if not all(math.isfinite(v) for v in values) or values[0] <= 0 or values[1] <= 0:
            continue
        timestamp = fields[30]
        try:
            datetime.strptime(timestamp, '%Y%m%d%H%M%S')
        except ValueError:
            continue
        quotes[code] = dict(code=code, name=fields[1].strip(), close=values[0], previous_close=values[1],
                            r1=values[2], high=values[3], low=values[4], amount_wan=values[5],
                            turnover=values[6], timestamp=timestamp,
                            date=f'{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}',
                            source='腾讯证券报价', basis='raw')
    return quotes


def price_metrics(record: dict, as_of: str, expected_dates: list[str] | None = None) -> dict:
    """One series, one adjustment basis. Missing today's bar never becomes today's MA."""
    by_date = {}
    for row in record.get('rows') or []:
        try:
            value = float(row['close'])
            if row['date'] <= as_of and math.isfinite(value) and value > 0:
                by_date[row['date']] = dict(row, close=value)
        except (KeyError, TypeError, ValueError):
            continue
    rows = [by_date[d] for d in sorted(by_date)]
    empty = dict(date=None, basis=record.get('basis'), r1=None, r5=None, r20=None,
                 ma5=None, ma10=None, above_ma5=None, dd20=None, relative_volume=None,
                 reference_date=None, since_reference=None, close=None, series=[], missing_dates=[])
    if not rows:
        return empty
    values = [r['close'] for r in rows]
    last = rows[-1]
    empty.update(date=last['date'], close=values[-1], series=rows[-35:])
    if last['date'] != as_of:
        return empty
    expected = sorted(set(d for d in (expected_dates or []) if d <= as_of))[-21:]
    missing = [d for d in expected if d >= rows[0]['date'] and d not in by_date]
    empty['missing_dates'] = missing
    if missing:
        return empty
    def ret(n):
        return 100 * (values[-1] / values[-n-1] - 1) if len(values) > n else None
    ma5 = sum(values[-5:])/5 if len(values) >= 5 else None
    ma10 = sum(values[-10:])/10 if len(values) >= 10 else None
    ref = by_date.get('2026-09-28')
    volumes = [r.get('volume') for r in rows[-6:-1]]
    volume_ok = len(volumes) == 5 and all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in volumes)
    empty.update(r1=ret(1), r5=ret(5), r20=ret(20), ma5=ma5, ma10=ma10,
                 above_ma5=values[-1] >= ma5 if ma5 is not None else None,
                 dd20=100*(values[-1]/max(values[-20:])-1) if len(values) >= 20 else None,
                 relative_volume=last.get('volume', 0)/(sum(volumes)/5) if volume_ok else None,
                 reference_date='2026-09-28' if ref else None,
                 since_reference=100*(values[-1]/ref['close']-1) if ref else None)
    return empty


def observation(metrics: dict, quote: dict | None, as_of: str) -> str:
    if not quote or quote.get('date') != as_of:
        return '最新报价不足'
    if metrics.get('date') != as_of or metrics.get('above_ma5') is None:
        return '仅当日报价 · 多日待验证'
    if quote['r1'] <= -9 and not metrics['above_ma5']:
        return '明显转弱 · 暂停强化判断'
    if metrics['above_ma5'] and (metrics.get('r5') or 0) > 0:
        return '价格延续' if quote['r1'] >= 0 else '分歧但均线未破'
    if quote['r1'] > 0 and (metrics.get('dd20') or 0) <= -15:
        return '深回撤反抽 · 未确认重入'
    if not metrics['above_ma5']:
        return '均线失守 · 修复待验证'
    return '反弹观察 · 未确认强化'


def build_stock_research(baseline: dict, prices: dict, quotes: dict, profiles: dict,
                         external: dict, *, as_of: str) -> dict:
    tracked = max((x for x in baseline.get('tracking', {}).get('daily', []) if x.get('date', '') <= as_of),
                  key=lambda x: x['date'], default={})
    old_rows = {x['code']: x for x in tracked.get('rows', [])}
    group_members = {x['code']: x for g in baseline.get('groups', []) for x in g.get('members', [])}
    eligible_profiles = {c: p for c, p in profiles.items()
                         if p.get('as_of') and p['as_of'] <= as_of}
    expected_dates = [x['date'] for x in baseline.get('automatic_phases', {}).get('daily', [])
                      if x.get('date', '') <= as_of]
    if as_of not in expected_dates:
        expected_dates.append(as_of)
    codes = set(prices.get('stocks', {})) | set(eligible_profiles)
    stocks = []
    for code in sorted(codes):
        price = prices.get('stocks', {}).get(code, {})
        quote = quotes.get(code)
        # Do not leak a future quote into an older view.
        if quote and quote['date'] > as_of:
            quote = None
        profile = eligible_profiles.get(code, {})
        previous = old_rows.get(code, {})
        metrics = price_metrics(price, as_of, expected_dates)
        current_quote = quote if quote and quote['date'] == as_of else None
        agreement = (abs(metrics['close']-current_quote['close']) <= .011
                     if current_quote and metrics['date'] == as_of and metrics.get('basis') == 'raw' else None)
        stocks.append(dict(code=code, name=(current_quote or {}).get('name') or previous.get('name')
                           or group_members.get(code, {}).get('name') or price.get('name') or code,
                           metrics=metrics, quote=current_quote, quote_raw_vs_series_agreement=agreement,
                           state=observation(metrics, current_quote, as_of),
                           formal_status=previous.get('status', '旧模型未跟踪'),
                           formal_status_date=tracked.get('date'),
                           first_seen=previous.get('first_seen'), first_admitted=previous.get('first_admitted'),
                           last_supported=previous.get('last_supported'),
                           previous_metrics=previous.get('metrics') or group_members.get(code, {}).get('metrics', {}),
                           industry=profile.get('industry', '本轮行业资料未核验'),
                           themes=profile.get('themes', []), pool=profile.get('pool', '扩展观察'),
                           role=profile.get('role', '价格观察 · 催化待核验'), profile=profile,
                           error=price.get('error')))
    index = {x['code']: x for x in stocks}
    cohorts = baseline.get('automatic_phases', {}).get('cohorts', [])
    visible_cohorts = [x for x in cohorts if x.get('formed_on', '') <= as_of]
    frozen = []
    for c in visible_cohorts[-2:]:
        members = [index[code] for code in c['codes'] if code in index]
        valid = [s for s in members if s['metrics']['date'] == as_of and s['metrics']['above_ma5'] is not None]
        strong = [s for s in valid if s['metrics']['above_ma5'] and (s['metrics']['r5'] or 0) > 0]
        frozen.append(dict(id=c['id'], formed_on=c['formed_on'], anchor=c['anchor_name'],
                           codes=c['codes'], coverage=len(valid), denominator=len(c['codes']), strong=len(strong),
                           limitation='仅计算冻结篮子价格覆盖与强势占比，不重跑全市场阶段/排名，不改写正式状态。'))
    historical = []
    for g in baseline.get('groups', []):
        historical.append(dict(id=g['id'], title=g['title'], start=g.get('start'),
                               members=[code for code in g.get('codes', []) if code in index],
                               previous_assessment=g.get('assessment')))
    return dict(schema='crowding-stock-research/v1', requested_date=as_of,
                generated_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                price_source=prices.get('source'), price_source_url=prices.get('source_url'),
                price_scope='定向个股补充，不是全市场刷新', baseline_price_date=baseline.get('market_as_of'),
                baseline_requested_date=baseline.get('requested_date'),
                formal_tracking_updated=False, automatic_phase_updated=False,
                profiles_as_of=max((p['as_of'] for p in eligible_profiles.values()), default=None),
                summary_counts=dict(Counter(x['pool'] for x in stocks)),
                stocks=stocks, frozen_cohorts=frozen, historical_groups=historical,
                external_fetched_at=external.get('fetched_at'),
                limitations=['抱团角色为研究推断，不等于机构持仓、资金流或盈利承诺。',
                             '当日报价为不复权；多日收益和均线来自每只股票单一日K序列，不拼接补洞。',
                             '缺失日K只保留当日报价，不计算多日收益/均线，不用旧数据冒充最新。',
                             '涨停复盘题材为第三方归因；没有原始公告的催化标为待核验。',
                             '生成于盘后；本报告不是历史事前选股记录，不覆盖旧池或退出档案。'])


def _e(value):
    return html.escape(str(value if value is not None else '—'), quote=True)


def _pct(value):
    return '—' if value is None else f'{value:+.2f}%'


def _clock(value):
    if not value:
        return '—'
    try:
        return datetime.fromisoformat(value).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M（北京时间）')
    except (TypeError, ValueError):
        return str(value)


def _table(headers, rows):
    return '<div class="research-table"><table><thead><tr>' + ''.join(f'<th>{_e(h)}</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{v}</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def _link(stock):
    return f'<a href="#stock-{_e(stock["code"])}">{_e(stock["name"])}<small>{_e(stock["code"][2:])}</small></a>'


def _spark(series):
    if len(series) < 2:
        return '<p class="muted">多日日K缺失，不绘制补洞曲线。</p>'
    values = [r['close'] for r in series]
    lo, hi = min(values), max(values)
    span = hi-lo or 1
    points = ' '.join(f'{10+560*i/(len(values)-1):.2f},{92-74*(v-lo)/span:.2f}' for i, v in enumerate(values))
    return f'<svg class="research-spark" viewBox="0 0 580 108" role="img" aria-label="最近{len(values)}个观测日收盘价路径，不是分时图"><line x1="10" y1="92" x2="570" y2="92" stroke="currentColor" opacity=".15"/><polyline points="{points}" fill="none" stroke="currentColor" stroke-width="2.5"/></svg><p class="tiny">{_e(series[0]["date"])} → {_e(series[-1]["date"])} · 单源收盘曲线，不同个股纵轴独立</p>'


RESEARCH_CSS = '''
.research-table{overflow:auto;margin:14px 0 24px}.research-table table{width:100%;border-collapse:collapse;font-size:13px;min-width:790px}.research-table th{text-align:left;color:var(--muted);font-weight:500;padding:12px;background:var(--panel)}.research-table td{padding:13px 12px;border-bottom:1px solid var(--edge);vertical-align:top}.research-table small,.stock-id small{display:block;color:var(--muted);font:11px/1.5 Consolas,monospace}.research-card{border:1px solid var(--edge);background:var(--panel);padding:24px;margin:18px 0;scroll-margin-top:90px}.research-card h3{font-size:24px;letter-spacing:-.4px;margin:0}.research-card summary{cursor:pointer;font-weight:700;padding:13px 0}.research-card p{margin:8px 0 15px}.research-meta{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin:10px 0}.research-meta span{padding:4px 8px;border:1px solid var(--edge)}.research-columns{display:grid;grid-template-columns:1fr 1fr;gap:24px}.research-spark{width:100%;height:108px;color:var(--new)}.research-sources a{display:inline-block;margin:5px 12px 5px 0;font-size:12px}.research-filter{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:16px 0}.research-filter input,.research-filter select{padding:10px;border:1px solid var(--edge);background:var(--panel);color:var(--text)}.research-filter input{flex:1;min-width:200px}.research-signature{border-left:5px solid var(--new);padding:24px;background:var(--panel)}.research-signature h2{font-size:clamp(24px,3vw,38px);line-height:1.4}.research-lanes{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:20px 0}.research-lanes article{padding:18px;border-top:3px solid var(--edge);background:var(--panel)}.research-lanes strong{display:block;margin-bottom:8px}.research-lanes p{font-size:13px;margin:0}.research-up{color:#bd2737}.research-down{color:#167957}.research-tag{font:12px/1.5 Consolas,monospace;color:var(--muted)}
@media(max-width:720px){.research-columns,.research-lanes{grid-template-columns:1fr}.research-card{padding:18px}.research-signature{padding:18px}}[hidden]{display:none!important}
'''


def render_stock_research(research: dict | None) -> str:
    if not research:
        return ''
    stocks = research['stocks']
    def cards(s):
        p, m, q = s['profile'], s['metrics'], s['quote'] or {}
        rows = []
        for title, key in [('上涨催化 · 事实与第三方归因', 'catalyst'), ('抱团逻辑 · 研究推断', 'logic'),
                           ('业务与财务 · 兑现边界', 'business'), ('资金与筹码 · 不等于净流入', 'flow'),
                           ('主要风险 / 相反证据', 'risk'), ('后续观察 / 失效条件', 'watch')]:
            rows.append(f'<div><h4>{title}</h4><p>{_e(p.get(key) or "本轮缺少可靠信息；不凭涨幅编造订单、业绩或游资席位。")}</p></div>')
        sources = []
        for source in p.get('sources', []):
            url = source.get('url', '')
            if not url.startswith(('https://', 'http://')) or source.get('date', '') > research['requested_date']:
                continue
            sources.append(f'<a href="{_e(url)}" target="_blank" rel="noopener noreferrer">{_e(source["title"])} · {_e(source.get("date"))}</a>')
        nums = f'当日 {_pct(q.get("r1"))} · 5日 {_pct(m.get("r5"))} · 20日 {_pct(m.get("r20"))} · 距20日收盘高点 {_pct(m.get("dd20"))}'
        ma = '—' if m.get('ma5') is None else f'{m["ma5"]:.2f}'
        body = f'<p class="muted">{_e(nums)}</p>{_spark(m["series"])}<p class="tiny">MA5 {ma}（{_e(m["basis"])}）；报价时间 {_e(q.get("timestamp"))}；日K截至 {_e(m["date"])}。报价换手率 {_pct(q.get("turnover"))} 是接口口径，不与自由流通口径混用。</p><div class="research-columns">{"".join(rows)}</div><div class="research-sources">{"".join(sources) or "暂无已核验原始催化链接；见本页来源边界。"}</div>'
        if not p:
            body = f'<details><summary>展开价格证据 · 催化待核验</summary>{body}</details>'
        return f'<article id="stock-{_e(s["code"])}" class="research-card" data-pool="{_e(s["pool"])}" data-search="{_e(s["name"]+" "+s["code"]+" "+" ".join(s["themes"]))}"><div class="research-tag">{_e(s["role"])}</div><h3>{_e(s["name"])} <small>{_e(s["code"][2:])}</small></h3><div class="research-meta"><span>{_e(s["industry"])}</span><span>{_e(" / ".join(s["themes"]) or "题材未核验")}</span><span>{_e(s["state"])}</span></div>{body}</article>'
    cards_html = ''.join(cards(s) for s in sorted(stocks, key=lambda s: (not bool(s['profile']), s['profile'].get('order', 999), s['code'])))
    pools = sorted(set(s['pool'] for s in stocks))
    filter_html = '<div class="research-filter"><label for="research-search">查找个股</label><input id="research-search" type="search" placeholder="名称、代码或板块"><label for="research-pool">观察分类</label><select id="research-pool"><option value="">全部分类</option>' + ''.join(f'<option>{_e(p)}</option>' for p in pools) + '</select><span id="research-visible" aria-live="polite"></span></div>'
    return f'<section id="stock-analysis"><h2>个股多角度分析</h2><p class="muted">判断日 {_e(research["requested_date"])}。顺序是事实、逻辑推断、相反证据、后续验证；不是买入排序。</p>{filter_html}<p id="research-empty" class="muted" hidden>没有匹配个股，请清空搜索或改选分类。</p>{cards_html}</section>'


def render_basket_selection(research: dict) -> str:
    """Explain why the frozen basket formed; separate price selection from business evidence."""
    index = {s['code']: s for s in research.get('stocks', [])}
    cohort = next((c for c in research.get('frozen_cohorts', []) if c.get('formed_on') == '2026-10-22'), None)
    if not cohort:
        return ''
    rationale = {
        'sz301313': '物理AI、具身数据与数字孪生叙事；半年报有业务方向披露，但收入下降、亏损扩大。',
        'sz300839': '主业改善叠加液冷算力第二增长曲线；三季报和10月23日调研记录提供经营线索，液冷独立利润金额仍未拆出。',
        'sz000530': '冰雪经济、制冷与液冷预期叠加价格辨识度；公告显示冰雪尚未确认收入，液冷仅零星收入，前三季度利润下滑。',
        'sz002724': '专业照明与AI智慧照明场景形成题材映射；公告明确AI业务收入规模有限，上半年利润明显下降。',
        'sz002242': '消费品牌叠加机器人/AI参股映射和前期涨幅；公告明确参股投资处早期，对主营和业绩无重大影响。',
        'sz002811': '连续涨幅和高辨识度带来动量关注；异动公告未确认独立重大催化，只能视为独立价格线。',
    }
    rows = []
    for code in cohort.get('codes', []):
        s = index.get(code)
        if not s: continue
        m = s['metrics']
        rows.append([_link(s), _e(_pct(m.get('r5'))), _e(_pct(m.get('r20'))), _e(rationale.get(code, '价格强势进入固定篮子；业务证据待补充。'))])
    table = _table(['冻结成员', '前5日', '前20日', '业务线索与反证'], rows)
    return f"""<section id=\"basket-selection\"><h2>为什么选择这些股：价格篮子与业务证据分开</h2>
<p class=\"muted\">这6只首先因为价格强度进入篮子，不是因为程序已经证明它们属于同一行业、同一资金账户或机构抱团。下面把“模型为什么选”与“市场为什么可能关注”分开。</p>
<div class=\"research-columns\"><article><h3>模型入选口径</h3><p>近5日收益超过4%、近20日收益超过8%，最近5个交易日中至少2天进入价格样本前15%。评分由5日收益百分位、20日收益百分位和持续天数构成；核心代理连续2日确认后，取核心及排名靠前候选，最多固定6只。</p><p class=\"tiny\">冻结日：2026-10-22。评分是相对强度分，不是胜率；该规则偏向已经上涨的股票，不能证明提前发现。</p></article><article><h3>抱团机制假设</h3><p>题材或业绩先吸引注意力，连续上涨提高辨识度，后续交易者继续围绕强势股参与，可能形成价格与关注度相互强化。这个假设需要分歧后承接、成员联动和参与者证据验证，单靠涨幅不足以证明。</p><p class=\"tiny\">公告、调研和财报只能说明业务线索，不能倒推出同日盘中唯一涨因；高换手也可能同时代表接力和兑现。</p></article></div>{table}
<p class=\"tiny\">博汇股份的业绩与液冷证据、海洋王的AI业务边界已补充；冰山冷热、九阳股份、郑中设计的反证也保留。10月26日收盘时该篮子只有3/6同时站上MA5且近5日为正，不能继续整体宣称强化。</p></section>"""


RESEARCH_SCRIPT = '''
(()=>{const input=document.getElementById('research-search'),select=document.getElementById('research-pool');if(!input||!select)return;const cards=[...document.querySelectorAll('.research-card')];function run(){const term=input.value.toLowerCase().trim(),pool=select.value;let n=0;cards.forEach(c=>{c.hidden=!!((pool&&c.dataset.pool!==pool)||(term&&!c.dataset.search.toLowerCase().includes(term)));if(!c.hidden)n++;});document.getElementById('research-visible').textContent=`显示 ${n} / ${cards.length} 只`;document.getElementById('research-empty').hidden=n!==0;}input.addEventListener('input',run);select.addEventListener('change',run);run();})();
'''


def generate_stock_research_html(r: dict, *, baseline_link: str = '') -> str:
    if r['requested_date'] != '2026-10-26':
        raise ValueError('Editorial conclusions are dated 2026-10-26; a different date needs new research copy.')
    index = {s['code']: s for s in r['stocks']}
    as_of = r['requested_date']
    def summary_rows(codes):
        result = []
        for c in codes:
            if c not in index:
                continue
            s = index[c]; q = s['quote'] or {}; m = s['metrics']; p = s['profile']
            change = q.get('r1')
            color = 'research-up' if change is not None and change > 0 else 'research-down'
            result.append([_link(s), _e(' / '.join(s['themes']) or s['industry']),
                           f'<span class="{color}">{_pct(change)}</span>', _e(_pct(m.get('r5'))),
                           _e(s['state']), _e(p.get('short') or s['role'])])
        return result
    headers = ['个股', '板块 / 方向', '10月26日', '近5交易日', '当前复核', '结论 / 下一步']
    core_codes = [s['code'] for s in r['stocks'] if s['pool'] in ['核心延续', '新补位', '独立消费'] and s['profile']]
    core_codes.sort(key=lambda c: index[c]['profile'].get('order', 999))
    current = _table(headers, summary_rows(core_codes))
    follow_codes = ['sz002242','sz002343','sz000530','sz002724','sz300839','sz002811','sz001216','sh688018','sz300031']
    follow_rows = []
    for code in follow_codes:
        s=index.get(code)
        if not s: continue
        follow_rows.append([_link(s), _e(s['formal_status']), _pct(s['previous_metrics'].get('r5')),
                            _pct((s['quote'] or {}).get('r1')), _e(s['state']), _e(s['profile'].get('short', '催化待核验'))])
    previous = _table(['个股','10月23日旧模型状态','旧5日收益','10月26日','最新研究复核','跟踪结论'], follow_rows)
    trial = _table(['前期候选','9月28日收盘至今','近5日','20日收盘高点回撤','当前状态','上轮最后支持日'],
                   [[_link(index[c]), _pct(index[c]['metrics']['since_reference']), _pct(index[c]['metrics']['r5']),
                     _pct(index[c]['metrics']['dd20']), _e(index[c]['state']), _e(index[c]['last_supported'])]
                    for c in ['sh600241','sz301513','sz301190','sh688137'] if c in index])
    cohort_html=''
    for cohort in r['frozen_cohorts']:
        rows=[]
        for c in cohort['codes']:
            if c not in index:continue
            s=index[c]
            rows.append([_link(s),_pct((s['quote'] or {}).get('r1')),_pct(s['metrics']['r5']),_e(s['state'])])
        cohort_html+=f'<h3>{_e(cohort["formed_on"])}冻结篮子 · {_e(cohort["anchor"])}</h3><p class="muted">多日证据覆盖 {cohort["coverage"]}/{cohort["denominator"]}；MA5上且5日为正 {cohort["strong"]}/{cohort["denominator"]}。{_e(cohort["limitation"])}</p>'+_table(['冻结成员','当日','5日','当前复核'],rows)
    hist=''
    for group in r['historical_groups']:
        if group['id']=='trial':continue
        rows=[[ _link(index[c]), _pct((index[c]['quote'] or {}).get('r1')), _pct(index[c]['metrics']['r5']), _pct(index[c]['metrics']['dd20']), _e(index[c]['state'])] for c in group['members']]
        hist+=f'<details><summary>{_e(group["title"])}</summary>'+_table(['历史个股','当日','5日','20日回撤','当前复核'],rows)+'</details>'
    pool_rows=[]
    for s in sorted(r['stocks'],key=lambda s:(s['pool'],s['profile'].get('order',999),s['code'])):
        pool_rows.append([_link(s),_e(s['pool']),_e(' / '.join(s['themes']) or '催化未核验'),_pct((s['quote'] or {}).get('r1')),
                          _e(s['profile'].get('watch') or '连续两日均线/回撤验证，并补充原始公告；不得直接升级为核心。')])
    full_baseline = f'<a href="{_e(baseline_link)}">打开上轮完整模型报告（全市场仅截至10月23日）</a>' if baseline_link else ''
    basket_selection=render_basket_selection(r)
    analysis=render_stock_research(r)
    market = r.get('market_context') or {}
    market_html = ''
    if market.get('date') == as_of:
        indices = market.get('indices') or {}
        index_rows = [[_e(v['name']),_e(v['close']),_pct(v['r1'])]
                      for v in indices.values() if v.get('date') == as_of]
        breadth = market.get('kpl_breadth') or {}
        robot = market.get('cls_robot_plate') or {}
        source_links = ' '.join(f'<a href="{_e(s["url"])}" target="_blank" rel="noopener noreferrer">{_e(s["title"])}</a>'
                                for s in market.get('sources', []) if s.get('date', '') <= as_of and s.get('url','').startswith('https://'))
        plate_change = robot.get('rise_range')
        market_html = f'''<section id="crowding-logic"><h2>为什么会抱团，又为什么开始分化？</h2>
<p class="muted">先看市场环境与反证，再讨论资金行为。下面的因果解释是研究推断，不是逐笔账户证明。</p>
{_table(['指数','收盘点位','当日'],index_rows)}
<p class="tiny">开盘啦截面：上涨 {_e(breadth.get('SZJS'))}、下跌 {_e(breadth.get('XDJS'))}、涨停 {_e(breadth.get('ZT'))}、跌停 {_e(breadth.get('DT'))}；该接口口径不与剔除ST的统计混算。财联社机器人概念板块 {_pct(plate_change*100 if isinstance(plate_change,(int,float)) else None)}，核心上涨不代表全板块普涨。{source_links}</p>
<div class="research-columns"><article><h3>情绪与结构</h3><p>推断：指数分化、个股跌多涨少时，高辨识度叙事可能吸引交易注意力，呈现“部分核心强、后排弱”。机器人板块整体下跌而部分核心继续涨，是集中化的线索，但没有账户持仓证据，不能直接认证机构抱团。</p></article>
<article><h3>产业环节与主题</h3><p>数据与仿真（凡拓）→ 感知与触觉（福莱 / 泛亚）→ 灵巧手与驱动（兆威 / 华阳）→ 腱绳材料（大业）。这是产业映射，不是同一笔订单链；送样、参展和小规模营收均不能写成利润兑现。</p></article>
<article><h3>筹码与交易博弈</h3><p>多日强势和連板会建立辨识度，但高换手同时意味着分歧。凡拓与大业今日换手高，可能是接力也可能是出货；连板转趋势后应核对承接，不能只看股价红绿。没有完整龙虎榜、持仓和逐笔证据，不写“某游资统一抱团”。</p></article>
<article><h3>周期与相反证据</h3><p>原6只价格篮子跨主题，今日只有3只维持MA5上且5日为正；冰山、郑中明显负反馈。前周期的近岸反抽与时代 / 尚水 / 善水继续下跌并存，说明新旧周期可以分化，不能用单日上涨宣布旧团出清完成。</p></article>
<article><h3>基本面与估值</h3><p>凡拓仍处转型与亏损背景，大业腱绳未量产，兆威相关收入占比极低，九阳机器人为早期参股。抱团的价格溢价与主营盈利分开；公司澄清和财报是检验叙事的必要相反证据。</p></article>
<article><h3>后续三条验证路径</h3><p>延续：核心守均线、后排承接、订单证据增加。切换：新梯队获得连续支持、旧核心转弱。失败：多核心放量破位、反抽不过均线、亏钱效应扩散。三个路径均可能发生，观察池不是确定买卖计划。</p></article></div></section>'''
    css='''
:root{color-scheme:light;--bg:#edf3f8;--panel:#fff;--edge:#ccd8e6;--text:#172d46;--muted:#536b84;--new:#087e91}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.8 "Microsoft YaHei","PingFang SC",sans-serif}a{color:#076b83;text-decoration:none}a:hover{text-decoration:underline}a:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #b15b16;outline-offset:3px}nav{position:sticky;top:0;z-index:5;display:flex;gap:22px;flex-wrap:wrap;padding:13px max(20px,calc((100vw - 1220px)/2));background:#172d46;color:white}nav a{color:#d5eef2;font-size:13px}main{max-width:1220px;padding:35px 24px 70px;margin:auto}header{border-bottom:1px solid var(--edge);padding:15px 0 24px}h1{font-size:clamp(30px,4vw,49px);line-height:1.3;margin:10px 0}h2{font-size:27px}h3{font-size:20px}h4{font-size:15px;margin:12px 0 5px}p{margin:8px 0 16px}.muted,.tiny{color:var(--muted)}.tiny{font-size:12px}.alert{background:#fff2df;border:1px solid #ddbc8d;padding:15px 20px;margin:20px 0;font-size:13px}section{margin-top:42px;scroll-margin-top:75px}.research-sources{border-top:1px solid var(--edge);padding-top:12px}details{padding:12px 0}summary{cursor:pointer}.source-boundary{background:var(--panel);padding:22px}footer{margin-top:40px;font-size:12px;color:var(--muted)}@media(max-width:720px){main{padding:20px 14px 50px}nav{gap:10px 18px;padding:11px 14px}.research-table table{min-width:720px}}@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
'''
    market_nav = '<a href="#crowding-logic">抱团逻辑</a>' if market_html else ''
    basket_nav = '<a href="#basket-selection">入选原因</a>' if basket_selection else ''
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>抱团股研究 · {as_of}收盘</title><style>{css}{RESEARCH_CSS}</style></head><body><nav aria-label="报告导航"><a href="#conclusions">结论</a><a href="#previous-tracking">上一周期追踪</a><a href="#watch-pool">股票池观察</a>{basket_nav}{market_nav}<a href="#stock-analysis">个股分析</a><a href="#sources">来源边界</a></nav><main>
<header><div class="research-tag">A股 · 收盘研究 / {as_of}</div><h1>核心仍在，队伍已分化。</h1><p class="muted">最近抱团股、前一周期个股追踪、股票池观察。先给结论，再看业务、催化、筹码与失效条件。</p><p class="tiny">定向行情截至 {as_of}；外部信息抓取 {_e(_clock(r['external_fetched_at']))}。上轮全市场模型行情 {_e(r['baseline_price_date'])}，本轮未改写正式入池、退出与自动阶段。</p></header>
<section id="conclusions"><div class="research-signature"><h2>机器人 / 物理AI仍是重点观察，<br>但不是整个旧篮子一起强化。</h2><p>研究推断：凡拓数创、大业股份、兆威机电保留趋势辨识度；华阳智能、襄阳轴承、福莱新材是梯队 / 新补位观察；上纬新材、泛亚微透是事件观察。上海九百属于独立消费线，不拼成机器人龙头链。</p></div>
<div class="research-lanes"><article><strong>核心延续</strong><p>凡拓 +10.43%、大业 +8.08%、兆威 +4.94%。强势不等于低风险；尤其关注高换手、未兑现业务与公告提示。</p></article><article><strong>旧篮子负反馈</strong><p>冰山 -9.97%、慈文 -10.01%、华瓷 -9.99%。九阳 -0.05%但略低于MA5。旧状态不能照搬至今天。</p></article><article><strong>上一周期修复未齐</strong><p>时代、尚水、善水继续下跌；近岸 +13.08%属于深回撤反抽，未因此自动重入。</p></article></div>
<p class="alert">当前6只冻结篮子，多日证据完整6/6，MA5上且5日为正仅3/6。本轮定向样本不能替代全市场排名，不继续宣称今天“自动聚焦强化”。</p>{current}</section>
<section id="previous-tracking"><h2>前一周期个股追踪</h2><p class="muted">旧身份与新复核分列；上轮状态截至10月23日，当前研究截至10月26日，不覆盖历史记录。</p>{previous}<h3>9月28日试错池 · 逐只复核</h3><p class="tiny">区间收益以9月28日收盘为基点，不是从首次推荐成交计算；20日回撤为距窗口收盘高点，不是盘中最大回撤。四只在上轮已退出或归档，不把今天反抽倒填为已确认核心。</p>{trial}{cohort_html}<h3>更早医药、农业、消费与并行方向</h3>{hist}</section>
<section id="watch-pool"><h2>股票池观察</h2><p class="muted">研究分层不是交易资格。新出现不等于低位；20%涨停股也可能已经高度拥挤。</p>{_table(['股票','研究分类','板块','当日','升级 / 失效观察'],pool_rows)}</section>
    {basket_selection}{market_html}{analysis}<section id="sources" class="source-boundary"><h2>证据、来源与边界</h2><p>报价与定向日K来自腾讯证券；机器人主线观察来自财联社；涨停梯队及题材归因来自开盘啦。不同统计口径不混用；没有逐笔成交、完整龙虎榜或基金持仓验证，不声称机构抱团或主力净流入。</p><ul>{''.join(f'<li>{_e(v)}</li>' for v in r['limitations'])}</ul><p>新华文轩、澳弘电子、采纳股份日K刷新失败：只显示10月26日报价，多日指标留空。此前9月29日冻结篮子的多日覆盖仅3/6，暂停整体修复确认。沈鼓集团本次补充得到上市后短序列，若不足20日指标则留空。</p><p>外部接口的“penAI”文字存在拼写缺陷；本报告未把它作为已核验的OpenAI公司事实或确定性催化。公告落款、网站发布日期与抓取时间分别记录，盘后新公告不用于解释同日盘中下跌。</p><p>{full_baseline}</p><p class="tiny">正式模型未刷新：价格阶段最后可见日仍为10月23日。本专题是10月26日独立研究补充，不能用于事前选股收益归因。</p></section><footer>研究观察，不构成个性化投资建议或自动交易信号。先验证事实，再讨论价格；先看失效，再看弹性。</footer></main><script>{RESEARCH_SCRIPT}</script></body></html>'''


def stock_research_markdown(r: dict) -> str:
    if r['requested_date'] != '2026-10-26':
        raise ValueError('Editorial conclusions are dated 2026-10-26; a different date needs new research copy.')
    lines=[f'# 抱团股研究 · {r["requested_date"]}收盘','',
           '## 结论先行','机器人/物理AI核心延续，但原6只冻结篮子只有3只维持MA5上且5日为正；不能把10月23日自动“聚焦强化”照搬至今天。',
           f'定向行情截至{r["requested_date"]}；上轮全市场模型截至{r["baseline_price_date"]}，本轮未重跑正式入池、退出或自动阶段。','',
           '## 上一周期追踪','时代万恒、尚水智能、善水科技继续走弱；近岸蛋白单日反抽，不自动视作重入。','',
           '|股票|10月26日|5日|20日回撤|MA5|最新研究复核|旧模型状态（10月23日）|','|---|---:|---:|---:|---:|---|---|']
    ordered=sorted(r['stocks'],key=lambda s:(not bool(s['profile']),s['profile'].get('order',999),s['code']))
    for s in ordered:
        m=s['metrics'];ma='—' if m['ma5'] is None else f'{m["ma5"]:.2f}'
        lines.append(f'|{s["name"]} {s["code"][2:]}|{_pct((s["quote"] or {}).get("r1"))}|{_pct(m["r5"])}|{_pct(m["dd20"])}|{ma}|{s["state"]}|{s["formal_status"]}|')
    lines += ['', '## 为什么选择这些股：价格篮子与业务证据分开',
              '2026年10月22日冻结的6只股票首先因为价格强度入选：近5日收益超过4%、近20日收益超过8%，最近5个交易日中至少2天进入价格样本前15%。评分由5日收益百分位、20日收益百分位和持续天数组成；核心代理连续2日确认后固定最多6只。评分是相对强度分，不是胜率，也不证明同一行业、同一资金账户或机构抱团。',
              '', '|冻结成员|前5日|前20日|业务线索与反证|','|---|---:|---:|---|']
    selection_reason = {
        'sz301313':'物理AI/具身数据方向；半年报有业务披露，但收入下降、亏损扩大。',
        'sz300839':'主业改善叠加液冷算力；三季报和调研有线索，液冷独立利润未拆出。',
        'sz000530':'冰雪/制冷/液冷预期；冰雪尚未确认收入，液冷仅零星收入，利润下滑。',
        'sz002724':'AI智慧照明映射；公司称相关收入规模有限，利润明显下降。',
        'sz002242':'消费品牌叠加机器人参股映射；参股处早期，对主营和业绩影响小。',
        'sz002811':'连续涨幅带来动量关注；公告未确认独立重大催化，只能视为独立价格线。',
    }
    for c in ['sz301313','sz300839','sz000530','sz002724','sz002242','sz002811']:
        s=next((x for x in ordered if x['code']==c),None)
        if s:
            lines.append(f'|{s["name"]} {c[2:]}|{_pct(s["metrics"].get("r5"))}|{_pct(s["metrics"].get("r20"))}|{selection_reason[c]}|')
    lines += ['', '抱团机制仍只是研究假设：题材或业绩先吸引注意力，连续上涨提高辨识度，后续交易者继续围绕强势股参与。需要分歧后承接、成员联动和参与者证据验证；高换手也可能同时代表接力和兑现。10月26日收盘该篮子只有3/6同时站上MA5且近5日为正。', '', '## 个股多角度分析']
    for s in ordered:
        p=s['profile']
        if not p:continue
        lines+=['',f'### {s["name"]} / {s["code"][2:]}',f'板块：{s["industry"]}；题材：{"、".join(s["themes"])}；研究分类：{s["pool"]}。']
        for label,key in [('催化','catalyst'),('抱团逻辑（推断）','logic'),('业务与财务','business'),('资金与筹码','flow'),('风险','risk'),('后续验证','watch')]:
            lines.append(f'- {label}：{p.get(key,"本轮未核验")}')
        for source in p.get('sources',[]):
            if source.get('date','')<=r['requested_date'] and source.get('url','').startswith('https://'):
                lines.append(f'- 来源：[{source["title"]}]({source["url"]})（{source.get("date")}）')
    lines+=['','## 来源边界',*['- '+v for v in r['limitations']],'- 3只日K获取失败，多日指标留空；冻结篮子整体确认暂停。','- 盘后公告不倒推为同日盘中下跌的确定原因。']
    return '\n'.join(lines)+'\n'
