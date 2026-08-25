# -*- coding: utf-8 -*-
"""长期趋势层 (独立模块, 每日自动直出)。

回答的问题: 指数当下处在 上升 / 震荡 / 下跌 哪一档长期趋势, 以及这一档该怎么
读小阶段 —— 同一个"回撤段", 在上升档里是上升中继, 在下跌档里是反弹减仓。

⚠️ 为什么要单独一层 (不是重复 phase_resonance):
  phase_resonance 的"大段"判据只有一个变量 amp = box_hi/box_lo - 1, 语义是**振幅**
  不是**方向**: 单边上涨 5% 会被标成"箱体震荡", 反复来回但振幅 7% 会被标成"趋势段"。
  且它要求窗口内先有 >= 4% 的有效下跌段, 识别不到就 return None —— 实测近 1 年
  抽样 50% 的交易日返回 None (近 10 年 31%), 那些日子整块降级。均线类判据永远有
  输出, 天然兜底。两层不重叠: 本层管方向与仓位上限, phase_resonance 管区间与破位线。

⚠️ 定位: **单侧风险闸门**, 不是方向预测器。
  实测 (上证 8600 根日线): 上升 vs 震荡 在指数远期收益上分不开, 2015 年后甚至反号
  (上升 T+20 -0.26% / 震荡 +0.45%)。真正稳定有信息的只有"下跌"这一档 —— 回撤段落在
  下跌档时 T+3 均值 2005+ -0.83% (n=31) / 2015+ -1.57% (n=16), 而非下跌档 +0.01%
  (n=119)。所以本模块只在下跌档收紧动作, 上升/震荡 一律交回情绪层定节奏。

参数由 8600 根日线扫描 48 组选出 (见下方常数注释), 频率表每次运行实时重算, 不硬编码。

用法 (主报告已接入):
    from trend_regime import build_trend_regime, render_trend_regime_html
    res = build_trend_regime(sub_phase=phase_res['det'].get('sub_phase'))
    html = render_trend_regime_html(res)
"""
from __future__ import annotations

import statistics as st
from html import escape

import pandas as pd

from time_utils import filter_completed_rows

# ── 判据常数 ────────────────────────────────────────────────────────────
# 选型标准 (48 组扫描, 21 组通过, 本组在通过者中样本最大且邻域全部同向):
#   ① 下跌档里"回撤段"的 T+3 在 2005+/2015+ 都 < -0.5%  ② 下跌档占比 15~35%
#   ③ 段平均持续 >= 20 天。本组实测: 占比 24.8%, 段均 52 天, 闸门样本 n=31/16。
TREND_MA = 120          # 长期均线周期 (交易日)
TREND_SLOPE_WIN = 20    # 均线斜率窗口: MA 今日 vs N 日前
TREND_SLOPE_TH = 1.0    # 斜率阈值 (%): 超过算上行, 低于负值算下行, 之间算走平
TREND_CONFIRM = 3       # 新标签连续这么多根才切换 (裸判据 17% 的段只活 1 天, 加确认后清零)
FREQ_START = '2005-01-01'   # 频率表样本起点 (更早的 A 股结构与今天不可比)
# 频率表用的"小趋势"事件口径 (与 phase_resonance 的小阶段同义: 阶段高之后的回撤)
PB_HIGH_WIN = 20        # 先创 N 日新高收盘
PB_BARS = 3             # 新高之后第 N 根
PB_LO, PB_HI = -3.5, -1.0   # 该根距新高的回撤落在此区间 (%) 才算"回撤段"事件

LABELS = ('上升', '震荡', '下跌')
_IDX_MEMO: dict = {}


def fetch_index_full(symbol: str = 'sh000001') -> list:
    """拉指数全历史日线 (频率表需要长样本, 不能只取 90 根)。进程内记忆化。"""
    if symbol in _IDX_MEMO:
        return _IDX_MEMO[symbol]
    import akshare as ak
    df = ak.stock_zh_index_daily(symbol=symbol)
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
    # 与全项目 REPORT_DATE 口径一致: 不把生成日之后的行情带进报告
    df = filter_completed_rows(df, 'date')
    rows = df[['date', 'open', 'high', 'low', 'close', 'volume']].to_dict('records')
    _IDX_MEMO[symbol] = rows
    return rows


