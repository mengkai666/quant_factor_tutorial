from pathlib import Path

import pandas as pd
import pytest

from data_sources.price_provider import PRICE_COLUMNS, PriceProvider
from data_sources.quality_gate import DataQualityError, MarketDataQualityGate
from data_sources.universe_provider import UNIVERSE_COLUMNS
from pipeline.data_pipeline import DataPipeline


def _universe():
    return pd.DataFrame([
        ["sh600000", "600000", "SH", "A", "1999-01-01", "", "listed", "", "fixture", "now"],
        ["sz000001", "000001", "SZ", "B", "1991-01-01", "", "listed", "", "fixture", "now"],
        ["bj920117", "920117", "BJ", "C", "2022-01-01", "", "listed", "", "fixture", "now"],
    ], columns=UNIVERSE_COLUMNS)


def _prices(multiplier=1.05):
    rows = []
    for date, factor in (("2026-08-04", 1.0), ("2026-08-05", multiplier)):
        for code, base in (("sh600000", 10), ("sz000001", 20), ("bj920117", 30)):
            rows.append([date, code, base * factor, base * factor / 2, "traded",
                         "fixture_raw", "fixture_qfq", "now"])
    return pd.DataFrame(rows, columns=PRICE_COLUMNS)


def test_invalid_candidate_does_not_replace_official_cache(tmp_path):
    official = tmp_path / "prices.csv"
    candidate = tmp_path / "prices.candidate.csv"
    report = tmp_path / "quality.json"
    pd.DataFrame({"sentinel": ["keep"]}).to_csv(official, index=False)
    bad = _prices()
    bad.loc[bad.date == "2026-08-05", "close_qfq"] *= 10
    bad.to_csv(candidate, index=False)
    pipeline = DataPipeline(quality_gate=MarketDataQualityGate(), price_provider=PriceProvider())

    with pytest.raises(DataQualityError):
        pipeline.validate_and_promote(_universe(), candidate, official, "2026-08-05", report)

    assert pd.read_csv(official).to_dict("records") == [{"sentinel": "keep"}]
    assert candidate.exists()
    assert report.exists()


def test_valid_candidate_replaces_official_cache_atomically(tmp_path):
    official = tmp_path / "prices.csv"
    candidate = tmp_path / "prices.candidate.csv"
    report = tmp_path / "quality.json"
    pd.DataFrame({"sentinel": ["old"]}).to_csv(official, index=False)
    _prices().to_csv(candidate, index=False)
    pipeline = DataPipeline(quality_gate=MarketDataQualityGate(), price_provider=PriceProvider())

    got = pipeline.validate_and_promote(_universe(), candidate, official, "2026-08-05", report)

    assert got.ok
    assert not candidate.exists()
    assert pd.read_csv(official).columns.tolist() == PRICE_COLUMNS
    assert not list(tmp_path.glob("*.tmp"))


def test_prepare_cold_start_refreshes_universe_then_rebuilds_candidate(tmp_path):
    order = []

    class Calendar:
        def latest_closed_day(self):
            order.append("calendar")
            return "2026-08-05"

        def trading_days(self, start, end):
            return ["2026-08-04", "2026-08-05"]

    class Universe:
        def refresh(self, path):
            order.append("universe")
            frame = _universe()
            frame.to_csv(path, index=False)
            return frame

    class Prices:
        def rebuild(self, universe, dates, path, *, batch_size, resume):
            order.append("prices")
            assert batch_size == 50
            assert resume is True
            frame = _prices()
            frame.to_csv(path, index=False)
            return type("Result", (), {"data": frame, "status": "success"})()

        def promote(self, candidate, official):
            order.append("promote")
            Path(candidate).replace(official)

    pipeline = DataPipeline(calendar_provider=Calendar(), universe_provider=Universe(),
                            price_provider=Prices(), quality_gate=MarketDataQualityGate())
    result = pipeline.prepare(
        start="2026-08-04", target_date=None,
        universe_path=tmp_path / "universe.csv",
        candidate_path=tmp_path / "candidate.csv",
        official_path=tmp_path / "official.csv",
        quality_report_path=tmp_path / "quality.json",
    )

    assert result.target_date == "2026-08-05"
    assert order == ["calendar", "universe", "prices", "promote"]
    assert (tmp_path / "official.csv").exists()
