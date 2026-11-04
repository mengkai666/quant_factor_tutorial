"""Research-signal integrity, not tests of cosmetic text."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from stock_action_research import (build_candidate, current_metrics, historical_replay,
                                   parse_sina_daily, screen_prices)


def bars(n=25):
    dates = pd.bdate_range("2026-09-01", periods=n).strftime("%Y-%m-%d").tolist()
    return [{"date": d, "open": 10 + i / 10, "close": 10 + i / 10,
             "high": 10.2 + i / 10, "low": 9.8 + i / 10, "volume": 1000.0}
            for i, d in enumerate(dates)]


def test_source_future_is_excluded_and_invalid_ohlc_rejected():
    payload = 'var _x=([{"day":"2026-10-30","open":"10","high":"11","low":"9","close":"10","volume":"100"},{"day":"2026-11-02","open":"20","high":"21","low":"19","close":"20","volume":"100"}]);'
    assert len(parse_sina_daily(payload, "2026-10-30")) == 1
    with pytest.raises(ValueError, match="Inconsistent"):
        parse_sina_daily(payload.replace('"high":"11"', '"high":"8"'), "2026-10-30")


def test_missing_session_and_cross_source_disagreement_block():
    rows = bars()
    last = rows[-1]
    quote = {**last}
    expected = [r["date"] for r in rows]
    good = current_metrics({"rows": rows}, expected, quote, last["close"], last["date"])
    assert good["usable"]
    missing = current_metrics({"rows": rows[:8] + rows[9:]}, expected, quote, last["close"], last["date"])
    assert not missing["usable"]
    bad_quote = {**quote, "close": quote["close"] + 1}
    assert not current_metrics({"rows": rows}, expected, bad_quote, last["close"], last["date"])["usable"]


def test_pending_trigger_has_bounded_entry_and_no_automatic_buy():
    rows = bars()
    last = rows[-1]
    m = current_metrics({"rows": rows}, [r["date"] for r in rows], last, last["close"], last["date"])
    c = build_candidate("sz000678", "测试股份", {"r5": 25, "r20": 50, "persistence": 5}, m, last,
                        as_of=last["date"], plan_date="2026-11-02", market_risk="weak")
    assert c["stop_price"] < c["entry_zone"][0] <= c["entry_zone"][1]
    assert c["trigger_verification"] == {"status": "not_verified", "confirmed": None}
    assert c["actual_status"].startswith("盘中触发未核验")
    assert all(g["passed"] is None for g in c["conditions"][1:])
    assert c["stop"]["t_plus_one"] is True
    assert c["planned_risk_pct"] == pytest.approx((1 - c['stop_price'] / c['entry_zone'][1]) * 100)
    cancel = c["cancellation"]
    assert cancel["intraday_at_or_below"] == c["stop_price"]
    assert cancel["opening_grace"] == {"above_price": c["stop_price"],
                                       "below_price": c["trigger"]["support"],
                                       "reclaim_price": c["trigger"]["support"],
                                       "deadline": "09:45"}
    assert cancel["invalidation_precedes_grace"] is True
    assert cancel["same_day_restart"] is False


def test_screen_does_not_forward_fill_missing_prices():
    rows = bars(40)
    index = [r["date"] for r in rows]
    p = pd.DataFrame({"sz000001": [10 * 1.02 ** i for i in range(40)]}, index=index)
    assert bool(screen_prices(p).loc["sz000001", "eligible"])
    p.iloc[-3, 0] = None
    assert not bool(screen_prices(p).loc["sz000001", "eligible"])


def test_historical_outcomes_remain_null_until_mature_and_keep_losses():
    dates = pd.bdate_range("2026-07-01", periods=60).strftime("%Y-%m-%d").tolist()
    # Trending then collapse: success-only selection would incorrectly drop it.
    values = [10 * 1.03 ** i for i in range(35)] + [27 * .94 ** i for i in range(25)]
    prices = pd.DataFrame({"sz000001": values}, index=dates)
    result = historical_replay(prices, {}, dates[-1], {})
    assert any(e["return_10d"] < 0 for e in result["events"] if e["return_10d"] is not None)
    short = historical_replay(prices.iloc[:27], {}, dates[26], {})
    assert short["events"]
    assert all(e["return_10d"] is None for e in short["events"])
    assert all(e["date"] <= dates[26] for e in short["events"])


def test_historical_replay_enforces_as_of_even_with_future_input_rows():
    dates = pd.bdate_range("2026-07-01", periods=60).strftime("%Y-%m-%d").tolist()
    prices = pd.DataFrame({"sz000001": [10 * 1.03 ** i for i in range(60)]}, index=dates)
    cutoff = dates[26]
    expected = historical_replay(prices.loc[:cutoff], {}, cutoff, {})
    actual = historical_replay(prices, {}, cutoff, {})
    assert actual == expected
    assert actual["events"]
    assert all(event["return_10d"] is None for event in actual["events"])
