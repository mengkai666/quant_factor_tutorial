from datetime import datetime

import pandas as pd
import pytest

from data_sources.calendar_provider import CalendarProvider
from data_sources.fetch_status import FetchStatusStore
from data_sources.models import FetchStatus
from data_sources.universe_provider import UNIVERSE_COLUMNS, UniverseProvider


def _source_frames():
    return {
        "SH": pd.DataFrame({"代码": ["600000"], "名称": ["浦发银行"], "上市日期": ["1999-11-10"]}),
        "SZ": pd.DataFrame({"代码": ["000001"], "名称": ["平安银行"], "上市日期": ["1991-04-03"]}),
        "BJ": pd.DataFrame({"代码": ["920117"], "名称": ["国航远洋"], "上市日期": ["2022-12-15"]}),
    }


def test_calendar_provider_filters_range_and_finds_latest_closed_day():
    source = lambda: pd.DataFrame({
        "trade_date": pd.to_datetime(["2026-08-03", "2026-08-04", "2026-08-05"])
    })
    provider = CalendarProvider(source=source, close_hour=16)

    assert provider.trading_days("2026-08-04", "2026-08-05") == ["2026-08-04", "2026-08-05"]
    assert provider.latest_closed_day(datetime(2026, 8, 5, 15, 0)) == "2026-08-04"
    assert provider.latest_closed_day(datetime(2026, 8, 5, 17, 0)) == "2026-08-05"


def test_universe_fetch_contains_all_three_exchanges_and_canonical_schema(tmp_path):
    status = FetchStatusStore(tmp_path / "status.csv")
    provider = UniverseProvider(sources={k: (lambda frame=v: frame) for k, v in _source_frames().items()},
                                status_store=status, now=lambda: datetime(2026, 8, 5, 17, 0))

    result = provider.fetch()

    assert result.status is FetchStatus.SUCCESS
    assert result.expected_count == result.actual_count == 3
    assert result.data.columns.tolist() == UNIVERSE_COLUMNS
    assert set(result.data["code"]) == {"sh600000", "sz000001", "bj920117"}
    assert set(result.data["exchange"]) == {"SH", "SZ", "BJ"}
    assert set(result.data["list_status"]) == {"listed"}


@pytest.mark.parametrize("frame", [
    pd.DataFrame({"代码": [None], "名称": ["坏代码"]}),
    pd.DataFrame({"代码": ["000001"], "名称": ["错误市场"]}),
])
def test_universe_rejects_empty_or_wrong_exchange_codes(frame):
    provider = UniverseProvider(
        sources={"SH": lambda: frame, "SZ": lambda: _source_frames()["SZ"],
                 "BJ": lambda: _source_frames()["BJ"]},
        retry=1,
    )

    result = provider.fetch()

    assert result.status is FetchStatus.FAILED
    assert "SH" in result.message


def test_universe_refresh_preserves_prior_delisted_rows_and_replaces_atomically(tmp_path):
    path = tmp_path / "stock_universe.csv"
    prior = pd.DataFrame([{
        "code": "sh600001", "raw_code": "600001", "exchange": "SH", "name": "退市样本",
        "list_date": "1990-01-01", "delist_date": "2025-01-01", "list_status": "delisted",
        "industry": "", "source": "prior", "updated_at": "2025-01-01T00:00:00",
    }], columns=UNIVERSE_COLUMNS)
    prior.to_csv(path, index=False)
    provider = UniverseProvider(
        sources={k: (lambda frame=v: frame) for k, v in _source_frames().items()},
        status_store=FetchStatusStore(tmp_path / "status.csv"),
        now=lambda: datetime(2026, 8, 5, 17, 0),
    )

    refreshed = provider.refresh(path)

    assert set(refreshed["code"]) == {"sh600000", "sz000001", "bj920117", "sh600001"}
    old = refreshed.set_index("code").loc["sh600001"]
    assert old["list_status"] == "delisted"
    assert old["delist_date"] == "2025-01-01"
    assert not list(tmp_path.glob("*.tmp"))


def test_universe_fetch_is_failed_when_any_exchange_source_fails(tmp_path):
    sources = {k: (lambda frame=v: frame) for k, v in _source_frames().items()}
    sources["BJ"] = lambda: (_ for _ in ()).throw(TimeoutError("BJ timeout"))
    provider = UniverseProvider(sources=sources, status_store=FetchStatusStore(tmp_path / "status.csv"))

    result = provider.fetch()

    assert result.status is FetchStatus.FAILED
    assert "BJ" in result.message
    assert result.data is None


def test_default_sh_source_combines_main_board_and_star_market(monkeypatch):
    calls = []

    class AK:
        @staticmethod
        def stock_info_sh_name_code(symbol):
            calls.append(symbol)
            code = "600000" if symbol == "主板A股" else "688001"
            return pd.DataFrame({"证券代码": [code], "证券简称": [symbol], "上市日期": ["2020-01-01"]})

    monkeypatch.setitem(__import__("sys").modules, "akshare", AK)
    frame = UniverseProvider._default_sources()["SH"]()
    assert calls == ["主板A股", "科创板"]
    assert set(frame["证券代码"]) == {"600000", "688001"}


def test_universe_provider_retries_transient_exchange_failure(tmp_path):
    frames = _source_frames()
    attempts = {"BJ": 0}

    def bj():
        attempts["BJ"] += 1
        if attempts["BJ"] < 3:
            raise ConnectionError("transient SSL EOF")
        return frames["BJ"]

    sources = {"SH": lambda: frames["SH"], "SZ": lambda: frames["SZ"], "BJ": bj}
    result = UniverseProvider(sources=sources, retry=3, retry_delay=0).fetch()
    assert result.status is FetchStatus.SUCCESS
    assert attempts["BJ"] == 3


def test_universe_refresh_rejects_silent_exchange_shrink_and_preserves_cache(tmp_path):
    path = tmp_path / "stock_universe.csv"
    prior = pd.DataFrame([
        ["sh600000", "600000", "SH", "浦发银行", "1999-11-10", "", "listed", "", "prior", "now"],
        ["sh600001", "600001", "SH", "样本银行", "1999-11-10", "", "listed", "", "prior", "now"],
        ["sz000001", "000001", "SZ", "平安银行", "1991-04-03", "", "listed", "", "prior", "now"],
        ["bj920117", "920117", "BJ", "国航远洋", "2022-12-15", "", "listed", "", "prior", "now"],
    ], columns=UNIVERSE_COLUMNS)
    prior.to_csv(path, index=False)
    status = FetchStatusStore(tmp_path / "status.csv")
    provider = UniverseProvider(
        sources={k: (lambda frame=v: frame) for k, v in _source_frames().items()},
        status_store=status,
        now=lambda: datetime(2026, 8, 5, 17, 0),
        min_refresh_ratio=0.9,
    )

    with pytest.raises(RuntimeError, match="SH universe shrank"):
        provider.refresh(path)

    assert set(pd.read_csv(path, dtype=str)["code"]) == set(prior["code"])
    latest = status.latest("2026-08-05", "universe", "SH,SZ,BJ")
    assert latest is not None
    assert latest.status is FetchStatus.FAILED
