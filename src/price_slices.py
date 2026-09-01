# -*- coding: utf-8 -*-
"""价格缓存的"逐日切片"归档 (让本地和 CI 共用同一条数据血统)。

要解决什么:
    `data/price_history_cache.csv` 32MB, 在 .gitignore 里, CI 用 actions/cache 自己
    存一份。两条血统从不对账 —— 所以 2026-08-28 出现了这种局面: CI 那天绿灯、报告
    正常发布, 本地却整天没有沪深收盘, 而任何一侧都看不出另一侧缺东西。
    更糟的是 CI 的 CACHE_MAX_SIZE_MB=10 (本地 100), 它那份每天被裁到只剩一个月左右,
    actions/cache 又只留 7 天 —— 也就是说这份数据在仓库里**根本没有可回溯的副本**。

怎么做:
    每天把当天(以及最近若干天)的行单独存成一个 gzip 切片
    `data/price_slices/YYYY-MM-DD.csv.gz` (5538 行 ≈ 90KB), 这个体积可以进 git。
    于是:
      · CI 跑完 commit 切片 → 本地 `git pull` 后自动补上漏掉的那天, 一次网络请求都不用发;
      · 本地跑完 commit 切片 → CI 同理;
      · 大 CSV 坏了/被裁了, 也能从切片重建, 不再依赖 actions/cache 的 7 天寿命。

合并规则 (关键, 别改成 slice 优先):
    (code,date) 本地已有 → **保留本地**; 本地没有 → 从切片补。
    切片和本地都出自同一条流水线, 但本地可能刚做过 backfill/对账修复,
    让切片覆盖本地等于把修复结果冲掉。
"""
from __future__ import annotations

import glob
import gzip
import hashlib
import os

import pandas as pd

from ad_breadth import MIN_MARKET_BREADTH
from paths import PRICE_CACHE, PRICE_SLICE_DIR

# 导出窗口: 每次跑批重写最近这些天的切片 (只写内容真变了的)。
# 不止写"今天"是因为回补/对账经常改到前几天, 切片必须跟着更新。
EXPORT_DAYS = 10
# 保留上限 (天). 一天 ≈ 55KB, 150 天 ≈ 8MB 摊在半年多的提交里。
# 为什么是 150 而不是 250: 下限由**最长分析窗口**定 —— 回测 203 天是 tools/ 里
# 本地跑的事, 跑批自己最长只要 MA120 (src/trend_regime.py) 的 120 个交易日。
# 150 留了 30 天余量, 同时把进 git 的总量砍掉四成。
KEEP_SLICES = 150

# 一天的行数低于这个数就**不许导出成切片**。
# 2026-09-01 事故: CI 的 baostock 整段抓取撞上 GLOBAL_TIMEOUT=300s 被 break,
# 已抓到的 ~840 只股票 × 60 个交易日照样落库 —— 每行都是真值, 但每天只有全市场
# 的 15%。这种"部分覆盖板"当天就把 58 天的 sentiment up/down 覆盖成了 ~840
# (见 src/ad_breadth.py)。判据挡住了下游消费, 但只要它被导成切片进 git, 另一侧
# `git pull` 后就会把这 840 行当成"这天我有了"填进缓存 —— 而价格抓取的起点是
# max(date)+1, 区间中段的薄天**永远不会被重抓**, 于是薄成了永久事实。
# 切片是仓库里唯一可回溯的副本, 它只准装可信的整天。
SLICE_MIN_COVERAGE = MIN_MARKET_BREADTH

