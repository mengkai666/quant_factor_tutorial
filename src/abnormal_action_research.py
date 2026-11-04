"""Action-first abnormal-move research; plans are not executed trading signals."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, date
import json
from pathlib import Path

import pandas as pd

from abnormal_target_cases import _fetch_rows, SHANGHAI

LEGACY_CASE_AS_OF = '2026-10-30'
LEGACY_CASE_PLAN_DATE = '2026-11-02'

NOTICES = {
    'sh603278': {
        'title': '大业股份10月31日严重异常波动公告',
        'url': 'https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12684212&stockid=603278',
        'date': '2026-10-31', 'known_at': '2026-10-31',
        'status': '公司公告确认严重异常波动（11月2日预案可知，不能回填10月30日盘中）',
        'risk': '机器人腱绳仍在研发送样验证阶段、未量产且未产生实质收益；公司提示业绩亏损和炒作风险。'},
    'sz002724': {
        'title': '海洋王严重异常波动公告2026-054',
        'url': 'https://static.cninfo.com.cn/finalpage/2026-11-02/1225650401.PDF',
        'date': '2026-11-02', 'known_at': '2026-11-01',
        'status': '公司公告确认10月19—30日偏离100.02%；巨潮列表实际披露11月1日16:00',
        'risk': '公告提示估值明显高于行业、市场情绪过热；PDF目录日期11月2日不作为提前可知依据。'},
    'sz300949': {
        'title': '奥雅股份10月27日普通异常波动公告',
        'url': 'https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12667802&stockid=300949',
        'date': '2026-10-27', 'known_at': '2026-10-27',
        'status': '10月26—27日普通异动公告已核验；严重异动未据此确认',
        'risk': '前三季度尚未盈利，营收同比下降15.65%；债务重组效果取决于履约及现金到账。'},
}


def _save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def flat_scenarios(prices, indices, calendar, code, as_of):
    """All 1..N windows, frozen future stock/index, no claim of regulatory reset."""
    index_code = 'sz399102' if code.startswith(('sz300', 'sz301')) else ('sh000002' if code.startswith('sh') else 'sz399107')
    days = [str(x)[:10] for x in calendar if str(x)[:10] <= as_of][-31:]
    stock = prices[prices.code.eq(code)].set_index('date').close_qfq.reindex(days)
    ix = indices[indices.index_code.eq(index_code)].set_index('date').close.reindex(days)
    if len(days) < 31 or stock.isna().any() or ix.isna().any():
        return []
    ps, xs = list(stock.astype(float)), list(ix.astype(float))
    result = []
    for k in range(6):
        if k:
            ps.append(ps[-1]); xs.append(xs[-1])
        devs = {}
        for n in (10, 30):
            devs[f'dev{n}'] = max(((ps[-1] / ps[-1-lag] - 1) - (xs[-1] / xs[-1-lag] - 1)) * 100 for lag in range(1, n+1))
        if k in (0, 1, 2, 3, 5):
            result.append({'flat_days': k, **{key: round(val, 2) for key, val in devs.items()}})
    return result


def candidate_from_rows(item, rows, plan_date, notice=None):
    last, previous = rows[-1], rows[-2]
    c = last['close']; ma = sum(x['close'] for x in rows[-5:]) / 5
    change = (c / previous['close'] - 1) * 100
    position = (c - last['low']) / max(last['high'] - last['low'], .01)
    # Prioritize observable strength only. All intraday/risk gates remain pending.
    qualifies = bool(item.get('box_eligible')) and c >= ma and change >= 3 and position >= .75
    bad_analog = item.get('analog_n', 0) >= 5 and (item.get('median10') or 0) < 0
    qualifies = qualifies and not bad_analog
    tier = '优先' if qualifies else ('回避' if c < ma else '观察')
    trigger = last['high']
    upper = round(trigger * 1.005, 2)
    structural = last['low']
    last20_high = max(r['high'] for r in rows[-20:])
    status = notice['status'] if notice else item.get('regulation_status', '公告状态未核验')
    why = f"{item.get('sequence', '')}；收盘{c:.2f}元，日涨跌{change:+.2f}%，MA5 {ma:.2f}元；日内区间收盘位置{position*100:.0f}%。{status}。"
    evidence = [f"{last['date']}价格筛查口径的1—10日最大偏离{item.get('dev10', 0):.2f}%，1—30日最大偏离{item.get('dev30', 0):.2f}%；不等于完整监管认定。"]
    if notice:
        evidence.append(notice['risk'])
    if item.get('analog_n', 0) >= 5:
        evidence.append(f"同类历史{item['analog_n']}只，后10日收益中位{item['median10']:+.2f}%，25%—75%分位{item['p25_10']:+.2f}%至{item['p75_10']:+.2f}%；非个股目标价或成交胜率。")
    else:
        evidence.append(f"合格同类历史仅{item.get('analog_n', 0)}只，不给胜率、预计上涨幅度或‘横几日必涨’结论。")
    card = {
        'code': item['code'], 'name': item['name'], 'tier': tier, 'setup': '异动后承接再突破' if qualifies else '修复/突破等待',
        'reference_price': c, 'price_as_of': last['date'], 'why': why,
        'entry_trigger': f"{plan_date} 10:00后，先突破{trigger:.2f}元，再回踩不破并重新上拐；站在当日成交均价上方，同题材至少2只活跃同伴同步走强（名单/分钟行情需盘中核验）。实际回踩低点至入场价风险≤2%，各项齐备才考虑小仓试错。",
        'entry_zone': f'{trigger:.2f}—{upper:.2f}元，仅限上述突破回踩完成后；不是预挂买单区',
        'entry_lower': trigger, 'entry_upper': upper,
        'stop_price': f'实际回踩低点（尚未形成），距买价超过2%则取消；日线结构底{structural:.2f}元仅供原持仓评估，不能用它放大试错风险',
        'target_reference': f'近20日日高{last20_high:.2f}元仅为压力参考；无可靠固定目标。获利2R且已到可卖日可分批兑现，R=买价减实际回踩低点。',
        'upside_pct': None, 'risk_pct': None, 'rr': None,
        'not_buy': f'高开超过3%、价格直接越过{upper:.2f}元、涨停买不到、突破回落至{trigger:.2f}元下方、同题材无共振、开盘新增不利公告或风险距离>2%，任一出现即不买；弱市不为凑名单下单。',
        'holding': 'A股T+1：当天新买不能当天止损；次日起可卖时，失守实际回踩低点优先退出，跌停可能卖不出。两个可交易日仍无延续且跌回突破位，按失败处理；不摊平补仓。',
        'scenario_up': f'突破{trigger:.2f}并回踩承接，才由观察转为条件试错；连续保持突破位后才讨论二波。',
        'scenario_flat': f'停留在{structural:.2f}—{trigger:.2f}元，继续等，不因多横一天就买。',
        'scenario_down': f'跌破{structural:.2f}元或已形成回踩低点后不能收复，取消预案；原持仓按可卖规则降风险。',
        'actual_status': '盘中触发未核验：尚无完整分钟与门槛证据',
        'trigger_verification': {'status': 'not_verified', 'confirmed': None},
        'executable': False,
        'missing': ['分钟成交量、回踩低点、同题材共振、成交可达性尚未核验；2%/0.5%/10:00为研究参数，未做成交约束回测'],
        'evidence': evidence, 'sources': [notice] if notice else [],
        'score': round(change + position*10, 3), 'regulation_status': status,
    }
    legacy_case_applies = last['date'] == LEGACY_CASE_AS_OF and plan_date == LEGACY_CASE_PLAN_DATE
    if item['code'] == 'sz301313' and legacy_case_applies:
        card.update(tier='回避', setup='旧支撑已破，不能把下跌称为横盘',
                    entry_trigger='不执行当前买入预案。先收复10月23日确认收盘61.44及10月28日低点62.01，再重建稳定区间；未收复不猜监管结束反弹。',
                    entry_zone='无：结构修复前不买',
                    not_buy='本预案不新开仓；收复旧锚只进入结构复核，须另建稳定区间和新的预案，不能复用通用突破买区。',
                    holding='仅供原持仓风险复核：遵守T+1可卖限制，按原持仓既定失效规则评估；不因形成新低下移原止损，不加仓摊平。',
                    scenario_up='收复61.44及62.01只代表开始修复；新的稳定区间确认后另建预案，本卡仍不新开仓。',
                    scenario_flat='旧锚未收复或稳定区间未重建，继续回避，不以横盘天数替代结构确认。',
                    scenario_down='59.60元已低于61.44及62.01两个旧锚；58.86只是新低，不是把原止损不断下移的理由。')
    if item['code'] == 'sz300006' and legacy_case_applies:
        card.update(setup='收复旧高后的二次回踩，未证明二波',
                    entry_trigger='先守住9.75—9.76元旧高，再观察收复10.48元（10/29收盘）；仅收复10.48不够，需后续回踩不破且重新上拐、站上当日均价，同题材至少2只活跃股走强，实际回踩风险≤2%。',
                    entry_zone='10.48—10.53元，仅在全部确认后；高开或直接拉过不追',
                    entry_lower=10.48, entry_upper=10.53,
                    not_buy='买入前含开盘触及/跌破9.75元即取消当日预案，随后收回也不重启；弱于当日均价、没有同题材承接、回踩风险>2%，或直接拉过10.53元，均不买；10月30日收盘在当日低位，不能沿用10月28日涨停的强势结论。',
                    stop_price='实际确认回踩低点且风险≤2%；9.75是旧平台失效线，不应让新买仓位承担约7%以上止损距离',
                    scenario_up='先收复10.48，再看11.00—11.22元前两日日高压力；不过压力位不能叫确定二波。',
                    scenario_flat='9.75—10.48之间继续观察，不在弱势回落途中预判支撑一定有效。',
                    scenario_down='买入前含开盘触及/跌破9.75元即取消且当日不重启；收盘是否回到9.75上方仅用于后续结构重评，不能恢复已取消的买入预案；已持有者按可卖规则评估。',
                    cancellation={'rule_version': '2026-11-03', 'intraday_at_or_below': 9.75,
                                  'same_day_restart': False})
    if item['code'] in ('sz301313', 'sz300006') and not legacy_case_applies:
        card.update(tier='观察', setup='固定案例价位已过适用期，等待重新评估',
                    entry_trigger='本卡不生成新买入条件；须按本次完整行情重建旧锚、稳定区间及失效规则后另出预案。',
                    not_buy='历史案例买区已停止沿用；本次结构与风险距离尚未完成重新核验，不新开仓。',
                    holding='原持仓仍按既定失效规则和T+1可卖限制评估；不以下跌形成的新低自动下移原止损。',
                    scenario_up='上涨或收复旧价位只作为重新评估线索；新的稳定区间与买入条件尚未给出。',
                    scenario_flat='仅观察本次行情，不沿用历史案例的横盘区间或固定触发价。',
                    scenario_down='继续下跌时不得把新低自动改为新支撑；原持仓按既定规则评估。')
        card['missing'].append('旧案例只适用于2026-10-30观察/2026-11-02预案；本次需重新核验结构锚与入场条件')
    if card['tier'] == '回避' or (item['code'] in ('sz301313', 'sz300006') and not legacy_case_applies):
        # Inactive cards must not leave executable-looking machine fields behind.
        card.update(entry_zone='无：本卡不提供新开仓价位', entry_lower=None, entry_upper=None,
                    stop_price=None, target_reference=None, upside_pct=None, risk_pct=None, rr=None,
                    actual_status='不适用：本卡不生成新开仓预案',
                    trigger_verification={'status': 'not_applicable', 'confirmed': None})
        if card['tier'] == '回避' and item['code'] != 'sz301313':
            card.update(entry_trigger='当前结构不合格，不执行新开仓预案；重新核验结构后另出条件。',
                        not_buy='本预案回避新开仓，通用突破和追价条件均不适用。',
                        holding='原持仓按既定失效规则和T+1可卖限制评估；本卡不为新开仓设置止损，也不下移原持仓止损。',
                        scenario_up='反弹后重新核验结构，原回避卡不自动转换为买入预案。',
                        scenario_flat='结构尚未恢复，维持回避。',
                        scenario_down='结构进一步走弱时维持回避，原持仓按既定规则评估。')
    return card


def build_abnormal_research(root: Path, out: Path, as_of: str, plan_date: str, refresh: bool=True) -> dict:
    root, out = Path(root), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    payload = json.loads((root/'output/abnormal_lianban_study/study_payload.json').read_text(encoding='utf-8'))
    if payload['meta']['price_as_of'] != as_of or plan_date <= as_of:
        raise ValueError('异动证据日期与预案不匹配，拒绝沿用旧名单')
    snapshot = out/'raw_ohlc.json'
    raw = json.loads(snapshot.read_text(encoding='utf-8')) if snapshot.exists() else {}
    errors = []
    if refresh:
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {r['code']: pool.submit(_fetch_rows, r['code'], as_of) for r in payload['current']}
            for code, future in futures.items():
                try:
                    raw[code] = future.result()
                except Exception as exc:
                    errors.append(f'{code}未刷新：{type(exc).__name__}')
        _save(snapshot, raw)
    cards, excluded = [], []
    known_cutoff = min(datetime.now(SHANGHAI).date().isoformat(), (date.fromisoformat(plan_date)-timedelta(days=1)).isoformat())
    for item in payload['current']:
        rows = [r for r in raw.get(item['code'], {}).get('rows', []) if r['date'] <= as_of]
        if len(rows) < 20 or rows[-1]['date'] != as_of or abs(rows[-1]['close']-item.get('raw_close', -999)) > .011:
            excluded.append({'code': item['code'], 'name': item['name'], 'reason': '原始OHLC不足/过期/收盘交叉不一致，禁止生成价位'})
            continue
        notice = NOTICES.get(item['code'])
        if notice and notice['known_at'] > known_cutoff:
            notice = None
        card = candidate_from_rows(item, rows, plan_date, notice)
        card['sources'].append({'title': '新浪未复权日线', 'url': raw[item['code']]['source_url'], 'date': as_of})
        cards.append(card)
    cards.sort(key=lambda x: ({'优先': 0, '观察': 1, '回避': 2}[x['tier']], -x['score']))
    # Max three priorities, retain named user cases and representative near-threshold failure.
    priority = [r for r in cards if r['tier'] == '优先'][:3]
    focus = priority + [r for r in cards if r not in priority and r['code'] in ('sz300006', 'sz301313', 'sz300949')]
    if not focus:
        focus = cards[:3]
    for r in cards:
        if r not in focus:
            excluded.append({'code': r['code'], 'name': r['name'], 'reason': r['why']+' '+('同类历史后验偏弱，不选作当前重点。' if r['code']=='sh605488' else '当前强度排序未进入重点；不是永远不买。')})
    for i, card in enumerate(focus, 1):
        card['rank'] = i
    prices = pd.read_csv(root/'data/price_history_cache.csv', usecols=['code','date','close_qfq'])
    ix = pd.read_csv(root/'output/abnormal_lianban_study/formal_index_series.csv')
    ix['date'] = ix.date.astype(str).str[:10]
    # The generated index series uses index_close, while public snapshots use close.
    if 'index_close' in ix.columns:
        ix = ix.rename(columns={'index_close': 'close'})
    calendar = pd.read_csv(root/'data/trading_calendar_cache.csv').trade_date.astype(str).tolist()
    trials = []
    for card in focus:
        scenario = flat_scenarios(prices, ix, calendar, card['code'], as_of)
        if scenario:
            txt = '；'.join(f"横{r['flat_days']}日：10日{r['dev10']:.1f}%/30日{r['dev30']:.1f}%" for r in scenario)
            card['evidence'].append('假设后续股价和分类指数收盘均不变、无复权变化、忽略监管重置，仅演示滚动窗口：'+txt+'。数值下降不等于可以买或解除监控。')
            trials.append({'code': card['code'], 'name': card['name'], 'scenarios': scenario})
    history = []
    for row in payload['sideways_summary']:
        history.append({'label': f"{row['board']}整理{row['days']}日", 'n': row['n'], 'n10': row['n10'],
                        'median10': round(row['median10'], 2) if row['median10'] is not None else None,
                        'win_count': row['win10_success'], 'notes': f"上涨分母{row['win10_n']}；事件可能重叠，不能合并为独立样本或交易胜率"})
    mature = [e for e in payload['events'] if e.get('return10') is not None]
    examples = []
    for positive in (True, False):
        group = sorted([e for e in mature if (e['return10'] > 0) == positive], key=lambda x:x['event_date'], reverse=True)[:3]
        for e in group:
            examples.append({'name': e['name'], 'code': e['code'], 'date': str(e['event_date'])[:10],
                             'setup': e['sequence'], 'notes': f"后10日{e['return10']:+.2f}%；按最近成熟事件展示{'上涨' if positive else '未上涨'}例，不用这些赢家/输家反推当前概率"})
    for c in payload['cases']:
        examples.append({'name': c['name'], 'date': as_of, 'setup': c['conclusion'],
                         'notes': '公告后收益按固定3/5/10日计算，未满期留空；完整日线见原研究报告'})
    sources = payload['sources'] + [v for v in NOTICES.values() if v['known_at'] <= known_cutoff]
    sources.append({'title': '海洋王巨潮实际披露时间：11月1日16:00', 'url': 'https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?code=002724&endDate=2026-11-02&name=%E6%B5%B7%E6%B4%8B%E7%8E%8B&orgId=9900023121&startDate=2026-11-02&url=disclosure%2Flist%2Fsearch', 'date':'2026-11-01'})
    report = {
        'kind': 'abnormal', 'title': '异动与二波：从连板复盘到条件买点', 'as_of': as_of, 'plan_date': plan_date,
        'generated_at': datetime.now(SHANGHAI).isoformat(timespec='seconds'),
        'scope': f"覆盖{payload['meta']['stock_count']}只价格可重建股票、{payload['meta']['event_count']}个预警事件、{payload['meta']['complete_10_count']}个完整后10日事件；当前{len(payload['current'])}只核查来自本次预警/案例池，不是全市场购买名单。",
        'market_status': '收盘后预案；高风险条件研究，不直接追板',
        'decision_summary': '先看'+('、'.join(c['name'] for c in priority) if priority else '无合格优先标的')+'的突破回踩承接；其余按本次观察/回避卡处理。优先只是盯盘顺序，盘中触发尚未核验。',
        'decision_counts': {'priority': len(priority), 'watch': sum(r['tier']=='观察' for r in focus), 'avoid': sum(r['tier']=='回避' for r in focus), 'triggered': None},
        'trigger_verification': {'status': 'not_verified', 'triggered_count': None,
                                 'reason': '尚无计划日完整分钟、市场、同题材与公告门槛证据'},
        'summary': [f'使用{as_of}完整收盘和截至{known_cutoff}已公开且核验的公告，为{plan_date}准备条件单；未接入盘中买点确认。',
                    '核心战法：价格结构保持 → 真实突破 → 回踩承接 → 题材同涨 → 风险距离合格；五项缺一不买。',
                    '历史统计没有支持横盘满2/3/5日必涨；只把横盘日数当作观察阶段，不当买点。',
                    '严重异动不是涨停禁令，也不是强势认证；已知公告按各自披露时点纳入核验，不能用主动进异动推断主力意图。'],
        'market_summary': ['全市场环境和同题材强度未通过前，优先名单只作为盯盘顺序。', '计划日09:45后上涨家数占上涨/下跌合计至少35%，且较09:35不下降；跌停家数低于前日并停止扩张，才继续检查个股。35%是保守研究门槛，不是已验证最优参数。', '需要先指定至少2只同题材活跃同伴；当前未接通题材分时核验，不允许系统自动认定共振已通过。'],
        'candidates': focus, 'excluded': excluded, 'history': history + examples,
        'history_stats': history, 'flat_scenarios': trials,
        'rules': [
            {'title': '执行顺序', 'entry': '09:25核对公告/停牌 → 09:30—10:00只观察 → 10:00后核对突破回踩和同题材 → 不满足则空仓', 'exit': '先明确实际回踩止损与T+1隔夜风险，再决定是否试错'},
            {'title': '资金规则（示例参数，非个性化仓位）', 'entry': '单笔初始仓位不超过总资产5%，不融资；同类合计不超过10%；名义风险预算0.25% ÷ 止损距离并受5%上限约束', 'exit': 'T+1/跌停/跳空可导致实际损失超过预算；本规则未验证最优'},
            {'title': '10/30口径', 'entry': '按累计个股收益减分类指数收益，10个交易日+100%、30个交易日+200%；另有其他严重异动条件，不能只盯这两项', 'exit': '断板一天不清零；7个10%涨停累计94.87%，是否触发还要看此前交易日及分类指数'},
            {'title': '已异动后如何做', 'entry': '不因进入异动立即卖、不因敢于进入异动立即买；看公告后支撑、前高和量价路径', 'exit': '旧支撑破位就下调评级；严禁一边创新低一边下移失效线'},
            {'title': '还要横盘几天？只做条件模拟', 'entry': '下表述假设后续个股和分类指数收盘都不变、无复权变化，仅展示滚动10/30日偏离值如何变化：' + '；'.join(t['name']+'：'+ '、'.join(f"横{s['flat_days']}日→{s['dev10']:.1f}%/{s['dev30']:.1f}%" for s in t['scenarios'] if s['flat_days'] in (1,3,5)) for t in trials), 'exit': '这不是预测行情，也不是正式监管重置/监控解除倒计时；滚动数值降低只改变算术条件，不能推出未来上涨空间。'},
        ],
        'limitations': payload['meta']['limitations'] + ['当前选股、0.5%追价上限、2%风险距离等为未验证研究参数；历史路径统计不是本条件策略回测。', '新增公告信息时点与行情截止日分开，周末消息不得回填历史信号。', '同题材共振、实际回踩及分钟成交尚缺，触发状态与数量均未核验，不能记为0。', '2026-11-03修订统一回避卡字段与取消优先级；固定个股案例只适用于2026-10-30观察/2026-11-02预案，修订不能回填原冻结预案。'] + errors,
        'sources': sources, 'data_quality': {'price_as_of': as_of, 'announcement_knowledge_date': known_cutoff, 'raw_price_basis': '未复权；股数换算为手', 'errors': errors},
        'links': {'完整异动历史与逐日日线': '../abnormal_lianban_study/abnormal_lianban_study.html'},
    }
    _save(out/'research.json', report)
    return report
