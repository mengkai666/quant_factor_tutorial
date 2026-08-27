# -*- coding: utf-8 -*-
"""价格缓存深度回补 (腾讯区间 K 线, 全市场分钟级完成)。

为什么再加一个回补工具:
    `backfill_price_gap.py` 走 baostock 多进程逐股抓, 是为"中段缺几天"设计的,
    对上百个交易日的深度回补太慢, 且有串号护栏开销 (见 baostock-batch-code-mislabel)。
    腾讯 `fqkline/get` 一次请求就能返回一只股票**整个区间**的 raw+qfq 日线
    (1000 根上限), 于是深度回补的请求数只与股票数有关, 与天数无关 ——
    实测 55 股/s (16 线程), 全市场 5538 只 ~100s 拿回 67 个交易日。

为什么需要深度:
    `phase_resonance.LOOKBACK_DAYS = 90` —— 阶段识别回看 90 个交易日, 下跌段起点
    最远可落在窗口左端。价格缓存浅于此, `_phase_returns` 就出不来 `下跌段` 列,
    四象限个股代表恒为空 (见 sentiment-ad-reconcile 点 11)。默认补到 105 天留余量。

安全设计 (顺序即优先级):
    1. 重叠日抽样对账: 先抓已有日期的样本, 与缓存现值比 close_raw, 偏差超阈值即中止
       —— 换源前先证明新源与旧源同口径, 不然补进来的是另一套价格。
    2. 逐日宽度体检: 新增日 close_raw 非空数须 >= --min-breadth, 不达标的日期整天丢弃
       —— 残缺日进了缓存, A/D 对账就会拿它算涨跌家数 (见 sentiment-ad-reconcile 点 10)。
    3. 默认干跑, --apply 才落库, 落库前备份原文件。

三条源 (--source):
    tencent  —— 默认, 最快 (55 股/s), 但 `fqkline/get` 有 WAF: 高并发跑一阵后整个
                出口 IP 被拉黑, 返回 501 + 跳 waf.tencent.com/501page.html。此时
                qt.gtimg.cn 行情接口仍通, 别误判成"全网断了"。
    baostock —— WAF 期间的备用源, 多进程逐股抓 (实测 6 进程 12 股/s, 全市场 68 天
                ~7 分钟)。raw/qfq 要分两次查 (adjustflag 3 / 2), 带串号护栏。
                东财 push2his 直连被 RemoteDisconnected、akshare 走代理不可用,
                都试过。抓多了会被判"黑名单用户"(10001011), 冷却几小时才恢复。
    sina     —— 腾讯 WAF + baostock 黑名单同时封死时的第三条源, 只给不复权收盘
                (raw, 与缓存 close_raw 中位偏差 0.0000%), 覆盖北交所/科创/B 股。
                自带 8 股/s 限速: 快了会 HTTP 456 (返 HTML 不返 JSON), 全市场 ~12 分钟。

用法:
    python tools/backfill_price_history.py                  # 干跑, 看要补哪些天
    python tools/backfill_price_history.py --apply          # 落库
    python tools/backfill_price_history.py --depth 150 --apply
    python tools/backfill_price_history.py --source baostock --workers 6 --apply
    python tools/backfill_price_history.py --source baostock --workers 6         --repair-days 2026-08-24 --apply        # 修被盘中快照污染的某一天
"""
from __future__ import annotations

import argparse
import os
import json
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pandas as pd
import requests

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'src'))

from paths import PRICE_CACHE, SECURITY_MASTER_CACHE  # noqa: E402
from data_sources.calendar_provider import CalendarProvider  # noqa: E402
from data_sources.price_provider import PriceProvider  # noqa: E402

CANONICAL = ['code', 'date', 'close_raw', 'close_qfq', 'close_legacy',
             'price_basis', 'source', 'source_timestamp']


