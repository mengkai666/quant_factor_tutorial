# pyright: reportMissingTypeStubs=false, reportGeneralTypeIssues=false
"""连板高度年度深研 — 龙头走势·反包模仿·战法系统

一年维度的市场高度全景分析:
  1. 多龙头个股完整走势追踪 (连板路径/断板/反包)
  2. 压力高度 vs 最高板高度关系 (逐龙头)
  3. 断板→反包模式识别与成功率
  4. 突破高度压制后的模仿个股与补涨走势
  5. 板块概念传导分析
  6. 六大维度战法系统总结

数据源 (全本地缓存, 零联网):
  data/annual_height_history.csv    逐日高度/压力/龙头/断板 (一年+)
  data/涨停历史缓存.csv             每日涨停/跌停名单 + 连板数
  data/baostock_limit_history.csv   K线重建涨停 + 连板数 (两年)
  data/price_history_cache.csv      日线收盘 (多口径)
  data/cls_plate_cache.csv          个股→板块归因

用法:
    python src/market_height_annual_study.py

输出:
    output/market_height_annual_report.md
"""
from __future__ import annotations

import os
import sys
import warnings
from collections import Counter, defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import (ZT_CACHE_FILE, PRICE_CACHE, OUTPUT_DIR, DATA_DIR,  # noqa: E402
                   CLS_PLATE_CACHE, EM_PLATE_CACHE)
from time_utils import filter_completed_rows  # noqa: E402

ANNUAL_HISTORY = os.path.join(DATA_DIR, 'annual_height_history.csv')
BAOSTOCK_LIMIT = os.path.join(DATA_DIR, 'baostock_limit_history.csv')

# 常量
MIN_DRAGON_BOARD = 7      # >=7 板才算龙头深度追踪
MIN_REPAIR_BOARD = 3      # >=3 板断板后才算反包事件
MAX_REPAIR_GAP = 10       # 反包最长间隔交易日
IMITATION_LAG = 20        # 模仿个股最大滞后交易日
PRESSURE_WINDOW = 5       # 短压力窗口
BOARD_ZONES = [(1, 4, '低位区'), (5, 6, '中位区'), (7, 99, '高位区')]


# ─────────────────────────────────────────────────────────────
# 数据加载
# ─────────────────────────────────────────────────────────────
def load_annual_height() -> pd.DataFrame:
    """逐日市场高度表 (annual_height_history.csv)。"""
    df = pd.read_csv(ANNUAL_HISTORY)
    df['date'] = df['date'].astype(str).str.strip()
    for col in ['height', 'pressure5', 'pressure20', 'broken_height',
                'future_max1', 'future_max3', 'future_max5', 'future_max10']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.sort_values('date').reset_index(drop=True)


def load_zt_cache() -> pd.DataFrame:
    """涨停历史缓存。"""
    df = pd.read_csv(ZT_CACHE_FILE, encoding='utf-8-sig', dtype={'日期': str})
    df.columns = [c.strip().lstrip('\ufeff') for c in df.columns]
    df = filter_completed_rows(df, '日期')
    df['日期'] = df['日期'].astype(str).str.strip()
    df = df[df['日期'].str.len() == 8].copy()
    df['连板数'] = pd.to_numeric(df['连板数'], errors='coerce').fillna(1).astype(int)
    df['代码'] = df['代码'].astype(str).str.strip()
    df['名称'] = df['名称'].astype(str).str.strip()
    return df


def load_baostock_limit() -> pd.DataFrame:
    """baostock 重建涨停历史。"""
    if not os.path.exists(BAOSTOCK_LIMIT):
        return pd.DataFrame()
    df = pd.read_csv(BAOSTOCK_LIMIT, dtype={'code': str, 'date': str})
    df['lb'] = pd.to_numeric(df['lb'], errors='coerce').fillna(1).astype(int)
    df['date'] = df['date'].astype(str).str.strip()
    df['code'] = df['code'].astype(str).str.strip()
    return df.sort_values(['code', 'date'])


def load_plate_cache() -> pd.DataFrame:
    """板块归因缓存 (cls + em 合并)。"""
    frames = []
    for path in [CLS_PLATE_CACHE, EM_PLATE_CACHE]:
        if os.path.exists(path):
            try:
                f = pd.read_csv(path, dtype=str)
                if 'code' in f.columns and 'mainline' in f.columns:
                    frames.append(f[['date', 'code', 'sub', 'mainline']].dropna(subset=['mainline']))
            except Exception:
                pass
    if not frames:
        return pd.DataFrame(columns=['date', 'code', 'sub', 'mainline'])
    df = pd.concat(frames, ignore_index=True)
    df = df[~df['mainline'].isin({'其它', '其他', '未知', '综合', 'nan', '', 'None', 'ST'})]
    return df.drop_duplicates(subset=['date', 'code'], keep='last')


def _norm_date_ymd(d: str) -> str:
    """YYYY-MM-DD -> YYYYMMDD, 或原样返回 YYYYMMDD。"""
    return d.replace('-', '')[:8]


# ─────────────────────────────────────────────────────────────
# 1. 龙头路径重建 (Leader Path Tracker)
# ─────────────────────────────────────────────────────────────
def build_leader_paths(ah: pd.DataFrame, zt: pd.DataFrame, bs: pd.DataFrame) -> list[dict]:
    """为每只 7板+ 龙头重建完整连板路径。"""
    # 提取所有 7板+ 龙头
    dragons = ah[ah['height'] >= MIN_DRAGON_BOARD][['date', 'leader_codes', 'leader_names']].copy()
    code_set = set()
    code_name = {}
    for _, r in dragons.iterrows():
        codes = str(r.get('leader_codes', '')).split('|')
        names = str(r.get('leader_names', '')).split('、')
        for i, c in enumerate(codes):
            c = c.strip()
            if c:
                code_set.add(c)
                if i < len(names):
                    code_name[c] = names[i].strip()

    zt_only = zt[zt['类型'] == 'ZT']
    paths = {}
    for code in sorted(code_set):
        # 涨停缓存
        sub_zt = zt_only[zt_only['代码'] == code].sort_values('日期')
        zt_records = []
        for _, r in sub_zt.iterrows():
            d = _norm_date_ymd(r['日期'])
            zt_records.append((d, int(r['连板数'])))

        # baostock
        sub_bs = bs[bs['code'] == code].sort_values('date') if not bs.empty else pd.DataFrame()
        bs_records = []
        for _, r in sub_bs.iterrows():
            d = _norm_date_ymd(r['date'])
            bs_records.append((d, int(r['lb'])))

        # 合并: baostock 优先, 涨停缓存补充
        by_date = {}
        for d, lb in bs_records:
            by_date[d] = lb
        for d, lb in zt_records:
            if d not in by_date or lb > by_date[d]:
                by_date[d] = lb

        if not by_date:
            continue

        path = sorted(by_date.items(), key=lambda x: x[0])
        name = code_name.get(code, code)
        max_board = max(lb for _, lb in path)

        paths[code] = {
            'code': code, 'name': name, 'max_board': max_board, 'path': path,
        }

    results = []
    for code, info in sorted(paths.items(), key=lambda x: -x[1]['max_board']):
        path = info['path']
        segments = _identify_segments(path)
        info['segments'] = segments
        info['total_limit_days'] = len(path)
        info['longest_segment'] = max((s['height'] for s in segments), default=0)
        results.append(info)

    return results


