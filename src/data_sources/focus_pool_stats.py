"""Historical sample summaries for dashboard scenarios."""
from __future__ import annotations

import pandas as pd


def _summary(samples: list[bool], horizon: int, min_samples: int) -> dict:
    count = len(samples)
    return {
        "sample_count": count,
        "win_rate": (sum(samples) / count) if count else None,
        "horizon": horizon,
        "min_samples": min_samples,
    }


def build_scenario_stats(sentiment_df, horizon: int = 3,
                         min_samples: int = 10) -> dict[str, dict]:
    """Estimate scenario outcomes from prior max-height observations."""
    keys = ("breakout", "continuation", "divergence", "breakdown")
    buckets = {key: [] for key in keys}
    if (sentiment_df is None or not isinstance(sentiment_df, pd.DataFrame)
            or sentiment_df.empty or "连板高度" not in sentiment_df.columns):
        return {key: _summary([], horizon, min_samples) for key in keys}

    frame = sentiment_df.copy()
    if "日期" in frame.columns:
        frame = frame.sort_values("日期")
    heights = pd.to_numeric(frame["连板高度"], errors="coerce").fillna(0).astype(int).tolist()
    for index in range(1, max(1, len(heights) - horizon)):
        current = heights[index]
        previous = heights[index - 1]
        future = heights[index + 1:index + 1 + horizon]
        if len(future) < horizon or current <= 0:
            continue
        outcome = max(future) > current
        if current >= 4 and current > previous:
            buckets["breakout"].append(outcome)
        elif current >= 5 and current == previous:
            buckets["continuation"].append(outcome)
        elif current >= 5 and current < previous:
            buckets["divergence"].append(outcome)
        elif previous - current >= 2:
            buckets["breakdown"].append(outcome)

    return {
        key: _summary(values, horizon, min_samples)
        for key, values in buckets.items()
    }


def format_sample_label(summary: dict | None) -> str:
    summary = summary or {}
    count = int(summary.get("sample_count", 0) or 0)
    minimum = int(summary.get("min_samples", 5) or 5)
    win_rate = summary.get("win_rate")
    if count < minimum or not isinstance(win_rate, (int, float)):
        return f"样本 {count} · 样本不足"
    horizon = int(summary.get("horizon", 3) or 3)
    return f"样本 {count} · T+{horizon}新高 {win_rate * 100:.0f}%"
