import pandas as pd

from data_sources.limit_pool_metrics import build_ladder_review


def _pool(codes, heights):
    return pd.DataFrame({"代码": codes, "名称": codes, "连板数": heights})


def test_ladder_review_calculates_distribution_promotions_and_breaks():
    history = {
        "20260804": _pool(["000001", "000002", "000003", "000004"], [1, 1, 2, 4]),
        "20260805": _pool(["000001", "000003", "000005"], [2, 3, 1]),
    }

    result = build_ladder_review(history, {"20260805": pd.DataFrame()})

    assert result["date"] == "20260805"
    assert result["distribution"] == {1: 1, 2: 1, 3: 1}
    assert result["promotions"][1] == {"eligible": 2, "advanced": 1, "rate": 0.5}
    assert result["promotions"][2] == {"eligible": 1, "advanced": 1, "rate": 1.0}
    assert result["high_break_count"] == 1
    assert result["missing_heights"] == []


def test_ladder_review_handles_single_day_without_fake_promotions():
    result = build_ladder_review(
        {"20260805": _pool(["000001"], [1])},
        {"20260805": pd.DataFrame({"代码": ["600000"]})},
    )

    assert result["promotions"] == {}
    assert result["dt_count"] == 1


def test_ladder_review_accepts_hyphenated_date_keys_for_both_days():
    history = {
        "2026-08-04": _pool(["000001", "000002"], [1, 3]),
        "2026-08-05": _pool(["000001"], [2]),
    }
    down_history = {
        "2026-08-05": pd.DataFrame({"代码": ["600000", "600001"]}),
    }

    result = build_ladder_review(history, down_history)

    assert result["promotions"][1]["advanced"] == 1
    assert result["high_break_count"] == 1
    assert result["dt_count"] == 2
