"""缠论内核 (src/chan_engine.py) 的结构不变量。

不对拍 czsc (CI 装不上 Rust 扩展), 改为钉住 czsc 实现里成立的几条硬约束:
去包含方向、分型交替、笔长度、相邻笔共享端点、笔被破坏回退、增量 == 一次性 (不穿越)。
"""
import math
import random

import pytest

from chan_engine import (DOWN, MARK_D, MARK_G, UP, ZS, ChanAnalyzer, NewBar, RawBar,
                         bars_from_records, check_fx, check_gap_info, get_zs_seq,
                         remove_include, resample_weekly)


def _dt(i):
    # 连续自然日字符串 (内核只要求可比较)
    from datetime import date, timedelta
    return (date(2025, 1, 1) + timedelta(days=i)).isoformat()


def _bar(i, high, low, close=None, open_=None, vol=100.0):
    close = (high + low) / 2 if close is None else close
    open_ = close if open_ is None else open_
    return RawBar(symbol='T', dt=_dt(i), open=open_, high=high, low=low, close=close, vol=vol, id=i)


def zigzag_bars(n=400, period=24, amp=10.0, drift=0.02, seed=7):
    """正弦波 + 噪声 + 漂移: 足够产生几十笔、若干中枢, 且确定可复现。"""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        mid = 100 + drift * i + amp * math.sin(2 * math.pi * i / period) + rng.uniform(-0.8, 0.8)
        h = mid + rng.uniform(0.3, 1.5)
        l = mid - rng.uniform(0.3, 1.5)
        out.append(_bar(i, h, l, close=rng.uniform(l, h), open_=rng.uniform(l, h), vol=rng.uniform(50, 150)))
    return out


def random_walk_bars(n=600, seed=11):
    rng = random.Random(seed)
    p, out = 50.0, []
    for i in range(n):
        p *= 1 + rng.gauss(0, 0.02)
        h, l = p * (1 + abs(rng.gauss(0, 0.01))), p * (1 - abs(rng.gauss(0, 0.01)))
        out.append(_bar(i, h, l, close=p))
    return out


# ------------------------------------------------------------------ 去包含 / 分型

def test_remove_include_up_takes_higher_high_and_higher_low():
    k1 = NewBar.from_raw(_bar(0, 10, 8))
    k2 = NewBar.from_raw(_bar(1, 12, 9))           # k1 -> k2 向上
    k3 = _bar(2, 11.5, 9.5)                         # 被 k2 包含
    has, k4 = remove_include(k1, k2, k3)
    assert has
    assert (k4.high, k4.low) == (12, 9.5)           # 向上: 高高、低高
    assert len(k4.elements) == 2


def test_remove_include_down_takes_lower_high_and_lower_low():
    k1 = NewBar.from_raw(_bar(0, 12, 10))
    k2 = NewBar.from_raw(_bar(1, 11, 8))            # 向下
    k3 = _bar(2, 10.5, 8.5)                         # 被包含
    has, k4 = remove_include(k1, k2, k3)
    assert has
    assert (k4.high, k4.low) == (10.5, 8)           # 向下: 高低、低低


def test_no_include_returns_new_bar():
    k1 = NewBar.from_raw(_bar(0, 10, 8))
    k2 = NewBar.from_raw(_bar(1, 12, 9))
    has, k4 = remove_include(k1, k2, _bar(2, 13, 10))
    assert not has and k4.high == 13


def test_check_fx_top_and_bottom():
    a, b, c = (NewBar.from_raw(_bar(i, h, l)) for i, (h, l) in enumerate([(10, 8), (12, 9), (11, 7)]))
    fx = check_fx(a, b, c)
    assert fx is not None and fx.mark == MARK_G and fx.fx == 12
    a, b, c = (NewBar.from_raw(_bar(i, h, l)) for i, (h, l) in enumerate([(12, 9), (10, 7), (11, 8)]))
    fx = check_fx(a, b, c)
    assert fx is not None and fx.mark == MARK_D and fx.fx == 7


# ------------------------------------------------------------------ 笔 / 中枢

def test_bi_invariants_on_zigzag():
    c = ChanAnalyzer(zigzag_bars(), symbol='T')
    bl = c.bi_list
    assert len(bl) >= 10
    for i, bi in enumerate(bl):
        assert len(bi.bars) >= 6                         # 笔至少 min_bi_len 根无包含K线
        assert bi.high >= bi.low
        if bi.direction == UP:
            assert bi.fx_a.mark == MARK_D and bi.fx_b.mark == MARK_G
        else:
            assert bi.fx_a.mark == MARK_G and bi.fx_b.mark == MARK_D
        if i:
            prev = bl[i - 1]
            assert prev.direction != bi.direction            # 方向交替
            assert prev.edt < bi.edt                         # czsc 的时序判据
            assert prev.fx_b.dt == bi.fx_a.dt                # 相邻笔共享端点分型


