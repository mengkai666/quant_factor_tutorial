# -*- coding: utf-8 -*-
"""CI 每日数据提交撞上本地改动时的**逐行定边**工具。

为什么要有这个脚本
    `data/` 下的缓存两侧都在写: 本地跑批写一份, CI 跑批写一份。两侧改的往往是
    **不同的行**, 于是 git 报"无冲突"直接自动合并 —— 而自动合并对这些文件是错的:
      · sentiment 的 up/down: CI 那侧若拿被裁短的价格缓存重算, 会把历史日写成
        `up+down≈840` 的残缺值。git 看见"只有远端改了这些行", 就照抄进来, 本地
        原有的宽真值被静默换掉 (2026-09-01 实测 58 天)。
      · jsonl 留痕: 两侧各追加了几行, 自动合并按行取并集通常没问题, 但一旦
        两侧在同一行位置各写一行就会判冲突/丢行。这里显式取并集。
    判据不重新发明: 宽度体检直接用 src/ad_breadth.py, 与主程序、对账脚本同源。

用法 (在 `git merge --no-commit` 之后跑, 此时工作树是自动合并的结果):
    python tools/merge_data_caches.py --ref HEAD              # 干跑, 只报告
    python tools/merge_data_caches.py --ref HEAD --apply      # 落库
`--ref` 指"合并前本地那一侧"(merge --no-commit 时就是 HEAD)。脚本对每个日期在
"工作树版本"和"ref 版本"之间挑更可信的一侧, 不做平均、不做插值。
"""
import argparse
import io
import json
import os
import subprocess
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
from ad_breadth import MIN_MARKET_BREADTH, _as_float  # noqa: E402

try:
    sys.stdout.reconfigure(encoding='utf-8')  # pyrefly: ignore [missing-attribute]
except Exception:
    pass

SENTIMENT = 'data/sentiment_history_cache.csv'
JSONL_UNION = ('data/market_phase_snapshots.jsonl',
               'data/report_prediction_history.jsonl')


def _show(ref: str, path: str) -> bytes | None:
    out = subprocess.run(['git', 'show', f'{ref}:{path}'], cwd=ROOT,
                         capture_output=True)
    return out.stdout if out.returncode == 0 else None


def _total(row) -> float:
    up, down = _as_float(row.get('up')), _as_float(row.get('down'))
    return (up or 0.0) + (down or 0.0)


def resolve_sentiment(ref: str, apply: bool) -> int:
    """逐日在工作树版本与 ref 版本之间挑更宽的一侧 (残缺的一侧一律不采用)。"""
    path = os.path.join(ROOT, SENTIMENT)
    blob = _show(ref, SENTIMENT)
    if blob is None or not os.path.exists(path):
        print(f'  ⏭️ {SENTIMENT}: 一侧不存在, 跳过')
        return 0
    cur = pd.read_csv(path, encoding='utf-8-sig', dtype={'日期': str})
    old = pd.read_csv(io.BytesIO(blob), encoding='utf-8-sig', dtype={'日期': str})
    old_by_date = {str(r['日期']): r for _, r in old.iterrows()}

    fixes, added = [], []
    for idx, row in cur.iterrows():
        date = str(row['日期'])
        ref_row = old_by_date.get(date)
        if ref_row is None:
            continue
        now_total, ref_total = _total(row), _total(ref_row)
        # 规则只有一条: **宽的一侧赢**。up+down 就是"这天有多少只股票算出了涨跌",
        # 更宽 = 覆盖更全, 不存在"宽得过头"这回事; 窄的一侧一律不采用。
        if ref_total <= now_total:
            continue
        fixes.append((idx, date, now_total, ref_total,
                      _as_float(ref_row.get('up')), _as_float(ref_row.get('down'))))
    for date, ref_row in old_by_date.items():
        if date not in set(cur['日期'].astype(str)):
            added.append(ref_row)

    if not fixes and not added:
        print(f'  ✅ {SENTIMENT}: 工作树版本每一天都不比 {ref} 窄')
        return 0
    print(f'  🔧 {SENTIMENT}: {len(fixes)} 天被自动合并换窄了, 回退成 {ref} 的值:')
    for _, date, now_total, ref_total, _u, _d in fixes[:80]:
        flag = ' ← 残缺' if now_total < MIN_MARKET_BREADTH else ''
        print(f'    {date}: 合计 {now_total:.0f} → {ref_total:.0f}{flag}')
    if len(fixes) > 80:
        print(f'    ... 另 {len(fixes) - 80} 天')
    if added:
        print(f'  ➕ {len(added)} 天只在 {ref} 里有, 补回')
    if not apply:
        print('  ℹ️ 干跑模式, 未写入。加 --apply 落库。')
        return len(fixes) + len(added)

    for idx, _date, _nt, _rt, up, down in fixes:
        cur.at[idx, 'up'] = up
        cur.at[idx, 'down'] = down
    if added:
        cur = pd.concat([cur, pd.DataFrame(added)], ignore_index=True)
    cur = cur.drop_duplicates(subset=['日期'], keep='first').sort_values('日期')
    # 无 BOM 的 utf-8: 与主程序写法一致 (BOM 会让首列变成 '﻿日期')
    cur.to_csv(path, index=False)
    print(f'  ✅ 已落库 {len(fixes)} 天回退 + {len(added)} 天补回')
    return len(fixes) + len(added)


def union_jsonl(ref: str, apply: bool) -> int:
    """两侧各追加的留痕取并集 (本地顺序在前, ref 独有的追加在后)。"""
    total = 0
    for rel in JSONL_UNION:
        path = os.path.join(ROOT, rel)
        blob = _show(ref, rel)
        if blob is None or not os.path.exists(path):
            continue
        cur = [l for l in io.open(path, encoding='utf-8').read().splitlines() if l.strip()]
        old = [l for l in blob.decode('utf-8').splitlines() if l.strip()]
        missing = [l for l in old if l not in set(cur)]
        if not missing:
            print(f'  ✅ {rel}: {len(cur)} 行已含 {ref} 的全部留痕')
            continue
        print(f'  ➕ {rel}: 补回 {ref} 独有的 {len(missing)} 行 (并集 {len(cur) + len(missing)} 行)')
        total += len(missing)
        if apply:
            with io.open(path, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write('\n'.join(cur + missing) + '\n')
    return total


def check_duplicate_rows() -> int:
    """自动合并最爱制造的另一种伤: 整行重复 (两侧各写了同一条记录的两个副本)。"""
    hits = 0
    for rel in ('data/涨停历史缓存.csv', 'data/cls_plate_cache.csv',
                'data/em_stock_plate_cache.csv', 'data/sentiment_history_cache.csv'):
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        frame = pd.read_csv(path, dtype=str, encoding='utf-8-sig')
        dups = int(frame.duplicated().sum())
        if dups:
            hits += dups
            print(f'  ⚠️ {rel}: {dups} 行整行重复 (自动合并的典型伤, 需手工核对)')
    if not hits:
        print('  ✅ 无整行重复')
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref', default='HEAD',
                    help='"合并前本地那一侧" (git merge --no-commit 时就是 HEAD)')
    ap.add_argument('--apply', action='store_true', help='落库 (默认干跑只报告)')
    args = ap.parse_args()

    print(f'  📐 逐行定边: 工作树 ↔ {args.ref}')
    changed = resolve_sentiment(args.ref, args.apply)
    changed += union_jsonl(args.ref, args.apply)
    check_duplicate_rows()
    if changed and not args.apply:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
