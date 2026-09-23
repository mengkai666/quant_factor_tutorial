# -*- coding: utf-8 -*-
"""baostock 长历史日线回补 + 涨停/连板重建 + 停牌标记落库。

为什么需要这个工具 (两件事一次做完)
--------------------------------
1. **更长样本**。高度/压力研究现在只有 10 个月、13 个完整周期, 撑不住 n=12 的纪律。
   而涨停池接口对任意历史日期返回 0 行 (memory zt-pool-api-no-history), 所以样本
   **无法**用现有管道加长 —— 只能从 K 线重建涨停。这不是"多抓几天"那么简单。
2. **停牌标记**。价格缓存里 2.17% 的股票日是"恰好 0.00%"的假平盘, 把所有中位数
   往 0 拉、把红盘率压低。`tradestatus='0'` 是真标记, 和 K 线一趟拿回来。

判据细节 (为什么不是百分比阈值) 见 src/baostock_bars.py 模块头部。

用法
--------------------------------
    # 干跑: 只报告要抓多少只、预计多久
    python tools/backfill_baostock_bars.py --start 2024-09-01 --end 2026-09-11

    # 实抓 (可随时 Ctrl-C, 逐股一个文件, 重跑自动续)
    python tools/backfill_baostock_bars.py --start 2024-09-01 --end 2026-09-11 --apply

    # 只重建 (K 线已在本地, 零网络)
    python tools/backfill_baostock_bars.py --reconstruct-only

    # 与涨停池真值对账 (看差异成分, 不是看准确率)
    python tools/backfill_baostock_bars.py --reconstruct-only --validate

已知边界 (必须随结论一起披露)
--------------------------------
* **北交所 (bj) 全段缺失**。baostock 报"股票代码未标识sh或sz"。实测代价: bj 占
  涨停池 0.75%、占 ≥7 板 0%、209 天里只有 1 天独占市场最高板 —— 对连板高度研究
  可忽略, 但报告里要写。
* **生存偏差**。stock_universe.csv 只有当前在册的 5543 只、退市行数为 0。默认走
  `--universe pit` 逐月取一次历史在册名单来补回退市股; 用 `--universe current`
  会快一些但结论带生存偏差。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

import pandas as pd

try:
    # pyrefly: ignore [missing-attribute]
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    # pyrefly: ignore [missing-attribute]
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'src'))

import baostock_bars as bb  # noqa: E402
from paths import (  # noqa: E402
    BAOSTOCK_BAR_DIR,
    BAOSTOCK_LIMIT_HISTORY,
    UNIVERSE_CACHE,
    ZT_CACHE_FILE,
)


def _month_sample_dates(start: str, end: str) -> list[str]:
    """逐月取一个采样日, 用来拼历史在册名单。

    按月足够: 退市/新上市不会在一个月内出现又消失, 而 query_all_stock 单次 30~70s,
    逐个交易日采样是几小时的事。
    """
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    out, cur = [], s
    while cur <= e:
        out.append(cur.strftime('%Y-%m-%d'))
        cur = (cur + pd.offsets.MonthBegin(1))
    tail = e.strftime('%Y-%m-%d')
    if tail not in out:
        out.append(tail)
    return out


def _load_universe(mode: str, start: str, end: str, log=print) -> list[str]:
    # 两道过滤是两个不同的问题, 缺一不可: is_supported 问 baostock 有没有覆盖
    # (北交所没有), is_stock 问这个代码是不是股票 (指数/场内基金要剔)。
    if mode == 'current':
        df = pd.read_csv(UNIVERSE_CACHE, dtype=str)
        codes = [bb.to_bare(c) for c in df['code'].dropna().unique()]
        codes = [c for c in codes if bb.is_supported(c) and bb.is_stock(c)]
        log(f'  当前在册名单: {len(codes)} 只 (⚠️ 带生存偏差, 无退市股)')
        return sorted(codes)

    dates = _month_sample_dates(start, end)
    log(f'  历史在册名单: 逐月采样 {len(dates)} 个日期 (首次抓取约 {len(dates) * 45 / 60:.0f} 分)')
    pit = bb.fetch_point_in_time_universe(dates, log=log)
    codes = sorted({bb.to_bare(c) for c in pit['code'].dropna().unique()})
    # 缓存可能是旧版本写的 (白名单之前那版把 1400 只基金也存进去了), 所以读出来
    # 还要再过一遍 —— 过滤只放在写入侧的话, 旧缓存会把污染一直带下去。
    codes = [c for c in codes if bb.is_supported(c) and bb.is_stock(c)]

    _log_survivorship_delta(codes, log)
    return codes


def _log_survivorship_delta(codes: list[str], log=print) -> None:
    """报出"在历史名单里、不在当前名单里"的那批, 并自己判断它是不是退市股。

    ⚠️ 这个差集**不等于**退市股, 别再把它直接写成"生存偏差修掉的部分"。原先那句
    话就是这么写的, 报了 1735 只"退市/改码", 实际大部分是基金 —— 当前名单里没有
    场内基金, 于是 1400 只被 query_all_stock 带进来的 ETF 全被算成了退市。
    品种过滤修好之后这句话大概率是对的, 但"大概率对"不是判据: 差集的成因至少有
    三种 (真退市 / 改码 / 两个数据源的覆盖范围不同), 靠断言分不开。
    所以改成看**分布**: 退市是零星发生的, 各代码段都有几只; 而覆盖差异是整段缺失,
    会让某个前缀在差集里的占比远高于它在全名单里的占比。用后者当判据。
    """
    try:
        cur_raw = {bb.to_bare(c) for c in pd.read_csv(UNIVERSE_CACHE, dtype=str)['code'].dropna()}
    except Exception as exc:
        log(f'  ⚠️ 读当前名单失败, 跳过生存偏差核对: {str(exc)[:60]}')
        return
    cur = {c for c in cur_raw if bb.is_supported(c) and bb.is_stock(c)}
    gone = sorted(set(codes) - cur)
    if not codes:
        return

    log(f'  历史名单 {len(codes)} 只, 其中 {len(gone)} 只不在当前名单里 '
        f'({len(gone) / len(codes) * 100:.1f}%)')
    if not gone:
        return

    # 逐前缀比占比。阈值 3 倍是留给"某个板块退市确实更多"的余量 —— 真退市的段间
    # 差异是倍数级, 而整段缺失是数量级 (差集里 100%, 全名单里 0%)。
    def _pfx(c: str) -> str:
        return c[:4]

    all_share: dict[str, float] = {}
    for c in codes:
        all_share[_pfx(c)] = all_share.get(_pfx(c), 0) + 1 / len(codes)
    gone_cnt: dict[str, int] = {}
    for c in gone:
        gone_cnt[_pfx(c)] = gone_cnt.get(_pfx(c), 0) + 1

    suspect = []
    for p, n in sorted(gone_cnt.items(), key=lambda kv: -kv[1]):
        share_gone = n / len(gone)
        share_all = all_share.get(p, 0.0)
        flag = share_all > 0 and share_gone > share_all * 3
        log(f'    {p}: {n} 只 (占差集 {share_gone * 100:.0f}%, '
            f'占全名单 {share_all * 100:.0f}%)' + ('  ← 偏高' if flag else ''))
        if flag:
            suspect.append(p)

    if suspect:
        log(f'  ⚠️ {"/".join(suspect)} 在差集里占比异常, 更像是两个名单的覆盖范围不同, '
            f'而不是退市 —— 别把这 {len(gone)} 只整批当成"生存偏差修掉的部分"')
    else:
        log('  各代码段占比与全名单接近 (零星退市的形态), '
            '这批可当作生存偏差修掉的部分')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2024-09-01', help='起始日 YYYY-MM-DD')
    ap.add_argument('--end', default=datetime.now().strftime('%Y-%m-%d'), help='结束日')
    ap.add_argument('--universe', choices=['pit', 'current'], default='pit',
                    help='pit=历史在册名单(补退市股, 默认) / current=当前名单(快但带生存偏差)')
    ap.add_argument('--apply', action='store_true', help='真抓 (默认干跑只报告)')
    ap.add_argument('--cores', type=int, default=0, help='进程数 (默认 min(4, cpu-1))')
    ap.add_argument('--limit', type=int, default=0, help='只抓前 N 只 (调试用)')
    ap.add_argument('--reconstruct-only', action='store_true', help='跳过抓取, 只重建')
    ap.add_argument('--validate', action='store_true', help='重建后与涨停池真值对账')
    args = ap.parse_args()

    # --cores 0 只是 argparse 的"用默认"哨兵, 不能直接进 Pool() (Pool(0) 直接抛)。
    # 留一核给系统: baostock 抓取是 IO 等待为主, 再多进程也吃不到带宽。
    cores = args.cores if args.cores > 0 else max(1, min(4, (os.cpu_count() or 2) - 1))

    t0 = time.time()

    if not args.reconstruct_only:
        print(f'\n=== 1/3 股票池 ({args.universe}) ===')
        codes = _load_universe(args.universe, args.start, args.end)
        if args.limit:
            codes = codes[:args.limit]
            print(f'  --limit: 只处理前 {len(codes)} 只')

        todo = [c for c in codes if not bb.has_bars(c)]
        print(f'\n=== 2/3 抓取 {args.start} ~ {args.end} ===')
        print(f'  共 {len(codes)} 只, 已有 {len(codes) - len(todo)} 只, 待抓 {len(todo)} 只')
        if todo:
            # 实测 ~1.2s/只 (24 个月), 4 进程并行按 3.5x 折算。
            print(f'  预计 {len(todo) * 1.2 / cores / 60 * 1.15:.0f} 分钟 ({cores} 进程)')
        if not args.apply:
            print('  干跑, 未抓取。加 --apply 真跑 (可随时 Ctrl-C, 重跑自动续)')
            return 0
        if todo:
            status = bb.fetch_many_parallel(todo, args.start, args.end, cores=cores)
            print('\n  抓取结果:')
            for st, n in status['status'].value_counts().items():
                print(f'    {st}: {n}')
            bad = status[status['status'] == 'mislabel']
            if not bad.empty:
                print(f'  ⚠️ {len(bad)} 只因串号被整只丢弃 (下次重跑会补): '
                      f'{", ".join(bad["code"].head(5))}')

    n_files = len([f for f in os.listdir(BAOSTOCK_BAR_DIR) if f.endswith('.csv.gz')])
    print(f'\n=== 3/3 重建 (本地 {n_files} 只) ===')
    if not n_files:
        print('  没有 K 线文件, 先跑抓取')
        return 1
    tables = bb.reconstruct()
    limit_df = tables['limit']

    if not limit_df.empty:
        dates = sorted(limit_df['date'].unique())
        print(f'  涨停覆盖 {dates[0]} ~ {dates[-1]}, {len(dates)} 个交易日')
        for lo, hi, tag in ((1, 1, '首板'), (2, 2, '2板'), (3, 4, '3-4板'),
                            (5, 6, '5-6板'), (7, 99, '≥7板')):
            sub = limit_df[(limit_df['lb'] >= lo) & (limit_df['lb'] <= hi)]
            print(f'    {tag}: {len(sub)} 行, {sub["code"].nunique()} 只')

    if args.validate:
        print('\n=== 对账 (差异看成分, 不看准确率 —— 真值池自己也漏票) ===')
        zt = pd.read_csv(ZT_CACHE_FILE, dtype=str)
        rec = bb.validate_against_truth(limit_df, zt)
        if not rec.empty:
            worst = rec.nlargest(5, 'only_truth')
            print('  漏抓最多的 5 天 (仅真值有):')
            for r in worst.itertuples(index=False):
                print(f'    {r.date}: 真值 {r.truth} 重建 {r.rebuilt} 漏 {r.only_truth} '
                      f'[{r.sample_only_truth}]')

    print(f'\n完成, 用时 {(time.time() - t0) / 60:.1f} 分')
    print(f'  涨停/连板: {BAOSTOCK_LIMIT_HISTORY}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
