"""高标追踪 (leader_tracker) — 连板高标身份 / 引力 / 生死→情绪信号。

独立增量模块 (见 [[incremental-modularization]]):
  build_leader_tracker(report_date=None) -> dict
  render_leader_tracker_html(result)     -> str
主报告只 import→调用→拼接; 任何失败静默兜底, 不阻断日报。

与既有 index-phase-leaders (phase_resonance.build_turning_summary, 按区间前复权涨幅
排 Top5) 是互补的两个维度: 那个是"涨幅榜", 本模块按连板数/人气识别高标, 并量化其
生死对短线情绪的信号。真源 = 涨停历史缓存 (连板数完整, 193 交易日)。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from html import escape

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from breakthrough_verify import (  # noqa: E402
    load_zt_cache, detect_stale_dates, load_sentiment_cache, _ladder_score,
)

# ─────────────────────────────────────────────────────────────
# 常量 (阈值来自 193 天回测, 与 breakthrough_verify / 记忆口径对齐)
# ─────────────────────────────────────────────────────────────
HIGH_BOARD = 6          # ≥ 此板数算"高标" (回测样本口径)
LONELY_MIN_H = 5        # 孤峰最低板数门槛
POPULARITY_WINDOW = 20  # 人气龙头: 近 N 个真实交易日内涨停次数
OCC_HOT = 0.60          # A/D 占比 up/(up+down) ≥ 此值 = 过热 (≈ up/down 1.5)
OCC_COLD = 0.35         # ≤ 此值 = 冰点 (≈ up/down 0.54)
CATCHUP_MIN_PCT = 5.0   # 补涨: 同题材未涨停但今日涨幅 ≥ 此值 (%)


def _norm_date(s) -> str:
    """任意日期 → YYYYMMDD (去横线取前 8 位)。"""
    return str(s).replace('-', '').strip()[:8]


def _occ(up, down):
    """A/D 占比 up/(up+down); 家数残缺返回 None。"""
    u, d = float(up or 0), float(down or 0)
    if u + d < 1:
        return None
    return u / (u + d)


def _regime(occ):
    if occ is None:
        return None
    if occ >= OCC_HOT:
        return '过热'
    if occ <= OCC_COLD:
        return '冰点'
    return '中性'


# ─────────────────────────────────────────────────────────────
# ① 上下文: 涨停全史 + 鬼影日 + 逐日 {code:height} + 名称 + A/D 占比
# ─────────────────────────────────────────────────────────────
def _build_context(report_date=None) -> dict:
    """加载并组织本模块所需的全部真源数据。

    返回:
      as_of        报告日 (YYYYMMDD, 缓存内 <= report_date 的最新真实交易日)
      by_day       {date: {code: height}} (含 stale 日, 判进阶时跳过)
      real_dates   非 stale 交易日升序列表 (<= as_of)
      stale        鬼影日集合
      name_by_code {code: name} (取每只票最新一次涨停时的名称)
      occ_by_date  {date: A/D 占比 up/(up+down)}
    """
    df = load_zt_cache()
    stale = detect_stale_dates(df)

    if report_date:
        cutoff = _norm_date(report_date)
        df = df[df['date'] <= cutoff].copy()
    if df.empty:
        raise ValueError('涨停缓存在报告日前无数据')

    all_dates = sorted(df['date'].unique(), key=str)
    as_of = all_dates[-1]
    by_day = {d: dict(zip(g['code'], g['height'])) for d, g in df.groupby('date')}
    real_dates = [d for d in all_dates if d not in stale]

    name_by_code = dict(
        zip(df.sort_values('date').drop_duplicates('code', keep='last')['code'],
            df.sort_values('date').drop_duplicates('code', keep='last')['name'])
    )

    sent = load_sentiment_cache()
    occ_by_date = {}
    if not sent.empty:
        for _, r in sent.iterrows():
            occ_by_date[_norm_date(r['date'])] = _occ(r['up'], r['down'])

    return {
        'as_of': as_of, 'by_day': by_day, 'real_dates': real_dates,
        'stale': stale, 'name_by_code': name_by_code, 'occ_by_date': occ_by_date,
    }


def _lifeline(by_day, real_dates, code, as_of):
    """回溯该 code 的连板生命线 → (first_board_date, consec_days)。

    从 as_of 起向前走真实交易日, 要求 code 连续在池且板数每档 -1 递减;
    跳过 stale 日 (real_dates 已排除)。断裂即停。
    """
    if as_of not in by_day or code not in by_day[as_of]:
        return None, 0
    try:
        idx = real_dates.index(as_of)
    except ValueError:
        return None, 0
    h = by_day[as_of][code]
    first = as_of
    consec = 1
    for j in range(idx - 1, -1, -1):
        d = real_dates[j]
        hj = by_day.get(d, {}).get(code)
        if hj is None or hj != h - 1:
            break
        h = hj
        first = d
        consec += 1
    return first, consec


# ─────────────────────────────────────────────────────────────
# ③ 生死 → 情绪信号 (每次运行从全史实时重算, 自更新/诚实)
# ─────────────────────────────────────────────────────────────
def build_leader_death_signal(ctx) -> dict:
    """高标断板 × 当日情绪分层的次日结局表 (占比口径, 实时重算)。

    断板事件: 昨日(最近真实交易日)存在 ≥HIGH_BOARD 板的 code 队列, 今日与涨停池
    交集为空 = 全员断板。事件落在"断板当日 cur", 按 cur 的 A/D 占比分档;
    走弱 = 次日占比 < 断板日占比。样本小 → 以"历史频率提示"呈现, 非铁律。
    """
    by_day = ctx['by_day']
    real_dates = ctx['real_dates']
    occ = ctx['occ_by_date']
    as_of = ctx['as_of']

    buckets = {'过热': [], '中性': [], '冰点': []}  # 每项: (weakened(bool|None), ad_delta(float|None))
    event_today = False
    regime_today = None

    for i in range(1, len(real_dates)):
        prev, cur = real_dates[i - 1], real_dates[i]
        prev_high = {c for c, h in by_day.get(prev, {}).items() if h >= HIGH_BOARD}
        if not prev_high:
            continue
        cur_codes = set(by_day.get(cur, {}).keys())
        if not prev_high.isdisjoint(cur_codes):
            continue  # 仍有高标在池 → 未全员断板
        occ_break = occ.get(cur)
        reg = _regime(occ_break)
        nxt = real_dates[i + 1] if i + 1 < len(real_dates) else None
        occ_next = occ.get(nxt) if nxt else None
        weakened = (occ_next is not None and occ_break is not None and occ_next < occ_break)
        delta = (occ_next - occ_break) if (occ_next is not None and occ_break is not None) else None
        if reg in buckets:
            buckets[reg].append((weakened if occ_next is not None else None, delta))
        if cur == as_of:
            event_today = True
            regime_today = reg

    table = []
    for reg in ('过热', '中性', '冰点'):
        rows = buckets[reg]
        n = len(rows)
        outc = [w for w, _ in rows if w is not None]
        deltas = [d for _, d in rows if d is not None]
        table.append({
            'regime': reg,
            'n': n,
            'weaken_rate': round(100 * sum(outc) / len(outc), 0) if outc else None,
            'ad_delta': round(sum(deltas) / len(deltas), 3) if deltas else None,
        })

    action = _death_action(event_today, regime_today, table)
    return {
        'event_today': event_today, 'regime': regime_today,
        'ad_today': occ.get(as_of), 'table': table, 'action': action,
    }


def _row_by_regime(table, reg):
    for r in table:
        if r['regime'] == reg:
            return r
    return {'weaken_rate': None}


def _death_action(event_today, regime, table) -> str:
    if not event_today:
        return '今日无高标断板事件 → 情绪信号中性, 按既有节奏跟踪。'
    if regime == '过热':
        r = _row_by_regime(table, '过热')['weaken_rate']
        pct = f'{int(r)}%' if r is not None else '多数'
        return (f'⚠️ 高标今日断板 + 盘面过热 → 历史同类次日 {pct} 概率退潮; '
                f'兑现节奏前置一档, 不追高、优先减弱转强。')
    if regime == '冰点':
        r = _row_by_regime(table, '冰点')['weaken_rate']
        pct = f'{int(100 - r)}%' if r is not None else '多数'
        return (f'高标今日断板 + 盘面冰点 → 历史同类次日 {pct} 概率企稳反弹; '
                f'别追空, 冰点可低吸主线核心, 等待新龙头。')
    return '高标今日断板 + 盘面中性 → 方向未定, 看次日承接与量能再定, 不预设方向。'


# ─────────────────────────────────────────────────────────────
# 题材归因查表 (em 缓存 code 是内部 sh/sz, ZT 是 6 位裸码, 需归一化对齐)
# ─────────────────────────────────────────────────────────────
def _to_internal(code):
    """6 位裸码 → 东财内部 sh/sz/bj 格式, 供 attributions 查表。"""
    try:
        from data_sources.models import normalize_code
        return normalize_code(code)
    except Exception:
        c = str(code).zfill(6)
        if c[0] in '5689':
            return 'sh' + c
        if c[0] in '48' or c.startswith('92'):
            return 'bj' + c
        return 'sz' + c


def _theme_of(attributions, code):
    """返回 (sub, ml) 或 None。attributions 键是内部格式。"""
    if not attributions:
        return None
    return attributions.get(_to_internal(code))


# ─────────────────────────────────────────────────────────────
# ① 高标身份卡
# ─────────────────────────────────────────────────────────────
def _build_identity(ctx, attributions) -> dict:
    by_day = ctx['by_day']
    real_dates = ctx['real_dates']
    as_of = ctx['as_of']
    names = ctx['name_by_code']
    today = by_day.get(as_of, {})

    if not today:
        return {'space_leader': None, 'popularity_leader': None, 'top_cohort': []}

    max_h = max(today.values())
    top_codes = sorted([c for c, h in today.items() if h == max_h])
    top_cohort = [{'code': c, 'name': names.get(c, c), 'height': max_h} for c in top_codes]

    lead_code = top_codes[0]
    first_bd, consec = _lifeline(by_day, real_dates, lead_code, as_of)
    theme = _theme_of(attributions, lead_code)
    space_leader = {
        'code': lead_code, 'name': names.get(lead_code, lead_code),
        'height': max_h, 'first_board_date': first_bd, 'consec_days': consec,
        'today_status': f'{max_h}板' + ('(孤峰候选)' if len(top_codes) == 1 else f'(并列{len(top_codes)}只)'),
        'theme': theme[1] if theme else None,
    }

    # 人气龙头: 近 POPULARITY_WINDOW 真实交易日涨停次数最多 (ZT 无成交额, 用频次近似)
    window = real_dates[-POPULARITY_WINDOW:]
    freq = defaultdict(int)
    for d in window:
        for c in by_day.get(d, {}):
            freq[c] += 1
    pop_leader = None
    if freq:
        pc = max(freq, key=lambda c: (freq[c], today.get(c, 0)))
        pt = _theme_of(attributions, pc)
        pop_leader = {
            'code': pc, 'name': names.get(pc, pc), 'zt_count_20d': freq[pc],
            'height': today.get(pc, 0), 'theme': pt[1] if pt else None,
        }

    return {'space_leader': space_leader, 'popularity_leader': pop_leader,
            'top_cohort': top_cohort}


# ─────────────────────────────────────────────────────────────
# ② 高标引力: 承接梯队 / 孤峰 / 抱团 / 模仿 / 补涨
# ─────────────────────────────────────────────────────────────
def _build_gravity(ctx, identity, attributions) -> dict:
    by_day = ctx['by_day']
    as_of = ctx['as_of']
    names = ctx['name_by_code']
    today = by_day.get(as_of, {})

    if not today:
        return _empty_gravity()

    max_h = max(today.values())
    ladder, h3, h4, h5, h6p = _ladder_score(today)
    n_at_max = sum(1 for h in today.values() if h == max_h)
    n_at_max_1 = sum(1 for h in today.values() if h == max_h - 1)
    n_at_max_2 = sum(1 for h in today.values() if h == max_h - 2)
    echelon = {'max_h': max_h, 'n_at_max': n_at_max, 'n_at_max_1': n_at_max_1,
               'n_at_max_2': n_at_max_2, 'ladder': ladder}

    is_lonely = (max_h >= LONELY_MIN_H) and (n_at_max_1 + n_at_max_2 <= 1)
    if is_lonely:
        reason = (f'{max_h}板高标下方仅 {n_at_max_1}+{n_at_max_2} 只承接, '
                  f'空中楼阁, 缺赚钱效应传导。')
    else:
        reason = f'{max_h}板下方 {n_at_max_1}/{n_at_max_2} 档承接' + (
            '较厚, 梯队健康。' if (n_at_max_1 + n_at_max_2) >= 2 else '一般。')

    # 抱团: 空间高标同题材今日在池只数
    lead = identity.get('space_leader') or {}
    lead_theme = lead.get('theme')
    lead_code = lead.get('code')
    cluster = {'theme': lead_theme, 'count': 0, 'members': []}
    imitation = {'count': 0, 'members': []}
    if lead_theme and attributions:
        for c in today:
            th = _theme_of(attributions, c)
            if th and th[1] == lead_theme:
                cluster['members'].append({'code': c, 'name': names.get(c, c),
                                           'height': today[c]})
                if c != lead_code and today[c] <= 2:
                    imitation['members'].append({'code': c, 'name': names.get(c, c),
                                                 'height': today[c]})
        cluster['count'] = len(cluster['members'])
        imitation['count'] = len(imitation['members'])

    catchup = _build_catchup(ctx, lead_theme, attributions, set(today.keys()))

    return {'echelon': echelon, 'is_lonely_peak': is_lonely,
            'lonely_peak_reason': reason, 'cluster': cluster,
            'imitation': imitation, 'catchup': catchup}


def _empty_gravity():
    return {'echelon': {'max_h': 0, 'n_at_max': 0, 'n_at_max_1': 0,
            'n_at_max_2': 0, 'ladder': 0}, 'is_lonely_peak': False,
            'lonely_peak_reason': '', 'cluster': {'theme': None, 'count': 0, 'members': []},
            'imitation': {'count': 0, 'members': []},
            'catchup': {'count': 0, 'members': [], 'partial': True}}


def _build_catchup(ctx, lead_theme, attributions, today_zt_codes) -> dict:
    """补涨 (best-effort): 同题材、今日未涨停但涨幅高的票。

    题材归因缓存只覆盖曾进涨停池的股 → 只能在已归因宇宙内 best-effort;
    需价格缓存算今日涨幅。任何缺失 → partial=True, 不吹全市场扫描。
    """
    out = {'count': 0, 'members': [], 'partial': True}
    if not (lead_theme and attributions):
        return out
    try:
        from paths import PRICE_CACHE
        if not os.path.exists(PRICE_CACHE):
            return out
        same_theme = {c[2:] if len(c) > 2 and c[:2].isalpha() else c
                      for c, (_, ml) in attributions.items() if ml == lead_theme}
        cand = {c.zfill(6) for c in same_theme} - {str(x).zfill(6) for x in today_zt_codes}
        if not cand:
            out['partial'] = False
            return out
        pct = _today_pct(ctx, cand)
        members = [{'code': c, 'name': ctx['name_by_code'].get(c, c),
                    'pct': round(p, 1)} for c, p in pct.items() if p >= CATCHUP_MIN_PCT]
        members.sort(key=lambda m: -m['pct'])
        out['members'] = members[:8]
        out['count'] = len(members)
        out['partial'] = True  # 归因宇宙受限, 恒标 partial
    except Exception:
        pass
    return out


def _today_pct(ctx, codes) -> dict:
    """从价格缓存算 codes 今日涨幅% (今收/上一真实交易日收 -1)。缺失跳过。"""
    from paths import PRICE_CACHE
    as_of = ctx['as_of']
    df = pd.read_csv(PRICE_CACHE, dtype={'code': str}) if os.path.exists(PRICE_CACHE) else pd.DataFrame()
    if df.empty or 'date' not in df.columns:
        return {}
    df['d'] = df['date'].astype(str).map(_norm_date)
    col = 'close_qfq' if 'close_qfq' in df.columns else (
        'close_legacy' if 'close_legacy' in df.columns else (
            'close' if 'close' in df.columns else None))
    if col is None:
        return {}
    from breakthrough_verify import _norm_code as _nc
    df['c6'] = df['code'].map(_nc)
    sub = df[df['c6'].isin(codes)].copy()
    sub[col] = pd.to_numeric(sub[col], errors='coerce')
    dates = sorted([d for d in sub['d'].unique() if d <= as_of])
    if len(dates) < 2:
        return {}
    d0, d1 = dates[-2], dates[-1]
    if d1 != as_of:
        return {}
    prev = dict(zip(sub[sub['d'] == d0]['c6'], sub[sub['d'] == d0][col]))
    cur = dict(zip(sub[sub['d'] == d1]['c6'], sub[sub['d'] == d1][col]))
    out = {}
    for c in codes:
        p0, p1 = prev.get(c), cur.get(c)
        if p0 and p1 and p0 > 0:
            out[c] = (p1 / p0 - 1) * 100
    return out


# ─────────────────────────────────────────────────────────────
# 编排: build_leader_tracker
# ─────────────────────────────────────────────────────────────
def build_leader_tracker(report_date=None) -> dict:
    """主入口。任何子步骤失败静默兜底, 保证返回可渲染 dict (不抛异常)。"""
    ctx = _build_context(report_date)

    # 题材归因 (纯缓存, 无网络); 失败不影响身份/生死
    attributions = {}
    try:
        from em_stock_plates import load_all_attributions
        attributions = load_all_attributions() or {}
    except Exception:
        attributions = {}

    identity = _build_identity(ctx, attributions)
    gravity = _build_gravity(ctx, identity, attributions)
    try:
        death = build_leader_death_signal(ctx)
    except Exception:
        death = {'event_today': False, 'regime': None, 'ad_today': None,
                 'table': [], 'action': '生死信号计算失败, 已跳过。'}

    result = {'as_of': ctx['as_of'], 'identity': identity,
              'gravity': gravity, 'death_signal': death}
    result.update(_flat_summary(result))
    return result


def _flat_summary(result) -> dict:
    """看板用扁平摘要: status / signal / stage / headline。"""
    ident = result['identity']
    grav = result['gravity']
    death = result['death_signal']
    lead = ident.get('space_leader')

    if not lead:
        return {'status': '无涨停高标', 'signal': '空窗',
                'stage': '无高标', 'headline': '今日无连板高标, 情绪处冰点/空窗期。'}

    max_h = lead['height']
    nm = lead['name']
    if max_h < HIGH_BOARD:
        stage = '低位无高标'
    elif grav['is_lonely_peak']:
        stage = '孤峰'
    else:
        stage = '梯队健在'

    status = f"空间{max_h}板·{nm}"
    if lead.get('consec_days'):
        status += f"({lead['consec_days']}连板)"

    if death['event_today']:
        signal = f"高标断板·{death['regime'] or '中性'}"
        headline = death['action']
    elif grav['is_lonely_peak']:
        signal = '孤峰预警'
        headline = f"{status} 成孤峰: {grav['lonely_peak_reason']} 高标一旦断板情绪易失速, 别接力空中票。"
    elif max_h < HIGH_BOARD:
        signal = '高度不足'
        headline = f"当前最高仅{max_h}板({nm}), 未及高标线({HIGH_BOARD}板); 情绪偏弱, 等待新龙头突破。"
    else:
        cl = grav['cluster']
        signal = '高标健在'
        extra = f", 同题材{cl['theme']}抱团{cl['count']}只" if cl.get('count') else ''
        headline = f"{status} 高标健在{extra}; 梯队承接尚可, 持有观察断板信号。"

    return {'status': status, 'signal': signal, 'stage': stage, 'headline': headline}


# ─────────────────────────────────────────────────────────────
# 渲染 (扁平三段网格, escape 全部动态文本, 红涨绿跌, 760px 单列防溢出)
# ─────────────────────────────────────────────────────────────
def _e(x):
    return escape(str(x)) if x is not None else ''


def _clr(v):
    """A 股红涨绿跌; v 为百分数或占比差。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return '#9aa0a6'
    if f > 0:
        return '#e04b4b'
    if f < 0:
        return '#2fa25f'
    return '#9aa0a6'


