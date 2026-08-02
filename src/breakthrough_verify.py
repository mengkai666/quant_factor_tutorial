# -*- coding: utf-8 -*-
"""分层晋级率 (Promotion Rate) — 涨跌停情绪的最灵敏温度计。

第 1 步 (仅此模块): 只算数据, 不改任何主文件, 不接报告。
输入:  data/涨停历史缓存.csv         (日期,类型,代码,名称,连板数)
输出:  DataFrame + 命令行速览

核心指标 (针对每个交易日 D):
  R1 = D-1 首板 (n=1) 中, 今日晋级 2 板 的比例
  R2 = D-1 2 板 中, 今日晋级 3 板 的比例
  R3 = D-1 3 板 中, 今日晋级 4 板 的比例
  R_high (可选) = D-1 ≥4 板 中, 今日继续晋级的比例  (样本小, 仅参考)

配套原始计数 (n_from / n_up), 便于分母过小时肉眼判读:
  样本 < 5 时 R 值噪音大, 应标注 "样本过小".

代码前缀归一化: 早期数据带 sh/sz, 近期不带, 统一去前缀取 6 位数字后匹配。

⚠️ STALE 日检测: 历史 ZT 接口偶发对某些日期返回前一日的 stale 快照
(实测: 07-15/16 及 07-20/21 的 ZT 池 code+height 分毫不差, 生物学上不可能)。
若某日 ZT 快照与前一日的 (code, height) 集合完全一致, 标记为 STALE,
不参与晋级率计算与 5MA (会污染分母), 但保留原始 ZT/最高板做展示。

用法 (命令行):
  python src/breakthrough_verify.py                 # 最近 20 日晋级率
  python src/breakthrough_verify.py --n 60          # 最近 60 日
  python src/breakthrough_verify.py --date 20260728 # 指定日期单独打印
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import pandas as pd

# 允许作为脚本或模块两种方式运行
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from paths import ZT_CACHE_FILE, SENTIMENT_CACHE  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────
MIN_SAMPLE_FOR_RATE = 5   # 分母 < 此值时, 认为 R 值不稳定, 标注 "样本过小"
_CODE_PREFIX_RE = re.compile(r'^(?:sh|sz|bj)', re.IGNORECASE)


def _norm_code(x) -> str:
    """代码归一化: 去 sh/sz/bj 前缀, 保留 6 位数字。缺失/异常回落空串。"""
    if x is None:
        return ''
    s = str(x).strip()
    if not s or s.lower() == 'nan':
        return ''
    s = _CODE_PREFIX_RE.sub('', s)
    # 补零到 6 位 (防止 Excel 吞掉前导 0)
    if s.isdigit():
        return s.zfill(6)
    return s


# ─────────────────────────────────────────────────────────────
# 读缓存
# ─────────────────────────────────────────────────────────────
def load_zt_cache(path: str = ZT_CACHE_FILE) -> pd.DataFrame:
    """读涨停缓存, 只保留 ZT 记录, 归一化代码 + 强类型化。

    返回列: date(str YYYYMMDD), code(str 6位), name(str), height(int)
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f'涨停缓存不存在: {path}')

    df = pd.read_csv(path, encoding='utf-8-sig', dtype={'日期': str, '代码': str})
    df.columns = [c.strip().lstrip('﻿') for c in df.columns]

    # 仅涨停 (跌停/其它类型忽略, 晋级率只看 ZT)
    df = df[df['类型'] == 'ZT'].copy()

    df['date'] = df['日期'].astype(str)
    df['code'] = df['代码'].map(_norm_code)
    df['name'] = df['名称'].astype(str).str.strip()
    df['height'] = pd.to_numeric(df['连板数'], errors='coerce').fillna(0).astype(int)

    # 丢弃无效行
    df = df[(df['code'] != '') & (df['height'] >= 1)]
    df = df[['date', 'code', 'name', 'height']].drop_duplicates(
        subset=['date', 'code'], keep='last'
    )
    return df.reset_index(drop=True)