def _load_cache() -> pd.DataFrame:
    if not os.path.exists(PRICE_CACHE):
        return pd.DataFrame(columns=CANONICAL)
    return pd.read_csv(PRICE_CACHE, dtype={'code': str}, low_memory=False)


def _universe() -> list[str]:
    df = pd.read_csv(SECURITY_MASTER_CACHE, dtype={'code': str})
    return sorted(df['code'].dropna().astype(str).str.strip().unique())


def _fetch(codes: list[str], dates: list[str], workers: int) -> pd.DataFrame:
    t0 = time.time()
    result = PriceProvider(max_workers=workers, retry=2, retry_delay=0.3).fetch_range(
        pd.DataFrame({'code': codes}), dates)
    elapsed = max(time.time() - t0, 0.1)
    print(f'  ⏱️ {result.status.name} 覆盖 {result.actual_count}/{result.expected_count}, '
          f'{elapsed:.1f}s ({len(codes) / elapsed:.1f} 股/s)')
    data = result.data
    if data is None or data.empty:
        return pd.DataFrame(columns=CANONICAL)
    out = data[data['close_raw'].notna()].copy()
    out['close_legacy'] = pd.NA
    out['price_basis'] = out['close_qfq'].notna().map({True: 'raw+qfq', False: 'raw'})
    out['source'] = out['source_raw'].replace('', 'tencent')
    out['source_timestamp'] = out['date'].astype(str).str.replace('-', '', regex=False)
    return out[CANONICAL]


# ---------------- baostock 深度回补路径 ----------------
# 腾讯 fqkline 被 WAF 拦 (501 → waf.tencent.com) 时的备用源。baostock 逐股一次
# 请求也能拿整个区间, 但 raw 与 qfq 要分两次 (adjustflag 3 / 2), 且库本身不是
# 线程安全的, 只能多进程。串号护栏见 baostock-batch-code-mislabel。
BS_CHUNK_TIMEOUT = 300


def _fetch_bs_chunk(job):
    """抓一批股票在 [start, end] 的 raw + qfq 收盘价。返回 list[dict]。"""
    codes, start, end = job
    import socket
    socket.setdefaulttimeout(20)
    # pyrefly: ignore [missing-import]
    import baostock as bs

    def _quiet(fn):
        saved = sys.stdout
        sys.stdout = open(os.devnull, 'w')
        try:
            return fn()
        finally:
            sys.stdout.close()
            sys.stdout = saved

    _quiet(bs.login)
    rows = []
    try:
        for code in codes:
            if not (code.startswith('sh') or code.startswith('sz')):
                continue  # baostock 不收录北交所
            bs_code = code[:2] + '.' + code[2:]
            closes = {}
            for flag, column in (('3', 'close_raw'), ('2', 'close_qfq')):
                try:
                    rs = bs.query_history_k_data_plus(
                        bs_code, 'date,code,close', start_date=start, end_date=end,
                        frequency='d', adjustflag=flag)
                except Exception:
                    continue
                if not rs or rs.error_code != '0':
                    continue
                try:
                    while rs.next():
                        row = rs.get_row_data()
                        # 串号护栏: query(A) 偶发返回 B 的 K 线, 用返回行裸码校验,
                        # 不符即丢弃 —— 缺这只好过把 B 的价格写成 A 的。
                        returned = row[1].replace('.', '') if row[1] else ''
                        if returned and returned != code:
                            continue
                        try:
                            value = float(row[2])
                        except (TypeError, ValueError):
                            continue
                        closes.setdefault(row[0], {})[column] = value
                except Exception:
                    continue
            for date, values in closes.items():
                if 'close_raw' not in values:
                    continue  # raw 是 A/D 真源基准, 缺 raw 的行不要
                rows.append({'code': code, 'date': date,
                             'close_raw': values['close_raw'],
                             'close_qfq': values.get('close_qfq')})
    finally:
        try:
            _quiet(bs.logout)
        except Exception:
            pass
    return rows