def _fmt_pct(v, plus=True):
    if v is None:
        return '—'
    s = f'{v:+.1f}%' if plus else f'{v:.1f}%'
    return s


def _fmt_date(d):
    s = _norm_date(d)
    if len(s) == 8:
        return f'{s[:4]}-{s[4:6]}-{s[6:]}'
    return _e(d)


def _render_identity_col(ident) -> str:
    lead = ident.get('space_leader')
    if not lead:
        return ''
    rows = []
    theme = f" · {_e(lead['theme'])}" if lead.get('theme') else ''
    fb = _fmt_date(lead['first_board_date']) if lead.get('first_board_date') else '—'
    rows.append(
        f"<div class='lt-row'><b>空间高标</b> {_e(lead['name'])} "
        f"<span class='lt-code'>{_e(lead['code'])}</span> · "
        f"<span class='lt-hi'>{_e(lead['height'])}板</span>"
        f"{theme}</div>"
        f"<div class='lt-sub'>{_e(lead['consec_days'])}连板 · 首板 {fb}</div>"
    )
    pop = ident.get('popularity_leader')
    if pop and pop['code'] != lead['code']:
        pt = f" · {_e(pop['theme'])}" if pop.get('theme') else ''
        rows.append(
            f"<div class='lt-row'><b>人气龙头</b> {_e(pop['name'])} "
            f"<span class='lt-code'>{_e(pop['code'])}</span> · "
            f"近20日涨停 {_e(pop['zt_count_20d'])} 次{pt}</div>"
        )
    cohort = ident.get('top_cohort') or []
    if len(cohort) > 1:
        names = '、'.join(_e(c['name']) for c in cohort[:6])
        rows.append(f"<div class='lt-sub'>最高板梯队({len(cohort)}只): {names}</div>")
    rows.append(
        "<div class='lt-act'>怎么操作: 高标是情绪风向标 — 只做它带动的主线核心, "
        "首阴/断板前不接力高位孤票。</div>"
    )
    return f"<div class='lt-col'><div class='lt-h'>① 高标身份</div>{''.join(rows)}</div>"


