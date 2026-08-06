"""数据缓存完整性体检 (可复跑, 无网络依赖)。

背景: 2026-08-03 事故暴露三类静默缺陷, 单看行数或日期范围都发现不了 ——
  1. 陈旧副本   某交易日行数满格, 但 close 整批是前一交易日的副本 (A/D 全 flat)
  2. 覆盖缺口   某交易日只抓到 30%~80% 的股票 (A/D 数值偏小但"看起来正常")
  3. 休市日污染 价格缓存无数据的日子, 涨停缓存却有涨停记录 (如 20260501 劳动节)
  4. 宽度残缺   sentiment 缓存 up+down 远低于全市场 (残缺快照被写进历史)

本脚本把当时手工敲的判据固化成一条命令, 不联网, 只读三份缓存。
退出码非 0 表示发现缺陷, 可挂 CI / 主程序前置检查。

用法:
    python tools/audit_data_integrity.py              # 全量体检
    python tools/audit_data_integrity.py --recent 60  # 只看最近 60 交易日
    python tools/audit_data_integrity.py --quiet      # 只报缺陷, 不打明细

判据同源说明:
  - 陈旧副本用"逐股 close 身份比对", 与主程序 _fetch_tencent_close 的护栏、
    _drop_stale_latest_day 同一判据 (相邻交易日全市场 >90% 报价精确相同 = 副本)。
    其它判据 (涨跌幅==0 / 行数比 / 时间点) 都被实测证明会漏, 别再换。
  - 宽度下限 MIN_MARKET_BREADTH 与主程序、tools/reconcile_sentiment_ad.py 同义。
"""
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from paths import (  # noqa: E402
    PRICE_CACHE, ZT_CACHE_FILE, SENTIMENT_CACHE, UNIVERSE_CACHE,
    FETCH_STATUS_CACHE, QUALITY_REPORT,
)
from data_sources.fetch_status import FetchStatusStore  # noqa: E402
from data_sources.models import FetchResult, FetchStatus  # noqa: E402
from data_sources.price_provider import PRICE_COLUMNS  # noqa: E402
from data_sources.quality_gate import MarketDataQualityGate  # noqa: E402

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # pyrefly: ignore [missing-attribute]
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')  # pyrefly: ignore [missing-attribute]
except Exception:
    pass

# 全市场宽度下限: up+down 低于此值视为残缺快照 (与主程序 MIN_MARKET_BREADTH 同义)
MIN_MARKET_BREADTH = 4000
# 覆盖率下限: 某交易日行数 / 基准行数 低于此值视为覆盖缺口
MIN_COVERAGE_RATIO = 0.90
# 陈旧副本阈值: 与前一交易日 close 逐股完全相同的占比上限 (留 10% 停牌余量)
STALE_IDENTITY_THRESHOLD = 0.90
# 身份比对的最小交集: 不足此数不判 (样本太小, 宁可放过也不误报)
MIN_COMPARE_SAMPLE = 500


def _to_dashed(yyyymmdd: str) -> str:
    return f'{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}'


def _to_compact(dashed: str) -> str:
    return dashed.replace('-', '')


def check_conflict_markers(path: str) -> list:
    """检查 CSV 是否残留 git 冲突标记 (曾致 awk 截断读到错误数, 见 memory)。"""
    if not os.path.exists(path):
        return []
    hits = []
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for i, line in enumerate(f, 1):
            if line.startswith(('<<<<<<<', '=======', '>>>>>>>')):
                hits.append((i, line.rstrip()[:40]))
                if len(hits) >= 5:
                    break
    return hits