def _fetch_baostock(codes, dates, workers):
    """多进程 baostock 区间抓取, 输出与 _fetch 同构的 CANONICAL 表。"""
    import multiprocessing
    start, end = min(dates), max(dates)
    keep = set(str(d) for d in dates)
    chunk_size = 150
    chunks = [(codes[i:i + chunk_size], start, end)
              for i in range(0, len(codes), chunk_size)]
    cores = max(1, workers)
    print(f'  🚀 baostock {cores} 进程 × {len(chunks)} 块, 区间 [{start}, {end}]')
    t0 = time.time()
    rows = []
    pool = multiprocessing.Pool(cores)
    try:
        it = pool.imap_unordered(_fetch_bs_chunk, chunks)
        for i in range(len(chunks)):
            try:
                rows.extend(it.next(timeout=BS_CHUNK_TIMEOUT))
            except multiprocessing.TimeoutError:
                print(f'  ⚠️ 第 {i + 1} 块超时放弃 (剩余块继续)')
                break
            except Exception as exc:
                print(f'  ⚠️ 第 {i + 1} 块失败: {type(exc).__name__} {str(exc)[:80]}')
                continue
            if (i + 1) % 5 == 0 or i + 1 == len(chunks):
                el = max(time.time() - t0, 0.1)
                done = min((i + 1) * chunk_size, len(codes))
                print(f'     {i + 1}/{len(chunks)} 块, {len(rows)} 行, '
                      f'{el:.0f}s ({done / el:.1f} 股/s)', flush=True)
    finally:
        pool.terminate()
        pool.join()
    if not rows:
        return pd.DataFrame(columns=CANONICAL)
    out = pd.DataFrame(rows)
    out['date'] = out['date'].astype(str)
    out = out[out['date'].isin(keep)]
    out = out[out['close_raw'].notna()].copy()
    if out.empty:
        return pd.DataFrame(columns=CANONICAL)
    out['close_legacy'] = pd.NA
    out['price_basis'] = out['close_qfq'].notna().map({True: 'raw+qfq', False: 'raw'})
    out['source'] = 'baostock'
    out['source_timestamp'] = out['date'].str.replace('-', '', regex=False)
    el = max(time.time() - t0, 0.1)
    print(f'  ⏱️ baostock 完成 {len(out)} 行 / {out["date"].nunique()} 天, '
          f'{el:.0f}s ({len(codes) / el:.1f} 股/s)')
    return out[CANONICAL]


# ---------------- 新浪深度回补路径 ----------------
# 腾讯 WAF (501) 与 baostock 黑名单 (10001011) 同时封死时的第三条源。实测:
#   * `close_raw` 与缓存现值中位偏差 0.0000% —— 同口径, 可直接混进 raw 列;
#   * 覆盖北交所/科创/创业/B 股 (东财 push2his 直连 RemoteDisconnected, 走代理
#     ProxyError, akshare 同源同废, 都试过);
#   * 只给不复权收盘, 没有 qfq —— 而 A/D 真源基准正是 raw (见 sentiment-ad-reconcile
#     点 9), 所以够用; 要 qfq 仍得等腾讯。
# 接口只支持"要最近 N 根", 不支持起止日期, 故 datalen 要从目标最早日反推。
SINA_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
           '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
SINA_URL = ('https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
            'CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen={n}')
# 返回的是 JS 字面量 (键不带引号), 不是标准 JSON, 补上引号再 json.loads。
_SINA_BARE_KEY = re.compile(r'(\w+):')
# 新浪按出口 IP 限流, 超了返回 **HTTP 456 + 一段 HTML** (不是 JSON, 也不是常见的
# 429/403)。实测 12 线程 ~77 股/s 跑到第 ~440 只就被切, 之后连 datalen=45 的单发
# 都 456; 降到 8 股/s 全市场跑通。限流只给 HTML 不报错, 不识别就会静默变成"大半个
# 市场没数据" → 被 --min-breadth 整天丢弃; 兜得住是好事, 但真因必须打出来。
SINA_MAX_RPS = 8.0
SINA_RETRY = 3


