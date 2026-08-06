"""sentiment 缓存 up/down 对账 (一次性运维脚本)。

背景: 价格缓存曾丢失 2026-07-29 / 07-30 两个交易日 (详见 tools/backfill_price_gap.py),
期间 sentiment_history_cache.csv 里这两天写进了残缺快照 (07-29: 1324/994 合计 2318,
07-30: 474/1853 合计 2327, 均远低于全市场 ~5000)。价格缓存回补后, 需要用
"唯一真源" A/D 覆盖这些天。

与主程序的关系: 主程序 (src/主线强度追踪.py) 的"最近 30 交易日 A/D 对账"
(RECENT_FILL_WINDOW) 做的是同一件事, 下次正常运行时会自动纠正。本脚本用于
不想跑整个主程序 (要联网拉涨停/板块/AI) 时, 单独把缓存对齐。

判据同源: 直接 import MarketSentimentFactor._load_ad_cache(), 与主程序、
limit_ratio_factor 用同一份计算逻辑 (±0.1% 判涨跌), 不重复实现。

用法:
    python tools/reconcile_sentiment_ad.py                      # 干跑, 只报告差异
    python tools/reconcile_sentiment_ad.py --apply               # 落库
    python tools/reconcile_sentiment_ad.py --window 30 --apply   # 自定对账窗口
"""
import argparse
import os
import shutil
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from paths import SENTIMENT_CACHE  # noqa: E402
from limit_ratio_factor import MarketSentimentFactor  # noqa: E402

try:
    sys.stdout.reconfigure(encoding='utf-8')  # pyrefly: ignore [missing-attribute]
    sys.stderr.reconfigure(encoding='utf-8')  # pyrefly: ignore [missing-attribute]
except Exception:
    pass

# 全市场宽度下限 (与主程序 MIN_MARKET_BREADTH 同义): up+down 低于此值视为残缺快照
MIN_MARKET_BREADTH = 4000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--window', type=int, default=30, help='对账最近 N 个交易日 (默认 30, 与主程序一致)')
    ap.add_argument('--apply', action='store_true', help='落库 (默认干跑只报告)')
    args = ap.parse_args()

    df = pd.read_csv(SENTIMENT_CACHE, encoding='utf-8-sig', dtype={'日期': str})
    df['up'] = pd.to_numeric(df['up'], errors='coerce').fillna(0)
    df['down'] = pd.to_numeric(df['down'], errors='coerce').fillna(0)
    print(f'  📂 sentiment 缓存: {len(df)} 行, {df["日期"].min()} ~ {df["日期"].max()}')

    ad_map = MarketSentimentFactor()._load_ad_cache() or {}
    if not ad_map:
        sys.exit('  ❌ 价格缓存 A/D 为空, 无法对账')
    print(f'  📊 价格缓存 A/D 覆盖 {len(ad_map)} 个交易日, 最新 {max(ad_map)}')

    window_dates = set(sorted(df['日期'].astype(str).unique())[-args.window:])

    changes, uncovered, thin = [], [], []
    for idx, row in df.iterrows():
        d = str(row['日期'])
        if d not in window_dates:
            continue
        res = ad_map.get(d)
        if res is None:
            if row['up'] == 0 and row['down'] == 0:
                uncovered.append(d)
            continue
        new_up, new_dn = float(res['up']), float(res['down'])
        if new_up + new_dn <= 0:
            if row['up'] == 0 and row['down'] == 0:
                uncovered.append(d)
            continue
        # 真源本身也要过宽度体检 (价格缓存该日若只有部分股票, A/D 同样不可信)
        if new_up + new_dn < MIN_MARKET_BREADTH:
            thin.append((d, int(new_up + new_dn), int(new_up), int(new_dn)))
            continue
        if new_up != row['up'] or new_dn != row['down']:
            changes.append((idx, d, row['up'], row['down'], new_up, new_dn))

    if changes:
        print(f'  🔧 需更新 {len(changes)} 天:')
        for _, d, ou, od, nu, nd in changes:
            flag = ' ← 残缺污染' if ou + od < MIN_MARKET_BREADTH else ''
            print(f'    {d}: {ou:.0f}/{od:.0f} (合计 {ou + od:.0f}) → {nu:.0f}/{nd:.0f} (合计 {nu + nd:.0f}){flag}')
    else:
        print('  ✅ 窗口内 up/down 与真源一致, 无需更新')
    if thin:
        print('  ⚠️ 真源自身宽度不足, 已跳过 (需先回补价格缓存):')
        for d, total, up, down in thin:
            print(f'    {d}: up={up} down={down} (合计 {total}) [thin]')
    if uncovered:
        print(f'  ⚠️ 价格缓存尚未覆盖: {uncovered}')

    if not changes:
        return
    if not args.apply:
        print('  ℹ️ 干跑模式, 未写入。加 --apply 落库。')
        return

    bak = f'{SENTIMENT_CACHE}.bak.{datetime.now():%Y%m%d_%H%M%S}'
    shutil.copy2(SENTIMENT_CACHE, bak)
    print(f'  💾 已备份 → {os.path.basename(bak)}')

    for idx, _, _, _, nu, nd in changes:
        df.at[idx, 'up'] = nu
        df.at[idx, 'down'] = nd
    # ⚠️ 必须与主程序写法完全一致: `to_csv(..., index=False)` = 无 BOM 的 utf-8。
    #    主程序读回时用 `pd.read_csv(SENTIMENT_CACHE, dtype={'日期': str})` (默认 utf-8),
    #    若这里写成 utf-8-sig, BOM 会让首列变成 '﻿日期', '日期' 列引用直接失效。
    #    (读入时用 utf-8-sig 是安全的 —— 它对无 BOM 文件同样正确。)
    df.to_csv(SENTIMENT_CACHE, index=False)
    print(f'  ✅ 已落库 {len(changes)} 天')


if __name__ == '__main__':
    main()