def audit_price(price_df: pd.DataFrame, dates: list, quiet: bool) -> list:
    """价格缓存: 陈旧副本 + 覆盖缺口 + 重复/异常值。返回缺陷描述列表。"""
    defects = []
    counts = price_df['date'].value_counts()
    baseline = int(counts.median())
    print(f'\n  📊 价格缓存: {len(price_df)} 行, {len(dates)} 交易日, '
          f'{dates[0]} ~ {dates[-1]}, 基准覆盖 {baseline} 只/日')

    # --- 1. 覆盖缺口 ---
    thin = [(d, int(counts[d])) for d in dates
            if counts[d] < baseline * MIN_COVERAGE_RATIO]
    if thin:
        defects.append(f'价格缓存覆盖不足 {len(thin)} 天 (<{MIN_COVERAGE_RATIO:.0%} 基准)')
        print(f'  ❌ 覆盖不足 {len(thin)} 天:')
        for d, n in (thin if not quiet else thin[:10]):
            print(f'       {d}: {n} 只 ({n / baseline:.0%})')
        if quiet and len(thin) > 10:
            print(f'       ... 另 {len(thin) - 10} 天')
    else:
        print(f'  ✅ 覆盖率: 全部 {len(dates)} 天 ≥{MIN_COVERAGE_RATIO:.0%} 基准')

    # --- 2. 陈旧副本 (逐股 close 身份比对) ---
    close_by_date = {d: dict(zip(price_df.loc[price_df['date'] == d, 'code'],
                                 price_df.loc[price_df['date'] == d, 'close']))
                     for d in dates}
    stale, ratios = [], []
    for prev, cur in zip(dates, dates[1:]):
        cur_map, prev_map = close_by_date[cur], close_by_date[prev]
        common = set(cur_map) & set(prev_map)
        if len(common) < MIN_COMPARE_SAMPLE:
            continue
        identical = sum(1 for c in common
                        if abs(float(cur_map[c]) - float(prev_map[c])) < 1e-6)
        ratio = identical / len(common)
        ratios.append(ratio)
        if ratio > STALE_IDENTITY_THRESHOLD:
            stale.append((cur, prev, identical, len(common), ratio))
    if stale:
        defects.append(f'价格缓存陈旧副本 {len(stale)} 天')
        print(f'  ❌ 陈旧副本 {len(stale)} 天 (close 与前一交易日逐股相同):')
        for cur, prev, ident, tot, r in stale:
            print(f'       {cur} ≈ {prev}: {ident}/{tot} ({r:.1%})')
    else:
        peak = max(ratios) if ratios else 0
        print(f'  ✅ 无陈旧副本 (相邻日最大身份重合 {peak:.1%}, '
              f'阈值 {STALE_IDENTITY_THRESHOLD:.0%})')

    # --- 3. 值域与重复 ---
    bad_close = (pd.to_numeric(price_df['close'], errors='coerce').fillna(0) <= 0).sum()
    dup = int(price_df.duplicated(subset=['code', 'date']).sum())
    if bad_close:
        defects.append(f'价格缓存 close<=0 或空值 {bad_close} 行')
        print(f'  ❌ close<=0/空值: {bad_close} 行')
    if dup:
        defects.append(f'价格缓存 (code,date) 重复 {dup} 行')
        print(f'  ❌ (code,date) 重复: {dup} 行')
    if not bad_close and not dup:
        print('  ✅ 值域与唯一性: close 全 >0, (code,date) 无重复')

    return defects


def audit_zt(price_dates_compact: set, quiet: bool) -> list:
    """涨停缓存: 休市日污染 (价格缓存无数据的日子却有涨停记录)。"""
    defects = []
    if not os.path.exists(ZT_CACHE_FILE):
        print('\n  ⚠️ 涨停缓存不存在, 跳过')
        return defects

    marks = check_conflict_markers(ZT_CACHE_FILE)
    if marks:
        defects.append(f'涨停缓存残留 git 冲突标记 {len(marks)} 处')
        print(f'\n  ❌ 涨停缓存有 git 冲突标记 (第 {marks[0][0]} 行起), 读数会被截断')
        return defects

    zt = pd.read_csv(ZT_CACHE_FILE, encoding='utf-8-sig', dtype={'日期': str})
    zt['日期'] = zt['日期'].astype(str).str.strip()
    zt_dates = sorted(zt['日期'].unique())
    print(f'\n  📊 涨停缓存: {len(zt)} 行, {len(zt_dates)} 天, {zt_dates[0]} ~ {zt_dates[-1]}')

    if not price_dates_compact:
        print('  ⚠️ 价格缓存无日期, 跳过涨停体检')
        return defects

    # 价格缓存有覆盖 = 确认是交易日; ZT 有记录但价格缓存整天无数据 = 休市日污染。
    # 必须先 clip 到价格缓存日期区间 [lo, hi] 再判: 区间外的天没有真源可比。
    # --recent N 会把区间收窄到最近 N 天, 不 clip 就会把更早的正常涨停日全判成
    # 污染 (2026-08-04 实测 --recent 30 误报 150 天)。
    lo, hi = min(price_dates_compact), max(price_dates_compact)
    in_range = [d for d in zt_dates if lo <= d <= hi]
    if len(in_range) < len(zt_dates):
        print(f'  ℹ️ {len(zt_dates) - len(in_range)} 天在价格缓存区间 ({lo} ~ {hi}) 之外, '
              f'无真源可比, 本次不判')

    orphan = [d for d in in_range if d not in price_dates_compact]
    if orphan:
        rows = {d: int((zt['日期'] == d).sum()) for d in orphan}
        defects.append(f'涨停缓存非交易日污染 {len(orphan)} 天')
        print(f'  ❌ 价格缓存无该日数据却有涨停记录 {len(orphan)} 天 (疑休市日污染):')
        for d in orphan:
            print(f'       {d}: {rows[d]} 行')
    else:
        print('  ✅ 无非交易日污染 (每个涨停日在价格缓存均有数据)')

    missing = sorted(price_dates_compact - set(zt_dates))
    if missing:
        # 不算缺陷: 涨停池接口只对当日有效, 历史日无法回补 (见 memory)
        head = missing if not quiet else missing[:8]
        print(f'  ℹ️ 价格缓存有但涨停缓存缺 {len(missing)} 天 (涨停池接口不支持历史回补): '
              f'{", ".join(head)}{" ..." if quiet and len(missing) > 8 else ""}')

    return defects


