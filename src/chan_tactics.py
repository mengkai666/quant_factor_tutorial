"""缠论战法库 (Event 组合) + 板块指数因果回测。

战法 = chan_signals 的 Event + 操作说明 (逻辑/入场/止损/仓位)。回测只回答一个问题:
**这个形态在本项目的板块指数上, 历史上有没有比"随便哪天买"更好?**

回测口径 (照搬项目里吃过亏的几条规矩, 见 memory)
--------------------------------------------------
* **不穿越**: ChanAnalyzer 逐根增量喂, 第 i 天的信号只用前 i 根K线; MACD 也是因果 EMA。
* **次日收盘建仓**: 信号在 T 日收盘才确认, 买入价取 T+1 收盘 (不是 T 日收盘 —
  limit-up-close-not-tradable 那次符号都反了)。持有到 T+1+h 收盘。
* **基准** = 同一批板块、同一批日期, 每天都按 T+1 收盘买入的平均收益。超额 = 战法均值 - 基准。
* **去重**: 同一板块触发后冷却 COOLDOWN 天 (形态是"状态", 会连续多日命中, 不去重等于一笔算 5 遍)。
* **独立性**: 板块之间高度同涨同跌, 30 个样本可能只来自 3 个交易日。所以除了 n 还给
  独立板块数 / 独立日期数 / 前 3 板块占比 (event-count-not-independent-samples)。
* **小样本**: n < MIN_SAMPLE 只叫"历史频率提示", 不叫规律。

板块指数只有 151 天, 周线 ~30 根 / 3~5 笔, 周线条件在回测里不启用 (样本会被筛到个位数);
报告里周线只作为"大级别方向"的展示与过滤建议。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from statistics import mean, median
from typing import Sequence

from chan_engine import ChanAnalyzer, RawBar, bars_from_records
from chan_signals import ChanContext, Event, Signal, compute_signals

HORIZONS = (5, 10)
COOLDOWN = 5
WARMUP = 40          # 前 40 根K线笔/中枢还没长出来, 不计信号
MIN_SAMPLE = 20


@dataclass(frozen=True)
class Tactic:
    """一个战法 = 事件 + 操作说明。side: long=做多类 (超额越大越好), exit=离场类 (超额越负越好)。"""
    key: str
    event: Event
    side: str
    thesis: str
    entry: str
    stop: str
    position: str


TACTICS: tuple[Tactic, ...] = (
    Tactic(
        key='third_buy',
        event=Event('三买主升', '开多', signals_any=(
            '日线_D1_中枢三买卖_三买_任意_任意',
            '日线_D1_三买辅助_三买_任意_任意',
        )),
        side='long',
        thesis='离开中枢后回抽不回 ZG = 中枢被确认在下方, 走势从盘整升级为上涨; 缠论里最适合追的买点。',
        entry='回抽笔底分型确认后次日; 已确认三买可直接参与, 不必等再创新高。',
        stop='有效跌回 ZG (中枢上沿) 即三买失败, 无条件离场。',
        position='标准仓; 离开笔力度弱于进入笔 (信号 v3) 时减半 —— 弱离开常是假突破。',
    ),
    Tactic(
        key='third_buy_pending',
        event=Event('三买候选', '观察', signals_all=('日线_D1_三买候选_回抽守ZG_任意_任意',)),
        side='long',
        thesis='已离开中枢、回抽进行中且尚未碰 ZG; 是"三买还没成立"的提前布局点。',
        entry='回抽缩量且出底分型时轻仓试; 贴近 ZG 时盈亏比最好。',
        stop='收盘破 ZG 即作废 (信号变为"已破ZG")。',
        position='试探仓 (标准仓的 1/3), 三买确认后再加。',
    ),
    Tactic(
        key='zs_up_pullback',
        event=Event('中枢上移回踩', '开多', signals_all=(
            '日线_D1_中枢走向_上移_任意_任意',
            '日线_D1_中枢位置_中枢上方_任意_任意',
            '日线_D1_表里关系_向下_底分_任意',
        )),
        side='long',
        thesis='中枢逐级上移 = 上涨趋势; 回调笔在中枢上方出底分型, 是趋势中的顺势低吸。',
        entry='回调笔出底分型 (表里关系=向下/底分) 次日。',
        stop='跌破最近中枢 ZG; 再破前一中枢 ZG 说明趋势结束。',
        position='标准仓。',
    ),
    Tactic(
        key='first_buy',
        event=Event('一买底背驰', '开多', signals_any=(
            '日线_D1B_BUY1_一买_任意_任意',
            '日线_D1五笔_形态_aAb式底背驰_任意_任意',
            '日线_D1五笔_形态_类趋势底背驰_任意_任意',
            '日线_D1七笔_形态_aAbcd式底背驰_任意_任意',
            '日线_D1七笔_形态_abcAd式底背驰_任意_任意',
            '日线_D1七笔_形态_aAb式底背驰_任意_任意',
            '日线_D1七笔_形态_类趋势底背驰_任意_任意',
        )),
        side='long',
        thesis='下跌段末笔力度 (价差+量能/长度) 衰竭; 抄的是"下跌结束", 不是"上涨开始"。',
        entry='末笔底分型确认后次日; MACD 面积也背驰 (双背驰) 时可信度更高。',
        stop='创新低即错 (一买的低点就是止损点, 没有商量)。',
        position='小仓 (标准仓的 1/3~1/2) —— 一买成功率天然低于二买/三买, 赢在盈亏比。',
    ),
    Tactic(
        key='second_buy',
        event=Event('二买确认', '开多', signals_all=('日线_D1_二买卖_二买_任意_任意',)),
        side='long',
        thesis='一买后第一次回抽不破一买低点 = 下跌段确认结束; 比一买胜率高、比三买位置低。',
        entry='回抽笔底分型确认后次日; 强二买 (回撤不足一半) 优先。',
        stop='破一买低点。',
        position='标准仓。',
    ),
    Tactic(
        key='top_divergence',
        event=Event('顶背驰减仓', '平多', signals_any=(
            '日线_D1_MACD背驰_顶背驰_显著_任意',
            '日线_D1B_SELL1_一卖_任意_任意',
            '日线_D1五笔_形态_aAb式顶背驰_任意_任意',
            '日线_D1五笔_形态_类趋势顶背驰_任意_任意',
            '日线_D1七笔_形态_aAbcd式顶背驰_任意_任意',
            '日线_D1七笔_形态_abcAd式顶背驰_任意_任意',
            '日线_D1七笔_形态_aAb式顶背驰_任意_任意',
            '日线_D1七笔_形态_类趋势顶背驰_任意_任意',
        )),
        side='exit',
        thesis='创新高的这一笔力度不如上一笔 = 上涨动能衰竭; 不是做空信号, 是"别再加、开始减"。',
        entry='(离场类) 背驰笔顶分型确认后减仓。',
        stop='重新放量越过背驰笔高点 = 背驰被消化 (强势股常见), 可回补。',
        position='减至半仓以下。',
    ),
    Tactic(
        key='third_sell',
        event=Event('三卖规避', '回避', signals_any=(
            '日线_D1_中枢三买卖_三卖_任意_任意',
            '日线_D1五笔_形态_类三卖_任意_任意',
            '日线_D1七笔_形态_类三卖_任意_任意',
        )),
        side='exit',
        thesis='跌离中枢后反抽不回 ZD = 中枢被确认在上方, 走势升级为下跌; 缠论里最该回避的位置。',
        entry='(离场类) 持仓清掉, 空仓不接。',
        stop='重新站回 ZD 且形成新中枢才解除。',
        position='清仓/不参与。',
    ),
    Tactic(
        key='zs_range',
        event=Event('中枢震荡', '观察', signals_all=('日线_D1_中枢位置_中枢内部_任意_任意',),
                    signals_any=('日线_D1三笔_形态_向上收敛_任意_任意', '日线_D1三笔_形态_向下收敛_任意_任意')),
        side='long',
        thesis='价格在中枢内且三笔收敛 = 无趋势; 中枢震荡里只做"下沿买、上沿卖", 不追突破。',
        entry='靠近 ZD 出底分型时低吸, 靠近 ZG 出顶分型时高抛。',
        stop='有效跌破 ZD (可能演变为三卖)。',
        position='轻仓, 高抛低吸。',
    ),
)

TACTIC_BY_KEY = {t.key: t for t in TACTICS}


def match_tactics(sigs: dict[str, Signal]) -> list[Tactic]:
    return [t for t in TACTICS if t.event.is_match(sigs)]


# ============================================================ 回测

def _iter_daily_signals(bars: Sequence[RawBar], symbol: str):
    """逐日增量: yield (i, signals)。第 i 天只用 bars[:i+1]。"""
    c = ChanAnalyzer(symbol=symbol)
    for i, bar in enumerate(bars):
        c.update(bar)
        if i < WARMUP:
            continue
        ctx = ChanContext(bars[:i + 1], symbol=symbol, analyzer=c)
        yield i, compute_signals(ctx)


def _fingerprint(sector_hist: dict) -> str:
    h = hashlib.sha1()
    for name in sorted(sector_hist):
        rows = sector_hist[name]
        if rows:
            h.update(f"{name}|{len(rows)}|{rows[-1].get('date')}|{rows[-1].get('close')}".encode())
    # 战法定义变了也要重算
    for t in TACTICS:
        h.update(repr(t.event).encode())
    h.update(f'{HORIZONS}{COOLDOWN}{WARMUP}'.encode())
    return h.hexdigest()[:16]


def backtest_sectors(sector_hist: dict, cache_path: str | None = None) -> dict:
    """在全部板块指数上回测 TACTICS。结果按数据指纹缓存 (数据不变就不重算)。

    返回 {'fingerprint', 'n_sectors', 'date_range', 'baseline': {h: mean}, 'tactics': {key: {...}}}
    """
    fp = _fingerprint(sector_hist)
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding='utf-8') as f:
                cached = json.load(f)
            if cached.get('fingerprint') == fp:
                return cached
        except (OSError, ValueError):
            pass

    events: dict[str, list[dict]] = {t.key: [] for t in TACTICS}
    base: dict[int, list[float]] = {h: [] for h in HORIZONS}
    all_dates: list[str] = []
    for name, rows in sector_hist.items():
        bars = bars_from_records(rows, symbol=name)
        if len(bars) <= WARMUP + 2:
            continue
        closes = [b.close for b in bars]
        last_fire: dict[str, int] = {}
        for i, sigs in _iter_daily_signals(bars, name):
            entry_i = i + 1
            fwd = {}
            for h in HORIZONS:
                if entry_i + h < len(bars):
                    r = closes[entry_i + h] / closes[entry_i] - 1
                    fwd[h] = r
                    base[h].append(r)
            if not fwd:
                continue
            for t in match_tactics(sigs):
                if i - last_fire.get(t.key, -10 ** 9) <= COOLDOWN:
                    continue
                last_fire[t.key] = i
                events[t.key].append({'sector': name, 'date': str(bars[i].dt), 'fwd': fwd})
        all_dates.extend([str(bars[0].dt), str(bars[-1].dt)])

    baseline = {h: (mean(v) if v else 0.0) for h, v in base.items()}
    out_t = {}
    for t in TACTICS:
        evs = events[t.key]
        stat = {'name': t.event.name, 'side': t.side, 'n': len(evs)}
        sectors = [e['sector'] for e in evs]
        stat['distinct_sectors'] = len(set(sectors))
        stat['distinct_dates'] = len({e['date'] for e in evs})
        if sectors:
            from collections import Counter
            top3 = sum(c for _, c in Counter(sectors).most_common(3))
            stat['top3_share'] = round(top3 / len(sectors), 3)
        else:
            stat['top3_share'] = None
        for h in HORIZONS:
            rs = [e['fwd'][h] for e in evs if h in e['fwd']]
            key = f't{h}'
            if rs:
                stat[key] = {
                    'n': len(rs),
                    'mean': round(mean(rs), 5),
                    'median': round(median(rs), 5),
                    'win': round(sum(1 for r in rs if r > 0) / len(rs), 3),
                    'edge': round(mean(rs) - baseline[h], 5),
                }
            else:
                stat[key] = {'n': 0}
        stat['small_sample'] = len(evs) < MIN_SAMPLE
        stat['verdict'] = _verdict(stat, t.side)
        out_t[t.key] = stat

    result = {
        'fingerprint': fp,
        'n_sectors': len(sector_hist),
        'date_range': [min(all_dates), max(all_dates)] if all_dates else [None, None],
        'baseline': {f't{h}': round(v, 5) for h, v in baseline.items()},
        'baseline_n': {f't{h}': len(v) for h, v in base.items()},
        'tactics': out_t,
        'rules': f'T+1 收盘建仓, 持有 {HORIZONS} 日; 同板块冷却 {COOLDOWN} 日; 前 {WARMUP} 根不计',
    }
    if cache_path:
        try:
            tmp = cache_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=1)
            os.replace(tmp, cache_path)
        except OSError:
            pass
    return result


def _verdict(stat: dict, side: str) -> str:
    """一句话结论。只看 T+10 (缠论笔级别的买卖点, 5 日常常还在回抽笔里)。"""
    t10 = stat.get('t10') or {}
    if not t10.get('n'):
        return '无样本'
    edge = t10['edge']
    tag = '历史频率提示' if stat['small_sample'] else '回测'
    if side == 'long':
        if edge > 0.01:
            word = '跑赢基准'
        elif edge < -0.01:
            word = '跑输基准'
        else:
            word = '与基准无差异'
    else:
        if edge < -0.01:
            word = '离场有效 (触发后跑输基准)'
        elif edge > 0.01:
            word = '离场无效 (触发后反而跑赢)'
        else:
            word = '与基准无差异'
    return f'{tag}: {word}'
