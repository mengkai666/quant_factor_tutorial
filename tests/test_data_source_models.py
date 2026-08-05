from datetime import datetime, timezone

import pytest

from data_sources.fetch_status import FetchStatusStore
from data_sources.models import FetchResult, FetchStatus, normalize_code


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