def detect_stale_dates(df: pd.DataFrame) -> set:
    """找出与前一日 (code, height) 集合完全一致的"鬼影日"。

    历史 ZT 接口偶发对某些日期返回前一日快照 (07-15/16, 07-20/21 均已实证)。
    仅当分毫不差才判 STALE — 生物学上不可能, 一般>20只涨停股次日必有 1-2 只
    加入或退出。样本 < 5 时不判 (噪音太大)。
    """
    if df.empty:
        return set()
    by_day = {
        d: frozenset(zip(g['code'], g['height']))
        for d, g in df.groupby('date')
    }
    dates = sorted(by_day.keys(), key=str)
    stale = set()
    for i in range(1, len(dates)):
        cur, prev = by_day[dates[i]], by_day[dates[i - 1]]
        if len(cur) >= 5 and cur == prev:
            stale.add(dates[i])
    return stale


# ─────────────────────────────────────────────────────────────
# 晋级率核心算法
# ─────────────────────────────────────────────────────────────
def compute_promotion_rates(df: pd.DataFrame) -> pd.DataFrame:
    """按日算 R1/R2/R3/R_high。

    定义: 若 code C 昨日高度为 n, 今日出现在 ZT 池且高度为 n+1, 记为一次晋级成功。
    分母 = 昨日 n 板家数; 分子 = 其中今日晋级到 n+1 的家数。

    返回列:
      date, zt, max_h,
      R1, R1_from, R1_up,
      R2, R2_from, R2_up,
      R3, R3_from, R3_up,
      Rhigh, Rhigh_from, Rhigh_up,
      R_avg_25    (R1/R2 加权 = (R1_up + R2_up) / (R1_from + R2_from), 更稳的综合温度)
    """
    if df.empty:
        return pd.DataFrame()

    dates = sorted(df['date'].unique(), key=str)
    # 按日建立 {code: height} 查表, O(1) 匹配
    by_day = {d: dict(zip(g['code'], g['height'])) for d, g in df.groupby('date')}
    stale = detect_stale_dates(df)

    def _empty_rates(row):
        for k in ('R1', 'R2', 'R3', 'Rhigh'):
            row[k] = None
            row[f'{k}_from'] = 0
            row[f'{k}_up'] = 0
        row['R_avg_25'] = None

    rows = []
    for i, d in enumerate(dates):
        today = by_day[d]
        zt = len(today)
        max_h = max(today.values()) if today else 0
        is_stale = d in stale

        row = {'date': d, 'zt': zt, 'max_h': max_h, 'is_stale': is_stale}

        # STALE 日: 保留 zt/max_h 做展示, 但不算晋级率 (今日快照= 昨日, 结果必然是 0)
        if is_stale:
            _empty_rates(row)
            rows.append(row)
            continue

        # 找最近的非 STALE 昨日作为基准 (往前跳 STALE 日)
        prev_idx = i - 1
        while prev_idx >= 0 and dates[prev_idx] in stale:
            prev_idx -= 1
        if prev_idx < 0:
            # 找不到干净基准 (首日或前面全 STALE)
            _empty_rates(row)
            rows.append(row)
            continue

        prev = by_day[dates[prev_idx]]

        # 按昨日高度分桶
        buckets = {1: [], 2: [], 3: [], 'high': []}  # 'high' = 昨日 >=4 板
        for c, h in prev.items():
            if h == 1:
                buckets[1].append(c)
            elif h == 2:
                buckets[2].append(c)
            elif h == 3:
                buckets[3].append(c)
            elif h >= 4:
                buckets['high'].append((c, h))

        def _count_up(codes_with_target):
            """codes_with_target: iterable of (code, expected_today_height)"""
            up = 0
            for c, target_h in codes_with_target:
                if today.get(c) == target_h:
                    up += 1
            return up

        # R1: 昨日 1 -> 今日 2
        r1_from = len(buckets[1])
        r1_up = _count_up((c, 2) for c in buckets[1])
        # R2: 昨日 2 -> 今日 3
        r2_from = len(buckets[2])
        r2_up = _count_up((c, 3) for c in buckets[2])
        # R3: 昨日 3 -> 今日 4
        r3_from = len(buckets[3])
        r3_up = _count_up((c, 4) for c in buckets[3])
        # Rhigh: 昨日 >=4 -> 今日 h+1
        rh_from = len(buckets['high'])
        rh_up = sum(1 for c, h in buckets['high'] if today.get(c) == h + 1)

        def _rate(up, frm):
            return round(up / frm, 3) if frm > 0 else None

        row.update({
            'R1': _rate(r1_up, r1_from), 'R1_from': r1_from, 'R1_up': r1_up,
            'R2': _rate(r2_up, r2_from), 'R2_from': r2_from, 'R2_up': r2_up,
            'R3': _rate(r3_up, r3_from), 'R3_from': r3_from, 'R3_up': r3_up,
            'Rhigh': _rate(rh_up, rh_from), 'Rhigh_from': rh_from, 'Rhigh_up': rh_up,
        })

        # 综合 (1+2 板加权) — 首板+2板占样本 90%+, 是最稳的情绪温度
        wf = r1_from + r2_from
        wu = r1_up + r2_up
        row['R_avg_25'] = round(wu / wf, 3) if wf > 0 else None
        row['is_pending'] = False

        rows.append(row)

    # PENDING 检测: 仅针对最新一日 (数据 finalize 问题只影响当天)。
    # 昨日分母够大 (≥30) 却全维度零晋级 → 收盘价里概率极低, 基本是当日数据
    # 未回补 (盘中快照 / ZT 池只入了首板未回填连板数)。标 PENDING, 情绪温度作废,
    # 待次日复核。历史日 (非最后一行) 不判, 真实的 0 晋级退潮日应保留。
    if rows:
        last = rows[-1]
        if not last.get('is_stale'):
            base = (last.get('R1_from', 0) or 0) + (last.get('R2_from', 0) or 0)
            ups = ((last.get('R1_up', 0) or 0) + (last.get('R2_up', 0) or 0)
                   + (last.get('R3_up', 0) or 0) + (last.get('Rhigh_up', 0) or 0))
            if base >= 30 and ups == 0:
                last['is_pending'] = True
                last['R_avg_25'] = None  # 作废假 0%, 不污染 5MA / 情绪解读

    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────
