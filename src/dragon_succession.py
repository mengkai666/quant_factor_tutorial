# -*- coding: utf-8 -*-
"""龙头接替 · 监管周期复盘 (dragon_succession) — 独立增量模块。

一句话: 把"哪只票被交易所盖章成监管高标 → 谁接了它的棒 → 停牌如何孵化下一棒 →
情绪何时崩溃"这段本来靠人工做的短线周期复盘, 固化成每日自动跑批的系统模块。

三段式入口 (镜像 leader_tracker):
  build_dragon_succession(report_date=None, mode='observation') -> dict   # 事实, 永返可渲染 dict
  generate_dragon_html(result)                                  -> str   # 独立子导航整页
  render_dragon_teaser_html(result)                             -> str   # 主报告入口卡 (引流)

关键洞察 (决定算法脊柱):
  接替谱系的骨架 **不是"每日最高板"** (噪声大, 会混进一日过客), 而是
  **"触发监管异动/严重异常波动/停牌公告的个股" = 交易所亲自盖章的真龙头**。
  既客观又贴合"监管周期"主题, 能把真龙头 (爱丽/一鸣/百花/神奇/金健) 干净筛出。

监管红线 (用户校正过的口径, 是本模块的中心机制):
  异动 = 10 天 ±100% / 严重异常波动 = 20~30 天 ±200% → 触发即停牌核查,
  复牌后若继续涨停即"突破监管压制"。每次停牌都是下一棒龙头的孵化窗。

提速铁律 (见 [[report-run-perf-optimization]]):
  周期识别 / 预筛 / 累计涨幅 / 停牌缺口 / 崩溃指标 **全走本地缓存, 零联网**;
  唯一联网点 = 巨潮公告, 只对 ≤~几十个候选抓, cache-first + 负缓存 + 系统性故障护栏。
  已收盘周期公告不可变 → 一生抓一次; 活跃周期同 run-day 命中缓存。

任何子步骤失败静默兜底, 保证不抛异常、不阻断日报。
"""
from __future__ import annotations

import bisect
import copy
import os
import sys
import time
from html import escape

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from breakthrough_verify import (  # noqa: E402
    load_zt_cache, detect_stale_dates, load_sentiment_cache,
    dt_count_by_day, _norm_code,
)
from paths import (  # noqa: E402
    ZT_CACHE_FILE, PRICE_CACHE, CNINFO_ANN_CACHE, CNINFO_ANN_NEG_CACHE,
)

# ─────────────────────────────────────────────────────────────
# 常量 (阈值按 2026-07~08 真实周期标定; 见 plan 与人工复盘)
# ─────────────────────────────────────────────────────────────
ENTER_H = 4              # 进入"周期"的最高板门槛 (≥4 板算高潮酝酿)
EXIT_RUN = 2             # 连续 N 个真实交易日最高板 <ENTER_H 判周期结束
CUM10_THRESH = 90.0      # 预筛 B: 10 日累计涨幅 ≥ (%) (监管异动 100% 留 10% 余量)
CUM30_THRESH = 180.0     # 预筛 B: 30 日累计涨幅 ≥ (%) (严重异动 200% 留 10% 余量)
CUM10_DAYS = 10          # 累计涨幅回看窗 (交易日)
CUM30_DAYS = 30
ZT_DROP = 0.4            # 情绪崩溃: 涨停家数环比跌幅 ≥
DT_SURGE = 3.0           # 情绪崩溃: 跌停家数环比放大 ≥ x
DT_ABS = 50              # 情绪崩溃: 跌停家数绝对值 ≥ (8/19 实测 118)
SEV_STAMP = 2            # severity ≥ 此值 = 交易所盖章 (异动及以上)
SEV_TOP = 3             # severity ≥ 此值 = 顶部标记 (严重异动 / 停牌核查)

# 脊柱筛选 (实测: 热周期里 sev≥2 会有 30+ 只盖章高标, 平铺=噪声;
#   核心脊柱只留"真高标": 严重异动/停牌(sev≥3) 或 停过牌 或 板高≥SPINE_MIN_H。
#   全量盖章板仍喂监管阶梯与崩溃指标, 只是不进接替链。调噪声改这三个常量)
SPINE_MIN_H = 6          # 板高 ≥ 此值即便只 sev2 也算真高标 (孤峰高标)
SPINE_MAX = 8            # 接替链最多展示棒数 (按脊柱分排序取前 N)

# 公告负缓存阈值 (照抄 price_gap_memo 语义, 换键为 (code6, query_date))
NEG_FAIL_PAST = 1        # 历史 query_date: 一轮抓失败即跳过 (公告不可变)
NEG_FAIL_TODAY = 2       # 当天: 需两轮
NEG_TTL_TODAY = 3 * 3600 # 当天只跳过 3 小时, 留盘中重试窗
NEG_MAX_ABS = 20         # 系统性故障护栏: 失败数上限 (绝对)
NEG_MAX_RATIO = 0.5      # 系统性故障护栏: 失败数上限 (占本轮请求数比例)

_ANN_COLS = ['code', 'query_date', 'ann_date', 'title', 'type', 'severity', 'url', 'fetched_at']
_NEG_COLS = ['code', 'query_date', 'attempts', 'last_attempt']


# ─────────────────────────────────────────────────────────────
# 通用小工具 (照 leader_tracker 口径: 红涨绿跌 / escape / 日期归一)
# ─────────────────────────────────────────────────────────────
def _norm_date(s) -> str:
    """任意日期 → YYYYMMDD (去横线取前 8 位)。"""
    return str(s).replace('-', '').strip()[:8]


def _today() -> str:
    return time.strftime('%Y%m%d')


def _e(x) -> str:
    return escape(str(x)) if x is not None else ''


def _clr(v) -> str:
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


def _fmt_date(d) -> str:
    s = _norm_date(d)
    if len(s) == 8:
        return f'{s[:4]}-{s[4:6]}-{s[6:]}'
    return _e(d)


def _fmt_pct(v, plus=True) -> str:
    if v is None:
        return '—'
    return f'{v:+.1f}%' if plus else f'{v:.1f}%'


# ─────────────────────────────────────────────────────────────
# 公告分类 (真实标题已验证; 从高到低短路)
# ─────────────────────────────────────────────────────────────
def classify_announcement(title) -> int:
    """标题 → severity 0~5。优先级即"停牌优先于严重异动"。

      含"复牌"                                     → 5 (复牌 = 突破监管继续走)
      含"停牌"                                     → 4 (停牌核查, 顶部标记)
      含"严重异常波动" 或 监管工作函/问询函/关注函  → 3 (顶部标记)
      含"异常波动"                                 → 2 (交易所盖章起点)
      含"风险提示"                                 → 1
      其他                                         → 0
    """
    t = str(title or '')

    def has(*ks):
        return any(k in t for k in ks)

    if has('复牌'):
        return 5
    if has('停牌'):
        return 4
    if has('严重异常波动') or has('监管工作函', '问询函', '关注函', '监管函'):
        return 3
    if has('异常波动'):
        return 2
    if has('风险提示'):
        return 1
    return 0


_SEV_KIND = {5: '复牌', 4: '停牌核查', 3: '严重异动/问询', 2: '异动', 1: '风险提示', 0: '其他'}


