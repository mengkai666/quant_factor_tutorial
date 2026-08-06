import pandas as pd
import pytest

from data_sources.models import FetchResult, FetchStatus
from data_sources.price_provider import PRICE_COLUMNS
from data_sources.quality_gate import DataQualityError, MarketDataQualityGate
from data_sources.universe_provider import UNIVERSE_COLUMNS


def _universe():
    return pd.DataFrame([
        ["sh600000", "600000", "SH", "A", "1999-01-01", "", "listed", "", "fixture", "now"],
        ["sz000001", "000001", "SZ", "B", "1991-01-01", "", "listed", "", "fixture", "now"],
        ["bj920117", "920117", "BJ", "C", "2022-01-01", "", "listed", "", "fixture", "now"],
    ], columns=UNIVERSE_COLUMNS)


def _prices():
    rows = []
    for date, multiplier in (("2026-08-04", 1.0), ("2026-08-05", 1.05)):
        for code, base in (("sh600000", 10), ("sz000001", 20), ("bj920117", 30)):
            rows.append([date, code, base * multiplier, base * multiplier / 2, "traded",
                         "fixture_raw", "fixture_qfq", "now"])
    return pd.DataFrame(rows, columns=PRICE_COLUMNS)


def test_quality_gate_accepts_healthy_sh_sz_bj_data():
    report = MarketDataQualityGate(min_coverage=0.9).validate(
        _universe(), _prices(), target_date="2026-08-05",
        fetch_results=[FetchResult.success(dataset="prices", date="2026-08-05",
                                           source="fixture", expected_count=3, actual_count=3)],
    )
    assert report.ok
    assert report.critical == []


@pytest.mark.parametrize("missing", ["close_raw", "close_qfq", "source_raw", "source_qfq", "trade_status"])
def test_quality_gate_rejects_missing_price_contract_columns(missing):
    with pytest.raises(DataQualityError) as exc:
        MarketDataQualityGate().enforce(_universe(), _prices().drop(columns=[missing]), "2026-08-05")
    assert missing in str(exc.value)


def test_quality_gate_rejects_exchange_or_coverage_gaps():
    with pytest.raises(DataQualityError) as exc:
        MarketDataQualityGate(min_coverage=0.9).enforce(
            _universe().query("exchange != 'BJ'"), _prices().query("code != 'bj920117'"), "2026-08-05"
        )
    assert "BJ" in str(exc.value)

    with pytest.raises(DataQualityError) as exc2:
        MarketDataQualityGate(min_coverage=0.9).enforce(
            _universe(), _prices().query("not (date == '2026-08-05' and code == 'bj920117')"), "2026-08-05"
        )
    assert "coverage" in str(exc2.value).lower()


def test_quality_gate_rejects_duplicates_bad_status_and_nonpositive_prices():
    duplicate = pd.concat([_prices(), _prices().iloc[[0]]], ignore_index=True)
    with pytest.raises(DataQualityError) as exc:
        MarketDataQualityGate().enforce(_universe(), duplicate, "2026-08-05")
    assert "duplicate" in str(exc.value).lower()

    invalid = _prices()
    invalid.loc[0, "trade_status"] = "mystery"
    invalid.loc[1, "close_raw"] = 0
    with pytest.raises(DataQualityError) as exc2:
        MarketDataQualityGate().enforce(_universe(), invalid, "2026-08-05")
    assert "trade_status" in str(exc2.value) or "non-positive" in str(exc2.value)


def test_quality_gate_detects_abnormal_qfq_jump_and_systematic_adjustment_switch():
    abnormal = _prices()
    abnormal.loc[(abnormal.date == "2026-08-05") & (abnormal.code == "sh600000"), "close_qfq"] = 20
    with pytest.raises(DataQualityError) as exc:
        MarketDataQualityGate(max_qfq_abs_return=0.25).enforce(_universe(), abnormal, "2026-08-05")
    assert "qfq jump" in str(exc.value).lower()

    switched = _prices()
    switched.loc[switched.date == "2026-08-05", "close_raw"] *= 10
    with pytest.raises(DataQualityError) as exc2:
        MarketDataQualityGate(systematic_switch_ratio=0.8).enforce(_universe(), switched, "2026-08-05")
    assert "adjustment switch" in str(exc2.value).lower()


def test_quality_gate_allows_normal_beijing_30_percent_limit_move():
    prices = _prices()
    mask = (prices.date == "2026-08-05") & (prices.code == "bj920117")
    prices.loc[mask, ["close_raw", "close_qfq"]] *= 1.30 / 1.05
    report = MarketDataQualityGate(max_qfq_abs_return=0.25).validate(
        _universe(), prices, "2026-08-05"
    )
    assert report.ok


def test_quality_gate_allows_beijing_qfq_rounding_when_raw_is_within_30_percent():
    prices = _prices()
    current = (prices.date == "2026-08-05") & (prices.code == "bj920117")
    previous = (prices.date == "2026-08-04") & (prices.code == "bj920117")
    prices.loc[previous, ["close_raw", "close_qfq"]] = [10.00, 9.99]
    prices.loc[current, ["close_raw", "close_qfq"]] = [13.00, 12.99]
    report = MarketDataQualityGate().validate(_universe(), prices, "2026-08-05")
    assert report.ok


