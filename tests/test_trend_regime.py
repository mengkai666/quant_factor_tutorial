# -*- coding: utf-8 -*-
"""长期趋势层 (src/trend_regime.py) 单测。

全部用合成序列, 不碰网络: build_trend_regime 支持注入 idx。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trend_regime import (  # noqa: E402
    TREND_CONFIRM, TREND_MA, TREND_SLOPE_WIN,
    build_trend_regime, classify_series, pullback_frequency,
    render_trend_regime_html,
)

N = TREND_MA + TREND_SLOPE_WIN + 80   # 足够长, 确认段能形成


def mk(closes, start_year=2020):
    """把收盘序列包成 fetch_index_full 的行格式 (日期只需单调且 >= FREQ_START)."""
    import datetime as dt
    d0 = dt.date(start_year, 1, 1)
    return [{'date': (d0 + dt.timedelta(days=i)).strftime('%Y-%m-%d'),
             'open': c, 'high': c, 'low': c, 'close': c, 'volume': 1}
            for i, c in enumerate(closes)]


def rising(n=N):
    return [100.0 * (1.002 ** i) for i in range(n)]


def falling(n=N):
    return [100.0 * (0.998 ** i) for i in range(n)]


def flat(n=N):
    return [100.0 + (i % 4) * 0.05 for i in range(n)]


# ---------- 三档识别 ----------

def test_label_rising():
    res = build_trend_regime(idx=mk(rising()))
    assert res['label'] == '上升'
    assert res['pos_pct'] > 0 and res['slope'] > 0
    assert res['cap'] and res['action']


def test_label_falling():
    res = build_trend_regime(idx=mk(falling()))
    assert res['label'] == '下跌'
    assert res['pos_pct'] < 0 and res['slope'] < 0
    assert '降一档' in res['cap'] or '防守' in res['cap']


def test_label_flat():
    res = build_trend_regime(idx=mk(flat()))
    assert res['label'] == '震荡'


# ---------- 确认根数: 一根反向不许切档 ----------

def test_one_bar_flip_does_not_switch_label():
    closes = rising()
    # 最后一根砸到 MA 下方: raw 会翻, 但 conf 需要连续 TREND_CONFIRM 根
    closes[-1] = closes[-1] * 0.5
    res = build_trend_regime(idx=mk(closes))
    assert res['label'] == '上升', '单根反向不应切档'
    assert res['raw'] != '上升'
    assert res['pending'] == res['raw'] and res['pending_days'] == 1


def test_confirm_bars_do_switch_label():
    closes = rising()
    for k in range(1, TREND_CONFIRM + 1):
        closes[-k] = closes[-k] * 0.5
    res = build_trend_regime(idx=mk(closes))
    assert res['label'] != '上升'
    assert res['pending'] is None


def test_classify_series_short_returns_none():
    raw, conf, s0 = classify_series(mk(rising(TREND_MA)))
    assert set(raw) == {None} and set(conf) == {None}
    assert s0 == TREND_MA


# ---------- near 贴近阈值旗标 ----------

def test_near_flag_off_on_pure_flat():
    res = build_trend_regime(idx=mk(flat()))
    assert res['label'] == '震荡' and res['near'] is None


def test_near_flag_on_when_hugging_threshold():
    # 斜率落在 (-1.0%, -0.8%] 且收盘在 MA 下方 -> 震荡档但贴近下跌
    hit = None
    for r in [x / 100000.0 for x in range(1, 60)]:
        closes = [100.0 * ((1 - r) ** i) for i in range(N)]
        res = build_trend_regime(idx=mk(closes))
        if res['label'] == '震荡' and res['near'] == '下跌':
            hit = res
            break
    assert hit is not None, '找不到贴近下跌档阈值的合成序列'
    assert hit['pos_pct'] < 0
    assert '一步' in hit['action']


# ---------- 频率表 ----------

def test_pullback_frequency_counts_events():
    # 锯齿: 新高后回撤约 -2%, 落在 [PB_LO, PB_HI] 区间
    closes = []
    base = 100.0
    for i in range(N):
        if i % 8 < 5:
            base *= 1.004
            closes.append(base)
        else:
            closes.append(base * 0.98)
    rows = mk(closes)
    raw, conf, s0 = classify_series(rows)
    freq = pullback_frequency(rows, conf, s0)
    assert freq, '锯齿序列应至少落出一个回撤段事件'
    for lab, v in freq.items():
        assert lab in ('上升', '震荡', '下跌')
        assert v['n'] >= 1
        assert v['t3_up'] is None or 0.0 <= v['t3_up'] <= 100.0


def test_pullback_frequency_empty_on_monotone():
    rows = mk(rising())
    raw, conf, s0 = classify_series(rows)
    assert pullback_frequency(rows, conf, s0) == {}


# ---------- 降级 ----------

@pytest.mark.parametrize('bad', [[], mk([100.0] * 10), mk(rising(TREND_MA))])
def test_build_returns_none_on_short_series(bad):
    assert build_trend_regime(idx=bad) is None


@pytest.mark.parametrize('bad', [None, {}, {'label': None}])
def test_render_empty_on_bad_input(bad):
    assert render_trend_regime_html(bad) == ''


# ---------- 渲染 ----------

def test_render_contains_sections_and_action():
    res = build_trend_regime(idx=mk(falling()),
                            sub_phase={'name': '回撤段'},
                            sentiment_regime={'title': '普涨强势'})
    html = render_trend_regime_html(res)
    assert html
    assert '怎么操作' in html
    assert '长期趋势' in html
    assert 'overflow-wrap:anywhere' in html
    assert 'None' not in html and 'nan' not in html
    # 与情绪层冲突时必须给出优先级, 不能两条结论并列
    assert '趋势定仓位上限' in html
    # 三段网格 + 小阶段组合
    assert html.count('tr-col') >= 3
    assert '回撤段' in html


def test_render_escapes_dynamic_text():
    res = build_trend_regime(idx=mk(flat()), sub_phase={'name': '<script>x</script>'})
    html = render_trend_regime_html(res)
    assert '<script>' not in html
    assert '&lt;script&gt;' in html
