"""缠论系统分析: 指数区间套 × 板块结构矩阵 × 焦点个股结构健康度。

三层 (对应"指数+板块+焦点个股"的覆盖范围)
--------------------------------------------------
1. **指数** (上证 sh000001 / 创业板 sz399006): 日线 + 周线两级, 走区间套 —— 周线定方向,
   日线找买卖点。数据: 腾讯前复权日K (指数没有复权, qfq 参数无副作用)。
2. **板块** (90 个同花顺行业指数, data/ths_sector_hist.json, 零网络): 20 日强度分位 ×
   缠论结构 → 四象限 (强+结构好 / 强+结构坏 / 弱+结构转好 / 弱+结构坏)。战法回测也在这一层做。
3. **焦点个股** (主线天梯 S/B/C 级前 30 只): 腾讯前复权日K + 磁盘缓存, 按主线聚合"结构健康度"。
   个股价格缓存只有 close, 画不了笔 (笔要 high/low), 所以必须单独拉 OHLC。

与 czsc 的关系
--------------------------------------------------
内核 (chan_engine) 与信号 (chan_signals) 逐函数对齐 waditu/czsc 的 Rust 实现, 但不依赖它:
czsc 需要编译 Rust 扩展, CI 上装不上; 而且本项目原来 zhao_tracker 指向的 czsc 路径根本不存在,
导致"缠论分析"一直跑的是三点兜底 (HAVE_CZSC=False)。

可靠性约定 (与 leader_tracker / dragon_succession 一致)
--------------------------------------------------
* 任何一层失败只降级该层, 不阻断日报; 整体失败返回 None, 主报告不挂 section。
* 历史日期重跑: 所有数据都截到 report_date, 不会把今天的结构写进昨天的报告。
* 回测结论只引用板块层 (有完整历史); 个股/指数上的"战法命中"是**外推**, 页面上会写明。
"""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from html import escape

from chan_engine import DOWN, UP, RawBar, bars_from_records, resample_weekly
from chan_signals import ChanContext, compute_multi_level
from chan_tactics import TACTICS, TACTIC_BY_KEY, backtest_sectors, match_tactics

INDEXES = (('sh000001', '上证指数'), ('sz399006', '创业板指'))
FOCUS_GRADES = ('S级', 'B级', 'C级')
FOCUS_CAP = 30
KLINE_COUNT = 320          # ~15 个月: 日线 25+ 笔, 周线 ~65 根 / 6~10 笔, 够做区间套
MIN_BARS = 60
FETCH_TIMEOUT = 8
FETCH_WORKERS = 8
CACHE_KEEP_DAYS = 60       # 超过 60 天没被用到的个股缓存会被清掉 (焦点池每天在换)
TENCENT_KLINE = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'

_MEMO: dict = {}
_CACHE_LOCK = threading.Lock()

# ============================================================ 数据: 腾讯前复权日K + 缓存

def _norm_date(d) -> str:
    """任意日期形状 → 'YYYY-MM-DD'。"""
    s = str(d or '').strip().replace('-', '').replace('/', '')[:8]
    if len(s) != 8 or not s.isdigit():
        return ''
    return f'{s[:4]}-{s[4:6]}-{s[6:]}'


def _fetch_tencent(code: str, count: int = KLINE_COUNT) -> list[dict] | None:
    """腾讯前复权日K。失败返回 None (区别于"成功但 0 行")。

    trust_env=False: 绕开本机 Clash 代理 (东财/腾讯域名走代理会被拒, 见 memory
    price-cache-tencent-fastpath)。参数里的逗号不能让 requests 转义, 所以直接拼 URL。
    """
    import requests
    url = f'{TENCENT_KLINE}?param={code},day,,,{int(count)},qfq'
    try:
        s = requests.Session()
        s.trust_env = False
        r = s.get(url, timeout=FETCH_TIMEOUT, headers={'User-Agent': 'Mozilla/5.0'})
        node = (r.json().get('data') or {}).get(code) or {}
    except Exception:
        return None
    raw = node.get('qfqday') or node.get('day') or []
    rows = []
    for x in raw:
        try:
            rows.append({'date': _norm_date(x[0]), 'open': float(x[1]), 'close': float(x[2]),
                         'high': float(x[3]), 'low': float(x[4]), 'vol': float(x[5])})
        except (IndexError, TypeError, ValueError):
            continue
    return [r for r in rows if r['date']]


def _load_cache(path: str) -> dict:
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(path: str, cache: dict) -> None:
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, path)
    except OSError:
        pass


def _prune_cache(cache: dict, report_date: str) -> None:
    try:
        floor = (datetime.strptime(report_date, '%Y-%m-%d') - timedelta(days=CACHE_KEEP_DAYS)).strftime('%Y-%m-%d')
    except ValueError:
        return
    for code in [c for c, v in cache.items() if str(v.get('used', '')) < floor]:
        cache.pop(code, None)


