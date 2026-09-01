# -*- coding: utf-8 -*-
"""进 git 的缓存文件的保留窗口与体积上限 (单一真源)。

要解决什么
    这些文件每天被跑批改写、被 CI 提交, 但**没有任何一处限制它们能长多大**。
    `trim_cache_file` 那道 10MB(CI)/100MB(本地) 的闸门只挂在 4 个文件上, 而且
    是按体积算的 —— 没有一个文件接近 10MB, 所以它一次都没触发过。真正在长的是:
      · report_prediction_history.jsonl  7.6KB/行 × ~6 行/天 ≈ 11MB/年
      · cninfo_announcement_cache.csv    每天净增 ~1000 行, 从不删
      · market_phase_snapshots.jsonl     5KB/行 × 2 行/天
      · report_daily_snapshots/          一天一个 json
    它们进 git, 代价是双份: 工作树体积 + 每天一整份 blob 进历史 (改一行也是全量)。

判据
    按**交易日保留窗口**裁, 不按体积。这些缓存都以日期为键, "留最近 N 天"才是
    语义正确的裁法, 体积随之天然有界; 纯按体积裁则会在证券口径扩容 (行数台阶)
    时突然多删几天, 而删掉的正是回测窗口的老数据。
    体积上限只作**兜底**: 记录变胖 (往里塞 focus_pool / market_snapshot) 时窗口
    管不住体积, 这时再按日期从老到新继续删。

窗口取值原则
    ① 装得下它的消费者最远要看的那段 —— MA120 (src/trend_regime.py) 120 个交易日,
       阶段识别的顶/底回溯, 情绪/涨停回归 203 天 (tools/ 里的回测), 场景校准的
       min_samples=8 (按桶算)。所以做长窗口分析的那几份下限是 250, 不可重算的真值
       (sentiment / 涨停历史) 给 500。
    ② 反过来, 窗口开大对**每天整份重写**的文件是有代价的: 保留 30 天就等于每天往
       git 历史里塞一份 30 天大的 blob。所以纯网络优化件 (cninfo / em 板块) 的窗口
       刻意压到 15~60 天 —— 它们的最坏情况只是重跑老报告时多抓一次网络。
    落地当天没有一个文件会被裁 (窗口全部 ≥ 当前实际跨度), 它是**天花板**, 防的是
    无界增长。

还剩什么没做
    真正贵的是**每天一份全量 blob**, 不是工作树体积。对 report_prediction_history
    这种"不可重算 + 单条 7KB"的文件, 下一级手段是**压扁老记录** (老于 N 天的行只留
    scenario_id / outcomes, 丢掉 market_snapshot / market_thesis / 那两份回填的
    scenario_calibration 快照), 统计价值一分不掉而字节掉 90%。这里没有做 —— 它改的是
    记录格式而不是保留窗口, 要单独验证 prediction_review 的每个读取点。

关联: 价格缓存的上限在 src/price_slices.py (PRICE_CACHE_MAX_SIZE_MB / KEEP_SLICES),
不在这里 —— 那份在 .gitignore 里, 永远不进 git, 约束条件完全不同。
"""
from __future__ import annotations

import glob
import json
import os

import pandas as pd

from paths import DATA_DIR

