"""价格缓存缺口回补 (一次性运维脚本)。

背景: update_price_cache 旧版按"日历天差 <=4"判断是否走腾讯快照快速路径,
在缓存落后 >=2 个交易日时会误触发, 只补最新一天就 return, 把中间交易日
(本次事故: 2026-07-29 / 07-30) 永久丢弃。主程序已修复 (改用交易日历精确判断),
但已丢失的历史日不会自动回补 —— update_price_cache 只从 max(date)+1 起抓,
而 max(date) 已经是更晚的日期。故用本脚本手工补齐。

用法:
    python tools/backfill_price_gap.py --dates 20260729,20260730          # 干跑, 只报告
    python tools/backfill_price_gap.py --dates 20260729,20260730 --apply  # 落库

流程:
  1. 备份 price_history_cache.csv (带时间戳)
  2. baostock 多进程逐股抓 [min(dates), max(dates)] 区间 close
  3. 只保留目标日期的行, 与现有缓存合并去重, 落库
  4. 报告各目标日的覆盖行数与 A/D (由 close 逐股比对前一交易日算出)

sentiment 缓存不在此脚本处理: 主程序的"最近 30 交易日 A/D 对账"
(RECENT_FILL_WINDOW) 会在下次运行时用价格缓存 A/D 覆盖窗口内每一天,
自动纠正 07-29/07-30/07-31 的残缺值。
"""
import argparse
import multiprocessing
import os
import shutil
import socket
import sys
import time
from datetime import datetime

import pandas as pd

# Windows 控制台默认 GBK, emoji/中文会 UnicodeEncodeError; 强制 UTF-8 输出
try:
    # pyrefly: ignore [missing-attribute]
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    # pyrefly: ignore [missing-attribute]
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from paths import PRICE_CACHE, INDUSTRY_CACHE  # noqa: E402
from data_sources.price_provider import normalize_price_frame, merge_price_frames  # noqa: E402

# 单块硬超时 (秒): 一块 200 只股票正常 20~40s。某只股票的畸形响应会让 rs.next()
# 在 worker 里 100% CPU 空转 (socket 超时管不住 C 层读循环), imap_unordered + 池
# 关闭会永久等它 —— 2026-08-03/04 两次卡死 9 小时的根因。超时即放弃该块, 卡死的
# worker 由 pool.terminate() 强杀, 漏掉的股票下一轮 --only-missing 增量补齐。
CHUNK_TIMEOUT = 180


def _fetch_bs_chunk(args):
    """抓一批股票在 [start, end] 的前复权价格。"""
    codes, start, end = args
    socket.setdefaulttimeout(15)  # 防止 socket 挂起导致进程死锁
    # pyrefly: ignore [missing-import]
    import baostock as bs

    old_stdout = sys.stdout
    sys.stdout = open(os.devnull, 'w')
    try:
        bs.login()
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout

    rows = []
    for code_str in codes:
        if not (code_str.startswith('sh') or code_str.startswith('sz')):
            continue
        bs_code = code_str[:2] + '.' + code_str[2:]
        try:
            rs = bs.query_history_k_data_plus(
                bs_code, "date,code,close",
                start_date=start, end_date=end,
                frequency="d", adjustflag="2",
            )
            if rs and rs.error_code == '0':
                while rs.next():
                    row = rs.get_row_data()
                    # ⚠️ 串号护栏: baostock 批量/多进程下偶发对 query(A) 返回 B 的 K 线,
                    #    若盲目贴 code_str 标签, 相邻代码的 close 会张冠李戴 (事故: 07-29/30
                    #    002049↔002050 / 002919↔002920 成对错位)。用返回行的 row[1] 裸码校验,
                    #    不符即丢弃 —— 该股当日缺失, 好过写入错值污染 A/D。
                    ret_code = row[1].replace('.', '') if row[1] else ''
                    if ret_code and ret_code != code_str:
                        continue
                    rows.append({
                        'date': row[0],
                        'code': code_str,
                        'close_qfq': float(row[2]),
                        'price_basis': 'qfq',
                        'source': 'baostock',
                    })
        except Exception:
            continue  # 单股失败不拖垮整块

    old_stdout = sys.stdout
    sys.stdout = open(os.devnull, 'w')
    try:
        bs.logout()
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout

    return rows