def load_klines(codes, report_date: str, cache_path: str | None = None,
                fetch=_fetch_tencent) -> dict[str, list[dict]]:
    """批量取日K, 截到 report_date。缓存命中条件: 已有 report_date 那根K线, 或者此前已经
    用同一个 report_date 抓过 (停牌票抓了也没有当天K线, 别每次都重抓)。

    网络失败时退回缓存里的旧数据 (宁可结构旧一天, 也不整段消失), 调用方用末根日期判断陈旧。
    """
    codes = list(dict.fromkeys(c for c in codes if c))
    cache = _load_cache(cache_path) if cache_path else {}
    need = []
    for c in codes:
        ent = cache.get(c) or {}
        rows = ent.get('rows') or []
        last = rows[-1]['date'] if rows else ''
        if not (last >= report_date or str(ent.get('checked', '')) >= report_date):
            need.append(c)

    if need:
        with ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, len(need))) as ex:
            got = dict(zip(need, ex.map(fetch, need)))
        with _CACHE_LOCK:
            for c, rows in got.items():
                # 只存到 report_date 为止: 腾讯会返回当天盘中的半根K线, 存进去的话
                # 下一次 report_date=今天 时会被当成"已有当天K线"命中缓存, 拿盘中价画收盘结构。
                rows = [r for r in (rows or []) if r['date'] <= report_date]
                if rows:          # None=失败 / []=空: 都保留旧缓存
                    cache[c] = {'rows': rows, 'checked': report_date}

    out = {}
    for c in codes:
        ent = cache.get(c)
        if not ent:
            continue
        ent['used'] = max(str(ent.get('used', '')), report_date)
        out[c] = [r for r in ent.get('rows') or [] if r['date'] <= report_date]

    if cache_path and (need or codes):
        _prune_cache(cache, report_date)
        _save_cache(cache_path, cache)
    return out


# ============================================================ 单标的分析

def _bars(rows: list[dict], symbol: str) -> list[RawBar]:
    return bars_from_records(rows, symbol=symbol)


def analyze_symbol(bars: list[RawBar], symbol: str, name: str = '', with_weekly: bool = True) -> dict | None:
    """一个标的的完整缠论结构 + 信号 + 战法命中。K线不足 MIN_BARS 返回 None。"""
    if len(bars) < MIN_BARS:
        return None
    day = ChanContext(bars, symbol=symbol, freq='日线')
    week = None
    if with_weekly:
        wbars = resample_weekly(bars, symbol)
        if len(wbars) >= 20:
            week = ChanContext(wbars, symbol=symbol, freq='周线')
    sigs = compute_multi_level(day, week)
    tactics = match_tactics(sigs)
    stage, tone = classify_stage(sigs)
    zs = day.real_zs[-1] if day.real_zs else None
    last_bi = day.bis[-1] if day.bis else None
    return {
        'symbol': symbol,
        'name': name or symbol,
        'as_of': str(bars[-1].dt),
        'price': bars[-1].close,
        'n_bars': len(bars),
        'n_bi': len(day.bis),
        'n_zs': len(day.real_zs),
        'stage': stage,
        'tone': tone,                          # good / warn / bad / neutral
        'nest': nest_view(sigs, week),
        'stop': stop_level(stage, day),
        'zs': ({'zg': zs.zg, 'zd': zs.zd, 'gg': zs.gg, 'dd': zs.dd, 'n_bi': len(zs.bis),
                'sdt': str(zs.sdt), 'edt': str(zs.edt)} if zs else None),
        'last_bi': ({'direction': last_bi.direction, 'high': last_bi.high, 'low': last_bi.low,
                     'change': last_bi.change, 'length': last_bi.length,
                     'sdt': str(last_bi.sdt), 'edt': str(last_bi.edt)} if last_bi else None),
        'ubi_len': day.c.ubi_len,
        'signals': {k: str(s) for k, s in sigs.items()},
        'tactics': [t.key for t in tactics],
        '_ctx': day,                           # 画图用; 序列化前会被剥掉
    }


def _v(sigs: dict, key: str, freq: str = '日线') -> tuple[str, str, str]:
    s = sigs.get(f'{freq}_{key}')
    return (s.v1, s.v2, s.v3) if s is not None else ('其他', '任意', '任意')


_FORM_KEYS = ('D1五笔_形态', 'D1七笔_形态')


def _form_has(sigs: dict, word: str) -> bool:
    return any(word in _v(sigs, k)[0] for k in _FORM_KEYS)


def classify_stage(sigs: dict) -> tuple[str, str]:
    """把一堆信号归并成一个"当前所处阶段"。优先级 = 风险优先 (先报坏消息)。

    返回 (阶段文字, tone)。tone 用于颜色与聚合: good 可做 / warn 警惕 / bad 回避 / neutral 观望。
    """
    zs3 = _v(sigs, 'D1_中枢三买卖')
    if zs3[0] == '三卖' or _form_has(sigs, '类三卖'):
        return '三卖·下跌确认', 'bad'
    bc = _v(sigs, 'D1_MACD背驰')
    if (bc[0] == '顶背驰' and bc[1] == '显著') or _v(sigs, 'D1B_SELL1')[0] == '一卖' or _form_has(sigs, '顶背驰'):
        return '顶背驰·减仓区', 'warn'
    if _v(sigs, 'D0_进行中背驰')[0] == '顶背驰预警':
        return '新高笔力度衰减·预警', 'warn'
    if zs3[0] == '三买' or _v(sigs, 'D1_三买辅助')[0] == '三买':
        return '三买·主升确认', 'good'
    if _v(sigs, 'D1_二买卖')[0] == '二买':
        return '二买·底部确认', 'good'
    pend = _v(sigs, 'D1_三买候选')
    if pend[0] == '回抽守ZG':
        return '离开中枢·回抽守ZG', 'good'
    if _v(sigs, 'D1B_BUY1')[0] == '一买' or _form_has(sigs, '底背驰'):
        return '一买·底背驰', 'neutral'
    pos = _v(sigs, 'D1_中枢位置')
    trend = _v(sigs, 'D1_中枢走向')[0]
    if pos[0] == '中枢上方':
        if trend in ('上移', '重叠上移'):
            return '中枢上移·上涨趋势', 'good'
        return '中枢上方·强于结构', 'neutral'
    if pos[0] == '中枢下方':
        if pend[0] == '已破ZG':
            return '突破失败·跌回中枢', 'bad'
        if trend in ('下移', '重叠下移'):
            return '中枢下移·下跌趋势', 'bad'
        return '中枢下方·弱于结构', 'warn'
    if pos[0] == '中枢内部':
        return f'中枢震荡·{pos[1]}', 'neutral'
    return '结构未成形', 'neutral'


