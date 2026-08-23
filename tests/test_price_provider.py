from datetime import datetime

import pandas as pd
import pytest

from data_sources.fetch_status import FetchStatusStore
from data_sources.models import FetchStatus
from data_sources.price_provider import PRICE_COLUMNS, PriceProvider
from data_sources.price_provider import parse_tencent_kline_payload, parse_tencent_qfq_payload
from market_data import compute_advance_decline, compute_period_returns


def _prices():
    return pd.DataFrame([
        ["2026-08-04", "sh600000", 10.0, 5.0, "traded", "raw", "qfq", "now"],
        ["2026-08-05", "sh600000", 11.0, 5.5, "traded", "raw", "qfq", "now"],
        # raw 上涨、qfq 下跌：用于证明 A/D 不会误读 qfq
        ["2026-08-04", "sz000001", 20.0, 20.0, "traded", "raw", "qfq", "now"],
        ["2026-08-05", "sz000001", 21.0, 19.0, "traded", "raw", "qfq", "now"],
        # 停牌：即使有昨收也不进当日 A/D
        ["2026-08-04", "bj920117", 8.0, 8.0, "traded", "raw", "qfq", "now"],
        ["2026-08-05", "bj920117", 8.0, 8.0, "suspended", "raw", "qfq", "now"],
    ], columns=PRICE_COLUMNS)


def test_advance_decline_uses_raw_close_and_excludes_suspended_rows():
    result = compute_advance_decline(_prices())
    row = result.set_index("date").loc["2026-08-05"]

    assert row["up"] == 2
    assert row["down"] == 0
    assert row["flat"] == 0
    assert row["eligible"] == 2


def test_period_returns_use_qfq_and_do_not_ffill_retired_codes():
    prices = _prices()
    # 该代码只在旧日存在，不得通过全表 ffill 混进最新排行
    old = pd.DataFrame([["2026-08-04", "sh600001", 3.0, 3.0, "traded", "raw", "qfq", "now"]],
                       columns=PRICE_COLUMNS)
    returns = compute_period_returns(pd.concat([prices, old]), periods=[1])
    got = returns.set_index("code")

    assert got.loc["sh600000", "1日涨幅"] == pytest.approx(10.0)
    assert got.loc["sz000001", "1日涨幅"] == pytest.approx(-5.0)
    assert "bj920117" not in got.index  # 最新日停牌，不参与动量排行
    assert "sh600001" not in got.index


def test_price_provider_normalizes_injected_fetches_and_records_partial(tmp_path):
    universe = pd.DataFrame({"code": ["sh600000", "sz000001", "bj920117"]})

    def fetcher(code, start, end):
        if code == "bj920117":
            return pd.DataFrame()  # 模拟单股临时失败/空响应
        return pd.DataFrame({
            "date": [start, end], "close_raw": [10.0, 11.0], "close_qfq": [5.0, 5.5],
            "trade_status": ["traded", "traded"], "source_raw": ["fixture"] * 2,
            "source_qfq": ["fixture"] * 2,
        })

    status = FetchStatusStore(tmp_path / "status.csv")
    provider = PriceProvider(fetcher=fetcher, status_store=status,
                             now=lambda: datetime(2026, 8, 5, 17, 0))
    result = provider.fetch_range(universe, ["2026-08-04", "2026-08-05"])

    assert result.status is FetchStatus.PARTIAL
    assert result.expected_count == 3
    assert result.actual_count == 2
    assert result.data.columns.tolist() == PRICE_COLUMNS
    assert set(result.data.loc[result.data["trade_status"] == "traded", "code"]) == {"sh600000", "sz000001"}
    assert set(result.data.loc[result.data["trade_status"] == "suspended", "code"]) == {"bj920117"}


def test_price_provider_rejects_ambiguous_legacy_close_column(tmp_path):
    universe = pd.DataFrame({"code": ["sh600000"]})
    provider = PriceProvider(fetcher=lambda *_: pd.DataFrame({
        "date": ["2026-08-05"], "close": [10.0]
    }), status_store=FetchStatusStore(tmp_path / "status.csv"))

    result = provider.fetch_range(universe, ["2026-08-05"])

    assert result.status is FetchStatus.FAILED
    assert "close_raw" in result.message


def test_parse_tencent_payload_requires_raw_and_qfq_series():
    raw = {"data": {"sh600000": {"day": [["2026-08-05", "9", "10", "11", "8", "100"]]}}}
    qfq = {"data": {"sh600000": {"qfqday": [["2026-08-05", "4", "5", "6", "3", "100"]]}}}
    frame = parse_tencent_kline_payload("sh600000", raw, qfq)
    assert frame.loc[0, "close_raw"] == 10.0
    assert frame.loc[0, "close_qfq"] == 5.0
    with pytest.raises(ValueError, match="qfqday"):
        parse_tencent_kline_payload("sh600000", raw, raw)


