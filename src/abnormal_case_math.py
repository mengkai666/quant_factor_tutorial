"""Price-only abnormal-move reconstruction; official announcements remain authoritative."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

import numpy as np
import pandas as pd


def add_deviation_windows(frame: pd.DataFrame, price_col: str = "close_used") -> pd.DataFrame:
    """Subtract cumulative index return, including all 1..N session subwindows.

    date_idx must refer to the shared exchange calendar. A missing session is never
    compressed. This does not reconstruct official post-announcement reset periods.
    """
    out = frame.sort_values(["code", "date_idx"]).copy()
    g = out.groupby("code", sort=False)
    out["dev10_pct"] = np.nan
    out["dev30_pct"] = np.nan
    out["dev10_lag"] = np.nan
    out["dev30_lag"] = np.nan
    out["dev10_base_date"] = pd.NaT
    out["dev30_base_date"] = pd.NaT
    consecutive = g["date_idx"].diff().eq(1)
    # Require every price/index observation in the proposed calendar interval.
    good_edge = consecutive & out[price_col].gt(0) & out["index_close"].gt(0)
    invalid_edges = (~good_edge).groupby(out["code"]).cumsum()
    invalid_group = invalid_edges.groupby(out["code"])
    for lag in range(1, 31):
        prev = g[[price_col, "index_close", "date_idx", "date"]].shift(lag)
        complete = invalid_edges.eq(invalid_group.shift(lag))
        valid = complete & out["date_idx"].sub(prev["date_idx"]).eq(lag) & prev[price_col].gt(0) & prev["index_close"].gt(0)
        ret = (out[price_col] / prev[price_col] - 1) * 100
        ixret = (out["index_close"] / prev["index_close"] - 1) * 100
        deviation = (ret - ixret).where(valid)
        if lag in (10, 30):
            out[f"fixed_dev{lag}_pct"] = deviation
            out[f"stock_ret{lag}_pct"] = ret.where(valid)
            out[f"index_ret{lag}_pct"] = ixret.where(valid)
        for window in (10, 30):
            if lag > window:
                continue
            better = deviation.notna() & (out[f"dev{window}_pct"].isna() | deviation.gt(out[f"dev{window}_pct"]))
            out.loc[better, f"dev{window}_pct"] = deviation[better]
            out.loc[better, f"dev{window}_lag"] = lag
            out.loc[better, f"dev{window}_base_date"] = pd.to_datetime(prev.loc[better, "date"])
    return out


def cumulative_deviation(stock_end: float, stock_base: float, index_end: float, index_base: float) -> float:
    return ((stock_end / stock_base - 1) - (index_end / index_base - 1)) * 100


def rounded_limit(previous_close: float, rate: float = 0.20) -> float:
    return float((Decimal(str(previous_close)) * (1 + Decimal(str(rate)))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def fixed_forward_return(path: pd.DataFrame, event_idx: int, horizon: int, price_col: str = "close_used") -> float:
    """A T+10 result cannot be filled from five available sessions."""
    p = path.set_index("date_idx")[price_col]
    interval = p.reindex(range(event_idx, event_idx + horizon + 1))
    if interval.isna().any() or (interval <= 0).any():
        return float("nan")
    return float((interval.iloc[-1] / interval.iloc[0] - 1) * 100)
