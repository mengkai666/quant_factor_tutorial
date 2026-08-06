from datetime import datetime

import pandas as pd

import lianban_analysis


class _CalendarAkshare:
    @staticmethod
    def tool_trade_date_hist_sina():
        return pd.DataFrame({"trade_date": ["2026-08-05", "2026-08-06", "2026-08-07"]})


def test_trading_dates_exclude_unclosed_current_day(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "akshare", _CalendarAkshare)

    dates = lianban_analysis.get_trading_dates(
        1, now=datetime(2026, 8, 6, 0, 30)
    )

    assert dates == ["20260805"]


def test_trading_dates_include_current_day_after_close(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "akshare", _CalendarAkshare)

    dates = lianban_analysis.get_trading_dates(
        2, now=datetime(2026, 8, 6, 16, 30)
    )

    assert dates == ["20260806", "20260805"]


def test_cache_future_dates_are_removed_before_selection():
    zt = {
        "20260806": pd.DataFrame({"代码": ["000001"]}),
        "20260805": pd.DataFrame({"代码": ["000002"]}),
    }
    dt = {"20260806": pd.DataFrame(), "20260805": pd.DataFrame()}

    filtered_zt, filtered_dt = lianban_analysis._trim_future_cache(
        zt, dt, "20260805"
    )

    assert set(filtered_zt) == {"20260805"}
    assert set(filtered_dt) == {"20260805"}


def test_fetch_persists_removal_of_future_cache_dates(monkeypatch):
    zt = {
        "20260806": pd.DataFrame({"代码": ["000001"]}),
        "20260805": pd.DataFrame({"代码": ["000002"]}),
    }
    dt = {
        "20260806": pd.DataFrame({"代码": ["600000"]}),
        "20260805": pd.DataFrame({"代码": ["600001"]}),
    }
    saved = []
    monkeypatch.setattr(lianban_analysis, "_load_cache", lambda: (zt, dt))
    monkeypatch.setattr(lianban_analysis, "get_trading_dates", lambda _days: ["20260805"])
    monkeypatch.setattr(lianban_analysis, "_save_cache", lambda zt_data, dt_data: saved.append((zt_data, dt_data)))

    lianban_analysis.fetch_zt_pool_data(1)

    assert len(saved) == 1
    assert set(saved[0][0]) == {"20260805"}
    assert set(saved[0][1]) == {"20260805"}