def test_quality_gate_allows_first_five_trading_days_after_listing_but_not_old_stock():
    universe = _universe()
    universe.loc[universe.code == "sz000001", "list_date"] = "2026-08-04"
    prices = _prices()
    current = (prices.date == "2026-08-05") & (prices.code == "sz000001")
    previous = (prices.date == "2026-08-04") & (prices.code == "sz000001")
    prices.loc[previous, ["close_raw", "close_qfq"]] = [10.0, 10.0]
    prices.loc[current, ["close_raw", "close_qfq"]] = [13.0, 13.0]
    assert MarketDataQualityGate().validate(universe, prices, "2026-08-05").ok

    universe.loc[universe.code == "sz000001", "list_date"] = "1991-01-01"
    with pytest.raises(DataQualityError, match="qfq_jump"):
        MarketDataQualityGate().enforce(universe, prices, "2026-08-05")


def test_quality_gate_blocks_critical_fetch_status():
    failed = FetchResult.failed(dataset="limit_pool", date="2026-08-05", source="fixture", message="timeout")
    with pytest.raises(DataQualityError) as exc:
        MarketDataQualityGate().enforce(_universe(), _prices(), "2026-08-05", [failed])
    assert "limit_pool" in str(exc.value)


def test_quality_gate_rejects_trade_status_inconsistent_with_listing_dates():
    universe = _universe()
    universe.loc[universe.code == "sz000001", "list_date"] = "2026-08-05"
    prices = _prices()

    with pytest.raises(DataQualityError, match="listing_status"):
        MarketDataQualityGate().enforce(universe, prices, "2026-08-05")

    universe.loc[universe.code == "sz000001", "list_date"] = "1991-01-01"
    prices.loc[prices.code == "sz000001", "trade_status"] = "not_listed"
    with pytest.raises(DataQualityError, match="listing_status"):
        MarketDataQualityGate().enforce(universe, prices, "2026-08-05")


def test_quality_gate_blocks_not_available_fetch_status():
    unavailable = FetchResult(
        dataset="prices", date="2026-08-05", source="fixture",
        status=FetchStatus.NOT_AVAILABLE, message="source disabled",
    )
    with pytest.raises(DataQualityError, match="fetch_status"):
        MarketDataQualityGate().enforce(_universe(), _prices(), "2026-08-05", [unavailable])


def test_quality_gate_reports_optional_plate_partial_as_warning():
    partial = FetchResult.partial(
        dataset="plates", date="2026-08-05", source="fixture",
        expected_count=3, actual_count=2, message="one code unavailable",
    )

    report = MarketDataQualityGate().validate(
        _universe(), _prices(), "2026-08-05", [partial]
    )

    assert report.ok
    assert [issue.code for issue in report.warnings] == ["fetch_status"]


def test_quality_gate_rejects_malformed_dates_unknown_codes_and_missing_status():
    malformed_date = _prices()
    malformed_date.loc[0, "date"] = "not-a-date"
    with pytest.raises(DataQualityError, match="price_date"):
        MarketDataQualityGate().enforce(_universe(), malformed_date, "2026-08-05")

    unknown_code = pd.concat([_prices(), _prices().iloc[[0]].assign(code="sz999999")], ignore_index=True)
    with pytest.raises(DataQualityError, match="price_code"):
        MarketDataQualityGate().enforce(_universe(), unknown_code, "2026-08-05")

    missing_status = _prices()
    missing_status.loc[0, "trade_status"] = ""
    with pytest.raises(DataQualityError, match="trade_status"):
        MarketDataQualityGate().enforce(_universe(), missing_status, "2026-08-05")



def test_quality_gate_reports_historical_fetch_degradation_as_warning():
    historical = FetchResult.failed(
        dataset="limit_pool", date="2026-08-04", source="provider",
        message="history unavailable",
    )

    report = MarketDataQualityGate().validate(
        _universe(), _prices(), "2026-08-05", fetch_results=[historical]
    )

    assert report.ok
    assert any(issue.code == "historical_fetch_status" for issue in report.warnings)


def test_quality_gate_checks_limit_pool_rows_and_plate_attribution():
    limit_pool = FetchResult.success(
        dataset="limit_pool", date="2026-08-05", source="fixture",
        expected_count=2, actual_count=2,
        data=pd.DataFrame([
            {"pool_type": "ZT", "code": "sz000001", "limit_count": 0},
            {"pool_type": "ZT", "code": "sz000001", "limit_count": 2},
        ]),
    )
    plates = FetchResult.zero(
        dataset="plates", date="2026-08-05", source="fixture",
    )

    report = MarketDataQualityGate().validate(
        _universe(), _prices(), "2026-08-05", [limit_pool, plates]
    )

    assert not report.ok
    assert "limit_pool_duplicate" in {issue.code for issue in report.critical}
    assert "limit_pool_count" in {issue.code for issue in report.critical}
    assert "fetch_status" in {issue.code for issue in report.warnings}