def _render_gravity_col(grav) -> str:
    ech = grav.get('echelon') or {}
    if not ech.get('max_h'):
        return ''
    rows = [
        f"<div class='lt-row'><b>承接梯队</b> 最高{_e(ech['max_h'])}板 "
        f"· 次高{_e(ech['n_at_max_1'])}只 / 再低{_e(ech['n_at_max_2'])}只 "
        f"· 梯队分 {_e(ech['ladder'])}</div>"
    ]
    if grav.get('is_lonely_peak'):
        rows.append(f"<div class='lt-warn'>⚠️ 孤峰: {_e(grav['lonely_peak_reason'])}</div>")
    cl = grav.get('cluster') or {}
    if cl.get('count'):
        mem = '、'.join(f"{_e(m['name'])}({_e(m['height'])})" for m in cl['members'][:6])
        rows.append(f"<div class='lt-row'><b>抱团</b> {_e(cl['theme'])} 同题材 "
                    f"{_e(cl['count'])} 只涨停: {mem}</div>")
    im = grav.get('imitation') or {}
    if im.get('count'):
        mem = '、'.join(_e(m['name']) for m in im['members'][:6])
        rows.append(f"<div class='lt-row'><b>模仿盘</b> 低位1-2板同题材 "
                    f"{_e(im['count'])} 只: {mem}</div>")
    cu = grav.get('catchup') or {}
    if cu.get('count'):
        mem = '、'.join(f"{_e(m['name'])}<span style='color:{_clr(m['pct'])}'>"
                       f"{_fmt_pct(m['pct'])}</span>" for m in cu['members'][:6])
        note = ' <span class=lt-note>(仅已归因个股)</span>' if cu.get('partial') else ''
        rows.append(f"<div class='lt-row'><b>补涨</b> 同题材未涨停大涨 "
                    f"{_e(cu['count'])} 只{note}: {mem}</div>")
    if grav.get('is_lonely_peak'):
        act = ('孤峰无承接 → 情绪缺传导, 高标见顶即退潮; 不追高位, 兑现前置一档。')
    elif cl.get('count', 0) >= 3:
        act = ('抱团成型 → 主线赚钱效应强; 顺势做梯队中低位补涨/回踩不破, '
               '龙头断板日整体减。')
    else:
        act = '承接一般 → 只跟龙头本身节奏, 不外溢做同题材跟风。'
    rows.append(f"<div class='lt-act'>怎么操作: {act}</div>")
    return f"<div class='lt-col'><div class='lt-h'>② 高标引力</div>{''.join(rows)}</div>"