class _RateLimiter:
    """跨线程最小请求间隔 (只保证平均速率不超, 不做突发额度)。"""

    def __init__(self, rps: float):
        self._gap = 1.0 / max(rps, 0.1)
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            due = max(time.time(), self._next)
            self._next = due + self._gap
        delay = due - time.time()
        if delay > 0:
            time.sleep(delay)


def _fetch_sina_one(session, code: str, datalen: int, keep: set,
                    limiter=None, stats=None) -> list[dict]:
    payload = None
    for attempt in range(SINA_RETRY):
        if limiter is not None:
            limiter.wait()
        try:
            resp = session.get(SINA_URL.format(code=code, n=datalen),
                               headers={'User-Agent': SINA_UA}, timeout=20)
        except Exception:
            time.sleep(0.5 * (attempt + 1))
            continue
        if resp.status_code == 456:  # 限流, 退避重试
            if stats is not None:
                stats['throttled'] = stats.get('throttled', 0) + 1
            time.sleep(1.5 * (attempt + 1))
            continue
        text = (resp.text or '').strip()
        if not text or text == 'null':
            return []  # 该代码新浪没数据 (退市/未上市), 不是限流, 别重试
        try:
            payload = json.loads(_SINA_BARE_KEY.sub(r'"\1":', text))
            break
        except Exception:
            if stats is not None:
                stats['unparsed'] = stats.get('unparsed', 0) + 1
            time.sleep(0.8 * (attempt + 1))
    if not isinstance(payload, list):
        return []
    rows = []
    for bar in payload:
        try:
            date = str(bar['day'])[:10]
            close = float(bar['close'])
        except (KeyError, TypeError, ValueError):
            continue
        if date not in keep or close <= 0:
            continue
        rows.append({'code': code, 'date': date, 'close_raw': close,
                     'close_qfq': pd.NA, 'close_legacy': pd.NA,
                     'price_basis': 'raw', 'source': 'sina',
                     'source_timestamp': date.replace('-', '')})
    return rows


def _fetch_sina(codes: list[str], dates: list[str], workers: int) -> pd.DataFrame:
    keep = {str(d)[:10] for d in dates}
    # 接口只能"要最近 N 根", N 得盖住目标最早日 → 今天; 多要 5 根留余量。
    earliest = min(keep)
    try:
        calendar = CalendarProvider().trading_days(
            earliest, datetime.now().strftime('%Y-%m-%d'))
        datalen = len(calendar) + 5
    except Exception:
        datalen = 260
    datalen = max(5, min(datalen, 1023))
    limiter = _RateLimiter(SINA_MAX_RPS)
    stats: dict = {}
    print(f'  🌐 新浪 getKLineData: {len(codes)} 只 × datalen={datalen} '
          f'(盖住 {earliest} 起), {workers} 线程, 限速 {SINA_MAX_RPS:.0f} 股/s')
    t0 = time.time()
    rows = []
    session = requests.Session()
    session.trust_env = False  # 腾讯/东财要走代理, 新浪直连即可, 别被代理拖慢
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for got in pool.map(
                lambda c: _fetch_sina_one(session, c, datalen, keep, limiter, stats),
                codes):
            rows.extend(got)
    elapsed = max(time.time() - t0, 0.1)
    out = pd.DataFrame(rows, columns=CANONICAL)
    days = out['date'].nunique() if len(out) else 0
    covered = out['code'].nunique() if len(out) else 0
    print(f'  ⏱️ 新浪完成 {len(out)} 行 / {days} 天 / {covered} 只, '
          f'{elapsed:.0f}s ({len(codes) / elapsed:.1f} 股/s)')
    if stats.get('throttled') or stats.get('unparsed'):
        print(f'  ⚠️ 被限流 (HTTP 456) {stats.get("throttled", 0)} 次, 响应无法解析 '
              f'{stats.get("unparsed", 0)} 次 —— 覆盖 {covered}/{len(codes)} 只。缺口大'
              f'就等几分钟 --only-missing 续抓, 或把 SINA_MAX_RPS 调低。')
    return out


