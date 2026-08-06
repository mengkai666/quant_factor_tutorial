import pandas as pd

from data_sources.limit_pool_reconciliation import reconcile_limit_pool


def _frame(codes, counts=None):
    counts = counts or [1] * len(codes)
    return pd.DataFrame({"code": codes, "limit_count": counts})


def test_matching_sources_are_consistent():
    result = reconcile_limit_pool(
        _frame(["sh600000", "sz000001"], [2, 1]),
        _frame(["sh600000", "sz000001"], [2, 1]),
    )

    assert result.status == "match"
    assert result.count_delta == 0
    assert result.message == ""


def test_count_drift_over_five_percent_is_partial():
    primary = _frame([f"sz{index:06d}" for index in range(1, 101)])
    secondary = _frame([f"sz{index:06d}" for index in range(1, 111)])

    result = reconcile_limit_pool(primary, secondary)

    assert result.status == "partial"
    assert result.count_delta == 10
    assert "count drift" in result.message


def test_high_board_code_or_height_drift_is_partial():
    result = reconcile_limit_pool(
        _frame(["sz000001", "sz000002"], [3, 1]),
        _frame(["sz000001", "sz000003"], [2, 1]),
    )

    assert result.status == "partial"
    assert "high-board" in result.message


def test_missing_secondary_is_unavailable_not_zero():
    result = reconcile_limit_pool(_frame(["sh600000"]), pd.DataFrame())

    assert result.status == "unavailable"
    assert "secondary" in result.message