def _identify_segments(path: list[tuple[str, int]]) -> list[dict]:
    """识别连续连板段, 以及段之间的断板->反包关系。"""
    if not path:
        return []

    segments = []
    cur_seg = {'start': path[0][0], 'dates': [path[0][0]], 'boards': [path[0][1]]}

    for i in range(1, len(path)):
        prev_d, prev_lb = path[i - 1]
        cur_d, cur_lb = path[i]

        if cur_lb == prev_lb + 1:
            cur_seg['dates'].append(cur_d)
            cur_seg['boards'].append(cur_lb)
        else:
            seg = _close_segment(cur_seg)
            segments.append(seg)
            cur_seg = {'start': cur_d, 'dates': [cur_d], 'boards': [cur_lb]}

    seg = _close_segment(cur_seg)
    segments.append(seg)

    # 标记反包关系
    for i in range(1, len(segments)):
        prev = segments[i - 1]
        cur = segments[i]
        try:
            d0 = pd.Timestamp(prev['end'])
            d1 = pd.Timestamp(cur['start'])
            gap = (d1 - d0).days
        except Exception:
            gap = 999
        cur['prev_height'] = prev['height']
        cur['gap_days'] = gap
        cur['is_repair'] = gap <= MAX_REPAIR_GAP * 2

    return segments


def _close_segment(seg: dict) -> dict:
    return {
        'start': seg['start'], 'end': seg['dates'][-1],
        'height': max(seg['boards']), 'days': len(seg['dates']),
        'boards': list(seg['boards']),
        'prev_height': 0, 'gap_days': 0, 'is_repair': False,
    }


# ─────────────────────────────────────────────────────────────
# 2. 压力高度 vs 最高板关系分析
# ─────────────────────────────────────────────────────────────
def analyze_height_pressure(ah: pd.DataFrame) -> dict:
    """分析一年内压力高度与最高板的关系。"""
    df = ah.copy()
    df['h'] = df['height'].astype(float)
    df['p5'] = pd.to_numeric(df['pressure5'], errors='coerce')
    df['p20'] = pd.to_numeric(df['pressure20'], errors='coerce')

    height_dist = df['h'].value_counts().sort_index()
    total_days = len(df)

    df['break5'] = df['h'] > df['p5']
    break_events_df = df[df['break5'] & df['p5'].notna()].copy()

    zones = {}
    for lo, hi, name in BOARD_ZONES:
        mask = (df['h'] >= lo) & (df['h'] <= hi)
        sub = df[mask]
        zones[name] = {
            'days': len(sub),
            'pct': len(sub) / total_days * 100 if total_days else 0,
            'med_height': sub['h'].median() if len(sub) else 0,
        }

    # 龙头在位期间
    dragon_periods = []
    current = None
    for _, r in df.iterrows():
        h = r['h']
        if h >= MIN_DRAGON_BOARD:
            if current is None:
                current = {'start': r['date'], 'heights': [h],
                           'leaders': str(r.get('leader_names', '')),
                           'pressures5': [r.get('p5')]}
            else:
                current['heights'].append(h)
                current['pressures5'].append(r.get('p5'))
        else:
            if current is not None:
                current['end'] = r['date']
                current['peak'] = max(current['heights'])
                current['duration'] = len(current['heights'])
                dragon_periods.append(current)
                current = None
    if current is not None:
        current['end'] = df['date'].iloc[-1]
        current['peak'] = max(current['heights'])
        current['duration'] = len(current['heights'])
        dragon_periods.append(current)

    break_analysis = []
    for _, r in break_events_df.iterrows():
        fwd = {}
        for k in [1, 3, 5, 10]:
            col = f'future_max{k}'
            if col in df.columns and pd.notna(r.get(col)):
                fwd[f'fmax{k}'] = float(r[col])
        break_analysis.append({
            'date': r['date'], 'height': int(r['h']),
            'pressure5': r['p5'], 'gap': r['h'] - r['p5'],
            'leader': str(r.get('leader_names', '')),
            **fwd,
        })

    return {
        'height_dist': height_dist, 'total_days': total_days,
        'zones': zones, 'break_events': break_analysis,
        'n_breaks': len(break_analysis), 'dragon_periods': dragon_periods,
    }


# ─────────────────────────────────────────────────────────────
# 3. 断板->反包模式识别
# ─────────────────────────────────────────────────────────────
def analyze_repair_patterns(leader_paths: list[dict], ah: pd.DataFrame) -> dict:
    """分析所有龙头的断板->反包模式。"""
    all_repairs = []

    for lp in leader_paths:
        segs = lp.get('segments', [])
        for i, seg in enumerate(segs):
            if not seg.get('is_repair'):
                continue
            prev_h = seg['prev_height']
            if prev_h < MIN_REPAIR_BOARD:
                continue
            all_repairs.append({
                'code': lp['code'], 'name': lp['name'],
                'first_height': prev_h,
                'break_date': segs[i - 1]['end'] if i > 0 else '',
                'repair_date': seg['start'],
                'gap_days': seg['gap_days'],
                'second_height': seg['height'],
                'total_boards': prev_h + seg['height'],
                'is_higher': seg['height'] > prev_h,
            })

    market_repairs = []
    for i in range(len(ah)):
        cur = ah.iloc[i]
        bh = cur.get('broken_height', 0)
        if pd.notna(bh) and float(bh) >= MIN_REPAIR_BOARD:
            market_repairs.append({
                'date': cur['date'],
                'broken_height': int(float(bh)),
                'broken_names': str(cur.get('broken_names', '')),
                'new_height': int(float(cur['height'])),
                'new_leader': str(cur.get('leader_names', '')),
                'height_drop': int(float(bh)) - int(float(cur['height'])),
            })

    repair_by_first_h = defaultdict(list)
    for r in all_repairs:
        key = '3-4板' if r['first_height'] <= 4 else ('5-6板' if r['first_height'] <= 6 else '>=7板')
        repair_by_first_h[key].append(r)

    return {
        'leader_repairs': all_repairs,
        'market_repairs': market_repairs,
        'repair_by_height': dict(repair_by_first_h),
        'total_repairs': len(all_repairs),
    }


