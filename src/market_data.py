from __future__ import annotations

import pandas as pd


def load_analysis_price_view(prices: pd.DataFrame) -> pd.DataFrame:
    """Return a legacy-compatible in-memory view whose `close` is explicitly qfq.

    The alias is never persisted. It exists only while legacy analysis functions are
    being extracted into the new pipeline.
    """
    if "close_qfq" not in prices.columns:
        raise ValueError("canonical price data requires close_qfq")
    view = prices.copy()
    view["close"] = pd.to_numeric(view["close_qfq"], errors="coerce")
    return view


def compute_advance_decline(prices: pd.DataFrame) -> pd.DataFrame:
    """Compute daily breadth from unadjusted prices of actually traded stocks."""
    required = {"date", "code", "close_raw", "trade_status"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"price data missing columns: {sorted(missing)}")
    traded = prices.loc[prices["trade_status"] == "traded", ["date", "code", "close_raw"]].copy()
    traded["close_raw"] = pd.to_numeric(traded["close_raw"], errors="coerce")
    traded = traded.dropna(subset=["close_raw"]).sort_values(["code", "date"])
    traded["previous_raw"] = traded.groupby("code")["close_raw"].shift(1)
    traded = traded.dropna(subset=["previous_raw"])
    traded["direction"] = 0
    traded.loc[traded["close_raw"] > traded["previous_raw"], "direction"] = 1
    traded.loc[traded["close_raw"] < traded["previous_raw"], "direction"] = -1
    rows = []
    for date, day in traded.groupby("date", sort=True):
        rows.append({
            "date": date,
            "up": int((day["direction"] > 0).sum()),
            "down": int((day["direction"] < 0).sum()),
            "flat": int((day["direction"] == 0).sum()),
            "eligible": int(len(day)),
        })
    return pd.DataFrame(rows, columns=["date", "up", "down", "flat", "eligible"])


def compute_period_returns(prices: pd.DataFrame, periods=(5, 10, 20, 60)) -> pd.DataFrame:
    """Compute qfq returns without carrying stale securities into the latest day."""
    required = {"date", "code", "close_qfq", "trade_status"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"price data missing columns: {sorted(missing)}")
    frame = prices.copy()
    frame["date"] = frame["date"].astype(str)
    frame["close_qfq"] = pd.to_numeric(frame["close_qfq"], errors="coerce")
    dates = sorted(frame["date"].unique())
    if not dates:
        return pd.DataFrame(columns=["code", "date", "close_qfq"])
    latest_date = dates[-1]
    latest = frame[(frame["date"] == latest_date) & (frame["trade_status"] == "traded")]
    latest = latest.dropna(subset=["close_qfq"]).drop_duplicates("code", keep="last").set_index("code")
    result = pd.DataFrame({
        "code": latest.index,
        "date": latest_date,
        "close_qfq": latest["close_qfq"].values,
    }).set_index("code")
    for period in periods:
        col = f"{period}日涨幅"
        result[col] = pd.NA
        if len(dates) <= period:
            continue
        base_date = dates[-(period + 1)]
        base = frame[(frame["date"] == base_date) & (frame["trade_status"] == "traded")]
        base = base.dropna(subset=["close_qfq"]).drop_duplicates("code", keep="last").set_index("code")
        common = result.index.intersection(base.index)
        values = (result.loc[common, "close_qfq"].astype(float) /
                  base.loc[common, "close_qfq"].astype(float) - 1.0) * 100.0
        result.loc[common, col] = values
        result[col] = pd.to_numeric(result[col], errors="coerce")
    return result.reset_index()
