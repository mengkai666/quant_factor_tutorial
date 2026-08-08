# pyright: reportMissingTypeStubs=false, reportGeneralTypeIssues=false, reportOperatorIssue=false, reportArgumentType=false, reportUnnecessaryCast=false
"""涨跌停规律 + 指数情绪 + 最高板/压力板 —— 操作手册版

从三份缓存跑统计, 但输出人话操作口令, 不是学术报告:
  - 涨停历史缓存.csv          : 每日涨停/跌停名单 + 连板数
  - sentiment_history_cache.csv : 每日 up/down 家数
  - price_history_cache.csv     : 日线收盘价

用法:
    python src/limit_pattern_study.py

输出:
    output/limit_pattern_study.md  操作手册
    stdout                         终端可读版
"""
from __future__ import annotations

import os
import sys
import warnings
from collections import defaultdict
from datetime import datetime
from math import ceil

import pandas as pd

warnings.filterwarnings('ignore')

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import ZT_CACHE_FILE, SENTIMENT_CACHE, PRICE_CACHE, OUTPUT_DIR  # noqa: E402
from time_utils import filter_completed_rows  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 数据加载
# ─────────────────────────────────────────────────────────────
def _load_zt_cache() -> pd.DataFrame:
    df = pd.read_csv(ZT_CACHE_FILE, encoding='utf-8-sig', dtype={'日期': str})
    df.columns = [c.strip().lstrip('﻿') for c in df.columns]
    df = filter_completed_rows(df, '日期')
    df['日期'] = df['日期'].astype(str).str.strip()
    df = df[df['日期'].str.len() == 8].copy()
    df['连板数'] = pd.to_numeric(df['连板数'], errors='coerce').fillna(1).astype(int)
    return df


def _load_sentiment() -> pd.DataFrame:
    df = pd.read_csv(SENTIMENT_CACHE, encoding='utf-8-sig', dtype={'日期': str})
    df.columns = [c.strip().lstrip('﻿') for c in df.columns]
    df = filter_completed_rows(df, '日期')
    for col in ('up', 'down', 'zt', 'dt'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['up', 'down'])
    df = df[(df['up'] + df['down']) > 1000].copy()
    df['日期'] = df['日期'].astype(str).str.strip()
    df = df.sort_values('日期').reset_index(drop=True)
    df['ad_ratio'] = df['up'] / (df['up'] + df['down'])
    return df


def _load_price_cache() -> pd.DataFrame:
    df = pd.read_csv(PRICE_CACHE)
    df = filter_completed_rows(df, 'date')
    df['date'] = df['date'].astype(str).str.replace('-', '', regex=False)
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    return df.dropna(subset=['close'])


