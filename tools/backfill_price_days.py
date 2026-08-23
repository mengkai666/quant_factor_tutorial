"""按交易日回补 price_history_cache 的 raw+qfq 收盘价 (一次性运维脚本)。

与 tools/backfill_price_gap.py 的区别: 后者只抓 baostock 前复权 (close_qfq),
回补后 close_raw 仍缺, 报告日/前一交易日的 A/D 口径 (_price_pair_coverage)
依然不达标。本脚本直接复用主程序的 _fetch_bs_chunk (同时抓 adjustflag 3/2,
带 row[1] 裸码串号护栏), 因此两个口径一次补齐。

主程序 update_price_cache 的 baostock 段有 300s 全局硬超时, 缓存落后 2 个
交易日 (5500 只 x 2 口径) 必然超时降级; 本脚本无全局上限、进程数可调, 先把
缺口补平, 再跑日报即可直接命中"缓存已最新"快路径。

用法:
    python tools/backfill_price_days.py --dates 2026-08-18,2026-08-19            # 干跑
    python tools/backfill_price_days.py --dates 2026-08-18,2026-08-19 --apply    # 落库

北交所 (bj) 不在 baostock 覆盖内, 留给主程序的 _fill_price_gaps_with_provider 补。
"""
import argparse
import importlib
import multiprocessing
import os
import shutil
import sys
import time
from datetime import datetime

import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from paths import PRICE_CACHE, SECURITY_MASTER_CACHE  # noqa: E402
from data_sources.price_provider import normalize_price_frame, merge_price_frames  # noqa: E402

CHUNK_TIMEOUT = 300


def _load_codes(price_df):
    """全市场代码: 证券主表优先 (与主程序 update_price_cache 同源)。"""
    if os.path.exists(SECURITY_MASTER_CACHE):
        try:
            sm = pd.read_csv(SECURITY_MASTER_CACHE, dtype=str)
            codes = sm['code'].dropna().unique().tolist()
            if codes:
                return codes
        except Exception as exc:
            print(f'  ⚠️ 证券主表读取失败, 回退价格缓存代码表: {exc}')
    return price_df['code'].unique().tolist()


def _coverage_report(frame, dates):
    df = normalize_price_frame(frame)
    for d in dates:
        sub = df[df['date'] == d]
        raw = int(sub['close_raw'].notna().sum()) if 'close_raw' in sub.columns else 0
        qfq = int(sub['close_qfq'].notna().sum()) if 'close_qfq' in sub.columns else 0
        print(f'    {d}: rows={len(sub)} close_raw={raw} close_qfq={qfq}')


def _ad_report(frame, dates):
    """用 close_raw 逐股比前一交易日算 A/D (与 limit_ratio_factor 同判据 ±0.1%)。"""
    df = normalize_price_frame(frame)
    df = df[df['close_raw'].notna()].sort_values(['code', 'date'])
    df['prev'] = df.groupby('code')['close_raw'].shift(1)
    df['chg'] = (df['close_raw'] / df['prev'] - 1) * 100
    for d in dates:
        sub = df[(df['date'] == d) & df['chg'].notna()]
        if sub.empty:
            print(f'    {d}: 无可比数据')
            continue
        up = int((sub['chg'] > 0.1).sum())
        dn = int((sub['chg'] < -0.1).sum())
        print(f'    {d}: up={up} down={dn} flat={len(sub) - up - dn} (合计 {len(sub)}) '
              f'涨停约={int((sub["chg"] > 9.8).sum())} 跌停约={int((sub["chg"] < -9.8).sum())}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dates', required=True, help='目标交易日, YYYY-MM-DD 逗号分隔')
    ap.add_argument('--apply', action='store_true', help='落库 (默认干跑)')
    ap.add_argument('--cores', type=int, default=0, help='进程数 (默认 min(8, cpu-1))')
    ap.add_argument('--only-missing', action='store_true', help='只抓目标日 raw 或 qfq 尚缺的代码')
    args = ap.parse_args()

    dates = sorted(d.strip() for d in args.dates.split(',') if d.strip())
    if not dates:
        sys.exit('  ❌ --dates 为空')

    price_df = normalize_price_frame(pd.read_csv(PRICE_CACHE, dtype={'code': str, 'date': str}))
    print(f'  📂 现有缓存 {len(price_df)} 行, 日期 {price_df["date"].min()} ~ {price_df["date"].max()}')
    print('  🔍 目标日现状:')
    _coverage_report(price_df, dates)

    codes = [c for c in _load_codes(price_df) if str(c).startswith(('sh', 'sz'))]
    if args.only_missing:
        need = set()
        for d in dates:
            sub = price_df[price_df['date'] == d]
            have_raw = set(sub.loc[sub['close_raw'].notna(), 'code'])
            have_qfq = set(sub.loc[sub['close_qfq'].notna(), 'code'])
            need |= (set(codes) - have_raw) | (set(codes) - have_qfq)
        codes = sorted(need)
        print(f'  ♻️ 增量模式: 待补 {len(codes)} 只')
        if not codes:
            print('  ✅ 目标日 raw/qfq 已全覆盖')
            return

    print(f'  🎯 待抓 {len(codes)} 只 (沪深), 区间 [{dates[0]}, {dates[-1]}]')
    if not args.apply:
        print('  ℹ️ 干跑模式, 未抓取。加 --apply 执行。')
        return

    tracker = importlib.import_module('主线强度追踪')
    fetch_chunk = tracker._fetch_bs_chunk

    bak = f'{PRICE_CACHE}.bak.{datetime.now():%Y%m%d_%H%M%S}'
    shutil.copy2(PRICE_CACHE, bak)
    print(f'  💾 已备份 → {os.path.basename(bak)}')

    cores = args.cores or max(1, min(8, multiprocessing.cpu_count() - 1))
    chunks = [codes[i:i + 200] for i in range(0, len(codes), 200)]
    print(f'  🚀 baostock + {cores} 进程, {len(chunks)} 块 (raw+qfq 各一次查询)...')

    t0 = time.time()
    rows = []
    pool = multiprocessing.Pool(cores)
    wedged = 0
    try:
        futures = [pool.apply_async(fetch_chunk, ((c, dates[0], dates[-1]),)) for c in chunks]
        for i, fut in enumerate(futures):
            try:
                rows.extend(fut.get(timeout=CHUNK_TIMEOUT))
            except multiprocessing.TimeoutError:
                wedged += 1
                print(f'    ⚠️ 块 {i} 超时放弃 ({len(chunks[i])} 只留待 --only-missing)')
            if (i + 1) % 5 == 0 or (i + 1) == len(chunks):
                print(f'    {i + 1}/{len(chunks)} 块, 累计 {len(rows)} 行 ({time.time() - t0:.1f}s)')
    finally:
        pool.terminate()
        pool.join()

    if not rows:
        print('  ❌ 未抓到数据, 缓存未改动')
        return

    new_df = normalize_price_frame(pd.DataFrame(rows))
    new_df = new_df[new_df['date'].isin(dates)]
    print(f'  ✅ 抓取完成 {len(new_df)} 行 ({time.time() - t0:.1f}s), wedged={wedged}')
    merged = merge_price_frames(price_df, new_df)
    merged.to_csv(PRICE_CACHE, index=False)
    print(f'  💾 已落库: {len(price_df)} → {len(merged)} 行')
    print('  🔍 落库后覆盖:')
    _coverage_report(merged, dates)
    print('  📊 A/D 校验:')
    _ad_report(merged, dates)


if __name__ == '__main__':
    main()
