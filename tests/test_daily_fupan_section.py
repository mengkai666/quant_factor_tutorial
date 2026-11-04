import json

import pandas as pd
import pytest

import daily_fupan_section as fupan
import phase_resonance


@pytest.fixture
def isolated_market(monkeypatch, tmp_path):
    monkeypatch.setattr(fupan, 'DAILY_SNAPSHOT_DIR', str(tmp_path))
    monkeypatch.setattr(fupan, 'SENTIMENT_CACHE', str(tmp_path / 'sentiment.csv'))
    monkeypatch.setattr(fupan, 'ZT_CACHE_FILE', str(tmp_path / 'limits.csv'))
    monkeypatch.setattr(phase_resonance, 'fetch_index', lambda symbol: [])
    return tmp_path


def test_unavailable_market_does_not_become_sample_quotes(isolated_market):
    pd.DataFrame([{'日期': 20261030, '代码': 'sh600000'}]).to_csv(isolated_market / 'limits.csv', index=False)
    market = fupan._load_market_overview('2026-11-02')
    assert all(market[key] is None for key in ('up', 'down', 'zt', 'dt', 'sh', 'cyb'))
    assert fupan._market_commentary(market)['regime'] == '市场广度待核验'


def test_index_daily_range_does_not_invent_intraday_sequence():
    quote = {'open': 99, 'high': 105, 'low': 95, 'close': 101, 'chg': 1}
    result = fupan._index_commentary('上证指数', quote)
    assert '日内区间 95.00—105.00 点' in result['description']
    assert '午后' not in result['description'] and '先' not in result['description']


def test_ratio_only_snapshot_never_invents_stock_counts(isolated_market):
    (isolated_market / '2026-11-02.json').write_text(json.dumps({
        'report_date': '2026-11-02', 'breadth_ratio': .853, 'limit_up': 125, 'limit_down': 8,
    }), encoding='utf8')
    market = fupan._load_market_overview('2026-11-02')
    assert market['up'] is None and market['down'] is None
    assert fupan._market_commentary(market)['breadth'] == '上涨占比 85.3% · 涨多跌少'


def test_real_zero_counts_survive_snapshot_and_sentiment_fallback(isolated_market):
    (isolated_market / '2026-11-02.json').write_text(json.dumps({
        'limit_up': 125, 'limit_down': 8,
    }), encoding='utf8')
    pd.DataFrame([{'日期': 20261102, 'up': 4600, 'down': 800, 'zt': 125, 'dt': 8}]).to_csv(
        isolated_market / 'sentiment.csv', index=False)
    market = fupan._load_market_overview('2026-11-02', advance_decline={'up': 0, 'zt': 0, 'dt': 0})
    assert [market[key] for key in ('up', 'down', 'zt', 'dt')] == [0, 800, 0, 0]


@pytest.mark.parametrize(('counts', 'breadth', 'limits'), [
    ({'up': 864, 'down': 4557, 'zt': 47, 'dt': 82}, '上涨占比 15.9% · 跌多涨少', '跌停比涨停多 35 家'),
    ({'up': 4606, 'down': 794, 'zt': 125, 'dt': 8}, '上涨占比 85.3% · 涨多跌少', '涨停比跌停多 117 家'),
    ({'up': 70, 'down': 30, 'zt': 20, 'dt': 80}, '上涨占比 70.0% · 涨多跌少', '跌停比涨停多 60 家'),
    ({'up': 0, 'down': 0, 'zt': 0, 'dt': 0}, '上涨占比待核验', '涨停与跌停家数相同'),
    ({'up': None, 'down': None, 'zt': None, 'dt': None}, '上涨占比待核验', '涨跌停家数待核验'),
])
def test_complete_section_reports_session_facts_without_assuming_improvement(monkeypatch, counts, breadth, limits):
    monkeypatch.setattr(fupan, '_load_market_overview', lambda *args: {**counts, 'sh': None, 'cyb': None})
    monkeypatch.setattr(fupan, '_load_ladder_and_progression', lambda *args: ({}, [], [], 0, 0, []))
    monkeypatch.setattr(fupan, '_load_core_stock_quotes', lambda *args: [])
    html = fupan.generate_daily_fupan_html('2026-11-02')
    assert breadth in html and limits in html
    assert '缺少同日完整行情' in html
    for unsupported in ('结构温和修复', '市场处于温和修复周期', '跌停收敛至', '跌停回落至低位', '全天跌停仅', '建议总仓位控制'):
        assert unsupported not in html
    assert 'nan' not in html.lower() and 'None' not in html