# ─────────────────────────────────────────────────────────────
# 分析 A: 涨跌停规律 → 操作手册
# ─────────────────────────────────────────────────────────────
def analyze_limit_patterns(zt: pd.DataFrame, sent: pd.DataFrame) -> list[str]:
    lines = ['## 一、涨跌停家数怎么看', '']

    days = sorted(zt['日期'].unique())
    lines.append(f'*基于 {days[0][:4]}年{days[0][4:6]}月 至 {days[-1][:4]}年{days[-1][4:6]}月 共 {len(days)} 个交易日*')
    lines.append('')

    daily = zt.groupby(['日期', '类型']).size().unstack(fill_value=0)
    for col in ('ZT', 'DT'):
        if col not in daily.columns:
            daily[col] = 0

    zt_median = int(daily['ZT'].median())
    zt_p90 = int(daily['ZT'].quantile(0.9))
    zt_p10 = int(daily['ZT'].quantile(0.1))
    dt_p90 = int(daily['DT'].quantile(0.9))

    lines.append('### 1. 判断当天市场冷热的三条线')
    lines.append('')
    lines.append(f'- **涨停 ≥ {zt_p90} 家 → 市场"发烧"了**  次日大概率退烧, 别追高')
    lines.append(f'- **涨停 ≤ {zt_p10} 家 → 市场"冻僵"了**  次日大概率反弹, 敢加仓')
    lines.append(f'- **涨停在 {zt_p10}~{zt_p90} 之间 → 正常温度**  按常规节奏做')
    lines.append('')
    lines.append(f'*参考: 正常一天涨停 {zt_median} 家左右, 跌停超过 {dt_p90} 家就要警惕*')
    lines.append('')

    # 连板存活率
    zt_only = zt[zt['类型'] == 'ZT']
    height_counts = zt_only.groupby(['日期', '连板数']).size().unstack(fill_value=0)
    max_height = int(zt_only['连板数'].max())
    total_by_height = height_counts.sum(axis=0)
    base = total_by_height.get(1, 1)

    lines.append('### 2. 连板越高越危险? 恰恰相反')
    lines.append('')
    lines.append('| 从N板打到N+1板 | 成功率 |')
    lines.append('|---|---|')

    transition_rates = {}
    transition_samples = {}
    for h in range(1, min(max_height, 7)):
        cur = int(total_by_height.get(h, 0))
        nxt = int(total_by_height.get(h + 1, 0))
        rate = nxt / cur * 100 if cur else 0
        if cur:
            transition_rates[h] = rate
            transition_samples[h] = cur
        lines.append(f'| {h}板 → {h+1}板 | {rate:.1f}% |')

    lines.append('')
    first_rate = transition_rates.get(1)
    higher_rates = [v for h, v in transition_rates.items() if h >= 3]
    if first_rate is not None:
        finding = (
            f'**关键发现**: 一进二历史样本 {transition_samples[1]} 组，晋级率 '
            f'**{first_rate:.1f}%**'
        )
        if higher_rates:
            finding += f'；3板以上各层平均晋级率 **{sum(higher_rates) / len(higher_rates):.1f}%**'
        finding += '。以上为当前缓存统计，不代表下一交易日必然重复。'
        lines.append(finding)
    else:
        lines.append('**关键发现**: 一进二没有足够历史样本，暂不输出固定晋级结论。')
    lines.append('')
    lines.append('**怎么操作**:')
    if first_rate is not None:
        lines.append(f'- 首板转二板的历史晋级率为 {first_rate:.1f}%，追高前先确认题材和封单质量')
    else:
        lines.append('- 首板晋级样本不足，暂不依据历史晋级率做方向判断')
    if higher_rates:
        lines.append('- 3板以上按各高度分别观察，不把“高板”直接等同于“龙头”')
    else:
        lines.append('- 3板以上样本不足，暂不输出中高位晋级结论')
    lines.append('- 任何晋级率都要结合当日数据质量、梯队完整度和次日实际回顾')
    lines.append('')

    # 高潮/冰点次日
    sent_map = dict(zip(sent['日期'], sent['ad_ratio']))
    sent_days_sorted = sorted(sent['日期'].tolist())
    next_of = {sent_days_sorted[i]: sent_days_sorted[i + 1]
               for i in range(len(sent_days_sorted) - 1)}

    hot_days = daily[daily['ZT'] >= zt_p90]
    cold_days = daily[daily['ZT'] <= zt_p10]

    def _next_ad(d):
        nd = next_of.get(d)
        return sent_map.get(nd) if nd else None

    hot_next = [_next_ad(d) for d in hot_days.index if _next_ad(d) is not None]
    cold_next = [_next_ad(d) for d in cold_days.index if _next_ad(d) is not None]

    lines.append('### 3. 高潮日 / 冰点日 的次日反应')
    lines.append('')
    if hot_next:
        crash = sum(1 for x in hot_next if float(x) < 0.35) / len(hot_next) * 100
        lines.append(f'- 昨天涨停 {zt_p90}+ 家 → **今天有 {crash:.0f}% 概率大跌**')
    if cold_next:
        rebound = sum(1 for x in cold_next if float(x) > 0.6) / len(cold_next) * 100
        lines.append(f'- 昨天涨停 {zt_p10} 家以下 → **今天有 {rebound:.0f}% 概率反弹**')
    lines.append('')
    lines.append('**怎么操作**: 极端情绪日是"逆向信号", 越是万人恐慌越要看多, 越是万人狂欢越要谨慎')

    return lines