def _severity_kind(sev) -> str:
    try:
        return _SEV_KIND.get(int(sev), '其他')
    except (TypeError, ValueError):
        return '其他'


# ─────────────────────────────────────────────────────────────
# ① 上下文: 涨停全史 + 鬼影日 + 逐日 {code:height} + DT家数/池 + 价格(懒)
# ─────────────────────────────────────────────────────────────
def _load_dt_pool(path=ZT_CACHE_FILE) -> dict:
    """{date(YYYYMMDD): {code6: name}} 的跌停池 (DT 行与 ZT 同表, 靠"类型"列区分)。

    dt_count_by_day 只给家数, 这里保留具体 code+name 供"高标一字跌停"判定。
    """
    if not os.path.exists(path):
        return {}
    try:
        df = pd.read_csv(path, encoding='utf-8-sig', dtype={'日期': str, '代码': str})
        df.columns = [c.strip().lstrip('﻿') for c in df.columns]
        dt = df[df['类型'] == 'DT']
        pool = {}
        for _, r in dt.iterrows():
            d = _norm_date(r.get('日期', ''))
            c6 = _norm_code(r.get('代码', ''))
            if not d or not c6:
                continue
            pool.setdefault(d, {})[c6] = str(r.get('名称', '') or '').strip()
        return pool
    except Exception:
        return {}


def _load_price_df(path=PRICE_CACHE) -> pd.DataFrame:
    """价格缓存 → 归一化 [c6, d, close] (读取范式照 leader_tracker._today_pct)。

    读失败/无价列返回空表 (纯增强, 缺价只让累计涨幅/停牌降级显 '—', 不阻断)。
    """
    if not os.path.exists(path):
        return pd.DataFrame(columns=['c6', 'd', 'close'])
    try:
        df = pd.read_csv(path, dtype={'code': str})
    except Exception:
        return pd.DataFrame(columns=['c6', 'd', 'close'])
    if df.empty or 'date' not in df.columns or 'code' not in df.columns:
        return pd.DataFrame(columns=['c6', 'd', 'close'])
    col = 'close_qfq' if 'close_qfq' in df.columns else (
        'close_legacy' if 'close_legacy' in df.columns else (
            'close' if 'close' in df.columns else None))
    if col is None:
        return pd.DataFrame(columns=['c6', 'd', 'close'])
    out = pd.DataFrame({
        'c6': df['code'].map(_norm_code),
        'd': df['date'].astype(str).map(_norm_date),
        'close': pd.to_numeric(df[col], errors='coerce'),
    })
    out = out[(out['c6'] != '') & out['close'].notna() & (out['close'] > 0)]
    return out.drop_duplicates(subset=['c6', 'd'], keep='last').reset_index(drop=True)