def nest_view(sigs: dict, week: ChanContext | None) -> str:
    """区间套: 周线方向 × 日线位置 → 一句操作语境。"""
    if week is None or len(week.bis) < 3:
        return '周线笔数不足, 仅看日线'
    wdir = _v(sigs, 'D1_表里关系', '周线')[0]
    wpos = _v(sigs, 'D1_中枢位置', '周线')[0]
    wbc = _v(sigs, 'D1_MACD背驰', '周线')[0]
    stage, tone = classify_stage(sigs)
    w = f'周线{wdir}笔' + (f'·{wpos}' if wpos not in ('其他', '无中枢') else '')
    if wbc == '顶背驰':
        return f'{w}·周线顶背驰 → 大级别动能衰竭, 日线买点只做短差'
    if wdir == UP:
        if tone == 'good':
            return f'{w} × 日线{stage} → 大小级别同向, 可做'
        if tone in ('warn', 'bad'):
            return f'{w} × 日线{stage} → 大级别向上中的小级别回调, 等日线买点'
        return f'{w} × 日线{stage} → 顺大级别, 等日线结构选点'
    if wdir == DOWN:
        if tone == 'good':
            return f'{w} × 日线{stage} → 大级别向下中的反弹, 只做反弹不做趋势'
        if tone in ('warn', 'bad'):
            return f'{w} × 日线{stage} → 大小级别同向向下, 回避'
        return f'{w} × 日线{stage} → 大级别未转, 日线底部信号先当反弹看'
    return f'{w} × 日线{stage}'


def stop_level(stage: str, day: ChanContext) -> float | None:
    """按阶段给结构止损位: 三买/离开中枢/上涨趋势看 ZG, 二买/一买看最近向下笔低点。"""
    zs = day.real_zs[-1] if day.real_zs else None
    if any(k in stage for k in ('三买', '离开中枢', '中枢上移')) and zs is not None:
        return round(zs.zg, 3)
    if any(k in stage for k in ('二买', '一买')):
        downs = [b for b in day.bis if b.direction == DOWN]
        if downs:
            return round(downs[-1].low, 3)
    return None


def chart_payload(day: ChanContext, n: int = 160) -> dict:
    """给 ECharts 的 K线 + 笔折线 + 中枢方框。只取最近 n 根, 笔/中枢裁到窗口内。"""
    bars = day.bars[-n:]
    if not bars:
        return {}
    d0 = bars[0].dt
    pts = []
    for bi in day.bis:
        for fx in (bi.fx_a, bi.fx_b):
            if fx.dt >= d0 and (not pts or pts[-1][0] != str(fx.dt)):
                pts.append([str(fx.dt), round(fx.fx, 3)])
    boxes = [{'x0': str(max(z.sdt, d0)), 'x1': str(z.edt), 'zg': round(z.zg, 3), 'zd': round(z.zd, 3)}
             for z in day.real_zs if z.edt >= d0]
    return {
        'dates': [str(b.dt) for b in bars],
        'ohlc': [[round(b.open, 3), round(b.close, 3), round(b.low, 3), round(b.high, 3)] for b in bars],
        'bi': pts,
        'zs': boxes,
    }


# ============================================================ 板块层

_QUADRANTS = {
    ('强', 'good'): '强势+结构健康', ('强', 'neutral'): '强势+结构待定', ('强', 'warn'): '强势+结构背驰',
    ('强', 'bad'): '强势+结构破坏', ('弱', 'good'): '弱势+结构转好', ('弱', 'neutral'): '弱势+结构待定',
    ('弱', 'warn'): '弱势+结构偏弱', ('弱', 'bad'): '弱势+结构破坏',
}


def analyze_sectors(sector_hist: dict, report_date: str) -> list[dict]:
    """全部板块指数: 20 日强度分位 × 缠论阶段。返回按强度降序的列表。"""
    out = []
    for name, rows in (sector_hist or {}).items():
        rows = [r for r in rows if _norm_date(r.get('date')) and _norm_date(r.get('date')) <= report_date]
        bars = _bars(rows, name)
        res = analyze_symbol(bars, name, name)
        if res is None:
            continue
        res['ret20'] = (bars[-1].close / bars[-21].close - 1) if len(bars) > 21 and bars[-21].close else None
        res['stale'] = _norm_date(res['as_of']) < report_date
        out.append(res)
    ranked = sorted([r for r in out if r['ret20'] is not None], key=lambda r: r['ret20'])
    for i, r in enumerate(ranked):
        r['pct'] = (i + 1) / len(ranked)
    for r in out:
        pct = r.get('pct')
        r['strength'] = '强' if pct is not None and pct >= 0.5 else '弱'
        r['quadrant'] = _QUADRANTS.get((r['strength'], r['tone']), '')
    out.sort(key=lambda r: -(r.get('pct') or 0))
    return out


# ============================================================ 焦点个股层

def pick_focus(mainline_ladder: dict | None, cap: int = FOCUS_CAP) -> list[dict]:
    """从主线天梯按 S→B→C 取前 cap 只 (天梯内部已按强度降序)。代码必须是 sh/sz/bj + 6 位。"""
    picked, seen = [], set()
    for g in FOCUS_GRADES:
        for it in (mainline_ladder or {}).get(g) or []:
            code = str(it.get('code') or '').lower()
            if len(code) != 8 or code[:2] not in ('sh', 'sz', 'bj') or not code[2:].isdigit() or code in seen:
                continue
            seen.add(code)
            picked.append({'code': code, 'name': it.get('name') or code, 'ml': it.get('ml') or '',
                           'sub': it.get('sub') or '', 'grade': g, 'score': it.get('score')})
            if len(picked) >= cap:
                return picked
    return picked