# 价格缓存自身的体积上限 (MB), **本地和 CI 同值**。
# 放在这里是因为它必须装得下切片保留窗口: KEEP_SLICES 天 × ~0.33MB/交易日。
# 为什么不能沿用 CACHE_MAX_SIZE_MB: 那个上限防的是**进 git 的**小缓存膨胀, CI 侧
# 收到 10MB; 而价格缓存在 .gitignore 里, 永远不进 git, 它的上限只影响 actions/cache
# 的一个条目 (仓库配额 10GB, LRU 淘汰, 而我们只需要最新那一个)。用 10MB 卡它的代价:
#   · CI 的价格缓存永远只剩 ~30 天, 历史日的 A/D 真源直接没有;
#   · 与本模块打架 —— sync 每次从 git 切片补回 250 天, trim 当场又裁回 30 天,
#     白做一遍还让 CI 的 A/D 覆盖天天抖;
#   · 2026-08 那 64 天 up+down≈840 的 sentiment 污染, 就是 CI 拿这份被裁短的缓存算的。
# 150MB ≈ 450 交易日, 装得下 250 天切片 (≈82MB) 还有余量。
PRICE_CACHE_MAX_SIZE_MB = 150

_KEY = ['code', 'date']


def _slice_path(date: str) -> str:
    return os.path.join(PRICE_SLICE_DIR, f'{date}.csv.gz')


def _digest(payload: bytes) -> str:
    return hashlib.sha1(payload).hexdigest()


def _to_csv_lf(frame: pd.DataFrame) -> bytes:
    """序列化成**固定 LF**的 UTF-8 字节。

    ⚠️ 必须钉死换行符: to_csv 在 Windows 上给 CRLF、在 CI (Linux) 上给 LF, 同一天的
       数据两边导出来就是两个不同的文件 —— 切片进 git, 于是每次换机器跑都诈出一个
       "变更", diff 里全是噪声, 而且内容比对永远判不出"没变"。
    """
    try:
        text = frame.to_csv(index=False, lineterminator='\n')
    except TypeError:                     # pandas < 1.5 的旧参数名
        text = frame.to_csv(index=False, line_terminator='\n')
    return text.encode('utf-8')


def _read_cache() -> pd.DataFrame:
    if not os.path.exists(PRICE_CACHE):
        return pd.DataFrame()
    return pd.read_csv(PRICE_CACHE, dtype={'code': str, 'date': str}, low_memory=False)


def export_slices(frame: pd.DataFrame | None = None, days: int = EXPORT_DAYS,
                  keep: int = KEEP_SLICES, quiet: bool = False) -> list[str]:
    """把最近 `days` 天的行导出成 gzip 切片, 返回真正写盘的日期列表。

    内容不变就不写: 切片进 git, 每天无谓改写会让 diff 里全是噪声。
    gzip 固定 mtime=0 —— 否则同样的内容因为时间戳不同也算变更。
    """
    frame = _read_cache() if frame is None else frame
    if frame is None or frame.empty or 'date' not in frame.columns:
        return []
    os.makedirs(PRICE_SLICE_DIR, exist_ok=True)
    dates = sorted(frame['date'].dropna().astype(str).unique())[-max(1, int(days)):]
    written, skipped = [], []
    for date in dates:
        chunk = frame.loc[frame['date'].astype(str) == date]
        if chunk.empty:
            continue
        # 覆盖不足的天一律不导 (见 SLICE_MIN_COVERAGE): 部分覆盖板一旦进了切片,
        # 另一侧就再也不会重抓这一天。
        coverage = int(chunk['code'].nunique()) if 'code' in chunk.columns else len(chunk)
        if coverage < SLICE_MIN_COVERAGE:
            skipped.append((date, coverage))
            continue
        payload = _to_csv_lf(chunk.sort_values(_KEY))
        path = _slice_path(date)
        if os.path.exists(path):
            try:
                with gzip.open(path, 'rb') as handle:
                    if _digest(handle.read()) == _digest(payload):
                        continue
            except Exception:
                pass                      # 读不出来就当它坏了, 重写
        with gzip.GzipFile(path, 'wb', mtime=0) as raw:
            raw.write(payload)
        written.append(date)
    pruned = _prune(keep)
    if not quiet:
        if written:
            print(f'  📦 价格切片已归档 {len(written)} 天: {", ".join(written[-5:])}'
                  f'{" ..." if len(written) > 5 else ""}')
        if skipped:
            detail = ", ".join(f'{d}({n})' for d, n in skipped[-5:])
            print(f'  ⏭️ {len(skipped)} 天覆盖不足 {SLICE_MIN_COVERAGE} 只, 未导出切片: '
                  f'{detail}{" ..." if len(skipped) > 5 else ""}')
        if pruned:
            print(f'  🧹 切片超出保留上限, 删掉最老 {pruned} 天')
    return written