# ─────────────────────────────────────────────────────────────
# 4. 模仿补涨分析
# ─────────────────────────────────────────────────────────────
def analyze_imitation(ah: pd.DataFrame, zt: pd.DataFrame, plates: pd.DataFrame) -> dict:
    """分析龙头打出赚钱效应后的形态模仿与板块概念模仿。"""
    zt_only = zt[zt['类型'] == 'ZT']

    daily_zt = defaultdict(dict)
    for _, r in zt_only.iterrows():
        d = _norm_date_ymd(r['日期'])
        daily_zt[d][r['代码']] = int(r['连板数'])

    # 龙头确认日
    dragon_confirm = []
    for _, r in ah.iterrows():
        h = float(r['height'])
        if h >= MIN_DRAGON_BOARD:
            codes = str(r.get('leader_codes', '')).split('|')
            names = str(r.get('leader_names', '')).split('、')
            for ci, c in enumerate(codes):
                c = c.strip()
                if c:
                    dragon_confirm.append({
                        'date': r['date'], 'code': c,
                        'name': names[ci].strip() if ci < len(names) else c,
                        'height': int(h),
                    })

    seen = set()
    unique_confirms = []
    for dc in dragon_confirm:
        key = dc['code']
        if key not in seen:
            seen.add(key)
            unique_confirms.append(dc)

    # 形态模仿
    dates_sorted = sorted(ah['date'].unique())
    form_imitations = []

    for dc in unique_confirms:
        confirm_d8 = _norm_date_ymd(dc['date'])
        future_dates = [d for d in dates_sorted if _norm_date_ymd(d) > confirm_d8][:IMITATION_LAG]

        imitators = []
        for fd in future_dates:
            fd8 = _norm_date_ymd(fd)
            pool = daily_zt.get(fd8, {})
            for code, lb in pool.items():
                if code == dc['code']:
                    continue
                if lb >= 3:
                    imitators.append({
                        'date': fd, 'code': code, 'lb': lb,
                        'lag': future_dates.index(fd) + 1,
                    })

        if imitators:
            form_imitations.append({
                'dragon': dc,
                'imitators': imitators,
                'n_imitators': len(set(im['code'] for im in imitators)),
            })

    # 板块概念模仿
    sector_imitations = []
    if not plates.empty:
        for dc in unique_confirms:
            confirm_d8 = _norm_date_ymd(dc['date'])
            code_plates = plates[plates['code'] == dc['code']]
            if code_plates.empty:
                continue
            mainlines = code_plates['mainline'].unique()
            for ml in mainlines:
                if not ml or ml in {'其它', '其他', '未知'}:
                    continue
                same_sector = plates[plates['mainline'] == ml]['code'].unique()
                same_sector = [c for c in same_sector if c != dc['code']]
                if not same_sector:
                    continue

                future_dates = [d for d in dates_sorted if _norm_date_ymd(d) > confirm_d8][:10]
                sector_zt_count = []
                for fd in future_dates:
                    fd8 = _norm_date_ymd(fd)
                    pool = daily_zt.get(fd8, {})
                    cnt = sum(1 for c in same_sector if c in pool)
                    sector_zt_count.append(cnt)

                if any(c > 0 for c in sector_zt_count):
                    sector_imitations.append({
                        'dragon': dc, 'mainline': ml,
                        'n_same_sector': len(same_sector),
                        'sector_zt_by_day': sector_zt_count,
                        'peak_day': sector_zt_count.index(max(sector_zt_count)) + 1 if sector_zt_count else 0,
                    })

    return {
        'dragon_confirms': unique_confirms,
        'form_imitations': form_imitations,
        'sector_imitations': sector_imitations,
    }


# ─────────────────────────────────────────────────────────────
# 5. 高度周期分析 (一年维度)
# ─────────────────────────────────────────────────────────────
def analyze_height_cycles(ah: pd.DataFrame) -> dict:
    """一年维度的高度周期分析。"""
    df = ah.copy()
    df['h'] = pd.to_numeric(df['height'], errors='coerce')
    CYCLE_FLOOR = 3

    cycles = []
    cur = None
    prev_date = None
    for _, r in df.iterrows():
        h = r['h']
        if pd.isna(h):
            if cur is not None:
                cur['end'] = prev_date
                cur['end_reason'] = '数据缺口'
                cycles.append(cur)
                cur = None
            continue
        h = int(h)
        prev_date = r['date']
        if h > CYCLE_FLOOR:
            if cur is None:
                cur = {'start': r['date'], 'heights': [h], 'dates': [r['date']],
                       'leaders': [str(r.get('leader_names', ''))]}
            else:
                cur['heights'].append(h)
                cur['dates'].append(r['date'])
                cur['leaders'].append(str(r.get('leader_names', '')))
        else:
            if cur is not None:
                cur['end'] = r['date']
                cur['end_reason'] = '自然结束'
                cycles.append(cur)
                cur = None
    if cur is not None:
        cur['end'] = df['date'].iloc[-1]
        cur['end_reason'] = '样本右截断'
        cycles.append(cur)

    cycle_stats = []
    for c in cycles:
        peak = max(c['heights'])
        peak_idx = c['heights'].index(peak)
        cycle_stats.append({
            'start': c['start'], 'end': c['end'],
            'length': len(c['heights']), 'peak': peak,
            'peak_day': peak_idx + 1,
            'peak_date': c['dates'][peak_idx],
            'after_peak': len(c['heights']) - peak_idx - 1,
            'end_reason': c['end_reason'],
            'natural': c['end_reason'] == '自然结束',
            'leaders_at_peak': c['leaders'][peak_idx] if peak_idx < len(c['leaders']) else '',
        })

    real = [c for c in cycle_stats if c['length'] >= 2]
    natural = [c for c in real if c['natural']]

    return {
        'all_cycles': cycle_stats, 'real_cycles': real,
        'natural_cycles': natural, 'n_total': len(cycle_stats),
        'n_real': len(real), 'n_natural': len(natural),
    }


