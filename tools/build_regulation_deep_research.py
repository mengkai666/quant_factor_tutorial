"""Build the dated regulation research report from preserved local evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from html import escape
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/regulation_research_20261103'
PERIOD = 'post_20260706'
NAMES = {'sz301313': '凡拓数创', 'sz300006': '莱美药业', 'sz002724': '海洋王',
         'sh603278': '大业股份', 'sz300949': '奥雅股份', 'sh605488': '福莱新材', 'sz300839': '博汇股份'}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def pct(value):
    return '—' if value is None else f'{value:+.2f}%'


def table(headers, rows):
    return '\n|' + '|'.join(headers) + '|\n|' + '|'.join('---' for _ in headers) + '|\n' + ''.join(
        '|' + '|'.join(str(v).replace('|', '／') for v in row) + '|\n' for row in rows) + '\n'


def stats_markdown(data):
    groups = data['groups']
    def select(cohort, board, split='all', value='all'):
        return next(r for r in groups if r['cohort'] == cohort and r['board'] == board
                    and r['period'] == PERIOD and r['split'] == split and r['value'] == value)
    rows = []
    for cohort, label in [('near_only', '首次接近且未越线'), ('cross_first', '首次数值越线')]:
        for board in ['主板', '创业板']:
            r = select(cohort, board)
            h = r['h10']
            rows.append([label, board, r['n'], f"{r['h5']['n']}/{h['n']}", pct(r['h5']['median']), pct(h['median']),
                         f"{h['positive']}/{h['n']}", f"{h['close_loss_ge15']}/{h['n']}"])
    result = ('主表固定观察日为2026年7月6日至10月30日。新旧交易规则均采用区间收益差公式；按7月6日分段用于区分规则时期，'
              '不表示此前公式不同。完整原池为133个首次预警事件，其中5个起点已经越线，剔除后接近组为128个；'
              '首次越线另有32个事件。这里“首次”以连续预警区间为单位，是该轮区间内首次接近或越线，'
              '不是个股上市以来首次，也不是已核验的公告重置周期；退出预警后再进入可能重复入组。下表为分段后的子样本。\n\n'
              '原输入并非全程等覆盖。7/6后68个原口径预警中，完整前10／30日历史分别只有65／59个；'
              '21个首次越线中分别为20／19个。较短的有效子窗口可能触发筛查，完整的较长窗口却缺失。'
              '所以不能将本池当成全市场完整名单，或把分段差异都归因于规则变化。\n\n')
    result += table(['事件', '板别', '事件数', '5/10日完整数', '5日收盘收益中位', '10日收盘收益中位', '10日上涨数/完整数', '后10日曾较观察日收盘跌≥15%'], rows)
    same_ten=next(r for r in data['complete10_matched_horizon_groups'] if r['cohort']=='cross_first' and r['board']=='主板'
                  and r['period']==PERIOD and r['split']=='all')
    result += ('**接近本身没有显示出稳定正向优势。** 固定同一批'+str(same_ten['n'])+'个完整10日的主板首次越线事件，'
               '10日收盘收益中位为'+pct(same_ten['h10']['median'])+'，次日中位却为'
               +pct(same_ten['h1']['median'])+'；这不是买入后能平稳拿到的利润。\n\n')
    rows = []
    for cohort, label in [('near_only', '接近'), ('cross_first', '越线')]:
        for board in ['主板', '创业板']:
            for value, state in [('limit', '收盘涨停'), ('nonlimit', '收盘未涨停')]:
                r = select(cohort, board, 'limit_state', value)
                rows.append([label, board, state, '/'.join(str(r[f'h{k}']['n']) for k in [1,5,10]), pct(r['h1']['median']),
                             pct(r['h5']['median']), pct(r['h10']['median']), f"{r['h10']['positive']}/{r['h10']['n']}"])
    result += '### 当日涨停与未涨停，确实需要分组\n\n'
    result += table(['事件', '板别', '当日状态', '1/5/10日完整数', '次日中位', '5日中位', '10日中位', '10日上涨数/完整数'], rows)
    result += ('主板接近阈值且未封板组，完整16个事件中只有3个在后10日收盘上涨，中位为−15.19%；这是降低追涨优先级的依据。'
               '但封板组仍有失败，且创业板接近且封板的完整样本仅3个，不能把3/3包装成100%胜率。'
               '首次越线的各小组同样很小，不能据此决定真实仓位。\n\n'
               '本段所有接近／首次数值越线事件当日收盘均上涨，因此这组数据无法检验“当日上涨越线与当日下跌越线”的差别。'
               '收盘未涨停也不等于冲高回落，后者需要OHLC乃至分钟证据。\n\n'
               '**跨期限分母可能变化。** 例如主板越线封板组，所有可用次日样本中位是+3.35%；固定同一批6个完整10日事件后，'
               '1／3／5／10日中位依次为−1.65%／+1.24%／+3.93%／+10.63%。因此表中各期限不能连成一条可交易的收益曲线。\n\n')
    result += '### 10日与30日，可能对应不同的行情阶段\n\n'
    rows=[]
    for r in data['cross_window_limit_groups']:
        if r['period']==PERIOD and r['board']=='主板' and r['limit_state']=='limit':
            rows.append([r['trigger_window']+'日阈值', r['h10']['n'], pct(r['h10']['median']),
                         f"{r['h10']['positive']}/{r['h10']['n']}", '、'.join(e['name'] for e in r['examples'] if e['h10_return'] is not None)])
    result+=table(['主板越线且封板', '10日完整数', '10日中位', '上涨数/完整数', '完整案例'],rows)
    result+=('10日阈值组可能更多反映快速加速，30日阈值组可能更多反映累计涨幅透支；这只是阶段假说，不能把4/4或0/2当可靠概率。'
             '主板封板越线总体中位虽为正，其中最差后10日收益仍为−31.78%，最深收盘峰谷回撤约−42.44%。\n\n'
             '创业板封板越线的4个完整事件中，3个来自同一只万邦医药；每只股票只留首例后，仅剩2个完整结果，'
             '中位由−2.30%变为+5.35%。依赖少数重复个股的结果，不适合决定方向或仓位。\n\n')
    rows = []
    for r in data['sideways_groups']:
        if r['period'] == PERIOD:
            rows.append([r['board'], r['days'], r['n'], r['h10']['n'], pct(r['h10']['median']),
                         f"{r['h10']['positive']}/{r['h10']['n']}"])
    result += '### 整理天数不能单独形成买点\n\n'
    result += table(['板别', '已完成整理天数', '事件数', '后10日完整数', '后10日收盘收益中位', '上涨数/完整数'], rows)
    result += ('这只是满足原收盘整理定义后再观察的结果，不是“突破并回踩确认”策略的回测。'
               '不同天数组不单调改善，不能用多横几天替代新的买盘证据。'
               '同一时期的接近／越线事件全部在MA5之上，几乎都创近期收盘高；这两项在本样本里几乎没有额外筛选能力，'
               '不能宣传为已验证的优势因子。\n\n'
               '按股票去重后，主板接近且未封板组仍为12个完整样本、中位−14.25%、3/12上涨，弱势关联较稳。'
               '但“未封板”不是“承接不足”的同义词；具体承接还须用价格平台、量价与分钟记录核验。'
               '详见[完整统计与缺数记录](stats_notes.html)、[逐项重算校验](stats_validation.json)。\n\n')
    return result


def case_inputs():
    archive = ROOT / 'output/trading_playbooks/review_2026-11-02/stock_evidence'
    inputs, chart = [], []
    for code, name in NAMES.items():
        path = archive / f'bars_{code}_2026-11-02.json'
        if not path.is_file():
            path = OUT / 'raw_cases' / f'bars_{code}_2026-11-02.json'
        raw = read_json(path)
        rows = [r for r in raw['rows'] if '2026-10-09' <= r['date'] <= '2026-11-02']
        if not rows or rows[-1]['date'] != '2026-11-02':
            raise ValueError(f'Missing complete case cutoff: {code}')
        for r in rows:
            if not 0 < r['low'] <= min(r['open'], r['close']) <= max(r['open'], r['close']) <= r['high']:
                raise ValueError(f'Invalid OHLC: {code}/{r["date"]}')
        chart.append({'code': code, 'name': name, 'rows': rows, 'url': raw.get('url', raw.get('source_url'))})
        inputs.append(path)
    return chart, inputs


def case_markdown(chart):
    descriptions = {
        'sz301313': '已公告严重异动；10/26—11/6重点监控。11/2先创55.00新低再收复旧锚，属于急跌后修复，等待新平台。',
        'sz300006': '普通异动已核验，严重异动未确认；11/2开盘低于旧高、盘后收回，只能说明修复，不证明旧方案已触发。',
        'sz002724': '10/30数值刚越线且强收，随后公告确认严重异动；11/2一字跌停。强收盘不足以保护隔夜仓位。',
        'sh603278': '10/30数值刚越线且强收，10/31公告确认严重异动；11/2开盘失守旧平台。不能继续解释为主动降速。',
        'sz300949': '10/30接近阈值，11/2收盘上涨；上涨与盘中先触失效条件可以同时发生，需先验证规则顺序。',
        'sh605488': '原近阈值观察股；后续按平台和承接重新评估，不能仅因横盘2日就买。补充日线为单源。',
        'sz300839': '原预警后整理案例；把恢复前高与收盘整理分开检验。补充日线为单源。',
    }
    rows = []
    for c in chart:
        last, prev = c['rows'][-1], c['rows'][-2]
        rows.append([c['name'], f"{prev['close']:.2f}", '/'.join(f'{last[k]:.2f}' for k in ['open','high','low','close']),
                     pct((last['close']/prev['close']-1)*100), descriptions[c['code']]])
    result = ('以下为11月2日案例补充，未混入固定10月30日截止的历史统计。五只原重点股票使用已保存的新浪日线和腾讯同日OHLC复核；'
              '福莱新材、博汇股份使用本轮补取的新浪日线，单源状态明确保留。\n\n')
    result += table(['股票', '10/30收盘', '11/2开／高／低／收', '11/2日涨跌', '对战法的启发'], rows)
    result += ('### 凡拓：越线后可延续，失去旧结构后须降级\n\n'
               '10/19—20两连板，10/21—22不再涨停，10/23收61.44并公告严重异动；10/26收67.85，较前收上涨10.43%。'
               '这说明“严重异动公布后下一日必跌”不成立。随后10/30收59.60，已经低于10/23收盘及10/28低点62.01，'
               '判断依据应切换到结构受损，不能仅凭还在监控期就把回落解释成蓄势。11/2最低55.00、收62.49，重新收复旧锚，'
               '但当天仍创阶段新低；只能重评修复，不自动沿用10/30的旧买区或宣布二波。'
               '[严重异动公告](https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12658399&stockid=301313)；'
               '[重点监控风险提示](https://www.cczq.com/home/a/edu/fxts/st/20261026/27567.html)。\n\n'
               '### 莱美：临界未封板、深回撤、再收复，是三个不同阶段\n\n'
               '10/16前收8.13，按20%价格限制取分位，涨停价9.76；最高9.76而收9.75，是触板未封。'
               '10/20收7.75，较10/16收盘下跌20.51%；10/28收9.92重新越过旧高，随后仍需检验站稳。'
               '这是一条高波动修复路径，不能提前把10/16的失守命名为成功洗盘。'
               '[10/14普通异动公告](https://static.cninfo.com.cn/finalpage/2026-10-14/1225605365.PDF)。\n\n'
               '### 海洋王与大业：极强收盘仍可能换来隔夜下跌\n\n')
    rows = []
    for c in chart:
        if c['code'] not in ['sz002724', 'sh603278']:
            continue
        d = {r['date']:r for r in c['rows']}
        r, n = d['2026-10-30'], d['2026-11-02']
        clv = (r['close']-r['low'])/(r['high']-r['low'])*100
        rows.append([c['name'], f'{clv:.1f}%', pct((n['open']/r['close']-1)*100),
                     pct((n['close']/r['close']-1)*100)])
    result += table(['股票', '10/30收盘位置', '11/2开盘相对前收', '11/2收盘相对前收'], rows)
    result += ('这组反例把“观察资格”和“可执行买点”分开：10/30强收只提供观察资格，'
               '下一日开盘后的新信息可以立即撤销它。公告变化、题材和风险偏好都可能参与；没有隔离这些因素就不能说是越线本身造成跌停。'
               '10/30市场上涨占比15.94%、跌停82家；11/2上涨占比85.30%、跌停8家，两股仍走弱。'
               '因此既要看到前一日市场承接脆弱，也不能把后一日个股失败简单归为大盘差。\n\n'
               '此外，海洋王风险公告披露扣非亏损等信息，大业公告说明机器人腱绳仍处研发送样、未产生实质收益。'
               '这些内容与数字越线同时被交易者重新评估，无法仅靠日线分解各自影响。'
               '[海洋王公告](https://static.cninfo.com.cn/finalpage/2026-11-02/1225650401.PDF)；'
               '[大业公告](https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12684212&stockid=603278)。'
               '详见[逐只案例、事件时间与来源](cases_notes.html)。\n\n')
    return result


CSS = r'''
:root{--bg:#f5f8fc;--paper:#fff;--ink:#172d49;--muted:#52657c;--line:#d6e0ed;--blue:#205bd5;--teal:#157977;--red:#b34445;--soft:#eaf0fa;--amber:#8c5810}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:80px}body{margin:0;color:var(--ink);background:var(--bg);font:16px/1.85 'Microsoft YaHei','Segoe UI',sans-serif}a{color:var(--blue);text-underline-offset:3px}a:hover{color:#123981}button,input,select{font:inherit}button,select{cursor:pointer}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #8aaef6;outline-offset:3px}
.topbar{background:var(--ink);color:#fff;padding:14px max(22px,calc((100vw - 1100px)/2));display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between}.topbar a{color:#d8e7ff}.topbar small{font-size:13px}.shell{max-width:1100px;margin:auto;padding:32px 30px 70px}.hero{display:grid;grid-template-columns:1.3fr 1fr;gap:35px;align-items:center;padding:20px 0 35px}.hero h1{font-family:'DengXian','Microsoft YaHei',sans-serif;font-size:clamp(30px,4vw,46px);line-height:1.25;margin:15px 0;font-weight:700;letter-spacing:-1px}.hero p{color:var(--muted);margin:12px 0}.eyebrow{color:var(--blue);font-size:13px;letter-spacing:2px;font-weight:bold}.rail{border-left:3px solid var(--blue);padding-left:24px}.rail div{padding:8px 0 10px;position:relative}.rail div:before{content:'';position:absolute;width:11px;height:11px;border-radius:50%;background:var(--blue);left:-31px;top:18px}.rail b{display:block}.rail span{font-size:13px;color:var(--muted)}
.jump{position:sticky;top:0;z-index:5;background:var(--bg);border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:12px 0;display:flex;gap:8px 22px;flex-wrap:wrap}.jump a{font-size:14px;text-decoration:none;font-weight:600}article{padding:20px 0}article>h1{font-size:27px;margin:14px 0}h2{font-size:26px;line-height:1.5;margin:56px 0 20px;border-top:2px solid var(--ink);padding-top:20px}h3{font-size:20px;margin:32px 0 14px}p{margin:16px 0}strong{font-weight:700}blockquote{margin:22px 0;padding:12px 24px;border-left:4px solid var(--blue);background:var(--soft)}.table-wrap{overflow-x:auto;margin:24px 0;background:var(--paper);border:1px solid var(--line);border-radius:9px}table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.75}th{background:var(--soft);font-weight:600;text-align:left}th,td{padding:12px 15px;border-bottom:1px solid var(--line);vertical-align:top;min-width:90px}td:first-child{font-weight:600}tr:last-child td{border-bottom:0}li{margin:8px 0}code{overflow-wrap:anywhere}hr{border:0;border-top:1px solid var(--line)}
.interactive{background:var(--paper);border:1px solid var(--line);padding:24px;border-radius:12px;margin:30px 0}.interactive h3{margin-top:0}.fields{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}label{display:flex;flex-direction:column;gap:6px;font-size:14px;color:var(--muted)}input[type=number],select{width:100%;min-width:0;padding:9px 10px;border:1px solid #91a6c3;border-radius:5px;background:white;color:var(--ink)}.calc-result{display:flex;gap:24px;flex-wrap:wrap;margin-top:20px}.calc-result b{font-size:24px;color:var(--blue);font-family:Consolas,monospace}.calc-result span{display:block;font-size:13px;color:var(--muted)}.note{font-size:13px;color:var(--muted)}.chart-controls{display:flex;gap:16px;align-items:end;justify-content:space-between;flex-wrap:wrap}.chart-controls label{min-width:170px}.chart-wrap{width:100%;margin-top:20px}.chart-wrap svg{display:block;width:100%;height:310px;overflow:visible}.chart-readout{padding:12px 0;min-height:50px;font-size:14px;line-height:1.6;font-variant-numeric:tabular-nums}input[type=range]{width:100%;accent-color:var(--blue)}.decisions{display:flex;flex-wrap:wrap;gap:9px}.decisions button{background:var(--soft);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:9px 13px;font-size:14px}.decisions button[aria-pressed=true]{background:var(--blue);color:white;border-color:var(--blue)}.decision-result{border-left:3px solid var(--blue);padding:6px 0 6px 18px;margin-top:20px;min-height:125px}.decision-result p{margin:6px 0}.footer{font-size:13px;color:var(--muted);padding-top:30px;border-top:1px solid var(--line)}
@media(max-width:720px){.shell{padding:20px 18px 45px}.hero{grid-template-columns:1fr;gap:15px}.hero h1{font-size:34px}.rail{margin:0 0 10px 8px}.rail div{padding:4px 0}.rail div:before{top:13px}.jump{gap:8px 16px}.fields{grid-template-columns:1fr 1fr}.interactive{padding:16px}h2{font-size:23px}table{font-size:13px}th,td{padding:9px 11px;min-width:100px}.chart-wrap svg{height:280px}.chart-readout{min-height:65px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}@media print{body{background:#fff}.shell{max-width:none;padding:0}.topbar,.jump,.decisions{display:none}.hero{padding:0}h2{break-after:avoid}table{font-size:11px}.interactive{break-inside:avoid}.table-wrap{overflow:visible}a{color:inherit}}
'''

CALCULATOR = '''<section class="interactive" id="calculator" aria-label="单窗口余量演示"><h3>调整数字，看“偏离余量”与“价格余量”的差别</h3><div class="fields">
<label>起算股价（元）<input id="base-price" type="number" min="0.01" step="0.01" value="10"></label>
<label>当前股价（元）<input id="now-price" type="number" min="0.01" step="0.01" value="19"></label>
<label>指数区间涨跌（%）<input id="index-return" type="number" min="-99.99" step="0.1" value="5"></label>
<label>阈值（百分点）<select id="threshold"><option value="100">100 / 10日</option><option value="200">200 / 30日</option></select></label></div>
<div class="calc-result" id="calc-result" aria-live="polite"></div><p class="note">仅演示一个已确定的起算区间。不是实时监管价格；未模拟窗口切换、公告重置、异动次数、除权及最小报价单位。</p></section>'''

CHART = '''<section class="interactive" id="case-chart"><div class="chart-controls"><label>查看个股日线<select id="case-select"></select></label><span class="note">未复权价格（元） · 红：收≥开；绿：收&lt;开</span></div><div class="chart-wrap" id="candles"></div><label for="day-slider">逐日查看（相邻刻度为有行情的交易日）</label><input id="day-slider" type="range" min="0" value="0"><div class="chart-readout" id="chart-readout" aria-live="polite"></div><p class="note" id="event-label"></p></section>'''

DECISION = '''<section class="interactive" id="decision"><h3>按已经出现的事实，选择下一步</h3><div class="decisions" role="group" aria-label="走势状态">
<button type="button" data-state="near" aria-pressed="true">接近但未整理</button><button type="button" data-state="box" aria-pressed="false">整理后突破</button><button type="button" data-state="cross" aria-pressed="false">越线当日强收</button><button type="button" data-state="fail" aria-pressed="false">硬失效先发生</button></div><div class="decision-result" id="decision-result" aria-live="polite"></div></section>'''

JS = r'''
const $=id=>document.getElementById(id);
function calc(){const b=+$('base-price').value,p=+$('now-price').value,ir=+$('index-return').value,h=+$('threshold').value;
if(!(b>0&&p>0&&ir>-100)){ $('calc-result').textContent='请输入正数股价及大于−100%的指数收益。';return;}
const d=(p/b-1)*100-ir, critical=b*(1+h/100+ir/100), space=(critical/p-1)*100;
$('calc-result').innerHTML=`<div><span>当前偏离值</span><b>${d.toFixed(2)}</b> 个百分点</div><div><span>单窗口临界价</span><b>${critical.toFixed(2)}</b> 元</div><div><span>${space>=0?'从现价至临界价':'现价已在该临界价上方'}</span><b>${space>=0?'+':''}${space.toFixed(2)}%</b></div>`;}
['base-price','now-price','index-return','threshold'].forEach(id=>$(id).addEventListener('input',calc));calc();
const decisions={near:['等待稳定结构','接近阈值只是观察条件。先核公告与实际价格余量，再等平台形成；没有新需求证据不提前追涨。'],box:['进入战法一的确认阶段','完整收盘突破后，下一交易日观察回踩承接、均价与同题材同步。硬失效未发生、分钟条件完整核验后，才记录模拟触发。'],cross:['先核公告，再看下一日承接','强收盘只提供候选资格。海洋王和大业的隔夜反例说明，不能跳过公告后的开盘及流动性核验。'],fail:['取消当日原案，后续另行建案','买入前先触硬失效位，当日不重启这份研究草案。已有仓位按T+1与可成交条件降风险；反弹不能改写先失效的顺序。']};
function choose(k){document.querySelectorAll('[data-state]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.state===k)));$('decision-result').innerHTML=`<b>${decisions[k][0]}</b><p>${decisions[k][1]}</p>`;}
document.querySelectorAll('[data-state]').forEach(b=>b.addEventListener('click',()=>choose(b.dataset.state)));choose('near');
const eventText={sz301313:'事件锚：10/23收盘后严重异动公告；10/26起重点监控。监控期不等于每天都跌。',sz300006:'事件锚：10/14普通异动；10/16触板未封；10/28收复旧高。严重异动未确认。',sz002724:'事件锚：10/30收盘数值刚越线；11/2大幅低开。数值认定与公告状态分别核验。',sh603278:'事件锚：10/30收盘数值刚越线；11/2开盘失去承接。强收盘不能替代隔夜风险评估。',sz300949:'10/30为接近阈值观察股；11/2上涨并不证明原盘中买点已触发。',sh605488:'原近阈值观察股，补充行情为新浪单源；不以整理天数推断必涨。',sz300839:'原预警后整理案例，补充行情为新浪单源；数值余量和价格结构分开检查。'};
DATA.forEach(c=>{const o=document.createElement('option');o.value=c.code;o.textContent=c.name+' '+c.code.slice(2);$('case-select').appendChild(o);});
function draw(reset=false){const c=DATA.find(x=>x.code===$('case-select').value),rows=c.rows;
if(reset){$('day-slider').max=rows.length-1;$('day-slider').value=rows.length-1;}
const selected=+$('day-slider').value,W=$('candles').clientWidth,H=window.innerWidth<720?280:310,L=43,R=14,T=20,B=35,lo=Math.min(...rows.map(r=>r.low))*.97,hi=Math.max(...rows.map(r=>r.high))*1.03;
const y=v=>T+(hi-v)/(hi-lo)*(H-T-B),dx=(W-L-R)/rows.length,x=i=>L+dx*(i+.5),cw=Math.max(3,Math.min(18,dx*.58));
let s=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${c.name}从10月9日至11月2日未复权日线图"><title>${c.name}未复权日线，价格单位元</title><desc>用下方日期滑块查看每一天开高低收。图不表示盘中先后顺序。</desc>`;
for(let i=0;i<5;i++){let v=lo+(hi-lo)*i/4,yy=y(v);s+=`<line x1="${L}" y1="${yy}" x2="${W-R}" y2="${yy}" stroke="#d6e0ed"/><text x="${L-7}" y="${yy+4}" text-anchor="end" font-size="11" fill="#52657c">${v.toFixed(1)}</text>`;}
s+=`<rect x="${x(selected)-dx/2}" y="${T}" width="${dx}" height="${H-T-B}" fill="#205bd5" opacity=".09"/>`;
rows.forEach((r,i)=>{const col=r.close>=r.open?'#b34445':'#157977';s+=`<g><title>${r.date} 开${r.open} 高${r.high} 低${r.low} 收${r.close}</title><line x1="${x(i)}" y1="${y(r.high)}" x2="${x(i)}" y2="${y(r.low)}" stroke="${col}" stroke-width="1.5"/><rect x="${x(i)-cw/2}" y="${y(Math.max(r.open,r.close))}" width="${cw}" height="${Math.max(1.5,Math.abs(y(r.open)-y(r.close)))}" fill="${col}"/></g>`;});
const ticks= W<500?[0,Math.floor(rows.length/2),rows.length-1]:[0,Math.floor(rows.length/3),Math.floor(2*rows.length/3),rows.length-1];ticks.forEach(i=>s+=`<text x="${x(i)}" y="${H-10}" text-anchor="middle" font-size="11" fill="#52657c">${rows[i].date.slice(5)}</text>`);s+='</svg>';$('candles').innerHTML=s;
const r=rows[selected],clv=r.high>r.low?((r.close-r.low)/(r.high-r.low)*100).toFixed(1)+'%':'不适用';$('chart-readout').textContent=`${c.name} · ${r.date}　开 ${r.open.toFixed(2)} / 高 ${r.high.toFixed(2)} / 低 ${r.low.toFixed(2)} / 收 ${r.close.toFixed(2)} 元　收盘位置 ${clv}`;$('event-label').textContent=eventText[c.code];}
$('case-select').addEventListener('change',()=>draw(true));$('day-slider').addEventListener('input',()=>draw());window.addEventListener('resize',()=>draw());draw(true);
'''


def document(title, content, extra_script=''):
    body = markdown.markdown(content, extensions=['tables', 'fenced_code', 'toc'], output_format='html')
    body = body.replace('<table>', '<div class="table-wrap"><table>').replace('</table>', '</table></div>')
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + escape(title) + '</title><style>' + CSS + '</style></head><body>'
            '<div class="topbar"><strong>监管临界点 · 研究档案</strong><small>2026.11.03 更新 · 历史统计与案例截止分别标注</small></div>'
            '<main class="shell">' + body + '</main>' + extra_script + '</body></html>')


def build(link_existing=False):
    stats = read_json(OUT/'stats.json')
    chart, inputs = case_inputs()
    content = (OUT/'report_body.md').read_text(encoding='utf-8')
    rules_path = OUT/'rules_notes.md'
    if not rules_path.exists() or not (OUT/'cases_notes.md').exists():
        raise FileNotFoundError('Case and rule verification notes must be complete before building.')
    rules = ('2026版沪深交易规则于2026年7月6日生效；新旧版本累计偏离公式均为区间个股收益减区间指数收益。'
             '沪市普通主板对应上证A股指数，深市主板对应深证A股指数，创业板对应创业板综合指数。'
             '普通主板10日同向普通异动次数标准为4次，创业板为3次；100%／200%之外仍有其他认定条件。'
             '[上交所2026交易规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml)；'
             '[深交所2026交易规则原文](https://docs.static.szse.cn/www/lawrules/rule/trade/W020260424690713155663.pdf)。\n\n'
             '**不可把一次100%严重异动当作所有通道一并清零。** 普通异动、10日100%、30日200%和次数条件应分别保存证据与官方区间。'
             '仅有笼统“重新计算”条文时，不擅自推定跨指标同步重置。大千生态、日出东方的先触100%后又触30日200%公告提供了反例，'
             '[大千生态公告](https://paper.cnstock.com/html/2024-11/21/content_1996485.htm)同时记载11/14触100%、11/20又按10/10开始的30日区间触200%；'
             '[日出东方公告](https://static.cninfo.com.cn/finalpage/2024-11-23/1221817337.PDF)也记载先后触发不同指标。'
             '完整条文、公告原文和证据边界见[规则与重置专项核验](rules_notes.html)。\n\n'
             '现有事件研究未完整恢复全部历史公告和重置区间，因此坚持称“数值重建”，不用它替代交易所正式严重异动名单。\n\n')
    links = [('完整历史分组、去重敏感性和缺数','stats_notes.html'),('统计结构化数据','stats.json'),('历史重算校验','stats_validation.json'),
             ('案例分析与来源','cases_notes.html'),('规则、重置与官方原文','rules_notes.html'),('完整文字版','监管临界点战法研究.md'),
             ('原异动研究','../abnormal_lianban_study/abnormal_lianban_study.html'),('三战法旧预案总览','../trading_playbooks/index.html')]
    replacements = {'<!-- STATS -->': stats_markdown(stats), '<!-- CASES -->': case_markdown(chart),
                    '<!-- RULES -->': rules, '<!-- EVIDENCE -->': '\n'.join(f'- [{label}]({url})' for label,url in links)}
    for key, value in replacements.items():
        content = content.replace(key, value)
    plain = content.replace('<!-- CALCULATOR -->','').replace('<!-- CASE_CHART -->','日线交互图见同目录HTML。').replace('<!-- DECISION -->','')
    (OUT/'监管临界点战法研究.md').write_text(plain,encoding='utf-8')
    html_content = content.replace('<!-- CALCULATOR -->', CALCULATOR).replace('<!-- CASE_CHART -->', CHART).replace('<!-- DECISION -->', DECISION)
    html_content = html_content.replace('# 监管临界点：从走势分化到条件战法\n', '')
    hero = ('<header class="hero"><div><div class="eyebrow">价格结构 × 监管状态 × 后续承接</div><h1>过了临界点，<br>为什么有的继续涨？</h1>'
            '<p>把接近、越线和公告后的走势分开研究。<br>七只案例、失败样本与三套条件动作。</p></div><div class="rail" aria-label="四种不同观察状态">'
            '<div><b>接近阈值</b><span>先核余量，等待结构</span></div><div><b>盘中测算越线</b><span>预警尚不等于收盘认定</span></div>'
            '<div><b>收盘数值越线</b><span>核公告、起算区间与重置</span></div><div><b>公告后的下一日</b><span>检验承接及实际可成交性</span></div></div></header>')
    content_html = markdown.markdown(html_content, extensions=['tables','fenced_code','toc'], output_format='html')
    content_html = content_html.replace('<table>','<div class="table-wrap"><table>').replace('</table>','</table></div>')
    headings = re.findall(r'<h2 id="([^"]+)">(.*?)</h2>', content_html)
    # Markdown's default slugger drops Chinese characters; use stable readable section ids.
    selected = []
    for i,(old,title) in enumerate(headings):
        target = f'section-{i+1}'
        content_html=content_html.replace(f'<h2 id="{old}">',f'<h2 id="{target}">',1)
        selected.append((target,title))
    labels={'先把':'监管口径','事件样本':'历史统计','真实案例':'个股案例','战法一':'条件战法','每日如何':'每日流程','规则与':'规则证据'}
    chosen = [(i,next(label for term,label in labels.items() if term in title)) for i,title in selected if any(term in title for term in labels)]
    nav = '<nav class="jump" aria-label="报告章节">'+''.join(f'<a href="#{i}">{escape(title)}</a>' for i,title in chosen)+'</nav>'
    payload = json.dumps(chart,ensure_ascii=False,separators=(',',':')).replace('</',r'<\/')
    html = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>监管临界点战法研究 · 2026-11-03</title><style>'+CSS+'</style></head><body>'
            '<div class="topbar"><strong>监管临界点 · 深度研究</strong><small>2026.11.03 更新 · <a href="../trading_playbooks/index.html">返回三战法</a></small></div>'
            '<main class="shell">'+hero+nav+'<article>'+content_html+'</article><footer class="footer">历史价格路径不等于策略收益；本页为有明确截止日期的研究快照。</footer></main>'
            '<script>const DATA='+payload+';'+JS+'</script></body></html>')
    (OUT/'index.html').write_text(html,encoding='utf-8')
    for name in ['stats_notes','cases_notes','rules_notes']:
        md = f'[返回完整研究](index.html)\n\n'+(OUT/f'{name}.md').read_text(encoding='utf-8')
        (OUT/f'{name}.html').write_text(document('监管临界点 · '+name,md),encoding='utf-8')
    inputs += [OUT/'stats.json', OUT/'stats_validation.json', OUT/'cases_notes.md', rules_path, OUT/'report_body.md']
    audit = {'research_date':'2026-11-03','history_cutoff':'2026-10-30','case_cutoff':'2026-11-02',
             'case_count':len(chart),'live_signal_verified':False,'execution_backtest':False,
             'inputs':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}}
    (OUT/'report_manifest.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    if link_existing:
        begin,end='<!-- REGULATION-DEEP-RESEARCH-BEGIN -->','<!-- REGULATION-DEEP-RESEARCH-END -->'
        for path in [ROOT/'output/trading_playbooks/index.html',ROOT/'output/trading_playbooks/abnormal_playbook.html',
                     ROOT/'output/abnormal_lianban_study/abnormal_lianban_study.html']:
            original=path.read_text(encoding='utf-8')
            backup=OUT/'entry_backups'/path.parent.name/path.name
            if not backup.exists():
                backup.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(path,backup)
            block=(begin+'<aside style="margin:18px auto;padding:18px 22px;max-width:1160px;background:#eaf0fa;color:#172d49;border-left:4px solid #205bd5;font:15px/1.7 Microsoft YaHei,system-ui">'
                   '<strong>新增：监管临界点战法深度研究 · 2026年11月3日</strong><p>接近与越线为何分化、七只案例、失败样本和条件战法。历史统计截至10/30，案例截至11/2；不替换本页原预案日期。</p>'
                   '<a style="color:#205bd5" href="../regulation_research_20261103/index.html">打开详细分析与战法 →</a></aside>'+end)
            if begin in original:
                updated=re.sub(re.escape(begin)+'.*?'+re.escape(end),lambda _:block,original,flags=re.S)
            else:
                updated=re.sub(r'(<body(?:\s[^>]*)?>)',lambda m:m.group(1)+block,original,count=1,flags=re.I)
            path.write_text(updated,encoding='utf-8')
    return audit


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--link-existing',action='store_true')
    args=parser.parse_args()
    print(json.dumps({k:v for k,v in build(args.link_existing).items() if k!='inputs'},ensure_ascii=False))