def mainline_health(focus: list[dict]) -> list[dict]:
    """按主线聚合焦点股的结构健康度。good 占比 >= 60% 且 bad <= 20% 叫"结构健康"。"""
    groups: dict[str, list[dict]] = {}
    for f in focus:
        groups.setdefault(f.get('ml') or '未归类', []).append(f)
    out = []
    for ml, items in groups.items():
        n = len(items)
        cnt = {t: sum(1 for x in items if x['tone'] == t) for t in ('good', 'warn', 'bad', 'neutral')}
        good, bad = cnt['good'] / n, (cnt['bad'] + cnt['warn']) / n
        if good >= 0.6 and cnt['bad'] / n <= 0.2:
            label = '结构健康'
        elif bad >= 0.5:
            label = '结构走坏'
        else:
            label = '结构分化'
        out.append({'ml': ml, 'n': n, **cnt, 'label': label,
                    'names': [x['name'] for x in items]})
    out.sort(key=lambda r: (-r['n'], -r['good']))
    return out


# ============================================================ 汇总入口

def _load_sector_hist() -> dict:
    try:
        from phase_resonance import THS_CACHE
        with open(THS_CACHE, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def build_chan_analysis(report_date, mainline_ladder: dict | None = None,
                        sector_hist: dict | None = None, fetch=_fetch_tencent,
                        kline_cache: str | None = '', backtest_cache: str | None = '') -> dict | None:
    """缠论三层分析。report_date 必填; 同一 (日期, 焦点池) 在一个进程内只算一次。

    kline_cache / backtest_cache 传 '' 表示用 paths 里的默认缓存, 传 None 表示不落盘 (测试用)。
    """
    rd = _norm_date(report_date)
    if not rd:
        return None
    focus_pick = pick_focus(mainline_ladder)
    memo_key = (rd, tuple(f['code'] for f in focus_pick))
    if memo_key in _MEMO:
        return _MEMO[memo_key]
    if kline_cache == '' or backtest_cache == '':
        from paths import CHAN_BACKTEST_CACHE, CHAN_KLINE_CACHE
        kline_cache = CHAN_KLINE_CACHE if kline_cache == '' else kline_cache
        backtest_cache = CHAN_BACKTEST_CACHE if backtest_cache == '' else backtest_cache

    result: dict = {'report_date': rd, 'indexes': [], 'sectors': [], 'focus': [],
                    'mainline_health': [], 'backtest': None, 'warnings': []}

    # ---- 板块层 + 回测 (零网络) ----
    hist = sector_hist if sector_hist is not None else _load_sector_hist()
    if hist:
        try:
            result['sectors'] = analyze_sectors(hist, rd)
        except Exception as e:
            result['warnings'].append(f'板块层失败: {e}')
        try:
            cut = {k: [r for r in v if _norm_date(r.get('date')) and _norm_date(r.get('date')) <= rd]
                   for k, v in hist.items()}
            result['backtest'] = backtest_sectors(cut, cache_path=backtest_cache)
        except Exception as e:
            result['warnings'].append(f'战法回测失败: {e}')
    else:
        result['warnings'].append('板块指数缓存缺失, 板块层与回测跳过')

    # ---- 指数 + 焦点个股 (一次批量取K线) ----
    codes = [c for c, _ in INDEXES] + [f['code'] for f in focus_pick]
    try:
        klines = load_klines(codes, rd, cache_path=kline_cache, fetch=fetch)
    except Exception as e:
        klines = {}
        result['warnings'].append(f'K线获取失败: {e}')

    for code, name in INDEXES:
        res = analyze_symbol(_bars(klines.get(code) or [], code), code, name)
        if res is None:
            result['warnings'].append(f'{name} K线不足, 指数层跳过')
            continue
        res['chart'] = chart_payload(res['_ctx'])
        if _norm_date(res['as_of']) < rd:
            result['warnings'].append(f"{name} 最新K线 {res['as_of']} 早于报告日 {rd} (数据滞后)")
        result['indexes'].append(res)

    for f in focus_pick:
        res = analyze_symbol(_bars(klines.get(f['code']) or [], f['code']), f['code'], f['name'])
        if res is None:
            continue
        res.update({k: f[k] for k in ('ml', 'sub', 'grade', 'score')})
        res['stale'] = _norm_date(res['as_of']) < rd
        result['focus'].append(res)
    if focus_pick and not result['focus']:
        result['warnings'].append('焦点个股K线全部获取失败, 个股层跳过')
    result['mainline_health'] = mainline_health(result['focus'])
    result['headline'] = _headline(result)

    if not (result['indexes'] or result['sectors'] or result['focus']):
        return None
    _MEMO[memo_key] = result
    return result


def _headline(r: dict) -> str:
    parts = []
    for ix in r['indexes']:
        parts.append(f"{ix['name']} {ix['stage']}")
    secs = r['sectors']
    if secs:
        good = sum(1 for s in secs if s['tone'] == 'good')
        bad = sum(1 for s in secs if s['tone'] == 'bad')
        parts.append(f'板块 {good}/{len(secs)} 结构健康、{bad} 个破坏')
    foc = r['focus']
    if foc:
        good = sum(1 for s in foc if s['tone'] == 'good')
        risky = sum(1 for s in foc if s['tone'] in ('warn', 'bad'))
        parts.append(f'焦点股 {good}/{len(foc)} 结构健康、{risky} 只背驰/破位')
    return ' · '.join(parts)


def to_jsonable(result: dict) -> dict:
    """剥掉画图用的 _ctx (不可序列化), 供快照/测试使用。"""
    def strip(x):
        return {k: v for k, v in x.items() if k != '_ctx'}
    out = dict(result)
    for k in ('indexes', 'sectors', 'focus'):
        out[k] = [strip(x) for x in result.get(k) or []]
    return out


# ============================================================ 渲染: 公共片段

_TONE_CLR = {'good': '#e04b4b', 'warn': '#ffb454', 'bad': '#3fb950', 'neutral': '#9aa0a6'}
_TONE_TXT = {'good': '可做', 'warn': '警惕', 'bad': '回避', 'neutral': '观望'}


def _e(x) -> str:
    return escape(str(x if x is not None else ''))


def _pct(v, digits: int = 1, plus: bool = True) -> str:
    if v is None:
        return '-'
    s = f'{v * 100:+.{digits}f}%' if plus else f'{v * 100:.{digits}f}%'
    return s


def _stage_tag(r: dict) -> str:
    c = _TONE_CLR.get(r.get('tone'), '#9aa0a6')
    return f"<span style='color:{c};font-weight:700'>{_e(r.get('stage'))}</span>"


def _tactic_names(keys) -> str:
    return '、'.join(TACTIC_BY_KEY[k].event.name for k in keys if k in TACTIC_BY_KEY) or '-'


def _zs_txt(r: dict) -> str:
    zs = r.get('zs')
    if not zs:
        return '无真中枢'
    return f"ZG {zs['zg']:.2f} / ZD {zs['zd']:.2f} ({zs['n_bi']}笔)"


def _bt_rows(bt: dict | None, compact: bool) -> str:
    if not bt:
        return ''
    trs = []
    for t in TACTICS:
        s = (bt.get('tactics') or {}).get(t.key)
        if not s:
            continue
        t10 = s.get('t10') or {}
        t5 = s.get('t5') or {}
        edge = t10.get('edge')
        good = edge is not None and ((edge > 0.01) if t.side == 'long' else (edge < -0.01))
        clr = '#e04b4b' if good else ('#9aa0a6' if edge is None or abs(edge) <= 0.01 else '#3fb950')
        small = " <span class=cs-note>(小样本)</span>" if s.get('small_sample') else ''
        cells = [f"<td>{_e(s['name'])}{small}</td>", f"<td>{s['n']}</td>"]
        if not compact:
            cells += [f"<td>{s['distinct_sectors']}</td>", f"<td>{s['distinct_dates']}</td>",
                      f"<td>{_pct(s.get('top3_share'), 0, False)}</td>",
                      f"<td>{_pct(t5.get('mean'))}</td>", f"<td>{_pct(t5.get('edge'))}</td>"]
        cells += [f"<td>{_pct(t10.get('mean'))}</td>",
                  f"<td style='color:{clr};font-weight:700'>{_pct(edge)}</td>",
                  f"<td>{_pct(t10.get('win'), 0, False)}</td>",
                  f"<td style='text-align:left'>{_e(s.get('verdict'))}</td>"]
        trs.append('<tr>' + ''.join(cells) + '</tr>')
    head = ['战法', '样本']
    if not compact:
        head += ['独立板块', '独立日期', '前3板块占比', 'T+5均值', 'T+5超额']
    head += ['T+10均值', 'T+10超额', 'T+10胜率', '结论']
    return ("<table class='cs-tbl'><tr>" + ''.join(f'<th>{h}</th>' for h in head) + '</tr>'
            + ''.join(trs) + '</table>')


def _bt_note(bt: dict | None) -> str:
    if not bt:
        return ''
    dr = bt.get('date_range') or ['', '']
    base = bt.get('baseline') or {}
    return (f"回测: {bt.get('n_sectors')} 个同花顺行业指数 {_e(dr[0])}~{_e(dr[1])}, "
            f"{_e(bt.get('rules'))}; 基准 (任意日买入) T+5 {_pct(base.get('t5'))} / T+10 {_pct(base.get('t10'))}。"
            "超额 = 战法均值 - 基准; 离场类战法超额为负才叫有效。板块之间同涨同跌, 独立日期数才是真实样本量。")


# ============================================================ 渲染: 主报告 section

_CS_CSS = """
<style>
.chan-sec{margin:18px 0;padding:14px 16px;border:1px solid #2a2d34;border-radius:10px;
  background:#16181d;color:#d7dae0;font-size:13px;line-height:1.6;}
.chan-sec .cs-title{font-size:15px;font-weight:700;margin-bottom:6px;}
.chan-sec .cs-headline{color:#ffd479;background:#23262d;padding:8px 10px;border-radius:6px;
  margin-bottom:12px;overflow-wrap:anywhere;}
.chan-sec .cs-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;}
.chan-sec .cs-col{min-width:0;overflow-wrap:anywhere;border-left:2px solid #2a2d34;padding-left:12px;}
.chan-sec .cs-h{font-weight:700;color:#8ab4f8;margin-bottom:6px;}
.chan-sec .cs-row{margin:4px 0;}
.chan-sec .cs-sub{font-size:12px;color:#9aa0a6;}
.chan-sec .cs-note{color:#767b83;font-size:11px;}
.chan-sec .cs-warn{color:#ffb454;font-size:12px;margin-top:6px;}
.chan-sec .cs-tbl-wrap{overflow-x:auto;margin-top:10px;}
.chan-sec .cs-tbl{border-collapse:collapse;width:100%;font-size:12px;margin:6px 0;}
.chan-sec .cs-tbl th,.chan-sec .cs-tbl td{border:1px solid #2a2d34;padding:3px 6px;text-align:center;
  white-space:nowrap;}
.chan-sec .cs-link{display:inline-block;margin-top:10px;color:#58a6ff;text-decoration:none;font-weight:700;}
@media (max-width:760px){.chan-sec .cs-grid{grid-template-columns:1fr;}
  .chan-sec .cs-col{border-left:none;border-top:1px solid #2a2d34;padding-left:0;padding-top:10px;}}
</style>
"""


def _index_col(indexes: list[dict]) -> str:
    rows = []
    for ix in indexes:
        bc = ix['signals'].get('日线_D0_进行中背驰', '')
        warn = "<div class='cs-warn'>⚠ 进行中的笔已出现顶背驰预警</div>" if '顶背驰预警' in bc else ''
        rows.append(f"<div class='cs-row'><b>{_e(ix['name'])}</b> {_stage_tag(ix)}"
                    f"<div class='cs-sub'>{_e(ix['nest'])}</div>"
                    f"<div class='cs-sub'>最近真中枢 {_e(_zs_txt(ix))} · 日线 {ix['n_bi']} 笔</div>{warn}</div>")
    body = ''.join(rows) or "<div class='cs-sub'>指数K线不可用</div>"
    return f"<div class='cs-col'><div class='cs-h'>① 指数区间套 (周线定向 × 日线选点)</div>{body}</div>"


def _sector_col(sectors: list[dict]) -> str:
    if not sectors:
        return "<div class='cs-col'><div class='cs-h'>② 板块结构矩阵</div><div class='cs-sub'>板块缓存缺失</div></div>"
    groups = [
        ('强势+结构健康', '顺势做', ('强', ('good',))),
        ('强势+背驰/破坏', '强而背驰, 别追', ('强', ('warn', 'bad'))),
        ('弱势+结构转好', '左侧观察', ('弱', ('good',))),
    ]
    rows = []
    for title, hint, (st, tones) in groups:
        items = [s for s in sectors if s['strength'] == st and s['tone'] in tones][:6]
        names = '、'.join(f"{_e(s['name'])}<span class=cs-note>({_e(s['stage'].split('·')[0])})</span>"
                         for s in items) or '<span class=cs-note>无</span>'
        rows.append(f"<div class='cs-row'><b>{title}</b> <span class=cs-note>{hint}</span><br>{names}</div>")
    return ("<div class='cs-col'><div class='cs-h'>② 板块结构矩阵 (20日强度 × 缠论阶段)</div>"
            + ''.join(rows) + '</div>')


def _health_col(health: list[dict], focus: list[dict]) -> str:
    if not focus:
        return "<div class='cs-col'><div class='cs-h'>③ 主线结构健康度</div><div class='cs-sub'>焦点股K线不可用</div></div>"
    clr = {'结构健康': '#e04b4b', '结构分化': '#ffb454', '结构走坏': '#3fb950'}
    rows = [f"<div class='cs-row'><b>{_e(h['ml'])}</b> "
            f"<span style='color:{clr.get(h['label'], '#9aa0a6')};font-weight:700'>{h['label']}</span> "
            f"<span class=cs-sub>健康 {h['good']} / 警惕 {h['warn']} / 破坏 {h['bad']} / 观望 {h['neutral']}</span></div>"
            for h in health[:6]]
    return ("<div class='cs-col'><div class='cs-h'>③ 主线结构健康度 (天梯焦点股)</div>"
            + ''.join(rows) + '</div>')


def _focus_table(focus: list[dict], limit: int | None) -> str:
    items = focus[:limit] if limit else focus
    trs = []
    for f in items:
        stop = (f"{f['stop']:.2f} <span class=cs-note>({_pct(f['stop'] / f['price'] - 1, 0)})</span>"
                if f.get('stop') and f.get('price') else '-')
        stale = " <span class=cs-note>(K线滞后)</span>" if f.get('stale') else ''
        trs.append(f"<tr><td>{_e(f['name'])}{stale}</td><td>{_e(f.get('grade'))}</td><td>{_e(f.get('ml'))}</td>"
                   f"<td>{_stage_tag(f)}</td><td>{_e(_zs_txt(f))}</td><td>{stop}</td>"
                   f"<td>{_e(_tactic_names(f['tactics']))}</td>"
                   f"<td style='text-align:left'>{_e(f['nest'])}</td></tr>")
    return ("<table class='cs-tbl'><tr><th>个股</th><th>天梯</th><th>主线</th><th>缠论阶段</th>"
            "<th>最近中枢</th><th>结构止损</th><th>命中战法</th><th>区间套</th></tr>"
            + ''.join(trs) + '</table>')


def render_chan_section_html(result: dict | None, link: str | None = None) -> str:
    """主报告里的缠论 section (精简版)。空/异常 → ''。"""
    if not result:
        return ''
    try:
        if link is None:
            try:
                from paths import SITE_URL
                link = f"{str(SITE_URL).rstrip('/')}/chan/latest.html"
            except Exception:
                link = './chan/latest.html'
        warns = ''.join(f"<div class='cs-warn'>⚠ {_e(w)}</div>" for w in result.get('warnings') or [])
        focus = result.get('focus') or []
        tbl = (f"<div class='cs-tbl-wrap'>{_focus_table(focus, 12)}</div>" if focus else '')
        bt = result.get('backtest')
        bt_html = (f"<div class='cs-h' style='margin-top:12px'>④ 战法历史表现 (板块指数因果回测)</div>"
                   f"<div class='cs-tbl-wrap'>{_bt_rows(bt, compact=True)}</div>"
                   f"<div class='cs-note'>{_e(_bt_note(bt))} 个股上的战法命中是外推, 不是该股自身的回测。</div>"
                   if bt else '')
        return (_CS_CSS + "<div class='chan-sec'>"
                f"<div class='cs-title'>🧩 缠论结构分析 · 笔 / 中枢 / 买卖点 / 背驰 "
                f"<span class=cs-note>({_e(result.get('report_date'))})</span></div>"
                f"<div class='cs-headline'>{_e(result.get('headline'))}</div>"
                "<div class='cs-grid'>"
                f"{_index_col(result.get('indexes') or [])}{_sector_col(result.get('sectors') or [])}"
                f"{_health_col(result.get('mainline_health') or [], focus)}</div>"
                f"{tbl}{bt_html}{warns}"
                f"<a class='cs-link' href='{_e(link)}'>完整缠论分析 (指数笔/中枢图 · 90 板块全表 · 战法库) →</a>"
                "</div>")
    except Exception:
        return ''


# ============================================================ 渲染: 独立子页 site/chan/

_PAGE_CSS = """
body{margin:0;background:#0d1117;color:#c9d1d9;font:14px/1.6 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif;}
.wrap{max-width:1280px;margin:0 auto;padding:16px;}
.top-nav{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;padding:10px 0;
  border-bottom:1px solid #30363d;margin-bottom:14px;}
.top-nav a{color:#58a6ff;text-decoration:none;font-size:13px;font-weight:600;margin-right:12px;}
h2{font-size:16px;color:#f0f6fc;margin:22px 0 8px;}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 16px;margin:12px 0;}
.chart-container{height:420px;width:100%;}
.tac{border-left:3px solid #8ab4f8;padding-left:10px;margin:10px 0;}
.tac b{color:#f0f6fc;}
.muted{color:#8b949e;font-size:12px;}
footer{color:#6e7681;font-size:12px;text-align:center;margin:30px 0 10px;}
"""


def _chart_js(div_id: str, title: str, p: dict) -> str:
    areas = [[{'xAxis': z['x0'], 'yAxis': z['zg']}, {'xAxis': z['x1'], 'yAxis': z['zd']}] for z in p.get('zs') or []]
    opt = {
        'backgroundColor': 'transparent',
        'title': {'text': title, 'left': 8, 'textStyle': {'fontSize': 13, 'color': '#c9d1d9'}},
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'cross'}},
        'grid': {'left': 60, 'right': 20, 'top': 36, 'bottom': 50},
        'xAxis': {'type': 'category', 'data': p['dates'], 'axisLabel': {'fontSize': 10}},
        'yAxis': {'scale': True, 'splitLine': {'lineStyle': {'color': '#21262d'}}},
        'dataZoom': [{'type': 'inside', 'start': 30}, {'type': 'slider', 'bottom': 6, 'height': 16, 'start': 30}],
        'series': [
            {'type': 'candlestick', 'name': 'K线', 'data': p['ohlc'],
             'itemStyle': {'color': '#e04b4b', 'color0': '#3fb950', 'borderColor': '#e04b4b', 'borderColor0': '#3fb950'},
             'markArea': {'silent': True, 'itemStyle': {'color': 'rgba(255,212,121,0.10)',
                                                        'borderColor': '#ffd479', 'borderWidth': 1},
                          'data': areas}},
            {'type': 'line', 'name': '笔', 'data': p['bi'], 'symbol': 'circle', 'symbolSize': 5,
             'lineStyle': {'color': '#8ab4f8', 'width': 1.6}, 'itemStyle': {'color': '#8ab4f8'}},
        ],
    }
    return (f"(function(){{var el=document.getElementById('{div_id}');if(!window.echarts||!el)return;"
            f"var c=echarts.init(el,'dark');c.setOption({json.dumps(opt, ensure_ascii=False)});"
            "window.addEventListener('resize',function(){c.resize();});})();")


