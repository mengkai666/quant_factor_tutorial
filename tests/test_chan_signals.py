"""缠论信号层 (src/chan_signals.py): 用"每条腿恰好一笔"的折线构造确定形态, 钉住买卖点判定。"""
from datetime import date, timedelta

import pytest

from chan_engine import DOWN, UP, RawBar
from chan_signals import (ChanContext, Event, Signal, check_first_buy, compute_signals,
                          macd, macd_bc, zs_position, zs_third_bs, zs_trend)


def legs(pivots, step=8, base_vol=100.0, vol_by_leg=None):
    """折线 → K线。每条腿 step 根, 单调推进 (无包含), 拐点处天然成分型。

    vol_by_leg: 每条腿的成交量 (用于一买/一卖的量能背驰)。
    """
    bars, d, i = [], date(2025, 1, 1), 0
    for k in range(len(pivots) - 1):
        a, b = pivots[k], pivots[k + 1]
        vol = (vol_by_leg or {}).get(k, base_vol)
        for j in range(step):
            mid = a + (b - a) * (j + 1) / step
            half = abs(b - a) / step * 0.35 + 0.05
            bars.append(RawBar(symbol='T', dt=(d + timedelta(days=i)).isoformat(), open=mid, close=mid,
                               high=mid + half, low=mid - half, vol=vol, id=i))
            i += 1
    return bars


def ctx_of(bars):
    return ChanContext(bars, symbol='T')


# 中枢 [92, 108] 震荡 4 个来回, 之后向上离开
_RANGE = [100, 90, 110, 92, 108, 91, 109, 93, 107]


def test_macd_is_causal():
    closes = [float(x) for x in range(1, 80)]
    full = macd(closes)[2]
    part = macd(closes[:50])[2]
    assert full[:50] == pytest.approx(part)


def test_third_buy_after_leaving_zs():
    # 离开笔冲到 125, 回抽笔只回到 113 (> ZG=108), 再起一小段让回抽笔的底分型成立
    bars = legs(_RANGE + [125, 113, 118], step=8)
    c = ctx_of(bars)
    s = zs_third_bs(c)
    assert s.v1 == '三买', [str(x) for x in compute_signals(c).values()]
    assert c.bis[-1].direction == DOWN and c.bis[-1].low > c.real_zs[-1].zg


def test_third_sell_after_leaving_zs_down():
    bars = legs([100, 110, 90, 108, 92, 109, 91, 107, 93] + [75, 87, 82], step=8)
    s = zs_third_bs(ctx_of(bars))
    assert s.v1 == '三卖'


def test_pullback_into_zs_is_not_third_buy():
    bars = legs(_RANGE + [125, 104, 110], step=8)      # 回抽跌回 ZG 以下
    assert zs_third_bs(ctx_of(bars)).v1 != '三买'


def test_zs_position_and_trend_up():
    # 两个中枢: [92,108] 之后在 [122,138] 再震荡 => 上移, 现价在上方
    bars = legs(_RANGE + [130, 120, 140, 122, 138, 121, 139, 150, 145], step=8)
    c = ctx_of(bars)
    assert len(c.real_zs) >= 2
    assert zs_trend(c).v1 == '上移'
    assert zs_position(c).v1 in ('中枢上方', '中枢内部')


def test_top_divergence_by_macd_area():
    # 逐级上涨, 最后一笔创新高但幅度明显小于前一同向笔 (MACD 红柱面积缩小)
    # 最后完成的向上笔 150->165 vs 前一向上笔 115->160 (起头 120->100 是引子)
    bars = legs([120, 100, 130, 115, 160, 150, 165, 158], step=10)
    c = ctx_of(bars)
    assert c.bis[-1].direction == UP, [repr(b) for b in c.bis]
    assert c.bis[-1].high > c.bis[-3].high
    s = macd_bc(c)
    assert s.v1 == '顶背驰' and float(s.v3) < 1
    # 反例: 最后一笔比前一同向笔更猛 => 无背驰
    bars = legs([120, 100, 130, 115, 140, 130, 175, 168], step=10)
    assert macd_bc(ctx_of(bars)).v1 == '无背驰'


def test_check_first_buy_on_constructed_bis():
    # 下跌段: 首笔最高, 末笔最低, 末笔价差/量能都弱于前面 => 一买
    # (首条腿 130->150 是引子: 序列第一根K线左边没有分型, 第一笔从 150 的顶分型起算)
    bars = legs([130, 150, 120, 135, 105, 115, 98, 104], step=8,
                vol_by_leg={1: 300, 3: 300, 5: 120})
    c = ctx_of(bars)
    assert len(c.bis) >= 5 and c.bis[-1].direction == DOWN, [repr(b) for b in c.bis]
    assert check_first_buy(c.bis[-5:])
    # 末笔力度比前面更大 (价差 38 > 20, 量能 4 倍) => 不是一买
    bars = legs([130, 150, 130, 140, 120, 128, 90, 100], step=8,
                vol_by_leg={1: 100, 3: 100, 5: 400})
    c = ctx_of(bars)
    assert c.bis[-1].direction == DOWN
    assert not check_first_buy(c.bis[-5:])


def test_compute_signals_covers_all_keys_and_never_raises():
    bars = legs(_RANGE + [125, 113, 118], step=8)
    sigs = compute_signals(ctx_of(bars))
    for key in ('日线_D1_表里关系', '日线_D1三笔_形态', '日线_D1五笔_形态', '日线_D1B_BUY1',
                '日线_D1_中枢三买卖', '日线_D1_MACD背驰', '日线_D1_中枢位置', '日线_D1_中枢走向'):
        assert key in sigs
    # 极短序列不报错, 只出"其他"
    short = compute_signals(ctx_of(legs([10, 12, 11], step=4)))
    assert all(s.v1 in ('其他', '无中枢', '无', '无背驰') or s.key.endswith('分型强弱') for s in short.values())


# ------------------------------------------------------------------ 信号 / 事件

def test_signal_parse_and_wildcard_match():
    s = Signal.parse('日线_D1_中枢三买卖_三买_强_离开强于进入')
    assert s.key == '日线_D1_中枢三买卖' and s.value == '三买_强_离开强于进入'
    assert s.is_match(Signal.parse('日线_D1_中枢三买卖_三买_任意_任意'))
    assert not s.is_match(Signal.parse('日线_D1_中枢三买卖_三卖_任意_任意'))
    with pytest.raises(ValueError):
        Signal.parse('a_b_c')


def test_event_all_any_not():
    sigs = {s.key: s for s in (Signal.parse('日线_D1_中枢走向_上移_任意_任意'),
                               Signal.parse('日线_D1_中枢位置_中枢上方_近_任意'),
                               Signal.parse('日线_D1_MACD背驰_顶背驰_显著_0.40'))}
    ev = Event('x', '开多', signals_all=('日线_D1_中枢走向_上移_任意_任意',),
               signals_any=('日线_D1_中枢位置_中枢上方_任意_任意', '日线_D1_中枢位置_中枢内部_任意_任意'))
    assert ev.is_match(sigs)
    blocked = Event('x', '开多', signals_all=ev.signals_all,
                    signals_not=('日线_D1_MACD背驰_顶背驰_任意_任意',))
    assert not blocked.is_match(sigs)
    missing = Event('x', '开多', signals_all=('日线_D1_二买卖_二买_任意_任意',))
    assert not missing.is_match(sigs)
    assert ev.matched_signals(sigs) == ['日线_D1_中枢走向_上移_任意_任意', '日线_D1_中枢位置_中枢上方_近_任意']