# 情绪缓存 (A/D 家数 + 跌停家数) 读取
# ─────────────────────────────────────────────────────────────
def load_sentiment_cache(path: str = SENTIMENT_CACHE) -> pd.DataFrame:
    """读盘面情绪缓存 (up/down 涨跌家数)。

    列: 日期,up,down,zt,dt,flat,date_str
    ZT/DT 列可能为空 (以涨停缓存为唯一真源), 这里只取 up/down。
    """
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, encoding='utf-8-sig', dtype={'日期': str})
        df.columns = [c.strip().lstrip('﻿') for c in df.columns]
        df['date'] = df['日期'].astype(str)
        if 'up' not in df.columns:
            df['up'] = 0
        if 'down' not in df.columns:
            df['down'] = 0
        df['up'] = pd.to_numeric(df['up'], errors='coerce').fillna(0).astype(int)
        df['down'] = pd.to_numeric(df['down'], errors='coerce').fillna(0).astype(int)
        return df[['date', 'up', 'down']].drop_duplicates(
            subset=['date'], keep='last'
        ).reset_index(drop=True)
    except Exception as e:
        print(f'  ⚠️ 情绪缓存读取失败: {e}')
        return pd.DataFrame()


def dt_count_by_day(path: str = ZT_CACHE_FILE) -> dict:
    """从涨停缓存直接数每日 DT 家数 (跌停缓存与 ZT 同表, 靠"类型"列区分)。"""
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path, encoding='utf-8-sig', dtype={'日期': str})
    df.columns = [c.strip().lstrip('﻿') for c in df.columns]
    dt = df[df['类型'] == 'DT']
    return dt.groupby('日期').size().to_dict()