# ─────────────────────────────────────────────────────────────
# 分析 B: 指数情绪 → 操作手册
# ─────────────────────────────────────────────────────────────
def analyze_index_sentiment(sent: pd.DataFrame) -> list[str]:
    lines = ['## 二、大盘冷热怎么看 (红绿股票比例)', '']
    lines.append(f'*涨家数 ÷ (涨家数 + 跌家数) 就是"红盘率", 越高说明市场越强*')
    lines.append('')

    p10 = sent['ad_ratio'].quantile(0.1)
    p25 = sent['ad_ratio'].quantile(0.25)
    p50 = sent['ad_ratio'].quantile(0.5)
    p75 = sent['ad_ratio'].quantile(0.75)
    p90 = sent['ad_ratio'].quantile(0.9)

    lines.append('### 1. 五档温度计')
    lines.append('')
    lines.append(f'- 🥶 **红盘率 < {p10:.0%}** — 冰点(极冷), 全场杀跌, 明天大概率反弹')
    lines.append(f'- ❄️ **红盘率 {p10:.0%}~{p25:.0%}** — 偏冷, 保守观望')
    lines.append(f'- 😐 **红盘率 {p25:.0%}~{p75:.0%}** — 正常温度, 按主线做')
    lines.append(f'- 🌡️ **红盘率 {p75:.0%}~{p90:.0%}** — 偏热, 该止盈了')
    lines.append(f'- 🔥 **红盘率 > {p90:.0%}** — 过热, 别追, 涨嗨的时候准备撤')
    lines.append('')

    # 极端反转
    sent_sorted = sent.sort_values('日期').reset_index(drop=True)
    sent_sorted['next_ad'] = sent_sorted['ad_ratio'].shift(-1)
    sent_sorted['delta'] = sent_sorted['next_ad'] - sent_sorted['ad_ratio']

    def _bucket(mask):
        sub = sent_sorted[mask].dropna(subset=['next_ad'])
        if not len(sub):
            return None, None
        rebound = (sub['delta'] > 0.1).sum() / len(sub) * 100
        return len(sub), rebound

    n_cold_ex, r_cold_ex = _bucket(sent_sorted['ad_ratio'] < 0.2)
    n_cold, r_cold = _bucket(sent_sorted['ad_ratio'] < 0.35)
    n_hot, r_hot = _bucket(sent_sorted['ad_ratio'] > 0.75)
    n_hot_ex, r_hot_ex = _bucket(sent_sorted['ad_ratio'] > 0.85)

    lines.append('### 2. 极端情绪之后, 市场会怎么走')
    lines.append('')
    lines.append('| 今天状态 | 样本 | 明天大反弹概率 |')
    lines.append('|---|---|---|')
    if n_cold_ex:
        lines.append(f'| 极冷(红盘率<20%) | {n_cold_ex}天 | **{r_cold_ex:.0f}%** ✅ 大概率反弹 |')
    if n_cold:
        lines.append(f'| 冰点(红盘率<35%) | {n_cold}天 | {r_cold:.0f}% ✅ 常反弹 |')
    if n_hot:
        lines.append(f'| 过热(红盘率>75%) | {n_hot}天 | {r_hot:.0f}% ❌ 反弹几率极低 |')
    if n_hot_ex:
        lines.append(f'| 极热(红盘率>85%) | {n_hot_ex}天 | {r_hot_ex:.0f}% ❌ 反弹几率极低 |')
    lines.append('')
    lines.append('**关键发现**: **冰点必反弹, 过热不必崩** — 这是不对称的')
    lines.append('- 底部越急越好抄, 越极端信号越强')
    lines.append('- 顶部不会一天崩, 是一路"缓慢消化", 但收益已到头')
    lines.append('')

    # 周中效应
    sent_sorted['dow'] = pd.to_datetime(sent_sorted['日期'], format='%Y%m%d').dt.dayofweek
    dow_names = {0: '一', 1: '二', 2: '三', 3: '四', 4: '五'}
    lines.append('### 3. 每周不同天的市场脾气')
    lines.append('')
    lines.append('| 星期 | 平均红盘率 | 大跌概率 | 大涨概率 |')
    lines.append('|---|---|---|---|')
    for d, name in dow_names.items():
        sub = sent_sorted[sent_sorted['dow'] == d]
        if len(sub):
            crash = (sub['ad_ratio'] < 0.3).sum() / len(sub) * 100
            surge = (sub['ad_ratio'] > 0.7).sum() / len(sub) * 100
            avg = sub['ad_ratio'].mean() * 100
            lines.append(f'| 周{name} | {avg:.0f}% | {crash:.0f}% | {surge:.0f}% |')
    lines.append('')
    lines.append('**怎么操作**:')
    weekday_stats = {}
    for d, name in dow_names.items():
        sub = sent_sorted[sent_sorted['dow'] == d]
        if len(sub):
            crash = (sub['ad_ratio'] < 0.3).sum() / len(sub) * 100
            surge = (sub['ad_ratio'] > 0.7).sum() / len(sub) * 100
            weekday_stats[d] = {'name': name, 'crash': crash, 'surge': surge, 'n': len(sub)}
    if weekday_stats:
        risky = max(weekday_stats.values(), key=lambda x: x['crash'] - x['surge'])
        stable = min(weekday_stats.values(), key=lambda x: x['crash'])
        lines.append(
            f'- 当前样本中周{risky["name"]}的“大跌-大涨”差值最高 '
            f'({risky["crash"] - risky["surge"]:+.0f} 个百分点)，高位仓位需要更谨慎'
        )
        lines.append(
            f'- 当前样本中周{stable["name"]}的大跌概率最低 '
            f'({stable["crash"]:.0f}%，{stable["n"]} 个样本)，但仍需结合当日盘面'
        )
    else:
        lines.append('- 星期分布样本不足，暂不输出固定的周内效应结论')
    lines.append('')

    # 连续性
    vals = sent_sorted['ad_ratio'].tolist()
    cnt2, flip2 = 0, 0
    for i in range(len(vals) - 2):
        if all(v > 0.6 for v in vals[i:i + 2]):
            cnt2 += 1
            if vals[i + 2] < 0.4:
                flip2 += 1
    lines.append('### 4. 情绪不能连着好太多天')
    if cnt2:
        lines.append('')
        lines.append(f'**连续 2 天大涨后, 第 3 天翻脸概率 {flip2/cnt2*100:.0f}%** ({flip2}/{cnt2})')
        lines.append('')
        lines.append('**怎么操作**: 连红 2 天就该警觉了, 情绪不能持续, 兑现节奏要前置')

    return lines


