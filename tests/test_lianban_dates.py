from datetime import datetime

import pandas as pd

import lianban_analysis
from data_sources.calendar_provider import CalendarProvider
from data_sources.models import FetchResult


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


def test_calendar_provider_reuses_last_valid_snapshot_when_source_fails(tmp_path):
    cache_path = tmp_path / "trade_calendar.csv"
    valid = CalendarProvider(
        source=lambda: pd.DataFrame({"trade_date": ["2026-08-04", "2026-08-05"]}),
        cache_path=cache_path,
    )
    assert valid.trading_days("2026-08-01", "2026-08-05") == ["2026-08-04", "2026-08-05"]

    degraded = CalendarProvider(
        source=lambda: (_ for _ in ()).throw(TimeoutError("calendar down")),
        cache_path=cache_path,
    )
    assert degraded.trading_days("2026-08-01", "2026-08-05") == ["2026-08-04", "2026-08-05"]
    assert degraded.last_result.status.value == "stale"


def test_limit_pool_cache_writes_provenance_sidecar(monkeypatch, tmp_path):
    cache = tmp_path / "limit_pool.csv"
    meta = tmp_path / "limit_pool_meta.csv"
    monkeypatch.setattr(lianban_analysis, "CACHE_FILE", str(cache))
    monkeypatch.setattr(lianban_analysis, "LIMIT_POOL_META_CACHE", str(meta))
    monkeypatch.setattr(lianban_analysis, "_load_cache", lambda: ({}, {}))
    monkeypatch.setattr(lianban_analysis, "get_trading_dates", lambda _days: ["20260805"])

    class Provider:
        def fetch_history(self, dates):
            data = pd.DataFrame([
                {"date": "2026-08-05", "pool_type": "ZT", "code": "sz000001",
                 "name": "样本", "limit_count": 2, "source": "fixture"},
            ])
            return {"2026-08-05": FetchResult.success(
                dataset="limit_pool", date="2026-08-05", source="ZT:fixture|DT:fixture",
                expected_count=1, actual_count=1, data=data,
            )}

    lianban_analysis.fetch_zt_pool_data(1, provider=Provider())

    saved = pd.read_csv(meta, dtype=str)
    assert set(saved["pool_type"]) == {"ZT", "DT"}
    assert set(saved["status"]) == {"success"}
    assert saved.loc[saved["pool_type"] == "ZT", "actual_count"].iloc[0] == "1"