def test_rollback_newest_bar_never_breaks_last_bi():
    """笔被破坏回退: 每根K线喂完后, **最新一根**无包含K线不越过最后一笔的端点。

    这是 czsc 给的保证, 也只有这么强 (照搬, 不加强):
    * 只看最新一根, 且每根K线最多回退一笔 —— 回退后露出来的前一笔, 可能已被未完成笔里
      更早的K线越过 (例: 向上笔被破坏弹出, 下面那个向下笔的低点早被分型右侧K线跌破), czsc 不再追查;
    * 生成**第一笔**的那一步直接 return, 不做破坏检查, 越界要到下一根K线才回退。
    """
    c = ChanAnalyzer(symbol='T')
    rolled_back = 0
    for bar in random_walk_bars():
        had_bi, n_before = bool(c.bi_list), len(c.bi_list)
        c.update(bar)
        rolled_back += len(c.bi_list) < n_before
        if not had_bi or not c.bi_list or not c.bars_ubi:
            continue
        last, newest = c.bi_list[-1], c.bars_ubi[-1]
        if last.direction == UP:
            assert newest.high <= last.high
        else:
            assert newest.low >= last.low
    assert rolled_back > 0          # 随机游走里必然发生过回退, 否则这条测试没测到东西


def test_incremental_equals_one_shot():
    """逐根 update 与一次性灌入结果完全一致 —— 回测不穿越的前提。"""
    bars = zigzag_bars(n=300)
    one = ChanAnalyzer(bars, symbol='T')
    inc = ChanAnalyzer(symbol='T')
    for b in bars:
        inc.update(b)
    sig = lambda c: [(b.sdt, b.edt, b.direction, b.high, b.low) for b in c.bi_list]
    assert sig(one) == sig(inc)


def test_prefix_structure_is_stable():
    """前 k 根算出的已完成笔, 除最后一笔(可能被后续K线破坏重算)外, 在全量结果里原样存在。"""
    bars = zigzag_bars(n=300)
    full = {(b.sdt, b.edt, b.direction) for b in ChanAnalyzer(bars).bi_list}
    for k in (120, 180, 240):
        part = ChanAnalyzer(bars[:k]).bi_list[:-1]
        assert {(b.sdt, b.edt, b.direction) for b in part} <= full


def test_same_dt_update_replaces_last_bar():
    bars = zigzag_bars(n=120)
    c = ChanAnalyzer(bars)
    n = len(c.bars_raw)
    last = bars[-1]
    c.update(RawBar(symbol='T', dt=last.dt, open=last.open, high=last.high + 0.1,
                    low=last.low, close=last.close, id=last.id))
    assert len(c.bars_raw) == n and c.bars_raw[-1].high == pytest.approx(last.high + 0.1)


def test_zs_definition():
    c = ChanAnalyzer(zigzag_bars(drift=0.0))
    real = c.real_zs_list
    assert real
    for z in real:
        assert len(z.bis) >= 3
        assert z.zg == min(b.high for b in z.bis[:3])
        assert z.zd == max(b.low for b in z.bis[:3])
        assert z.zg >= z.zd and z.gg >= z.zg and z.dd <= z.zd
    # get_zs_seq 覆盖全部笔且不重叠
    seq = get_zs_seq(c.bi_list)
    assert sum(len(z.bis) for z in seq) == len(c.bi_list)


# ------------------------------------------------------------------ 适配器

def test_bars_from_records_synthesizes_open_and_vol():
    rows = [{'date': '2026-01-02', 'close': 10, 'high': 10.5, 'low': 9.5, 'amount': 1e8},
            {'date': '2026-01-05', 'close': 11, 'high': 11.2, 'low': 10.1, 'amount': 2e8},
            {'date': '2026-01-06', 'close': float('nan'), 'high': 1, 'low': 1}]
    bars = bars_from_records(rows)
    assert len(bars) == 2                              # NaN 行被跳过
    assert bars[1].open == 10 and bars[1].open_synthetic
    assert bars[1].vol == 2e8                          # 没有 vol 用 amount 顶上


def test_resample_weekly_groups_iso_weeks():
    rows = [{'date': d, 'open': 1, 'close': c, 'high': c + 1, 'low': c - 1, 'vol': 1}
            for d, c in [('2026-03-02', 10), ('2026-03-03', 12), ('2026-03-06', 11),
                         ('2026-03-09', 13), ('2026-03-10', 9)]]
    w = resample_weekly(bars_from_records(rows), 'T')
    assert [b.dt for b in w] == ['2026-03-06', '2026-03-10']
    assert (w[0].high, w[0].low, w[0].close) == (13, 9, 11)
    assert (w[1].high, w[1].low, w[1].close) == (14, 8, 9)


def test_gap_info_marks_cover():
    bars = [_bar(0, 10, 9), _bar(1, 12, 11), _bar(2, 11.5, 10.5)]
    gaps = check_gap_info(bars)
    assert gaps and gaps[0]['kind'] == '向上缺口' and gaps[0]['cover'] == '未补'
    gaps = check_gap_info(bars + [_bar(3, 10.5, 9.8)])
    assert gaps[0]['cover'] == '已补'