# ─────────────────────────────────────────────────────────────
# 6. 报告生成
# ─────────────────────────────────────────────────────────────
def generate_report(ah, leader_paths, hp, repairs, imitation, cycles) -> str:
    L = []
    L.append('# 连板高度年度深研 — 龙头走势·反包模仿·战法系统')
    L.append(f'*生成时间: {datetime.now():%Y-%m-%d %H:%M} · '
             f'样本 {ah["date"].iloc[0]} -> {ah["date"].iloc[-1]} · '
             f'{len(ah)} 个交易日*')
    L.append('')

    L += _sec_data_overview(ah, leader_paths, hp)
    L += _sec_height_panorama(ah, hp)
    L += _sec_leader_tracking(leader_paths, ah)
    L += _sec_pressure_analysis(ah, hp)
    L += _sec_repair_patterns(repairs, leader_paths)
    L += _sec_imitation_analysis(imitation)
    L += _sec_cycle_analysis(cycles)
    L += _sec_strategy_system(ah, hp, repairs, imitation, cycles, leader_paths)

    return '\n'.join(L)


def _sec_data_overview(ah, leader_paths, hp) -> list[str]:
    L = ['## 〇、数据说明与样本概况', '']
    L.append(f'- **分析周期**: {ah["date"].iloc[0]} -> {ah["date"].iloc[-1]}，共 **{len(ah)} 个交易日**')
    L.append(f'- **追踪龙头**: {len(leader_paths)} 只 7板+龙头个股')
    L.append(f'- **突破事件**: {hp["n_breaks"]} 个突破压力高度的交易日')
    n_per = len(hp['dragon_periods'])
    L.append(f'- **龙头周期**: {n_per} 个 7板+高度周期')
    L.append('')
    L.append('> **数据来源**: annual_height_history (逐日高度/龙头, 主板普通10%样本) + '
             '涨停历史缓存 (全A涨停池) + baostock重建涨停 (两年K线)')
    L.append('')
    L.append('> **口径说明**: 压力P5 = 此前5个交易日最高板（不含当日）; '
             '高度 = 当日全市场最高连板数; 断板 = 前一日最高板个股当日无人晋级。')
    L.append('')
    return L


def _sec_height_panorama(ah, hp) -> list[str]:
    L = ['## 一、一年市场高度全景', '']
    L.append('### 1. 最高板的高度分布（一年）')
    L.append('')
    dist = hp['height_dist']
    total = hp['total_days']
    L.append('| 当日最高板 | 天数 | 占比 | 累计占比(>=该高度) |')
    L.append('|---|---|---|---|')
    cum = 0
    for h in sorted(dist.index, reverse=True):
        cum += int(dist[h])
        L.append(f'| {int(h)}板 | {int(dist[h])} | {dist[h]/total*100:.1f}% | {cum/total*100:.0f}% |')
    L.append('')

    med = int(ah['height'].median())
    ge5 = (ah['height'].astype(float) >= 5).mean() * 100
    ge7 = (ah['height'].astype(float) >= 7).mean() * 100
    ge10 = (ah['height'].astype(float) >= 10).mean() * 100
    L.append(f'**中位 {med} 板 | {ge5:.0f}% 的日子有 5板+ | {ge7:.0f}% 有 7板+ | {ge10:.0f}% 有 10板+**')
    L.append('')

    L.append('### 2. 三大高度区')
    L.append('')
    L.append('| 高度区 | 天数 | 占比 |')
    L.append('|---|---|---|')
    for name, info in hp['zones'].items():
        L.append(f'| {name} | {info["days"]} | {info["pct"]:.1f}% |')
    L.append('')

    L.append('### 3. 月度高度演变')
    L.append('')
    ah_c = ah.copy()
    ah_c['month'] = ah_c['date'].str[:7]
    monthly = ah_c.groupby('month').agg(
        avg_h=('height', 'mean'),
        max_h=('height', 'max'),
        ge7_days=('height', lambda x: (x.astype(float) >= 7).sum()),
    ).reset_index()
    L.append('| 月份 | 平均高度 | 最高板 | 7板+天数 |')
    L.append('|---|---|---|---|')
    for _, r in monthly.iterrows():
        L.append(f'| {r["month"]} | {r["avg_h"]:.1f} | {int(r["max_h"])} | {int(r["ge7_days"])} |')
    L.append('')
    return L


def _sec_leader_tracking(leader_paths, ah) -> list[str]:
    L = ['## 二、龙头个股完整走势追踪', '']
    L.append(f'共追踪 **{len(leader_paths)} 只** 最高板 >={MIN_DRAGON_BOARD}板 的龙头个股。')
    L.append('')

    L.append('### 1. 龙头概览')
    L.append('')
    L.append('| 排名 | 龙头 | 代码 | 最高板 | 涨停总天数 | 连板段数 | 有反包 | 总板数路径 |')
    L.append('|---|---|---|---|---|---|---|---|')
    for i, lp in enumerate(leader_paths, 1):
        segs = lp.get('segments', [])
        n_repair = sum(1 for s in segs if s.get('is_repair'))
        path_str = ' -> '.join(f'{s["height"]}板' for s in segs)
        has_rep = 'Yes' if n_repair else 'No'
        L.append(f'| {i} | {lp["name"]} | {lp["code"]} | **{lp["max_board"]}板** | '
                 f'{lp["total_limit_days"]} | {len(segs)} | {has_rep} | {path_str} |')
    L.append('')

    L.append('### 2. 逐龙头连板路径详解')
    L.append('')
    for lp in leader_paths[:15]:
        L.append(f'#### {lp["name"]} ({lp["code"]}) — 最高 {lp["max_board"]}板')
        L.append('')
        segs = lp.get('segments', [])
        if not segs:
            L.append('无连板路径数据。')
            L.append('')
            continue

        L.append('| 段 | 起始日 | 结束日 | 板高 | 天数 | 反包? | 与前段间隔 | 路径 |')
        L.append('|---|---|---|---|---|---|---|---|')
        for j, seg in enumerate(segs, 1):
            boards_str = ' -> '.join(str(b) for b in seg['boards'])
            repair_tag = '反包' if seg.get('is_repair') else ''
            gap_str = f'{seg["gap_days"]}天' if seg.get('gap_days', 0) > 0 else '—'
            L.append(f'| {j} | {seg["start"]} | {seg["end"]} | '
                     f'**{seg["height"]}板** | {seg["days"]}天 | {repair_tag} | {gap_str} | {boards_str} |')
        L.append('')

        if len(segs) > 1:
            repairs_in = [s for s in segs if s.get('is_repair')]
            if repairs_in:
                L.append(f'**走势特征**: {lp["name"]}经历了 {len(segs)} 段连板，其中 '
                         f'{len(repairs_in)} 次断板后反包。')
                for r in repairs_in:
                    extra = '(创新高)' if r['height'] > r.get('prev_height', 0) else ''
                    L.append(f'  - 前段 {r["prev_height"]}板断板后，间隔 {r["gap_days"]} 天反包，'
                             f'再连 {r["height"]}板 {extra}')
            else:
                L.append(f'**走势特征**: {lp["name"]}有 {len(segs)} 段独立连板，未出现断板后反包。')
        else:
            L.append(f'**走势特征**: {lp["name"]}一段完整连板 {segs[0]["height"]}板。')
        L.append('')

    return L


