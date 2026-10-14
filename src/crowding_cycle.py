"""抱团周期研究：行情代理不等于真实资金抱团；截图是事后研究注释。

所有计算先截断至 report_date；全市场与补充个股使用独立数据时钟。
不发网络请求、不回填价格缺口、不根据缺价推断跌停/停牌/出清。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from paths import DATA_DIR
from stock_code import infer_exchange

REFERENCE_DATE = '2026-10-08'
NAMES = {
    'sh600721': '百花医药', 'sh600664': '哈药股份', 'sh600613': '神奇制药',
    'sz002412': '汉森制药', 'sz000017': '深中华A', 'sh600371': '万向德农',
    'sh600108': '亚盛集团', 'sh600127': '金健米业', 'sh601086': '国芳集团',
    'sh600865': '百大集团', 'sz000978': '桂林旅游', 'sz003040': '楚天龙',
    'sz000993': '闽东电力', 'sh601091': '沈鼓集团', 'sh600241': '时代万恒',
    'sz301513': '尚水智能', 'sz301190': '善水科技', 'sh688137': '近岸蛋白',
}
# 以下归因来自用户截图，只在其提供日及之后展示，不参与滚动选股。
GROUPS = [
    dict(id='medicine', title='医药旧团 / 8月前史', start='2026-08-08', end='2026-08-28',
         codes=['sh600721', 'sh600664', 'sh600613', 'sz002412'], origin='复盘补充样本，不宣称线性接替'),
    dict(id='farm', title='农业 / 同一周期内部轮动', start='2026-08-31', end='2026-09-15',
         codes=['sh600371', 'sh600108', 'sh600127'], origin='截图1：万向德农 → 亚盛集团、金健米业'),
    dict(id='consumer', title='消费 / 核心切换', start='2026-08-31', end='2026-09-15',
         codes=['sh601086', 'sh600865', 'sz000978'], origin='截图1：国芳集团 → 百大集团 → 桂林旅游'),
    dict(id='parallel', title='并行方向 / 不能拼成单一龙头链', start='2026-08-31', end='2026-09-24',
         codes=['sz000017', 'sz003040', 'sz000993', 'sh601091'], origin='截图1：深中华断板为情绪参照；楚天龙；9/10闽东电力；9/17沈鼓集团'),
    dict(id='trial', title='9/28后 / 新周期试错池', start='2026-09-28', end='2026-10-08',
         codes=['sh600241', 'sz301513', 'sz301190', 'sh688137'], origin='截图2、3为同一观点；4只候选不等于4只已确认核心'),
]
PERIODS = [
    ('2026-08-08', '2026-08-21', '8月前史', '先辨识医药核心及高度样本；不能只看最后赢家。', ['medicine']),
    ('2026-08-24', '2026-08-28', '旧团分歧与过渡', '医药内部轮动、深中华及楚天龙并行；新高标不自动等于新周期。', ['medicine']),
    ('2026-08-31', '2026-09-03', '农业 / 消费聚焦', '资金可围绕少数辨识度标的轮动，板块标签与抱团行为分开判断。', ['farm', 'consumer', 'parallel']),
    ('2026-09-04', '2026-09-09', '第一核心断板后的换位', '国芳断板是个股节点；百大接力需观察承接和后续，不能立刻判整个周期结束。', ['consumer', 'farm']),
    ('2026-09-10', '2026-09-15', '二次断板 / 瓦解压力', '百大断板、金健及国芳回落，观察亏钱效应能否跨核心扩散。', ['consumer', 'farm', 'parallel']),
    ('2026-09-16', '2026-09-24', '修复与风格竞争', '大阳线可带来旧核心反抽；电力、次新与趋势候选并行，反抽不是新周期确认。', ['farm', 'consumer', 'parallel']),
    ('2026-09-28', '2026-10-08', '再出清后的试错', '对比旧团失效、新候选持续性和全市场修复；允许无交易，不预设胜者。', ['trial', 'farm', 'consumer']),
]
EVENTS = [
    ('2026-09-04', '国芳断板', ['sh601086']),
    ('2026-09-10', '百大断板；电力方向观察', ['sh600865', 'sz000993']),
    ('2026-09-15', '截图称金健、国芳出清', ['sh600127', 'sh601086']),
    ('2026-09-16', '截图称大阳线 / 风格转向趋势', []),
    ('2026-09-17', '次新方向：沈鼓集团', ['sh601091']),
    ('2026-09-28', '截图称旧抱团出清、新周期开始试错', ['sh600241', 'sz301513', 'sz301190', 'sh688137']),
]
SOURCES = [
    dict(id='images', title='用户提供的三张截图', kind='研究观点', date=REFERENCE_DATE,
         note='图2与图3内容重复；图1/2年份按图3的2026年解释。红叉科技栏不列为已确认主线。'),
    dict(id='calendar', title='上交所2026年部分节假日休市安排', kind='交易所公告', date='2025-12-22',
         url='https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml',
         note='10月1日至7日休市；10月8日开市。'),
    dict(id='shengu', title='沈鼓集团股票上市交易公告', kind='交易所公告', date='2026-09-16',
         url='https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20260916_10832340.shtml',
         note='证券代码601091，2026-09-17上市；新股无涨跌幅限制阶段不能套常规连板规则。'),
    dict(id='retail', title='零售板块早盘下挫（盘中快讯，不作收盘结论）', kind='财联社', date='2026-09-04',
         url='https://www.cls.cn/detail/2473881', note='盘中跌停不等于收盘跌停，收盘证据另从缓存核对。'),
    dict(id='trading-rules', title='上交所交易规则（2026年修订）', kind='交易所规则', date='2026-04-24',
         url='https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml',
         note='交收前卖出限制及交易制度应单独遵守；纸面止损不保证可成交。'),
    dict(id='quotes', title='腾讯证券个股日K补充', kind='单源价格', date=REFERENCE_DATE,
         url='https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
         note='只补截图样本；不拼接到全市场截面；前复权数据用于形态研究，不是可成交价或严格时点复权回测。'),
]
TACTICS = [
    dict(id='same-cycle', title='同周期换位：只接被市场选择的核心', stage='形成 / 强化 / 分歧',
         observe='原核心断板但未连续大幅破位；新候选与原核心的承接、相对强度和同方向跟随共同对比。',
         trigger='连续2个有完整行情的收盘保持相对强势，候选不跌破事先记录的承接区；次一可交易时点才考虑验证。',
         reject='旧团多核心同步破位、候选只靠一字封板或后排脉冲；没有成交数据时不生成入场结论。',
         exit='候选失守预先记录的承接区或强度回落，停止升级；不能把原核心反抽当作换位成功。',
         example='国芳 → 百大是截图内换位假说；百大断板后要重新评估，不延伸出必然赢家。'),
    dict(id='reset-trial', title='瓦解后试错：先切断旧周期假设', stage='瓦解 → 出清观察 → 试错',
         observe='固定旧核心篮子，不随下跌把输家剔除。至少观察2个完整交易日：不再普遍创新低、抛压没有继续扩散。',
         trigger='新候选与旧团脱钩、市场涨跌家数改善并具备量能和可成交性证据；只建立受限研究试错，不将单日转强当确认。',
         reject='全市场日期落后、旧团仍连续创新低、候选早已高位加速或只有一个标的脉冲；此时等待优于强行选龙头。',
         exit='失守试错日预先定义的结构失效位、市场重新恶化，或预定观察期内无持续性，结束该试错假说。',
         example='时代万恒、尚水智能、善水科技是候选；近岸蛋白须另标为既有趋势延续候选。'),
    dict(id='repair', title='旧团反抽：按修复处理，不冒充新生', stage='出清观察 / 反抽',
         observe='比较反抽幅度、前高距离及跟随广度；区分修复亏损和真正重获定价权。',
         trigger='只在完整证据下研究反抽；须重新验证承接与持续性，不能因跌得多就判定便宜。',
         reject='反抽不能收复破位区、仅单股回光返照、旧篮子仍分化走弱。',
         exit='反抽低点或预定义修复结构失效；旧团修复失败时不以补仓代替证伪。',
         example='9/15后国芳、金健仍有反弹，说明“出清”是过程，不能画成永久死亡线。'),
    dict(id='trend', title='趋势接力：先确认风格，再谈切换', stage='风格竞争 / 趋势强化',
         observe='高位连板退潮不代表趋势自动接班；观察趋势候选相对收益、回撤及多日承接。',
         trigger='趋势结构持续、回撤受控、市场参与广度配合；放量/缩量须有成交额真数据，价格模块不代判。',
         reject='仅大阳线、次新异常波动、复权口径混用或缺少历史样本；不比较不同涨跌幅制度的板数强弱。',
         exit='事前支撑结构失效或相对强势消失。A股T+1、跳空、跌停可能导致无法及时退出。',
         example='沈鼓按次新独立研究；近岸属于更早已启动的趋势，而非9/28低位首发。'),
]


def day(value: Any) -> str:
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        text = f'{text[:4]}-{text[4:6]}-{text[6:]}'
    return pd.Timestamp(text).strftime('%Y-%m-%d')


def code(value: Any) -> str:
    text = str(value).strip().lower()
    if len(text) == 8 and text[:2] in ('sh', 'sz', 'bj') and text[2:].isdigit():
        return text
    if text.isdigit():
        digits = text.zfill(6)
        exchange = infer_exchange(digits)
        return f'{exchange}{digits}' if exchange else ''
    return ''


def number(value: Any, digits: int = 2) -> float | None:
    try:
        value = float(value)
        return round(value, digits) if np.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str) if path.exists() else pd.DataFrame()


def normalize_prices(frame: pd.DataFrame, cutoff: str) -> pd.DataFrame:
    if frame.empty or not {'date', 'code', 'close_qfq'} <= set(frame):
        return pd.DataFrame(columns=['date', 'code', 'close'])
    d = frame[['date', 'code', 'close_qfq']].copy()
    d['date'] = pd.to_datetime(d['date'], errors='coerce').dt.strftime('%Y-%m-%d')
    d['code'] = d['code'].map(code)
    d['close'] = pd.to_numeric(d['close_qfq'], errors='coerce')
    d = d[(d.date <= cutoff) & d.code.ne('') & d.close.gt(0)]
    # 不把raw/legacy悄悄拼到qfq序列，不补缺值。
    return d.drop_duplicates(['date', 'code'], keep='last')[['date', 'code', 'close']]


def normalize_limits(frame: pd.DataFrame, cutoff: str) -> pd.DataFrame:
    d = frame.rename(columns={'日期': 'date', '代码': 'code', '名称': 'name', '类型': 'type', '连板数': 'height'}).copy()
    if d.empty or not {'date', 'code', 'type'} <= set(d):
        return pd.DataFrame(columns=['date', 'code', 'name', 'type', 'height'])
    d['date'] = pd.to_datetime(d.date.astype(str).str.replace('-', ''), format='%Y%m%d', errors='coerce').dt.strftime('%Y-%m-%d')
    d['code'] = d.code.map(code)
    d['height'] = pd.to_numeric(d.get('height', pd.Series(index=d.index, dtype=float)), errors='coerce')
    if 'name' not in d:
        d['name'] = d.code
    return d[(d.date <= cutoff) & d.code.ne('')].drop_duplicates(['date', 'code', 'type'], keep='last')


def assess_cohort(metrics: list[dict], *, stable_days: int = 0, was_broken: bool = False) -> dict:
    """固定篮子价格代理，不声称真实资金退出/出清完成；任何成员缺失均阻断升级。"""
    if not metrics or any(any(m.get(k) is None for k in ('r1', 'r5', 'dd20', 'above_ma5')) for m in metrics):
        return {'stage': '数据不足', 'weak_share': None, 'reason': '固定篮子有缺价或历史不足；不能据此认定出清。'}
    weak = sum(m['dd20'] <= -10 and not m['above_ma5'] for m in metrics) / len(metrics)
    if weak >= .6 and np.median([m['r1'] for m in metrics]) < -2:
        stage, reason = '瓦解', '多数固定核心深度回撤且当日继续下跌；仅为价格瓦解代理。'
    elif was_broken:
        stage = '出清观察' if stable_days >= 2 else '瓦解观察'
        reason = '至少2日不再普遍创新低，只能说明抛压暂稳，不等于出清完成。' if stable_days >= 2 else '等待旧篮子连续止跌，不急于推断新周期。'
    elif sum(m['above_ma5'] and m['r5'] > 0 for m in metrics) / len(metrics) >= .7:
        stage, reason = '强化', '固定篮子多数在5日均线上且5日收益为正；需量能与广度交叉确认。'
    else:
        stage, reason = '分歧', '固定篮子强弱分化；断板不等于整体瓦解。'
    return {'stage': stage, 'weak_share': round(weak, 3), 'reason': reason}


def price_matrices(prices: pd.DataFrame, dates: list[str]) -> dict:
    p = prices.pivot(index='date', columns='code', values='close').reindex(dates)
    r1 = p.pct_change(fill_method=None) * 100
    r5 = (p / p.shift(5) - 1).where(p.notna().rolling(6).sum().eq(6)) * 100
    r20 = (p / p.shift(20) - 1).where(p.notna().rolling(21).sum().eq(21)) * 100
    ma5 = p.rolling(5, min_periods=5).mean()
    peak = p.rolling(20, min_periods=5).max()
    dd20 = (p / peak - 1) * 100
    rank5, rank20 = r5.rank(axis=1, pct=True), r20.rank(axis=1, pct=True)
    persistence = rank5.ge(.85).rolling(5, min_periods=5).sum()
    score = (rank5 * 40 + rank20 * 40 + persistence * 4).where(r5.gt(4) & r20.gt(8) & persistence.ge(2))
    return dict(price=p, r1=r1, r5=r5, r20=r20, ma5=ma5, dd20=dd20, score=score, persistence=persistence)


def metrics_at(mats: dict, symbol: str, date: str) -> dict:
    if symbol not in mats['price'].columns or date not in mats['price'].index:
        return dict(r1=None, r5=None, r20=None, dd20=None, above_ma5=None, score=None)
    out = {k: number(mats[k].at[date, symbol]) for k in ('r1', 'r5', 'r20', 'dd20', 'score')}
    price, ma = mats['price'].at[date, symbol], mats['ma5'].at[date, symbol]
    out['above_ma5'] = bool(price >= ma) if pd.notna(price) and pd.notna(ma) else None
    return out


def stock_stats(series: pd.Series, start: str, end: str) -> dict:
    """区间收益用起始日前收盘；缺该基准时不冒充全区间收益。"""
    series = series.copy()
    series.index = series.index.astype(str)
    window = series.loc[(series.index >= start) & (series.index <= end)]
    valid = window.dropna()
    previous = series.loc[series.index < start].tail(1)
    base = previous.iloc[0] if not previous.empty else np.nan
    end_value = window.iloc[-1] if not window.empty else np.nan
    draw = pd.concat([previous, window])
    dd = (draw / draw.cummax() - 1) * 100
    return dict(first_date=str(valid.index[0]) if not valid.empty else None,
                last_date=str(valid.index[-1]) if not valid.empty else None,
                return_pct=number((end_value / base - 1) * 100) if pd.notna(base) else None,
                max_drawdown_pct=number(dd.min()), count=int(valid.size), expected_count=int(window.size),
                base_date=str(previous.index[0]) if pd.notna(base) else None,
                complete=bool(not window.empty and window.notna().all() and pd.notna(base)))


def _breadth(frame: pd.DataFrame, cutoff: str) -> dict:
    d = frame.rename(columns={'日期': 'date'}).copy()
    if d.empty or not {'date', 'up', 'down'} <= set(d):
        return {}
    d['date'] = pd.to_datetime(d.date.astype(str).str.replace('-', ''), format='%Y%m%d', errors='coerce').dt.strftime('%Y-%m-%d')
    result = {}
    for row in d[d.date <= cutoff].to_dict('records'):
        up, down = number(row.get('up')), number(row.get('down'))
        total = (up or 0) + (down or 0)
        result[row['date']] = number(up / total, 4) if up is not None and down is not None and total > 0 else None
    return result


def _scan(mats: dict, dates: list[str], names: dict, breadth: dict, counts: pd.Series) -> tuple[list, list]:
    daily, transitions = [], []
    current, pending, streak = None, None, 0
    for date in dates:
        prior = counts.loc[counts.index < date].tail(10)
        floor = .8 * float(prior.median()) if not prior.empty else 0
        complete = counts.get(date, 0) > 0 and counts.get(date, 0) >= floor
        top = mats['score'].loc[date].dropna().sort_values(ascending=False).head(6) if complete else pd.Series(dtype=float)
        codes = top.index.tolist()
        leader = codes[0] if codes else None
        streak = streak + 1 if leader and leader == pending else (1 if leader else 0)
        pending = leader
        if leader and streak >= 2 and current != leader:
            transitions.append(dict(date=date, old_code=current, old_name=names.get(current, current or '无已确认代理'),
                                    new_code=leader, new_name=names.get(leader, leader), kind='连续2日排名代理换位（不是资金因果证明）'))
            current = leader
        daily.append(dict(date=date, coverage=int(counts.get(date, 0)), coverage_ok=bool(complete),
                          up_share=breadth.get(date), top=[dict(code=c, name=names.get(c, c), **metrics_at(mats, c, date)) for c in codes],
                          confirmed_proxy=current, stage='数据不足' if not complete else ('候选竞争' if codes else '等待')))
    return daily, transitions


def build_crowding_report(report_date: str, *, data_dir: str | Path | None = None,
                          prices: pd.DataFrame | None = None, limits: pd.DataFrame | None = None,
                          sentiment: pd.DataFrame | None = None, calendar: list[str] | None = None,
                          names: dict | None = None, quotes: dict | None = None) -> dict:
    requested = day(report_date)
    start = (pd.Timestamp(requested) - pd.DateOffset(months=2)).strftime('%Y-%m-%d')
    root = Path(data_dir or DATA_DIR)
    prices = read_csv(root / 'price_history_cache.csv') if prices is None else prices
    limits = read_csv(root / '涨停历史缓存.csv') if limits is None else limits
    sentiment = read_csv(root / 'sentiment_history_cache.csv') if sentiment is None else sentiment
    p, z = normalize_prices(prices, requested), normalize_limits(limits, requested)
    p = p[p.date >= (pd.Timestamp(start) - pd.Timedelta(days=60)).strftime('%Y-%m-%d')]
    warnings = []
    if calendar is None:
        cal = read_csv(root / 'trading_calendar_cache.csv')
        calendar = cal['trade_date'].tolist() if 'trade_date' in cal else []
    calendar = sorted({day(d) for d in calendar if day(d) <= requested})
    calendar_known = bool(calendar)
    if not calendar:
        calendar = sorted(p.date.unique())
        warnings.append('缺少交易日历，无法可靠判断缺失交易日；使用观察日期降级展示。')
    expected = calendar[-1] if calendar else None
    counts = p.groupby('date').code.nunique().sort_index()
    # 不把少量晚到行抬成全市场截止日；逐日与此前10日截面比较。
    complete_dates = [d for d, n in counts.items() if d in calendar and n >= .8 * (counts.loc[counts.index < d].tail(10).median() if len(counts.loc[counts.index < d]) else n)]
    asof = complete_dates[-1] if complete_dates else None
    lag = len([d for d in calendar if asof and d > asof]) if calendar_known and asof else None
    if not asof:
        warnings.append('没有可用的全市场前复权价格截面，禁止确认抱团状态。')
    elif lag:
        warnings.append(f'全市场仅到{asof}，距应有交易日{expected}落后{lag}个交易日；不能据此给出今天的新周期确认。')
    warnings.extend(['成交额、换手率、分时承接及委托可成交性未接入；本模块只有价格强势代理。',
                     '截图是2026-10-08提供的事后注释，不能作为9月当时已知信号回测。',
                     '使用当前缓存前复权历史与可用股票样本；存在复权修订和存活样本偏差，无胜率/盈亏保证。'])
    if names is None:
        universe = read_csv(root / 'stock_universe.csv')
        names = dict(zip(universe['code'], universe['name'])) if {'code', 'name'} <= set(universe) else {}
    resolved = dict(names or {})
    if not z.empty:
        resolved.update(dict(zip(z.sort_values('date').code, z.sort_values('date')['name'])))
    resolved.update(NAMES if requested >= REFERENCE_DATE else {})
    first_price_date = p.date.min() if not p.empty else requested
    dates = [d for d in calendar if not p.empty and d >= first_price_date and d <= (asof or '')]
    mats = price_matrices(p[p.date <= (asof or '')], dates) if dates else None
    window_dates = [d for d in dates if d >= start]
    breadth = _breadth(sentiment, requested)
    daily, succession = _scan(mats, window_dates, resolved, breadth, counts) if mats else ([], [])
    if quotes is None:
        path = root / 'crowding_reference_quotes.json'
        quotes = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    groups, periods, events, paths = [], [], [], {}
    trial_gates = []
    reference_visible = requested >= REFERENCE_DATE and start <= REFERENCE_DATE
    # 辅助日线严格按请求日截断，只供截图组；不参与全市场排名。
    quote_dates = []
    series_by_symbol = {}
    if reference_visible:
        for group in GROUPS:
            members = []
            for symbol in group['codes']:
                q = quotes.get('stocks', {}).get(symbol, {})
                rows = [r for r in q.get('rows', []) if start <= r['date'] <= requested]
                warm = sorted([r for r in q.get('rows', []) if r['date'] <= requested], key=lambda x: x['date'])
                if rows:
                    series = pd.Series({r['date']: float(r['close']) for r in warm}).sort_index().reindex([d for d in calendar if warm[0]['date'] <= d <= expected])
                    source = '腾讯补充日线 / ' + q.get('basis', '未知口径')
                    quote_dates.append(rows[-1]['date'])
                elif mats and symbol in mats['price']:
                    series = mats['price'][symbol].reindex([d for d in calendar if d >= dates[0]])
                    source = '全市场本地前复权缓存'
                else:
                    series = pd.Series(dtype=float)
                    source = '缺少行情'
                series_by_symbol[symbol] = series
                if not series.empty:
                    # 缺失日保留null，走势图不得横跨空洞伪装连续价格。
                    paths[symbol] = [dict(date=d, close=number(v, 4)) for d, v in series.items() if d >= start]
                valid = series.dropna()
                last = str(valid.index[-1]) if not valid.empty else None
                target_end = min(requested, expected) if expected else requested
                local = pd.DataFrame({'date': series.index, 'code': symbol, 'close': series.values})
                qm = price_matrices(local, series.index.tolist()) if not series.empty else None
                metrics = metrics_at(qm, symbol, target_end) if qm else metrics_at({'price': pd.DataFrame()}, symbol, target_end)
                from_reference = series.get(group['start'], np.nan)
                end_value = series.get(target_end, np.nan)
                since = number((end_value / from_reference - 1) * 100) if pd.notna(from_reference) else None
                refs = z[(z.code == symbol) & (z.type == 'ZT') & (z.date >= group['start'])]
                members.append(dict(code=symbol, name=resolved.get(symbol, symbol), source=source, as_of=last,
                                    reference_date=group['start'], since_reference=since,
                                    two_month=stock_stats(series, start, target_end), metrics=metrics,
                                    zt_days=int(refs.date.nunique()), max_height=number(refs.height.max()),
                                    role=('既有趋势延续，非9/28低位首发' if symbol == 'sh688137' else
                                          '9/28列入候选，起涨与确认需分开' if group['id'] == 'trial' else
                                          '次新独立观察，常规连板不适用' if symbol == 'sh601091' else '截图/复盘样本，非事前推荐')))
            old = group['id'] not in ('trial', 'parallel')
            stable = 0
            was_broken = False
            history = []
            # 固定篮子逐日走状态，不允许通过剔除失败者制造“出清”。
            group_days = [d for d in calendar if max(group['start'], start) <= d <= requested]
            for d in group_days:
                snapshots, lows = [], []
                for member in members:
                    symbol = member['code']
                    s = series_by_symbol[symbol]
                    sample = s.loc[s.index <= d] if not s.empty else s
                    if len(sample) >= 6:
                        r1 = number((sample.iloc[-1] / sample.iloc[-2] - 1) * 100)
                        r5 = number((sample.iloc[-1] / sample.iloc[-6] - 1) * 100) if sample.tail(6).notna().all() else None
                        dd = number((sample.iloc[-1] / sample.tail(20).max() - 1) * 100)
                        above = bool(sample.iloc[-1] >= sample.tail(5).mean()) if sample.tail(5).notna().all() else None
                        snapshots.append(dict(r1=r1, r5=r5, dd20=dd, above_ma5=above))
                        lows.append(bool(sample.iloc[-1] < sample.iloc[:-1].tail(3).min()) if sample.tail(4).notna().all() else None)
                    else:
                        snapshots.append({}); lows.append(None)
                stable = stable + 1 if lows and all(x is not None for x in lows) and not any(lows) else 0
                state = assess_cohort(snapshots, stable_days=stable, was_broken=was_broken)
                was_broken = was_broken or state['stage'] == '瓦解'
                history.append(dict(date=d, **state))
            latest = history[-1] if history else assess_cohort([])
            if group['id'] == 'trial':
                latest = dict(stage='试错待确认', weak_share=None, reason='仅价格证据；全市场、成交与承接条件尚未全部验证。')
            if group['id'] == 'parallel':
                latest = dict(stage='并行观察', weak_share=None, reason='数字经济、电力、次新不应强行算作同一篮子。')
            groups.append(dict(**group, members=members, assessment=latest, history=history,
                               next_step='继续观察旧团失效和新候选承接，缺全市场证据不升级。' if not old else '停止把反抽自动视为新周期，逐股核对破位与修复。'))
        for a, b, title, thesis, ids in PERIODS:
            if b < start or a > requested:
                continue
            a, b = max(a, start), min(b, requested)
            leaders = []
            if mats:
                rows = z[(z.date >= a) & (z.date <= b) & (z.type == 'ZT')]
                tops = rows.groupby('code').height.max().sort_values(ascending=False).head(5)
                leaders = [dict(code=c, name=resolved.get(c, c), max_height=number(h)) for c, h in tops.items()]
            members = []
            for g in groups:
                if g['id'] in ids:
                    for member in g['members']:
                        s = series_by_symbol[member['code']]
                        members.append(dict(code=member['code'], name=member['name'], **stock_stats(s.sort_index(), a, b)))
            periods.append(dict(start=a, end=b, title=title, thesis=thesis, leaders=leaders, members=members,
                                boundary_source='事后研究分段，不是实时自动分界', group_ids=ids))
        for date, claim, symbols in EVENTS:
            evidence = []
            for symbol in symbols:
                before = p[(p.code == symbol) & (p.date <= date)].sort_values('date')
                exact = not before.empty and before.iloc[-1].date == date
                r1 = number((before.iloc[-1].close / before.iloc[-2].close - 1) * 100) if exact and len(before) >= 2 else None
                hits = z[(z.date == date) & (z.code == symbol)]
                evidence.append(dict(code=symbol, name=resolved.get(symbol, symbol), r1=r1,
                                     limit_types=hits.type.tolist(), max_height=number(hits.height.max())))
            events.append(dict(date=date, claim=claim, evidence=evidence,
                               status='研究注释；价格/涨停记录只验证局部现象，不验证“资金出清”因果'))
    if reference_visible:
        old_baskets = [g for g in groups if g['id'] in ('farm', 'consumer')]
        old_stable = bool(old_baskets) and all(g['assessment']['stage'] == '出清观察' for g in old_baskets)
        fresh_breadth = breadth.get(expected) if calendar_known and expected else None
        for member in next(g for g in groups if g['id'] == 'trial')['members']:
            mt = member['metrics']
            s = series_by_symbol[member['code']].reindex(calendar).tail(4)
            changes = s.pct_change(fill_method=None).iloc[1:]
            price_support = bool(len(changes) == 3 and changes.notna().all() and changes.tail(2).gt(0).all()
                                 and mt.get('above_ma5') and (mt.get('r5') or 0) > 0)
            trial_gates.append(dict(
                code=member['code'], name=member['name'], as_of=member['as_of'],
                label='价格持续性支持，仍待全证据' if price_support else '价格分歧 / 待观察',
                since_reference=member['since_reference'], reference_date=member['reference_date'],
                gates=[
                    dict(name='旧篮子连续止跌', status='初步支持' if old_stable else '未满足',
                         note='农业与消费固定篮子均至少2日不创新近3日低点；止跌不等于完成出清。'),
                    dict(name='全市场转暖', status='数据缺失/过期' if fresh_breadth is None else
                         ('初步支持' if fresh_breadth >= .55 else '未满足'),
                         note=f'需要{expected}的涨跌家数；上涨占比≥55%仅是未经校准的研究阈值。'),
                    dict(name='候选价格持续性', status='初步支持' if price_support else '未满足/缺价',
                         note='最近2个完整交易日收涨、在MA5上且5日收益为正；不等于可买。'),
                    dict(name='量能、承接及可成交', status='待人工验证',
                         note='日K不足以证明分时承接和可成交性；涨停不等于有买入机会。')],
                action='保留观察，不升级为已确认新周期', position=None))
    # 非参考时段仍提供滚动周复盘，不让模块停留于硬编码9月。
    if not periods and daily:
        for offset in range(0, len(daily), 5):
            block = daily[offset:offset + 5]
            periods.append(dict(start=block[0]['date'], end=block[-1]['date'], title='滚动5交易日观察',
                                thesis='按当日可见行情筛选价格强势代理，等待跨日验证。', leaders=block[-1]['top'],
                                members=[], group_ids=[], boundary_source='固定观察窗口，不宣称真实市场周期'))
    clocks = dict(prices=asof, limits=z.date.max() if not z.empty else None,
                  breadth=max((d for d, value in breadth.items() if value is not None), default=None),
                  reference_quotes=max(quote_dates) if quote_dates else None)
    lags = {key: len([d for d in calendar if last and d > last]) if calendar_known and last else None
            for key, last in clocks.items()}
    for key, label in [('limits', '涨跌停记录'), ('breadth', '市场涨跌家数')]:
        if not clocks[key]:
            warnings.append(f'{label}缺失，不能验证当日市场环境。')
        elif lags[key]:
            warnings.append(f'{label}仅到{clocks[key]}，落后{lags[key]}个交易日；不因价格更新而视为同步。')
    core_lags = [lags[key] for key in ('prices', 'limits', 'breadth')]
    quality = 'stale' if any(x for x in core_lags if x is not None) else (
        'partial' if not calendar_known or any(clocks[key] is None for key in ('prices', 'limits', 'breadth')) else 'research_only')
    return dict(schema='crowding-cycle/v1', requested_date=requested, window_start=start,
                market_as_of=asof, expected_trade_date=expected, lag_trading_days=lag,
                quality=quality, sources_as_of=clocks, sources_lag_trading_days=lags,
                warnings=warnings, reference_visible=reference_visible, groups=groups, periods=periods,
                events=events, paths=paths, daily=daily, succession=succession, trial_gates=trial_gates,
                tactics=[dict(t, example=t['example'] if reference_visible else '通用研究规则；本期不使用10月8日事后提供的具体股票名单。') for t in TACTICS],
                sources=[s for s in SOURCES if s['date'] <= requested],
                execution=dict(status='not_qualified', reason='研究观察，不生成自动交易信号；未有完整成交、量能和全市场时点证据。',
                               backtest='未做可成交回测；不提供胜率、收益承诺或固定买卖价。'))