def classify_series(idx: list) -> tuple:
    """返回 (raw 标签序列, 确认后标签序列, 起始下标)。纯机械, 无手工日期。"""
    n = len(idx)
    c = [float(r['close']) for r in idx]
    cum = [0.0]
    for x in c:
        cum.append(cum[-1] + x)

    def ma(i, k):
        return (cum[i + 1] - cum[i + 1 - k]) / k

    s0 = TREND_MA + TREND_SLOPE_WIN
    if n <= s0:
        return [None] * n, [None] * n, n
    raw = [None] * n
    for i in range(s0, n):
        m = ma(i, TREND_MA)
        slope = (m / ma(i - TREND_SLOPE_WIN, TREND_MA) - 1) * 100
        if c[i] > m and slope > TREND_SLOPE_TH:
            raw[i] = '上升'
        elif c[i] < m and slope < -TREND_SLOPE_TH:
            raw[i] = '下跌'
        else:
            raw[i] = '震荡'
    conf = [None] * n
    cur = raw[s0]
    prev = raw[s0]
    run = 0
    for i in range(s0, n):
        if raw[i] == prev:
            run += 1
        else:
            prev = raw[i]
            run = 1
        if run >= TREND_CONFIRM:
            cur = raw[i]
        conf[i] = cur
    return raw, conf, s0


def pullback_frequency(idx: list, conf: list, s0: int) -> dict:
    """实时重算"回撤段 x 长期趋势"的历史结局 (每次运行自我校准, 不硬编码常数)。

    事件: 创 PB_HIGH_WIN 日新高收盘, 之后第 PB_BARS 根距该高回撤落在 [PB_LO, PB_HI]。
    返回 {档位: {'n','t3_up','t3_mean','t5_mean'}}。样本薄时照实给 n, 由渲染层标注。
    """
    n = len(idx)
    c = [float(r['close']) for r in idx]
    d = [str(r['date']) for r in idx]
    buckets: dict = {}
    for h in range(max(s0, PB_HIGH_WIN), n - 1):
        if c[h] != max(c[h - PB_HIGH_WIN + 1:h + 1]):
            continue
        t = h + PB_BARS
        if t >= n or d[t] < FREQ_START or conf[t] is None:
            continue
        ret = (c[t] / c[h] - 1) * 100
        if PB_LO <= ret <= PB_HI:
            buckets.setdefault(conf[t], []).append(t)
    out = {}
    for lab, ix in buckets.items():
        def fwd(k):
            v = [(c[i + k] / c[i] - 1) * 100 for i in ix if i + k < n]
            return v
        v3, v5 = fwd(3), fwd(5)
        out[lab] = {
            'n': len(ix),
            't3_up': round(sum(1 for x in v3 if x > 0) / len(v3) * 100, 1) if v3 else None,
            't3_mean': round(st.mean(v3), 2) if v3 else None,
            't5_mean': round(st.mean(v5), 2) if v5 else None,
        }
    return out


def regime_action(label: str, sub_name: str = None, pos_pct: float = None,
                  ma_val: float = None) -> tuple:
    """(仓位档位, 怎么操作)。每个读数后面必须跟一条动作 (全项目一贯要求)。

    只在下跌档收紧: 上升/震荡 的节奏一律交回情绪层 (market_regime/market_stance),
    因为实测那两档在指数远期收益上分不开, 硬给方向等于凭空加噪。
    """
    ma_s = f'{ma_val:.0f}' if isinstance(ma_val, (int, float)) else f'MA{TREND_MA}'
    if label == '下跌':
        cap = '防守 (上限降一档)'
        act = (f'长期趋势仍在下跌档 (收盘在 {ma_s} 下方), 反弹按"减仓窗口"处理而不是上升中继: '
               f'仓位上限比情绪层给的档位降一档, 反弹到压力位先减不加。'
               f'收盘重新站上 {ma_s} 且连续 {TREND_CONFIRM} 根不掉才谈转档。')
        if sub_name in ('回撤段', '高位盘整'):
            act += f' 当前小阶段是"{sub_name}", 正是这一档里历史结局最差的组合, 别按回踩加仓做。'
        elif sub_name == '创阶段新高':
            act += ' 当前小阶段刚创阶段新高, 在下跌档里先按"反弹到压力位"对待, 不追高不打板。'
    elif label == '上升':
        cap = '进攻 (不额外压上限)'
        act = (f'长期趋势在上升档 (收盘在 {ma_s} 上方且均线上行), 本层不压仓位上限, '
               f'进攻节奏交回情绪层 (A/D 与连板梯队) 决定。'
               f'跌破 {ma_s} 并连续 {TREND_CONFIRM} 根不收回, 才把上限降一档。')
        if sub_name == '回撤段':
            act += ' 当前小阶段是"回撤段": 上升档里的回撤按上升中继处理, 守住破位线不减。'
    else:
        cap = '中性 (按情绪层节奏)'
        act = (f'长期趋势走平 (震荡档, {ma_s} 附近无方向), 本层不给方向, 只给一条纪律: '
               f'不做趋势加仓, 只做区间内的低吸高抛, 仓位上限按情绪层给的档位执行。'
               f'方向以收盘连续 {TREND_CONFIRM} 根站上/跌破 {ma_s} 为准。')
        if sub_name in ('回撤段', '高位盘整'):
            act += f' 当前小阶段"{sub_name}"在震荡档里历史上接近零期望, 别赌方向, 等区间边界。'
    if isinstance(pos_pct, (int, float)):
        act += f' 当前收盘距 {ma_s} {pos_pct:+.2f}%。'
    return cap, act