def _sec_pressure_analysis(ah, hp) -> list[str]:
    L = ['## 三、压力高度与突破分析', '']
    breaks = hp['break_events']
    L.append(f'一年内共 **{len(breaks)} 个突破P5压力高度**的交易日。')
    L.append('')

    if not breaks:
        L.append('无突破事件。')
        L.append('')
        return L

    L.append('### 1. 突破事件汇总')
    L.append('')
    L.append('| 日期 | 突破高度 | P5压力 | 差值 | 龙头 | 后1日最高 | 后5日最高 |')
    L.append('|---|---|---|---|---|---|---|')
    for b in breaks:
        fmax1 = f'{int(b.get("fmax1", 0))}' if pd.notna(b.get('fmax1')) else '—'
        fmax5 = f'{int(b.get("fmax5", 0))}' if pd.notna(b.get('fmax5')) else '—'
        p5 = f'{int(b["pressure5"])}' if pd.notna(b.get('pressure5')) else '—'
        L.append(f'| {b["date"]} | {int(b["height"])}板 | {p5}板 | '
                 f'+{int(b["gap"])} | {b["leader"]} | {fmax1} | {fmax5} |')
    L.append('')

    L.append('### 2. 突破落点分层')
    L.append('')
    lo_breaks = [b for b in breaks if b['height'] <= 6]
    hi_breaks = [b for b in breaks if b['height'] >= 7]
    L.append('| 突破落点 | 次数 | 后5日最高板中位 | 后5日创新高% |')
    L.append('|---|---|---|---|')
    for label, sub in [('中低位 <=6板', lo_breaks), ('高位 >=7板', hi_breaks)]:
        if not sub:
            continue
        fmax5_vals = [b.get('fmax5') for b in sub if pd.notna(b.get('fmax5'))]
        med5 = np.median(fmax5_vals) if fmax5_vals else float('nan')
        new_high = sum(1 for v in fmax5_vals if v > 0) / len(fmax5_vals) * 100 if fmax5_vals else 0
        L.append(f'| {label} | {len(sub)} | {med5:.0f}板 | {new_high:.0f}% |')
    L.append('')

    L.append('### 3. 龙头在位期间的压力动态')
    L.append('')
    periods = hp['dragon_periods']
    if periods:
        L.append('| 周期 | 龙头 | 持续天数 | 峰值 | 进入时P5 | 突破幅度 |')
        L.append('|---|---|---|---|---|---|')
        for j, p in enumerate(periods, 1):
            entry_p5 = p['pressures5'][0] if p['pressures5'] and pd.notna(p['pressures5'][0]) else None
            entry_p5_str = f'{int(entry_p5)}板' if entry_p5 is not None else '—'
            gap = int(p['peak'] - entry_p5) if entry_p5 is not None else 0
            leader_short = p['leaders'][:12]
            L.append(f'| {j} | {leader_short} | {p["duration"]}天 | '
                     f'**{p["peak"]}板** | {entry_p5_str} | +{gap}档 |')
        L.append('')
    return L


def _sec_repair_patterns(repairs, leader_paths) -> list[str]:
    L = ['## 四、断板->反包模式研究', '']
    leader_repairs = repairs['leader_repairs']
    market_repairs = repairs['market_repairs']

    L.append(f'### 1. 龙头个股的断板反包 (共 {len(leader_repairs)} 次)')
    L.append('')
    if leader_repairs:
        L.append('| 龙头 | 首段高度 | 断板日 | 间隔 | 反包后高度 | 合计板数 | 新高? |')
        L.append('|---|---|---|---|---|---|---|')
        for r in leader_repairs:
            nh = 'Yes' if r['is_higher'] else 'No'
            L.append(f'| {r["name"]} | {r["first_height"]}板 | {r["break_date"]} | '
                     f'{r["gap_days"]}天 | {r["second_height"]}板 | {r["total_boards"]} | {nh} |')
        L.append('')
        avg_gap = np.mean([r['gap_days'] for r in leader_repairs])
        higher_pct = sum(1 for r in leader_repairs if r['is_higher']) / len(leader_repairs) * 100
        L.append(f'**反包统计**: 平均间隔 {avg_gap:.0f}天 | '
                 f'反包后创新高 {higher_pct:.0f}% | '
                 f'平均合计板数 {np.mean([r["total_boards"] for r in leader_repairs]):.1f}')
        L.append('')
    else:
        L.append('无符合条件的龙头反包事件。')
        L.append('')

    L.append(f'### 2. 市场级断板事件 (共 {len(market_repairs)} 次)')
    L.append('')
    if market_repairs:
        layers = defaultdict(list)
        for r in market_repairs:
            bh = r['broken_height']
            key = '3-4板' if bh <= 4 else ('5-6板' if bh <= 6 else '>=7板')
            layers[key].append(r)
        L.append('| 断板高度 | 次数 | 断板后新高度中位 | 高度跌幅中位 | 典型案例 |')
        L.append('|---|---|---|---|---|')
        for layer_name in ['3-4板', '5-6板', '>=7板']:
            subs = layers.get(layer_name, [])
            if not subs:
                continue
            med_new = np.median([r['new_height'] for r in subs])
            med_drop = np.median([r['height_drop'] for r in subs])
            example = subs[0]['broken_names'][:8] if subs else '—'
            L.append(f'| {layer_name}断 | {len(subs)} | {med_new:.0f}板 | '
                     f'{med_drop:+.0f}档 | {example} |')
        L.append('')

    # 典型案例
    L.append('### 3. 典型反包案例深度拆解')
    L.append('')
    case_leaders = [lp for lp in leader_paths if
                    any(s.get('is_repair') for s in lp.get('segments', []))]
    for lp in case_leaders[:5]:
        segs = lp.get('segments', [])
        repairs_list = [(i, s) for i, s in enumerate(segs) if s.get('is_repair')]
        if not repairs_list:
            continue
        L.append(f'#### 案例: {lp["name"]} (最高{lp["max_board"]}板)')
        L.append('')
        for seg_i, seg in repairs_list:
            prev_seg = segs[seg_i - 1] if seg_i > 0 else None
            if prev_seg:
                L.append(f'- **第一段**: {prev_seg["start"]}起，连板 {prev_seg["height"]}板 -> '
                         f'{prev_seg["end"]} 断板')
            L.append(f'- **间隔**: {seg["gap_days"]}天 (自然日)')
            L.append(f'- **反包**: {seg["start"]}起，连板 {seg["height"]}板')
            if seg['height'] > seg.get('prev_height', 0):
                L.append(f'- **结果**: 反包后超越前高 ({seg.get("prev_height",0)}板 -> {seg["height"]}板)')
            else:
                L.append(f'- **结果**: 反包高度 {seg["height"]}板 (前段 {seg.get("prev_height",0)}板)')
        L.append('')
        total_boards = sum(s['height'] for s in segs)
        L.append(f'**{lp["name"]}模式总结**: {len(segs)}段连板，合计 {total_boards} 个涨停板，'
                 f'最终最高 {lp["max_board"]}板。')
        is_classic = any(s['height'] > s.get('prev_height', 0) for s in segs if s.get('is_repair'))
        if is_classic:
            L.append('属于**经典反包新高模式** — 断板不是结束，反包后打出更高空间。')
        L.append('')

    return L