# (相对 data/ 的路径, 日期字段, 保留交易日数, 体积上限 MB)
#   日期字段 = None 表示"一天一个文件"的目录 (按文件名日期裁)。
#   窗口和上限**两个都会生效**, 谁先咬住算谁: 窗口是语义天花板, 上限是字节兜底。
#   下面每一行的取值都按"它的消费者最远要看多久" + "每天的 blob 有多大"两头定。
CACHE_BUDGET = [
    # ① 不可重算的真值 —— 窗口给足, 它们本来就小。
    # A/D 真源在价格缓存被裁短后再也算不回来 (见 src/ad_breadth.py); 涨停池接口对
    # 历史日返回 0 行 (见 memory zt-pool-api-no-history)。回测最长 203 天, 给 500。
    ('sentiment_history_cache.csv', '日期', 500, 4),      # 0.01MB/229 天, 窗口永不咬
    ('涨停历史缓存.csv', '日期', 500, 8),                  # 0.66MB/201 天 → 500 天 ≈ 1.7MB
    # ② 长窗口分析要用的派生缓存 —— 250 交易日 (1 年) 已远超 MA120 与阶段识别。
    ('cls_plate_cache.csv', 'date', 250, 8),              # 3KB/天 → 250 天 ≈ 0.7MB
    ('ths_sector_hist.json', 'date', 250, 4),             # 9KB/天 → 250 天 ≈ 2.2MB
    ('report_daily_snapshots', None, 250, 8),             # 一天一个文件, 无整体重写
    # ③ 预测/阶段留痕 —— 不可重算 (点位记录复原不出来), 且 build_scenario_calibration
    # 的 min_samples=8 是按**桶**算的, 裁短直接掉校准样本。窗口 250 天, 上限只兜底
    # 记录变胖 (单条 7KB: scenario_plans + market_snapshot + market_thesis)。
    ('market_phase_snapshots.jsonl', 'report_date', 250, 6),
    ('report_prediction_history.jsonl', 'report_date', 250, 12),
    # ④ 纯网络优化件 —— 窗口刻意压短, 因为它们**每天整份重写**: 保留窗口开大一天,
    # 每天进 git 的 blob 就大一天份。cninfo 是"当日名单 × 近期公告"的正缓存
    # (dragon_succession._save_ann_cache 自称"纯优化件, 绝不阻断"), 重跑上周的报告
    # 顶多多抓一次网络; em 板块归因自己已经滚动到 10 天 (_EM_CACHE_KEEP_DAYS), 这里
    # 只是给它一个天花板。
    ('em_stock_plate_cache.csv', 'date', 60, 4),          # 85KB/天, 自滚 10 天
    ('cninfo_announcement_cache.csv', 'query_date', 15, 2),  # 126KB/天 (~1000 行)
]

# 明确豁免: 与日期无关, 也不随天数增长 (证券主表一行一只股票)。
EXEMPT = {'security_master.csv'}

# 上限在别处、但同样有人管的: CI 也会提交它们, 结构测试靠这张表确认"有人管"。
EXTERNAL_BUDGET = {
    'price_slices': 'src/price_slices.py: KEEP_SLICES / SLICE_MIN_COVERAGE',
}


def _size_mb(path: str) -> float:
    try:
        return os.path.getsize(path) / (1024 * 1024)
    except OSError:
        return 0.0


def _keep_newest(dates: list, keep: int, cap_mb: float, per_date_mb: float) -> set:
    """返回要保留的日期集合: 先按窗口, 再按体积上限继续往前削。"""
    dates = sorted(set(str(d) for d in dates if str(d) and str(d).lower() != 'nan'))
    kept = dates[-max(1, keep):]
    if cap_mb > 0 and per_date_mb > 0:
        while len(kept) > 1 and len(kept) * per_date_mb > cap_mb:
            kept = kept[1:]
    return set(kept)


def _uniq_dates(dates) -> list:
    return sorted({str(d) for d in dates if str(d) and str(d).lower() != 'nan'})


# 同一份 jsonl 里可以有两种记录形态: 预测行带 report_date, 而 reconcile 回填的
# 结果行 (event_type / prediction_id / recorded_at) **没有** —— 它们占
# report_prediction_history.jsonl 的 63% (188 行里 119 行)。只认 date_col 的话
# 这些行永远躲开保留窗口; 更糟的是体积兜底按"每天多少 MB"折算, 越删剩下的越是
# 那堆删不掉的, 文件只能单调变胖 —— 窗口和上限两道闸门对它同时失效。
# 回退字段取**记录时刻**的日期前缀 (ISO 8601 前 10 位与 report_date 同形)。
# 方向是安全的: recorded_at ≥ 它那条预测的 report_date, 所以结果行只会比预测行
# 多活几天 (最坏留下几条孤立结果行), 不会出现"预测还在、结果先被裁掉"。
_FALLBACK_DATE_KEYS = ('recorded_at', 'date', 'timestamp')