# ─────────────────────────────────────────────────────────────
# 压力突破事件 + 6 项验收表
# ─────────────────────────────────────────────────────────────
# 5 日压力位窗口 (与主线强度/timing_signal 口径对齐)
PRESSURE_WINDOW = 5

# 验收项阈值 (可调; 数值来自 timing_signal.py 头注释里的经验区间)
AD_HEALTHY = 1.15       # up/down 比 >= 此值算健康 (>1.15 ~ 弱赚钱)
AD_STRONG = 1.5         # >= 此值算强 (进攻档口径)
LADDER_HEALTHY = 5      # h3+2*h4+3*h5+4*h6+ 达此值算梯队够厚
R25_HEALTHY_RATIO = 1.0 # R25 >= 5日均 * 此值算不塌
DT_CTRL_RATIO = 1.5     # DT <= ZT * 此值算可控


def _ladder_score(today: dict) -> tuple[int, int, int, int, int]:
    """梯队分 = h3*1 + h4*2 + h5*3 + h6+*4 (含各高度家数)。"""
    h3 = sum(1 for h in today.values() if h == 3)
    h4 = sum(1 for h in today.values() if h == 4)
    h5 = sum(1 for h in today.values() if h == 5)
    h6p = sum(1 for h in today.values() if h >= 6)
    ladder = h3 * 1 + h4 * 2 + h5 * 3 + h6p * 4
    return ladder, h3, h4, h5, h6p


def _ad_ratio(sent_row) -> float | None:
    """up/down 比值; 缺失返回 None。"""
    if sent_row is None or sent_row.empty:
        return None
    u = float(sent_row['up'].iloc[0] or 0)
    d = float(sent_row['down'].iloc[0] or 0)
    if u + d < 1000:  # 家数残缺不给分
        return None
    return round(u / max(d, 1), 2)