def _render_death_col(death) -> str:
    table = death.get('table') or []
    if not any(r['n'] for r in table):
        return ''
    trs = []
    for r in table:
        wr = f"{int(r['weaken_rate'])}%" if r['weaken_rate'] is not None else '—'
        dv = r['ad_delta']
        dcell = (f"<span style='color:{_clr(dv)}'>{dv:+.3f}</span>"
                 if dv is not None else '—')
        hot = ' class=lt-hot' if (death.get('event_today') and
                                  death.get('regime') == r['regime']) else ''
        trs.append(f"<tr{hot}><td>{_e(r['regime'])}断</td><td>{_e(r['n'])}</td>"
                   f"<td>{wr}</td><td>{dcell}</td></tr>")
    ad = death.get('ad_today')
    adtxt = f"今日A/D占比 {ad:.2f}" if ad is not None else '今日A/D缺失'
    ev = ('🔴 今日发生高标断板' if death.get('event_today')
          else '今日无断板事件')
    tbl = ("<table class='lt-tbl'><tr><th>断板当日</th><th>样本</th>"
           "<th>次日走弱</th><th>A/D均变</th></tr>" + ''.join(trs) + "</table>")
    return (f"<div class='lt-col'><div class='lt-h'>③ 生死→情绪信号</div>"
            f"<div class='lt-sub'>{ev} · {adtxt}</div>{tbl}"
            f"<div class='lt-act'>怎么操作: {_e(death.get('action', ''))} "
            f"<span class=lt-note>(样本 n≈10, 历史频率提示非铁律)</span></div></div>")


