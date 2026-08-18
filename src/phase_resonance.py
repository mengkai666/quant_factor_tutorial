# -*- coding: utf-8 -*-
"""指数阶段 × 板块共振 复盘模块 (独立模块, 每日自动直出)。

回答的问题: 指数这一段从哪见底、之后是震荡还是走趋势, 每个阶段谁领涨,
以及"下跌段跌得狠的" 和 "反弹领涨的" 是不是同一批人 (超跌反弹 vs 真新主线)。

⚠️ 核心设计: 阶段划分必须**全自动**。手挑日期的分析明天就失效, 无法进复盘管线。
   本模块用纯机械规则从指数日线识别: 顶 → 底 → 见底反弹脉冲 → 震荡/趋势段 → 最新日。
   识别不出有效底部结构 (如单边上涨市) 时返回 None, 由渲染层降级为纯板块排名。

数据源:
  上证指数日线      akshare stock_zh_index_daily (sh000001)
  90 个行业板块指数  akshare stock_board_industry_index_ths  (东财板块接口本机代理不通)
  全市场个股收益    data/price_history_cache.csv (本地真源, 算市场宽度基准)

用法 (主报告已接入, 一般不用手调):
    from phase_resonance import build_phase_resonance, render_phase_resonance_html
    res = build_phase_resonance()
    html = render_phase_resonance_html(res)
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from html import escape

import pandas as pd

from paths import (
    CLS_PLATE_CACHE, DAILY_SNAPSHOT_DIR, DATA_DIR, EM_PLATE_CACHE,
    INDUSTRY_CACHE, PRICE_CACHE, SECURITY_MASTER_CACHE, ZT_CACHE_FILE,
)
from time_utils import filter_completed_rows
from data_sources.name_resolver import resolve_names
from data_sources.price_provider import build_price_matrix, price_value_column
from micro_cycle import (
    build_cycle_resonance, build_sector_return_table,
    build_signal_limit_chain, detect_micro_cycle,
)
from report_logic import neutralize_for_policy, scan_forbidden_semantics

# 同花顺板块指数日线缓存 (每交易日刷一次; 接口单次即返全历史, 无需增量)
THS_CACHE = os.path.join(DATA_DIR, 'ths_sector_hist.json')

LOOKBACK_DAYS = 90      # 阶段识别回看窗口 (交易日)
TOP_LOOKBACK = 30       # 顶只在底之前这么多交易日内找 (锁定"贴着底的那一段下跌腿",
                        # 否则窗口内最高点可能是两个月前, 把多段下跌混成一段)
MIN_DRAWDOWN = 4.0      # 顶→底 至少跌这么多 % 才认作"有效下跌段"
V_WINDOW = 5            # 底部之后几个交易日内的最高收盘 = 见底反弹脉冲终点
BOX_MAX_AMP = 6.0       # 震荡段振幅 <= 此值 算箱体, 否则算趋势
STRONG_Q = 0.70         # 四象限: 底部至今收益分位 >= 此值 算强
WEAK_Q = 0.30           # 四象限: <= 此值 算弱

def fetch_index(symbol='sh000001', lookback=LOOKBACK_DAYS):
    """拉指数日线, 返回 [{'date','close','high','low','volume'}] (升序)。"""
    import akshare as ak
    df = ak.stock_zh_index_daily(symbol=symbol)
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
    # 指数接口经常返回当天/未来交易日缓存；历史回顾必须与全项目
    # REPORT_DATE 口径一致，不能把生成日之后的“最新”行情带入报告。
    df = filter_completed_rows(df, 'date')
    df = df.tail(lookback + 40)
    return df[['date', 'open', 'high', 'low', 'close', 'volume']].to_dict('records')


def detect_phases(idx, lookback=LOOKBACK_DAYS):
    """从指数日线机械识别阶段。识别不出有效底部返回 None。

    规则 (全部可回溯, 无手工日期):
      底  = 回看窗口内最低收盘日
      顶  = 底之前的最高收盘日
      见底段 = 底 → 底之后 V_WINDOW 个交易日内的最高收盘日 (反弹脉冲)
      震荡段 = 见底段终点 → 倒数第二个交易日
      最新日 = 最后一个交易日 (单独拎出, 看今天谁在点火/退潮)
    """
    if not idx or len(idx) < 20:
        return None
    win = idx[-lookback:] if len(idx) > lookback else idx
    lows = [r['close'] for r in win]
    bi = lows.index(min(lows))              # 底在窗口内的下标
    # 底不能是最后一天 (还在跌, 没有"见底后"可谈), 也不能是第一天 (看不到下跌段)
    if bi >= len(win) - 2 or bi == 0:
        return None
    # 顶只在底前 TOP_LOOKBACK 个交易日内找: 要的是"贴着这个底的那一段下跌腿",
    # 不是窗口内的绝对最高点 (那可能是两个月前, 会把多段独立下跌混成一段)
    lo = max(0, bi - TOP_LOOKBACK)
    pre = win[lo:bi + 1]
    ti = lo + max(range(len(pre)), key=lambda i: pre[i]['close'])
    if ti >= bi:
        return None
    top, bot = win[ti], win[bi]
    dd = (bot['close'] / top['close'] - 1) * 100
    if dd > -MIN_DRAWDOWN:                  # 跌幅不够, 不算有效下跌段
        return None

    # 见底反弹脉冲: 底之后 V_WINDOW 日内最高收盘
    tail = win[bi:bi + V_WINDOW + 1]
    vi = bi + max(range(len(tail)), key=lambda i: tail[i]['close'])
    v_end = win[vi]

    phases = {'下跌段': (top['date'], bot['date']),
              '见底段': (bot['date'], v_end['date'])}
    # 震荡/趋势段 + 最新日
    latest = win[-1]
    if vi < len(win) - 2:
        osc_end = win[-2]
        phases['震荡段'] = (v_end['date'], osc_end['date'])
        phases['最新日'] = (osc_end['date'], latest['date'])
    elif vi < len(win) - 1:
        phases['最新日'] = (v_end['date'], latest['date'])
    phases['底部至今'] = (bot['date'], latest['date'])

    # 震荡段形态: 箱体 / 单边上行 / 二次探底
    seg = win[vi:]
    shape, box_lo, box_hi, amp = '—', None, None, None
    if len(seg) >= 3:
        box_hi = max(r['high'] for r in seg)
        box_lo = min(r['low'] for r in seg)
        amp = (box_hi / box_lo - 1) * 100
        c_last = latest['close']
        if amp <= BOX_MAX_AMP:
            if c_last >= box_hi * 0.997:
                shape = f'箱体突破 (箱体 {box_lo:.0f}~{box_hi:.0f}, 振幅 {amp:.1f}%, 收在上沿)'
            elif c_last <= box_lo * 1.003:
                shape = f'箱体下沿 (箱体 {box_lo:.0f}~{box_hi:.0f}, 振幅 {amp:.1f}%, 收在下沿)'
            else:
                pos = (c_last - box_lo) / (box_hi - box_lo) * 100
                shape = f'箱体震荡 (箱体 {box_lo:.0f}~{box_hi:.0f}, 振幅 {amp:.1f}%, 当前位于箱体 {pos:.0f}% 处)'
        else:
            shape = ('反弹趋势段' if c_last > seg[0]['close'] else '二次探底') + \
                    f' (区间 {box_lo:.0f}~{box_hi:.0f}, 振幅 {amp:.1f}%)'

    return {
        'phases': phases, 'shape': shape,
        'box_lo': box_lo, 'box_hi': box_hi, 'amp': amp,
        'top': {'date': top['date'], 'close': top['close']},
        'bottom': {'date': bot['date'], 'close': bot['close'],
                   'low': min(r['low'] for r in win[bi:bi + 3])},
        'latest': {'date': latest['date'], 'close': latest['close']},
        'drawdown': round(dd, 2),
        'index_series': win,
    }


def _seg_ret(recs, s, e, key='close'):
    """区间收益 %。端点取 <= 该日的最后一条 (端点缺失自动回退, 稳)。"""
    a = [r for r in recs if r['date'] <= s]
    b = [r for r in recs if r['date'] <= e]
    if not a or not b:
        return None
    c0, c1 = a[-1][key], b[-1][key]
    return round((c1 / c0 - 1) * 100, 2) if c0 else None


def _cache_covers(cache, need_start, latest_date):
    """缓存必须**两端都覆盖**才算可用。

    ⚠️ 只查最新日会漏掉一类静默错误: 缓存起点晚于下跌段起点时, 下跌段收益
       全为 None, 四象限/相关系数直接哑掉 (曾经就是这么坏的)。
    """
    if not cache:
        return False
    for recs in cache.values():
        if recs and recs[-1]['date'] >= latest_date and recs[0]['date'] <= need_start:
            return True
    return False


def fetch_sectors(latest_date, need_start=None, force=False):
    """拉同花顺 90 个行业板块指数日线。缓存两端都覆盖时复用。

    need_start: 分析需要的最早日期 (通常是下跌段起点)。缓存起点晚于它就必须重拉。
    """
    cache = {}
    if os.path.exists(THS_CACHE):
        try:
            with open(THS_CACHE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    need_start = need_start or latest_date
    if not force and _cache_covers(cache, need_start, latest_date):
        print(f'  📦 板块指数缓存已覆盖 {need_start}~{latest_date}, 复用 ({len(cache)} 个板块)')
        return cache

    import akshare as ak
    try:
        names = ak.stock_board_industry_summary_ths()['板块'].tolist()
    except Exception as e:
        print(f'  ⚠️ 板块列表获取失败, 用缓存兜底: {e}')
        return cache

    # 起点再往前留 120 自然日缓冲, 免得下次阶段起点略微前移就得重拉全量
    start = (pd.Timestamp(need_start) - pd.Timedelta(days=120)).strftime('%Y%m%d')
    end = pd.Timestamp(latest_date).strftime('%Y%m%d')
    print(f'  🌐 拉取 {len(names)} 个板块指数日线...')
    ok = 0
    for i, n in enumerate(names, 1):
        for attempt in range(3):
            try:
                d = ak.stock_board_industry_index_ths(
                    symbol=n, start_date=start, end_date=end)
                if d is not None and len(d):
                    d = d.rename(columns={'日期': 'date', '收盘价': 'close',
                                          '最高价': 'high', '最低价': 'low',
                                          '成交额': 'amount'})
                    d['date'] = pd.to_datetime(d['date']).dt.strftime('%Y-%m-%d')
                    cache[n] = d[['date', 'close', 'high', 'low', 'amount']].to_dict('records')
                    ok += 1
                break
            except Exception:
                time.sleep(0.8 * (attempt + 1))
        if i % 30 == 0:
            print(f'    {i}/{len(names)} ...', flush=True)
            with open(THS_CACHE, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False)
    with open(THS_CACHE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)
    print(f'  ✅ 板块指数 {ok}/{len(names)}')
    return cache


def sector_table(cache, det):
    """板块 × 阶段收益表 (含量比与距顶幅度)。"""
    ph = det['phases']
    fall_s, fall_e = ph['下跌段']
    bot_s, bot_e = ph['底部至今']
    rows = []
    for name, recs in cache.items():
        if not recs:
            continue
        r = {'板块': name}
        for p, (s, e) in ph.items():
            r[p] = _seg_ret(recs, s, e)
        # 量比: 底部之后日均成交额 / 下跌段日均 (真金白银是否回流)
        try:
            f_amt = [x['amount'] for x in recs if fall_s < x['date'] <= fall_e]
            b_amt = [x['amount'] for x in recs if bot_s < x['date'] <= bot_e]
            r['量比'] = round((sum(b_amt) / len(b_amt)) / (sum(f_amt) / len(f_amt)), 2) \
                if f_amt and b_amt else None
        except Exception:
            r['量比'] = None
        # 距顶: 区分"真新高"和"只是修复"
        r['距顶'] = _seg_ret(recs, ph['下跌段'][0], ph['底部至今'][1])
        rows.append(r)
    t = pd.DataFrame(rows)
    return t.dropna(subset=['底部至今']) if '底部至今' in t.columns else t


QUADRANTS = [
    ('独立主线', '抗跌 + 领涨', '真正的新方向, 资金主动选择', '#3fb950'),
    ('超跌反弹', '跌深 + 弹猛', '弹性来自跌幅, 不是新逻辑', '#d29922'),
    ('防御退潮', '抗跌 但 涨不动', '下跌段的避风港, 现在是死钱', '#8b949e'),
    ('深度受损', '跌深 且 没修复', '逻辑被破坏, 反弹最弱', '#f85149'),
]


def quadrants(t, det):
    """四象限分组: 下跌段抗跌性 × 底部至今强度。返回 {象限名: DataFrame}。"""
    if t.empty or '下跌段' not in t.columns:
        return {}
    c = t.dropna(subset=['下跌段', '底部至今'])
    if c.empty:
        return {}
    base = det['drawdown']                          # 指数下跌段幅度作抗跌基准
    strong = c['底部至今'].quantile(STRONG_Q)
    weak = c['底部至今'].quantile(WEAK_Q)
    out = {}
    out['独立主线'] = c[(c['下跌段'] > base) & (c['底部至今'] >= strong)]
    out['超跌反弹'] = c[(c['下跌段'] <= base) & (c['底部至今'] >= strong)]
    out['防御退潮'] = c[(c['下跌段'] > base) & (c['底部至今'] < weak)]
    out['深度受损'] = c[(c['下跌段'] <= base) & (c['底部至今'] < weak)]
    return {k: v.sort_values('底部至今', ascending=False) for k, v in out.items()}


def market_breadth(det):
    """全市场个股各阶段收益中位数 + 上涨占比 (指数震荡时底下到底涨没涨)。"""
    if not os.path.exists(PRICE_CACHE):
        return {}
    try:
        df = pd.read_csv(PRICE_CACHE)
        df = filter_completed_rows(df, 'date')
        value_column = price_value_column(df, 'qfq', allow_legacy=True)
        if not value_column:
            return {}
        df = df[pd.to_numeric(df[value_column], errors='coerce') > 0]
        px = df.pivot_table(index='date', columns='code', values=value_column,
                            aggfunc=lambda s: s.iloc[-1])
    except Exception as e:
        print(f'  ⚠️ 市场宽度计算跳过: {e}')
        return {}
    dates = list(px.index)

    def near(d):
        c = [x for x in dates if x <= d]
        return c[-1] if c else None

    out = {}
    for p, (s, e) in det['phases'].items():
        ds, de = near(s), near(e)
        if not ds or not de or ds == de:
            continue
        r = ((px.loc[de] / px.loc[ds] - 1) * 100).dropna()
        if r.empty:
            continue
        out[p] = {'median': round(r.median(), 2), 'win': round((r > 0).mean() * 100, 1),
                  'n': len(r)}
    return out


def build_turning_summary(det, table, stock_leaders, *, sector_top_n=3):
    """Build the current-stage summary anchored to the detected major bottom."""
    shape = str((det or {}).get("shape") or "").strip()
    label, separator, remainder = shape.partition(" (")
    detail = remainder[:-1] if separator and remainder.endswith(")") else remainder
    bottom = (det or {}).get("bottom") or {}
    latest = (det or {}).get("latest") or {}
    turning_date = str(bottom.get("date") or "")
    latest_date = str(latest.get("date") or "")
    bottom_close = pd.to_numeric(bottom.get("close"), errors="coerce")
    latest_close = pd.to_numeric(latest.get("close"), errors="coerce")
    index_return = None
    if pd.notna(bottom_close) and pd.notna(latest_close) and float(bottom_close) != 0:
        index_return = round((float(latest_close) / float(bottom_close) - 1) * 100, 2)

    trading_days = sum(
        1
        for row in (det or {}).get("index_series", [])
        if turning_date <= str(row.get("date") or "") <= latest_date
    )
    sectors = []
    if isinstance(table, pd.DataFrame) and not table.empty and {"板块", "底部至今"}.issubset(table.columns):
        ranked = table[["板块", "底部至今"]].copy()
        ranked["底部至今"] = pd.to_numeric(ranked["底部至今"], errors="coerce")
        ranked = ranked.dropna(subset=["底部至今"]).sort_values(
            ["底部至今", "板块"], ascending=[False, True]
        )
        sectors = [
            {"name": str(row["板块"]), "return": round(float(row["底部至今"]), 2)}
            for _, row in ranked.head(max(0, int(sector_top_n))).iterrows()
        ]

    stock_leaders = stock_leaders or {}
    stocks = list(stock_leaders.get("rows") or []) if stock_leaders.get("usable") else []
    stock_hint = "" if stock_leaders.get("usable") else "区间个股覆盖不足"
    return {
        "current_phase": {
            "label": label or shape or "阶段待确认",
            "detail": detail,
            "turning_date": turning_date,
            "latest_date": latest_date,
            "index_return": index_return,
            "trading_days": trading_days,
        },
        "turning_leaders": {
            "sectors": sectors,
            "stocks": stocks,
            "stock_hint": stock_hint,
        },
    }


_MEMO = {}


def build_phase_resonance(force_fetch=False):
    """主入口: 返回完整分析结果 dict, 失败返回 None (调用方静默降级)。

    进程内结果记忆: 主报告与决策看板归档会各调一次 generate_html, 不缓存的话
    同一次运行要重复拉两遍指数日线。
    """
    if not force_fetch and 'r' in _MEMO:
        return _MEMO['r']
    r = _build(force_fetch)
    _MEMO['r'] = r          # None 也记, 免得失败后每次调用都重试一遍网络
    return r


def _read_csv(path, **kwargs):
    if not path or not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path, **kwargs)
    except (OSError, UnicodeError, ValueError, pd.errors.ParserError):
        return pd.DataFrame()


def _pick_column(frame, *candidates):
    return next((name for name in candidates if name in frame.columns), None)


def _daily_limit_counts(history):
    if history is None or history.empty:
        return {}
    date_col = _pick_column(history, "date", "日期")
    code_col = _pick_column(history, "code", "代码")
    type_col = _pick_column(history, "type", "类型")
    if not date_col or not code_col:
        return {}
    frame = history.copy()
    if type_col:
        frame = frame[frame[type_col].astype(str).str.upper().eq("ZT")]
    frame["_date"] = frame[date_col].astype(str).str.replace("-", "", regex=False)
    frame["_code"] = frame[code_col].astype(str).str.strip()
    grouped = frame[frame["_code"].ne("")].groupby("_date")["_code"].nunique()
    return {
        f"{date[:4]}-{date[4:6]}-{date[6:8]}": int(count)
        for date, count in grouped.items()
        if len(str(date)) == 8
    }


def _immutable_snapshot_dates():
    result = set()
    if not os.path.isdir(DAILY_SNAPSHOT_DIR):
        return result
    try:
        names = os.listdir(DAILY_SNAPSHOT_DIR)
    except OSError:
        return result
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(DAILY_SNAPSHOT_DIR, name), encoding="utf-8") as handle:
                snapshot = json.load(handle)
        except (OSError, UnicodeError, ValueError, TypeError):
            continue
        if not isinstance(snapshot, dict):
            continue
        date = name[:-5]
        if (
            snapshot.get("snapshot_schema") == "daily-fact-snapshot/v1"
            and snapshot.get("date_verified") is True
            and snapshot.get("immutable") is True
            and str(snapshot.get("report_date") or date) == date
        ):
            result.add(date)
    return result


def _build_micro_cycle_payload(det, cache):
    history = _read_csv(ZT_CACHE_FILE)
    counts = _daily_limit_counts(history)
    micro_cycle = detect_micro_cycle(det, daily_limit_counts=counts)
    micro_chain = {}
    micro_resonance = {}
    if micro_cycle.get("signal_date") and micro_cycle.get("confirmation_date"):
        master = _read_csv(SECURITY_MASTER_CACHE, dtype=str)
        industry = _read_csv(INDUSTRY_CACHE, dtype=str)
        names = resolve_names(universe=master, industry=industry)
        trading_dates = [row["date"] for row in det["index_series"]]
        micro_chain = build_signal_limit_chain(
            history, trading_dates, micro_cycle["signal_date"], det["latest"]["date"],
            names=names, immutable_dates=_immutable_snapshot_dates(),
        )
        sector_returns = build_sector_return_table(
            cache, micro_cycle["signal_date"], det["latest"]["date"],
            micro_cycle.get("signal_return"),
        )
        price_history = _read_csv(PRICE_CACHE)
        price_history = filter_completed_rows(
            price_history, "date", report_date=det["latest"]["date"],
        )
        prices = build_price_matrix(price_history, "qfq", allow_legacy=True)
        micro_resonance = build_cycle_resonance(
            sector_returns, micro_chain, prices,
            micro_cycle["signal_date"], det["latest"]["date"],
            cls_attribution=_read_csv(CLS_PLATE_CACHE, dtype=str),
            em_attribution=_read_csv(EM_PLATE_CACHE, dtype=str),
        )
    return {
        "micro_cycle": micro_cycle,
        "micro_chain": micro_chain,
        "micro_resonance": micro_resonance,
    }


def _attach_micro_cycle(result, det, cache):
    try:
        payload = _build_micro_cycle_payload(det, cache)
    except Exception as exc:
        print(f"  短周期共振计算跳过 (不影响主要阶段): {exc}")
        payload = {"micro_cycle": {}, "micro_chain": {}, "micro_resonance": {}}
    return {**result, **payload}


def _build(force_fetch=False):
    try:
        idx = fetch_index()
    except Exception as e:
        print(f'  ⚠️ 阶段共振: 指数日线获取失败, 跳过 ({e})')
        return None
    det = detect_phases(idx)
    if not det:
        print('  ℹ️ 阶段共振: 当前无有效"顶→底→反弹"结构 (单边市/数据不足), 跳过')
        return None

    latest = det['latest']['date']
    cache = fetch_sectors(latest, need_start=det['phases']['下跌段'][0],
                          force=force_fetch)
    if not cache:
        print('  ⚠️ 阶段共振: 板块指数为空, 跳过')
        return None

    t = sector_table(cache, det)
    if t.empty:
        return None
    # 下跌段全空 = 板块历史不够长, 四象限与相关系数会哑掉。显式告警, 不静默出残页。
    if '下跌段' in t.columns and t['下跌段'].notna().sum() < 6:
        print(f"  ⚠️ 阶段共振: 板块指数覆盖不到下跌段起点 {det['phases']['下跌段'][0]}, "
              f"四象限/相关性不可用 (下次运行会自动重拉)")

    ph = list(det['phases'].keys())
    idx_ret = {p: _seg_ret(det['index_series'], s, e)
               for p, (s, e) in det['phases'].items()}
    # 相关性: 下跌段跌幅 vs 底部至今涨幅 (负 = 超跌反弹主导, 正 = 强者延续)
    cc = t[['下跌段', '底部至今']].dropna()
    corr = round(cc['下跌段'].corr(cc['底部至今']), 3) if len(cc) > 5 else None

    ranks = {}
    for p in ph:
        if p in t.columns and t[p].notna().any():
            ranks[p] = t.nlargest(8, p)[['板块', p, '量比']].to_dict('records')

    # 四象限个股代表 (全市场无偏, 独立模块; 失败时保留空结构供发布门禁识别)
    representatives = {}
    reps_html = ''
    stock_leaders = {"usable": False, "coverage": 0.0, "rows": []}
    try:
        from stock_representatives import (build_representatives,
                                           render_representatives_html)
        representatives = build_representatives(det['phases'], det['drawdown'])
        reps_html = render_representatives_html(representatives)
    except Exception as e:
        print(f'  ⚠️ 个股代表计算失败 (不影响板块部分): {e}')

    try:
        from stock_representatives import build_turning_stock_leaders
        stock_leaders = build_turning_stock_leaders(det['phases'])
    except Exception as e:
        print(f'  ⚠️ 转折以来个股榜计算失败 (不影响阶段与板块部分): {e}')

    turning_summary = build_turning_summary(det, t, stock_leaders)

    result = {
        'det': det, 'phase_names': ph, 'index_ret': idx_ret,
        'table': t, 'ranks': ranks, 'quadrants': quadrants(t, det),
        'representatives': representatives,
        'reps_html': reps_html,
        'corr': corr, 'breadth': market_breadth(det),
        'top_overall': t.nlargest(12, '底部至今').to_dict('records'),
        'bottom_overall': t.nsmallest(8, '底部至今').to_dict('records'),
        'new_high': t[t['距顶'] > 0].sort_values('距顶', ascending=False)
                     .to_dict('records') if '距顶' in t.columns else [],
        **turning_summary,
    }
    return _attach_micro_cycle(result, det, cache)


def _clr(v):
    """涨跌上色 (A股习惯: 红涨绿跌)。"""
    if v is None:
        return '#8b949e'
    return '#f85149' if v > 0 else ('#3fb950' if v < 0 else '#8b949e')


def _pct(v, digits=2):
    return '—' if v is None else f'{v:+.{digits}f}%'


def _turning_summary_html(res):
    """Render the permanent stage-and-leaders product summary."""
    phase = (res or {}).get("current_phase") or {}
    if not phase:
        return ""
    leaders = (res or {}).get("turning_leaders") or {}
    sectors = leaders.get("sectors") or []
    stocks = leaders.get("stocks") or []

    def safe(value):
        return escape(str(value or ""), quote=True)

    def return_text(value):
        try:
            number = float(value)
            if pd.isna(number):
                return "—"
            return f"{number:+.1f}%"
        except (TypeError, ValueError):
            return "—"

    index_return = phase.get("index_return")
    index_text = return_text(index_return)
    index_color = _clr(index_return)
    detail = safe(phase.get("detail"))
    detail_html = (
        f'<div class="phase-turning-detail">{detail}</div>' if detail else ""
    )
    turning_date = safe(phase.get("turning_date"))
    latest_date = safe(phase.get("latest_date"))
    trading_days = phase.get("trading_days")
    day_text = f" · {int(trading_days)} 个交易日" if trading_days else ""
    current = f'''
      <div class="phase-turning-col">
        <div class="phase-turning-kicker">当前阶段</div>
        <div class="phase-turning-stage">{safe(phase.get("label"))}</div>
        {detail_html}
        <div class="phase-turning-meta">主要转折 {turning_date} · 至 {latest_date}{day_text}</div>
        <div class="phase-turning-index">上证区间 <span style="color:{index_color};">{index_text}</span></div>
      </div>'''

    sector_rows = "".join(
        f'<div class="phase-turning-row"><span><b>{index}</b>{safe(row.get("name"))}</span>'
        f'<span style="color:{_clr(row.get("return"))};">{return_text(row.get("return"))}</span></div>'
        for index, row in enumerate(sectors, 1)
    )
    sector_column = (
        f'<div class="phase-turning-col"><div class="phase-turning-kicker">转折以来领涨板块</div>'
        f'{sector_rows}</div>' if sector_rows else ""
    )

    stock_rows = "".join(
        f'<div class="phase-turning-row"><span><b>{index}</b>{safe(row.get("name"))}'
        f'{"<i>ST</i>" if row.get("st") else ""}'
        f'<small>{safe(row.get("code"))}</small></span>'
        f'<span style="color:{_clr(row.get("return"))};">{return_text(row.get("return"))}</span></div>'
        for index, row in enumerate(stocks, 1)
    )
    stock_column = (
        f'<div class="phase-turning-col"><div class="phase-turning-kicker">转折以来领涨个股</div>'
        f'{stock_rows}</div>' if stock_rows else ""
    )

    columns = "".join((current, sector_column, stock_column))
    column_count = 1 + bool(sector_column) + bool(stock_column)
    hint = safe(leaders.get("stock_hint"))
    hint_html = f'<div class="phase-turning-hint">{hint}</div>' if hint else ""
    return f'''
    <style>
      .phase-turning-summary {{margin:4px 0 16px;border-top:1px solid rgba(48,54,61,.8);border-bottom:1px solid rgba(48,54,61,.8);}}
      .phase-turning-grid {{display:grid;grid-template-columns:repeat(var(--phase-cols),minmax(0,1fr));}}
      .phase-turning-col {{min-width:0;padding:14px 16px;overflow-wrap:anywhere;}}
      .phase-turning-col + .phase-turning-col {{border-left:1px solid rgba(48,54,61,.8);}}
      .phase-turning-kicker {{color:#8b949e;font-size:11px;font-weight:700;margin-bottom:8px;}}
      .phase-turning-stage {{color:#e6edf3;font-size:20px;font-weight:800;line-height:1.2;}}
      .phase-turning-detail {{color:#8b949e;font-size:12px;line-height:1.6;margin-top:5px;}}
      .phase-turning-meta,.phase-turning-index {{color:#8b949e;font-size:11px;line-height:1.6;margin-top:7px;}}
      .phase-turning-index span {{font-weight:800;}}
      .phase-turning-row {{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:5px 0;color:#e6edf3;font-size:12px;}}
      .phase-turning-row > span:first-child {{min-width:0;}}
      .phase-turning-row b {{display:inline-block;width:18px;color:#6e7681;font-size:10px;}}
      .phase-turning-row small {{display:block;margin-left:18px;color:#6e7681;font-size:10px;}}
      .phase-turning-row i {{margin-left:5px;color:#d29922;font-size:9px;font-style:normal;font-weight:800;}}
      .phase-turning-row > span:last-child {{flex:none;font-weight:800;white-space:nowrap;}}
      .phase-turning-hint {{padding:0 16px 10px;color:#6e7681;font-size:11px;}}
      @media (max-width:760px) {{
        .phase-turning-grid {{grid-template-columns:minmax(0,1fr);}}
        .phase-turning-col + .phase-turning-col {{border-left:0;border-top:1px solid rgba(48,54,61,.8);}}
      }}
    </style>
    <div class="phase-turning-summary">
      <div class="phase-turning-grid" style="--phase-cols:{column_count};">{columns}</div>
      {hint_html}
    </div>'''


def _micro_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>", "nat"} else text


_MICRO_FACT_STATUSES = frozenset({"探底未完成", "震荡筑底", "震荡转升", "小周期主升"})
_MICRO_FACT_LEVELS = frozenset({"核心共振", "次级共振", "连板跟随"})
_MICRO_FACT_DATE = re.compile(r"^(\d{4})[-/]?(\d{1,2})[-/]?(\d{1,2})$")
_MICRO_DEFAULT_IGNORABLE_RANGES = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C),
    (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F),
    (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F),
    (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def _micro_policy_normalized_text(value):
    text = unicodedata.normalize("NFKC", _micro_text(value))
    return "".join(
        char for char in text
        if unicodedata.category(char) != "Cf"
        and not any(start <= ord(char) <= end for start, end in _MICRO_DEFAULT_IGNORABLE_RANGES)
    )


def _micro_restricted_text(value):
    text = _micro_policy_normalized_text(value)
    if not text:
        return ""
    if not scan_forbidden_semantics(text, "facts_only"):
        return text
    cleaned = neutralize_for_policy(text, "facts_only")
    return "" if cleaned == "当前仅展示已校验事实" else cleaned


def _micro_restricted_date(value):
    text = _micro_policy_normalized_text(value)
    match = _MICRO_FACT_DATE.fullmatch(text)
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    try:
        pd.Timestamp(year=year, month=month, day=day)
    except ValueError:
        return ""
    return f"{month}/{day}"


def _micro_number(value):
    try:
        number = pd.to_numeric(value, errors="coerce")
        if not pd.api.types.is_scalar(number) or pd.isna(number):
            return None
        return float(number)
    except (TypeError, ValueError):
        return None


def _micro_date(value):
    text = _micro_text(value)
    parts = text.split("-")
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        return f"{int(parts[1])}/{int(parts[2])}"
    return text


def _micro_return(value):
    number = _micro_number(value)
    return "—" if number is None else f"{number:+.1f}%"


def _micro_color(value):
    return _clr(_micro_number(value))


def _micro_integer(value):
    number = _micro_number(value)
    return 0 if number is None else int(number)


def _micro_level(label, value):
    number = _micro_number(value)
    return "" if number is None else f"{label} {number:.0f}"


def _micro_cycle_html(res, *, restricted=False):
    micro = (res or {}).get("micro_cycle") or {}
    if not micro:
        return ""
    text_value = _micro_restricted_text if restricted else _micro_text
    date_value = _micro_restricted_date if restricted else _micro_date
    events = micro.get("events") or {}
    final_stop = events.get("final_stop") or {}
    rebound = events.get("rebound_high") or {}
    secondary = events.get("secondary_bottom") or {}
    retest = events.get("retest") or {}
    rebound_date = ""
    if rebound.get("high_date"):
        rebound_date = date_value(rebound.get("high_date"))
        close_date = date_value(rebound.get("close_date"))
        if close_date:
            rebound_date += f"-{close_date.split('/')[-1]}"
    timeline = [
        (date_value(final_stop.get("date")), "止跌确认", _micro_level("盘中低点", final_stop.get("low"))),
        (rebound_date, "第一反弹高点", "盘中与收盘高点分开确认"),
        (
            date_value(secondary.get("date")), "二次探底",
            "低点抬升" if secondary.get("higher_low") else "再次破底",
        ),
        (date_value(retest.get("date")), "局部回测", _micro_level("收盘", retest.get("close"))),
        (date_value(micro.get("signal_date")), "转强信号", "收涨且低点不再下移"),
        (date_value(micro.get("confirmation_date")), "收盘突破确认", "突破第一反弹收盘高点"),
    ]
    event_html = "".join(
        '<div class="micro-cycle-event">'
        f'<b>{escape(date, quote=True)}</b><span>{escape(label, quote=True)}</span>'
        f'{f"<small>{escape(evidence, quote=True)}</small>" if evidence else ""}</div>'
        for date, label, evidence in timeline if date
    )

    resonance = (res or {}).get("micro_resonance") or {}
    industry_html = "".join(
        f'<span>{escape(name, quote=True)} '
        f'<i style="color:{_micro_color(row.get("return"))};">{_micro_return(row.get("return"))}</i></span>'
        for row in resonance.get("strong_industries") or []
        if (name := text_value(row.get("name")))
    )
    industry_section = (
        f'<div class="micro-subhead">强行业</div><div class="micro-strong-industries">{industry_html}</div>'
        if industry_html else ""
    )

    mainline_html = ""
    for row in resonance.get("mainlines") or []:
        raw_level = (
            _micro_policy_normalized_text(row.get("level"))
            if restricted else _micro_text(row.get("level"))
        )
        level = raw_level if restricted and raw_level in _MICRO_FACT_LEVELS else text_value(raw_level)
        name = text_value(row.get("name"))
        if restricted and raw_level not in _MICRO_FACT_LEVELS:
            level = ""
        if not level and not name:
            continue
        evidence = " · ".join(
            item for value in row.get("industry_evidence") or []
            if (item := text_value(value))
        )
        leader_rows = []
        for stock in row.get("leaders") or []:
            code = text_value(stock.get("code"))
            display = text_value(stock.get("name")) or code
            if not display:
                continue
            code_html = f'<small>{escape(code, quote=True)}</small>' if code else ""
            leader_rows.append(
                '<span class="micro-leader">'
                f'{escape(display, quote=True)}{code_html}'
                f'<i style="color:{_micro_color(stock.get("return"))};">'
                f'{_micro_return(stock.get("return"))}</i></span>'
            )
        leaders = "".join(leader_rows)
        leader_block = (
            f'<div class="micro-leaders"><em>板块领涨个股</em>{leaders}</div>'
            if leaders else ""
        )
        level_html = f'<b>{escape(level, quote=True)}</b>' if level else ""
        name_html = f'<strong>{escape(name, quote=True)}</strong>' if name else ""
        evidence_html = f' · 行业证据 {escape(evidence, quote=True)}' if evidence else ""
        mainline_html += (
            '<div class="micro-mainline-row">'
            f'<div>{level_html}{name_html}'
            f'<small>启动 {_micro_integer(row.get("chain_count"))}/{_micro_integer(row.get("chain_total"))}'
            f'{evidence_html}</small></div>{leader_block}</div>'
        )
    mainline_section = (
        f'<div class="micro-subhead">共振主线</div>{mainline_html}' if mainline_html else ""
    )

    chain = (res or {}).get("micro_chain") or {}
    hints = [text_value(chain.get("hint"))]
    attribution_coverage = _micro_number(resonance.get("attribution_coverage", 1.0))
    if attribution_coverage is not None and attribution_coverage < 1.0:
        hints.append(f"主线归因覆盖 {attribution_coverage:.0%}")
    leader_coverage = _micro_number(resonance.get("leader_coverage", 1.0))
    if leader_coverage is not None and leader_coverage < 0.8:
        hints.append("个股前复权端点覆盖不足，收益暂不展示")
    hint_html = " · ".join(escape(item, quote=True) for item in hints if item)
    hint_block = f'<div class="micro-cycle-hint">{hint_html}</div>' if hint_html else ""
    full_date = date_value(micro.get("full_confirmation_date"))
    full_text = f" · {escape(full_date, quote=True)} 全面突破" if full_date else ""
    signal_summary = (
        f'<small>转强后 {_micro_return(micro.get("signal_return"))} · '
        f'连续 {_micro_integer(micro.get("rising_days"))} 日收涨{full_text}</small>'
        if date_value(micro.get("signal_date")) else ""
    )
    raw_status = (
        _micro_policy_normalized_text(micro.get("status"))
        if restricted else _micro_text(micro.get("status"))
    )
    status = raw_status if restricted and raw_status in _MICRO_FACT_STATUSES else text_value(raw_status)
    if restricted and raw_status not in _MICRO_FACT_STATUSES:
        status = ""
    status_html = f'<b>{escape(status, quote=True)}</b>' if status else ""
    return f'''
    <style>
      .micro-cycle-section{{margin:0 0 16px;border-top:1px solid rgba(48,54,61,.8);border-bottom:1px solid rgba(48,54,61,.8);padding:16px 0;overflow-wrap:anywhere;}}
      .micro-cycle-head{{display:flex;justify-content:space-between;gap:16px;align-items:flex-end;margin:0 16px 14px;}}
      .micro-cycle-head h3{{margin:0;color:#e6edf3;font-size:16px;letter-spacing:0;}}
      .micro-cycle-head b{{color:#58a6ff;font-size:20px;}}
      .micro-cycle-head small,.micro-cycle-hint{{color:#8b949e;font-size:11px;}}
      .micro-cycle-timeline{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));border-top:1px solid rgba(48,54,61,.7);border-bottom:1px solid rgba(48,54,61,.7);}}
      .micro-cycle-event{{min-width:0;padding:12px 10px;display:flex;flex-direction:column;gap:4px;}}
      .micro-cycle-event+.micro-cycle-event{{border-left:1px solid rgba(48,54,61,.7);}}
      .micro-cycle-event b{{color:#58a6ff;font-size:13px;}}.micro-cycle-event span{{color:#e6edf3;font-size:12px;font-weight:700;}}
      .micro-cycle-event small{{color:#8b949e;font-size:11px;line-height:1.45;}}
      .micro-subhead{{margin:14px 16px 8px;color:#8b949e;font-size:11px;font-weight:700;}}
      .micro-strong-industries{{display:flex;flex-wrap:wrap;gap:8px 16px;margin:0 16px;}}
      .micro-strong-industries span{{color:#e6edf3;font-size:12px;}}.micro-strong-industries i{{font-style:normal;font-weight:700;}}
      .micro-mainline-row{{display:grid;grid-template-columns:minmax(220px,.8fr) minmax(0,1.5fr);gap:16px;padding:11px 16px;border-top:1px solid rgba(48,54,61,.55);}}
      .micro-mainline-row>div:first-child{{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;}}
      .micro-mainline-row b{{color:#d29922;font-size:11px;}}.micro-mainline-row strong{{color:#e6edf3;font-size:14px;}}
      .micro-mainline-row small{{color:#8b949e;font-size:10px;}}
      .micro-leaders{{display:flex;gap:8px 12px;align-items:center;flex-wrap:wrap;}}.micro-leaders em{{color:#8b949e;font-size:10px;font-style:normal;}}
      .micro-leader{{display:inline-flex;gap:5px;align-items:baseline;color:#e6edf3;font-size:12px;flex-wrap:wrap;}}
      .micro-leader small{{font-size:9px;}}.micro-leader i{{font-size:11px;font-style:normal;font-weight:700;}}
      .micro-cycle-hint{{margin:10px 16px 0;}}
      @media(max-width:760px){{.micro-cycle-head{{align-items:flex-start;flex-direction:column;}}.micro-cycle-timeline{{grid-template-columns:1fr;}}.micro-cycle-event+.micro-cycle-event{{border-left:0;border-top:1px solid rgba(48,54,61,.55);}}.micro-mainline-row{{grid-template-columns:1fr;gap:8px;}}}}
    </style>
    <section class="micro-cycle-section">
      <div class="micro-cycle-head"><div><h3>短周期结构</h3>{status_html}</div>
      {signal_summary}</div>
      <div class="micro-cycle-timeline">{event_html}</div>
      {industry_section}{mainline_section}
      {hint_block}
    </section>'''


def render_micro_cycle_html(res):
    """Render only confirmed short-cycle facts for restricted reports."""
    return _micro_cycle_html(res, restricted=True)


def _phase_timeline(res):
    """阶段时间轴表: 每段区间 + 指数涨跌 + 个股中位 + 上涨占比。"""
    det, br = res['det'], res['breadth']
    rows = ''
    for p in res['phase_names']:
        s, e = det['phases'][p]
        ir = res['index_ret'].get(p)
        b = br.get(p) or {}
        med, win = b.get('median'), b.get('win')
        # 指数与个股中位背离 (指数跌但个股涨) 是"指数震荡, 底下普涨"的铁证
        flag = ''
        if ir is not None and med is not None and ir < 0 < med:
            flag = '<span style="color:#d29922;font-weight:bold;"> ⚠背离</span>'
        rows += (
            f'<tr style="border-bottom:1px solid rgba(48,54,61,0.5);">'
            f'<td style="padding:7px 10px;color:#e6edf3;font-weight:bold;white-space:nowrap;">{p}{flag}</td>'
            f'<td style="padding:7px 10px;color:#8b949e;font-size:12px;white-space:nowrap;">{s} → {e}</td>'
            f'<td style="padding:7px 10px;color:{_clr(ir)};font-weight:bold;text-align:right;">{_pct(ir)}</td>'
            f'<td style="padding:7px 10px;color:{_clr(med)};text-align:right;">{_pct(med)}</td>'
            f'<td style="padding:7px 10px;color:#8b949e;text-align:right;">'
            f'{"—" if win is None else f"{win:.0f}%"}</td>'
            f'</tr>'
        )
    return f'''
    <div class="phase-timeline-wrap" style="max-width:100%;overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;background:rgba(255,255,255,0.02);border-radius:8px;overflow:hidden;font-size:13px;">
      <tr style="background:rgba(255,255,255,0.05);">
        <th style="padding:8px 10px;text-align:left;color:#8b949e;font-size:12px;">阶段</th>
        <th style="padding:8px 10px;text-align:left;color:#8b949e;font-size:12px;">区间</th>
        <th style="padding:8px 10px;text-align:right;color:#8b949e;font-size:12px;">上证</th>
        <th style="padding:8px 10px;text-align:right;color:#8b949e;font-size:12px;">个股中位</th>
        <th style="padding:8px 10px;text-align:right;color:#8b949e;font-size:12px;">上涨占比</th>
      </tr>
      {rows}
    </table>
    </div>'''


def _rank_cards(res):
    """每阶段领涨 TOP8 横向卡片 (看清各阶段是不是同一批人)。"""
    cards = ''
    for p in res['phase_names']:
        if p == '底部至今' or p not in res['ranks']:
            continue
        items = ''
        for r in res['ranks'][p][:8]:
            v = r.get(p)
            vol = r.get('量比')
            vtag = (f'<span style="color:#8b949e;font-size:11px;"> 量比{vol:.2f}</span>'
                    if vol else '')
            items += (f'<div style="display:flex;justify-content:space-between;gap:8px;padding:3px 0;">'
                      f'<span style="color:#e6edf3;font-size:12px;">{r["板块"]}{vtag}</span>'
                      f'<span style="color:{_clr(v)};font-size:12px;font-weight:bold;">{_pct(v,1)}</span>'
                      f'</div>')
        cards += (
            f'<div style="flex:1;min-width:190px;background:rgba(255,255,255,0.03);'
            f'border:1px solid rgba(48,54,61,0.8);border-radius:8px;padding:10px 12px;">'
            f'<div style="color:#58a6ff;font-size:12px;font-weight:bold;margin-bottom:6px;">'
            f'{p} 领涨</div>{items}</div>')
    return (f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin:12px 0;">{cards}</div>'
            if cards else '')


def _quadrant_html(res):
    """四象限卡片: 每组列板块名 + 下跌段/底部至今。"""
    qs = res['quadrants']
    if not qs:
        return ''
    blocks = ''
    for name, cond, desc, clr in QUADRANTS:
        d = qs.get(name)
        if d is None or d.empty:
            continue
        rows = ''
        for _, r in d.head(10).iterrows():
            rows += (f'<div style="display:flex;justify-content:space-between;gap:6px;padding:2px 0;">'
                     f'<span style="color:#e6edf3;font-size:12px;">{r["板块"]}</span>'
                     f'<span style="font-size:11px;white-space:nowrap;">'
                     f'<span style="color:{_clr(r["下跌段"])};">{r["下跌段"]:+.1f}</span>'
                     f'<span style="color:#8b949e;"> → </span>'
                     f'<span style="color:{_clr(r["底部至今"])};font-weight:bold;">{r["底部至今"]:+.1f}%</span>'
                     f'</span></div>')
        more = (f'<div style="color:#8b949e;font-size:11px;margin-top:4px;">…共 {len(d)} 个</div>'
                if len(d) > 10 else '')
        blocks += (
            f'<div style="flex:1;min-width:230px;background:rgba(255,255,255,0.03);'
            f'border-left:3px solid {clr};border-radius:6px;padding:10px 12px;">'
            f'<div style="color:{clr};font-size:13px;font-weight:bold;">{name} '
            f'<span style="color:#8b949e;font-size:11px;font-weight:normal;">({cond})</span></div>'
            f'<div style="color:#8b949e;font-size:11px;margin:3px 0 7px;">{desc}</div>'
            f'{rows}{more}</div>')
    return f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin:12px 0;">{blocks}</div>'


def _verdict(res):
    """一句话结论: 反弹性质 (超跌修复 vs 真主线) + 新高名单 + 背离提示。"""
    corr = res['corr']
    if corr is None:
        nature = '样本不足, 暂不判定反弹性质'
    elif corr <= -0.30:
        nature = (f'下跌段跌幅 vs 反弹涨幅 相关系数 <b>{corr}</b> (显著负相关) —— '
                  f'<b style="color:#d29922;">超跌修复主导</b>, 跌得狠的弹得猛, 这一段的领涨多数不是新逻辑')
    elif corr >= 0.30:
        nature = (f'下跌段跌幅 vs 反弹涨幅 相关系数 <b>{corr}</b> (正相关) —— '
                  f'<b style="color:#3fb950;">强者延续主导</b>, 抗跌的继续领涨, 主线清晰')
    else:
        nature = (f'下跌段跌幅 vs 反弹涨幅 相关系数 <b>{corr}</b> (弱相关) —— '
                  f'超跌修复与独立主线<b>并行</b>, 需按板块分开看, 不能一刀切')

    nh = res.get('new_high') or []
    if nh:
        names = '、'.join(f'{r["板块"]} {r["距顶"]:+.1f}%' for r in nh[:6])
        hi = (f'<div style="margin-top:6px;">🏆 <b>已收复/超越下跌段起点</b>'
              f'(区分"真新高"与"只是修复"): {names}'
              f'{f" 等 {len(nh)} 个" if len(nh) > 6 else ""}</div>')
    else:
        hi = ('<div style="margin-top:6px;color:#8b949e;">🏆 暂无板块收复下跌段起点 —— '
              '全场仍在修复途中, 反弹级别有限</div>')

    # 背离: 指数震荡/下跌但个股中位上涨
    br, det = res['breadth'], res['det']
    div = ''
    for p in ('震荡段', '最新日'):
        ir, b = res['index_ret'].get(p), br.get(p)
        if ir is not None and b and ir < 0 < b['median']:
            div = (f'<div style="margin-top:6px;color:#d29922;">⚠️ <b>{p}指数与个股背离</b>: '
                   f'上证 {ir:+.2f}% 但个股中位 {b["median"]:+.2f}%, '
                   f'{b["win"]:.0f}% 的股票在涨 —— 指数被权重拖住, 底下是普涨的结构性行情, '
                   f'"震荡"两个字最容易骗人的地方</div>')
            break
    return f'<div style="color:#e6edf3;font-size:13px;line-height:1.75;">{nature}{hi}{div}</div>'


def render_phase_resonance_html(res):
    """渲染完整 section。res 为 None 时返回空串 (调用方无需判空)。"""
    if not res:
        return ''
    det = res['det']
    top, bot, latest = det['top'], det['bottom'], det['latest']
    clr = '#58a6ff'

    head = (
        f'<div style="display:flex;gap:18px;flex-wrap:wrap;align-items:baseline;margin-bottom:10px;">'
        f'<div><span style="color:#8b949e;font-size:12px;">阶段起点(顶)</span> '
        f'<b style="color:#e6edf3;">{top["date"]} {top["close"]:.0f}</b></div>'
        f'<div><span style="color:#8b949e;font-size:12px;">最低收盘(底)</span> '
        f'<b style="color:#3fb950;">{bot["date"]} {bot["close"]:.0f}</b>'
        f'<span style="color:#8b949e;font-size:11px;"> (盘中最低 {bot["low"]:.0f})</span></div>'
        f'<div><span style="color:#8b949e;font-size:12px;">最大回撤</span> '
        f'<b style="color:#3fb950;">{det["drawdown"]:.2f}%</b></div>'
        f'<div><span style="color:#8b949e;font-size:12px;">最新</span> '
        f'<b style="color:#e6edf3;">{latest["date"]} {latest["close"]:.0f}</b></div>'
        f'</div>'
        f'<div style="color:#e6edf3;font-size:13px;margin-bottom:12px;">'
        f'<b>见底后形态:</b> <span style="color:{clr};font-weight:bold;">{det["shape"]}</span></div>'
    )

    return f'''
    <div style="background:rgba(0,0,0,0.5);border:2px solid {clr};border-radius:12px;
                padding:20px;margin-bottom:30px;box-shadow:0 0 15px {clr}40;">
      <div style="color:#8b949e;font-size:13px;font-weight:bold;text-transform:uppercase;margin-bottom:8px;">
        🔀 指数阶段 × 板块共振 (Phase Resonance) · 阶段自动识别
      </div>
      {head}
      {_turning_summary_html(res)}
      {_micro_cycle_html(res)}
      {_phase_timeline(res)}
      <div style="color:#8b949e;font-size:12px;font-weight:bold;margin:14px 0 2px;">
        📊 各阶段领涨 —— 横向对比就能看出是不是同一批人接力
      </div>
      {_rank_cards(res)}
      <div style="color:#8b949e;font-size:12px;font-weight:bold;margin:14px 0 2px;">
        🎯 四象限 (下跌段抗跌性 × 底部至今强度) —— 格式: 下跌段 → 底部至今
      </div>
      {_quadrant_html(res)}
      {res.get('reps_html', '')}
      <div style="background:rgba(255,255,255,0.03);border-radius:8px;padding:12px;margin-top:12px;">
        <div style="color:#8b949e;font-size:12px;font-weight:bold;margin-bottom:6px;">🧭 结论</div>
        {_verdict(res)}
      </div>
    </div>
    '''


if __name__ == '__main__':
    r = build_phase_resonance()
    if not r:
        print('无有效阶段结构')
    else:
        d = r['det']
        print(f"顶 {d['top']['date']} {d['top']['close']:.0f} → "
              f"底 {d['bottom']['date']} {d['bottom']['close']:.0f} "
              f"({d['drawdown']:.2f}%) → 最新 {d['latest']['date']} {d['latest']['close']:.0f}")
        print(f"形态: {d['shape']}")
        print(f"阶段: {d['phases']}")
        print(f"指数各段: {r['index_ret']}")
        print(f"市场宽度: {r['breadth']}")
        print(f"相关系数: {r['corr']}")
        for k, v in r['quadrants'].items():
            print(f"  [{k}] n={len(v)}: {', '.join(v['板块'].head(8))}")
        out = os.path.join(os.path.dirname(DATA_DIR), 'output', '_phase_resonance_preview.html')
        with open(out, 'w', encoding='utf-8') as f:
            f.write('<body style="background:#0d1117;font-family:sans-serif;padding:20px;">'
                    + render_phase_resonance_html(r) + '</body>')
        print(f'预览已存 {out}')
