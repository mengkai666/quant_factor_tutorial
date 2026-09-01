# -*- coding: utf-8 -*-
"""timing_signal v2 vs v2.1 全量回测 (2025-11-06 ~ 2026-07-24, 173 交易日).

目的:
  · 逐日重放场景分类 (v2 与 v2.1 并行跑)
  · 关注 v2.1 新增两道 E 场景过滤门的降级样本, 复核 T+1/T+3 实盘表现
  · 输出改动样本明细 + 场景胜率对比

关键口径:
  · A/D 家数取 data/sentiment_history_cache.csv 的 up/down
  · 涨停/跌停/最高板/梯队 从 data/涨停历史缓存.csv 逐日重建
  · T+1 表现 = 次日 A/D vs 今日 A/D 变化 + 次日最高板变化
  · T+3 表现 = 3 日内是否破新高 (板数)  以及 3 日内 A/D 是否入冰点 (<0.20)
"""
import os
import sys

import pandas as pd

# Windows 控制台默认 GBK, emoji/中文会 UnicodeEncodeError; 强制 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # pyrefly: ignore [missing-attribute]
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')  # pyrefly: ignore [missing-attribute]
except Exception:
    pass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'src'))

from timing_signal import _classify_scene  # noqa: E402


# ============================================================
#  数据加载
# ============================================================
# 全市场宽度下限: 单一真源在 src/ad_breadth.py
from ad_breadth import MIN_MARKET_BREADTH  # noqa: E402


def _trim_uninformative_prefix(facts):
    """裁掉序列开头"无真源"的天, 中段残缺只告警不丢弃。

    sentiment 缓存起点 (20250919) 早于价格缓存起点 (2025-11-04) 40 天, 这段
    up/down 全是 0 —— A/D 真源根本不覆盖, 对账窗口开多大都补不上。它们进回测后
    ad = 0/0 = NaN, 全部落进 '中性震荡' 桶 (E/A 场景都要求 ad > 0.65, NaN 进不去,
    故不伪造进攻信号), 但会给该桶掺入零信息样本稀释胜率分母。同理开头缺 ZT 数据的
    天 max_h=0, 连板因子失真。

    只裁前缀: 中段丢天会让 prev_h / T+1 指向非相邻交易日, 反而制造新的失真 ——
    中段残缺的正解是回补价格缓存 (tools/backfill_price_gap.py), 故此处只告警。
    """
    breadth = facts['up'] + facts['down']
    ok = (breadth >= MIN_MARKET_BREADTH) & (facts['max_h'] > 0)
    if not ok.any():
        return facts
    first = int(ok.idxmax())
    if first > 0:
        dropped = facts.iloc[:first]
        print(f'  ⚠️ 裁掉开头 {first} 天无真源样本 '
              f'({dropped["date"].min()} ~ {dropped["date"].max()}): '
              f'早于价格缓存起点, up/down 无 A/D 真源可对账')
        facts = facts.iloc[first:].reset_index(drop=True)
    thin = facts[(facts['up'] + facts['down']) < MIN_MARKET_BREADTH]
    if not thin.empty:
        print(f'  ⚠️ 中段 {len(thin)} 天宽度残缺 (<{MIN_MARKET_BREADTH}), 已保留以维持序列连续性, '
              f'但胜率含噪: {sorted(thin["date"].tolist())[:5]}...')
        print('     ➡️ 修复: python tools/backfill_price_gap.py --dates <这些天> --apply --overwrite')
    return facts


def load_daily_facts():
    """回放全量 173 天, 每天生成一个 dict, 含分类器所需全部因子."""
    ad = pd.read_csv(os.path.join(_ROOT, 'data', 'sentiment_history_cache.csv'))
    ad = ad.rename(columns={'日期': 'date'})
    ad['date'] = ad['date'].astype(int)
    ad = ad[['date', 'up', 'down']].dropna().sort_values('date').reset_index(drop=True)

    zt = pd.read_csv(os.path.join(_ROOT, 'data', '涨停历史缓存.csv'))
    zt = zt.rename(columns={'日期': 'date', '类型': 'kind', '连板数': 'height'})
    zt['date'] = zt['date'].astype(int)

    # 每日 ZT/DT 计数, 每日最高连板, 每日梯队分布
    zt_only = zt[zt['kind'] == 'ZT'].copy()
    dt_only = zt[zt['kind'] == 'DT'].copy()

    daily_zt_count = zt_only.groupby('date').size().rename('zt_count')
    daily_dt_count = dt_only.groupby('date').size().rename('dt_count')
    daily_max_h = zt_only.groupby('date')['height'].max().rename('max_h')

    # 梯队计数: h3/h4/h5/h6+ (h6+ 合并所有 ≥6 板)
    def echelon_counts(g):
        c3 = int((g == 3).sum())
        c4 = int((g == 4).sum())
        c5 = int((g == 5).sum())
        c6p = int((g >= 6).sum())
        return pd.Series({'h3': c3, 'h4': c4, 'h5': c5, 'h6p': c6p})

    echelon = zt_only.groupby('date')['height'].apply(echelon_counts).unstack(fill_value=0)

    facts = ad.set_index('date').join(daily_zt_count).join(daily_dt_count) \
              .join(daily_max_h).join(echelon).fillna(0).astype({
                  'zt_count': int, 'dt_count': int, 'max_h': int,
                  'h3': int, 'h4': int, 'h5': int, 'h6p': int,
              }).reset_index()
    facts['ad'] = facts['up'] / (facts['up'] + facts['down'])
    facts['ladder'] = facts['h3'] + 2 * facts['h4'] + 3 * facts['h5'] + 4 * facts['h6p']
    facts = facts.sort_values('date').reset_index(drop=True)
    return _trim_uninformative_prefix(facts)