def _prune(keep: int) -> int:
    """只留最近 keep 天的切片, 返回删掉的个数。"""
    paths = sorted(glob.glob(os.path.join(PRICE_SLICE_DIR, '*.csv.gz')))
    excess = len(paths) - max(1, int(keep))
    if excess <= 0:
        return 0
    for path in paths[:excess]:
        try:
            os.remove(path)
        except OSError:
            pass
    return excess


def read_slice(date: str) -> pd.DataFrame:
    """读一天的切片; 不存在/损坏返回空表。"""
    path = _slice_path(date)
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype={'code': str, 'date': str}, low_memory=False)
    except Exception:
        return pd.DataFrame()


def available_dates() -> list[str]:
    return sorted(os.path.basename(p)[:-len('.csv.gz')]
                  for p in glob.glob(os.path.join(PRICE_SLICE_DIR, '*.csv.gz')))


def merge_slices(frame: pd.DataFrame, dates: list[str] | None = None,
                 quiet: bool = False) -> tuple[pd.DataFrame, list[str]]:
    """用切片补齐 frame 里缺的 (code,date), 返回 (新表, 实际补了的日期)。

    本地已有的 (code,date) 一律保留 —— 见模块头部"合并规则"。
    """
    if frame is None:
        frame = pd.DataFrame()
    pool = available_dates() if dates is None else [d for d in dates if d in set(available_dates())]
    if not pool:
        return frame, []

    have = set()
    if not frame.empty and {'code', 'date'} <= set(frame.columns):
        have = set(zip(frame['code'].astype(str), frame['date'].astype(str)))
    local_by_date = (frame.groupby(frame['date'].astype(str))['code'].nunique().to_dict()
                     if not frame.empty and 'date' in frame.columns else {})

    additions, filled = [], []
    for date in pool:
        chunk = read_slice(date)
        if chunk.empty:
            continue
        # 本地这天已经不比切片薄, 就别费劲逐行比对了 (常态路径, 省一次 zip)
        if local_by_date.get(date, 0) >= chunk['code'].nunique():
            continue
        keys = list(zip(chunk['code'].astype(str), chunk['date'].astype(str)))
        mask = [key not in have for key in keys]
        missing = chunk.loc[mask]
        if missing.empty:
            continue
        additions.append(missing)
        filled.append(f'{date}(+{len(missing)})')
    if not additions:
        return frame, []

    merged = pd.concat([frame] + additions, ignore_index=True) if not frame.empty \
        else pd.concat(additions, ignore_index=True)
    merged = merged.drop_duplicates(subset=_KEY).sort_values(_KEY).reset_index(drop=True)
    if not quiet:
        print(f'  🔁 从切片补回价格数据: {", ".join(filled)}')
    return merged, filled


def sync_cache_from_slices(quiet: bool = False) -> list[str]:
    """独立入口: 读大 CSV → 用切片补 → 变了就写回。返回补上的日期描述列表。"""
    frame = _read_cache()
    merged, filled = merge_slices(frame, quiet=quiet)
    if not filled:
        if not quiet:
            print('  ✅ 价格缓存与切片一致, 无需补齐')
        return []
    merged.to_csv(PRICE_CACHE, index=False)
    if not quiet:
        print(f'  💾 价格缓存已写回 ({len(merged)} 行)')
    return filled