# ─────────────────────────────────────────────────────────────
# 分析 C: 最高板/压力板 → 操作手册
# ─────────────────────────────────────────────────────────────
def analyze_top_board_features(zt: pd.DataFrame, price: pd.DataFrame,
                                sent: pd.DataFrame) -> list[str]:
    lines = ['## 三、最高板长啥样 (龙头股怎么跟)', '']

    zt_only = zt[zt['类型'] == 'ZT'].copy()
    days = sorted(zt_only['日期'].unique())
    daily_max = zt_only.groupby('日期')['连板数'].max()

    if daily_max.empty:
        return lines + ['样本不足：当前缓存没有可用的涨停连板记录，暂不输出最高板结论。', '']

    med = int(daily_max.median())
    high_threshold = max(med + 1, int(ceil(float(daily_max.quantile(0.75)))))
    extreme_threshold = max(high_threshold + 1, int(ceil(float(daily_max.quantile(0.9)))))
    p_high = (daily_max >= high_threshold).sum() / len(daily_max) * 100
    p_extreme = (daily_max >= extreme_threshold).sum() / len(daily_max) * 100

    lines.append('### 1. 市场龙头一般能走多高')
    lines.append('')
    lines.append(f'- **当前样本最高连板中位数 = {med} 板**')
    lines.append(f'- **{p_high:.0f}% 的日子达到 {high_threshold} 板以上** — 作为高位情绪观察线')
    lines.append(f'- **{p_extreme:.0f}% 的日子达到 {extreme_threshold} 板以上** — 作为极端高度观察线')
    lines.append('')
    if p_high >= 50:
        action = (
            f'达到 {high_threshold} 板以上的日子占比 {p_high:.0f}%，高位情绪并不罕见；'
            '仍要结合梯队完整度和次日数据质量，不因高度单独加仓'
        )
    else:
        action = (
            f'达到 {high_threshold} 板以上的日子占比仅 {p_high:.0f}%，当前更接近低高度博弈；'
            '高位股以兑现和次日确认优先'
        )
    lines.append(f'**怎么操作**: {action}')
    lines.append('')

    # 各高度断板率
    day_to_stocks_by_height = defaultdict(lambda: defaultdict(list))
    for _, row in zt_only.iterrows():
        day_to_stocks_by_height[row['日期']][int(row['连板数'])].append(row['代码'])

    day_next = {days[i]: days[i + 1] for i in range(len(days) - 1)}
    day_zt_set = defaultdict(set)
    for _, row in zt_only.iterrows():
        day_zt_set[row['日期']].add(row['代码'])

    price_pivot = price.pivot_table(index='date', columns='code',
                                     values='close', aggfunc='last')

    lines.append('### 2. 手里的连板股, 明天涨还是跌')
    lines.append('')
    lines.append('| 手里持有 | 明天继续涨停概率 | 明天平均涨幅 | 明天翻绿概率 |')
    lines.append('|---|---|---|---|')

    ops_map = {}
    for h in range(2, 9):
        promoted, broken = 0, 0
        prices_next = []
        for d, next_d in day_next.items():
            if h not in day_to_stocks_by_height[d]:
                continue
            for code in day_to_stocks_by_height[d][h]:
                if code in day_zt_set.get(next_d, set()):
                    promoted += 1
                else:
                    broken += 1
                if next_d in price_pivot.index and code in price_pivot.columns and d in price_pivot.index:
                    p_today = price_pivot.at[d, code]
                    p_next = price_pivot.at[next_d, code]
                    if pd.notna(p_today) and pd.notna(p_next) and p_today:
                        prices_next.append((float(p_next) - float(p_today)) / float(p_today) * 100)
        total = promoted + broken
        if total < 5:
            continue
        promote_rate = promoted / total * 100
        avg_next = sum(prices_next) / len(prices_next) if prices_next else 0
        neg_rate = sum(1 for x in prices_next if x < 0) / len(prices_next) * 100 if prices_next else 0
        ops_map[h] = (promote_rate, avg_next, neg_rate)
        lines.append(f'| {h}板 | {promote_rate:.0f}% | {avg_next:+.1f}% | {neg_rate:.0f}% |')

    lines.append('')
    lines.append('**关键发现**:')
    if 2 in ops_map:
        p, _, _ = ops_map[2]
        lines.append(f'- **2 板是最危险的位置**: 只有 {p:.0f}% 能继续封涨停, 其他 {100-p:.0f}% 都断了 —— **首板/二板尾盘减仓**')
    if 5 in ops_map or 6 in ops_map:
        h = 5 if 5 in ops_map else 6
        p, _, _ = ops_map[h]
        lines.append(f'- **{h}-6 板反而稳定**: {p:.0f}% 能继续涨停, 说明能走到这的都是硬货, **中位段拿得住**')
    if 7 in ops_map:
        p, avg, _ = ops_map[7]
        lines.append(f'- **7 板股: {p:.0f}% 还能继续封, 均涨 {avg:+.1f}%** — 龙头加速期, 但要留意情绪配合')
    lines.append('')

    # 孤峰
    orphan_days, healthy_days = [], []
    for d in days:
        heights = sorted(day_to_stocks_by_height[d].keys(), reverse=True)
        if len(heights) < 2:
            continue
        h_top, h_second = heights[0], heights[1]
        if h_top >= 5 and (h_top - h_second) >= 2:
            orphan_days.append(d)
        elif h_top >= 5:
            healthy_days.append(d)

    def _next_day_sent(d):
        nd = day_next.get(d)
        return sent.set_index('日期')['ad_ratio'].get(nd) if nd else None

    orphan_next = [x for x in (_next_day_sent(d) for d in orphan_days) if x is not None]
    healthy_next = [x for x in (_next_day_sent(d) for d in healthy_days) if x is not None]

    lines.append('### 3. 龙头股"高处不胜寒"—— 孤峰效应')
    lines.append('')
    lines.append('如果最高板有个 8 板股, 但下面 6-7 板一个都没有, 这叫"**孤峰**"—— 老大跟老二差太远, 补给线断了')
    lines.append('')
    if orphan_next and healthy_next:
        crash_o = sum(1 for x in orphan_next if x < 0.35) / len(orphan_next) * 100
        crash_h = sum(1 for x in healthy_next if x < 0.35) / len(healthy_next) * 100
        lines.append(f'- **孤峰日**({len(orphan_next)} 样本): 次日市场崩塌概率 **{crash_o:.0f}%** ⚠️')
        lines.append(f'- **正常阶梯日**({len(healthy_next)} 样本): 次日市场崩塌概率 {crash_h:.0f}%')
        lines.append('')
        lines.append('**怎么操作**: 看到"最高板独一档, 下面接力断层"—— 明天全场大概率转弱, 手里的高位股先出, 别恋战')

    lines.append('')

    # 顶标命运
    top_next_zt = 0
    top_total = 0
    top_perf = []
    for d, next_d in day_next.items():
        heights = day_to_stocks_by_height[d]
        if not heights:
            continue
        h_top = max(heights.keys())
        if h_top < 4:
            continue
        for code in heights[h_top]:
            top_total += 1
            if code in day_zt_set.get(next_d, set()):
                top_next_zt += 1
            if next_d in price_pivot.index and code in price_pivot.columns and d in price_pivot.index:
                p_today = price_pivot.at[d, code]
                p_next = price_pivot.at[next_d, code]
                if pd.notna(p_today) and pd.notna(p_next) and p_today:
                    top_perf.append((p_next - p_today) / p_today * 100)

    if top_total:
        promote = top_next_zt / top_total * 100
        avg = sum(top_perf) / len(top_perf) if top_perf else 0
        neg = sum(1 for x in top_perf if x < 0) / len(top_perf) * 100 if top_perf else 0
        lines.append('### 4. 市场最高板 (龙一) 明天怎么走')
        lines.append('')
        lines.append(f'*基于 {top_total} 只 ≥4 板龙一样本*')
        lines.append('')
        lines.append(f'- **明天继续涨停概率 {promote:.0f}%**')
        lines.append(f'- **明天平均涨幅 {avg:+.1f}%**')
        lines.append(f'- **明天翻绿概率 {neg:.0f}%**')
        lines.append('')
        lines.append(f'**怎么操作**: 龙一次日"进可攻退可守", 均涨 {avg:+.1f}% 是正期望, 但 {neg:.0f}% 概率会翻绿, **早盘冲高不追, 回踩再进**')

    return lines