def build_trend_regime(idx: list = None, sub_phase: dict = None,
                       sentiment_regime: dict = None) -> dict:
    """长期趋势层主入口。数据不足或拉取失败返回 None (渲染层降级为不显示)。

    sub_phase: phase_resonance 的小阶段 dict (可选), 用来给出"长期 x 小趋势"的组合处置。
    sentiment_regime: market_regime.classify_market_regime 的结果 (可选), 用来在
                      价格趋势与情绪档位打架时显式给优先级, 而不是让两条结论并列出现。
    """
    try:
        # 注意 is not None: 显式传空序列表示"就这些数据", 必须降级返回 None,
        # 不能悄悄回落去拉网络 (测试注入与离线运行都依赖这个语义)。
        rows = idx if idx is not None else fetch_index_full()
    except Exception:
        return None
    if not rows or len(rows) <= TREND_MA + TREND_SLOPE_WIN + 5:
        return None
    raw, conf, s0 = classify_series(rows)
    label = conf[-1]
    if not label:
        return None
    n = len(rows)
    c = [float(r['close']) for r in rows]
    cum = [0.0]
    for x in c:
        cum.append(cum[-1] + x)
    ma_val = (cum[n] - cum[n - TREND_MA]) / TREND_MA
    ma_prev = (cum[n - TREND_SLOPE_WIN] - cum[n - TREND_SLOPE_WIN - TREND_MA]) / TREND_MA
    slope = (ma_val / ma_prev - 1) * 100
    pos_pct = (c[-1] / ma_val - 1) * 100

    # 本档已持续多少个交易日 (确认后的连续段长度)
    run_days = 1
    for i in range(n - 2, s0 - 1, -1):
        if conf[i] == label:
            run_days += 1
        else:
            break
    # 未确认的反向信号: raw 已经翻了但还没满 TREND_CONFIRM 根
    pending, pending_days = None, 0
    if raw[-1] != label:
        pending = raw[-1]
        for i in range(n - 1, s0 - 1, -1):
            if raw[i] == pending:
                pending_days += 1
            else:
                break

    # 震荡档是残差桶, 本身就是缓冲带; 但贴着阈值时要明说"离切档一步之遥",
    # 否则读者会把"震荡"读成"安全", 而下一根就可能确认成下跌档。
    near = None
    if label == '震荡':
        if c[-1] < ma_val and slope <= -TREND_SLOPE_TH * 0.8:
            near = '下跌'
        elif c[-1] > ma_val and slope >= TREND_SLOPE_TH * 0.8:
            near = '上升'

    sub = sub_phase or {}
    sub_name = sub.get('name')
    cap, action = regime_action(label, sub_name, pos_pct, ma_val)
    freq = pullback_frequency(rows, conf, s0)

    # 与情绪层冲突消解: 趋势定上限, 情绪定节奏 —— 优先级写死, 不让两条结论并列打脸
    conflict = None
    # market_regime 用 title, market_stance 用 stance, 两个都接
    _sr = sentiment_regime or {}
    s_title = _sr.get('title') or _sr.get('stance') or ''
    if label == '下跌' and ('普涨' in s_title or '强' in s_title):
        conflict = (f'情绪层给的是"{s_title}", 价格长期趋势却在下跌档 —— 不矛盾, 是两个尺度。'
                    f'优先级: 趋势定仓位上限 (降一档), 情绪定进攻节奏 (可以做, 但做短)。')
    elif label == '上升' and ('弱' in s_title or '防守' in s_title):
        conflict = (f'情绪层给的是"{s_title}", 价格长期趋势仍在上升档 —— 按"趋势不压上限, '
                    f'情绪压节奏"处理: 不减仓位上限, 但当日不进攻。')

    if near:
        action += (f' 注意: 斜率 {slope:+.2f}% 已贴近 {TREND_SLOPE_TH:.1f}% 阈值, '
                   f'离切到{near}档只差一步 —— 按{near}档预案准备, 别等确认了再动。')
    if pending and pending_days:
        stage = f'{label}档 (第 {run_days} 日, {pending}档信号已连续 {pending_days} 根待确认)'
    else:
        stage = (f'{label}档 (已持续 {run_days} 个交易日'
                 + (f', 贴近{near}档阈值)' if near else ')'))
    headline = f'长期趋势 {label}档 · {cap}' + (f' × 小阶段 {sub_name}' if sub_name else '')
    return {
        'as_of': str(rows[-1]['date']),
        'label': label, 'raw': raw[-1],
        'pending': pending, 'pending_days': pending_days,
        'run_days': run_days,
        'close': round(c[-1], 2), 'ma': round(ma_val, 2),
        'ma_period': TREND_MA, 'slope': round(slope, 2), 'pos_pct': round(pos_pct, 2),
        'cap': cap, 'action': action, 'freq': freq,
        'sub_name': sub_name, 'conflict': conflict, 'near': near,
        'params': {'ma': TREND_MA, 'slope_win': TREND_SLOPE_WIN,
                   'slope_th': TREND_SLOPE_TH, 'confirm': TREND_CONFIRM},
        'status': f'{label}档', 'signal': cap, 'stage': stage, 'headline': headline,
    }