def evaluate_breakthrough(df_zt: pd.DataFrame, df_rate: pd.DataFrame,
                          df_sent: pd.DataFrame, dt_by_day: dict) -> pd.DataFrame:
    """按日给出压力突破事件 + 6 项验收表。

    事件: max_h_today >= max(前 5 交易日 max_h) → 触发"过压力位"事件
    验收 6 项:
      ① A/D 健康 (>= AD_HEALTHY 且抬升)
      ② 梯队厚 (ladder >= LADDER_HEALTHY, 无孤峰)
      ③ R25 不塌 (>= 5日均值)
      ④ DT 可控 (dt <= zt * DT_CTRL_RATIO)
      ⑤ 高度接力 (今日空间板股昨日已在 ZT 池, 说明是连续晋级不是空降)
      ⑥ 主升样本 (R2 或 R3 有分子 > 0, 说明"次日 3板/4板"是真的有人接)

    判决: ≥4 项 = 真突破, ≤2 = 陷阱, 中间 = 存疑。
    """
    if df_zt.empty or df_rate.empty:
        return pd.DataFrame()

    dates = sorted(df_zt['date'].unique(), key=str)
    by_day = {d: dict(zip(g['code'], g['height']))
              for d, g in df_zt.groupby('date')}
    rate_idx = df_rate.set_index('date')

    # 排除 STALE 日, 压力位窗口只在真实交易日之间取
    stale = set(df_rate[df_rate.get('is_stale', False)]['date'])

    ad_series = {}
    if not df_sent.empty:
        sd = df_sent.set_index('date')
        for d in dates:
            if d in sd.index:
                u = float(sd.at[d, 'up'] or 0)
                dn = float(sd.at[d, 'down'] or 0)
                if u + dn >= 1000:
                    ad_series[d] = round(u / max(dn, 1), 2)

    rows = []
    for i, d in enumerate(dates):
        if d in stale:
            continue
        today = by_day[d]
        if not today:
            continue
        max_h = max(today.values())
        # 5 日压力 (向前找 5 个非 STALE 日的 max_h)
        prev_maxes = []
        j = i - 1
        while j >= 0 and len(prev_maxes) < PRESSURE_WINDOW:
            pd_ = dates[j]
            if pd_ not in stale and by_day.get(pd_):
                prev_maxes.append(max(by_day[pd_].values()))
            j -= 1
        if not prev_maxes:
            continue
        pressure = max(prev_maxes)
        is_event = max_h >= pressure and max_h >= 4  # 不足 4 板不谈"突破"

        # 各验收项
        ladder, h3, h4, h5, h6p = _ladder_score(today)
        r_row = rate_idx.loc[d] if d in rate_idx.index else None

        r25 = r_row['R_avg_25'] if r_row is not None else None
        # R25 5日均 (只算非 STALE 且非空)
        ma_pool = []
        k = i - 1
        while k >= 0 and len(ma_pool) < 5:
            dk = dates[k]
            if dk not in stale and dk in rate_idx.index:
                v = rate_idx.at[dk, 'R_avg_25']
                if pd.notnull(v):
                    ma_pool.append(v)
            k -= 1
        r25_ma = round(sum(ma_pool) / len(ma_pool), 3) if ma_pool else None

        ad = ad_series.get(d)
        ad_prev = ad_series.get(dates[i - 1]) if i > 0 else None
        ad_rising = ad is not None and ad_prev is not None and ad > ad_prev

        zt = len(today)
        dt = int(dt_by_day.get(d, 0))

        # 高度接力: 今日 max_h 的股, 昨日是否在 ZT 池且高度 = max_h - 1
        prev_day = None
        for pj in range(i - 1, -1, -1):
            if dates[pj] not in stale and by_day.get(dates[pj]):
                prev_day = by_day[dates[pj]]
                break
        top_codes = [c for c, h in today.items() if h == max_h]
        relay_ok = False
        if prev_day and top_codes:
            relay_ok = any(prev_day.get(c) == max_h - 1 for c in top_codes)

        # 6 项验收
        checks = {
            '① A/D 健康且抬升': ad is not None and ad >= AD_HEALTHY and ad_rising,
            '② 梯队厚 (≥5)': ladder >= LADDER_HEALTHY,
            '③ R25 不塌': r25 is not None and r25_ma is not None
                          and r25 >= r25_ma * R25_HEALTHY_RATIO,
            '④ DT 可控': dt <= zt * DT_CTRL_RATIO,
            '⑤ 高度接力': relay_ok,
            '⑥ 主升有分子': r_row is not None and (
                (r_row.get('R2_up') or 0) > 0 or (r_row.get('R3_up') or 0) > 0
            ),
        }
        passed = sum(1 for v in checks.values() if v)

        if is_event:
            if passed >= 4:
                verdict, verdict_clr = '真突破', '#f85149'
            elif passed <= 2:
                verdict, verdict_clr = '陷阱/诱多', '#58a6ff'
            else:
                verdict, verdict_clr = '存疑', '#d29922'
        else:
            verdict, verdict_clr = '无事件', '#8b949e'

        rows.append({
            'date': d, 'zt': zt, 'dt': dt, 'max_h': max_h, 'pressure_5d': pressure,
            'is_event': is_event, 'passed': passed, 'verdict': verdict,
            'verdict_color': verdict_clr,
            'ad': ad, 'ad_rising': ad_rising, 'ladder': ladder,
            'h3': h3, 'h4': h4, 'h5': h5, 'h6p': h6p,
            'r25': r25, 'r25_ma5': r25_ma, 'relay_ok': relay_ok,
            'checks': checks,
        })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────