def _record_date(obj: dict, date_col: str, sample: str = '') -> str:
    """取一条 jsonl 记录的日期: 先认 date_col, 缺了才用回退字段的日期前缀。

    sample 是这份文件里 date_col 的一个实际取值, 只用来对齐写法 —— 紧凑 8 位的
    文件里塞进带横线的回退日期会让排序整体乱掉 (窗口就选错了天)。
    两个字段都没有 (或读不懂) 返回空串, 调用方一律保留该行, 不猜。
    """
    value = str(obj.get(date_col, '') or '')
    if value and value.lower() != 'nan':
        return value
    for key in _FALLBACK_DATE_KEYS:
        raw = str(obj.get(key, '') or '')
        if len(raw) >= 10 and raw[:2] == '20':
            got = raw[:10]
            return got.replace('-', '') if len(sample) == 8 and sample.isdigit() else got
    return ''


def _trim_jsonl(path: str, date_col: str, keep: int, cap_mb: float) -> tuple:
    """JSONL: 按日期字段留最近 keep 天。返回 (删掉的天数, 删掉的行数)。"""
    with open(path, encoding='utf-8') as handle:
        raw = [line for line in handle.read().splitlines() if line.strip()]
    records = []
    for line in raw:
        try:
            records.append((json.loads(line), line))
        except ValueError:
            records.append((None, line))              # 读不懂的行一律保留, 不猜
    sample = next((str(obj.get(date_col, '') or '') for obj, _ in records
                   if isinstance(obj, dict) and obj.get(date_col)), '')
    keys = [_record_date(obj, date_col, sample) if isinstance(obj, dict) else ''
            for obj, _ in records]
    uniq = _uniq_dates(keys)
    if not uniq:
        return 0, 0
    kept = _keep_newest(uniq, keep, cap_mb, _size_mb(path) / len(uniq))
    dropped = {d for d in uniq if d not in kept}
    if not dropped:
        return 0, 0
    out = [line for (obj, line), key in zip(records, keys) if key not in dropped]
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write('\n'.join(out) + ('\n' if out else ''))
    return len(dropped), len(records) - len(out)


def _has_bom(path: str) -> bool:
    try:
        with open(path, 'rb') as handle:
            return handle.read(3) == b'\xef\xbb\xbf'
    except OSError:
        return False


def _trim_csv(path: str, date_col: str, keep: int, cap_mb: float) -> tuple:
    """CSV: 按日期列留最近 keep 天。返回 (删掉的天数, 删掉的行数)。"""
    frame = pd.read_csv(path, dtype=str, encoding='utf-8-sig')
    if frame.empty or date_col not in frame.columns:
        return 0, 0
    uniq = _uniq_dates(frame[date_col].tolist())
    if not uniq:
        return 0, 0
    kept = _keep_newest(uniq, keep, cap_mb, _size_mb(path) / len(uniq))
    dropped = {d for d in uniq if d not in kept}
    if not dropped:
        return 0, 0
    mask = ~frame[date_col].astype(str).isin(dropped)
    # BOM 有无必须**原样保留**: 这几份缓存的写入方各不相同 (涨停/板块/公告用
    # utf-8-sig, sentiment 用无 BOM 的 utf-8), 瘦身顺手改掉编码 = 整份文件的字节
    # 全变 + 首列名可能变成 '﻿日期' 让列引用失效 (见 tools/reconcile_sentiment_ad.py)。
    frame.loc[mask].to_csv(path, index=False,
                           encoding='utf-8-sig' if _has_bom(path) else 'utf-8')
    return len(dropped), int(len(frame) - int(mask.sum()))