_TR_CSS = """
<style>
.trend-regime{margin:18px 0;padding:14px 16px;border:1px solid #2a2d34;
  border-radius:10px;background:#16181d;color:#d7dae0;font-size:13px;line-height:1.6;}
.trend-regime .tr-title{font-size:15px;font-weight:700;margin-bottom:6px;}
.trend-regime .tr-headline{font-size:13px;color:#ffd479;background:#23262d;
  padding:8px 10px;border-radius:6px;margin-bottom:12px;overflow-wrap:anywhere;}
.trend-regime .tr-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;}
.trend-regime .tr-col{min-width:0;overflow-wrap:anywhere;
  border-left:2px solid #2a2d34;padding-left:12px;}
.trend-regime .tr-h{font-weight:700;color:#8ab4f8;margin-bottom:6px;}
.trend-regime .tr-row{margin:4px 0;overflow-wrap:anywhere;}
.trend-regime .tr-sub{font-size:12px;color:#9aa0a6;margin:2px 0;overflow-wrap:anywhere;}
.trend-regime .tr-up{color:#e04b4b;font-weight:700;}
.trend-regime .tr-dn{color:#3fb950;font-weight:700;}
.trend-regime .tr-flat{color:#ffb454;font-weight:700;}
.trend-regime .tr-warn{color:#ffb454;margin:4px 0;overflow-wrap:anywhere;}
.trend-regime .tr-act{font-size:12px;color:#7fd1b9;margin-top:8px;
  border-top:1px dashed #2a2d34;padding-top:6px;overflow-wrap:anywhere;}
.trend-regime .tr-note{color:#767b83;font-size:11px;overflow-wrap:anywhere;}
.trend-regime .tr-tbl{border-collapse:collapse;margin:6px 0;width:100%;font-size:12px;}
.trend-regime .tr-tbl th,.trend-regime .tr-tbl td{
  border:1px solid #2a2d34;padding:3px 6px;text-align:center;}
.trend-regime .tr-tbl tr.tr-now{background:#3a2a1a;}
@media (max-width:760px){.trend-regime .tr-grid{grid-template-columns:1fr;}
  .trend-regime .tr-col{border-left:none;border-top:1px solid #2a2d34;
  padding-left:0;padding-top:10px;}}
</style>
"""


def _e(v) -> str:
    return escape('' if v is None else str(v))


def _cls(label: str) -> str:
    return {'上升': 'tr-up', '下跌': 'tr-dn'}.get(label, 'tr-flat')


