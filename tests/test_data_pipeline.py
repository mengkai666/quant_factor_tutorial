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


def test_publication_scopes_keep_market_facts_available_when_optional_modules_fail():
    from data_sources.quality_gate import build_module_quality, aggregate_report_quality

    modules = {
        "universe": build_module_quality("universe", total=5538, covered=5538),
        "price_raw": build_module_quality("price_raw", total=5538, covered=5538),
        "breadth": build_module_quality("breadth", total=5538, covered=5538),
        "limit_pool": build_module_quality("limit_pool", total=74, covered=74),
        "echelon": build_module_quality(
            "echelon", total=74, covered=74, critical=False
        ),
        "history": build_module_quality(
            "history", total=20, covered=20, critical=False
        ),
        "sector": build_module_quality(
            "sector", total=74, covered=13, critical=False
        ),
        "price_qfq": build_module_quality(
            "price_qfq", total=5538, covered=0, critical=False
        ),
        "ai": build_module_quality(
            "ai", total=1, covered=0, critical=False
        ),
    }

    quality = aggregate_report_quality(modules)
    scopes = quality["publication_scopes"]

    assert quality["publication_mode"] == "observation"
    assert scopes["market_facts"]["mode"] == "full"
    assert scopes["lianban_review"]["mode"] == "full"
    assert scopes["mainline_review"]["mode"] == "limited"
    assert scopes["return_analysis"]["mode"] == "unavailable"
    assert scopes["ai_review"]["mode"] == "unavailable"


def test_ai_publication_scope_is_limited_when_guarded_ai_is_facts_only():
    from data_sources.quality_gate import build_module_quality, build_publication_scopes

    modules = {
        'ai': build_module_quality(
            'ai', total=1, covered=1, critical=False,
            lineage={
                'input_quality_status': 'blocked',
                'publication_mode': 'facts_only',
            },
        ),
    }

    scopes = build_publication_scopes(modules)

    assert scopes['ai_review']['mode'] == 'limited'


def test_ai_publication_scope_is_full_for_decision_ready_output():
    from data_sources.quality_gate import build_module_quality, build_publication_scopes

    modules = {
        'ai': build_module_quality(
            'ai', total=1, covered=1, critical=False,
            lineage={
                'input_quality_status': 'ok',
                'publication_mode': 'decision',
            },
        ),
    }

    scopes = build_publication_scopes(modules)
