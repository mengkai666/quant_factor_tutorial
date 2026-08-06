"""Daily ladder distribution, promotion and high-break review metrics."""
from __future__ import annotations

import pandas as pd

from .models import normalize_code


def _height_map(frame) -> dict[str, int]:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    code_col = next((col for col in ("代码", "code") if col in frame.columns), None)
    height_col = next((col for col in ("连板数", "limit_count") if col in frame.columns), None)
    if not code_col:
        return {}
    heights = pd.to_numeric(
        frame[height_col] if height_col else pd.Series(1, index=frame.index),
        errors="coerce",
    ).fillna(1).clip(lower=1).astype(int)
    result = {}
    for code, height in zip(frame[code_col], heights):
        try:
            result[normalize_code(code)] = int(height)
        except ValueError:
            continue
    return result


def _date_frame(history, compact_date: str):
    history = history or {}
    frame = history.get(compact_date)
    if frame is not None:
        return frame
    hyphenated = f"{compact_date[:4]}-{compact_date[4:6]}-{compact_date[6:]}"
    return history.get(hyphenated)


def build_ladder_review(zt_history, dt_history=None) -> dict:
    """Build current ladder evidence from legacy per-day pool dictionaries."""
    dates = sorted(str(date).replace("-", "") for date in (zt_history or {}))
    if not dates:
        return {
            "date": "", "distribution": {}, "promotions": {},
            "high_break_count": 0, "missing_heights": [], "dt_count": 0,
        }
    latest = dates[-1]
    current_frame = _date_frame(zt_history, latest)
    current = _height_map(current_frame)
    distribution = {}
    for height in current.values():
        distribution[height] = distribution.get(height, 0) + 1

    promotions = {}
    high_break_count = 0
    if len(dates) >= 2:
        previous = _height_map(_date_frame(zt_history, dates[-2]))
        for height in sorted(set(previous.values())):
            eligible_codes = [code for code, value in previous.items() if value == height]
            advanced = sum(current.get(code) == height + 1 for code in eligible_codes)
            promotions[height] = {
                "eligible": len(eligible_codes),
                "advanced": advanced,
                "rate": advanced / len(eligible_codes) if eligible_codes else 0.0,
            }
        high_break_count = sum(
            height >= 3 and current.get(code, 0) <= height
            for code, height in previous.items()
        )

    max_height = max(distribution, default=0)
    missing_heights = [
        height for height in range(1, max_height)
        if height not in distribution
    ]
    dt_frame = _date_frame(dt_history, latest)
    dt_count = len(dt_frame) if isinstance(dt_frame, pd.DataFrame) else 0
    return {
        "date": latest,
        "distribution": dict(sorted(distribution.items())),
        "promotions": promotions,
        "high_break_count": int(high_break_count),
        "missing_heights": missing_heights,
        "dt_count": dt_count,
    }
