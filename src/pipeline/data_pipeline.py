from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from data_sources.calendar_provider import CalendarProvider
from data_sources.price_provider import PriceProvider
from data_sources.quality_gate import DataQualityError, MarketDataQualityGate, QualityReport
from data_sources.universe_provider import UniverseProvider


@dataclass
class PreparedMarketData:
    target_date: str
    universe: pd.DataFrame
    prices: pd.DataFrame
    quality: QualityReport


def run_preflight_gate(universe_path, price_path, target_date: str, quality_report_path,
                       fetch_results=None, quality_gate=None) -> QualityReport:
    """Load official caches and enforce the single pre-report quality truth."""
    universe = pd.read_csv(universe_path, dtype=str).fillna("")
    prices = pd.read_csv(price_path, dtype={"code": str, "date": str})
    gate = quality_gate or MarketDataQualityGate()
    report = gate.validate(universe, prices, target_date, fetch_results)
    report.write_json(quality_report_path)
    if not report.ok:
        raise DataQualityError(report)
    return report


class DataPipeline:
    def __init__(self, calendar_provider=None, universe_provider=None,
                 price_provider=None, quality_gate=None):
        self.calendar_provider = calendar_provider or CalendarProvider()
        self.universe_provider = universe_provider or UniverseProvider()
        self.price_provider = price_provider or PriceProvider()
        self.quality_gate = quality_gate or MarketDataQualityGate()

    def validate_and_promote(self, universe: pd.DataFrame, candidate_path, official_path,
                             target_date: str, quality_report_path,
                             fetch_results=None) -> QualityReport:
        candidate = pd.read_csv(candidate_path, dtype={"code": str, "date": str})
        report = self.quality_gate.validate(universe, candidate, target_date, fetch_results)
        report.write_json(quality_report_path)
        if not report.ok:
            raise DataQualityError(report)
        self.price_provider.promote(candidate_path, official_path)
        return report

    def prepare(self, *, start: str, target_date: str | None, universe_path,
                candidate_path, official_path, quality_report_path) -> PreparedMarketData:
        target = target_date or self.calendar_provider.latest_closed_day()
        dates = self.calendar_provider.trading_days(start, target)
        if not dates or dates[-1] != target:
            raise ValueError(f"target date {target} is not a closed trading day")
        universe = self.universe_provider.refresh(universe_path)
        fetch_result = self.price_provider.rebuild(
            universe, dates, candidate_path, batch_size=50, resume=True
        )
        quality = self.validate_and_promote(
            universe, candidate_path, official_path, target, quality_report_path,
            [fetch_result] if hasattr(fetch_result, "date") else None,
        )
        prices = pd.read_csv(official_path, dtype={"code": str, "date": str})
        return PreparedMarketData(target, universe, prices, quality)