_LT_CSS = """
<style>
.leader-tracker{margin:18px 0;padding:14px 16px;border:1px solid #2a2d34;
  border-radius:10px;background:#16181d;color:#d7dae0;font-size:13px;line-height:1.6;}
.leader-tracker .lt-title{font-size:15px;font-weight:700;margin-bottom:6px;}
.leader-tracker .lt-headline{font-size:13px;color:#ffd479;background:#23262d;
  padding:8px 10px;border-radius:6px;margin-bottom:12px;overflow-wrap:anywhere;}
.leader-tracker .lt-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;}
.leader-tracker .lt-col{min-width:0;overflow-wrap:anywhere;
  border-left:2px solid #2a2d34;padding-left:12px;}
.leader-tracker .lt-h{font-weight:700;color:#8ab4f8;margin-bottom:6px;}
.leader-tracker .lt-row{margin:4px 0;overflow-wrap:anywhere;}
.leader-tracker .lt-sub{font-size:12px;color:#9aa0a6;margin:2px 0;overflow-wrap:anywhere;}
.leader-tracker .lt-code{color:#9aa0a6;font-size:12px;}
.leader-tracker .lt-hi{color:#e04b4b;font-weight:700;}
.leader-tracker .lt-warn{color:#ffb454;margin:4px 0;overflow-wrap:anywhere;}
.leader-tracker .lt-act{font-size:12px;color:#7fd1b9;margin-top:8px;
  border-top:1px dashed #2a2d34;padding-top:6px;overflow-wrap:anywhere;}
.leader-tracker .lt-note{color:#767b83;font-size:11px;}
.leader-tracker .lt-tbl{border-collapse:collapse;margin:6px 0;width:100%;font-size:12px;}
.leader-tracker .lt-tbl th,.leader-tracker .lt-tbl td{
  border:1px solid #2a2d34;padding:3px 6px;text-align:center;}
.leader-tracker .lt-tbl tr.lt-hot{background:#3a2a1a;}
@media (max-width:760px){.leader-tracker .lt-grid{grid-template-columns:1fr;}
  .leader-tracker .lt-col{border-left:none;border-top:1px solid #2a2d34;
  padding-left:0;padding-top:10px;}}
</style>
"""