def _load_codes(price_df):
    """全市场代码: 优先行业缓存 (与主程序 update_price_cache 同源), 回退价格缓存。"""
    if os.path.exists(INDUSTRY_CACHE):
        try:
            idf = pd.read_csv(INDUSTRY_CACHE, dtype=str)
            if not idf.empty and 'code' in idf.columns:
                codes = idf['code'].dropna().unique().tolist()
                if codes:
                    return codes
        except Exception as e:
            print(f'  ⚠️ 行业缓存读取失败, 回退价格缓存代码表: {e}')
    return price_df['code'].unique().tolist()


def _report_ad(price_df, target_dates):
    """按价格缓存算目标日 A/D (与 MarketSentimentFactor._load_ad_cache 同判据: ±0.1%)。"""
    df = normalize_price_frame(price_df)
    value_col = 'close_qfq' if df.get('close_qfq', pd.Series(dtype=float)).notna().any() else 'close_legacy'
    df['date_clean'] = df['date'].str.replace('-', '')
    df = df.sort_values(['code', 'date_clean'])
    df['prev_close'] = df.groupby('code')[value_col].shift(1)
    df['chg_pct'] = (df[value_col] / df['prev_close'] - 1) * 100
    df = df.dropna(subset=['chg_pct'])
    for d in target_dates:
        sub = df[df['date_clean'] == d]
        if sub.empty:
            print(f'    {d}: 无数据')
            continue
        up = int((sub['chg_pct'] > 0.1).sum())
        dn = int((sub['chg_pct'] < -0.1).sum())
        flat = len(sub) - up - dn
        print(f'    {d}: up={up} down={dn} flat={flat} (合计 {len(sub)})')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dates', required=True, help='要回补的交易日, YYYYMMDD 逗号分隔')
    ap.add_argument('--apply', action='store_true', help='落库 (默认干跑只报告)')
    ap.add_argument('--cores', type=int, default=0, help='进程数 (默认 min(4, cpu-1))')
    ap.add_argument('--only-missing', action='store_true',
                    help='只抓目标日尚缺的代码 (增量重试; baostock 逐股偶发超时会漏股, '
                         '首轮跑完用它补齐, 避免重抓全市场)')
    ap.add_argument('--overwrite', action='store_true',
                    help='用新抓的数据覆盖目标日已有行 (修陈旧快照污染: 目标日行数满格但 '
                         'close 全是前一交易日的值, 默认合并会让旧污染行胜出)')
    args = ap.parse_args()

    target_dates = sorted(d.strip() for d in args.dates.split(',') if d.strip())
    if not target_dates:
        sys.exit('  ❌ --dates 为空')
    target_dashed = {datetime.strptime(d, '%Y%m%d').strftime('%Y-%m-%d') for d in target_dates}

    price_df = normalize_price_frame(pd.read_csv(PRICE_CACHE, dtype={'code': str, 'date': str}))
    orig_rows = len(price_df)
    print(f'  📂 现有价格缓存: {len(price_df)} 行, 日期范围 {price_df["date"].min()} ~ {price_df["date"].max()}')
    existing = price_df[price_df['date'].isin(target_dashed)]
    print(f'  🔍 目标日现有行数: {dict(existing["date"].value_counts()) or "全部缺失"}')

    codes = _load_codes(price_df)
    if args.only_missing:
        # 任一目标日缺该代码, 就重抓它 (抓的是整个 [start,end] 区间, 一次覆盖所有目标日)
        full = set(codes)
        need = set()
        for d in sorted(target_dashed):
            have = set(price_df.loc[price_df['date'] == d, 'code'])
            need |= (full - have)
        print(f'  ♻️ 增量模式: 全市场 {len(codes)} 只, 目标日尚缺 {len(need)} 只')
        codes = sorted(need)
        if not codes:
            print('  ✅ 目标日已全覆盖, 无需回补')
            return
    start = min(target_dashed)
    end = max(target_dashed)
    print(f'  🎯 待抓 {len(codes)} 只股票, 区间 [{start}, {end}]')

    if not args.apply:
        print('  ℹ️ 干跑模式, 未抓取。加 --apply 执行回补。')
        return

    bak = f'{PRICE_CACHE}.bak.{datetime.now():%Y%m%d_%H%M%S}'
    shutil.copy2(PRICE_CACHE, bak)
    print(f'  💾 已备份 → {os.path.basename(bak)}')

    cores = args.cores or max(1, min(4, multiprocessing.cpu_count() - 1))
    chunk_size = 200
    chunks = [codes[i:i + chunk_size] for i in range(0, len(codes), chunk_size)]
    print(f'  🚀 baostock + {cores} 进程, {len(chunks)} 个任务块...')

    t0 = time.time()
    new_rows = []
    tasks = [(c, start, end) for c in chunks]
    # apply_async + 逐块 .get(timeout) 取代 imap_unordered: 卡死的块最多拖 CHUNK_TIMEOUT
    # 秒就被放弃, 不会无限等待。健康块的结果已在后台 worker 算好, .get() 秒回。
    pool = multiprocessing.Pool(cores)
    wedged = []
    try:
        async_results = [pool.apply_async(_fetch_bs_chunk, (t,)) for t in tasks]
        for i, ar in enumerate(async_results):
            try:
                new_rows.extend(ar.get(timeout=CHUNK_TIMEOUT))
            except multiprocessing.TimeoutError:
                wedged.append(i)
                print(f'    ⚠️ 块 {i} 超时 {CHUNK_TIMEOUT}s 放弃 '
                      f'({len(tasks[i][0])} 只留待 --only-missing 补)')
            if (i + 1) % 5 == 0 or (i + 1) == len(chunks):
                print(f'    已处理 {i + 1}/{len(chunks)} 块, 累计 {len(new_rows)} 行 ({time.time() - t0:.1f}s)')
    finally:
        pool.terminate()  # 强杀所有 worker (含 100% CPU 空转那个), 不 join 等它自己退
        pool.join()
    if wedged:
        print(f'  ⚠️ {len(wedged)} 块超时被放弃, 跑完再用 --only-missing 增量补齐: '
              f'python tools/backfill_price_gap.py --dates <同一批> --apply --only-missing')

    if not new_rows:
        print('  ❌ 未抓到任何数据, 缓存未改动')
        return

    new_df = pd.DataFrame(new_rows)
    new_df = new_df[new_df['date'].isin(target_dashed)]  # 只保留目标日, 不动其他日期
    print(f'  ✅ 抓取完成: 目标日 {len(new_df)} 行 (耗时 {time.time() - t0:.1f}s)')
    print(f'     各日覆盖: {dict(new_df["date"].value_counts())}')
    if new_df.empty:
        print('  ❌ 目标日无数据 (可能非交易日或 baostock 未收录), 缓存未改动')
        return

    if args.overwrite:
        # 覆盖模式: 先删掉目标日的旧行, 新抓的行独占。
        # 用于修陈旧快照污染 (目标日已有 5190 行满格, 但 close 全是前一交易日的值,
        # 默认 keep='first' 会让污染行胜出, 等于什么都没修)。
        before = len(price_df)
        price_df = price_df[~price_df['date'].isin(target_dashed)]
        print(f'  🧹 覆盖模式: 已剔除目标日旧行 {before - len(price_df)} 条')
    combined = merge_price_frames(price_df, new_df)
    combined.to_csv(PRICE_CACHE, index=False)
    print(f'  💾 已落库: {orig_rows} → {len(combined)} 行 ({len(combined) - orig_rows:+d})')

    print('  📊 回补后目标日 A/D:')
    _report_ad(combined, target_dates)
    print('  ➡️ 下一步: 跑主程序, "最近30交易日 A/D 对账" 会自动纠正 sentiment 缓存。')


if __name__ == '__main__':
    main()
