import json

import pandas as pd
import pytest

from data_sources.price_provider import PRICE_COLUMNS
from data_sources.quality_gate import DataQualityError
from data_sources.universe_provider import UNIVERSE_COLUMNS
from pipeline.data_pipeline import run_preflight_gate
from market_data import load_analysis_price_view


def _universe():
    return pd.DataFrame([
        ["sh600000", "600000", "SH", "A", "1999-01-01", "", "listed", "", "fixture", "now"],
        ["sz000001", "000001", "SZ", "B", "1991-01-01", "", "listed", "", "fixture", "now"],
        ["bj920117", "920117", "BJ", "C", "2022-01-01", "", "listed", "", "fixture", "now"],
    ], columns=UNIVERSE_COLUMNS)


def _prices():
    rows = []
    for date, factor in (("2026-08-04", 1.0), ("2026-08-05", 1.02)):
        for code, base in (("sh600000", 10), ("sz000001", 20), ("bj920117", 30)):
            rows.append([date, code, base * factor, base * factor / 2, "traded",
                         "fixture_raw", "fixture_qfq", "now"])
    return pd.DataFrame(rows, columns=PRICE_COLUMNS)


def test_preflight_gate_runs_before_report_and_delivery_callbacks(tmp_path):
    events = []
    universe = tmp_path / "universe.csv"
    prices = tmp_path / "prices.csv"
    report = tmp_path / "quality.json"
    _universe().to_csv(universe, index=False)
    bad = _prices().drop(columns=["close_raw"])
    bad.to_csv(prices, index=False)

    with pytest.raises(DataQualityError):
        run_preflight_gate(universe, prices, "2026-08-05", report)

    assert events == []
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["ok"] is False


def test_preflight_gate_returns_single_quality_truth_for_homepage(tmp_path):
    universe = tmp_path / "universe.csv"
    prices = tmp_path / "prices.csv"
    report = tmp_path / "quality.json"
    _universe().to_csv(universe, index=False)
    _prices().to_csv(prices, index=False)

    quality = run_preflight_gate(universe, prices, "2026-08-05", report)

    assert quality.ok
    assert quality.to_dict()["ok"] is True


def test_legacy_analysis_view_maps_only_qfq_to_in_memory_close():
    view = load_analysis_price_view(_prices())
    assert "close" in view.columns
    assert view["close"].equals(view["close_qfq"])
    assert not view["close"].equals(view["close_raw"])