# ─────────────────────────────────────────────────────────────
# 结尾: 总操作口令
# ─────────────────────────────────────────────────────────────
def build_summary_playbook(zt: pd.DataFrame, sent: pd.DataFrame) -> list[str]:
    lines = ['## 四、每日实操口令表', '']
    lines.append('把上面的规律浓缩成"看到什么, 做什么":')
    lines.append('')

    daily = zt.groupby(['日期', '类型']).size().unstack(fill_value=0)
    for col in ('ZT', 'DT'):
        if col not in daily.columns:
            daily[col] = 0
    zt_p90 = int(daily['ZT'].quantile(0.9)) if len(daily) else 0
    zt_p10 = int(daily['ZT'].quantile(0.1)) if len(daily) else 0
    sent_sorted = sent.sort_values('日期').reset_index(drop=True)
    sent_p10 = sent_sorted['ad_ratio'].quantile(0.1) if len(sent_sorted) else None
    sent_p90 = sent_sorted['ad_ratio'].quantile(0.9) if len(sent_sorted) else None

    lines.append('| 观察到 | 明天怎么做 |')
    lines.append('|---|---|')
    if zt_p90:
        lines.append(f'| 涨停 ≥ {zt_p90} 家 (当前样本 P90) | 减仓, 别追高 |')
    if zt_p10:
        lines.append(f'| 涨停 ≤ {zt_p10} 家 (当前样本 P10) | 先确认数据完整，再观察情绪修复 |')
    if sent_p10 is not None:
        lines.append(f'| 红盘率 < {sent_p10:.0%} (当前样本 P10) | 只作为观察信号，不单凭概率加仓 |')
    if sent_p90 is not None:
        lines.append(f'| 红盘率 > {sent_p90:.0%} (当前样本 P90) | 不追高，优先评估兑现压力 |')
    lines.append('| 连续 2 天大涨 | 第 3 天警觉, 兑现节奏前置 |')
    lines.append('| 今天是周内风险差值最高日 | 高位股降低仓位，等待次日确认 |')
    lines.append('| 今天是周五冰点 | 只有在历史样本达到门槛且命中率真实计算后，才考虑反弹预案 |')
    lines.append('| 手里持首板 / 2 板 | 参考对应高度的动态晋级率，不使用固定经验值 |')
    lines.append('| 手里持 3-6 板 | 逐层核对梯队完整度、断板率和数据质量 |')
    lines.append('| 出现孤峰(龙一 8 板, 下面 5 板都没) | 全场明天转弱, 高位股先出 |')
    lines.append('| 龙一 ≥ 4 板 | 早盘冲高不追, 回踩再进 |')
    lines.append('')
    lines.append('---')
    lines.append('')
    date_count = int(zt['日期'].nunique()) if not zt.empty else 0
    record_count = int(len(zt))
    lines.append(
        f'*报告基于当前缓存 {date_count} 个交易日、{record_count} 条涨停/跌停记录动态计算。'
        '所有概率均需结合样本量、数据质量和 T+1/T+3 实际回顾，不代表未来必然重复。*'
    )
    return lines


