# -*- coding: utf-8 -*-
"""价格缓存 ↔ 逐日切片 同步 (本地/CI 互补空洞的手动入口)。

典型用法:
    python tools/sync_price_slices.py                 # 用切片补齐本地大 CSV (常用)
    python tools/sync_price_slices.py --export        # 把本地最近 10 天导出成切片
    python tools/sync_price_slices.py --export --days 60
    python tools/sync_price_slices.py --status        # 只看两边各有哪些天、覆盖多少

为什么会有空洞可补: 大 CSV 在 .gitignore 里, 本地和 CI 各存一份且从不对账;
切片进 git, 于是 `git pull` + 这个脚本就能把另一侧跑出来的数据搬过来, 零网络请求。
判据与背景见 src/price_slices.py 模块头部。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

import pandas as pd  # noqa: E402

import price_slices as PS  # noqa: E402
from paths import PRICE_CACHE  # noqa: E402


def _status() -> int:
    slices = PS.available_dates()
    frame = PS._read_cache()
    local = (frame.groupby(frame['date'].astype(str))['code'].nunique().to_dict()
             if not frame.empty and 'date' in frame.columns else {})
    print(f'  切片: {len(slices)} 天' + (f' ({slices[0]} ~ {slices[-1]})' if slices else ''))
    print(f'  大 CSV: {len(local)} 天, {len(frame)} 行 -> {PRICE_CACHE}')
    gaps = []
    for date in slices:
        chunk = PS.read_slice(date)
        if chunk.empty:
            continue
        have, mine = chunk['code'].nunique(), local.get(date, 0)
        if mine < have:
            gaps.append(f'{date}: 本地 {mine} / 切片 {have}')
    if gaps:
        print(f'  ❌ 本地比切片薄 {len(gaps)} 天 (跑 python tools/sync_price_slices.py 补):')
        for line in gaps[:20]:
            print(f'       {line}')
        if len(gaps) > 20:
            print(f'       ... 另 {len(gaps) - 20} 天')
        return 1
    print('  ✅ 大 CSV 覆盖不低于任何切片')
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='价格缓存与逐日切片互相补齐')
    ap.add_argument('--export', action='store_true', help='反向: 把大 CSV 导出成切片')
    ap.add_argument('--days', type=int, default=PS.EXPORT_DAYS, help='--export 的天数窗口')
    ap.add_argument('--status', action='store_true', help='只报告差异, 不改任何文件')
    args = ap.parse_args(argv)

    if args.status:
        return _status()
    if args.export:
        written = PS.export_slices(days=args.days)
        if not written:
            print('  ✅ 切片已是最新, 无需重写')
        return 0
    filled = PS.sync_cache_from_slices()
    if filled:
        print('  ➡️ 建议接着跑一次体检: python src/data_selfcheck.py')
    return 0


if __name__ == '__main__':
    sys.exit(main())