def _sec_imitation_analysis(imitation) -> list[str]:
    L = ['## 五、突破高度压制后的模仿与补涨', '']
    form_ims = imitation['form_imitations']
    L.append(f'### 1. 形态模仿 (龙头确认后的跟风连板股)')
    L.append('')
    L.append(f'分析 {len(imitation["dragon_confirms"])} 只龙头确认后 {IMITATION_LAG} 个交易日内，'
             f'其他个股出现 3板+ 连板的情况。')
    L.append('')

    if form_ims:
        L.append('| 龙头 | 确认日 | 高度 | 跟风3板+个股数 | 跟风峰值板数 | 平均滞后天数 |')
        L.append('|---|---|---|---|---|---|')
        for fi in form_ims:
            d = fi['dragon']
            ims = fi['imitators']
            max_lb = max(im['lb'] for im in ims) if ims else 0
            avg_lag = np.mean([im['lag'] for im in ims]) if ims else 0
            L.append(f'| {d["name"]} | {d["date"]} | {d["height"]}板 | '
                     f'{fi["n_imitators"]} | {max_lb}板 | {avg_lag:.1f}天 |')
        L.append('')

        total_imitators = sum(fi['n_imitators'] for fi in form_ims)
        avg_per = total_imitators / len(form_ims) if form_ims else 0
        L.append(f'**形态模仿统计**: 平均每只龙头确认后带出 **{avg_per:.1f}** 只 3板+跟风股。')
        L.append('')

        all_lags = [im['lag'] for fi in form_ims for im in fi['imitators']]
        if all_lags:
            L.append('| 滞后天数 | 出现次数 | 占比 |')
            L.append('|---|---|---|')
            lag_counts = Counter(all_lags)
            for lag in sorted(lag_counts.keys()):
                pct = lag_counts[lag] / len(all_lags) * 100
                L.append(f'| 第{lag}天 | {lag_counts[lag]} | {pct:.1f}% |')
            L.append('')
            peak_lag = Counter(all_lags).most_common(1)[0][0]
            L.append(f'**补涨高峰期**: 龙头确认后 **第{peak_lag}天** 跟风最多。')
            L.append('')
    else:
        L.append('未检测到明显的形态模仿。')
        L.append('')

    sector_ims = imitation['sector_imitations']
    L.append(f'### 2. 板块概念传导 (龙头板块内的补涨)')
    L.append('')
    if sector_ims:
        L.append(f'共发现 **{len(sector_ims)}** 个板块概念传导案例。')
        L.append('')
        L.append('| 龙头 | 板块概念 | 板块内个股数 | 补涨峰值日 | 峰值涨停数 |')
        L.append('|---|---|---|---|---|')
        for si in sector_ims[:20]:
            d = si['dragon']
            peak_val = max(si['sector_zt_by_day']) if si['sector_zt_by_day'] else 0
            L.append(f'| {d["name"]} | {si["mainline"]} | {si["n_same_sector"]} | '
                     f'第{si["peak_day"]}天 | {peak_val}只 |')
        L.append('')
        delays = [si['peak_day'] for si in sector_ims]
        if delays:
            L.append(f'**板块传导延迟**: 龙头确认后板块补涨峰值出现在第 **{np.median(delays):.0f}天** (中位)。')
            L.append('')
    else:
        L.append('板块归因数据不足，暂无法分析概念传导。')
        L.append('')
    return L


def _sec_cycle_analysis(cycles) -> list[str]:
    L = ['## 六、高度周期形状 (一年维度)', '']
    real = cycles['real_cycles']
    natural = cycles['natural_cycles']

    L.append(f'周期定义: 最高板 >3 板的连续交易日段。共切出 **{cycles["n_total"]} 段**:')
    L.append(f'- 真周期 (>=2天): **{cycles["n_real"]} 段**')
    L.append(f'- 自然结束: **{cycles["n_natural"]} 段**')
    L.append('')

    if not real:
        L.append('样本不足。')
        L.append('')
        return L

    L.append('### 1. 全部周期一览')
    L.append('')
    L.append('| # | 起 | 止 | 天数 | 峰值 | 峰值在 | 见峰后剩 | 峰值龙头 | 结束原因 |')
    L.append('|---|---|---|---|---|---|---|---|---|')
    for j, c in enumerate(sorted(real, key=lambda x: x['start']), 1):
        leader_short = c['leaders_at_peak'][:8] if c['leaders_at_peak'] else ''
        L.append(f'| {j} | {c["start"]} | {c["end"]} | {c["length"]}天 | '
                 f'**{c["peak"]}板** | 第{c["peak_day"]}天 | {c["after_peak"]}天 | '
                 f'{leader_short} | {c["end_reason"]} |')
    L.append('')

    if natural:
        lengths = [c['length'] for c in natural]
        peaks = [c['peak'] for c in natural]
        peak_days = [c['peak_day'] for c in natural]
        afters = [c['after_peak'] for c in natural]

        L.append(f'### 2. 自然结束周期的形状 (n={len(natural)})')
        L.append('')
        L.append(f'- 周期长度中位 **{np.median(lengths):.0f} 天** ({min(lengths)}-{max(lengths)})')
        L.append(f'- 峰值高度中位 **{np.median(peaks):.0f} 板** ({min(peaks)}-{max(peaks)})')
        L.append(f'- 峰值出现在周期第 **{np.median(peak_days):.0f} 天** (中位)')
        L.append(f'- 见峰之后还剩 **{np.median(afters):.0f} 天** (中位)')
        L.append('')

        L.append('### 3. 清过某个高度后还能走多远')
        L.append('')
        L.append('| 清掉这一档 | 周期数 | 最终峰值中位 | 就此止步% |')
        L.append('|---|---|---|---|')
        for lvl in (4, 5, 6, 7, 8, 10):
            sub = [c for c in real if c['peak'] >= lvl]
            if not sub:
                continue
            stop = sum(1 for c in sub if c['peak'] == lvl) / len(sub) * 100
            L.append(f'| {lvl}板 | {len(sub)} | {np.median([c["peak"] for c in sub]):.0f}板 | {stop:.0f}% |')
        L.append('')
    return L