def _build_context(report_date=None, mode='observation') -> dict:
    """加载并组织本模块所需的全部真源数据 (全本地零联网)。

    返回:
      as_of        报告日 (缓存内 <= report_date 的最新真实交易日, YYYYMMDD)
      policy       发布策略 mode (供子页末尾 sanitize)
      by_day       {date: {code6: height}} (含 stale 日, 判周期时只走 real_dates)
      real_dates   非 stale 交易日升序 (<= as_of)
      stale        鬼影日集合
      name_by_code {code6: name} (每只票最新一次涨停时的名称)
      zt_count     {date: 涨停家数}
      dt_count     {date: 跌停家数}
      dt_pool      {date: {code6: name}} 跌停池
      price_df     归一化价格 [c6, d, close] (可能为空表)
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
    zt_count = {d: len(m) for d, m in by_day.items()}

    latest = df.sort_values('date').drop_duplicates('code', keep='last')
    name_by_code = dict(zip(latest['code'], latest['name']))

    dt_count = {_norm_date(k): int(v) for k, v in dt_count_by_day().items()}
    dt_pool = _load_dt_pool()
    price_df = _load_price_df()

    return {
        'as_of': as_of, 'policy': str(mode or 'observation').lower(),
        'by_day': by_day, 'real_dates': real_dates, 'stale': stale,
        'name_by_code': name_by_code, 'zt_count': zt_count,
        'dt_count': dt_count, 'dt_pool': dt_pool, 'price_df': price_df,
    }


# ─────────────────────────────────────────────────────────────
# ② 公告缓存 (镜像 price_gap_memo: 正缓存 upsert + 负缓存 TTL + 系统性护栏)
# ─────────────────────────────────────────────────────────────
def _load_ann_cache() -> pd.DataFrame:
    """读公告正缓存; 失败/不存在返回空表 (纯优化件, 绝不阻断)。"""
    if not os.path.exists(CNINFO_ANN_CACHE):
        return pd.DataFrame(columns=_ANN_COLS)
    try:
        df = pd.read_csv(CNINFO_ANN_CACHE, dtype={'code': str, 'query_date': str,
                                                  'ann_date': str})
        for c in _ANN_COLS:
            if c not in df.columns:
                df[c] = '' if c != 'severity' else 0
        return df[_ANN_COLS]
    except Exception:
        return pd.DataFrame(columns=_ANN_COLS)


def _save_ann_cache(new_rows) -> bool:
    """读旧→concat→drop_duplicates(['code','query_date','url'])→sort→to_csv。

    公告缓存天然小 (短名单 × ~20 条), 无需 size-trim (勿 import 主文件 trim_cache_file, 循环依赖)。
    """
    if not new_rows:
        return False
    try:
        old = _load_ann_cache()
        add = pd.DataFrame(new_rows, columns=_ANN_COLS)
        merged = pd.concat([old, add], ignore_index=True)
        merged = merged.drop_duplicates(subset=['code', 'query_date', 'url'], keep='last')
        merged = merged.sort_values(['code', 'query_date', 'ann_date'])
        merged.to_csv(CNINFO_ANN_CACHE, index=False, encoding='utf-8-sig')
        return True
    except Exception:
        return False


def _read_neg() -> dict:
    """→ {(code6, query_date): (attempts, last_ts)}; 读失败返回空。"""
    if not os.path.exists(CNINFO_ANN_NEG_CACHE):
        return {}
    try:
        df = pd.read_csv(CNINFO_ANN_NEG_CACHE, dtype={'code': str, 'query_date': str})
    except Exception:
        return {}
    memo = {}
    for row in df.itertuples(index=False):
        code = str(getattr(row, 'code', '') or '').strip()
        qd = str(getattr(row, 'query_date', '') or '').strip()
        if not code or not qd:
            continue
        try:
            attempts = int(getattr(row, 'attempts', 0) or 0)
        except (TypeError, ValueError):
            attempts = 0
        try:
            last = float(getattr(row, 'last_attempt', 0) or 0)
        except (TypeError, ValueError):
            last = 0.0
        memo[(code, qd)] = (attempts, last)
    return memo


def _save_neg(memo) -> bool:
    try:
        rows = [{'code': c, 'query_date': d, 'attempts': a, 'last_attempt': round(float(t), 3)}
                for (c, d), (a, t) in sorted(memo.items())]
        pd.DataFrame(rows, columns=_NEG_COLS).to_csv(
            CNINFO_ANN_NEG_CACHE, index=False, encoding='utf-8-sig')
        return True
    except Exception:
        return False


def _neg_skippable(memo, code6, query_date, now=None) -> bool:
    attempts, last = memo.get((code6, query_date), (0, 0.0))
    if query_date >= _today():
        return attempts >= NEG_FAIL_TODAY and (now or time.time()) - last < NEG_TTL_TODAY
    return attempts >= NEG_FAIL_PAST


def _neg_record(failed_codes, ok_codes, query_date, attempted=None, now=None):
    """记一轮公告抓取结果 (失败 attempts+1, 成功即删), 含系统性故障护栏。"""
    failed = {str(c).strip() for c in (failed_codes or ()) if str(c).strip()}
    ok = {str(c).strip() for c in (ok_codes or ()) if str(c).strip()}
    failed -= ok
    if not failed and not ok:
        return
    if failed and attempted:
        limit = max(NEG_MAX_ABS, int(attempted) * NEG_MAX_RATIO)
        if len(failed) > limit:
            return  # 全线失败更像代理/接口故障, 整轮不记账
    memo = _read_neg()
    ts = now or time.time()
    for c in ok:
        memo.pop((c, query_date), None)
    for c in failed:
        memo[(c, query_date)] = (memo.get((c, query_date), (0, 0.0))[0] + 1, ts)
    _save_neg(memo)


def _ann_is_fresh(code6, query_date, cache_df) -> bool:
    """该 (code6, query_date) 是否已有新鲜缓存。

    已收盘 query_date (< 今天): 公告不可变, 抓过一次即永久新鲜。
    活跃 query_date (== 今天): 需当天抓过 (fetched_at 同日) 才算新鲜。
    """
    if cache_df.empty:
        return False
    hit = cache_df[(cache_df['code'].astype(str) == code6) &
                   (cache_df['query_date'].astype(str) == query_date)]
    if hit.empty:
        return False
    if query_date < _today():
        return True
    fetched = hit['fetched_at'].astype(str).map(lambda s: _norm_date(s[:10]))
    return (fetched == _today()).any()


def get_announcements_cached(code6, as_of, force=False, _neg=None, _stats=None):
    """cache-first 取某票公告并分类。返回 [{ann_date,title,type,severity,kind,url}]。

    命中新鲜正缓存 → 直接返回 (零联网)。miss → fetch→分类→upsert; 空/失败写负缓存。
    _neg/_stats 供 build_backbone 批量共享负缓存视图与本轮统计 (系统性护栏)。
    """
    code6 = _norm_code(code6)
    query_date = _norm_date(as_of)
    cache_df = _load_ann_cache()

    def _from_cache():
        hit = cache_df[(cache_df['code'].astype(str) == code6) &
                       (cache_df['query_date'].astype(str) == query_date)]
        rows = []
        for _, r in hit.iterrows():
            sev = classify_announcement(r.get('title', ''))
            try:
                sev = int(r.get('severity', sev) or sev)
            except (TypeError, ValueError):
                pass
            rows.append({
                'ann_date': _norm_date(r.get('ann_date', '')),
                'title': str(r.get('title', '') or ''),
                'type': str(r.get('type', '') or ''),
                'severity': sev, 'kind': _severity_kind(sev),
                'url': str(r.get('url', '') or ''),
            })
        return rows

    if not force and _ann_is_fresh(code6, query_date, cache_df):
        return _from_cache()

    neg = _neg if _neg is not None else _read_neg()
    if not force and _neg_skippable(neg, code6, query_date):
        return _from_cache()  # 负缓存判定"抓不到", 回落已有(可能空)缓存

    # ── miss: 真正联网抓一次 ──
    try:
        from catalyst_attribution import fetch_announcements
        raw = fetch_announcements(code6) or []
    except Exception:
        raw = []

    if _stats is not None:
        _stats['attempted'] = _stats.get('attempted', 0) + 1
        if raw:
            _stats.setdefault('ok', set()).add(code6)
        else:
            _stats.setdefault('failed', set()).add(code6)

    if not raw:
        if _stats is None:  # 单发模式: 立即记负缓存 (批量模式由调用方汇总记账)
            _neg_record([code6], [], query_date)
        return _from_cache()

    new_rows, out = [], []
    now_iso = time.strftime('%Y-%m-%d %H:%M:%S')
    for a in raw:
        title = a.get('title', '')
        sev = classify_announcement(title)
        ann_date = _norm_date(a.get('date', ''))
        url = str(a.get('url', '') or '')
        new_rows.append({
            'code': code6, 'query_date': query_date, 'ann_date': ann_date,
            'title': title, 'type': a.get('type', ''), 'severity': sev,
            'url': url, 'fetched_at': now_iso,
        })
        out.append({'ann_date': ann_date, 'title': title, 'type': a.get('type', ''),
                    'severity': sev, 'kind': _severity_kind(sev), 'url': url})
    _save_ann_cache(new_rows)
    if _stats is None:
        _neg_record([], [code6], query_date)  # 成功即清负缓存
    return out


# ─────────────────────────────────────────────────────────────
# ③ 事实计算 (全本地可注入; 网络仅在 build_backbone 对短名单抓公告)
# ─────────────────────────────────────────────────────────────
def _max_h_by_day(by_day, dates) -> dict:
    return {d: (max(by_day.get(d, {}).values()) if by_day.get(d) else 0) for d in dates}


def identify_cycle(by_day, real_dates, as_of):
    """识别最近一段"周期窗": 最高板 ≥ENTER_H 开窗, 连续 EXIT_RUN 日 <ENTER_H 收窗。

    返回 {start,end,peak_date,peak_height,is_current,n_days} 或 None (全程无高板/过短)。
    """
    if not real_dates:
        return None
    max_h = _max_h_by_day(by_day, real_dates)
    segments = []  # (start, end_high_date)
    open_seg = False
    seg_start = None
    last_high = None
    low_run = 0
    for d in real_dates:
        mh = max_h.get(d, 0)
        if mh >= ENTER_H:
            if not open_seg:
                open_seg, seg_start = True, d
            last_high = d
            low_run = 0
        elif open_seg:
            low_run += 1
            if low_run >= EXIT_RUN:
                segments.append((seg_start, last_high, False))
                open_seg = False
                seg_start = last_high = None
                low_run = 0
    if open_seg:  # 末尾未收窗 = 当前活跃周期
        segments.append((seg_start, last_high, True))
    if not segments:
        return None

    start, end, is_current = segments[-1]
    if not start or not end:
        return None
    window = [d for d in real_dates if start <= d <= end]
    if not window:
        return None
    peak_date = max(window, key=lambda d: max_h.get(d, 0))
    peak_height = max_h.get(peak_date, 0)
    if peak_height < ENTER_H:
        return None
    return {
        'start': start, 'end': end, 'peak_date': peak_date,
        'peak_height': int(peak_height), 'is_current': bool(is_current),
        'n_days': len(window),
    }


# 价格帧按 code6 的一次性索引。改前 _cum_gain/infer_halt_intervals 每次都做
# price_df[price_df['c6'] == code6] —— 在几十万行的 object 列上全表字符串比较,
# 一轮报告调 4700+ 次, profile 里 pandas comp_method_OBJECT_ARRAY self 101.3s。
# 分组一次后查表, 语义完全不变 (组内仍按 d 升序, 稳定排序保留同日原顺序)。
_PRICE_IDX_CACHE = {'frame': None, 'index': None}


def _price_index(price_df) -> dict:
    """{code6: (升序日期 list, 对应收盘 list)}; 空表/缺列返回 {}。

    单槽缓存按帧对象身份 (`is`) 命中, 并持有强引用, 因此不存在 id 复用误命中。
    """
    if price_df is None or getattr(price_df, 'empty', True):
        return {}
    if not {'c6', 'd', 'close'}.issubset(price_df.columns):
        return {}
    if _PRICE_IDX_CACHE['frame'] is price_df and _PRICE_IDX_CACHE['index'] is not None:
        return _PRICE_IDX_CACHE['index']
    index = {}
    for c6, g in price_df.sort_values('d', kind='stable').groupby('c6', sort=False):
        index[c6] = (g['d'].tolist(), g['close'].tolist())
    _PRICE_IDX_CACHE['frame'] = price_df
    _PRICE_IDX_CACHE['index'] = index
    return index


def _cum_gain(price_df, code6, d_end, lookback_days):
    """code6 截至 d_end 的近 lookback_days 交易日累计涨幅% (缺价返回 None)。"""
    entry = _price_index(price_df).get(code6)
    if not entry:
        return None
    dates, closes = entry
    cut = bisect.bisect_right(dates, d_end)   # <= d_end 的行数, 等价于原来的 len(sub)
    if cut < 2:
        return None
    p1 = closes[cut - 1]
    idx = max(0, cut - 1 - lookback_days)
    p0 = closes[idx]
    if not p0 or p0 <= 0:
        return None
    return (p1 / p0 - 1) * 100


def _candidate_prefilter(ctx, cycle) -> list:
    """周期窗内的监管高标候选 (纯本地, 把联网压到短名单)。

    A = 窗口内出现过 height ≥ ENTER_H 的 code6
    B = 窗口内有过涨停 且 10日累计≥CUM10 或 30日累计≥CUM30 的 code6
    并集。B 只在"窗内有涨停"的票上算累计, 既贴题又把成本限在几百只内。
    """
    by_day, price_df = ctx['by_day'], ctx['price_df']
    start, end = cycle['start'], cycle['end']
    win_dates = [d for d in ctx['real_dates'] if start <= d <= end]

    cand_a, seen_zt = set(), set()
    for d in win_dates:
        for c6, h in by_day.get(d, {}).items():
            seen_zt.add(c6)
            if h >= ENTER_H:
                cand_a.add(c6)

    cand_b = set()
    if price_df is not None and not price_df.empty:
        for c6 in seen_zt:
            g10 = _cum_gain(price_df, c6, end, CUM10_DAYS)
            g30 = _cum_gain(price_df, c6, end, CUM30_DAYS)
            if (g10 is not None and g10 >= CUM10_THRESH) or \
               (g30 is not None and g30 >= CUM30_THRESH):
                cand_b.add(c6)
    return sorted(cand_a | cand_b)


def infer_halt_intervals(price_df, code6, market_days, anns):
    """从价格缺口推断停牌区间, 再用 severity≥4 (停牌) 公告交叉验证。

    只有被成交日"夹住"的缺口才记 halt (前导/尾随缺失 = 未上市/退市/数据洞, 不算)。
    孤立单日缺口且附近无停牌公告 → 丢弃 (可能只是数据洞)。
    尾段: 该票最后有价日 < 窗口末日 且附近有停牌公告 → {end:None, resumed:False}。
    数据 <2 返回 []（不猜停牌）。
    """
    if price_df is None or price_df.empty or not market_days:
        return []
    mdays = sorted(set(market_days))
    _entry = _price_index(price_df).get(code6)
    have = (set(_entry[0]) if _entry else set()) & set(mdays)
    if len(have) < 2:
        return []
    first_have, last_have = min(have), max(have)
    halt_days = [d for d in mdays if first_have < d < last_have and d not in have]

    sev4_days = sorted(_norm_date(a['ann_date']) for a in (anns or [])
                       if a.get('severity', 0) >= 4 and a.get('ann_date'))

    def _near_sev4(day):
        return any(abs(mdays.index(day) - mdays.index(sd)) <= 1
                   for sd in sev4_days if sd in mdays)

    halts, i = [], 0
    while i < len(halt_days):
        j = i
        while j + 1 < len(halt_days) and mdays.index(halt_days[j + 1]) == mdays.index(halt_days[j]) + 1:
            j += 1
        gap = halt_days[i:j + 1]
        gap_start = gap[0]
        # 复牌日 = 缺口后第一个有价的交易日
        after = [d for d in mdays if d > gap[-1]]
        resume = next((d for d in after if d in have), None)
        keep = len(gap) >= 2 or _near_sev4(gap_start)
        if keep:
            halts.append({'start': gap_start, 'end': resume, 'resumed': resume is not None})
        i = j + 1

    # 尾段停牌 (最后有价日之后仍有交易日, 且附近有停牌公告)
    trailing = [d for d in mdays if d > last_have]
    if trailing and (any(sd >= last_have for sd in sev4_days) or
                     _near_sev4(last_have)):
        halts.append({'start': trailing[0], 'end': None, 'resumed': False})
    return halts


def build_regulated_board(ctx, cycle) -> list:
    """全量监管盖章板: 对候选逐个取窗口内 severity≥2 公告, 无则剔; 富化并按首涨日排。

    这是**全量**盖章高标 (热周期实测 30+ 只), 喂监管阶梯与崩溃指标;
    接替链只展示其中的核心脊柱 (见 select_spine)。排序 = 首涨日升序=时间序。
    """
    by_day, price_df = ctx['by_day'], ctx['price_df']
    start, end, as_of = cycle['start'], cycle['end'], ctx['as_of']
    win_dates = [d for d in ctx['real_dates'] if start <= d <= end]
    # market_days = 周期起到 as_of(含峰后回落) 的全市场交易日, 用价格缓存日作权威日历
    if not price_df.empty:
        market_days = sorted({d for d in price_df['d'].unique() if start <= d <= as_of})
    else:
        market_days = [d for d in ctx['real_dates'] if start <= d <= as_of]

    cand = _candidate_prefilter(ctx, cycle)
    neg = _read_neg()
    stats = {}
    board = []
    for c6 in cand:
        anns_all = get_announcements_cached(c6, as_of, _neg=neg, _stats=stats)
        # 窗口内 (start~as_of) 的交易所盖章公告
        anns = [a for a in anns_all
                if a.get('severity', 0) >= SEV_STAMP
                and a.get('ann_date') and start <= a['ann_date'] <= as_of]
        if not anns:
            continue
        anns.sort(key=lambda a: (a['ann_date'], -a['severity']))

        # 富化
        appear = [d for d in win_dates if c6 in by_day.get(d, {})]
        first_rise = appear[0] if appear else (anns[0]['ann_date'])
        peak_h = max((by_day[d][c6] for d in appear), default=0)
        max_sev = max(a['severity'] for a in anns)
        top_marker = next((a['ann_date'] for a in anns if a['severity'] >= SEV_TOP), None)
        halts = infer_halt_intervals(price_df, c6, market_days, anns)
        cum = _cum_gain(price_df, c6, as_of, CUM30_DAYS)
        is_suspended_now = bool(halts and halts[-1].get('end') is None)

        board.append({
            'code': c6, 'code6': c6, 'name': ctx['name_by_code'].get(c6, c6),
            'first_rise_date': first_rise, 'peak_height': int(peak_h),
            'cum_gain_pct': (round(cum, 1) if cum is not None else None),
            'max_severity': int(max_sev), 'top_marker_date': top_marker,
            'announcements': anns, 'halts': halts,
            'is_suspended_now': is_suspended_now, 'role': 'successor',
        })

    # 批量负缓存记账 (系统性故障护栏: 全线失败不记账)
    if stats:
        _neg_record(stats.get('failed', set()), stats.get('ok', set()),
                    _norm_date(as_of), attempted=stats.get('attempted'))

    board.sort(key=lambda b: (b['first_rise_date'], -b['peak_height'], -b['max_severity']))
    return board


def _spine_score(b) -> float:
    """脊柱分: 板高 + 监管盖章 ×2 + 停过牌 +3 + 停牌中 +2 (爱丽这类真龙头分最高)。"""
    return (b.get('peak_height', 0)
            + b.get('max_severity', 0) * 2
            + (3 if b.get('halts') else 0)
            + (2 if b.get('is_suspended_now') else 0))


def select_spine(board, max_n=SPINE_MAX) -> list:
    """从全量盖章板挑核心脊柱 = 接替链要展示的真高标 (去掉 sev2 一日过客)。

    入选 = 严重异动/停牌(sev≥SEV_TOP) 或 停过牌 或 板高≥SPINE_MIN_H;
    按脊柱分取前 max_n; 锚定龙头=分最高者 (非最早起涨者, 修正"百花当锚"的偏差);
    展示顺序按首涨日升序 (呈现时间接替)。入选为空则回落全板按分取前 max_n。
    """
    if not board:
        return []
    core = [b for b in board
            if b.get('max_severity', 0) >= SEV_TOP or b.get('halts')
            or b.get('peak_height', 0) >= SPINE_MIN_H]
    if not core:
        core = list(board)
    core = sorted(core, key=_spine_score, reverse=True)[:max_n]
    anchor_code = core[0]['code6'] if core else None
    spine = sorted(core, key=lambda b: (b['first_rise_date'], -b['peak_height'],
                                        -b['max_severity']))
    for b in spine:
        b['role'] = 'anchor' if b['code6'] == anchor_code else 'successor'
    return spine



def build_succession_edges(spine, board=None) -> list:
    """停牌孵化链: 停牌高标 → 停牌孵化期内起涨的下一棒 (跨全量盖章板搜索)。

    孵化是**停牌锚定 + 跨全板**的因果关系, 不是相邻脊柱对——被孵化的接棒常是
    脊柱外的 sev2 票 (如爱丽停牌②→神奇制药)。故对每个"停过牌"的高标, 取其每段
    停牌窗 [halt.start, halt.end 或 as_of], 找全板中在窗内首涨的票 = 被孵化接棒。

    孵化窗内起涨 = 老龙头停牌核查造成资金外溢, 直接点燃下一棒 (爱丽停牌①→百花孵化,
    停牌②→神奇/金健孵化)。返回按孵化窗起始排序的边; 无停牌高标则返回 []。
    """
    universe = board if board else spine
    edges = []
    seen = set()
    for old in spine:
        halts = old.get('halts') or []
        if not halts:
            continue
        for h in halts:
            win_start = h.get('start')
            win_end = h.get('end') or _today()
            if not win_start:
                continue
            for new in universe:
                if new['code6'] == old['code6']:
                    continue
                nf = new.get('first_rise_date')
                if not (nf and win_start <= nf <= win_end):
                    continue
                key = (old['code6'], new['code6'])
                if key in seen:
                    continue
                seen.add(key)
                gap_days = None
                try:
                    gap_days = (pd.to_datetime(nf) - pd.to_datetime(win_start)).days
                except Exception:
                    gap_days = None
                edges.append({
                    'from': old['code6'], 'to': new['code6'],
                    'from_name': old['name'], 'to_name': new['name'],
                    'incubation': True, 'window': [win_start, h.get('end')],
                    'to_first_rise': nf, 'gap_days': gap_days,
                    'resumed': h.get('resumed', False),
                })
    edges.sort(key=lambda e: (e['window'][0] or '', e['to']))
    return edges


def build_regulatory_ladder(backbone) -> list:
    """把 backbone 的 severity≥2 公告摊平成一条按日期升序的监管阶梯时间线。"""
    rows = []
    for b in backbone:
        for a in b.get('announcements', []):
            if a.get('severity', 0) < SEV_STAMP:
                continue
            rows.append({
                'date': a['ann_date'], 'code6': b['code6'], 'name': b['name'],
                'severity': a['severity'], 'kind': a['kind'],
                'title': a['title'], 'url': a['url'],
                'is_top_marker': a['severity'] >= SEV_TOP,
            })
    rows.sort(key=lambda r: (r['date'], -r['severity']))
    return rows


def build_collapse_metric(ctx, cycle, backbone) -> dict:
    """情绪崩溃指标 (阈值按 8/19 实测标定: 涨停79→36 / 跌停5→118, ×23.6)。

    逐日 drop=(zt_prev-zt)/zt_prev、surge=dt/max(dt_prev,1);
    is_collapse_day = drop≥ZT_DROP and (surge≥DT_SURGE or dt≥DT_ABS)。取 drop 最大日。
    high_leaders_limit_down = backbone 中 max_sev≥3 且落在崩溃日跌停池的票。
    """
    zt_count, dt_count, dt_pool = ctx['zt_count'], ctx['dt_count'], ctx['dt_pool']
    start, peak = cycle['start'], cycle['peak_date']
    # 崩溃通常在见顶后; 从峰值日起扫到 as_of (含峰后回落)
    scan = [d for d in ctx['real_dates'] if peak <= d <= ctx['as_of']]
    best = None
    for prev, d in zip(scan, scan[1:]):
        ztp, zt = zt_count.get(prev, 0), zt_count.get(d, 0)
        dtp, dt = dt_count.get(prev, 0), dt_count.get(d, 0)
        drop = (ztp - zt) / ztp if ztp > 0 else 0.0
        surge = dt / max(dtp, 1)
        is_col = drop >= ZT_DROP and (surge >= DT_SURGE or dt >= DT_ABS)
        rec = {
            'date': d, 'zt_prev': ztp, 'zt': zt, 'dt_prev': dtp, 'dt': dt,
            'zt_drop_pct': round(drop * 100, 1), 'dt_surge_x': round(surge, 1),
            'is_collapse': is_col,
        }
        if is_col and (best is None or drop > best['_drop']):
            rec['_drop'] = drop
            best = rec
    if best is None:
        return {
            'date': None, 'zt_prev': None, 'zt': None, 'dt_prev': None, 'dt': None,
            'zt_drop_pct': None, 'dt_surge_x': None, 'is_collapse': False,
            'high_leaders_limit_down': [], 'note': '周期内暂未触发情绪崩溃阈值。',
        }
    best.pop('_drop', None)
    day_pool = dt_pool.get(best['date'], {})
    highs = [{'code6': b['code6'], 'name': b['name']} for b in backbone
             if b.get('max_severity', 0) >= SEV_TOP and b['code6'] in day_pool]
    best['high_leaders_limit_down'] = highs
    hl = ('、'.join(h['name'] for h in highs) if highs else '—')
    best['note'] = (f"{_fmt_date(best['date'])} 情绪崩溃: 涨停 {best['zt_prev']}→{best['zt']} "
                    f"(-{best['zt_drop_pct']}%), 跌停 {best['dt_prev']}→{best['dt']} "
                    f"(×{best['dt_surge_x']}); 监管高标一字跌停: {hl}。")
    return best


def _make_headline(cycle, backbone, collapse, anchor=None) -> str:
    if not backbone:
        return (f"{_fmt_date(cycle['start'])}~{_fmt_date(cycle['end'])} 周期最高 "
                f"{cycle['peak_height']} 板; 公告暂不可用, 脊柱降级为连板口径。")
    anchor = anchor or backbone[0]
    parts = [f"以 {anchor['name']}({anchor['peak_height']}板) 为锚的监管周期"]
    n = len(backbone)
    if n > 1:
        parts.append(f"{n} 只核心高标接替")
    if collapse.get('is_collapse'):
        parts.append(f"{_fmt_date(collapse['date'])} 情绪崩溃"
                     f"(涨停{collapse['zt_prev']}→{collapse['zt']}、"
                     f"跌停{collapse['dt_prev']}→{collapse['dt']})")
    elif cycle.get('is_current'):
        parts.append("周期仍活跃")
    else:
        parts.append("周期已结束")
    return '，'.join(parts) + '。'


def _make_stage(cycle, collapse) -> str:
    if collapse.get('is_collapse') and cycle.get('is_current'):
        return '崩溃/退潮'
    if cycle.get('is_current'):
        return '进行中'
    return '已结束'


# ─────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────
# 进程内结果记忆: 主报告一轮里 build_dragon_succession 被调两次 (主报告 teaser +
# 独立子页), 同 report_date/mode 下两次算的是同一份纯本地数据, 第二次纯属重算
# (实测 53.7s/次)。返回 deepcopy, 两个调用方就地改也互不污染。
# 强制重算: DRAGON_NO_MEMO=1; _build_context 被替换(测试 monkeypatch)时也自动绕过,
# 否则两个用不同上下文的测试会互相吃到对方的缓存结果。
_RESULT_MEMO: dict = {}
_BUILD_CONTEXT_ORIG = _build_context


def build_dragon_succession(report_date=None, mode='observation') -> dict:
    """主入口 (带进程内记忆)。语义与 _build_dragon_succession_uncached 完全一致。"""
    if os.environ.get('DRAGON_NO_MEMO') == '1' or _build_context is not _BUILD_CONTEXT_ORIG:
        return _build_dragon_succession_uncached(report_date, mode=mode)
    key = (_norm_date(report_date) if report_date else '', str(mode or 'observation').lower())
    if key not in _RESULT_MEMO:
        _RESULT_MEMO[key] = _build_dragon_succession_uncached(report_date, mode=mode)
    return copy.deepcopy(_RESULT_MEMO[key])


def _build_dragon_succession_uncached(report_date=None, mode='observation') -> dict:
    """真正的构建流程。任何子步骤失败静默兜底, 保证返回可渲染 dict (不抛异常)。

    深度冰点(无任何周期) → has_cycle=False, degraded=True (teaser 返''、子页不归档)。
    """
    base = {
        'as_of': _norm_date(report_date) if report_date else '',
        'policy': str(mode or 'observation').lower(),
        'has_cycle': False, 'cycle': None, 'backbone': [], 'succession': [],
        'ladder': [], 'collapse': {}, 'headline': '', 'teaser': {},
        'degraded': True, 'degrade_reason': None,
    }
    try:
        ctx = _build_context(report_date, mode=mode)
    except Exception as e:
        base['degrade_reason'] = f'上下文加载失败: {e}'
        return base
    base['as_of'] = ctx['as_of']

    try:
        cycle = identify_cycle(ctx['by_day'], ctx['real_dates'], ctx['as_of'])
    except Exception as e:
        base['degrade_reason'] = f'周期识别失败: {e}'
        return base
    if not cycle:
        base['degrade_reason'] = '深度冰点/无 ≥4 板周期, 无接替谱系可复盘。'
        return base

    try:
        board = build_regulated_board(ctx, cycle)   # 全量盖章板 (喂阶梯/崩溃)
    except Exception as e:
        board = []
        base['degrade_reason'] = f'脊柱构建失败(降级为连板口径): {e}'
    try:
        backbone = select_spine(board)               # 核心脊柱 (接替链展示)
    except Exception:
        backbone = board[:SPINE_MAX]

    try:
        succession = build_succession_edges(backbone, board)
    except Exception:
        succession = []
    try:
        ladder = build_regulatory_ladder(board)      # 阶梯用全量板, 呈现完整监管压制
    except Exception:
        ladder = []
    try:
        collapse = build_collapse_metric(ctx, cycle, board)  # 崩溃高标扫全量板
    except Exception:
        collapse = {'date': None, 'is_collapse': False,
                    'high_leaders_limit_down': [], 'note': '崩溃指标计算失败。'}

    anchor = next((b for b in backbone if b.get('role') == 'anchor'),
                  (backbone[0] if backbone else None))
    headline = _make_headline(cycle, backbone, collapse, anchor)
    stage = _make_stage(cycle, collapse)
    top_markers = sum(1 for r in ladder if r.get('is_top_marker'))
    teaser = {
        'leader_name': (anchor['name'] if anchor else None),
        'leader_height': (anchor['peak_height'] if anchor else cycle['peak_height']),
        'backbone_names': [b['name'] for b in backbone],
        'stage': stage, 'n_backbone': len(backbone), 'n_board': len(board),
        'top_markers': top_markers,
    }
    return {
        'as_of': ctx['as_of'], 'policy': ctx['policy'],
        'has_cycle': True, 'cycle': cycle, 'backbone': backbone, 'board': board,
        'succession': succession, 'ladder': ladder, 'collapse': collapse,
        'headline': headline, 'teaser': teaser,
        'degraded': (not backbone), 'degrade_reason': base['degrade_reason'],
    }


def _flat_teaser(result) -> dict:
    """teaser/看板用扁平摘要 (照 leader_tracker._flat_summary 口径)。"""
    if not result or not result.get('has_cycle'):
        return {'headline': '', 'stage': '无周期'}
    t = result.get('teaser', {})
    return {'headline': result.get('headline', ''), 'stage': t.get('stage', ''),
            'leader_name': t.get('leader_name'), 'n_backbone': t.get('n_backbone', 0)}


# ─────────────────────────────────────────────────────────────
# 渲染 (子页整页 + 主报告入口卡; escape 全部动态文本, 红涨绿跌, 760px 单列)
# ─────────────────────────────────────────────────────────────
_DRAGON_CSS = """
<style>
.dragon-page{max-width:880px;margin:0 auto;padding:40px 20px;
  font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
  background:#0d1117;color:#e6edf3;line-height:1.7;}
.dragon-page .back{color:#8ab4f8;text-decoration:none;font-size:14px;}
.dragon-page .back:hover{text-decoration:underline;}
.dragon-page h1{font-size:26px;font-weight:800;margin:14px 0 4px;letter-spacing:-0.5px;}
.dragon-page .dp-sub{color:#8b949e;font-size:13px;margin-bottom:18px;}
.dragon-page .dp-headline{background:#1b2330;border-left:4px solid #e0a94b;
  border-radius:8px;padding:12px 16px;color:#ffd479;font-size:14px;
  margin-bottom:22px;overflow-wrap:anywhere;}
.dragon-page h2{font-size:16px;font-weight:700;color:#8ab4f8;margin:26px 0 12px;
  border-bottom:1px solid #21262d;padding-bottom:6px;}
.dragon-page .dp-note{color:#767b83;font-size:12px;}
.dragon-page .chain{display:flex;flex-wrap:wrap;gap:8px;align-items:stretch;}
.dragon-page .baton{flex:1;min-width:150px;background:#161b22;border:1px solid #30363d;
  border-radius:10px;padding:12px 14px;overflow-wrap:anywhere;}
.dragon-page .baton.anchor{border-color:#e04b4b;box-shadow:0 0 12px rgba(224,75,75,.18);}
.dragon-page .baton .b-role{font-size:11px;font-weight:700;letter-spacing:1px;
  text-transform:uppercase;color:#8b949e;}
.dragon-page .baton.anchor .b-role{color:#e04b4b;}
.dragon-page .baton .b-name{font-size:16px;font-weight:700;margin:2px 0;}
.dragon-page .baton .b-hi{color:#e04b4b;font-weight:700;}
.dragon-page .baton .b-meta{font-size:12px;color:#9aa0a6;margin-top:4px;}
.dragon-page .baton .b-halt{font-size:12px;color:#e0a94b;margin-top:4px;}
.dragon-page .arrow{align-self:center;color:#6e7681;font-size:20px;font-weight:700;}
.dragon-page table{border-collapse:collapse;width:100%;font-size:13px;margin:6px 0;}
.dragon-page th,.dragon-page td{border:1px solid #21262d;padding:6px 8px;text-align:left;
  overflow-wrap:anywhere;}
.dragon-page th{background:#161b22;color:#8b949e;font-weight:600;}
.dragon-page tr.top-marker{background:#2a1d1d;}
.dragon-page .sev{display:inline-block;min-width:52px;text-align:center;border-radius:5px;
  padding:1px 6px;font-size:11px;font-weight:700;}
.dragon-page .sev.s5{background:#5a2a2a;color:#ff9d9d;}
.dragon-page .sev.s4{background:#5a3a1a;color:#ffce8a;}
.dragon-page .sev.s3{background:#4a3a1a;color:#ffd479;}
.dragon-page .sev.s2{background:#2a3a4a;color:#9dc7ff;}
.dragon-page .collapse-card{background:#161b22;border:1px solid #30363d;border-radius:10px;
  padding:14px 16px;overflow-wrap:anywhere;}
.dragon-page .collapse-card.hit{border-color:#2fa25f;}
.dragon-page .collapse-card b{color:#e6edf3;}
.dragon-page .edge{margin:6px 0;font-size:13px;overflow-wrap:anywhere;}
.dragon-page .edge .yes{color:#7fd1b9;font-weight:700;}
.dragon-page .edge .no{color:#767b83;}
.dragon-page a.ann{color:#8ab4f8;text-decoration:none;}
.dragon-page a.ann:hover{text-decoration:underline;}
.dragon-page footer{margin-top:40px;color:#6e7681;font-size:12px;text-align:center;
  border-top:1px solid #21262d;padding-top:16px;}
@media (max-width:760px){.dragon-page .chain{flex-direction:column;}
  .dragon-page .arrow{transform:rotate(90deg);align-self:flex-start;}}
</style>
"""


def _sev_badge(sev) -> str:
    s = int(sev) if str(sev).isdigit() else 0
    cls = f's{s}' if s in (2, 3, 4, 5) else 's2'
    return f"<span class='sev {cls}'>{_e(_severity_kind(s))}</span>"


def _render_backbone_chain(backbone) -> str:
    if not backbone:
        return "<p class='dp-note'>公告数据暂不可用, 无法确认监管高标脊柱。</p>"
    cells = []
    for i, b in enumerate(backbone):
        role = '锚定龙头' if b['role'] == 'anchor' else '接替高标'
        cls = 'baton anchor' if b['role'] == 'anchor' else 'baton'
        cum = _fmt_pct(b['cum_gain_pct']) if b['cum_gain_pct'] is not None else '—'
        halt_txt = ''
        for h in (b.get('halts') or []):
            if h.get('resumed'):
                halt_txt += f"停牌 {_fmt_date(h['start'])}→复牌 {_fmt_date(h['end'])}; "
            elif h.get('end') is None:
                halt_txt += f"停牌 {_fmt_date(h['start'])} 核查中; "
        halt_html = f"<div class='b-halt'>🔒 {_e(halt_txt.rstrip('; '))}</div>" if halt_txt else ''
        tm = (f" · 顶部标记 {_fmt_date(b['top_marker_date'])}"
              if b.get('top_marker_date') else '')
        cells.append(
            f"<div class='{cls}'><div class='b-role'>{_e(role)}</div>"
            f"<div class='b-name'>{_e(b['name'])} "
            f"<span class='b-hi'>{_e(b['peak_height'])}板</span></div>"
            f"<div class='b-meta'>首涨 {_fmt_date(b['first_rise_date'])} · "
            f"累计 {cum} · 盖章 {_e(_severity_kind(b['max_severity']))}{tm}</div>"
            f"{halt_html}</div>"
        )
        if i < len(backbone) - 1:
            cells.append("<div class='arrow'>→</div>")
    return f"<div class='chain'>{''.join(cells)}</div>"


def _render_succession(edges) -> str:
    if not edges:
        return ''
    # 按孵化母体(停牌高标)分组呈现: 一个停牌龙头 → 其孵化窗内点燃的接棒们
    by_from = {}
    for e in edges:
        by_from.setdefault((e['from'], e['from_name']), []).append(e)
    blocks = []
    for (fcode, fname), es in by_from.items():
        win = es[0].get('window') or [None, None]
        wtxt = (f"{_fmt_date(win[0])}→复牌 {_fmt_date(win[1])}"
                if win and win[0] and win[1] else
                (f"{_fmt_date(win[0])} 核查中" if win and win[0] else '—'))
        kids = '、'.join(
            f"{_e(e['to_name'])}(首涨 {_fmt_date(e.get('to_first_rise'))})"
            for e in sorted(es, key=lambda x: x.get('to_first_rise') or ''))
        blocks.append(
            f"<div class='edge'><b>{_e(fname)}</b> 停牌 <span class='yes'>{wtxt}</span> "
            f"→ 孵化期内点燃: <b>{kids}</b></div>"
        )
    return ("<h2>接替关系 · 停牌孵化链</h2>" + ''.join(blocks) +
            "<p class='dp-note'>停牌核查期资金外溢, 直接点燃孵化窗内首涨的下一棒; "
            "接棒常为脊柱外新面孔 (完整监管盖章名单见监管阶梯)。</p>")


def _render_ladder(ladder) -> str:
    if not ladder:
        return ''
    trs = []
    for r in ladder:
        cls = " class='top-marker'" if r.get('is_top_marker') else ''
        title = _e(r['title'])
        if r.get('url'):
            title = f"<a class='ann' href='{_e(r['url'])}' target='_blank' rel='noopener'>{title}</a>"
        trs.append(
            f"<tr{cls}><td>{_fmt_date(r['date'])}</td><td>{_e(r['name'])}</td>"
            f"<td>{_sev_badge(r['severity'])}</td><td>{title}</td></tr>"
        )
    return ("<h2>监管阶梯 · 时间线</h2>"
            "<table><tr><th>日期</th><th>个股</th><th>盖章</th><th>公告</th></tr>"
            + ''.join(trs) + "</table>"
            "<p class='dp-note'>红底行 = 顶部标记 (严重异常波动 / 停牌核查); "
            "历史规律: 严重异动公告 ≈ 阶段顶部信号。</p>")


def _render_collapse(collapse) -> str:
    if not collapse:
        return ''
    hit = collapse.get('is_collapse')
    cls = 'collapse-card hit' if hit else 'collapse-card'
    if not hit:
        return (f"<h2>情绪崩溃指标</h2><div class='{cls}'>"
                f"{_e(collapse.get('note', '周期内暂未触发崩溃阈值。'))}</div>")
    zt_prev, zt = collapse['zt_prev'], collapse['zt']
    dt_prev, dt = collapse['dt_prev'], collapse['dt']
    highs = collapse.get('high_leaders_limit_down') or []
    hl = ('、'.join(_e(h['name']) for h in highs)) if highs else '—'
    return (
        f"<h2>情绪崩溃指标 · {_fmt_date(collapse['date'])}</h2>"
        f"<div class='{cls}'>"
        f"<div>涨停家数 <b style='color:{_clr(-1)}'>{_e(zt_prev)} → {_e(zt)}</b> "
        f"(环比 -{_e(collapse['zt_drop_pct'])}%)</div>"
        f"<div>跌停家数 <b style='color:{_clr(1)}'>{_e(dt_prev)} → {_e(dt)}</b> "
        f"(放大 ×{_e(collapse['dt_surge_x'])})</div>"
        f"<div style='margin-top:6px'>监管高标一字跌停: <b>{hl}</b></div>"
        f"</div>"
    )


def generate_dragon_html(result) -> str:
    """龙头接替·监管周期 独立子导航整页。空/无周期 → 返回 '' (调用方不归档)。"""
    if not result or not isinstance(result, dict) or not result.get('has_cycle'):
        return ''
    try:
        cycle = result.get('cycle') or {}
        as_of = _fmt_date(result.get('as_of', ''))
        stage = result.get('teaser', {}).get('stage', '')
        span = (f"{_fmt_date(cycle.get('start'))} ~ {_fmt_date(cycle.get('end'))}"
                if cycle else '—')
        degrade = result.get('degrade_reason') if result.get('degraded') else None
        degrade_html = (f"<p class='dp-note'>⚠️ {_e(degrade)}</p>" if degrade else '')

        body = (
            f"<a class='back' href='../index.html'>← 返回首页</a>"
            f"<h1>🐉 龙头接替 · 监管周期复盘</h1>"
            f"<div class='dp-sub'>周期 {span} · 峰值 {_e(cycle.get('peak_height'))} 板 "
            f"· 状态 {_e(stage)} · 截至 {as_of}</div>"
            f"<div class='dp-headline'>{_e(result.get('headline', ''))}</div>"
            f"{degrade_html}"
            f"<h2>接替谱系 · 监管高标脊柱</h2>"
            f"{_render_backbone_chain(result.get('backbone', []))}"
            f"<p class='dp-note'>脊柱 = 本周期触发交易所异动/严重异动/停牌公告的个股中, "
            f"经监管强度×连板高度排序取前 {len(result.get('backbone', []))} 只核心真龙头 "
            f"(全周期共 {result.get('teaser', {}).get('n_board', len(result.get('backbone', [])))} "
            f"只盖章高标, 完整名单见下方监管阶梯); 按首涨日排列即接替顺序, 锚定龙头为周期最强。</p>"
            f"{_render_succession(result.get('succession', []))}"
            f"{_render_ladder(result.get('ladder', []))}"
            f"{_render_collapse(result.get('collapse', {}))}"
            f"<footer>数据自动跑批生成 · 复盘性质, 仅供研究参考, 不构成投资建议</footer>"
        )
        html = (f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
                f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>龙头接替 · 监管周期复盘 · {as_of}</title>{_DRAGON_CSS}</head>"
                f"<body><div class='dragon-page'>{body}</div></body></html>")
    except Exception:
        return ''

    try:
        from report_logic import sanitize_html_for_policy
        return sanitize_html_for_policy(html, result.get('policy') or 'observation')
    except Exception:
        return html


_TEASER_CSS = """
<style>
.dragon-teaser{display:block;text-decoration:none;margin:18px 0;
  background:linear-gradient(135deg,#221a1a,#2a1d16);
  border:1px solid #4a2a2a;border-left:4px solid #e04b4b;border-radius:12px;
  padding:16px 20px;color:#e6edf3;transition:border-color .15s,transform .15s;
  overflow-wrap:anywhere;}
.dragon-teaser:hover{border-color:#e04b4b;transform:translateX(2px);}
.dragon-teaser .dt-label{color:#e0736b;font-size:11px;font-weight:700;
  letter-spacing:1.5px;text-transform:uppercase;}
.dragon-teaser .dt-title{font-size:16px;font-weight:700;margin:3px 0;}
.dragon-teaser .dt-chain{color:#c9d1d9;font-size:13px;margin-top:4px;}
.dragon-teaser .dt-right{color:#e0736b;font-size:13px;font-weight:600;margin-top:6px;}
</style>
"""


def render_dragon_teaser_html(result) -> str:
    """主报告内一张精炼入口卡, 引流到已发布的完整子页。

    无周期/坏输入 → 返回 '' (主报告不挂卡)。href 用**绝对 SITE_URL**,
    因主报告被逐字节复制到 output/ 与 site/reports/{date}.html 与 site/latest.html,
    相对 dragon/latest.html 只对 site/latest.html 成立; 绝对 URL 从 3 处副本全部正确。
    """
    if not result or not isinstance(result, dict) or not result.get('has_cycle'):
        return ''
    try:
        t = result.get('teaser', {})
        names = t.get('backbone_names') or []
        if names:
            chain = ' → '.join(_e(n) for n in names[:5])
            if len(names) > 5:
                chain += ' …'
        else:
            chain = _e(result.get('headline', ''))
        stage = _e(t.get('stage', ''))
        n_bb = _e(t.get('n_backbone', 0))
        markers = _e(t.get('top_markers', 0))

        try:
            from paths import SITE_URL
            href = f"{str(SITE_URL).rstrip('/')}/dragon/latest.html"
        except Exception:
            href = './dragon/latest.html'

        return (
            _TEASER_CSS +
            f"<a class='dragon-teaser' href='{_e(href)}' target='_blank' rel='noopener'>"
            f"<div class='dt-label'>🐉 龙头接替 · 监管周期复盘</div>"
            f"<div class='dt-title'>{_e(result.get('headline', ''))}</div>"
            f"<div class='dt-chain'>接替脊柱 ({n_bb} 棒 · {markers} 次顶部标记): {chain}</div>"
            f"<div class='dt-right'>状态 {stage} · 打开完整谱系 (接替链 / 监管阶梯 / 情绪崩溃) →</div>"
            f"</a>"
        )
    except Exception:
        return ''
