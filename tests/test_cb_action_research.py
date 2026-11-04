"""Contract tests for the decisions that could otherwise mislead a reader."""
from pathlib import Path

import pandas as pd
import pytest

from src.cb_action_research import _candidate, _frame, _historical_events, _history_summary, redemption_state
from src.cb_action_research import _detail_universe, _repair_watchlist, _recovery_reserves, build_cb_research


def test_last_trading_day_cannot_be_used_on_next_session():
    state = redemption_state({"delist_dt": "2026-10-30", "redeem_icon": "R"}, {}, "2026-11-02")
    assert state["expired"]
    assert "排除" in state["status"]


def test_no_notice_does_not_imply_redemption_safe():
    state = redemption_state({}, {}, "2026-11-02")
    assert state["status"] == "强赎状态缺失"
    assert state["conversion_price"] is None


def test_future_and_immature_bars_cannot_supply_outcomes():
    dates = pd.bdate_range("2026-09-01", periods=27).strftime("%Y-%m-%d")
    bars = [{"date": date, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 100} for date in dates]
    bars[21].update(close=108, high=109, volume=200)
    frame = _frame(bars, dates[24])
    assert frame.iloc[-1]["date"] == dates[24]
    events = _historical_events("123456", "测试债", frame, pd.DataFrame())
    assert len(events) == 1
    assert events[0]["mature"] is False
    assert events[0]["forward_5d_return_pct"] is None


def test_failure_events_remain_in_the_statistics():
    events = [{"code": "1", "setup": "放量债股共振", "mature": True, "forward_5d_return_pct": 10},
              {"code": "2", "setup": "放量债股共振", "mature": True, "forward_5d_return_pct": -20},
              {"code": "3", "setup": "放量债股共振", "mature": False, "forward_5d_return_pct": None}]
    row = _history_summary(events)[0]
    assert row["events"] == 2
    assert row["negative_outcomes"] == 1
    assert row["median_5d_return_pct"] == -5
    assert "不是T+0胜率" in row["label"]


def test_candidate_is_an_untriggered_intraday_plan():
    row = {"code": "123456", "name": "测试债", "stock_code": "300001", "stock_name": "测试股", "amount": 1e9}
    bond = {"date": "2026-10-30", "close": 150, "high": 155, "low": 145, "ma5": 148, "day_change_pct": 2}
    stock = {"close": 20, "high": 21, "day_change_pct": 3}
    red = {"conversion_price": 15, "remaining_issue_yi": 5}
    candidate = _candidate(row, bond, stock, red, 1, "2026-11-02")
    assert candidate["premium_pct"] == 12.5
    assert candidate["tier"] == "观察"
    assert "当前=等待" in candidate["actual_status"]
    assert "14:50" in candidate["holding"]
    assert "不隔夜" in candidate["holding"]
    assert candidate["stop"]["max_loss_pct"] == 1.5
    assert candidate["trigger"]["requires_vwap"] is True
    assert isinstance(candidate["evidence"], list)
    assert len(candidate["evidence"]) == 4
    assert all(isinstance(item, str) for item in candidate["evidence"])
    assert candidate["raw_evidence"]["stock"]["close"] == 20


def test_recovery_reserves_cannot_bypass_other_failed_gates():
    reason = '收盘位于日内区间下40%，抛压未消化'
    row = {'code': '1', 'name': '候补债', 'stock_name': '正股', 'price': 150,
           'pct_change': 2, 'premium_pct': 20, 'amount_yi': 1, 'reason': reason,
           'reasons': [reason], 'bond_metrics': {'close_location': .25},
           'stock_metrics': {'day_change_pct': 3}}
    blocked = {**row, 'name': '风险债', 'reasons': [reason, '正股风险警示']}
    reserves = _recovery_reserves([row, blocked], '2026-11-02')
    assert len(reserves) == 1
    assert reserves[0]['close_location_pct'] == 25
    assert '下一交易日筛选' in reserves[0]['recheck']
    assert '当前未通过完整筛选' in reserves[0]['actual_status']
    assert 'entry_trigger' not in reserves[0]