def _state_col(res: dict) -> str:
    lab = res.get('label')
    rows = [f"<div class='tr-h'>① 当前档位</div>",
            f"<div class='tr-row'>长期趋势 <span class='{_cls(lab)}'>{_e(lab)}档</span>"
            f" · 已持续 {_e(res.get('run_days'))} 个交易日</div>",
            f"<div class='tr-sub'>收盘 {_e(res.get('close'))} / MA{_e(res.get('ma_period'))} "
            f"{_e(res.get('ma'))} ({res.get('pos_pct', 0):+.2f}%)</div>",
            f"<div class='tr-sub'>MA{_e(res.get('ma_period'))} 的 "
            f"{TREND_SLOPE_WIN} 日斜率 {res.get('slope', 0):+.2f}% "
            f"(阈值 ±{TREND_SLOPE_TH:.1f}%)</div>"]
    if res.get('near'):
        rows.append(f"<div class='tr-warn'>⚠️ 贴近{_e(res['near'])}档阈值, 差一步就切档</div>")
    if res.get('pending'):
        rows.append(f"<div class='tr-warn'>⚠️ {_e(res['pending'])}档信号已连续 "
                    f"{_e(res.get('pending_days'))} 根, 满 {TREND_CONFIRM} 根才确认切换</div>")
    if res.get('sub_name'):
        rows.append(f"<div class='tr-sub'>当前小阶段: {_e(res['sub_name'])} "
                    f"(来自阶段共振, 管当天动作; 本层只管方向与仓位上限)</div>")
    return "<div class='tr-col'>" + ''.join(rows) + '</div>'


def _freq_col(res: dict) -> str:
    freq = res.get('freq') or {}
    if not freq:
        return ''
    now = res.get('label')
    body = []
    for lab in LABELS:
        f = freq.get(lab)
        if not f:
            continue
        cls = " class='tr-now'" if lab == now else ''
        t3u = '—' if f.get('t3_up') is None else f"{f['t3_up']:.0f}%"
        t3m = '—' if f.get('t3_mean') is None else f"{f['t3_mean']:+.2f}%"
        t5m = '—' if f.get('t5_mean') is None else f"{f['t5_mean']:+.2f}%"
        body.append(f"<tr{cls}><td>{_e(lab)}档</td><td>{f.get('n')}</td>"
                    f"<td>{t3u}</td><td>{t3m}</td><td>{t5m}</td></tr>")
    if not body:
        return ''
    thin = min((f.get('n') or 0) for f in freq.values() if f) < 20
    note = (f"口径: 创 {PB_HIGH_WIN} 日新高收盘后第 {PB_BARS} 根、距高回撤 "
            f"{abs(PB_HI):.1f}~{abs(PB_LO):.1f}% 的历史样本 ({FREQ_START} 起, 每次运行实时重算)。"
            + ('部分档位样本 < 20, 只作历史频率提示, 不是铁律。' if thin else
               '样本仍偏薄, 只作历史频率提示, 不是铁律。'))
    return ("<div class='tr-col'><div class='tr-h'>② 同一个回撤段, 分档后的历史结局</div>"
            "<table class='tr-tbl'><tr><th>长期趋势</th><th>样本</th><th>T+3 上涨</th>"
            "<th>T+3 均值</th><th>T+5 均值</th></tr>" + ''.join(body) + '</table>'
            f"<div class='tr-note'>{_e(note)}</div></div>")


def _act_col(res: dict) -> str:
    rows = [f"<div class='tr-h'>③ 怎么操作</div>",
            f"<div class='tr-row'>仓位档位: {_e(res.get('cap'))}</div>",
            f"<div class='tr-act'>{_e(res.get('action'))}</div>"]
    if res.get('conflict'):
        rows.append(f"<div class='tr-warn'>层级冲突消解: {_e(res['conflict'])}</div>")
    p = res.get('params') or {}
    rows.append(f"<div class='tr-note'>判据: 收盘 vs MA{p.get('ma')} + MA 的 "
                f"{p.get('slope_win')} 日斜率 ±{p.get('slope_th')}%, 连续 {p.get('confirm')} "
                f"根确认才切档 (参数由 8600 根日线扫描 48 组选出; 加确认是因为裸判据 17% 的"
                f"标签只活 1 天)。本层是单侧风险闸门: 只在下跌档收紧, 上升/震荡 的节奏交回情绪层。</div>")
    return "<div class='tr-col'>" + ''.join(rows) + '</div>'


def render_trend_regime_html(res) -> str:
    """把 build_trend_regime 的 dict 渲染成 HTML section。空/异常 → 返回空串。"""
    if not res or not isinstance(res, dict) or not res.get('label'):
        return ''
    try:
        cols = [c for c in (_state_col(res), _freq_col(res), _act_col(res)) if c]
        if not cols:
            return ''
        return (_TR_CSS + "<div class='trend-regime'>"
                f"<div class='tr-title'>🧭 长期趋势层 · 上升/震荡/下跌 × 小趋势 "
                f"<span class=tr-note>({_e(res.get('as_of'))})</span></div>"
                f"<div class='tr-headline'>{_e(res.get('headline'))}</div>"
                f"<div class='tr-grid'>{''.join(cols)}</div></div>")
    except Exception:
        return ''