# ─────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────
def main():
    print(f'[{datetime.now():%H:%M:%S}] 加载缓存...')
    zt = _load_zt_cache()
    sent = _load_sentiment()
    print(f'  ZT 缓存 {len(zt)} 行, 情绪缓存 {len(sent)} 行')
    print(f'[{datetime.now():%H:%M:%S}] 加载价格缓存(可能耗时)...')
    price = _load_price_cache()
    print(f'  价格缓存 {len(price)} 行')

    all_lines = ['# 涨跌停 + 大盘情绪 + 龙头股 —— 操作手册',
                 f'*生成时间: {datetime.now():%Y-%m-%d %H:%M}*', '',
                 '> 看到规律就想着"明天怎么做", 不看统计学术语', '']

    print(f'[{datetime.now():%H:%M:%S}] A. 涨跌停规律...')
    all_lines += analyze_limit_patterns(zt, sent) + ['']
    print(f'[{datetime.now():%H:%M:%S}] B. 大盘情绪...')
    all_lines += analyze_index_sentiment(sent) + ['']
    print(f'[{datetime.now():%H:%M:%S}] C. 龙头股规律...')
    all_lines += analyze_top_board_features(zt, price, sent) + ['']
    all_lines += build_summary_playbook(zt, sent)

    out_txt = '\n'.join(all_lines)

    out_path = os.path.join(OUTPUT_DIR, 'limit_pattern_study.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(out_txt)

    print('\n' + '=' * 60)
    print(out_txt)
    print('=' * 60)
    print(f'\n手册已保存: {out_path}')


if __name__ == '__main__':
    main()
