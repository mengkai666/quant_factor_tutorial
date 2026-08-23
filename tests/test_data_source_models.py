from datetime import datetime, timezone

import pytest

from data_sources.fetch_status import FetchStatusStore
from data_sources.models import FetchResult, FetchStatus, normalize_code
from data_sources.run_context import run_context


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("600000", "sh600000"),
        ("600000.SH", "sh600000"),
        ("sh.600000", "sh600000"),
        ("SZ000001", "sz000001"),
        ("000001.SZ", "sz000001"),
        ("920117.BJ", "bj920117"),
        ("920117BJ", "bj920117"),
        ("bj.920117", "bj920117"),
        ("430047", "bj430047"),
    ],
)
def test_normalize_code_supports_sh_sz_bj(value, expected):
    assert normalize_code(value) == expected


@pytest.mark.parametrize("value", ["", None, "12345", "hk00700", "ABCDEF"])
def test_normalize_code_rejects_unsupported_identifiers(value):
    with pytest.raises(ValueError):
        normalize_code(value)


def test_fetch_result_zero_is_explicit_not_inferred_from_failure():
    zero = FetchResult.zero(dataset="limit_down", date="2026-08-05", source="fixture")
    failed = FetchResult.failed(
        dataset="limit_down", date="2026-08-05", source="fixture", message="timeout"
    )

    assert zero.status is FetchStatus.ZERO
    assert zero.actual_count == 0
    assert failed.status is FetchStatus.FAILED
    assert failed.actual_count == 0


def test_fetch_status_store_upserts_logical_key_atomically(tmp_path):
    path = tmp_path / "fetch_status.csv"
    store = FetchStatusStore(path)
    started = datetime(2026, 8, 5, 8, 30, tzinfo=timezone.utc)

    store.record(
        FetchResult.success(
            dataset="prices",
            date="2026-08-05",
            source="fixture",
            expected_count=3,
            actual_count=3,
            scope="SH,SZ,BJ",
            started_at=started,
            run_id="run-1",
        )
    )
    store.record(
        FetchResult.failed(
            dataset="prices",
            date="2026-08-05",
            source="fixture-2",
            message="schema drift",
            scope="SH,SZ,BJ",
            run_id="run-2",
        )
    )

    latest = store.latest("2026-08-05", "prices", "SH,SZ,BJ")
    assert latest is not None
    assert latest.status is FetchStatus.FAILED
    assert latest.source == "fixture-2"
    assert latest.run_id == "run-2"
    assert len(store.read()) == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_fetch_status_store_results_filters_datasets_and_restores_status(tmp_path):
    path = tmp_path / "fetch_status.csv"
    store = FetchStatusStore(path)

    store.record(
        FetchResult.failed(
            dataset="limit_pool",
            date="2026-08-04",
            source="fixture",
            message="timeout",
            scope="SH,SZ,BJ",
            run_id="run-limit",
        )
    )
    store.record(
        FetchResult.success(
            dataset="prices",
            date="2026-08-04",
            source="fixture",
            expected_count=3,
            actual_count=3,
            scope="SH,SZ,BJ",
            run_id="run-prices",
        )
    )

    results = store.results(datasets={"limit_pool"})

    assert len(results) == 1
    assert results[0].dataset == "limit_pool"
    assert results[0].date == "2026-08-04"
    assert results[0].status is FetchStatus.FAILED
    assert results[0].run_id == "run-limit"


def test_fetch_status_store_results_tolerates_malformed_log_fields(tmp_path):
    path = tmp_path / "fetch_status.csv"
    path.write_text(
        "date,dataset,scope,status,source,expected_count,actual_count,message,"
        "started_at,finished_at,run_id\n"
        "2026-08-04,limit_pool,all,failed,fixture,bad,,timeout,bad,,run-1\n",
        encoding="utf-8",
    )

    results = FetchStatusStore(path).results()

    assert len(results) == 1
    assert results[0].status is FetchStatus.FAILED
    assert results[0].expected_count == 0
    assert results[0].actual_count == 0
    assert results[0].started_at.tzinfo is not None
    assert results[0].finished_at.tzinfo is not None


def test_fetch_status_store_inherits_run_id_from_store_context(tmp_path):
    path = tmp_path / "fetch_status.csv"
    with run_context("run-context"):
        store = FetchStatusStore(path)
        store.record(
            FetchResult.success(
                dataset="calendar",
                date="2026-08-08",
                source="fixture",
                expected_count=1,
                actual_count=1,
            )
        )

    result = store.latest("2026-08-08", "calendar", "all")
    assert result is not None
    assert result.run_id == "run-context"


def test_fetch_status_store_does_not_override_explicit_run_id(tmp_path):
    path = tmp_path / "fetch_status.csv"
    with run_context("run-context"):
        store = FetchStatusStore(path)
        store.record(
            FetchResult.success(
                dataset="calendar",
                date="2026-08-08",
                source="fixture",
                expected_count=1,
                actual_count=1,
                run_id="run-explicit",
            )
        )

    result = store.latest("2026-08-08", "calendar", "all")
    assert result is not None
    assert result.run_id == "run-explicit"


def test_build_price_matrix_stitches_legacy_history_per_stock_without_backfill():
    import pandas as pd
    from data_sources.price_provider import build_price_matrix

    prices = pd.DataFrame([
        {"date": "2026-07-17", "code": "sh600001", "close_legacy": 10.0, "close_qfq": None},
        {"date": "2026-08-06", "code": "sh600001", "close_legacy": 12.0, "close_qfq": 18.0},
        {"date": "2026-08-07", "code": "sh600001", "close_legacy": 13.0, "close_qfq": 19.5},
        {"date": "2026-07-17", "code": "sz000002", "close_legacy": 20.0, "close_qfq": None},
        {"date": "2026-08-07", "code": "sz000002", "close_legacy": 22.0, "close_qfq": None},
        {"date": "2026-08-07", "code": "bj920001", "close_legacy": None, "close_qfq": 8.0},
    ])

    matrix = build_price_matrix(prices, "qfq", allow_legacy=True)

    assert matrix.loc["2026-07-17", "sh600001"] == 15.0
    assert matrix.loc["2026-08-07", "sh600001"] == 19.5
    assert matrix.loc["2026-07-17", "sz000002"] == 20.0
    assert pd.isna(matrix.loc["2026-07-17", "bj920001"])
def test_normalize_price_frame_memo_returns_independent_copies():
    """归一化带内容哈希记忆; 调用方就地改列不能污染下一次的返回值。"""
    import pandas as pd
    from data_sources import price_provider as pp

    frame = pd.DataFrame([
        {"date": "2026-08-19", "code": "600000", "close_raw": 10.0, "close_qfq": 9.5},
        {"date": "2026-08-20", "code": "600000", "close_raw": 11.0, "close_qfq": 10.4},
    ])
    first = pp.normalize_price_frame(frame)
    baseline = pp._normalize_price_frame_uncached(frame)
    assert first.equals(baseline)

    first.loc[0, "close_raw"] = -1.0
    second = pp.normalize_price_frame(frame)
    assert second.equals(baseline)
    assert second is not first
