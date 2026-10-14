"""缠论战法回测 (chan_tactics) + 系统分析 (chan_analysis) + 发布归档。全程离线 (fetch 打桩)。"""
import json
import math
import os
import random
from datetime import date, timedelta

import pytest

import chan_analysis
from chan_analysis import (build_chan_analysis, generate_chan_standalone_html, load_klines,
                           pick_focus, render_chan_section_html, to_jsonable)
from chan_tactics import TACTICS, backtest_sectors


def _rows(n=220, seed=1, period=26, amp=8.0, drift=0.03, start=date(2025, 6, 2), base=100.0, with_open=True):
    rng = random.Random(seed)
    out, d, i = [], start, 0
    while len(out) < n:
        if d.weekday() < 5:
            mid = base + drift * i + amp * math.sin(2 * math.pi * i / period) + rng.uniform(-1, 1)
            h, l = mid + rng.uniform(0.2, 1.2), mid - rng.uniform(0.2, 1.2)
            r = {'date': d.isoformat(), 'close': rng.uniform(l, h), 'high': h, 'low': l, 'vol': rng.uniform(1e5, 3e5)}
            if with_open:
                r['open'] = rng.uniform(l, h)
            out.append(r)
            i += 1
        d += timedelta(days=1)
    return out


def _sector_hist(k=6, n=160):
    # 板块缓存的真实形状: 没有 open, 只有 amount
    hist = {}
    for j in range(k):
        rows = _rows(n=n, seed=10 + j, period=20 + 3 * j, with_open=False)
        for r in rows:
            r['amount'] = r.pop('vol')
        hist[f'板块{j}'] = rows
    return hist


@pytest.fixture(autouse=True)
def _clear_memo():
    chan_analysis._MEMO.clear()
    yield
    chan_analysis._MEMO.clear()


# ------------------------------------------------------------------ 回测

def test_backtest_fields_and_cache(tmp_path):
    hist = _sector_hist()
    cache = str(tmp_path / 'bt.json')
    r = backtest_sectors(hist, cache_path=cache)
    assert set(r['tactics']) == {t.key for t in TACTICS}
    for s in r['tactics'].values():
        assert {'n', 'distinct_sectors', 'distinct_dates', 'top3_share', 't5', 't10', 'verdict', 'small_sample'} <= set(s)
        if s['n']:
            assert s['distinct_sectors'] <= s['n'] and s['distinct_dates'] <= s['n']
    assert r['baseline_n']['t5'] > 0
    # 同一数据 → 命中缓存 (文件内容不变, 返回同一指纹)
    mtime = os.path.getmtime(cache)
    r2 = backtest_sectors(hist, cache_path=cache)
    assert r2['fingerprint'] == r['fingerprint'] and os.path.getmtime(cache) == mtime
    # 数据变了 → 指纹变, 重算
    hist['板块0'] = hist['板块0'][:-1]
    assert backtest_sectors(hist, cache_path=cache)['fingerprint'] != r['fingerprint']


def test_backtest_is_causal():
    """截掉最后 30 天重跑, 截断日期之前的事件 (及其 T+5 收益) 必须完全一致 —— 没用到未来数据。"""
    from chan_tactics import _iter_daily_signals, match_tactics
    from chan_engine import bars_from_records
    rows = _sector_hist(k=1, n=160)['板块0']
    full = bars_from_records(rows, symbol='x')
    cut = bars_from_records(rows[:-30], symbol='x')
    ev = lambda bars: {(i, t.key) for i, s in _iter_daily_signals(bars, 'x') for t in match_tactics(s)}
    a, b = ev(full), ev(cut)
    assert {e for e in a if e[0] < len(cut)} == b


# ------------------------------------------------------------------ 分析

def _ladder():
    return {'S级': [{'name': '甲', 'code': 'sh600001', 'ml': '科技', 'sub': 'x', 'score': 90},
                    {'name': '坏代码', 'code': '600002', 'ml': '科技', 'sub': 'x', 'score': 88}],
            'B级': [{'name': '乙', 'code': 'sz000002', 'ml': '周期', 'sub': 'y', 'score': 60}],
            'D级': [{'name': '不取', 'code': 'sz000003', 'ml': '周期', 'sub': 'y', 'score': 13}]}


def _fake_fetch(code, count=320):
    seed = sum(map(ord, code))
    return _rows(n=300, seed=seed, start=date(2025, 1, 2))


def test_pick_focus_grade_order_and_code_filter():
    picked = pick_focus(_ladder())
    assert [p['code'] for p in picked] == ['sh600001', 'sz000002']