# ============================================================
#  两版分类器 (v2 原版 & v2.1 加保护)
# ============================================================
def classify_v2_original(curr_h, prev_h, ad, ladder, zt, dt, pressure_5d):
    """不含反身顶保护的旧 E 判定."""
    h_drop = prev_h - curr_h if prev_h > 0 else 0
    if ad is not None and ad < 0.20 and curr_h <= 4:
        return 'D_冰点抄底'
    if h_drop >= 3 and dt > 15:
        return 'F_顶部崩塌'
    is_breakout = curr_h > prev_h and (pressure_5d <= 0 or curr_h > pressure_5d)
    if is_breakout and curr_h >= 5:
        if ad is not None and ad > 0.65 and ladder is not None and ladder >= 12:
            return 'A+_突破共振'
        return 'A_突破陷阱'
    # 旧 E: 只看 A/D>0.65 + 6板 + 梯队饱满, 不加保护
    if ad is not None and ad > 0.65 and curr_h >= 6 and (ladder is None or ladder >= 8):
        return 'E_主升加速'
    if curr_h >= 6 and ad is not None and ad < 0.40:
        return 'C_高位分歧'
    if h_drop >= 1 and ad is not None and ad < 0.35 and zt < 80:
        return 'B_退潮蓄势'
    return '中性震荡'


def classify_v2_1(curr_h, prev_h, ad, ladder, zt, dt, pressure_5d,
                  h3, h4, h5, h6p):
    """带反身顶保护的新版, 直接调 src/timing_signal.py 里的实现."""
    scene, *_ = _classify_scene(curr_h, prev_h, ad, ladder, zt, dt, pressure_5d,
                                h3=h3, h4=h4, h5=h5, h6p=h6p)
    return scene


# ============================================================
#  T+1 / T+3 表现打分
# ============================================================
def score_forward(facts, i, curr_h, ad):
    """返回 (t1_ad, t1_max_h, t3_break_high, t3_ice, t1_crash).
    · t3_break_high: 3 日内 max_h > curr_h → 破新高
    · t3_ice: 3 日内 A/D 跌破 0.20 → 冰点崩塌 (E 场景的腰斩代表)
    · t1_crash: T+1 A/D 直接崩塌 <0.35 且比今日跌 30+ pct → 反身顶命中
    """
    if i + 1 >= len(facts):
        return None
    t1 = facts.iloc[i + 1]
    t1_ad = float(t1['ad']) if pd.notnull(t1['ad']) else None
    t1_max_h = int(t1['max_h'])
    # T+1 崩塌: A/D 跌至 0.35 以下 且 相对今日跌幅 >= 0.30
    if t1_ad is not None and ad is not None:
        t1_crash = (t1_ad < 0.35) and ((ad - t1_ad) >= 0.30)
    else:
        t1_crash = False

    forward = facts.iloc[i + 1: i + 4]
    t3_break_high = bool((forward['max_h'] > curr_h).any())
    t3_ice = bool((forward['ad'] < 0.20).any())
    return {
        't1_ad': round(t1_ad, 3) if t1_ad is not None else None,
        't1_max_h': t1_max_h,
        't3_break_high': t3_break_high,
        't3_ice': t3_ice,
        't1_crash': t1_crash,
    }


# ============================================================
#  主回测
# ============================================================
def run_backtest():
    facts = load_daily_facts()
    print(f'\n=== 数据覆盖: {facts["date"].min()} ~ {facts["date"].max()} · {len(facts)} 交易日 ===\n')

    rows = []
    for i, r in facts.iterrows():
        curr_h = int(r['max_h'])
        prev_h = int(facts.iloc[i - 1]['max_h']) if i > 0 else 0
        ad = float(r['ad']) if pd.notnull(r['ad']) else None
        ladder = int(r['ladder'])
        zt = int(r['zt_count'])
        dt = int(r['dt_count'])

        # 5 日压力位 = 昨天前 5 日最高板 (含昨天)
        if i >= 1:
            recent = facts.iloc[max(0, i - 5): i]['max_h']
            pressure_5d = int(recent.max()) if len(recent) else prev_h
        else:
            pressure_5d = prev_h

        h3, h4, h5, h6p = int(r['h3']), int(r['h4']), int(r['h5']), int(r['h6p'])

        s_v2 = classify_v2_original(curr_h, prev_h, ad, ladder, zt, dt, pressure_5d)
        s_v21 = classify_v2_1(curr_h, prev_h, ad, ladder, zt, dt, pressure_5d,
                              h3, h4, h5, h6p)

        forward = score_forward(facts, i, curr_h, ad)

        rows.append({
            'date': int(r['date']),
            'ad': round(ad, 3) if ad is not None else None,
            'max_h': curr_h,
            'prev_h': prev_h,
            'zt': zt, 'dt': dt,
            'ladder': ladder,
            'h3': h3, 'h4': h4, 'h5': h5, 'h6p': h6p,
            'scene_v2': s_v2,
            'scene_v21': s_v21,
            'changed': s_v2 != s_v21,
            **(forward or {}),
        })

    df = pd.DataFrame(rows)
    return df