# HTML 片段 (可嵌入现有报告)
# ─────────────────────────────────────────────────────────────
def render_html_section(df_rate: pd.DataFrame, df_event: pd.DataFrame,
                        n_recent: int = 10) -> str:
    """渲染一段暗色 HTML, 风格与 market_stance / decision_dashboard 一致。

    包含:
      · 最新日综合温度卡片 (R25 + 5日均 + 情绪解读)
      · 近 N 日晋级率序列表格
      · 最近一次"过压力位"事件的 6 项验收表
    """
    if df_rate.empty:
        return ''

    # 5MA 补齐 (显示需要)
    rr = df_rate.copy()
    rr['R25_ma5'] = rr['R_avg_25'].rolling(5, min_periods=3).mean().round(3)

    last = rr.iloc[-1]
    last_bad = bool(last.get('is_stale')) or bool(last.get('is_pending'))
    last_tag = 'STALE' if last.get('is_stale') else ('未 finalize' if last.get('is_pending') else '')

    # 最新日若 STALE/PENDING, 温度回退到最近一个干净日 (否则卡片永远灰着无信息)
    read = last
    if last_bad:
        bad_mask = rr['is_stale'].fillna(False).astype(bool)
        if 'is_pending' in rr.columns:
            bad_mask = bad_mask | rr['is_pending'].fillna(False).astype(bool)
        clean = rr[~bad_mask]
        if not clean.empty:
            read = clean.iloc[-1]
    r25 = read['R_avg_25']
    ma5 = read['R25_ma5']

    pre = f'[最新日 {last["date"]} {last_tag}, 温度取 {read["date"]}] ' if last_bad else ''

    if r25 is None or ma5 is None or pd.isna(r25) or pd.isna(ma5):
        head_clr = '#8b949e'
        head_txt = f'{pre}暂无可读情绪温度, 待数据 finalize'
    elif r25 >= ma5 * 1.15:
        head_clr = '#f85149'
        head_txt = f'{pre}晋级率 {r25:.1%} 显著高于 5日均 {ma5:.1%} → 情绪回暖, 突破可信度上升'
    elif r25 <= ma5 * 0.7:
        head_clr = '#58a6ff'
        head_txt = f'{pre}晋级率 {r25:.1%} 显著低于 5日均 {ma5:.1%} → 情绪塌缩, 突破多为陷阱'
    else:
        head_clr = '#d29922'
        head_txt = f'{pre}晋级率 {r25:.1%} 与 5日均 {ma5:.1%} 持平 → 未明显变盘'

    # 表头
    tail = rr.tail(n_recent)
    rows_html = ''
    for _, r in tail.iterrows():
        tag = ' <span style="color:#8b949e;">[STALE]</span>' if r.get('is_stale') else ''
        def _c(v):
            return _fmt_pct(v) if pd.notnull(v) else '  —  '
        rows_html += (
            f'<tr>'
            f'<td style="padding:6px 10px;color:#e6edf3;">{r["date"]}{tag}</td>'
            f'<td style="padding:6px 10px;color:#e6edf3;text-align:right;">{int(r["zt"])}</td>'
            f'<td style="padding:6px 10px;color:#e6edf3;text-align:right;">{int(r["max_h"])}</td>'
            f'<td style="padding:6px 10px;color:#8b949e;text-align:right;">{_c(r["R1"])}</td>'
            f'<td style="padding:6px 10px;color:#8b949e;text-align:right;">{_c(r["R2"])}</td>'
            f'<td style="padding:6px 10px;color:#8b949e;text-align:right;">{_c(r["R3"])}</td>'
            f'<td style="padding:6px 10px;color:#e6edf3;text-align:right;font-weight:bold;">'
            f'{_c(r["R_avg_25"])}</td>'
            f'<td style="padding:6px 10px;color:#8b949e;text-align:right;">'
            f'{_c(r["R25_ma5"])}</td>'
            f'</tr>'
        )

    # 最近一次 "有事件" 的日子的验收表
    ev_html = ''
    if not df_event.empty:
        ev_days = df_event[df_event['is_event']]
        if not ev_days.empty:
            ev = ev_days.iloc[-1]
            check_rows = ''
            for name, ok in ev['checks'].items():
                mark = '✅' if ok else '⬜'
                clr = ev['verdict_color'] if ok else '#8b949e'
                check_rows += (
                    f'<tr><td style="padding:6px 12px;color:#e6edf3;">{name}</td>'
                    f'<td style="padding:6px 12px;color:{clr};font-weight:bold;'
                    f'white-space:nowrap;">{mark}</td></tr>'
                )
            ev_html = f'''
    <div style="margin-top:16px;padding:12px 14px;background:rgba(0,0,0,0.35);
                border-left:3px solid {ev['verdict_color']};border-radius:6px;">
      <div style="color:#8b949e;font-size:12px;font-weight:bold;margin-bottom:4px;">
        最近一次过压力位事件 · {ev['date']}
      </div>
      <div style="color:#e6edf3;font-size:14px;margin-bottom:6px;">
        空间板 <b>{int(ev['max_h'])}</b> ≥ 前5日压力 <b>{int(ev['pressure_5d'])}</b>
        &nbsp;→&nbsp; 6项验收通过
        <b style="color:{ev['verdict_color']};">{ev['passed']}/6</b>
        &nbsp;→&nbsp; 判决: <b style="color:{ev['verdict_color']};">{ev['verdict']}</b>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:13px;">{check_rows}</table>
    </div>
    '''

    return f'''
    <div style="background:rgba(0,0,0,0.5);border:2px solid {head_clr};
                border-radius:12px;padding:20px;margin-bottom:30px;
                box-shadow:0 0 15px {head_clr}40;">
      <div style="color:#8b949e;font-size:13px;font-weight:bold;
                  text-transform:uppercase;margin-bottom:6px;">
        🌡️ 分层晋级率 · 情绪温度计 + 压力突破验收
      </div>
      <div style="font-size:20px;font-weight:800;color:{head_clr};margin-bottom:12px;">
        {head_txt}
      </div>
      <div style="color:#8b949e;font-size:12px;margin-bottom:6px;">
        近 {n_recent} 日 (STALE 日不参与 5MA):
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:13px;
                   background:rgba(255,255,255,0.02);border-radius:8px;overflow:hidden;">
        <thead>
          <tr style="background:rgba(255,255,255,0.05);">
            <th style="padding:8px 10px;color:#8b949e;text-align:left;">日期</th>
            <th style="padding:8px 10px;color:#8b949e;text-align:right;">ZT</th>
            <th style="padding:8px 10px;color:#8b949e;text-align:right;">最高板</th>
            <th style="padding:8px 10px;color:#8b949e;text-align:right;">R1 首→2</th>
            <th style="padding:8px 10px;color:#8b949e;text-align:right;">R2 2→3</th>
            <th style="padding:8px 10px;color:#8b949e;text-align:right;">R3 3→4</th>
            <th style="padding:8px 10px;color:#8b949e;text-align:right;">R25</th>
            <th style="padding:8px 10px;color:#8b949e;text-align:right;">R25 5MA</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>{ev_html}
    </div>
    '''


