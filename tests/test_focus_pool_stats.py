import pandas as pd

from data_sources.focus_pool_stats import build_scenario_stats, format_sample_label


def test_scenario_stats_reports_sample_count_and_t3_result():
    frame = pd.DataFrame({
        "日期": [f"2026080{index}" for index in range(1, 9)],
        "连板高度": [3, 4, 5, 4, 5, 6, 5, 7],
        "up": [3000] * 8, "down": [2000] * 8,
        "zt": [80] * 8, "dt": [2] * 8,
    })

    stats = build_scenario_stats(frame, min_samples=2)

    assert stats["breakout"]["sample_count"] >= 2
    assert 0 <= stats["breakout"]["win_rate"] <= 1
    assert "样本" in format_sample_label(stats["breakout"])
    assert "%" in format_sample_label(stats["breakout"])


def test_insufficient_samples_never_formats_a_probability():
    summary = {"sample_count": 1, "win_rate": 1.0, "horizon": 3}

    assert format_sample_label(summary) == "样本 1 · 样本不足"


def test_default_minimum_requires_ten_samples():
    frame = pd.DataFrame({
        "日期": [f"2026-07-{day:02d}" for day in range(1, 16)],
        "连板高度": [6, 4, 6, 4, 6, 4, 6, 4, 6, 4, 6, 4, 6, 4, 6],
    })

    stats = build_scenario_stats(frame)

    assert stats["breakdown"]["min_samples"] == 10