def _trim_nested_json(path: str, date_col: str, keep: int, cap_mb: float) -> tuple:
    """{板块: [{date, ...}, ...]} 这种嵌套结构: 每个键各留最近 keep 天。"""
    with open(path, encoding='utf-8') as handle:
        blob = json.load(handle)
    if not isinstance(blob, dict):
        return 0, 0
    all_dates = set()
    for series in blob.values():
        if isinstance(series, list):
            all_dates.update(str(row.get(date_col, '')) for row in series
                             if isinstance(row, dict))
    uniq = sorted(d for d in all_dates if d and d.lower() != 'nan')
    if not uniq:
        return 0, 0
    per_date_mb = _size_mb(path) / len(uniq)
    kept = _keep_newest(uniq, keep, cap_mb, per_date_mb)
    dropped = [d for d in uniq if d not in kept]
    if not dropped:
        return 0, 0
    removed = 0
    for key, series in blob.items():
        if not isinstance(series, list):
            continue
        before = len(series)
        # 读不懂的行 (非 dict) 一律保留, 不猜 —— 与 _trim_table 的 jsonl 分支同一原则。
        blob[key] = [row for row in series
                     if not isinstance(row, dict)
                     or str(row.get(date_col, '')) in kept]
        removed += before - len(blob[key])
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(blob, handle, ensure_ascii=False)
    return len(dropped), removed


def _trim_daily_dir(path: str, keep: int, cap_mb: float) -> tuple:
    """一天一个文件的目录 (report_daily_snapshots/2026-09-01.json): 按文件名日期裁。"""
    files = sorted(glob.glob(os.path.join(path, '*.json')))
    if not files:
        return 0, 0
    total_mb = sum(_size_mb(f) for f in files)
    per_date_mb = total_mb / len(files)
    names = [os.path.basename(f)[:-len('.json')] for f in files]
    kept = _keep_newest(names, keep, cap_mb, per_date_mb)
    dropped = 0
    for f, name in zip(files, names):
        if name not in kept:
            try:
                os.remove(f)
                dropped += 1
            except OSError:
                pass
    return dropped, dropped


def enforce_cache_budget(quiet: bool = False) -> list:
    """把 CACHE_BUDGET 里每个文件裁进保留窗口 + 体积上限。返回改动说明列表。

    任何一项出错只跳过它 —— 瘦身失败绝不能反过来把日报判失败。
    """
    changed = []
    for rel, date_col, keep, cap_mb in CACHE_BUDGET:
        path = os.path.join(DATA_DIR, rel)
        if not os.path.exists(path):
            continue
        before_mb = _size_mb(path) if os.path.isfile(path) else \
            sum(_size_mb(f) for f in glob.glob(os.path.join(path, '*.json')))
        try:
            if date_col is None:
                days, rows = _trim_daily_dir(path, keep, cap_mb)
            elif path.endswith('.json'):
                days, rows = _trim_nested_json(path, date_col, keep, cap_mb)
            elif path.endswith('.jsonl'):
                days, rows = _trim_jsonl(path, date_col, keep, cap_mb)
            else:
                days, rows = _trim_csv(path, date_col, keep, cap_mb)
        except Exception as exc:
            if not quiet:
                print(f'  ⚠️ 缓存预算未执行 ({rel}): {type(exc).__name__}: {exc}')
            continue
        if not days:
            continue
        after_mb = _size_mb(path) if os.path.isfile(path) else \
            sum(_size_mb(f) for f in glob.glob(os.path.join(path, '*.json')))
        note = (f'{rel}: 删最老 {days} 天 / {rows} 行, '
                f'{before_mb:.2f}MB → {after_mb:.2f}MB (窗口 {keep} 天, 上限 {cap_mb}MB)')
        changed.append(note)
        if not quiet:
            print(f'  🧹 {note}')
    if not changed and not quiet:
        print('  ✅ 进 git 的缓存均在保留窗口与体积上限内')
    return changed


if __name__ == '__main__':
    import sys
    sys.exit(0 if enforce_cache_budget() is not None else 1)