def _dispatch(source, codes, dates, workers):
    if source == 'sina':
        return _fetch_sina(codes, dates, workers)
    if source == 'baostock':
        return _fetch_baostock(codes, dates, workers)
    return _fetch(codes, dates, workers)


def _sample_check(cache: pd.DataFrame, codes: list[str], size: int, workers: int,
                  source: str = 'tencent') -> bool:
    """重叠日对账: 新源与缓存现值必须同口径, 否则中止。

    逐日算偏差, 而不是把所有重叠日混在一起 —— 缓存里个别日期本身就可能是盘中/
    陈旧快照 (实测 2026-08-24 有 81% 的股票与收盘价差 >0.5%, 邻近各日 0%)。混算会
    让这一天的污染冒充"换源口径不一致", 把好端端的回补拦掉。

    判据用**逐日中位偏差**, 不用坏行占比 (2026-08-27 改)。原先"单日坏行 >20% 才算
    该日可疑, 否则坏行计入判决"漏掉了**局限在某个板块的污染**: 2026-08-24 的陈旧行
    只集中在北交所 (bj9xxxxx, 全市场约 6%), 抽样里占 12.5% —— 够不上 20% 的可疑线,
    却远超 1% 的口径容忍线, 于是合法回补被判"不同口径"拦死。
    真正的换源口径不一致会**整市场系统性偏移且每一天都偏**, 缓存污染则只偏某一天/
    某一段。故: 中位偏差 >0.5% 的天记为"口径偏移", 只有**所有重叠日都偏移**才中止;
    某天大盘对得上却有零散坏行, 那是该日缓存脏, 报出来让人用 --repair-days 修。
    """
    have = sorted(cache.loc[cache['close_raw'].notna(), 'date'].astype(str).unique())
    if len(have) < 3 or size <= 0:
        print('  ⏭️ 缓存无可比重叠日, 跳过抽样对账')
        return True
    dates = have[-5:]
    pool = sorted(set(cache.loc[cache['date'] == dates[-1], 'code'].astype(str)) & set(codes))
    step = max(1, len(pool) // size)
    sample = pool[::step][:size]
    print(f'  🔍 重叠日对账: {len(sample)} 只 × {dates}')
    fetched = _dispatch(source, sample, dates, workers)
    if fetched.empty:
        print('  ❌ 抽样未取到数据, 中止 (网络或接口变更)')
        return False
    merged = cache.merge(fetched[['code', 'date', 'close_raw']].rename(
        columns={'close_raw': 'probe'}), on=['code', 'date'], how='inner')
    merged = merged[merged['close_raw'].notna()].copy()
    if merged.empty:
        print('  ❌ 抽样与缓存无交集, 中止')
        return False
    base = merged['close_raw'].astype(float).abs().replace(0, float('nan'))
    merged['dev'] = ((merged['close_raw'].astype(float)
                      - merged['probe'].astype(float)).abs() / base)
    merged = merged[merged['dev'].notna()].copy()
    if merged.empty:
        print('  ❌ 抽样无可比价格, 中止')
        return False
    merged['bad'] = merged['dev'] > 0.005
    stat = merged.groupby('date').agg(n=('bad', 'size'), bad=('bad', 'sum'),
                                      med=('dev', 'median'))
    stat['pct'] = (stat['bad'] / stat['n'] * 100).round(1)
    shifted, dirty = [], []
    for date, row in stat.iterrows():
        if row['med'] > 0.005:
            mark = '❌ 整体偏移 (疑换源口径)'
            shifted.append(date)
        elif row['pct'] > 1:
            mark = '⚠️ 大盘对得上, 零散坏行 → 缓存该日脏'
            dirty.append(date)
        else:
            mark = 'ok'
        print(f'     {date}: {int(row["bad"])}/{int(row["n"])} 偏差>0.5% '
              f'({row["pct"]}%), 中位偏差 {row["med"] * 100:.3f}% {mark}')
    if len(shifted) == len(stat):
        cols = ['code', 'date', 'close_raw', 'probe']
        print(merged[merged['bad']][cols].head(10).to_string(index=False))
        print('  ❌ 每个重叠日大盘都整体偏移 = 新源与缓存不同口径, '
              '中止 (别把两套价格混进同一列)')
        return False
    if shifted:
        print(f'  ⚠️ {len(shifted)} 天整体偏移但其余日对得上 → 是这些天的缓存被'
              f'整片污染, 不是换源: {shifted}')
    if dirty:
        print(f'  ⚠️ {len(dirty)} 天缓存有零散坏行 (盘中/陈旧快照, 可局限在某板块): '
              f'{dirty}')
    if shifted or dirty:
        print('     修它们: --repair-days ' + ','.join(str(d) for d in shifted + dirty))
    ok_days = stat[stat['med'] <= 0.005]
    print(f'  ✅ 大盘同口径的重叠日 {len(ok_days)}/{len(stat)} 天, '
          f'中位偏差 {stat["med"].median() * 100:.3f}%')
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description='腾讯区间 K 线深度回补价格缓存')
    ap.add_argument('--depth', type=int, default=105, help='目标覆盖交易日数 (默认 105)')
    ap.add_argument('--start', help='起始交易日 YYYY-MM-DD (给了就忽略 --depth)')
    ap.add_argument('--workers', type=int, default=16,
                    help='腾讯路径=线程数(默认16); baostock 路径=进程数(建议 4~6)')
    ap.add_argument('--source', choices=['tencent', 'baostock', 'sina'],
                    default='tencent',
                    help='数据源。腾讯被 WAF 拦 (501) 时用 baostock')
    ap.add_argument('--sample-check', type=int, default=200, help='重叠日对账抽样股数, 0=跳过')
    ap.add_argument('--min-breadth', type=int, default=4000, help='单日 close_raw 非空数下限')
    ap.add_argument('--repair-days', default='',
                    help='额外重抓并覆盖这些已有日期 YYYY-MM-DD 逗号分隔 '
                         '(修盘中/陈旧快照污染; 抽样对账标出的可疑日就填这里)')
    ap.add_argument('--only-missing', action='store_true',
                    help='只抓在目标日仍缺覆盖的代码 (增量补漏: baostock 抓多了会被限流成"黑名单用户", 首轮总会漏几百只, 冷却后用它续上, 不必重抓全市场)')
    ap.add_argument('--apply', action='store_true', help='落库 (默认干跑)')
    args = ap.parse_args()

    cache = _load_cache()
    have = set(cache['date'].astype(str).unique())
    cal = CalendarProvider()
    end = max(have) if have else datetime.now().strftime('%Y-%m-%d')
    window = cal.trading_days('2025-01-01', end)
    target = [d for d in window if d >= args.start] if args.start else window[-args.depth:]
    missing = [d for d in target if d not in have]
    repair = sorted({d.strip() for d in args.repair_days.split(',') if d.strip()})
    print(f'📂 缓存: {len(cache)} 行, {len(have)} 个交易日'
          + (f', {min(have)} ~ {max(have)}' if have else ''))
    print(f'🎯 目标: {len(target)} 天 {target[0]} ~ {target[-1]}; 缺 {len(missing)} 天')
    if repair:
        print(f'🔧 另需重抓覆盖 {len(repair)} 天: {repair}')
    if not missing and not repair:
        print('✅ 目标深度已覆盖, 无需回补')
        return 0
    if missing:
        print(f'   待补: {missing[0]} ~ {missing[-1]}')
    wanted = sorted(set(missing) | set(repair))

    codes = _universe()
    print(f'🌐 全市场 {len(codes)} 只 (security_master)')
    if args.only_missing and missing:
        covered = cache[cache['close_raw'].notna() & cache['date'].astype(str).isin(missing)]
        per_code = covered.groupby('code')['date'].nunique()
        full = set(per_code[per_code >= len(missing)].index.astype(str))
        codes = [c for c in codes if c not in full]
        print(f'  ♻️ 增量模式: 目标日仍缺覆盖的 {len(codes)} 只')
        if not codes:
            print('✅ 目标日已全覆盖, 无需回补')
            return 0
    if not _sample_check(cache, codes, args.sample_check, args.workers, args.source):
        return 1

    print(f'🚀 [{args.source}] 抓取 {len(wanted)} 个交易日 (每股按区间请求)...')
    fetched = _dispatch(args.source, codes, wanted, args.workers)
    if fetched.empty:
        print('❌ 未取到任何数据, 缓存未改动')
        return 1

    breadth = fetched.groupby('date')['close_raw'].count().sort_index()
    thin = breadth[breadth < args.min_breadth]
    if len(thin):
        print(f'  ⚠️ {len(thin)} 天宽度不足 (<{args.min_breadth}), 整天丢弃:')
        for d, n in thin.items():
            print(f'     {d}: {n}')
        fetched = fetched[~fetched['date'].isin(thin.index)]
    kept = breadth[breadth >= args.min_breadth]
    print(f'  ✅ 采用 {len(kept)} 天, 每日 close_raw 非空 {kept.min()} ~ {kept.max()}')
    if fetched.empty:
        print('❌ 全部日期宽度不足, 缓存未改动')
        return 1

    if not args.apply:
        print('ℹ️ 干跑模式, 未写入。加 --apply 落库。')
        return 0

    backup = f'{PRICE_CACHE}.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    shutil.copy2(PRICE_CACHE, backup) if os.path.exists(PRICE_CACHE) else None
    base = cache.reindex(columns=CANONICAL)
    if repair:
        # 覆盖语义按 (code, date) 单元, 不按整天: 只删"这次真抓到替代值"的那些行
        # (下面 keep='first' 让先出现的行胜出, 旧行不删就永远压着新值)。整天删过一次
        # 教训惨痛 —— 新源漏抓的代码 (baostock 限流漏了 929 只) 会连旧值一起消失,
        # 而下一交易日的逐股涨跌要拿这天的 close 当基准, 一天缺股连累两天 A/D。
        touched = fetched.loc[fetched['date'].astype(str).isin(repair), ['code', 'date']]
        if len(touched):
            wipe = pd.MultiIndex.from_arrays(
                [base['code'].astype(str), base['date'].astype(str)]
            ).isin(pd.MultiIndex.from_arrays(
                [touched['code'].astype(str), touched['date'].astype(str)]))
            print(f'  🔧 覆盖模式: 替换 {int(wipe.sum())} 行旧数据 ({len(repair)} 天), '
                  f'新源未覆盖的代码保留原值')
            base = base[~wipe]
    merged = pd.concat([base, fetched], ignore_index=True)
    merged = merged.drop_duplicates(['code', 'date'], keep='first').sort_values(['date', 'code'])
    tmp = f'{PRICE_CACHE}.tmp'
    merged.to_csv(tmp, index=False, encoding='utf-8')
    os.replace(tmp, PRICE_CACHE)
    days = sorted(merged['date'].astype(str).unique())
    print(f'💾 已备份 → {os.path.basename(backup)}')
    print(f'✅ 落库: {len(merged)} 行, {len(days)} 个交易日 {days[0]} ~ {days[-1]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
