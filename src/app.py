from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from pipeline.data_pipeline import run_preflight_gate
from pipeline.analysis_pipeline import AnalysisPipeline
from pipeline.delivery_pipeline import DeliveryPipeline
from pipeline.report_pipeline import ReportPipeline


@dataclass
class MarketTrackerApp:
    data: object
    analysis: object
    report: object
    delivery: object

    def run(self, context: dict | None = None) -> dict:
        state = dict(context or {})
        for stage in (self.data, self.analysis, self.report, self.delivery):
            state = stage.run(state)
        return state


class PreflightDataStage:
    """Load official market data and enforce the report-entry quality gate."""

    def __init__(self, universe_path, price_path, target_date: str, quality_report_path,
                 quality_gate=None):
        self.universe_path = Path(universe_path)
        self.price_path = Path(price_path)
        self.target_date = target_date
        self.quality_report_path = Path(quality_report_path)
        self.quality_gate = quality_gate

    def run(self, context: dict) -> dict:
        quality = run_preflight_gate(
            self.universe_path, self.price_path, self.target_date,
            self.quality_report_path, quality_gate=self.quality_gate,
        )
        context["quality"] = quality
        context["universe"] = pd.read_csv(self.universe_path, dtype=str).fillna("")
        context["prices"] = pd.read_csv(self.price_path, dtype={"code": str, "date": str})
        context["target_date"] = self.target_date
        return context


class LegacyWorkflow:
    """Advance the legacy implementation one application stage at a time."""

    def __init__(self):
        self._iterator = None
        self._completed = False

    def advance(self, expected_stage: str) -> dict:
        if self._completed:
            return {}
        if self._iterator is None:
            import legacy_tracker
            self._iterator = legacy_tracker.iter_main()
        try:
            payload = next(self._iterator)
        except StopIteration:
            self._completed = True
            return {}
        actual_stage = payload.get("legacy_stage")
        if actual_stage != expected_stage:
            raise RuntimeError(
                f"legacy workflow stage mismatch: expected {expected_stage}, got {actual_stage}"
            )
        if expected_stage == "delivery":
            self._completed = True
        return payload


class LegacyTrackerStage:
    """Compatibility stage, with optional staged advancement for new callers."""

    def __init__(self, stage_name: str | None = None, workflow: LegacyWorkflow | None = None):
        self.stage_name = stage_name
        self.workflow = workflow

    def run(self, context: dict) -> dict:
        if self.stage_name is None:
            import legacy_tracker
            legacy_tracker.main()
            context["legacy_completed"] = True
            return context
        payload = (self.workflow or LegacyWorkflow()).advance(self.stage_name)
        context.update(payload)
        return context


class NoOpStage:
    def run(self, context: dict) -> dict:
        return context


def build_default_app() -> MarketTrackerApp:
    workflow = LegacyWorkflow()

    def advance(stage_name: str):
        def runner(context: dict):
            payload = workflow.advance(stage_name)
            context.update(payload)
            return payload
        return runner

    return MarketTrackerApp(
        data=LegacyTrackerStage("data", workflow),
        analysis=AnalysisPipeline(advance("analysis")),
        report=ReportPipeline(advance("report")),
        delivery=DeliveryPipeline(advance("delivery")),
    )


def main(app: MarketTrackerApp | None = None) -> int:
    try:
        (app or build_default_app()).run({})
        return 0
    except Exception as exc:
        print(f"❌ 主线追踪失败: {exc}")
        return 1