# ─────────────────────────────────────────────────────────────
# 对外主入口 — 供主文件 / 报告调用
# ─────────────────────────────────────────────────────────────
def run(n_recent: int = 10) -> dict:
    """一步跑完: 读缓存 → 算晋级率 → 事件验收 → 返回 dict (含 html 片段)。

    返回:
      {'df_rate': DataFrame, 'df_event': DataFrame, 'html': str}
    """
    df_zt = load_zt_cache(ZT_CACHE_FILE)
    df_rate = compute_promotion_rates(df_zt)
    df_sent = load_sentiment_cache(SENTIMENT_CACHE)
    dt_by_day = dt_count_by_day(ZT_CACHE_FILE)
    df_event = evaluate_breakthrough(df_zt, df_rate, df_sent, dt_by_day)
    html = render_html_section(df_rate, df_event, n_recent=n_recent)
    return {'df_rate': df_rate, 'df_event': df_event, 'html': html}


# ─────────────────────────────────────────────────────────────
# 命令行速览
# ─────────────────────────────────────────────────────────────
def _fmt_pct(v):
    if v is None or pd.isna(v):
        return '  —  '
    return f'{v * 100:5.1f}%'


def _fmt_row(r, ma5=None):
    """一行打印: 日期 ZT 最高板 R1 R2 R3 Rh R25 R25_5日均"""
    ma5_str = _fmt_pct(ma5) if ma5 is not None else '  —  '
    tag = ' [STALE]' if r.get('is_stale') else (
        ' [PENDING]' if r.get('is_pending') else '')
    return (
        f"{r['date']}{tag}  ZT={int(r['zt']):3d}  H={int(r['max_h'])}  "
        f"R1={_fmt_pct(r['R1'])}({r['R1_up']}/{r['R1_from']})  "
        f"R2={_fmt_pct(r['R2'])}({r['R2_up']}/{r['R2_from']})  "
        f"R3={_fmt_pct(r['R3'])}({r['R3_up']}/{r['R3_from']})  "
        f"Rh={_fmt_pct(r['Rhigh'])}({r['Rhigh_up']}/{r['Rhigh_from']})  "
        f"R25={_fmt_pct(r['R_avg_25'])}  ma5={ma5_str}"
    )