def report(df):
    # =========== A. 场景分布对比 ===========
    print('=== A. 场景分布对比 (v2 vs v2.1) ===')
    dist_v2 = df['scene_v2'].value_counts().rename('v2')
    dist_v21 = df['scene_v21'].value_counts().rename('v2.1')
    dist = pd.concat([dist_v2, dist_v21], axis=1).fillna(0).astype(int)
    print(dist.to_string())

    # =========== B. 被降级的样本 ===========
    changed = df[df['changed']].copy()
    print(f'\n=== B. v2.1 触发反身顶保护的日子: {len(changed)} 例 ===')
    if not changed.empty:
        cols = ['date', 'ad', 'max_h', 'ladder', 'h3', 'h4', 'h5', 'h6p',
                'scene_v2', 'scene_v21', 't1_ad', 't1_max_h', 't3_break_high',
                't3_ice', 't1_crash']
        print(changed[cols].to_string(index=False))

        # 保护有效性: 这些"本应是 E 主升"的日子, 实际 T+1/T+3 表现
        n = len(changed)
        n_t1_crash = int(changed['t1_crash'].sum())
        n_t3_ice = int(changed['t3_ice'].sum())
        n_t3_break = int(changed['t3_break_high'].sum())
        print('\n  保护有效性分解:')
        print(f'    T+1 情绪崩塌 (A/D 跌 30+pct 至 <0.35): {n_t1_crash}/{n} = {n_t1_crash/n:.0%}')
        print(f'    T+3 内 A/D 破 0.20 冰点:             {n_t3_ice}/{n} = {n_t3_ice/n:.0%}')
        print(f'    T+3 内板数破新高:                     {n_t3_break}/{n} = {n_t3_break/n:.0%}')
        print(f'\n  结论: 若这些日子 v2 让你 7-9 成进攻, {n_t1_crash} 天次日就挂高点了.')

    # =========== C. 未被降级的真 E 主升样本 (对照组) ===========
    true_e = df[(df['scene_v2'] == 'E_主升加速') & (df['scene_v21'] == 'E_主升加速')].copy()
    print(f'\n=== C. 通过保护的真 E_主升加速: {len(true_e)} 例 (v2.1 仍保留进攻) ===')
    if not true_e.empty:
        n = len(true_e)
        n_t3_break = int(true_e['t3_break_high'].sum())
        n_t3_ice = int(true_e['t3_ice'].sum())
        n_t1_crash = int(true_e['t1_crash'].sum())
        print(f'  T+3 破新高:     {n_t3_break}/{n} = {n_t3_break/n:.0%}   (胜率)')
        print(f'  T+3 冰点崩塌:   {n_t3_ice}/{n} = {n_t3_ice/n:.0%}    (腰斩率)')
        print(f'  T+1 情绪崩塌:   {n_t1_crash}/{n} = {n_t1_crash/n:.0%}')

    # =========== D. v2 版全量 E 主升 (含被降级的) 的胜率 vs v2.1 的胜率 ===========
    v2_all_e = df[df['scene_v2'] == 'E_主升加速']
    v21_all_e = df[df['scene_v21'] == 'E_主升加速']

    def stats(sub, label):
        if len(sub) == 0:
            print(f'  [{label}] 样本 0')
            return
        n = len(sub)
        bh = int(sub['t3_break_high'].sum())
        ic = int(sub['t3_ice'].sum())
        cr = int(sub['t1_crash'].sum())
        print(f'  [{label}] n={n}   T+3 破新高 {bh/n:.0%}   T+3 冰点崩塌 {ic/n:.0%}   T+1 崩塌 {cr/n:.0%}')

    print('\n=== D. E_主升加速 场景胜率对比 ===')
    stats(v2_all_e, 'v2 (无保护)  ')
    stats(v21_all_e, 'v2.1 (加保护)')

    return changed


if __name__ == '__main__':
    df = run_backtest()
    changed = report(df)

    # 导出明细供离线复盘
    out_dir = os.path.join(_ROOT, 'output')
    os.makedirs(out_dir, exist_ok=True)
    df.to_csv(os.path.join(out_dir, 'backtest_timing_v2_1.csv'),
              index=False, encoding='utf-8-sig')
    print(f'\n=== 明细已导出: output/backtest_timing_v2_1.csv ({len(df)} 行) ===')
