# -*- coding: utf-8 -*-
"""历史情景概率校准。"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from report_logic import binomial_confidence_interval


def calibrate_scenario(outcomes: Iterable[Any] | None, *, predicted_probability: float | None = None, baseline_probability: float | None = None, similarities: Iterable[float] | None = None, sample_dates: Iterable[str] | None = None, min_sample: int = 10) -> dict[str, Any]:
    binary = [1 if bool(value) else 0 for value in (outcomes or ())]
    n, hits = len(binary), sum(binary)
    interval = binomial_confidence_interval(hits, n)
    publish = n >= max(1, int(min_sample))
    probability = float(predicted_probability) if predicted_probability is not None else (hits / n if n else None)
    brier = (sum((probability - actual) ** 2 for actual in binary) / n) if n and probability is not None else None
    similarities_list = [float(x) for x in (similarities or ())]
    dates = sorted(str(x) for x in (sample_dates or ()) if str(x))
    return {
        "sample_size": n, "successes": hits,
        "publish_probability": publish,
        "hit_rate": hits / n if publish else None,
        "raw_hit_rate": hits / n if n else None,
        "confidence_interval": interval,
        "baseline_probability": baseline_probability,
        "predicted_probability": probability if publish else None,
        "similarity": sum(similarities_list) / len(similarities_list) if similarities_list else None,
        "latest_sample_date": dates[-1] if dates else None,
        "brier_score": brier,
        "reason": "" if publish else f"历史样本不足（{n}/{max(1, int(min_sample))}）",
    }