def print_recent(df_rate: pd.DataFrame, n: int = 20):
    """打印最近 n 日 + R25 的 5 日均线, 供肉眼判读趋势。"""
    if df_rate.empty:
        print('  ⚠️ 无数据')
        return
    df = df_rate.copy()
    df['R25_ma5'] = df['R_avg_25'].rolling(5, min_periods=3).mean().round(3)

    tail = df.tail(n)
    print('=' * 100)
    print(f'{"日期":<10} {"涨停/最高板":<12} {"R1(首→2)":<20} '
          f'{"R2(2→3)":<20} {"R3(3→4)":<20} {"Rhigh(≥4→+1)":<22} '
          f'{"R25":<8} {"R25 5MA":<8}')
    print('-' * 100)
    for _, r in tail.iterrows():
        ma5 = r['R25_ma5'] if pd.notnull(r['R25_ma5']) else None
        print(_fmt_row(r, ma5))
    print('=' * 100)

    # 一句话情绪解读 (最新一日)
    last = tail.iloc[-1]
    if last.get('is_pending'):
        print(f'\n最新日 {last["date"]} 数据疑似未 finalize (分母 '
              f'{int(last["R1_from"] + last["R2_from"])} 但零晋级) → 标 PENDING, '
              f'情绪温度待明日回补后复核')
        return
    r25 = last['R_avg_25']
    ma5 = last['R25_ma5']
    if r25 is None or ma5 is None or pd.isna(r25) or pd.isna(ma5):
        return
    if r25 >= ma5 * 1.15:
        vibe = '📈 晋级率显著抬升 vs 5日均值 → 情绪回暖'
    elif r25 <= ma5 * 0.7:
        vibe = '📉 晋级率显著低于 5日均值 → 情绪塌缩 (突破常为陷阱)'
    else:
        vibe = '➖ 晋级率与 5日均值持平 → 情绪未明显变盘'
    print(f'\n最新日 {last["date"]} 综合温度 R25 = {r25:.1%}, 5日均 = {ma5:.1%}')
    print(f'  {vibe}')


def main():
    ap = argparse.ArgumentParser(description='分层晋级率 (涨跌停情绪温度计)')
    ap.add_argument('--n', type=int, default=20, help='最近 N 个交易日 (默认 20)')
    ap.add_argument('--date', type=str, default=None, help='仅打印指定日期 (YYYYMMDD)')
    ap.add_argument('--full', action='store_true', help='打印全量历史 (慎用)')
    args = ap.parse_args()

    print(f'读取涨停缓存: {ZT_CACHE_FILE}')
    df = load_zt_cache(ZT_CACHE_FILE)
    print(f'  ZT 样本 {len(df)} 行, 交易日 {df["date"].nunique()} 天')

    df_rate = compute_promotion_rates(df)

    if args.date:
        r = df_rate[df_rate['date'] == args.date]
        if r.empty:
            print(f'  ⚠️ 找不到日期 {args.date}')
            return
        # 顺带把该日的 5MA 也拿出来
        idx = df_rate.index[df_rate['date'] == args.date][0]
        ma5 = df_rate['R_avg_25'].iloc[max(0, idx - 4):idx + 1].mean()
        print(_fmt_row(r.iloc[0], ma5 if pd.notnull(ma5) else None))
        return

    n = len(df_rate) if args.full else args.n
    print_recent(df_rate, n)


if __name__ == '__main__':
    main()
