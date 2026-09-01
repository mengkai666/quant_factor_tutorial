"""数据缓存完整性体检 (可复跑, 无网络依赖)。

背景: 2026-08-03 事故暴露三类静默缺陷, 单看行数或日期范围都发现不了 ——
  1. 陈旧副本   某交易日行数满格, 但 close 整批是前一交易日的副本 (A/D 全 flat)
  2. 覆盖缺口   某交易日只抓到 30%~80% 的股票 (A/D 数值偏小但"看起来正常")
  3. 休市日污染 价格缓存无数据的日子, 涨停缓存却有涨停记录 (如 20260501 劳动节)
  4. 宽度残缺   sentiment 缓存 up+down 远低于全市场 (残缺快照被写进历史)
  5. 整天空洞   价格缓存自己缺了一个真交易日 (2026-09-01 加, 见下)

⚠️ 第 3 类的判据 2026-09-01 改过一次 (别改回去): 以前是"价格缓存无该日行 ⇒ 休市",
   而价格缓存**已经不等于交易日全集** —— CI 那份是从 git 里的切片重建的, 切片按覆盖
   门槛拒收薄天 (src/price_slices.py SLICE_MIN_COVERAGE), 所以它天生缺几个真交易日。
   实测后果: 10 个真交易日 (每天 29~109 条真涨停) 被判成休市日污染, 还附了"备份后
   删除这些行"的建议 —— 照做就是永久销毁真实记录。现在判休市**必须有正面证据**
   (见 CORROBORATING_SOURCES), 而"价格缓存缺整天"改由第 5 类对着正主报。

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
import glob
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from paths import PRICE_CACHE, ZT_CACHE_FILE, SENTIMENT_CACHE  # noqa: E402
from time_utils import filter_completed_rows, get_report_cutoff  # noqa: E402

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # pyrefly: ignore [missing-attribute]
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')  # pyrefly: ignore [missing-attribute]
except Exception:
    pass

# 全市场宽度下限: up+down 低于此值视为残缺快照。判据单一真源在 src/ad_breadth.py ——
# 体检的阈值必须和写入方一致, 否则闸门与写入方各判一套 (写进去的值体检报错, 或反之)。
from ad_breadth import MIN_MARKET_BREADTH  # noqa: E402
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


def _compact_token(value) -> str:
    """把任意日期写法收成 YYYYMMDD; 认不出来返回空串。"""
    token = str(value or '').replace('-', '').replace('/', '').strip()
    return token if len(token) == 8 and token.isdigit() else ''


# 判"这天休市"要用的**正面证据**来源: 与价格缓存彼此独立、按日增长的几份缓存。
# 为什么需要它: 价格缓存不再等于"交易日全集" (见文件头的 ⚠️), 它自己就可能缺真交易日。
# 只认正面方向 —— 某份缓存这天有行 ⇒ 这天开过市; 反过来不成立 (它们各有保留窗口,
# 见 src/cache_budget.py), 所以只有"窗口内所有来源当天都没数据"才敢判休市。
CORROBORATING_SOURCES = (
    ('cls_plate_cache.csv', 'csv'),            # 财联社板块 (日期列 date, compact)
    ('em_stock_plate_cache.csv', 'csv'),       # 东财个股概念 (日期列 date, compact)
    ('ths_sector_hist.json', 'nested_json'),   # 同花顺板块指数 {板块: [{date, ...}]}
    ('report_daily_snapshots', 'daily_dir'),   # 一天一个报告快照
    ('price_slices', 'daily_dir'),             # 进 git 的价格切片 (薄天会缺, 有=铁证)
)


def corroborating_trade_dates() -> tuple[set, dict]:
    """返回 (有独立证据"开过市"的日期集合 compact, {来源: 天数})。

    任何一份读不出来只是少一个证人, 绝不因此判休市 —— 证据越少, 下面 classify 的
    "不判"就越多, 方向永远偏安全。
    """
    from paths import DATA_DIR
    dates, detail = set(), {}
    for rel, kind in CORROBORATING_SOURCES:
        path = os.path.join(DATA_DIR, rel)
        found = set()
        try:
            if kind == 'csv':
                frame = pd.read_csv(path, encoding='utf-8-sig', dtype=str, usecols=['date'])
                found = {_compact_token(v) for v in frame['date'].unique()}
            elif kind == 'nested_json':
                with open(path, encoding='utf-8') as handle:
                    blob = json.load(handle)
                found = {_compact_token(row.get('date'))
                         for series in blob.values() if isinstance(series, list)
                         for row in series if isinstance(row, dict)}
            else:                                          # 一天一个文件的目录
                found = {_compact_token(os.path.basename(f).split('.')[0])
                         for f in glob.glob(os.path.join(path, '*'))}
        except Exception:
            continue
        found.discard('')
        if found:
            detail[rel] = len(found)
            dates |= found
    return dates, detail


def classify_absent_days(days, evidence: set) -> tuple[list, list, list]:
    """把"价格缓存整天没有行"的日子分三类: (确认休市, 真交易日, 无证据不判)。

    第三类是证据窗口之前的老日子: 那几份对证缓存自己有保留窗口, 窗口之前既证不出
    开市也证不出休市。宁可不判, 不可误判成休市 —— 误判的代价是"建议删掉真实记录"。
    """
    days = sorted(str(d) for d in days)
    if not evidence:
        return [], [], days
    floor = min(evidence)
    holiday, trading, unknown = [], [], []
    for day in days:
        if day in evidence:
            trading.append(day)
        elif day >= floor:
            holiday.append(day)
        else:
            unknown.append(day)
    return holiday, trading, unknown


def audit_price_gaps(price_dates_compact: set, evidence: set, quiet: bool) -> list:
    """价格缓存自己缺的整天: 有独立证据开过市, 而缓存区间内一行都没有。

    这是第 3 类判据反过来找对了正主。2026-09-01 CI 的形状: 薄天 (覆盖 3769~4056 只)
    被切片门槛拒收 → 从切片重建的那份缓存缺这 10 天 → 涨停/sentiment 里那 10 天的
    真实记录反被判成"污染"。缺的是价格缓存, 该补的也是它。
    补法优先级 (memory price-cache-whole-day-recovery): 切片 > .bak 备份 > 联网重抓,
    且只补整天。
    """
    defects = []
    if not price_dates_compact:
        return defects
    if not evidence:
        print()
        print('  ⚠️ 没有任何对证缓存可读, 无法判断价格缓存缺哪些交易日, 跳过')
        return defects
    lo, hi = min(price_dates_compact), max(price_dates_compact)
    gaps = sorted(d for d in evidence if lo <= d <= hi and d not in price_dates_compact)
    print()
    print(f'  📊 对证交易日: {len(evidence)} 天 ({min(evidence)} ~ {max(evidence)})')
    if not gaps:
        print('  ✅ 价格缓存区间内无整天空洞 (每个有独立证据的交易日都有行)')
        return defects
    defects.append(f'价格缓存缺 {len(gaps)} 个交易日 (区间内整天无行)')
    head = gaps if not quiet else gaps[:10]
    print(f'  ❌ 价格缓存缺 {len(gaps)} 个交易日 (其它缓存证明这几天开过市): '
          f'{", ".join(head)}{" ..." if quiet and len(gaps) > 10 else ""}')
    print('     ➡️ 修复: python tools/sync_price_slices.py (切片) → '
          'tools/import_legacy_price_backup.py --apply (.bak 备份) → '
          'tools/backfill_price_history.py --repair-days <这些天> --apply (联网重抓)')
    return defects


def _price_value_column(price_df: pd.DataFrame) -> tuple[str | None, bool]:
    """Return the raw close column and whether the cache is legacy.

    ``close_raw`` is the only authoritative value for stale-copy and value-range
    checks.  The ``close`` fallback is retained only so the audit can explain an
    old cache clearly instead of crashing with a KeyError.
    """
    if 'close_raw' in price_df.columns:
        return 'close_raw', False
    if 'close' in price_df.columns:
        return 'close', True
    return None, False


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
    close_col, legacy_schema = _price_value_column(price_df)
    if close_col is None:
        defects.append('价格缓存缺少 close_raw（无法审计 raw 收盘价）')
        print('\n  ❌ 价格缓存缺少 close_raw, 无法进行 raw 收盘价体检')
        return defects
    if legacy_schema:
        defects.append('价格缓存仍使用旧 close 字段（未迁移到 close_raw）')
        print('  ❌ 价格缓存仍使用旧 close 字段；主链路只接受 close_raw')

    raw_values = pd.to_numeric(
        price_df['close_raw'] if 'close_raw' in price_df.columns else price_df['close'],
        errors='coerce',
    )
    legacy_values = pd.to_numeric(price_df.get('close_legacy'), errors='coerce')
    valid_raw = raw_values.notna() & (raw_values > 0)
    valid_legacy = legacy_values.notna() & (legacy_values > 0)
    if 'close_qfq' in price_df.columns:
        qfq_values = pd.to_numeric(price_df['close_qfq'], errors='coerce')
        qfq_missing = int(qfq_values.isna().sum())
        print(f'  ℹ️ qfq 可选列: {len(qfq_values) - qfq_missing}/{len(qfq_values)} 行有效, '
              f'{qfq_missing} 行缺失不影响 raw 主链路')
    else:
        print('  ℹ️ qfq 可选列不存在；本次只审计 raw 主链路')

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

    raw_counts = price_df.loc[valid_raw].groupby('date')['code'].nunique()
    raw_dates = [d for d in dates if int(raw_counts.get(d, 0)) > 0]
    if raw_dates:
        raw_baseline = int(raw_counts.loc[raw_dates].median())
        print(f'  ℹ️ raw 收盘覆盖: {len(raw_dates)}/{len(dates)} 天有 raw, 中位 {raw_baseline} 只/日')
        # 分母是**当天自己的行数**, 不是窗口中位数。证券口径台阶式扩容
        # (~4604 → 5199 @2026-07-06 → 5538 @2026-08-06), 拿跨台阶的中位数当分母, 台阶
        # 下方的老日子会整段被判 raw 不足 —— 2026-09-01 CI 实测把 20260805 (当天 5204 行
        # 里 4737 只有 raw, 口径内 91%) 报成 86% 不足。"这天比同侪薄"由上面的覆盖缺口
        # 判据管 (它有 --recent 收窄来避台阶), 这里只问"当天已知的股票有多少拿到 raw"。
        raw_thin = [(d, int(raw_counts.get(d, 0)), int(counts[d])) for d in raw_dates
                    if int(raw_counts.get(d, 0)) < int(counts[d]) * MIN_COVERAGE_RATIO]
        if raw_thin:
            defects.append(f'raw 收盘覆盖不足 {len(raw_thin)} 天 (<当日行数的 {MIN_COVERAGE_RATIO:.0%})')
            print(f'  ❌ raw 覆盖不足 {len(raw_thin)} 天:')
            for d, n, total in (raw_thin if not quiet else raw_thin[:10]):
                print(f'       {d}: {n}/{total} 只有 raw ({n / total:.0%})')
        else:
            print(f'  ✅ raw 覆盖: 每天 ≥{MIN_COVERAGE_RATIO:.0%} 的当日行数有 raw 收盘')
        # raw 段内整天 0 raw: 那天的 raw 抓取全军覆没, 只剩 legacy 兼容值撑着行数,
        # 覆盖缺口判据看不见 (行数是满的), 上面的比例判据也看不见 (被 >0 过滤掉了)。
        blind = [d for d in dates if raw_dates[0] <= d <= raw_dates[-1]
                 and int(raw_counts.get(d, 0)) == 0]
        if blind:
            defects.append(f'价格缓存 {len(blind)} 天在 raw 段内却无任何 raw 收盘')
            print(f'  ❌ raw 段 ({raw_dates[0]} ~ {raw_dates[-1]}) 内 {len(blind)} 天没有一行 raw: '
                  f'{", ".join(blind if not quiet else blind[:10])}')
    else:
        print('  ⚠️ raw 收盘在当前窗口没有有效行；只能审计 legacy 历史兼容值')

    # --- 2. 陈旧副本 (逐股同口径收盘价身份比对) ---
    basis_by_date = {
        d: str(price_df.loc[price_df['date'] == d, 'price_basis'].mode().iloc[0])
        if 'price_basis' in price_df.columns and not price_df.loc[price_df['date'] == d, 'price_basis'].dropna().empty
        else ''
        for d in dates
    }
    close_by_date = {}
    for d in dates:
        day_mask = price_df['date'] == d
        use_legacy = 'price_basis' in price_df.columns and basis_by_date[d] == 'legacy_mixed'
        values, valid = (legacy_values, valid_legacy) if use_legacy else (raw_values, valid_raw)
        mask = day_mask & valid
        close_by_date[d] = dict(zip(price_df.loc[mask, 'code'], values.loc[mask]))
    stale, ratios = [], []
    for prev, cur in zip(dates, dates[1:]):
        if basis_by_date.get(cur) != basis_by_date.get(prev):
            continue
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
    raw_present = raw_values.notna()
    legacy_present = legacy_values.notna()
    bad_raw = int((raw_present & ~valid_raw).sum())
    bad_legacy = int((legacy_present & ~valid_legacy).sum())
    dup = int(price_df.duplicated(subset=['code', 'date']).sum())
    if bad_raw:
        defects.append(f'价格缓存 close_raw<=0 {bad_raw} 行')
        print(f'  ❌ close_raw<=0: {bad_raw} 行')
    if bad_legacy:
        defects.append(f'价格缓存 close_legacy<=0 {bad_legacy} 行')
        print(f'  ❌ close_legacy<=0: {bad_legacy} 行')
    if dup:
        defects.append(f'价格缓存 (code,date) 重复 {dup} 行')
        print(f'  ❌ (code,date) 重复: {dup} 行')
    if not bad_raw and not bad_legacy and not dup:
        print('  ✅ 值域与唯一性: 已有 raw/legacy 数值均 >0, (code,date) 无重复')

    return defects


def audit_zt(price_dates_compact: set, evidence: set, quiet: bool) -> list:
    """涨停缓存: 休市日污染 (对证缓存也证不出这天开过市, 却有涨停记录)。"""
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
    # 价格缓存没这天 ≠ 这天休市 (见文件头 ⚠️): 先拿对证缓存分类, 只有"谁都证不出
    # 开过市"才算污染, 也只有那一类才配"删行"的建议。
    holiday, real_trading, unknown = classify_absent_days(orphan, evidence)
    if holiday:
        rows = {d: int((zt['日期'] == d).sum()) for d in holiday}
        defects.append(f'涨停缓存非交易日污染 {len(holiday)} 天')
        print(f'  ❌ 休市日却有涨停记录 {len(holiday)} 天 (价格缓存与所有对证缓存当天均无数据):')
        for d in holiday:
            print(f'       {d}: {rows[d]} 行')
        print('     ➡️ 修复: 备份后删除这些行 (休市日不该有涨停记录)')
    else:
        print('  ✅ 无休市日污染 (没有一个涨停日被证明是休市日)')
    if real_trading:
        head = real_trading if not quiet else real_trading[:10]
        print(f'  ℹ️ {len(real_trading)} 天价格缓存无行但**是真交易日** (其它缓存当天有数据), '
              f'涨停记录不是污染, 缺的是价格缓存 (见上面"价格缓存缺整天"): '
              f'{", ".join(head)}{" ..." if quiet and len(real_trading) > 10 else ""}')
    if unknown:
        print(f'  ℹ️ {len(unknown)} 天在对证缓存的保留窗口之前, 既证不出开市也证不出休市, 本次不判')

    # 陈旧快照 / 残缺名单 (2026-08-27 加)。涨停池接口只对当日有效, 盘前跑或失败重试
    # 会把前一交易日的名单写到当日名下。这一判据不需要价格真源, 所以能覆盖价格缓存
    # 区间之外的老日子 (实测 20260317 就是靠它现形的)。两条指标, 均只在两日名单都
    # >= 20 只时才判:
    #   ① Jaccard >= 0.95  => 与前一日逐条相同, 就是陈旧快照 (正常相邻日中位 0.12,
    #      99 分位 0.47);
    #   ② 当日名单 100% 落在前一日名单内 => 该日只剩连板股、首板全无, 是修复后的
    #      残缺名单或被截断的抓取 (正常相邻日该比例中位 0.21, 90 分位 0.36)。
    zt_only = zt[zt.get('类型', 'ZT').astype(str).str.strip() == 'ZT'] if '类型' in zt.columns else zt
    lists = {d: set(g['代码'].astype(str).str.strip())
             for d, g in zt_only.groupby('日期')}
    dup_days, residual_days = [], []
    for index in range(1, len(zt_dates)):
        today, prev = zt_dates[index], zt_dates[index - 1]
        cur, before = lists.get(today, set()), lists.get(prev, set())
        if len(cur) < 20 or len(before) < 20:
            continue
        union = cur | before
        if union and len(cur & before) / len(union) >= 0.95:
            dup_days.append(f'{today}(=前一日 {prev})')
        elif cur and cur <= before:
            residual_days.append(f'{today}({len(cur)} 只全在 {prev} 名单内)')
    if dup_days:
        defects.append(f'涨停缓存陈旧快照 {len(dup_days)} 天')
        print(f'  ❌ 名单与前一交易日逐条相同 {len(dup_days)} 天 (陈旧快照, '
              f'真名单已丢失): {", ".join(dup_days)}')
    else:
        print('  ✅ 无陈旧快照 (无一天名单与前一交易日逐条相同)')
    if residual_days:
        head = residual_days if not quiet else residual_days[:6]
        print(f'  ℹ️ 名单全部落在前一日名单内 {len(residual_days)} 天 (只剩连板股、'
              f'首板缺失, 修复留下的残缺名单): {", ".join(head)}'
              + (' ...' if quiet and len(residual_days) > 6 else ''))


    missing = sorted(price_dates_compact - set(zt_dates))
    if missing:
        # 不算缺陷: 涨停池接口只对当日有效, 历史日无法回补 (见 memory)
        head = missing if not quiet else missing[:8]
        print(f'  ℹ️ 价格缓存有但涨停缓存缺 {len(missing)} 天 (涨停池接口不支持历史回补): '
              f'{", ".join(head)}{" ..." if quiet and len(missing) > 8 else ""}')

    return defects


def audit_sentiment(price_dates_compact: set, evidence: set, quiet: bool) -> list:
    """sentiment 缓存: 休市日污染 + up/down 宽度残缺 + 编码 BOM。"""
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
    absent = sorted(set(in_range['日期']) - price_dates_compact)
    # 同 audit_zt: "价格缓存该日无行"只是**这份副本**没有, 不等于休市 (见文件头 ⚠️)。
    holiday, real_trading, unknown = classify_absent_days(absent, evidence)
    if holiday:
        defects.append(f'sentiment 缓存非交易日污染 {len(holiday)} 天')
        print(f'  ❌ 休市日却有涨跌家数 {len(holiday)} 天 (价格缓存与所有对证缓存当天均无数据): '
              f'{", ".join(holiday)}')
        print('     ➡️ 修复: 备份后删除这些行 (休市日不该有涨跌家数)')
    else:
        print('  ✅ 无休市日污染 (没有一天被证明是休市日)')
    if real_trading:
        head = real_trading if not quiet else real_trading[:10]
        print(f'  ℹ️ {len(real_trading)} 天价格缓存无行但**是真交易日**, 这些行不是污染: '
              f'{", ".join(head)}{" ..." if quiet and len(real_trading) > 10 else ""}')
    if unknown:
        print(f'  ℹ️ {len(unknown)} 天在对证缓存的保留窗口之前, 既证不出开市也证不出休市, 本次不判')

    # 区间内的宽度残缺都是"可修"的, 但根因有两种, 修法不同:
    #   (a) 该日价格缓存行数不足 → 补覆盖;
    #   (b) 该日有行, 但与前一交易日**没有共同的价格口径** (raw/qfq/legacy 按日成片,
    #       交界日无法同口径相减, 见 _compute_ad_cache) → 要补的是那一列收盘价本身,
    #       补 qfq 也行, 只要与前一交易日同列都有值。
    # 提示语按实际情形分开给, 别一律指向"补覆盖" —— (b) 类日子覆盖是满的。
    breadth = in_range['up'] + in_range['down']
    thin = in_range.loc[breadth < MIN_MARKET_BREADTH, ['日期', 'up', 'down']]
    # 价格缓存整天没有行的日子: **这份副本**里没有 A/D 真源, 既判不出宽度对不对, 也
    # 修不了 (reconcile 无从下手)。根因由 audit_price_gaps 对着价格缓存报, 这里只挂个
    # 指路牌 —— 否则同一个洞报两次, 且第二次给的药方是错的。
    blind = [d for d in thin['日期'] if d not in price_dates_compact]
    if blind:
        head = blind if not quiet else blind[:10]
        print(f'  ℹ️ {len(blind)} 天价格缓存整天无行, 宽度无真源可比, 本次不判 '
              f'(根因见"价格缓存缺整天"): {", ".join(head)}'
              f'{" ..." if quiet and len(blind) > 10 else ""}')
        thin = thin.loc[~thin['日期'].isin(blind)]
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
    no_shared_basis = [str(r['日期']) for _, r in thin.iterrows()
                       if str(r['日期']) in price_dates_compact]
    if no_shared_basis:
        print(f'     ℹ️ 其中 {len(no_shared_basis)} 天价格缓存**有覆盖**, 残缺来自'
              f'"与前一交易日无共同价格口径": {", ".join(no_shared_basis[:10])}')
        print('     ➡️ 修复: tools/backfill_price_history.py --repair-days <这些天> '
              '--apply 把该段补出与前一交易日同列的收盘价, 再 reconcile')
    if len(no_shared_basis) < len(thin):
        print('     ➡️ 修复(覆盖不足那几天): 先 tools/backfill_price_gap.py 补价格缓存'
              '该日覆盖, 再 tools/reconcile_sentiment_ad.py --window <N> --apply')

    return defects


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--recent', type=int, default=0,
                    help='只体检最近 N 个交易日 (默认 0 = 全量)')
    ap.add_argument('--quiet', action='store_true', help='明细折叠, 只报缺陷概览')
    ap.add_argument('--as-of', default=None,
                    help='报告截止日 YYYY-MM-DD/YYYYMMDD；排除该日之后的缓存行')
    args = ap.parse_args()

    print('=' * 72)
    print('  数据缓存完整性体检')
    print('=' * 72)

    if not os.path.exists(PRICE_CACHE):
        sys.exit('  ❌ 价格缓存不存在, 无法体检')

    marks = check_conflict_markers(PRICE_CACHE)
    if marks:
        sys.exit(f'  ❌ 价格缓存残留 git 冲突标记 (第 {marks[0][0]} 行), 先解决冲突再体检')

    try:
        cutoff = get_report_cutoff(report_date=args.as_of)
    except ValueError as exc:
        sys.exit(f'  ❌ {exc}')

    price_df = pd.read_csv(PRICE_CACHE, dtype={'code': str})
    required = {'code', 'date'}
    missing_required = sorted(required - set(price_df.columns))
    if missing_required:
        sys.exit(f'  ❌ 价格缓存缺少必要列: {", ".join(missing_required)}')
    price_df['date'] = price_df['date'].astype(str).str.strip()
    before_rows = len(price_df)
    price_df = filter_completed_rows(price_df, 'date', report_date=cutoff.isoformat())
    if len(price_df) < before_rows:
        print(f'  🕒 截止日: {cutoff.isoformat()} (过滤未来缓存 {before_rows - len(price_df)} 行)')
    all_dates = sorted(price_df['date'].unique())
    if not all_dates:
        sys.exit(f'  ❌ 截止 {cutoff.isoformat()} 前没有可审计的价格缓存')
    dates, price_window = all_dates, price_df
    if args.recent > 0:
        dates = all_dates[-args.recent:]
        price_window = price_df[price_df['date'].isin(dates)]
        print(f'  🔎 价格覆盖体检范围: 最近 {args.recent} 交易日 '
              f'(ZT / sentiment 的绝对判据仍走全区间 {all_dates[0]} ~ {all_dates[-1]})')

    # ⚠️ --recent 只许收窄 audit_price。它存在的唯一理由是**覆盖率基准是窗口内行数中位数**,
    #    而证券口径是台阶式扩容的 (~4604 → 5199 @2026-07-06 → 5538 @2026-08-06), 窗口一跨
    #    台阶就把老口径整段判成"覆盖不足", 闸门永远红 = 等于没有闸门。
    #    audit_zt / audit_sentiment 判的是**绝对**条件 (休市日却有行 / up+down < 4000),
    #    没有台阶问题, 收窄它们只会把老缺陷藏起来 —— 2026-08-31 实测 origin/master 的
    #    sentiment 缓存有 64 天 up+down≈800 (CI 拿自己那份 ~840 只宽的价格缓存算的 A/D),
    #    全部早于自适应窗口 (近 18 天), 收窄后一天都报不出来。
    price_dates_compact = {_to_compact(d) for d in all_dates}
    evidence, evidence_detail = corroborating_trade_dates()
    defects = audit_price(price_window, dates, args.quiet)
    defects += audit_price_gaps(price_dates_compact, evidence, args.quiet)
    if evidence_detail and not args.quiet:
        print('     证人: ' + ', '.join(f'{k}({v}天)' for k, v in evidence_detail.items()))
    defects += audit_zt(price_dates_compact, evidence, args.quiet)
    defects += audit_sentiment(price_dates_compact, evidence, args.quiet)

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