def test_current_coverage_includes_liquid_pullbacks_and_gainers_beyond_top30():
    rows = [{'code': str(i), 'amount': 5e8 - i * 1e7, 'changepercent': -4} for i in range(45)]
    rows[-1]['changepercent'] = 2
    selected = _detail_universe(list(reversed(rows)))
    by_code = {row['code']: row for row in selected}
    assert by_code['30']['amount_rank'] == 31  # 2亿元，超过旧前30范围。
    assert by_code['44']['amount_rank'] == 45  # 低成交额但满足债涨门槛。
    assert '31' not in by_code
    assert len(selected) == 32
    assert all('amount_rank' not in row for row in rows)  # 不修改原始报价。


def _pullback_row(**overrides):
    reasons = ['最近收盘未保持≥1%正收益，强势确认不足', '转债收盘低于MA5']
    row = {'code': '123456', 'name': '回撤债', 'stock_code': '300001', 'stock_name': '正股',
           'price': 150, 'pct_change': -4, 'premium_pct': 20, 'amount_yi': 3, 'amount_rank': 35,
           'as_of': '2026-10-30', 'reasons': reasons, 'reason': '；'.join(reasons), 'redemption': {},
           'bond_metrics': {'date': '2026-10-30', 'close': 150, 'day_change_pct': -4, 'bars': 45,
                            'ma5': 155, 'prior_20d_high': 175, 'close_location': .2},
           'stock_metrics': {'date': '2026-10-30', 'day_change_pct': -2}}
    return {**row, **overrides}


def test_repair_watch_is_separate_and_retains_premium_and_short_history_blocks():
    row = _pullback_row()
    high_premium = _pullback_row(code='118999', premium_pct=95,
                                reasons=row['reasons'] + ['转股溢价率缺失或超过研究阈值50%'])
    new_bond = _pullback_row(code='123999', bond_metrics={**row['bond_metrics'], 'bars': 32},
                            reasons=row['reasons'] + ['上市/可得日线不足40根，暂不纳入成熟形态策略'])
    watched = _repair_watchlist([row, high_premium, new_bond], '2026-11-02')
    assert len(watched) == 3
    assert all(item['execution_eligible'] is False and 'entry_trigger' not in item for item in watched)
    blocked = {item['code']: item for item in watched}
    assert '50%' in blocked['118999']['blocking_gates'][0]
    assert '40根' in blocked['123999']['blocking_gates'][0]
    assert '交易资格仍被拦截' in blocked['118999']['actual_status']
    assert not blocked['123456']['blocking_gates']
    assert '下一交易日条件观察' in blocked['123456']['recheck']
    assert not _recovery_reserves([row, high_premium, new_bond], '2026-11-02')


def test_repair_watch_rejects_bad_data_lifecycle_risk_and_nonliquid_names():
    row = _pullback_row()
    cases = [
        _pullback_row(redemption={'announced': True}),
        _pullback_row(stock_name='ST正股'),
        _pullback_row(amount_yi=1.99),
        _pullback_row(reasons=row['reasons'] + ['债或正股日线日期缺失/不一致']),
        _pullback_row(reasons=row['reasons'] + ['强赎状态和当前转股价缺失']),
        _pullback_row(reasons=row['reasons'] + ['市场快照与日线收盘不一致']),
    ]
    assert not _repair_watchlist(cases, '2026-11-02')


