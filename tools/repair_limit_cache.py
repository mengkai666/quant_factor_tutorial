#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""涨停缓存"日期错标"体检与修复 (独立工具, 不联网)。

病症 (2026-08-27 全量扫出): `data/涨停历史缓存.csv` 里有些交易日存的名单其实
属于**前一个交易日** —— 涨停池接口只对当日有效 (见 zt-pool-api-no-history),
抓取脚本在盘前跑或失败重试时把上一日快照写到了当日名下。实测三种形态:
  ① 整行重复: 当日名单与前一日逐条相同 (当日真名单丢失);
  ② 内容错位: 当日存的是前一日的真名单, 前一日存的又是更前一日的 (可还原);
  ③ 合并写入: 当日名单 = 前一日名单 ∪ 当日真名单 (可剔除污染部分)。

判据不靠接口, 靠价格: 一只股票"涨停"当天的涨跌幅必然贴着它的法定涨跌幅上限
(主板 10%, ST 主板 5%, 创业板/科创板 20%, 北交所 30%, 跌停取负号)。于是每一行
都能独立回答"我属于哪一天"。ST 与否直接看缓存自带的名称, 无需外部映射。

修复只做"搬回原位"与"删重复", 绝不凭价格**新增**行 —— 缺的那天宁可空着, 也不
注入 名称/连板数 不可靠的行 (这份缓存有 19 个消费方, 错的连板数会静默污染
连板分析/天梯/龙头接替)。

用法:
    python tools/repair_limit_cache.py                    # 干跑: 诊断 + 修复计划
    python tools/repair_limit_cache.py --apply            # 落库 (先自动备份)
    python tools/repair_limit_cache.py --verify-only      # 只体检, 不出计划