def test_parse_tencent_payload_accepts_long_range_rows_with_amount_field():
    raw = {"data": {"sh600000": {"day": [
        ["2026-08-05", "9", "10", "11", "8", "100", "1000.50"]
    ]}}}
    qfq = {"data": {"sh600000": {"qfqday": [
        ["2026-08-05", "4", "5", "6", "3", "100", "1000.50"]
    ]}}}
    frame = parse_tencent_kline_payload("sh600000", raw, qfq)
    assert frame[["date", "close_raw", "close_qfq"]].to_dict("records") == [
        {"date": "2026-08-05", "close_raw": 10.0, "close_qfq": 5.0}
    ]


def test_price_provider_materializes_not_listed_and_suspended_days(tmp_path):
    universe = pd.DataFrame({
        "code": ["bj920117", "sh600000"],
        "list_date": ["2026-08-05", "1999-01-01"],
    })

    def fetcher(code, _start, _end):
        if code == "bj920117":
            dates = ["2026-08-05"]
        else:
            dates = ["2026-08-04"]  # 08-05 无成交，视为停牌
        return pd.DataFrame({
            "date": dates, "close_raw": [10.0], "close_qfq": [10.0],
            "trade_status": ["traded"], "source_raw": ["fixture"],
            "source_qfq": ["fixture"],
        })

    result = PriceProvider(fetcher=fetcher, status_store=FetchStatusStore(tmp_path / "s.csv")).fetch_range(
        universe, ["2026-08-04", "2026-08-05"]
    )
    got = result.data.set_index(["code", "date"])
    assert got.loc[("bj920117", "2026-08-04"), "trade_status"] == "not_listed"
    assert got.loc[("sh600000", "2026-08-05"), "trade_status"] == "suspended"
    assert pd.isna(got.loc[("sh600000", "2026-08-05"), "close_raw"])


def test_rebuild_resumes_from_valid_checkpoint_without_refetching(tmp_path):
    universe = pd.DataFrame({"code": ["sh600000", "sz000001"]})
    candidate = tmp_path / "candidate.csv"
    existing = pd.DataFrame([
        ["2026-08-05", "sh600000", 10.0, 5.0, "traded", "fixture", "fixture", "now"],
    ], columns=PRICE_COLUMNS)
    existing.to_csv(candidate, index=False)
    calls = []

    def fetcher(code, start, end):
        calls.append(code)
        return pd.DataFrame({
            "date": [end], "close_raw": [20.0], "close_qfq": [10.0],
            "trade_status": ["traded"], "source_raw": ["fixture"],
            "source_qfq": ["fixture"],
        })

    result = PriceProvider(fetcher=fetcher, max_workers=1).rebuild(
        universe, ["2026-08-05"], candidate, batch_size=1, resume=True
    )
    assert calls == ["sz000001"]
    assert result.status is FetchStatus.SUCCESS
    assert set(pd.read_csv(candidate)["code"]) == {"sh600000", "sz000001"}


def test_rebuild_records_aggregate_status_after_all_batches(tmp_path):
    universe = pd.DataFrame({"code": ["sh600000", "sz000001"]})
    status = FetchStatusStore(tmp_path / "status.csv")

    def fetcher(code, _start, end):
        return pd.DataFrame({
            "date": [end], "close_raw": [10.0], "close_qfq": [10.0],
            "trade_status": ["traded"], "source_raw": ["fixture"],
            "source_qfq": ["fixture"],
        })

    result = PriceProvider(fetcher=fetcher, status_store=status, max_workers=1).rebuild(
        universe, ["2026-08-05"], tmp_path / "candidate.csv", batch_size=1, resume=True
    )

    latest = status.latest("2026-08-05", "prices", "SH,SZ,BJ")
    assert result.status is FetchStatus.SUCCESS
    assert latest is not None
    assert latest.status is FetchStatus.SUCCESS
    assert latest.expected_count == 2
    assert latest.actual_count == 2


def test_rebuild_refetches_checkpoint_code_with_only_suspended_placeholders(tmp_path):
    universe = pd.DataFrame({"code": ["sh600000"], "list_date": ["1999-01-01"]})
    candidate = tmp_path / "candidate.csv"
    pd.DataFrame([[
        "2026-08-05", "sh600000", pd.NA, pd.NA, "suspended", "", "", "now"
    ]], columns=PRICE_COLUMNS).to_csv(candidate, index=False)
    calls = []

    def fetcher(code, _start, end):
        calls.append(code)
        return pd.DataFrame({
            "date": [end], "close_raw": [10.0], "close_qfq": [10.0],
            "trade_status": ["traded"], "source_raw": ["fixture"],
            "source_qfq": ["fixture"],
        })

    PriceProvider(fetcher=fetcher, max_workers=1).rebuild(
        universe, ["2026-08-05"], candidate, batch_size=1, resume=True
    )
    assert calls == ["sh600000"]
    assert pd.read_csv(candidate).iloc[0]["trade_status"] == "traded"


