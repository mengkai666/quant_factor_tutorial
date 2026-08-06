import pytest
import pandas as pd

from app import PreflightDataStage, MarketTrackerApp, build_default_app, main
from data_sources.price_provider import PRICE_COLUMNS
from data_sources.quality_gate import DataQualityError
from data_sources.universe_provider import UNIVERSE_COLUMNS
from pipeline.analysis_pipeline import AnalysisPipeline
from pipeline.delivery_pipeline import DeliveryPipeline
from pipeline.report_pipeline import ReportPipeline


class Stage:
    def __init__(self, name, events, result=None, error=None):
        self.name = name
        self.events = events
        self.result = result if result is not None else {"stage": name}
        self.error = error

    def run(self, context):
        self.events.append(self.name)
        if self.error:
            raise self.error
        context[self.name] = self.result
        return context


def test_market_tracker_app_runs_data_analysis_report_delivery_in_order():
    events = []
    app = MarketTrackerApp(
        data=Stage("data", events), analysis=Stage("analysis", events),
        report=Stage("report", events), delivery=Stage("delivery", events),
    )

    context = app.run({"seed": True})

    assert events == ["data", "analysis", "report", "delivery"]
    assert context["delivery"] == {"stage": "delivery"}


def test_market_tracker_app_short_circuits_after_data_failure():
    events = []
    app = MarketTrackerApp(
        data=Stage("data", events, error=RuntimeError("quality failed")),
        analysis=Stage("analysis", events), report=Stage("report", events),
        delivery=Stage("delivery", events),
    )

    with pytest.raises(RuntimeError, match="quality failed"):
        app.run({})
    assert events == ["data"]


def test_app_main_returns_nonzero_on_stage_failure_and_zero_on_success():
    assert main(app=MarketTrackerApp(
        Stage("data", []), Stage("analysis", []), Stage("report", []), Stage("delivery", [])
    )) == 0
    assert main(app=MarketTrackerApp(
        Stage("data", [], error=RuntimeError("boom")), Stage("analysis", []),
        Stage("report", []), Stage("delivery", [])
    )) == 1


def test_default_app_advances_legacy_workflow_through_real_boundaries(monkeypatch):
    import legacy_tracker

    events = []

    def workflow():
        for name in ("data", "analysis", "report", "delivery"):
            events.append(name)
            yield {"legacy_stage": name}

    monkeypatch.setattr(legacy_tracker, "iter_main", workflow)
    monkeypatch.setattr(legacy_tracker, "main", lambda: events.append("monolithic"))

    context = build_default_app().run({})

    assert events == ["data", "analysis", "report", "delivery"]
    assert context["legacy_stage"] == "delivery"
    assert context["analysis"]["legacy_stage"] == "analysis"
    assert context["report"]["legacy_stage"] == "report"
    assert context["delivery"]["legacy_stage"] == "delivery"


def _offline_universe():
    return pd.DataFrame([
        ["sh600000", "600000", "SH", "A", "1999-01-01", "", "listed", "", "fixture", "now"],
        ["sz000001", "000001", "SZ", "B", "1991-01-01", "", "listed", "", "fixture", "now"],
        ["bj920117", "920117", "BJ", "C", "2022-01-01", "", "listed", "", "fixture", "now"],
    ], columns=UNIVERSE_COLUMNS)


def _offline_prices():
    rows = []
    for date, factor in (("2026-08-04", 1.0), ("2026-08-05", 1.02)):
        for code, base in (("sh600000", 10), ("sz000001", 20), ("bj920117", 30)):
            rows.append([date, code, base * factor, base * factor / 2, "traded",
                         "fixture_raw", "fixture_qfq", "now"])
    return pd.DataFrame(rows, columns=PRICE_COLUMNS)


def test_offline_pipeline_writes_report_only_after_real_preflight_gate(tmp_path):
    universe_path = tmp_path / "universe.csv"
    prices_path = tmp_path / "prices.csv"
    quality_path = tmp_path / "quality.json"
    output_path = tmp_path / "report.html"
    _offline_universe().to_csv(universe_path, index=False)
    _offline_prices().to_csv(prices_path, index=False)
    events = []

    app = MarketTrackerApp(
        data=PreflightDataStage(universe_path, prices_path, "2026-08-05", quality_path),
        analysis=AnalysisPipeline(lambda context: events.append("analysis") or len(context["prices"])),
        report=ReportPipeline(lambda _context: (events.append("report"),
                                                 output_path.write_text("<html>offline</html>", encoding="utf-8"))[1]),
        delivery=DeliveryPipeline(lambda _context: events.append("delivery")),
    )

    app.run({})

    assert events == ["analysis", "report", "delivery"]
    assert output_path.read_text(encoding="utf-8") == "<html>offline</html>"


def test_offline_pipeline_blocks_report_when_preflight_fails(tmp_path):
    universe_path = tmp_path / "universe.csv"
    prices_path = tmp_path / "prices.csv"
    quality_path = tmp_path / "quality.json"
    output_path = tmp_path / "report.html"
    _offline_universe().to_csv(universe_path, index=False)
    bad = _offline_prices().drop(columns=["close_qfq"])
    bad.to_csv(prices_path, index=False)
    events = []
    app = MarketTrackerApp(
        data=PreflightDataStage(universe_path, prices_path, "2026-08-05", quality_path),
        analysis=AnalysisPipeline(lambda _context: events.append("analysis")),
        report=ReportPipeline(lambda _context: (events.append("report"),
                                                 output_path.write_text("bad", encoding="utf-8"))[1]),
        delivery=DeliveryPipeline(lambda _context: events.append("delivery")),
    )

    with pytest.raises(DataQualityError):
        app.run({})

    assert events == []
    assert not output_path.exists()