def _sec_strategy_system(ah, hp, repairs, imitation, cycles, leader_paths) -> list[str]:
    L = ['## 七、系统战法 (六大维度)', '']
    L.append(f'以下战法基于一年 {len(ah)} 个交易日的全样本提炼，每条带触发条件、样本数、和操作建议。')
    L.append('')

    # 战法A: 龙头高度突破
    L.append('### 战法A: 龙头高度突破战法')
    L.append('')
    breaks = hp['break_events']
    lo_br = [b for b in breaks if b['height'] <= 6]
    hi_br = [b for b in breaks if b['height'] >= 7]
    L.append(f'- **触发**: 最高板突破前5日压力高度 (P5)')
    L.append(f'- **样本**: {len(breaks)} 个突破日 (中低位 {len(lo_br)} + 高位 {len(hi_br)})')
    L.append('')
    if lo_br:
        fmax5_lo = [b.get('fmax5') for b in lo_br if pd.notna(b.get('fmax5'))]
        nh_lo = sum(1 for v in fmax5_lo if v > 0) / len(fmax5_lo) * 100 if fmax5_lo else 0
        L.append(f'**中低位突破 (<=6板)**: 后5日再创新高 {nh_lo:.0f}% (n={len(lo_br)})')
        L.append(f'  - 操作: 高度还在爬升阶段，可关注新首板标的，不追已高位的龙头')
    L.append('')
    if hi_br:
        fmax5_hi = [b.get('fmax5') for b in hi_br if pd.notna(b.get('fmax5'))]
        nh_hi = sum(1 for v in fmax5_hi if v > 0) / len(fmax5_hi) * 100 if fmax5_hi else 0
        L.append(f'**高位突破 (>=7板)**: 后5日再创新高 {nh_hi:.0f}% (n={len(hi_br)})')
        L.append(f'  - 操作: 高度见顶概率增大，手中高位股分批兑现，不追龙头')
    L.append('')

    # 战法B: 断板反包
    L.append('### 战法B: 断板反包战法')
    L.append('')
    lr = repairs['leader_repairs']
    L.append(f'- **触发**: 龙头 >={MIN_REPAIR_BOARD}板断板后，{MAX_REPAIR_GAP*2}天内再次涨停')
    L.append(f'- **样本**: {len(lr)} 次龙头反包事件')
    L.append('')
    if lr:
        gap_vals = [r['gap_days'] for r in lr]
        higher_pct = sum(1 for r in lr if r['is_higher']) / len(lr) * 100
        L.append(f'- 反包间隔中位 **{np.median(gap_vals):.0f}天**')
        L.append(f'- 反包后超越前高比例 **{higher_pct:.0f}%**')
        L.append(f'- 平均合计涨停板数 **{np.mean([r["total_boards"] for r in lr]):.1f}板**')
        L.append('')
        L.append('**操作**:')
        L.append('  - 龙头断板后不急于清仓，观察 3-5 天是否有反包信号')
        L.append('  - 反包确认 (再次涨停) 后可轻仓跟进，止损设在反包日低点')
        L.append('  - 断板后超过 10 天未反包，基本可以放弃')
    L.append('')

    # 战法C: 模仿补涨
    L.append('### 战法C: 模仿补涨战法')
    L.append('')
    form_ims = imitation['form_imitations']
    L.append(f'- **触发**: 龙头达到 7板+ 确认后，同期出现 3板+ 跟风股')
    L.append(f'- **样本**: {len(form_ims)} 个龙头->跟风案例')
    L.append('')
    if form_ims:
        total_imitators = sum(fi['n_imitators'] for fi in form_ims)
        avg_per = total_imitators / len(form_ims)
        all_lags = [im['lag'] for fi in form_ims for im in fi['imitators']]
        peak_lag = Counter(all_lags).most_common(1)[0][0] if all_lags else 0
        L.append(f'- 平均每只龙头带出 **{avg_per:.1f}只** 3板+跟风股')
        L.append(f'- 跟风高峰在龙头确认后第 **{peak_lag}天**')
        L.append('')
        L.append('**操作**:')
        L.append(f'  - 龙头确认 7板+ 后，重点关注第 {max(1,peak_lag-2)}-{peak_lag+2} 天的新首板标的')
        L.append('  - 跟风股优选: 与龙头同题材/概念 > 纯形态模仿')
        L.append('  - 跟风股的预期高度 <= 龙头高度，不宜追高')
    L.append('')

    # 战法D: 高度周期
    L.append('### 战法D: 市场高度周期战法')
    L.append('')
    natural = cycles['natural_cycles']
    L.append(f'- **样本**: {len(natural)} 个自然结束的高度周期')
    L.append('')
    if natural:
        afters = [c['after_peak'] for c in natural]
        pct_1d = sum(1 for a in afters if a <= 1) / len(afters) * 100
        med_after = np.median(afters)
        med_len = np.median([c['length'] for c in natural])
        L.append(f'- 周期长度中位 **{med_len:.0f}天**')
        L.append(f'- 见峰后 **{pct_1d:.0f}%** 的周期在 1 天内结束')
        L.append(f'- 见峰后剩余中位 **{med_after:.0f}天**')
        L.append('')
        L.append('**操作**:')
        L.append('  - 高度创新高当天 = 开始减仓的信号，不是加仓')
        L.append(f'  - 周期前 {int(med_len//2)} 天是高度爬升区，可以积极参与')
        L.append(f'  - 见峰后跑路要快 — {pct_1d:.0f}% 概率只给1天反应时间')
    L.append('')

    # 战法E: 接力换手
    L.append('### 战法E: 接力换手战法')
    L.append('')
    mr = repairs['market_repairs']
    hi_breaks_mr = [r for r in mr if r['broken_height'] >= 7]
    lo_breaks_mr = [r for r in mr if r['broken_height'] <= 6]
    L.append(f'- **触发**: 最高板龙头断板')
    L.append(f'- **样本**: {len(mr)} 次断板 (高位>=7板断 {len(hi_breaks_mr)} + 低位<=6板断 {len(lo_breaks_mr)})')
    L.append('')
    if hi_breaks_mr:
        drop_vals = [r['height_drop'] for r in hi_breaks_mr]
        new_h_vals = [r['new_height'] for r in hi_breaks_mr]
        L.append(f'**高位断板 (>=7板)**:')
        L.append(f'  - 断板后新高度中位 **{np.median(new_h_vals):.0f}板** (跌 {np.median(drop_vals):+.0f}档)')
        L.append(f'  - 操作: 高位断板是**周期结束信号**，清仓高位股，等新周期启动')
    L.append('')
    if lo_breaks_mr:
        new_h_lo = [r['new_height'] for r in lo_breaks_mr]
        L.append(f'**低位断板 (<=6板)**:')
        L.append(f'  - 断板后新高度中位 **{np.median(new_h_lo):.0f}板**')
        L.append(f'  - 操作: 低位断板多为洗盘不是退潮，可等新方向首板出现')
    L.append('')

    # 战法F: 龙头辨识
    L.append('### 战法F: 龙头辨识战法')
    L.append('')
    L.append(f'- **样本**: {len(leader_paths)} 只 7板+龙头的共性特征')
    L.append('')
    if leader_paths:
        total_segs = [len(lp.get('segments', [])) for lp in leader_paths]
        has_repair = sum(1 for lp in leader_paths
                         if any(s.get('is_repair') for s in lp.get('segments', [])))
        repair_pct = has_repair / len(leader_paths) * 100
        max_boards = [lp['max_board'] for lp in leader_paths]
        L.append(f'- 最高板分布: 中位 **{int(np.median(max_boards))}板**, 最高 **{max(max_boards)}板**')
        L.append(f'- 有反包经历的龙头: **{repair_pct:.0f}%** ({has_repair}/{len(leader_paths)})')
        L.append(f'- 平均连板段数: **{np.mean(total_segs):.1f}段**')
        L.append('')
        L.append('**龙头识别要点**:')
        L.append('  - 真龙头多数有断板反包经历 — 一路无阻的反而是少数')
        L.append('  - 龙头的赚钱效应在 4-5板 开始显现，7板以上进入市场共识')
        L.append('  - 首板题材辨识: 龙头首板当天的题材概念是后续跟风的方向指引')
        L.append('  - 监管节奏: 异动公告/停牌核查 = 交易所盖章，不是利空是确认')
    L.append('')

    # 总结表
    L.append('---')
    L.append('')
    L.append('### 战法体系总结')
    L.append('')
    L.append('| 战法 | 核心逻辑 | 适用阶段 | 风险等级 |')
    L.append('|---|---|---|---|')
    L.append('| A-高度突破 | 压力高度被突破 = 高度还在往上 | 周期初中期 | ** |')
    L.append('| B-断板反包 | 龙头断板后等反包信号 | 龙头断板后3-5天 | *** |')
    L.append('| C-模仿补涨 | 龙头确认后跟风股机会 | 龙头确认7板+后 | ** |')
    L.append('| D-高度周期 | 周期见峰 = 减仓信号 | 全周期 | * |')
    L.append('| E-接力换手 | 高位断板清仓/低位断板等新方向 | 断板当日 | *** |')
    L.append('| F-龙头辨识 | 识别真龙头的特征模式 | 周期启动初期 | ** |')
    L.append('')
    L.append('> **使用原则**: 六大战法不是独立使用的，需要**交叉验证**。'
             '例如: 战法A (高度突破) 触发后，同时检查战法D (周期位置) 确认不是见峰前最后一次突破；'
             '战法B (反包) 触发后，结合战法C (模仿) 看是否有跟风盘确认赚钱效应。')
    L.append('')
    L.append('> **风险提示**: 所有战法基于历史统计，不构成投资建议。'
             '连板高度分析的核心认知是 **高度是均值回归量，不是动量量** — '
             '突破高度压制不等于能赚钱，它只说明行情高度还在往上。')
    L.append('')
    return L