def render_leader_tracker_html(result) -> str:
    """把 build_leader_tracker 的 dict 渲染成 HTML section。空/异常 → 返回空串。"""
    if not result or not isinstance(result, dict):
        return ''
    try:
        ident = result.get('identity') or {}
        grav = result.get('gravity') or {}
        death = result.get('death_signal') or {}
        headline = result.get('headline', '')

        cols = [
            _render_identity_col(ident),
            _render_gravity_col(grav),
            _render_death_col(death),
        ]
        cols = [c for c in cols if c]

        if not cols:
            # 全空 (无高标) → 只出一行摘要, 不出空网格
            if not headline:
                return ''
            return (_LT_CSS + "<div class='leader-tracker'>"
                    "<div class='lt-title'>🏔️ 高标追踪</div>"
                    f"<div class='lt-headline'>{_e(headline)}</div></div>")

        as_of = _fmt_date(result.get('as_of', ''))
        return (_LT_CSS + "<div class='leader-tracker'>"
                f"<div class='lt-title'>🏔️ 高标追踪 · 连板高标 / 引力 / 生死情绪 "
                f"<span class=lt-note>({as_of})</span></div>"
                f"<div class='lt-headline'>{_e(headline)}</div>"
                f"<div class='lt-grid'>{''.join(cols)}</div></div>")
    except Exception:
        return ''