"""
from __future__ import annotations

import argparse
import io
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from paths import PRICE_CACHE, ZT_CACHE_FILE  # noqa: E402

# 与 src/limit_ratio_factor.py 的 A/D 真源同一套口径配对优先级。
BASIS_PRIORITY = (
    ('close_raw', 'raw'),
    ('close_qfq', 'qfq_fallback'),
    ('close_legacy', 'legacy_mixed'),
    ('close', 'legacy_mixed'),
)
DEFAULT_TOLERANCE = 0.35   # 幅度容差(百分点): 覆盖四舍五入与 1 分钱级误差
DEFAULT_WINDOW = 3         # 候选交易日窗口: 前后各 N 个交易日
MIN_DECIDED = 8            # 一天至少这么多行能独立定归属, 才敢下"整日错标"结论


def legal_limit(code: str, name: str) -> float:
    """该股票当日的法定涨跌幅上限(百分点, 正数)。"""
    code = (code or '').strip().lower()
    upper = (name or '').upper()
    if code.startswith('bj') or code[:2] in ('92', '83', '43'):
        return 30.0
    if code.startswith('sz30') or code.startswith('sh68'):
        return 20.0          # 创业板/科创板 ST 也是 20%, 故先判板再判 ST
    return 5.0 if 'ST' in upper else 10.0


def load_chg_table(path: Path) -> pd.DataFrame:
    """逐股票逐日涨跌幅。口径配对规则与 A/D 真源一致: 只认"当天和前一天都有值"
    的同一列, 跨口径相减一律弃用 (见 ad-price-basis-pairing)。"""
    columns = ['code', 'date'] + [c for c, _ in BASIS_PRIORITY]
    usable = pd.read_csv(path, nrows=0).columns.tolist()
    df = pd.read_csv(path, usecols=[c for c in columns if c in usable],
                     low_memory=False)
    for column, _ in BASIS_PRIORITY:
        if column not in df.columns:
            df[column] = np.nan
        df[column] = pd.to_numeric(df[column], errors='coerce')
    df['code'] = df['code'].astype(str).str.strip().str.lower()
    df['date'] = df['date'].astype(str).str.replace('-', '', regex=False).str[:8]
    df = df[df['date'].str.len() == 8]

    # 同股同日多行(raw/qfq/legacy 各一行)合并成一行, 各列取非空值
    df = df.groupby(['code', 'date'], as_index=False).max(numeric_only=True)
    df = df.sort_values(['code', 'date'])

    market_dates = sorted(df['date'].unique())
    expected_prev = {market_dates[i]: market_dates[i - 1]
                     for i in range(1, len(market_dates))}
    df['prev_date'] = df.groupby('code')['date'].shift(1)
    df['expected_prev'] = df['date'].map(expected_prev)
    for column, _ in BASIS_PRIORITY:
        df['_prev_' + column] = df.groupby('code')[column].shift(1)
    # 前一行必须正好是全市场上一交易日, 不能拿更早的日期冒充
    df = df[df['prev_date'].eq(df['expected_prev'])].copy()

    paired = [((df[c] > 0) & (df['_prev_' + c] > 0)).to_numpy()
              for c, _ in BASIS_PRIORITY]
    cur = np.select(paired, [df[c].to_numpy(dtype=float) for c, _ in BASIS_PRIORITY],
                    default=np.nan)
    prev = np.select(paired, [df['_prev_' + c].to_numpy(dtype=float)
                              for c, _ in BASIS_PRIORITY], default=np.nan)
    df['chg_pct'] = (cur / prev - 1.0) * 100.0
    out = df.dropna(subset=['chg_pct'])[['code', 'date', 'chg_pct']]
    return out.reset_index(drop=True)


def load_zt(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip().lstrip('﻿') for c in df.columns]
    if '冲突' in ''.join(df.columns) or any(
            str(v).startswith(('<<<<<<<', '=======', '>>>>>>>'))
            for v in df.iloc[:, 0].head(200)):
        raise SystemExit('❌ 缓存含 git 冲突标记, 先清理再跑 (见 zt-cache-git-conflict-hazard)')
    df['_date'] = df['日期'].str.replace('-', '', regex=False).str[:8]
    df['_code'] = df['代码'].str.strip().str.lower()
    df['_sign'] = np.where(df['类型'].str.strip().str.upper() == 'DT', -1.0, 1.0)
    df['_limit'] = [legal_limit(c, n) for c, n in zip(df['_code'], df['名称'])]
    df['_target'] = df['_sign'] * df['_limit']
    return df


def diagnose(zt: pd.DataFrame, chg: pd.DataFrame, tolerance: float,
             window: int) -> pd.DataFrame:
    """给每行判"它属于哪一天"。返回带 _verdict/_belongs 列的副本。

    _verdict: in_place(存对了) / moved(应搬到 _belongs) / ambiguous(多天都贴限,
    连板股常见, 保守留原地) / unverifiable(没有一天贴限, 留原地) /
    uncovered(价格缓存判不了这行)。
    """
    price_days = sorted(chg['date'].unique())
    day_index = {d: i for i, d in enumerate(price_days)}
    lookup = {(c, d): v for c, d, v in
              zip(chg['code'], chg['date'], chg['chg_pct'])}

    verdicts, belongs, detail = [], [], []
    for code, stored, target in zip(zt['_code'], zt['_date'], zt['_target']):
        center = day_index.get(stored)
        if center is None:
            verdicts.append('uncovered'); belongs.append(stored); detail.append('')
            continue
        lo, hi = max(0, center - window), min(len(price_days) - 1, center + window)
        hits = []
        for i in range(lo, hi + 1):
            day = price_days[i]
            value = lookup.get((code, day))
            if value is None:
                continue
            if abs(value - target) <= tolerance:
                hits.append((day, value))
        if not hits:
            if (code, stored) not in lookup:
                verdicts.append('uncovered'); belongs.append(stored); detail.append('')
            else:
                verdicts.append('unverifiable'); belongs.append(stored)
                detail.append(f'{lookup[(code, stored)]:+.2f}% vs 限 {target:+.0f}%')
            continue
        days = [d for d, _ in hits]
        if stored in days:
            verdicts.append('in_place'); belongs.append(stored); detail.append('')
        elif len(days) == 1:
            verdicts.append('moved'); belongs.append(days[0]); detail.append('')
        else:
            verdicts.append('ambiguous'); belongs.append(stored)
            detail.append('/'.join(days))
    out = zt.copy()
    out['_verdict'] = verdicts
    out['_belongs'] = belongs
    out['_detail'] = detail
    return out


def apply_consensus(diag: pd.DataFrame, min_share: float = 0.8) -> pd.DataFrame:
    """用"整日共识偏移"兜住连板股。

    单行判据对连板股天生模糊 (它昨天今天都贴着涨停价, 两天都命中)。但如果同一
    存储日里能独立定案的行绝大多数指向同一个偏移 (如 -1 天), 那这天整体就是错标,
    模糊行也该跟着搬 —— 前提是它的候选里确实有那个偏移日。
    """
    out = diag.copy()
    out['_consensus'] = 0
    for stored, group in out.groupby('_date'):
        decided = group[group['_verdict'].isin(['in_place', 'moved'])]
        if len(decided) < MIN_DECIDED:
            continue
        offsets = Counter(
            0 if v == 'in_place' else 1 for v in decided['_verdict'])
        moved_rows = decided[decided['_verdict'] == 'moved']
        if moved_rows.empty:
            continue
        modal_day, modal_n = Counter(moved_rows['_belongs']).most_common(1)[0]
        if modal_n / max(len(decided), 1) < min_share:
            continue
        out.loc[group.index, '_consensus'] = 1
        mask = (out.index.isin(group.index)) & (out['_verdict'] == 'ambiguous')
        for i in out.index[mask]:
            if modal_day in str(out.at[i, '_detail']).split('/'):
                out.at[i, '_verdict'] = 'moved'
                out.at[i, '_belongs'] = modal_day
        _ = offsets
    return out


SYSTEMIC_MIN_ROWS = 20     # 整日错标至少这么多行指向同一天
SYSTEMIC_MIN_SHARE = 0.15  # 且占该日行数这么多比例 (实测: 整日错标日 ≥33 行/18%~92%,
#                            零散误命中日 ≤8 行, 两类天然分得很开)


def gate(diag: pd.DataFrame) -> pd.DataFrame:
    """两道闸, 把"零散单行"从修复范围里踢出去。

    R1 方向闸: 陈旧快照只会把**更早**的名单写到**更晚**的日期下, 所以只认
    目标日 < 存储日 的搬动。反向(0429 的行说自己属于 0430)与病理矛盾, 那是
    价格侧误差(除权/停牌/新股)造成的巧合命中, 一律留原地。

    R2 系统性闸: 只修"整日级"错标 —— 同一天至少 SYSTEMIC_MIN_ROWS 行且占该日
    ≥ SYSTEMIC_MIN_SHARE 指向同一个更早的日子。130 行里飘出 1 行的那种, 更可能
    是这 1 行的价格有问题, 而不是这一天错标; 搬它只会毁掉真数据。
    """
    out = diag.copy()
    out['_gated'] = ''
    backward = (out['_verdict'] == 'moved') & (out['_belongs'] < out['_date'])
    out.loc[(out['_verdict'] == 'moved') & ~backward, ['_verdict', '_gated']] =         ['unverifiable', 'R1_方向']
    for stored, group in out.groupby('_date'):
        moved = group[group['_verdict'] == 'moved']
        if moved.empty:
            continue
        modal_day, modal_n = Counter(moved['_belongs']).most_common(1)[0]
        systemic = (modal_n >= SYSTEMIC_MIN_ROWS
                    and modal_n >= SYSTEMIC_MIN_SHARE * len(group))
        if systemic:
            # 同一天里指向别的日子的零星行也不动 (只信主流那个偏移)
            stray = moved.index[moved['_belongs'] != modal_day]
            out.loc[stray, ['_verdict', '_gated']] = ['unverifiable', 'R2_非主流偏移']
        else:
            out.loc[moved.index, ['_verdict', '_gated']] = ['unverifiable', 'R2_零散']
    return out


def fix_stale_lianban(kept: pd.DataFrame) -> int:
    """留在错标日上的行, 连板数也是从前一日抄来的, 补回 +1。

    错标日上仍保留的行 = 该股前一日和当日都封板 (故两天都贴限) 的连板股。既然整日
    名单抄的是前一日, 这些行的连板数也是前一日的值, 真值必然 +1。

    两个要点:
    ① 比对基准必须是**搬动之后**的前一日 (不是原始那份)。链式错标 (0709 存的是
       0708 的、0710 存的是 0709 的) 里, 0710 那行的值是 0709 的真值, 只有跟修好
       的 0709 比才能对上; 跟原始 0709 (=0708 的值) 比会差 2 而漏判。
    ② 判据要严: 只有"前一日存在同代码同类型且连板数**逐值相同**"才算抄来的。
       合并写入日 (当日真行 + 前日污染行混在一起) 里的真行连板数本就比前日大 1,
       这条判据天然放它过, 不会被误加。
    按日期升序推进, 修好的值即时进入查表, 让链式错标能逐日传下去。
    """
    lookup: dict = {}
    for d, t, c, lb in zip(kept['_new_date'], kept['类型'],
                           kept['_code'], kept['连板数']):
        lookup[(d, str(t).strip(), c)] = lb
    days = sorted(set(kept['_new_date']))
    prev_of = {days[i]: days[i - 1] for i in range(1, len(days))}
    bumped = 0
    for day in days:
        prev = prev_of.get(day)
        if prev is None:
            continue
        rows = kept.index[(kept['_new_date'] == day) & (kept['_stale_day'] == 1)]
        for i in rows:
            key = (prev, str(kept.at[i, '类型']).strip(), kept.at[i, '_code'])
            old_value = lookup.get(key)
            if old_value is None or str(old_value) != str(kept.at[i, '连板数']):
                continue
            try:
                fixed = str(int(float(old_value)) + 1)
            except (TypeError, ValueError):
                continue
            kept.at[i, '连板数'] = fixed
            lookup[(day, str(kept.at[i, '类型']).strip(), kept.at[i, '_code'])] = fixed
            bumped += 1
    return bumped


def chain_report(frame: pd.DataFrame, date_col: str, days_scope: set) -> dict:
    """连板数自洽率: 独立于价格的第二把尺子。

    连板数是"连续封板天数", 所以 n≥2 的行必须在上一交易日有同代码同类型的 n-1 行,
    n==1 的行必须在上一交易日**没有**同代码同类型的行。日期错标会同时破坏这两条
    (整日名单错位后, 连板数与邻日对不上), 所以修复前后比这个比率, 能在不看价格的
    前提下印证修复方向对不对。缓存本身有断档日 (涨停池接口无历史, 补不回来),
    上一交易日整天缺席的行不计入分母。
    """
    present = {}
    for d, t, c, lb in zip(frame[date_col], frame['类型'], frame['_code'],
                           frame['连板数']):
        present.setdefault(d, {})[(str(t).strip(), c)] = lb
    days = sorted(present)
    prev_of = {days[i]: days[i - 1] for i in range(1, len(days))}
    total = ok = 0
    for day in days:
        if day not in days_scope:
            continue
        prev = prev_of.get(day)
        if prev is None or prev not in days_scope:
            continue
        prev_rows = present.get(prev, {})
        for key, value in present[day].items():
            try:
                level = int(float(value))
            except (TypeError, ValueError):
                continue
            total += 1
            if level >= 2:
                try:
                    ok += int(float(prev_rows.get(key, -99))) == level - 1
                except (TypeError, ValueError):
                    pass
            else:
                ok += key not in prev_rows
    return {'total': total, 'ok': ok, 'rate': ok / total if total else float('nan')}


DUP_JACCARD = 0.95     # 与 src/lianban_analysis.py 的写入闸门同源同阈值
DUP_MIN_ROWS = 20      # 名单太短(如 DT)不判, 少数几只重合是常态


def detect_duplicate_days(kept: pd.DataFrame, exclude: set) -> dict:
    """价格缓存覆盖不到的日子也能判的一把尺子: 整日名单与前一存储日逐条相同、
    且连板数一个都没递增 = 该日写的是前一日的陈旧快照 (涨停池接口只对当日有效,
    见备忘 zt-pool-api-no-history)。真实市场不会连续两天出现完全一致的涨停名单,
    更不会连板数集体不涨, 故该日真名单已丢失, 无从回补 —— 只能删掉这一整天,
    留空缺胜过留错数。返回 {待删日: 被抄的那一日}。
    exclude 里的日子已由价格侧判过, 不重复处理。
    """
    zt_only = kept[kept['类型'] == 'ZT']
    codes, levels = {}, {}
    for day, code, level in zip(zt_only['_new_date'], zt_only['_code'],
                                zt_only['连板数']):
        codes.setdefault(day, set()).add(code)
        levels.setdefault(day, {})[code] = str(level)
    flagged, previous = {}, None
    for day in sorted(codes):
        if previous is not None and day not in exclude:
            cur, prev = codes[day], codes[previous]
            if len(cur) >= DUP_MIN_ROWS and len(prev) >= DUP_MIN_ROWS:
                union = cur | prev
                shared = cur & prev
                jaccard = len(shared) / len(union) if union else 0.0
                same_level = sum(1 for c in shared
                                 if levels[day][c] == levels[previous][c])
                if (jaccard >= DUP_JACCARD and shared
                        and same_level / len(shared) >= DUP_JACCARD):
                    flagged[day] = previous
                    continue        # 该日将被删, 下一日仍跟 previous 比
        previous = day
    return flagged


def build_plan(diag: pd.DataFrame):
    """搬回原位 + 删重复。返回 (修复后的行, 删掉的行号)。"""
    keep = diag.copy()
    keep['_new_date'] = np.where(keep['_verdict'] == 'moved',
                                 keep['_belongs'], keep['_date'])
    # 被判定"整日名单抄自前一日"的日子, 留下来的行连板数也要补 (见 fix_stale_lianban)
    stale_days = {d for d in keep['_date'].unique()
                  if (keep['_verdict'][keep['_date'] == d] == 'moved').sum() > 0}
    keep['_stale_day'] = keep['_date'].isin(stale_days).astype(int)
    keep.loc[keep['_verdict'] == 'moved', '_stale_day'] = 0
    existing = {(d, t, c) for d, t, c in zip(
        keep.loc[keep['_verdict'] != 'moved', '_date'],
        keep.loc[keep['_verdict'] != 'moved', '类型'],
        keep.loc[keep['_verdict'] != 'moved', '_code'])}
    drop_dup = []
    for i in keep.index[keep['_verdict'] == 'moved']:
        key = (keep.at[i, '_new_date'], keep.at[i, '类型'], keep.at[i, '_code'])
        if key in existing:
            drop_dup.append(i)      # 目标日已有同一只票的同类型行 = 纯重复快照
        else:
            existing.add(key)
    kept = keep.drop(index=drop_dup)
    return kept, drop_dup


def main() -> int:
    parser = argparse.ArgumentParser(description='涨停缓存日期错标体检与修复')
    parser.add_argument('--apply', action='store_true', help='落库 (默认只干跑)')
    parser.add_argument('--tolerance', type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument('--window', type=int, default=DEFAULT_WINDOW)
    parser.add_argument('--verify-only', action='store_true', help='只体检')
    args = parser.parse_args()

    zt_path, price_path = Path(ZT_CACHE_FILE), Path(PRICE_CACHE)
    print(f'📄 涨停缓存 {zt_path}')
    print(f'📄 价格缓存 {price_path}')
    zt = load_zt(zt_path)
    chg = load_chg_table(price_path)
    days = sorted(chg['date'].unique())
    print(f'   涨停缓存 {len(zt)} 行 / {zt["_date"].nunique()} 天; '
          f'价格可判 {len(chg)} 行 / {len(days)} 天 {days[0]} ~ {days[-1]}')

    diag = gate(apply_consensus(diagnose(zt, chg, args.tolerance, args.window)))
    counts = Counter(diag['_verdict'])
    judged = len(diag) - counts['uncovered']
    print('\n=== 逐行归属体检 ===')
    for name, label in (('in_place', '存对了'), ('moved', '错标(应搬走)'),
                        ('ambiguous', '多日皆贴限(保守留原地)'),
                        ('unverifiable', '无一日贴限(留原地)'),
                        ('uncovered', '价格缓存判不了')):
        n = counts[name]
        base = f'{n / judged:.1%}' if judged and name != 'uncovered' else '-'
        print(f'  {label:<22} {n:>6} 行  {base}')

    bad_days = sorted({d for d, v in zip(diag['_date'], diag['_verdict'])
                       if v == 'moved'})
    print(f'\n=== 涉及 {len(bad_days)} 个存储日 ===')
    kept, drop_dup = build_plan(diag)
    dup_days = detect_duplicate_days(kept, set(bad_days))
    drop_dupday = [i for i in kept.index
                   if kept.at[i, '_new_date'] in dup_days
                   and kept.at[i, '_date'] == kept.at[i, '_new_date']]
    if drop_dupday:
        kept = kept.drop(index=drop_dupday)
    bumped = fix_stale_lianban(kept)
    for day in bad_days:
        group = diag[diag['_date'] == day]
        moved = group[group['_verdict'] == 'moved']
        to = Counter(moved['_belongs']).most_common(2)
        after = int((kept['_new_date'] == day).sum())
        dup = len([i for i in drop_dup if diag.at[i, '_date'] == day])
        flag = '整日' if len(moved) >= SYSTEMIC_MIN_ROWS else '零散'
        print(f'  {day}: 原 {len(group):>3} 行 → 搬走 {len(moved):>3} '
              f'(其中重复删 {dup:>3}) → 修复后该日 {after:>3} 行  '
              f'[{flag}] 去向 {to}')

    print(f'\n合计: 搬动 {counts["moved"]} 行, 其中 {len(drop_dup)} 行是重复快照将删除, '
          f'{counts["moved"] - len(drop_dup)} 行改挂到真实日期')
    print(f'         另有 {bumped} 行留在原日但连板数 +1 (值是从前一日抄来的)')
    if dup_days:
        print(f'· 另有 {len(dup_days)} 天整日名单与前一存储日逐条相同且连板数不递增, '
              f'价格缓存判不到但病理确凿, 整日删除 (共 {len(drop_dupday)} 行):')
        for day, source in dup_days.items():
            print(f'    {day} = {source} 的名单副本')
    gated = Counter(g for g in diag['_gated'] if g)
    if gated:
        print(f'🚧 两道闸拦下不动: {dict(gated)} (方向与病理矛盾 / 零散单行, 更像价格侧误差)')
    emptied = [d for d in bad_days if int((kept['_new_date'] == d).sum()) == 0]
    if emptied:
        print(f'⚠️ 修复后变为空的日期 ({len(emptied)} 天, 真名单不在缓存里, '
              f'涨停池接口无历史故无法回补): {" ".join(emptied)}')

    scope = set(zt['_date']) & set(chg['date'])
    before = chain_report(diag, '_date', scope)
    after = chain_report(kept, '_new_date', scope)
    print()
    print(f'=== 连板数自洽率 (独立于价格的第二把尺子, {len(scope)} 天内) ===')
    print(f'  修复前 {before["ok"]}/{before["total"]} = {before["rate"]:.1%}')
    print(f'  修复后 {after["ok"]}/{after["total"]} = {after["rate"]:.1%}')
    if after['rate'] < before['rate']:
        print('  ❌ 自洽率下降, 修复方向可疑, 不要落库')

    if args.verify_only or not args.apply:
        print('\n(干跑, 未改动任何文件; 加 --apply 落库)')
        return 0

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = zt_path.with_suffix(f'.csv.bak.{stamp}')
    shutil.copy2(zt_path, backup)

    # 最小改动落库: 保持原有行序与行尾(CRLF), 只删该删的行、只改该改的两个字段。
    # 整份重排会造出 1.5 万行的 diff, 与 CI 每日的数据提交必然冲突 (见备忘 7/8),
    # 也让人工复核无从下手。
    with io.open(zt_path, encoding='utf-8-sig', newline='') as handle:
        raw = handle.readlines()
    header, body = raw[0], raw[1:]
    if len(body) != len(diag):
        raise SystemExit(f'❌ 行数不匹配 (文件 {len(body)} vs 解析 {len(diag)}), 放弃落库')
    drop = set(drop_dup) | set(drop_dupday)
    changed_date = {i: kept.at[i, '_new_date'] for i in kept.index
                    if kept.at[i, '_new_date'] != diag.at[i, '_date']}
    changed_level = {i: kept.at[i, '连板数'] for i in kept.index
                     if str(kept.at[i, '连板数']) != str(diag.at[i, '连板数'])}
    out_lines = [header]
    for position, line in enumerate(body):
        if position in drop:
            continue
        if position in changed_date or position in changed_level:
            stripped = line.rstrip()   # 连行尾一起去掉, tail 原样还回
            tail = line[len(stripped):]
            fields = stripped.split(',')
            if len(fields) != 5:
                raise SystemExit(f'❌ 第 {position + 2} 行字段数异常, 放弃落库')
            if position in changed_date:
                fields[0] = str(changed_date[position])
            if position in changed_level:
                fields[4] = str(changed_level[position])
            line = ','.join(fields) + tail
        out_lines.append(line)
    with io.open(zt_path, 'w', encoding='utf-8-sig', newline='') as handle:
        handle.writelines(out_lines)
    print(f'💾 备份 {backup.name}')
    print(f'✅ 落库: {len(out_lines) - 1} 行 (原 {len(body)} 行, 删 {len(drop)}, '
          f'改日期 {len(changed_date)}, 改连板数 {len(changed_level)})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
