"""Authorization needs actual as-of/batch evidence, not a healthy-looking badge."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from test_strategy_qualification import event_metrics, plan, quality, record
from test_strategy_qualification_integration import context


@pytest.fixture
def preview_tool():
    path = Path(__file__).resolve().parents[1] / "tools" / "preview_recap_report.py"
    spec = importlib.util.spec_from_file_location("qualification_preview_tool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("change", [
    "all_batches_missing", "module_batch_missing", "conflicting_batch", "empty_lineage_batch",
    "as_of_missing", "wrong_legacy_raw_as_of", "core_nested_stale",
    "core_nested_expired", "structural_stale", "structural_nested_stale",
    "structural_as_of_missing", "structural_wrong_as_of",
])
def test_required_provenance_failure_cannot_be_authorized(change):
    from decision_dashboard import build_today_decision
    ctx = context(phase="early_0935")
    q = ctx["data_quality"]
    raw = q["modules"]["price_raw"]
    sector = q["modules"]["sector"]
    if change == "all_batches_missing":
        q.pop("run_id")
        for row in q["modules"].values():
            row.pop("run_id", None)
            row.get("lineage", {}).pop("run_id", None)
    elif change == "module_batch_missing":
        raw.pop("run_id")
    elif change == "conflicting_batch":
        raw["lineage"]["run_id"] = "old-run"
    elif change == "empty_lineage_batch":
        raw["lineage"]["run_id"] = ""
    elif change == "as_of_missing":
        raw["lineage"].pop("report_date")
    elif change == "wrong_legacy_raw_as_of":
        raw["source_timestamp"] = "2026-09-02"
    elif change.startswith("core_nested_"):
        raw["lineage"]["freshness_level"] = change.removeprefix("core_nested_")
    elif change == "structural_stale":
        sector["freshness_level"] = "stale"
    elif change == "structural_nested_stale":
        sector["lineage"]["freshness_level"] = "stale"
    elif change == "structural_as_of_missing":
        sector["lineage"].pop("report_date")
    else:
        sector["lineage"]["report_date"] = "2026-09-02"
    ready = build_today_decision(ctx)["readiness"]
    assert not ready["plan_permitted"], change
    assert not ready["execution_ready"], change


def test_reference_collection_time_is_distinct_from_report_as_of_date():
    from decision_dashboard import build_today_decision
    ctx = context(phase="early_0935")
    ctx["data_quality"]["modules"]["universe"]["source_timestamp"] = "2026-08-07T01:14:17+08:00"
    assert build_today_decision(ctx)["readiness"]["execution_ready"]


def test_quality_producer_preserves_explicit_as_of_and_collection_time():
    from data_sources.quality_gate import build_module_quality
    from data_sources.run_context import run_context
    with run_context("run"):
        row = build_module_quality("universe", total=1, covered=1,
            source="fixture_security_master", source_timestamp="2026-08-07T01:14:17+08:00",
            report_date="2026-09-03")
    assert row["lineage"]["report_date"] == "2026-09-03"
    assert row["source_timestamp"] == "2026-08-07T01:14:17+08:00"
    assert row["run_id"] == "run"


def test_quality_producer_does_not_overwrite_an_incompatible_source_date():
    from data_sources.quality_gate import build_module_quality
    from data_sources.run_context import run_context
    with run_context("run"):
        row = build_module_quality("price_raw", total=1, covered=1,
            source="fixture", source_timestamp="2026-09-02", report_date="2026-09-03",
            lineage={"report_date": "2026-09-02"})
    assert row["lineage"]["report_date"] == "2026-09-02"


@pytest.mark.parametrize("changed,expected", [(None, True), ("ai", True), ("price_raw", False), ("sector", False)])
def test_aggregate_batch_failure_is_scoped_to_required_inputs(changed, expected):
    from data_sources.quality_gate import aggregate_report_quality
    from data_sources.run_context import run_context
    from decision_dashboard import build_today_decision
    from strategy_qualification import qualify_strategies
    ctx = context(phase="early_0935")
    modules = deepcopy(ctx["data_quality"]["modules"])
    for row in modules.values():
        row["run_id"] = "run"
    if changed:
        modules[changed]["run_id"] = "old-run"
    with run_context("run"):
        q = aggregate_report_quality(modules)
    scoped = qualify_strategies(ctx["scenario_plans"], quality=q,
        validation_records=[record(ctx["scenario_plans"][0])], event_metrics=event_metrics(),
        report_date=ctx["date_str"], target_trade_date=ctx["next_trade_date"])
    q["strategy_qualification"] = scoped
    q["publication_mode"] = scoped["publication_mode"]
    ctx["data_quality"] = q
    ctx["publication_mode"] = scoped["publication_mode"]
    ready = build_today_decision(ctx)["readiness"]
    assert ready["execution_ready"] is expected
    if changed == "sector":
        assert ready["data"]["status"] == "ready"
        assert any(row["scope"] == "strategy_data" for row in ready["issues"])
    if changed == "ai":
        assert ready["data"]["status"] == "ready"
        assert not any(row["module"] == "run_id_consistency" for row in ready["issues"])
        assert any(row["module"] == "run_id_consistency" for row in ready["nonblocking_issues"])


@pytest.mark.parametrize("field", ["source", "evidence_ref", "evaluated_at", "valid_from", "valid_until"])
@pytest.mark.parametrize("container", ["object", "list"])
def test_rejected_validation_metadata_never_exposes_raw_samples(field, container):
    from strategy_qualification import qualify_strategies
    from decision_dashboard import build_today_decision
    from report_closure import build_decision_replay_context
    ctx = context()
    p = ctx["scenario_plans"][0]
    evidence = record(p)
    marker = "SYNTHETIC_PRIVATE_SAMPLE_METADATA"
    evidence["samples"][0]["sample_id"] = marker
    evidence[field] = {"samples": deepcopy(evidence["samples"])} if container == "object" else deepcopy(evidence["samples"])
    scoped = qualify_strategies([p], quality=ctx["data_quality"], validation_records=[evidence],
        event_metrics=event_metrics(), report_date=ctx["date_str"], target_trade_date=ctx["next_trade_date"])
    ctx["data_quality"]["strategy_qualification"] = scoped
    ctx["data_quality"]["publication_mode"] = scoped["publication_mode"]
    ctx["publication_mode"] = scoped["publication_mode"]
    decision = build_today_decision(ctx)
    replay = build_decision_replay_context(ctx, decision)
    assert scoped["strategies"][p["scenario_id"]]["validation"]["status"] == "unverified"
    for surface in (scoped, decision, replay):
        assert marker not in json.dumps(surface)


def test_daily_delta_preserves_snapshot_date_and_batch_through_review_gate():
    from data_sources.quality_gate import apply_review_readiness_gates
    from data_sources.run_context import run_context
    from review_metrics import build_daily_delta_snapshot
    from strategy_qualification import qualify_strategies
    current = {"report_date": "2026-09-03", "run_id": "run", "max_height": 3,
               "limit_pool_rows": [{"code": "sz000001", "height": 3}]}
    previous = {"report_date": "2026-09-02", "run_id": "previous-run", "max_height": 2,
                "limit_pool_rows": [{"code": "sz000001", "height": 2}]}
    delta = build_daily_delta_snapshot(current, previous)
    assert delta.get("report_date") == "2026-09-03"
    assert delta.get("run_id") == "run"
    assert delta.get("previous_report_date") == "2026-09-02"
    with run_context("run"):
        gated = apply_review_readiness_gates(quality(), daily_delta=delta,
            ladder_metrics=event_metrics(), ai_result={"status": "unavailable"})
    module = gated["modules"]["daily_delta"]
    assert module["lineage"]["report_date"] == "2026-09-03"
    assert module["lineage"]["run_id"] == module["run_id"] == "run"
    result = qualify_strategies([plan()], quality=gated, validation_records=[record()],
        event_metrics=event_metrics(), report_date="2026-09-03", target_trade_date="2026-09-04")
    assert result["strategies"]["selective_mainline_hold"]["plan_permitted"]


def test_missing_core_date_is_not_presented_as_a_passed_data_check():
    from decision_dashboard import build_today_decision
    ctx = context(validated=False)
    ctx["data_quality"]["modules"]["price_raw"]["lineage"].pop("report_date")
    ready = build_today_decision(ctx)["readiness"]
    assert ready["data"]["status"] == "missing"
    assert any(issue["scope"] == "core_market" for issue in ready["issues"])
    assert "独立验证尚未通过" not in ready["action"]["reason"]


def test_nested_core_staleness_is_visible_in_the_data_axis():
    from decision_dashboard import build_today_decision
    ctx = context(phase="early_0935")
    ctx["data_quality"]["modules"]["price_raw"]["lineage"]["freshness_level"] = "stale"
    assert build_today_decision(ctx)["readiness"]["data"]["status"] == "expired"


@pytest.mark.parametrize("field", ["source", "evidence_ref", "evaluated_at", "sample_size", "unexpected_metadata"])
def test_refresh_does_not_republish_nested_private_metadata_from_old_summaries(field):
    from decision_dashboard import build_today_decision
    ctx = context()
    summary = ctx["data_quality"]["strategy_qualification"]["strategies"]["selective_mainline_hold"]["validation"]
    summary[field] = {"samples": [{"sample_id": "PRIVATE_CACHED_SUMMARY_SAMPLE"}]}
    decision = build_today_decision(ctx)
    assert "PRIVATE_CACHED_SUMMARY_SAMPLE" not in json.dumps(decision)
    if field != "unexpected_metadata":
        assert not decision["readiness"]["plan_permitted"]


@pytest.mark.parametrize("field", ["sample_size", "status", "issues"])
@pytest.mark.parametrize("kind", ["object", "list"])
def test_public_qualification_panel_never_formats_nested_private_metadata(field, kind):
    from recap_panels import render_strategy_qualification
    ctx = context()
    bad = {"samples": [{"sample_id": "PRIVATE_HTML_PANEL_SAMPLE"}]}
    summary = ctx["data_quality"]["strategy_qualification"]["strategies"]["selective_mainline_hold"]["validation"]
    summary[field] = bad if kind == "object" else [bad]
    html = render_strategy_qualification(ctx["data_quality"])
    assert "PRIVATE_HTML_PANEL_SAMPLE" not in html
    assert "未验证" in html


@pytest.mark.parametrize("kind", ["object", "list"])
def test_compact_preview_uses_effective_sanitized_qualification(tmp_path, monkeypatch, kind, preview_tool):
    preview = preview_tool
    ctx = context()
    bad = {"samples": [{"sample_id": "PRIVATE_COMPACT_PREVIEW_SAMPLE"}]}
    ctx["data_quality"]["strategy_qualification"]["strategies"]["selective_mainline_hold"]["validation"]["sample_size"] = bad if kind == "object" else [bad]
    stored = {"report_date": "2026-09-03", "target_trade_date": "2026-09-04",
        "quality": ctx["data_quality"], "publication_mode": ctx["publication_mode"],
        "market_state": ctx["market_state"], "market_thesis": ctx["market_thesis"],
        "mainline_review": ctx["mainline_review"], "scenario_plans": ctx["scenario_plans"],
        "scenario_posterior": ctx["scenario_posterior"],
        "facts": {"strategy_event_metrics": event_metrics()}}
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"context": stored}), encoding="utf-8")
    before = audit.read_bytes()
    monkeypatch.setattr(preview, "load_limit_event_snapshot", lambda *args: None)
    monkeypatch.setattr(preview.CalendarProvider, "cached_next_trading_day", lambda *args: "2026-09-04")
    output = tmp_path / "preview"
    result = preview.build_preview(audit, output)
    assert audit.read_bytes() == before
    for artifact in output.glob("*.html"):
        assert "PRIVATE_COMPACT_PREVIEW_SAMPLE" not in artifact.read_text(encoding="utf-8")
    effective = result["strategy_qualification"]["strategies"]["selective_mainline_hold"]
    assert not effective["plan_permitted"]
    assert effective["validation"]["sample_size"] is None


def test_default_preview_does_not_treat_legacy_counts_as_strategy_approval(tmp_path, monkeypatch, preview_tool):
    preview = preview_tool
    from decision_dashboard import build_today_decision
    from report_logic import build_market_state
    ctx = context()
    q = ctx["data_quality"]
    q.pop("strategy_qualification")
    q.update(status="ok", publication_mode="decision", decision_degraded=[], historical_samples=25)
    for row in q["modules"].values():
        row["status"] = "ok"
    ctx["market_state"] = build_market_state(q, historical_samples=25)
    stored = {"report_date": "2026-09-03", "target_trade_date": "2026-09-04",
        "quality": q, "publication_mode": "decision", "market_state": ctx["market_state"],
        "market_thesis": ctx["market_thesis"], "mainline_review": ctx["mainline_review"],
        "scenario_plans": ctx["scenario_plans"], "scenario_posterior": ctx["scenario_posterior"],
        "facts": {"candidate_funnel": build_today_decision(ctx)["candidate_funnel"],
                  "strategy_event_metrics": event_metrics()}}
    audit = tmp_path / "legacy.json"
    audit.write_text(json.dumps({"context": stored}), encoding="utf-8")
    monkeypatch.setattr(preview, "load_limit_event_snapshot", lambda *args: None)
    monkeypatch.setattr(preview.CalendarProvider, "cached_next_trading_day", lambda *args: "2026-09-04")
    result = preview.build_preview(audit, tmp_path / "preview")
    assert result["readiness"]["strategy"]["status"] == "unverified"
    assert not result["readiness"]["plan_permitted"]
    assert result["strategy_qualification"]["eligible_strategy_ids"] == []