def test_high_breadth_does_not_override_more_limit_downs():
    result = fupan._market_commentary({'up': 70, 'down': 30, 'zt': 20, 'dt': 80})
    assert '未确认修复前不新增仓位' in result['plan']


@pytest.fixture
def isolated_stock_quotes(monkeypatch, tmp_path):
    slices = tmp_path / 'slices'
    slices.mkdir()
    monkeypatch.setattr(fupan, 'PRICE_SLICE_DIR', str(slices))
    monkeypatch.setattr(fupan, 'PRICE_CACHE', str(tmp_path / 'prices.csv'))
    monkeypatch.setattr(fupan, 'CALENDAR_CACHE', str(tmp_path / 'calendar.csv'))
    monkeypatch.setattr(fupan, 'SECURITY_MASTER_CACHE', str(tmp_path / 'master.csv'))
    pd.DataFrame({'trade_date': ['2026-10-29', '2026-10-30', '2026-11-02']}).to_csv(fupan.CALENDAR_CACHE, index=False)
    return tmp_path


def _core_quotes(price_df=None):
    tiers = {3: [{'code': '000001', 'name': '甲股', 'sector': '测试', 'amount': 1e8}]}
    return fupan._load_core_stock_quotes('2026-11-02', tiers, [], 3, price_df)


def test_missing_stock_prices_remain_unknown_instead_of_fifteen_or_ten_percent(isolated_stock_quotes):
    row = _core_quotes()[0]
    assert row['price'] is None and row['chg'] is None
    assert row['price_date'] is None
    assert row['quote_status'] == '同日收盘价待核验'


def test_stale_cache_rows_never_become_report_day_quotes(isolated_stock_quotes):
    pd.DataFrame([
        {'date': '2026-10-29', 'code': 'sz000001', 'close_raw': 10},
        {'date': '2026-10-30', 'code': 'sz000001', 'close_raw': 11},
        {'date': '2026-11-03', 'code': 'sz000001', 'close_raw': 12},
    ]).to_csv(fupan.PRICE_CACHE, index=False)
    row = _core_quotes()[0]
    assert row['price'] is None and row['chg'] is None


def test_current_slice_without_previous_close_keeps_price_but_not_invented_change(isolated_stock_quotes):
    pd.DataFrame([{'date': '2026-11-02', 'code': '000001', 'close_raw': 12.34}]).to_csv(
        isolated_stock_quotes / 'slices/2026-11-02.csv.gz', index=False)
    row = _core_quotes()[0]
    assert row['price'] == 12.34 and row['chg'] is None
    assert row['price_date'] == '2026-11-02'
    assert row['quote_status'] == '前一交易日收盘价待核验'


def test_in_memory_prices_are_used_and_real_unchanged_close_is_zero(isolated_stock_quotes):
    frame = pd.DataFrame([
        {'date': '2026-10-30', 'code': 'sz000001', 'close_raw': 11},
        {'date': '2026-11-02', 'code': '000001.SZ', 'close_raw': 11},
    ])
    pd.DataFrame([{'date': '2026-11-02', 'code': 'sz000001', 'close_raw': 99}]).to_csv(fupan.PRICE_CACHE, index=False)
    original = frame.copy(deep=True)
    row = _core_quotes(frame)[0]
    assert row['price'] == 11 and row['chg'] == 0
    assert row['previous_close_date'] == '2026-10-30'
    assert row['quote_status'] == '同日及前一交易日原始收盘价已核验'
    pd.testing.assert_frame_equal(frame, original)