def _sector_table(sectors: list[dict]) -> str:
    trs = []
    for s in sectors:
        stale = " <span class=muted>(滞后)</span>" if s.get('stale') else ''
        trs.append(f"<tr><td>{_e(s['name'])}{stale}</td><td>{_pct(s.get('ret20'))}</td>"
                   f"<td>{_pct(s.get('pct'), 0, False)}</td><td>{_stage_tag(s)}</td>"
                   f"<td>{_e(s.get('quadrant'))}</td><td>{_e(_zs_txt(s))}</td>"
                   f"<td>{_e(_tactic_names(s['tactics']))}</td></tr>")
    return ("<table class='cs-tbl'><tr><th>板块</th><th>20日涨幅</th><th>强度分位</th><th>缠论阶段</th>"
            "<th>象限</th><th>最近中枢</th><th>命中战法</th></tr>" + ''.join(trs) + '</table>')


def _tactic_lib(bt: dict | None) -> str:
    out = []
    for t in TACTICS:
        s = ((bt or {}).get('tactics') or {}).get(t.key) or {}
        verdict = f" <span class=muted>· {_e(s.get('verdict'))} (n={s.get('n', 0)})</span>" if s else ''
        out.append(f"<div class='tac'><b>{_e(t.event.name)}</b> <span class=muted>[{_e(t.event.operate)}]</span>{verdict}"
                   f"<div>逻辑: {_e(t.thesis)}</div><div>入场: {_e(t.entry)}</div>"
                   f"<div>止损: {_e(t.stop)}</div><div>仓位: {_e(t.position)}</div></div>")
    return ''.join(out)


