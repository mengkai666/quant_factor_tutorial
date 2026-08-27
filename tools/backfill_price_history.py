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

两条源 (--source):
    tencent  —— 默认, 最快 (55 股/s), 但 `fqkline/get` 有 WAF: 高并发跑一阵后整个
                出口 IP 被拉黑, 返回 501 + 跳 waf.tencent.com/501page.html。此时
                qt.gtimg.cn 行情接口仍通, 别误判成"全网断了"。
    baostock —— WAF 期间的备用源, 多进程逐股抓 (实测 6 进程 12 股/s, 全市场 68 天
                ~7 分钟)。raw/qfq 要分两次查 (adjustflag 3 / 2), 带串号护栏。
                东财 push2his 直连被 RemoteDisconnected、akshare 走代理不可用,
                都试过, 只有 baostock 稳。

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
import shutil
import sys
import time
from datetime import datetime

import pandas as pd

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


def _dispatch(source, codes, dates, workers):
    if source == 'baostock':
        return _fetch_baostock(codes, dates, workers)
    return _fetch(codes, dates, workers)


def _sample_check(cache: pd.DataFrame, codes: list[str], size: int, workers: int,
                  source: str = 'tencent') -> bool:
    """重叠日对账: 新源与缓存现值必须同口径, 否则中止。

    逐日算偏差率, 而不是把所有重叠日混在一起 —— 缓存里个别日期本身就可能是盘中/
    陈旧快照 (实测 2026-08-24 有 81% 的股票与收盘价差 >0.5%, 邻近各日 0%)。混算会
    让这一天的污染冒充"换源口径不一致", 把好端端的回补拦掉。判据: 单日坏行占比
    >20% 记为"缓存该日可疑", 只报告不参与判决; 干净日仍有超阈值的坏行才中止。
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
    base = merged['close_raw'].astype(float).abs()
    merged['bad'] = (merged['close_raw'].astype(float)
                     - merged['probe'].astype(float)).abs() > base * 0.005
    stat = merged.groupby('date')['bad'].agg(n='size', bad='sum')
    stat['pct'] = (stat['bad'] / stat['n'] * 100).round(1)
    for date, row in stat.iterrows():
        mark = '⚠️ 缓存该日可疑' if row['pct'] > 20 else 'ok'
        print(f'     {date}: {int(row["bad"])}/{int(row["n"])} 偏差>0.5% ({row["pct"]}%) {mark}')
    suspect = stat[stat['pct'] > 20].index.tolist()
    clean = merged[~merged['date'].isin(suspect)]
    if suspect:
        print(f'  ⚠️ {len(suspect)} 天缓存现值与收盘价不符 (盘中/陈旧快照): {suspect}')
        print('     这些天不参与换源判决; 修它们用 --repair-days')
    if clean.empty:
        print('  ❌ 所有重叠日都可疑, 无法证明同口径, 中止')
        return False
    bad_rows = int(clean['bad'].sum())
    print(f'  ✅ 干净重叠日 {clean["date"].nunique()} 天 / {len(clean)} 行, 偏差>0.5% 的 {bad_rows} 行')
    if bad_rows > max(1, len(clean) // 100):
        cols = ['code', 'date', 'close_raw', 'probe']
        print(clean[clean['bad']][cols].head(10).to_string(index=False))
        print('  ❌ 新源与缓存不同口径, 中止 (别把两套价格混进同一列)')
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description='腾讯区间 K 线深度回补价格缓存')
    ap.add_argument('--depth', type=int, default=105, help='目标覆盖交易日数 (默认 105)')
    ap.add_argument('--start', help='起始交易日 YYYY-MM-DD (给了就忽略 --depth)')
    ap.add_argument('--workers', type=int, default=16,
                    help='腾讯路径=线程数(默认16); baostock 路径=进程数(建议 4~6)')
    ap.add_argument('--source', choices=['tencent', 'baostock'], default='tencent',
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