def test_price_provider_retries_one_code_without_losing_other_results(tmp_path):
    universe = pd.DataFrame({"code": ["sh600000", "sz000001"]})
    attempts = {"sh600000": 0, "sz000001": 0}

    def fetcher(code, _start, end):
        attempts[code] += 1
        if code == "sh600000" and attempts[code] < 3:
            raise TimeoutError("transient")
        return pd.DataFrame({
            "date": [end], "close_raw": [10.0], "close_qfq": [10.0],
            "trade_status": ["traded"], "source_raw": ["fixture"],
            "source_qfq": ["fixture"],
        })

    result = PriceProvider(fetcher=fetcher, max_workers=2, retry=3, retry_delay=0).fetch_range(
        universe, ["2026-08-05"]
    )
    assert result.status is FetchStatus.SUCCESS
    assert attempts == {"sh600000": 3, "sz000001": 1}
    assert set(result.data.loc[result.data.trade_status == "traded", "code"]) == set(attempts)


def test_default_fetcher_falls_back_to_sina_when_tencent_is_limited(monkeypatch):
    calls = []

    def tencent(code, start, end):
        calls.append(("tencent", code))
        raise RuntimeError("HTTP 501")

    def sina(code, start, end):
        calls.append(("sina", code))
        return pd.DataFrame({
            "date": [end], "close_raw": [10.0], "close_qfq": [9.0],
            "trade_status": ["traded"], "source_raw": ["sina_raw"],
            "source_qfq": ["sina_qfq"],
        })

    monkeypatch.setattr(PriceProvider, "_tencent_fetcher", staticmethod(tencent))
    monkeypatch.setattr(PriceProvider, "_sina_fetcher", staticmethod(sina))
    result = PriceProvider._default_fetcher("sh600000", "2026-08-04", "2026-08-05")
    assert calls == [("tencent", "sh600000"), ("sina", "sh600000")]
    assert result.loc[0, "source_qfq"] == "sina_qfq"


def test_parse_tencent_qfq_payload_keeps_raw_close_empty():
    payload = {"data": {"sh600000": {"qfqday": [
        ["2026-08-07", "4", "5", "6", "3", "100"],
        ["2026-08-10", "5", "5.5", "6", "4", "120"],
    ]}}}

    frame = parse_tencent_qfq_payload("sh600000", payload)

    assert frame["close_raw"].isna().all()
    assert frame["close_qfq"].tolist() == [5.0, 5.5]
    assert frame["source_raw"].eq("").all()
    assert frame["source_qfq"].eq("tencent_qfq").all()


def test_fetch_qfq_range_uses_qfq_fetcher_without_calling_full_fetcher(tmp_path):
    full_calls = []
    qfq_calls = []

    def full_fetcher(*args):
        full_calls.append(args)
        raise AssertionError("qfq-only repair must not fetch raw prices again")

    def qfq_fetcher(code, start, end):
        qfq_calls.append((code, start, end))
        return pd.DataFrame({
            "date": [start, end],
            "close_raw": [pd.NA, pd.NA],
            "close_qfq": [10.0, 11.0],
            "trade_status": ["traded", "traded"],
            "source_raw": ["", ""],
            "source_qfq": ["fixture_qfq", "fixture_qfq"],
        })

    provider = PriceProvider(
        fetcher=full_fetcher,
        qfq_fetcher=qfq_fetcher,
        status_store=FetchStatusStore(tmp_path / "status.csv"),
        max_workers=1,
    )
    result = provider.fetch_qfq_range(
        pd.DataFrame({"code": ["sh600000"]}),
        ["2026-08-07", "2026-08-10"],
    )

    assert result.status is FetchStatus.SUCCESS
    assert full_calls == []
    assert qfq_calls == [("sh600000", "2026-08-07", "2026-08-10")]
    assert result.data["close_raw"].isna().all()
    assert result.data["close_qfq"].tolist() == [10.0, 11.0]


def test_default_qfq_fetcher_falls_back_to_akshare_when_tencent_fails(monkeypatch):
    calls = []

    def tencent(code, start, end):
        calls.append(("tencent", code))
        raise RuntimeError("temporary failure")

    def akshare(code, start, end):
        calls.append(("akshare", code))
        return pd.DataFrame({
            "date": [end], "close_raw": [pd.NA], "close_qfq": [9.0],
            "trade_status": ["traded"], "source_raw": [""],
            "source_qfq": ["akshare_qfq"],
        })

    monkeypatch.setattr(PriceProvider, "_tencent_qfq_fetcher", staticmethod(tencent))
    monkeypatch.setattr(PriceProvider, "_sina_qfq_fetcher", staticmethod(akshare))

    frame = PriceProvider._default_qfq_fetcher(
        "sh600000", "2026-08-07", "2026-08-10",
    )

    assert calls == [("tencent", "sh600000"), ("akshare", "sh600000")]
    assert frame.loc[0, "source_qfq"] == "akshare_qfq"