_METHOD_HTML = """
<div class='card'><b>缠论计算链</b>
<div>原始K线 → <b>去包含</b> (向上取高高、向下取低低) → <b>分型</b> (顶/底, 强制交替) →
<b>笔</b> (顶底分型之间 ≥ 6 根无包含K线, 新K线破笔端点则回退重算) →
<b>中枢</b> (连续三笔重叠: ZG = 前三笔高点最小值, ZD = 前三笔低点最大值) → 买卖点 / 背驰。</div>
<div style='margin-top:8px'><b>信号 → 事件 → 战法</b> (借鉴 czsc 的三层设计): 信号只描述事实
(<code>日线_D1_中枢三买卖_三买_强_离开强于进入</code>), 事件是信号的 全部/任一/排除 组合,
战法 = 事件 + 入场/止损/仓位。改战法不碰信号, 信号可以单测, 回测就是逐日重放。</div>
<div style='margin-top:8px'><b>背驰两种口径</b>: 形态信号用笔的价差力度 (czsc 原版), MACD背驰 用柱子面积
(缠论原文)。两者同时满足最可靠; 只有价差背驰而 MACD 面积放大的, 在强势股里常是加速而非见顶。</div>
<div style='margin-top:8px' class='muted'>阶段判定按"风险优先"归并: 三卖 > 顶背驰 > 进行中背驰预警 > 三买 > 二买 >
回抽守ZG > 一买 > 中枢位置。内核逐函数对齐 waditu/czsc 的 Rust 实现, 纯 Python 无依赖, 逐根增量计算 (回测不穿越)。</div>
</div>
"""