def audit_sentiment(price_dates_compact: set, quiet: bool) -> list:
    """sentiment 缓存: 假交易日 + up/down 宽度残缺 + 编码 BOM。"""
    defects = []
    if not os.path.exists(SENTIMENT_CACHE):
        print('\n  ⚠️ sentiment 缓存不存在, 跳过')
        return defects

    with open(SENTIMENT_CACHE, 'rb') as f:
        if f.read(3) == b'\xef\xbb\xbf':
            defects.append('sentiment 缓存带 UTF-8 BOM (首列名会变 \\ufeff日期)')
            print('\n  ❌ sentiment 缓存带 BOM, 写入时须 to_csv(..., index=False) 不带 encoding')

    sent = pd.read_csv(SENTIMENT_CACHE, encoding='utf-8-sig', dtype={'日期': str})
    for col in ('up', 'down'):
        if col not in sent.columns:
            defects.append(f'sentiment 缓存缺列 {col}')
            print(f'\n  ❌ sentiment 缓存缺列 {col}, 跳过宽度体检')
            return defects
        sent[col] = pd.to_numeric(sent[col], errors='coerce').fillna(0)
    print(f'\n  📊 sentiment 缓存: {len(sent)} 行, {sent["日期"].min()} ~ {sent["日期"].max()}')

    if not price_dates_compact:
        print('  ⚠️ 价格缓存无日期, 跳过 sentiment 体检')
        return defects

    # 一切判据都先 clip 到价格缓存日期区间 [lo, hi]: 区间外的天没有 A/D 真源可比,
    # 既判不了假交易日, 也修不了宽度残缺。这样 --recent N 收窄区间时不会误报
    # (曾把 63 天说成"早于价格缓存起点", 其实真源起点是 20251104, 只是窗口被收窄了)。
    lo, hi = min(price_dates_compact), max(price_dates_compact)
    in_range = sent[(sent['日期'] >= lo) & (sent['日期'] <= hi)]
    if len(in_range) < len(sent):
        print(f'  ℹ️ {len(sent) - len(in_range)} 天在价格缓存区间 ({lo} ~ {hi}) 之外, '
              f'A/D 真源不覆盖, 本次不判 (全量体检时即缓存起点之前那段, '
              f'回测侧由 _trim_uninformative_prefix 裁掉)')

    # 假交易日 (2026-08-04 发现): 20260501 劳动节休市, sentiment 里却有一行
    # up=1958/down=3139 —— 外部来源写进了非交易日。ZT 缓存同日也曾有 109 行涨停污染。
    fake = sorted(set(in_range['日期']) - price_dates_compact)
    if fake:
        defects.append(f'sentiment 缓存非交易日污染 {len(fake)} 天')
        print(f'  ❌ 非交易日却有数据 {len(fake)} 天 (价格缓存该日无任何行): '
              f'{", ".join(fake)}')
        print('     ➡️ 修复: 备份后删除这些行 (休市日不该有涨跌家数)')
    else:
        print('  ✅ 无非交易日污染 (价格缓存区间内每天都是真交易日)')

    # 区间内的宽度残缺都是"可修"的: 根因是价格缓存该日覆盖不足 → 先回补价格再对账。
    breadth = in_range['up'] + in_range['down']
    thin = in_range.loc[breadth < MIN_MARKET_BREADTH, ['日期', 'up', 'down']]
    if thin.empty:
        print(f'  ✅ 区间内全部交易日 up+down ≥ {MIN_MARKET_BREADTH}')
        return defects

    defects.append(f'sentiment 缓存 up+down 残缺 {len(thin)} 天 (<{MIN_MARKET_BREADTH}, 可修)')
    print(f'  ❌ up+down < {MIN_MARKET_BREADTH} 且在真源区间内 共 {len(thin)} 天:')
    show = thin if not quiet else thin.head(10)
    for _, r in show.iterrows():
        print(f'       {r["日期"]}: up={int(r["up"])} down={int(r["down"])} '
              f'(合计 {int(r["up"] + r["down"])})')
    if quiet and len(thin) > 10:
        print(f'       ... 另 {len(thin) - 10} 天')
    print('     ➡️ 修复: 先 tools/backfill_price_gap.py 补价格缓存该日覆盖, '
          '再 tools/reconcile_sentiment_ad.py --window <N> --apply')

    return defects


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--recent', type=int, default=0,
                    help='只体检最近 N 个交易日 (默认 0 = 全量)')
    ap.add_argument('--quiet', action='store_true', help='明细折叠, 只报缺陷概览')
    args = ap.parse_args()

    print('=' * 72)
    print('  数据缓存完整性体检')
    print('=' * 72)

    if not os.path.exists(PRICE_CACHE):
        sys.exit('  ❌ 价格缓存不存在, 无法体检')

    marks = check_conflict_markers(PRICE_CACHE)
    if marks:
        sys.exit(f'  ❌ 价格缓存残留 git 冲突标记 (第 {marks[0][0]} 行), 先解决冲突再体检')

    price_df = pd.read_csv(PRICE_CACHE, dtype={'code': str})
    # 新价格契约是正式生产口径；旧单列 close 缓存只用于迁移诊断，绝不再判“通过”。
    if set(PRICE_COLUMNS).issubset(price_df.columns):
        if not os.path.exists(UNIVERSE_CACHE):
            sys.exit('  ❌ stock_universe.csv 不存在，无法证明沪深北全市场覆盖')
        universe = pd.read_csv(UNIVERSE_CACHE, dtype=str).fillna('')
        dates = sorted(price_df['date'].astype(str).unique())
        if args.recent > 0:
            dates = dates[-args.recent:]
            price_df = price_df[price_df['date'].astype(str).isin(dates)]
        target_date = dates[-1]
        fetch_results = []
        if os.path.exists(FETCH_STATUS_CACHE):
            for _, row in FetchStatusStore(FETCH_STATUS_CACHE).read().iterrows():
                try:
                    fetch_results.append(FetchResult(
                        dataset=row['dataset'], date=row['date'], source=row['source'],
                        status=FetchStatus(row['status']),
                        expected_count=int(row['expected_count'] or 0),
                        actual_count=int(row['actual_count'] or 0),
                        scope=row['scope'], message=row['message'], run_id=row['run_id'],
                    ))
                except (ValueError, KeyError):
                    continue
        report = MarketDataQualityGate().validate(
            universe, price_df, target_date, fetch_results
        )
        report.write_json(QUALITY_REPORT)
        print(f'  📊 新价格契约: {len(price_df)} 行, 目标日 {target_date}')
        if not report.ok:
            print(f'  ❌ 发现 {len(report.critical)} 个严重缺陷:')
            for issue in report.critical:
                print(f'     - [{issue.code}] {issue.message}')
            sys.exit(1)
        print('  ✅ 沪深北 universe、raw/qfq 价格、来源、状态与覆盖率均通过体检')
        return

    missing_contract = sorted(set(PRICE_COLUMNS) - set(price_df.columns))
    print('  ❌ 价格缓存仍是旧单列 close 格式，禁止进入生产计算')
    print(f'     缺少字段: {", ".join(missing_contract)}')
    print('     请运行: python tools/rebuild_market_data.py')
    sys.exit(1)

    # 以下保留旧审计函数供迁移诊断和历史测试调用，不再作为生产 main 路径。
    price_df['date'] = price_df['date'].astype(str).str.strip()
    dates = sorted(price_df['date'].unique())
    if args.recent > 0:
        dates = dates[-args.recent:]
        price_df = price_df[price_df['date'].isin(dates)]
        print(f'  🔎 范围: 最近 {args.recent} 交易日')

    price_dates_compact = {_to_compact(d) for d in dates}
    defects = audit_price(price_df, dates, args.quiet)
    defects += audit_zt(price_dates_compact, args.quiet)
    defects += audit_sentiment(price_dates_compact, args.quiet)

    print('\n' + '=' * 72)
    if defects:
        print(f'  ❌ 发现 {len(defects)} 类缺陷:')
        for d in defects:
            print(f'     - {d}')
        print('=' * 72)
        sys.exit(1)
    print('  ✅ 三份缓存均通过体检')
    print('=' * 72)


if __name__ == '__main__':
    main()
