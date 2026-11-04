import numpy as np
import pandas as pd

from abnormal_case_math import add_deviation_windows, cumulative_deviation, fixed_forward_return, rounded_limit


def test_formula_subtracts_returns_not_relative_price_ratio():
    assert abs(cumulative_deviation(200, 100, 90, 100) - 110) < 1e-9
    assert round(cumulative_deviation(61.44, 28.98, 3408.52, 3655.56), 2) == 118.77


def test_ten_sessions_requires_ten_intervals_and_searches_short_windows():
    df = pd.DataFrame({"code": ["sz301313"] * 12, "date_idx": range(12),
                       "date": pd.bdate_range("2026-01-01", periods=12),
                       "close_used": [100, 50] + [100] * 8 + [200, 200], "index_close": [100] * 12})
    out = add_deviation_windows(df)
    assert np.isnan(out.iloc[9].fixed_dev10_pct)
    assert out.iloc[10].fixed_dev10_pct == 100
    assert out.iloc[10].dev10_pct == 300
    assert out.iloc[10].dev10_lag == 9


def test_missing_calendar_session_does_not_compress_window():
    df = pd.DataFrame({"code": ["a"] * 3, "date_idx": [0, 2, 3],
                       "date": pd.to_datetime(["2026-01-01", "2026-01-05", "2026-01-06"]),
                       "close_used": [10, 20, 22], "index_close": [100] * 3})
    out = add_deviation_windows(df)
    assert np.isnan(out.iloc[1].dev10_pct)
    assert abs(out.iloc[2].dev10_pct - 10) < 1e-8


def test_partial_forward_window_not_published_as_ten_days():
    df = pd.DataFrame({"date_idx": range(6), "close_used": [10, 11, 12, 13, 14, 15]})
    assert fixed_forward_return(df, 0, 5) == 50
    assert np.isnan(fixed_forward_return(df, 0, 10))


def test_one_cent_below_limit_is_not_closed_limit():
    assert rounded_limit(8.13) == 9.76
    assert rounded_limit(8.27) == 9.92
    assert 9.75 < rounded_limit(8.13)