def generate_chan_standalone_html(result: dict | None, is_site_mode: bool = True) -> str:
    """完整缠论分析子页。无结果返回 '' (publish 跳过归档, 首页不挂入口)。"""
    if not result:
        return ''
    from web_assets import echarts_head_html
    rd = result.get('report_date') or ''
    if is_site_mode:
        nav = [('../index.html', '🏠 首页'), (f'../reports/{rd}.html', '📊 主线追踪大报告'),
               ('../dashboards/latest.html', '📈 决策看板'), ('../dragon/latest.html', '🐉 龙头接替谱系')]
    else:
        nav = [('本地导航入口.html', '🏠 导航门户'), ('主线强度追踪.html', '📊 主线追踪大报告'),
               ('site/dashboards/latest.html', '📈 决策看板'), ('site/dragon/latest.html', '🐉 龙头接替谱系')]
    nav_html = ''.join(f"<a href='{_e(h)}'>{t}</a>" for h, t in nav)
    charts, scripts = [], []
    for i, ix in enumerate(result.get('indexes') or []):
        p = ix.get('chart') or {}
        if not p.get('dates'):
            continue
        cid = f'chanIdx{i}'
        charts.append(f"<div class='card'><div>{_stage_tag(ix)} <span class=muted>{_e(ix['nest'])}</span></div>"
                      f"<div class='muted'>最近真中枢 {_e(_zs_txt(ix))} · 日线 {ix['n_bi']} 笔 / {ix['n_zs']} 个真中枢"
                      f"{' · 结构止损 ' + format(ix['stop'], '.2f') if ix.get('stop') else ''}</div>"
                      f"<div class='chart-container' id='{cid}'></div></div>")
        scripts.append(_chart_js(cid, f"{ix['name']} · 日线笔与中枢 (黄框=中枢 ZD~ZG)", p))
    warns = ''.join(f"<div class='cs-warn'>⚠ {_e(w)}</div>" for w in result.get('warnings') or [])
    bt = result.get('backtest')
    focus = result.get('focus') or []
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="report-date" content="{_e(rd)}">
<title>缠论结构分析 · {_e(rd)}</title>
{echarts_head_html()}
<style>{_PAGE_CSS}</style>{_CS_CSS}
</head><body><div class="wrap">
<div class="top-nav"><div>{nav_html}</div><div class="muted">数据基准日: {_e(rd)}</div></div>
<div class="chan-sec"><div class="cs-title">🧩 缠论结构分析 · 指数区间套 × 板块结构矩阵 × 焦点个股</div>
<div class="cs-headline">{_e(result.get('headline'))}</div>{warns}</div>
<h2>① 指数: 日线笔 / 中枢</h2>{''.join(charts) or "<div class='muted'>指数K线不可用</div>"}
<h2>② 焦点个股结构 (主线天梯 S/B/C 前 {len(focus)} 只)</h2>
<div class="chan-sec"><div class="cs-tbl-wrap">{_focus_table(focus, None) if focus else '无焦点股数据'}</div></div>
<h2>③ 板块结构全表 ({len(result.get('sectors') or [])} 个同花顺行业指数)</h2>
<div class="chan-sec"><div class="cs-tbl-wrap">{_sector_table(result.get('sectors') or [])}</div>
<div class="cs-note">板块指数缓存没有开盘价, 开盘价用前收合成; 不影响笔/中枢 (只用 high/low), 只影响 K 线实体颜色。</div></div>
<h2>④ 战法库与历史表现</h2>
<div class="chan-sec"><div class="cs-tbl-wrap">{_bt_rows(bt, compact=False)}</div>
<div class="cs-note">{_e(_bt_note(bt))}</div></div>
<div class="card">{_tactic_lib(bt)}</div>
<h2>⑤ 方法</h2>{_METHOD_HTML}
<footer>主线强度追踪系统 · 缠论结构模块 (chan_engine / chan_signals / chan_tactics) · 仅供研究参考, 不构成投资建议</footer>
</div><script>{''.join(scripts)}</script></body></html>
"""


def export_chan_standalone_reports(result: dict | None, output_dir: str) -> str | None:
    """本地 output/ 写一份最新子页 (站点副本由 publish 归档到 site/chan/)。返回路径或 None。"""
    html = generate_chan_standalone_html(result, is_site_mode=False)
    if not html:
        return None
    path = os.path.join(output_dir, '缠论结构分析_最新.html')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html)
        return path
    except OSError:
        return None