def test_complete_raw_close_pair_retains_real_twenty_percent_move(isolated_stock_quotes):
    frame = pd.DataFrame([
        {'date': '2026-10-30', 'code': 'sz000001', 'close_raw': 10},
        {'date': '2026-11-02', 'code': 'sz000001', 'close_raw': 12},
    ])
    row = _core_quotes(frame)[0]
    assert row['price'] == 12 and row['chg'] == pytest.approx(20)


def test_missing_previous_session_cannot_be_replaced_with_older_slice(isolated_stock_quotes):
    for date, price in [('2026-10-29', 10), ('2026-11-02', 12)]:
        pd.DataFrame([{'date': date, 'code': 'sz000001', 'close_raw': price}]).to_csv(
            isolated_stock_quotes / f'slices/{date}.csv.gz', index=False)
    row = _core_quotes()[0]
    assert row['price'] == 12 and row['chg'] is None
    assert row['previous_close_date'] is None


@pytest.mark.parametrize('current,previous,expected_price', [
    (float('nan'), 10, None), (float('inf'), 10, None), (0, 10, None), (-5, 10, None),
    (12, float('nan'), 12), (12, float('inf'), 12), (12, 0, 12), (12, -1, 12),
])
def test_invalid_close_cannot_supply_price_or_change(isolated_stock_quotes, current, previous, expected_price):
    frame = pd.DataFrame([
        {'date': '2026-10-30', 'code': 'sz000001', 'close_raw': previous},
        {'date': '2026-11-02', 'code': 'sz000001', 'close_raw': current},
    ])
    row = _core_quotes(frame)[0]
    assert row['price'] == expected_price and row['chg'] is None


def test_unknown_quote_cells_render_without_nan_none_or_fabricated_numbers(isolated_stock_quotes, monkeypatch):
    monkeypatch.setattr(fupan, '_load_market_overview', lambda *args: {key: None for key in ('up', 'down', 'zt', 'dt', 'sh', 'cyb')})
    tiers = {3: [{'code': '000001', 'name': '甲股', 'sector': '测试', 'amount': 1e8}]}
    monkeypatch.setattr(fupan, '_load_ladder_and_progression', lambda *args: (tiers, [], [], 3, 2, []))
    html = fupan.generate_daily_fupan_html('2026-11-02')
    table = html.split('<table class="f-simple-table">', 1)[1].split('</table>', 1)[0]
    assert '同日收盘价待核验' in table
    assert table.count('>待核验<') == 2
    assert '15.00 元' not in table and '+10.00%' not in table and '+0.00%' not in table
    assert 'nan' not in table.lower() and 'None' not in table


def test_unknown_change_renders_separately_from_valid_price(isolated_stock_quotes, monkeypatch):
    monkeypatch.setattr(fupan, '_load_market_overview', lambda *args: {key: None for key in ('up', 'down', 'zt', 'dt', 'sh', 'cyb')})
    tiers = {3: [{'code': '000001', 'name': '甲股', 'sector': '测试', 'amount': 1e8}]}
    monkeypatch.setattr(fupan, '_load_ladder_and_progression', lambda *args: (tiers, [], [], 3, 2, []))
    frame = pd.DataFrame([{'date': '2026-11-02', 'code': 'sz000001', 'close_raw': 12.34}])
    html = fupan.generate_daily_fupan_html('2026-11-02', price_df=frame)
    table = html.split('<table class="f-simple-table">', 1)[1].split('</table>', 1)[0]
    assert '12.34 元' in table and '>待核验<' in table
    assert '前一交易日收盘价待核验' in table
    assert '+10.00%' not in table and '+0.00%' not in table
