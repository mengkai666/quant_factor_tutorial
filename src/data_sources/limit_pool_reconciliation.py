"""Compare independent limit-pool snapshots without merging their rows."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class LimitPoolReconciliation:
    status: str
    primary_count: int
    secondary_count: int
    count_delta: int
    missing_codes: tuple[str, ...] = ()
    extra_codes: tuple[str, ...] = ()
    message: str = ""


def _snapshot(frame: pd.DataFrame) -> tuple[set[str], dict[str, int]]:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return set(), {}
    if "code" not in frame.columns:
        return set(), {}
    codes = frame["code"].astype(str).str.strip()
    counts = pd.to_numeric(
        frame.get("limit_count", pd.Series(1, index=frame.index)), errors="coerce"
    ).fillna(1).clip(lower=1).astype(int)
    mapping = dict(zip(codes, counts))
    return set(mapping), mapping


def reconcile_limit_pool(primary: pd.DataFrame, secondary: pd.DataFrame) -> LimitPoolReconciliation:
    """Return match/partial/unavailable evidence for two normalized snapshots."""
    primary_codes, primary_heights = _snapshot(primary)
    secondary_codes, secondary_heights = _snapshot(secondary)
    if not secondary_codes:
        return LimitPoolReconciliation(
            status="unavailable",
            primary_count=len(primary_codes),
            secondary_count=0,
            count_delta=0,
            message="secondary snapshot unavailable",
        )

    count_delta = abs(len(primary_codes) - len(secondary_codes))
    threshold = max(3, round(max(len(primary_codes), 1) * 0.05))
    messages = []
    if count_delta > threshold:
        messages.append(
            f"count drift primary={len(primary_codes)} "
            f"secondary={len(secondary_codes)} delta={count_delta} threshold={threshold}"
        )

    primary_high = {code: height for code, height in primary_heights.items() if height >= 2}
    secondary_high = {code: height for code, height in secondary_heights.items() if height >= 2}
    if set(primary_high) != set(secondary_high) or any(
        primary_high.get(code) != secondary_high.get(code)
        for code in set(primary_high) | set(secondary_high)
    ):
        missing = tuple(sorted(set(primary_high) - set(secondary_high)))
        extra = tuple(sorted(set(secondary_high) - set(primary_high)))
        messages.append(
            f"high-board drift missing={','.join(missing) or '-'} "
            f"extra={','.join(extra) or '-'}"
        )
    else:
        missing = ()
        extra = ()

    return LimitPoolReconciliation(
        status="partial" if messages else "match",
        primary_count=len(primary_codes),
        secondary_count=len(secondary_codes),
        count_delta=count_delta,
        missing_codes=missing,
        extra_codes=extra,
        message="; ".join(messages),
    )
