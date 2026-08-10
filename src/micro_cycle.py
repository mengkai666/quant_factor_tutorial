from __future__ import annotations

from typing import Any


def _after(rows, date):
    return [row for row in rows if str(row.get("date", "")) > date]


def _event(date="", **values):
    return {"date": date, **values}


def detect_micro_cycle(det: dict, *, daily_limit_counts=None) -> dict:
    rows = sorted((det or {}).get("index_series") or [], key=lambda row: row["date"])
    bottom_date = str(((det or {}).get("bottom") or {}).get("date") or "")
    start = next((i for i, row in enumerate(rows) if row["date"] == bottom_date), -1)
    empty = {
        "status": "探底未完成", "events": {}, "signal_date": "",
        "confirmation_date": "", "full_confirmation_date": "",
        "signal_return": None, "rising_days": 0, "signal_basis": "unavailable",
    }
    if start < 0 or len(rows) - start < 8:
        return empty

    stop_slice = rows[start:start + 3]
    stop = min(stop_slice, key=lambda row: float(row["low"]))
    stop_i = rows.index(stop)
    rebound_slice = rows[stop_i + 1:stop_i + 6]
    if len(rebound_slice) < 3:
        return empty
    high_peak = max(rebound_slice, key=lambda row: float(row["high"]))
    close_peak = max(rebound_slice, key=lambda row: float(row["close"]))
    close_peak_i = rows.index(close_peak)

    after_rebound = rows[close_peak_i + 1:]
    confirmation = next(
        (row for row in after_rebound if float(row["close"]) > float(close_peak["close"])),
        None,
    )
    search_end = rows.index(confirmation) if confirmation else len(rows)
    pullback_rows = rows[close_peak_i + 1:search_end]
    if not pullback_rows:
        return empty
    secondary = min(pullback_rows, key=lambda row: float(row["low"]))
    higher_low = float(secondary["low"]) > float(stop["low"])

    secondary_i = rows.index(secondary)
    retest_rows = rows[secondary_i + 1:search_end]
    retest = min(retest_rows, key=lambda row: float(row["close"])) if retest_rows else secondary
    retest_i = rows.index(retest)
    candidates = rows[retest_i + 1:search_end + (1 if confirmation else 0)]
    price_signal = next((
        row for row in candidates
        if float(row["close"]) > float(rows[rows.index(row) - 1]["close"])
        and float(row["low"]) >= float(retest["low"])
    ), None)

    signal = price_signal
    basis = "price_only" if signal else "unavailable"
    counts = daily_limit_counts or {}
    if signal and counts:
        previous = rows[rows.index(signal) - 1]["date"]
        if counts.get(signal["date"], 0) > counts.get(previous, 0):
            basis = "price+limit_pool"
        else:
            signal = None
            basis = "unavailable"

    full = next((
        row for row in after_rebound
        if float(row["high"]) > float(high_peak["high"])
        and float(row["close"]) > float(close_peak["close"])
    ), None)
    signal_i = rows.index(signal) if signal else -1
    signal_rows = rows[signal_i:] if signal_i >= 0 else []
    rising_days = 1
    for previous, current in zip(signal_rows, signal_rows[1:]):
        if float(current["close"]) > float(previous["close"]):
            rising_days += 1
        else:
            rising_days = 1
    status = "探底未完成" if not higher_low else "震荡筑底"
    if confirmation:
        status = "小周期主升" if signal and rising_days >= 4 else "震荡转升"
    signal_return = (
        round((float(rows[-1]["close"]) / float(signal["close"]) - 1) * 100, 2)
        if signal else None
    )
    return {
        "status": status,
        "events": {
            "final_stop": _event(stop["date"], low=float(stop["low"])),
            "rebound_high": {
                "high_date": high_peak["date"], "high": float(high_peak["high"]),
                "close_date": close_peak["date"], "close": float(close_peak["close"]),
            },
            "secondary_bottom": _event(
                secondary["date"], low=float(secondary["low"]), higher_low=higher_low,
            ),
            "retest": _event(retest["date"], close=float(retest["close"])),
        },
        "signal_date": signal["date"] if signal else "",
        "confirmation_date": confirmation["date"] if confirmation else "",
        "full_confirmation_date": full["date"] if full else "",
        "signal_return": signal_return,
        "rising_days": rising_days if signal else 0,
        "signal_basis": basis,
    }
