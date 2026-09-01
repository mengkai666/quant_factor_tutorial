# -*- coding: utf-8 -*-
"""从历史备份 (`data/price_history_cache.csv.bak.*`) 找回被裁掉的**整个交易日**。

要解决什么:
    价格缓存的自动抓取起点是 `max(date) + 1 天`, 往回最多退一个交易日 (补 A/D 配对)。
    也就是说**前段被裁掉的日子永远不会自己回来** —— 2026-09-01 实测: 日历里
    2025-11-04 ~ 2026-08-31 有 202 个交易日, 缓存只剩 108 天, 缺的 94 天
    (2025-11-04 ~ 2026-03-25) 整段消失, 而 memory 里 179/203 天的回测窗口就在这段。

    回补脚本 (`tools/backfill_price_history.py`) 逐股重抓要以小时计, 但这些天其实
    还躺在 backfill 自己留下的 `.bak.<时间戳>` 里 —— 零网络就能拿回来。

两条硬规则 (别改):
  1. **只补整天**。备份是**老 schema** (`date,code,close`, 无 raw/qfq 之分), 落库只能
     记成 `close_legacy` + `price_basis='legacy_mixed'`。往一个已有 raw 行的日子里掺
     legacy 行, 会把那天整体降级成 legacy_mixed (见 limit_ratio_factor 的 basis 投票
     和 report_logic 的降级披露) —— 用一天的口径污染换几百只覆盖, 不值。
  2. **本地优先**。备份往往早于后来的对账修复 (如 2026-08-04 修 07-10/16/21/31 的
     陈旧副本污染), 让它覆盖本地等于把修复冲掉。同 [[price-slices-shared-lineage]]。

代价 (可接受): legacy 段与 raw 段的交界日无共同价格口径, A/D 判"未覆盖", 损失一天。

两路 (治的是两种病, 共用"只碰 legacy 天 + 本地优先"这两条硬规则):
  · 默认路 **补整天**   —— 缓存里整天没有的日子, 从备份整天搬回来。
  · --widen-thin **补宽** —— 天在, 但只有 3769~3905 只 (全市场 ~5000)。薄天会被
    切片门槛 (SLICE_MIN_COVERAGE=4000) 拒收 → 进不了 git → CI 的缓存天生缺这几天 →
    CI 侧体检把真交易日误判成休市日污染, sentiment 的 up+down 也同批残缺。
    详见 widen_thin_days 的 docstring。

用法:
    python tools/import_legacy_price_backup.py                    # 体检: 列出能补哪些天
    python tools/import_legacy_price_backup.py --apply            # 落盘 (先自动备份)
    python tools/import_legacy_price_backup.py --backup <path>    # 指定备份
    python tools/import_legacy_price_backup.py --min-coverage 4000  # 只补够宽的天
    python tools/import_legacy_price_backup.py --widen-thin        # 体检: 哪些薄天能补宽
    python tools/import_legacy_price_backup.py --widen-thin --apply
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from console_io import enable_utf8_console  # noqa: E402

enable_utf8_console()

from paths import DATA_DIR, PRICE_CACHE  # noqa: E402
from audit_data_integrity import MIN_COVERAGE_RATIO  # noqa: E402  (覆盖判据同一真源)

NEW_COLS = ['date', 'code', 'close_raw', 'close_qfq', 'close_legacy',
            'price_basis', 'source', 'source_timestamp']
LEGACY_BASIS = 'legacy_mixed'


def _candidates() -> list:
    return sorted(glob.glob(PRICE_CACHE + '.bak.*'))


def _load_backup(path: str) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={'code': str, 'date': str}, low_memory=False)
    frame['date'] = frame['date'].astype(str).str.strip()
    frame['code'] = frame['code'].astype(str).str.strip()
    return frame


def _close_column(frame: pd.DataFrame) -> str | None:
    """老备份是 `close`; 新 schema 的备份优先用 close_raw。"""
    for col in ('close_raw', 'close', 'close_legacy'):
        if col in frame.columns and frame[col].notna().any():
            return col
    return None


def pick_backup(paths: list, missing: set) -> tuple:
    """挑覆盖缺失日最多的那份备份, 返回 (path, 能补的日期集合)。"""
    best, best_days = None, set()
    for path in paths:
        try:
            dates = set(pd.read_csv(path, usecols=['date'], dtype=str)['date'].astype(str).str.strip())
        except Exception:
            continue
        hit = dates & missing
        if len(hit) > len(best_days):
            best, best_days = path, hit
    return best, best_days


def to_new_schema(chunk: pd.DataFrame, close_col: str, tag: str) -> pd.DataFrame:
    out = pd.DataFrame({
        'date': chunk['date'].astype(str),
        'code': chunk['code'].astype(str),
        'close_raw': pd.NA,
        'close_qfq': pd.NA,
        'close_legacy': pd.to_numeric(chunk[close_col], errors='coerce'),
        'price_basis': LEGACY_BASIS,
        'source': f'legacy_backup:{tag}',
        'source_timestamp': chunk['date'].astype(str).str.replace('-', '', regex=False),
    })
    return out.loc[out['close_legacy'].notna() & (out['close_legacy'] > 0), NEW_COLS]


def _day_profile(cur: pd.DataFrame) -> pd.DataFrame:
    """每天的 (证券数, raw 行数)。判"薄天"看前者, 判"这天能不能掺 legacy"看后者。"""
    raw = cur['close_raw'] if 'close_raw' in cur.columns else pd.Series(pd.NA, index=cur.index)
    prof = pd.DataFrame({
        'codes': cur.groupby('date')['code'].nunique(),
        'raw_rows': pd.to_numeric(raw, errors='coerce').notna().groupby(cur['date']).sum(),
    })
    return prof.fillna(0).astype(int)


def _write_back(cur: pd.DataFrame, add: pd.DataFrame) -> pd.DataFrame:
    """本地优先合并后落盘 (先自动备份)。cur 放前面 → keep='first' 就是本地赢。"""
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safety = f'{PRICE_CACHE}.bak.{stamp}'
    shutil.copy2(PRICE_CACHE, safety)
    print(f'💾 已备份当前缓存: {os.path.basename(safety)}')
    merged = pd.concat([cur, add], ignore_index=True)
    merged = merged.drop_duplicates(subset=['code', 'date'], keep='first')
    merged = merged.sort_values(['date', 'code']).reset_index(drop=True)
    merged.to_csv(PRICE_CACHE, index=False)
    return merged


def widen_thin_days(cur: pd.DataFrame, paths: list, threshold: int, apply_: bool) -> int:
    """把**已有但覆盖过薄**的 legacy 天补宽到备份的全市场口径。

    为什么要有这一路 (和"补整天"是两件事):
        整天缺失的日子由 main() 那路补。这里治的是另一种病 —— 天在, 但只有 3769~3905
        只 (全市场 ~5000)。薄天的连锁反应是静默的:
          ① export_slices 按 SLICE_MIN_COVERAGE=4000 拒收 → 这天永远进不了 git 切片;
          ② CI 的价格缓存是从切片重建的 → CI 天生没有这几天;
          ③ 于是 CI 侧的体检拿"价格缓存整天无行"推出"这天休市", 把真涨停记录报成
             非交易日污染并建议删行 (2026-09-01 实测 10 天), 而 sentiment 的 up+down
             也因为没有 A/D 真源而残缺 (同一批 10 天)。
        补宽 = 从源头解掉这一整条链, 比在下游给每个判据打补丁划算。

    三条闸门 (缺一不可):
      1. **只碰纯 legacy 天** —— 有任何 raw 行的天一律跳过 (掺 legacy 会把整天口径
         降级成 legacy_mixed, 见文件头硬规则 1)。
      2. **同血统才补** —— 逐行比对重叠的 (date,code): 备份与本地的收盘价必须一致
         (容差 1e-9)。不一致说明这份备份是修复前/另一条口径的快照, 往里掺它的证券
         会让同一天内部不自洽, 而 A/D 是逐股做隔日差分的 (见 [[ad-price-basis-pairing]])。
      3. **新增证券必须在 legacy 段里已有历史** —— 否则这只股票在本段内孤立一天,
         隔日差分永远配不上对, 白占行数。
    """
    prof = _day_profile(cur)
    # 判薄要**两条线取严**:
    #   · 绝对线 (threshold, 默认 MIN_MARKET_BREADTH=4000): 低于它切片直接拒收,
    #     A/D 也判残缺 —— 这是"能不能用"的硬线。
    #   · 相对线 (段内中位覆盖 × MIN_COVERAGE_RATIO): A/D 是**隔日逐股配对**, 一个
    #     4043 只的天会把它前后两个 pair 一起拖下去 (实测 20260310 配对只剩 3369,
    #     而它自己 4036 只"过了"绝对线)。所以薄要对着段内的全市场口径判。
    legacy_days = prof.loc[prof['raw_rows'] == 0]
    floor = threshold
    if len(legacy_days) >= 20:
        relative = int(MIN_COVERAGE_RATIO * legacy_days['codes'].median())
        if relative > floor:
            print(f'📏 判薄下限取严: max(绝对 {threshold}, 段内中位 '
                  f'{int(legacy_days["codes"].median())} × {MIN_COVERAGE_RATIO:.0%} = {relative})')
            floor = relative
    # 只在 legacy 段里判薄: 相对线是拿 legacy 段的中位数算的, 套到 raw 段 (口径
    # 4588~5538, 中间还有两级扩容台阶) 会把一大片正常的天误报成薄。raw 段的薄天
    # 这个工具本来也修不了 (硬规则 1), 只对**绝对线**以下的报一句, 指去联网重抓。
    targets = sorted(legacy_days.index[legacy_days['codes'] < floor])
    raw_broken = sorted(prof.index[(prof['raw_rows'] > 0) & (prof['codes'] < threshold)])
    if raw_broken:
        print(f'  ⏭️ {len(raw_broken)} 天含 raw 行且覆盖 <{threshold}, 本工具不碰 '
              f'(硬规则 1), 需 tools/backfill_price_history.py --repair-days: '
              f'{", ".join(raw_broken[:8])}{" ..." if len(raw_broken) > 8 else ""}')
    print(f'🔍 legacy 段 {len(legacy_days)} 天, 其中覆盖 <{floor} 只的薄天 {len(targets)} 天')
    if not targets:
        print('✅ legacy 段没有薄天, 无需补宽')
        return 0

    # 挑"对这些天能多给出行数最多"的那份备份。
    best, best_path, best_gain = None, None, 0
    cur_keys = set(zip(cur['date'], cur['code']))
    for path in paths:
        try:
            frame = _load_backup(path)
        except Exception:
            continue
        sub = frame.loc[frame['date'].isin(targets)]
        if sub.empty or _close_column(sub) is None:
            continue
        gain = len(set(zip(sub['date'], sub['code'])) - cur_keys)
        if gain > best_gain:
            best, best_path, best_gain = sub, path, gain
    if best is None or best_gain <= 0:
        print(f'⚠️ {len(paths)} 份备份都补不宽这 {len(targets)} 天, 只能走 '
              'tools/backfill_price_history.py --repair-days ... 联网重抓')
        return 0

    tag = os.path.basename(best_path).split('.bak.')[-1]
    close_col = _close_column(best)
    print(f'📦 选用备份: {os.path.basename(best_path)} (收盘价列 {close_col}, '
          f'可多给 {best_gain} 行)')

    legacy_codes = set(cur.loc[cur['price_basis'] == LEGACY_BASIS, 'code'])
    local = cur.loc[cur['date'].isin(targets), ['date', 'code', 'close_legacy']].copy()
    local['local_v'] = pd.to_numeric(local['close_legacy'], errors='coerce')
    best = best.copy()
    best['bak_v'] = pd.to_numeric(best[close_col], errors='coerce')

    keep_days, rows, notes = [], [], []
    for day in targets:
        bak_day = best.loc[best['date'] == day]
        loc_day = local.loc[local['date'] == day]
        pair = loc_day.merge(bak_day[['date', 'code', 'bak_v']], on=['date', 'code'], how='inner')
        bad = int(((pair['local_v'] - pair['bak_v']).abs() > 1e-9).sum())
        if bad:                                        # 闸门 2: 血统不同, 整天不碰
            notes.append(f'{day}: 重叠 {len(pair)} 行里 {bad} 行收盘价与本地不一致, 跳过 (非同血统)')
            continue
        new = bak_day.loc[~bak_day['code'].isin(set(loc_day['code']))]
        orphan = sorted(set(new['code']) - legacy_codes)
        if orphan:                                     # 闸门 3: 只丢这些股, 天照补
            notes.append(f'{day}: {len(orphan)} 只在 legacy 段内没有历史, 已剔除 '
                         f'({", ".join(orphan[:5])}{" ..." if len(orphan) > 5 else ""})')
            new = new.loc[new['code'].isin(legacy_codes)]
        if new.empty:
            notes.append(f'{day}: 备份没有本地之外的证券, 跳过')
            continue
        keep_days.append(day)
        rows.append(new)
    for note in notes:
        print(f'  ℹ️ {note}')
    if not rows:
        print('ℹ️ 三条闸门过后没有可补宽的天')
        return 0

    add = to_new_schema(pd.concat(rows, ignore_index=True), close_col, tag)
    gain = add['date'].value_counts()
    print(f'  ➕ 补宽 {len(keep_days)} 天 / {len(add)} 行:')
    for day in keep_days:
        before = int(prof.loc[day, 'codes'])
        after = before + int(gain.get(day, 0))
        flag = '✅' if after >= floor else '⚠️ 仍不足'
        print(f'       {day}: {before} → {after} 只 {flag}')
    below = [d for d in keep_days if int(prof.loc[d, 'codes']) + int(gain.get(d, 0)) < floor]
    if below:
        print(f'     ⚠️ {len(below)} 天补宽后仍 <{floor}, 覆盖仍不达段内口径 (需联网重抓)')

    if not apply_:
        print('🔍 体检模式, 未写盘。加 --apply 落盘。')
        return 0
    merged = _write_back(cur, add)
    print(f'✅ 已写回: {len(cur)} → {len(merged)} 行 (天数不变 {merged["date"].nunique()})')
    print('➡️ 下一步: python tools/reconcile_sentiment_ad.py --window '
          f'{merged["date"].nunique()} --apply    (让补宽日的 up/down 重算)')
    print('➡️ 再: python tools/sync_price_slices.py --export --days 150'
          '    (让补宽日进 git 切片, CI 才拿得到)')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--backup', default=None, help='备份文件路径 (默认自动挑覆盖最多的一份)')
    ap.add_argument('--min-coverage', type=int, default=0,
                    help='某天在备份里不足这么多只就不补 (默认 0 = 都补)')
    ap.add_argument('--apply', action='store_true', help='真正写盘 (默认只体检)')
    ap.add_argument('--widen-thin', action='store_true',
                    help='另一路: 不补整天, 而是把已有的薄 legacy 天补宽到全市场口径')
    ap.add_argument('--threshold', type=int, default=None,
                    help='--widen-thin 的薄天判据 (默认取 ad_breadth.MIN_MARKET_BREADTH)')
    args = ap.parse_args()

    if not os.path.exists(PRICE_CACHE):
        print(f'❌ 价格缓存不存在: {PRICE_CACHE}')
        return 1

    cur = pd.read_csv(PRICE_CACHE, dtype={'code': str, 'date': str}, low_memory=False)
    cur['date'] = cur['date'].astype(str).str.strip()
    cur_dates = set(cur['date'])
    print(f'📂 当前缓存: {len(cur)} 行, {len(cur_dates)} 天, '
          f'{min(cur_dates)} ~ {max(cur_dates)}')

    paths_all = [args.backup] if args.backup else _candidates()
    if args.widen_thin:
        if not paths_all:
            print(f'ℹ️ 没有找到任何备份 ({PRICE_CACHE}.bak.*), 无事可做')
            return 0
        threshold = args.threshold
        if threshold is None:
            from ad_breadth import MIN_MARKET_BREADTH
            threshold = MIN_MARKET_BREADTH      # 与切片门槛同一个数, 不再写死第二份
        return widen_thin_days(cur, paths_all, threshold, args.apply)

    cal_path = os.path.join(DATA_DIR, 'trading_calendar_cache.csv')
    if not os.path.exists(cal_path):
        print(f'❌ 交易日历缓存不存在, 无法判断缺哪些天: {cal_path}')
        return 1
    cal = pd.read_csv(cal_path, dtype=str)['trade_date'].astype(str).str.strip()

    paths = paths_all
    if not paths:
        print(f'ℹ️ 没有找到任何备份 ({PRICE_CACHE}.bak.*), 无事可做')
        return 0

    # 缺失日的搜索区间: 从"任一备份的最早日"到"当前缓存最新日"。
    # 不用日历全程 —— 那会把 1990 年以来全算成缺失。
    earliest = min(cur_dates)
    for path in paths:
        try:
            head = pd.read_csv(path, usecols=['date'], dtype=str)['date'].astype(str).str.strip().min()
            earliest = min(earliest, head)
        except Exception:
            continue
    window = [d for d in cal if earliest <= d <= max(cur_dates)]
    missing = {d for d in window if d not in cur_dates}
    print(f'📅 日历区间 {earliest} ~ {max(cur_dates)}: {len(window)} 个交易日, '
          f'缓存缺 {len(missing)} 天')
    if not missing:
        print('✅ 区间内无整天缺失, 无需导入')
        return 0

    path, hit = pick_backup(paths, missing) if not args.backup else (paths[0], None)
    if args.backup:
        hit = set(pd.read_csv(paths[0], usecols=['date'], dtype=str)
                  ['date'].astype(str).str.strip()) & missing
    if not path or not hit:
        print(f'⚠️ {len(paths)} 份备份都补不上这 {len(missing)} 天, 只能走 '
              'tools/backfill_price_history.py 重抓')
        return 0

    tag = os.path.basename(path).split('.bak.')[-1]
    bak = _load_backup(path)
    close_col = _close_column(bak)
    if close_col is None:
        print(f'❌ 备份里没有可用的收盘价列 (列: {list(bak.columns)})')
        return 1
    print(f'📦 选用备份: {os.path.basename(path)} (收盘价列 {close_col}, '
          f'可补 {len(hit)} 天)')

    add = to_new_schema(bak.loc[bak['date'].isin(hit)], close_col, tag)
    cov = add['date'].value_counts()
    if args.min_coverage > 0:
        thin = sorted(cov[cov < args.min_coverage].index)
        if thin:
            print(f'  ⏭️ {len(thin)} 天覆盖不足 {args.min_coverage} 只, 跳过: '
                  f'{", ".join(thin[:8])}{" ..." if len(thin) > 8 else ""}')
            add = add.loc[~add['date'].isin(thin)]
            cov = add['date'].value_counts()
    if add.empty:
        print('ℹ️ 过滤后无可导入的行')
        return 0

    days = sorted(cov.index)
    print(f'  ➕ 待补 {len(days)} 天 / {len(add)} 行 ({days[0]} ~ {days[-1]})')
    print(f'     每日覆盖 min={cov.min()} 中位={int(cov.median())} max={cov.max()}')
    print(f'     口径: close_legacy + price_basis={LEGACY_BASIS} '
          f'(交界日 {days[-1]} → 之后第一个 raw 日无共同口径, A/D 判未覆盖)')

    still = sorted(missing - set(days))
    if still:
        print(f'  ⚠️ 仍有 {len(still)} 天补不上 (备份里也没有): '
              f'{", ".join(still[:8])}{" ..." if len(still) > 8 else ""}')

    if not args.apply:
        print('\n🔍 体检模式, 未写盘。加 --apply 落盘。')
        return 0

    print()
    # 本地优先在 _write_back 里 (cur 放前面 + keep='first') —— 这里 add 全是整天缺失,
    # 理论上不会撞, 去重是兜底。
    merged = _write_back(cur, add)
    print(f'✅ 已写回: {len(cur)} → {len(merged)} 行, '
          f'{len(cur_dates)} → {merged["date"].nunique()} 天')
    print('\n➡️ 下一步: python tools/reconcile_sentiment_ad.py --window '
          f'{merged["date"].nunique()} --apply   (让新增日的 up/down 有真源)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