def test_expanded_screening_preserves_strong_gates_and_original_history_cohort(tmp_path, monkeypatch):
    import src.cb_action_research as cb

    dates = pd.bdate_range(end='2026-10-30', periods=50).strftime('%Y-%m-%d')
    market = [{'code': f'{123000+i}', 'symbol': f'sz{123000+i}', 'name': f'测试债{i}',
               'amount': 5e8 - i * 1e7, 'changepercent': 2 if i == 44 else -4 if i == 30 else 0,
               'quote_date': '2026-10-30', 'trade': 102 if i == 44 else 96 if i == 30 else 100}
              for i in range(45)]
    monkeypatch.setattr(cb, '_fetch_market', lambda: {'rows': market, 'complete_pagination': True})
    monkeypatch.setattr(cb, '_fetch_base', lambda: {'rows': []})
    monkeypatch.setattr(cb, '_fetch_redeem', lambda: {'rows': [
        {'bond_id': row['code'], 'stock_id': '300001', 'stock_nm': '测试股', 'convert_price': 100}
        for row in market]})
    closes = {row['symbol']: row['trade'] for row in market}
    closes['sz300001'] = 101

    def bars(symbol):
        result = [{'date': date, 'open': 100, 'high': 101, 'low': 99, 'close': 100, 'volume': 100} for date in dates]
        close = closes[symbol]
        result[-1].update(open=close - .5, high=close + 1, low=close - 1, close=close)
        return result

    monkeypatch.setattr(cb, '_fetch_daily', bars)
    history_codes = []
    monkeypatch.setattr(cb, '_historical_events', lambda code, *args: history_codes.append(code) or [])
    result = build_cb_research(tmp_path, tmp_path / 'report', '2026-10-30', '2026-11-02')
    assert result['screening_audit']['detailed'] == 32
    assert result['screening_audit']['quote_gain_ge_1_unchecked'] == []
    assert [row['code'] for row in result['candidates']] == ['123044']
    assert [row['code'] for row in result['repair_watchlist']] == ['123030']
    assert result['repair_watchlist'][0]['execution_eligible'] is False
    assert '转债收盘低于MA5' in result['repair_watchlist'][0]['failed_gates']
    assert set(history_codes) == {row['code'] for row in market[:30]}
    assert result['market_summary']['history_bonds'] == 30
    assert result['decision_counts']['watch'] == 1
    assert result['decision_counts']['triggered'] is None
    assert '盘中触发状态未核验' in result['summary'][1]
    assert '触发前均为空仓' not in result['summary'][1]
    assert '活跃债回撤研究' in result['summary'][1]
    assert result['repair_watchlist'][0]['setup'].startswith('活跃债回撤')


@pytest.mark.parametrize('data_date,quote_time,quote_close,verified', [
    ('2026-10-30', '15:00:00', '101', False),
    ('2026-11-02', '14:59:00', '101', False),
    ('2026-11-02', '15:00:00', '110', False),
    ('2026-11-02', '15:00:00', '101', True),
])
def test_session_review_requires_matching_closed_session_data(tmp_path, monkeypatch, data_date, quote_time, quote_close, verified):
    import json
    from tools import review_cb_session as review

    raw = tmp_path / 'cb_evidence/raw'
    raw.mkdir(parents=True)
    (tmp_path / 'cb_baotuan_playbook.json').write_text(json.dumps({
        'as_of': '2026-10-30', 'plan_date': '2026-11-02', 'candidates': [], 'excluded': []}), encoding='utf-8')
    (raw / 'market.json').write_text(json.dumps({'rows': [
        {'code': '123456', 'symbol': 'sz123456', 'name': '测试债', 'amount': 3e8, 'trade': 100}]}), encoding='utf-8')
    (raw / 'redemption.json').write_text(json.dumps({'rows': [
        {'bond_id': '123456', 'stock_id': '300001', 'stock_nm': '测试股'}]}), encoding='utf-8')
    monkeypatch.setattr(review, '_fetch_daily', lambda symbol: [
        {'date': data_date, 'open': 100, 'high': 102, 'low': 98, 'close': 101, 'volume': 1}])

    class Response:
        text = '\n'.join(f'var hq_str_{symbol}="' + ','.join(
            ['测试', '100', '100', quote_close, '102', '98', '0', '0', '1', '1'] + ['0'] * 20 + [data_date, quote_time]) + '";'
            for symbol in ('sz123456', 'sz300001'))

        def raise_for_status(self):
            pass

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(review, '_session', Session)
    result = review.collect_review(tmp_path, '2026-11-02', ['123456'])
    row = result['rows'][0]
    assert result['verified_pairs'] == int(verified)
    assert row['bond']['observed_latest_daily_date'] == data_date
    assert row['close_vs_prior_close_pct'] == (1 if verified else None)
    assert row['open_gap_pct'] == (0 if verified else None)
    assert row['intraday_execution_confirmed'] is False
    assert ('不能判断买点触发或交易盈亏' if verified else '不能当作当日复盘结果') in row['execution_conclusion']