def test_build_chan_analysis_offline(tmp_path):
    rd = _rows(n=300, start=date(2025, 1, 2))[-1]['date']
    r = build_chan_analysis(rd, _ladder(), sector_hist=_sector_hist(), fetch=_fake_fetch,
                            kline_cache=str(tmp_path / 'k.json'), backtest_cache=None)
    assert r is not None and r['report_date'] == rd
    assert [x['symbol'] for x in r['indexes']] == ['sh000001', 'sz399006']
    assert [x['symbol'] for x in r['focus']] == ['sh600001', 'sz000002']
    assert len(r['sectors']) == 6 and all('quadrant' in s for s in r['sectors'])
    assert r['backtest'] and r['mainline_health']
    for x in r['indexes'] + r['focus'] + r['sectors']:
        assert x['tone'] in ('good', 'warn', 'bad', 'neutral') and x['stage']
    json.dumps(to_jsonable(r), ensure_ascii=False)          # 可序列化 (剥掉 _ctx)

    sec = render_chan_section_html(r, link='chan/latest.html')
    assert 'chan-sec' in sec and '缠论结构分析' in sec and 'chan/latest.html' in sec
    assert '甲' in sec and '坏代码' not in sec
    page = generate_chan_standalone_html(r)
    assert page.startswith('<!doctype html>') and 'echarts' in page and 'chanIdx0' in page
    assert f'content="{rd}"' in page


def test_history_rerun_truncates_to_report_date(tmp_path):
    rows = _rows(n=300, start=date(2025, 1, 2))
    rd = rows[-40]['date']
    r = build_chan_analysis(rd, _ladder(), sector_hist=_sector_hist(), fetch=lambda c, n=320: rows,
                            kline_cache=str(tmp_path / 'k.json'), backtest_cache=None)
    for x in r['indexes'] + r['focus']:
        assert x['as_of'] <= rd
    for s in r['sectors']:
        assert s['as_of'] <= rd


def test_load_klines_cache_and_network_failure(tmp_path):
    cache = str(tmp_path / 'k.json')
    rows = _rows(n=100, start=date(2025, 1, 2))
    rd = rows[-1]['date']
    partial = rows + [{'date': '2099-01-01', 'open': 1, 'close': 1, 'high': 1, 'low': 1, 'vol': 1}]
    got = load_klines(['sh600001'], rd, cache_path=cache, fetch=lambda c: partial)
    assert got['sh600001'][-1]['date'] == rd
    stored = json.load(open(cache, encoding='utf-8'))['sh600001']['rows']
    assert stored[-1]['date'] == rd                          # 盘中/未来的半根K线不入缓存

    def boom(c):
        raise AssertionError('缓存已覆盖 report_date, 不应再联网')
    assert load_klines(['sh600001'], rd, cache_path=cache, fetch=boom)['sh600001'] == got['sh600001']

    # 新日期 + 网络失败 (None) → 退回旧缓存, 不丢数据
    later = '2099-12-31'
    assert load_klines(['sh600001'], later, cache_path=cache, fetch=lambda c: None)['sh600001'][-1]['date'] == rd


def test_build_returns_none_when_everything_missing(tmp_path):
    r = build_chan_analysis('2026-09-30', None, sector_hist={}, fetch=lambda c, n=320: None,
                            kline_cache=str(tmp_path / 'k.json'), backtest_cache=None)
    assert r is None
    assert render_chan_section_html(None) == '' and generate_chan_standalone_html(None) == ''


# ------------------------------------------------------------------ 发布

def test_publish_archives_chan_page_and_entry(tmp_path):
    import publish_site
    from test_dragon_succession import _make_report
    site = str(tmp_path / 'site')
    publish_site.publish(_make_report(tmp_path), site, chan_html='<html>CHAN</html>')
    assert os.path.isfile(os.path.join(site, 'chan', '2026-08-20.html'))
    assert os.path.isfile(os.path.join(site, 'chan', 'latest.html'))
    idx = open(os.path.join(site, 'index.html'), encoding='utf-8').read()
    assert 'href="chan/latest.html"' in idx


def test_publish_without_chan_html_has_no_entry(tmp_path):
    import publish_site
    from test_dragon_succession import _make_report
    site = str(tmp_path / 'site')
    publish_site.publish(_make_report(tmp_path), site)
    assert not os.path.isdir(os.path.join(site, 'chan'))
    assert 'href="chan/latest.html"' not in open(os.path.join(site, 'index.html'), encoding='utf-8').read()