# ─────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────
def main():
    t0 = datetime.now()
    print(f'[{t0:%H:%M:%S}] 连板高度年度深研 — 开始')

    print(f'[{datetime.now():%H:%M:%S}] 载入 annual_height_history...')
    ah = load_annual_height()
    print(f'  {len(ah)} 行, {ah["date"].iloc[0]} -> {ah["date"].iloc[-1]}')

    print(f'[{datetime.now():%H:%M:%S}] 载入涨停历史缓存...')
    zt = load_zt_cache()
    print(f'  {len(zt)} 行')

    print(f'[{datetime.now():%H:%M:%S}] 载入 baostock 涨停历史...')
    bs = load_baostock_limit()
    print(f'  {len(bs)} 行')

    print(f'[{datetime.now():%H:%M:%S}] 载入板块归因缓存...')
    plates = load_plate_cache()
    print(f'  {len(plates)} 行')

    print(f'[{datetime.now():%H:%M:%S}] 1. 重建龙头路径...')
    leader_paths = build_leader_paths(ah, zt, bs)
    print(f'  {len(leader_paths)} 只龙头')

    print(f'[{datetime.now():%H:%M:%S}] 2. 压力高度分析...')
    hp_result = analyze_height_pressure(ah)
    print(f'  {hp_result["n_breaks"]} 个突破日')

    print(f'[{datetime.now():%H:%M:%S}] 3. 断板反包分析...')
    repairs = analyze_repair_patterns(leader_paths, ah)
    print(f'  {repairs["total_repairs"]} 次反包')

    print(f'[{datetime.now():%H:%M:%S}] 4. 模仿补涨分析...')
    imit = analyze_imitation(ah, zt, plates)
    print(f'  {len(imit["form_imitations"])} 个跟风案例')

    print(f'[{datetime.now():%H:%M:%S}] 5. 高度周期分析...')
    cyc = analyze_height_cycles(ah)
    print(f'  {cyc["n_real"]} 个周期')

    print(f'[{datetime.now():%H:%M:%S}] 6. 生成报告...')
    report = generate_report(ah, leader_paths, hp_result, repairs, imit, cyc)

    out_path = os.path.join(OUTPUT_DIR, 'market_height_annual_report.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f'\n{"="*70}')
    print(report[:3000])
    print(f'... (报告共 {len(report)} 字符)')
    print(f'{"="*70}')
    print(f'\n报告已保存: {out_path}')
    print(f'耗时 {(datetime.now() - t0).total_seconds():.1f}s')


if __name__ == '__main__':
    main()
