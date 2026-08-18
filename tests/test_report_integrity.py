from pathlib import Path
import json

import pandas as pd
import pytest


def _quality(*, price_coverage=99.0, status="ok", critical_blocked=None):
    return {
        "status": status,
        "critical_blocked": list(critical_blocked or []),
        "modules": {
            "universe": {"status": "ok", "coverage_pct": 100.0, "source": "security_master"},
            "price_raw": {"status": "ok", "coverage_pct": price_coverage, "source": "price_cache"},
            "breadth": {"status": "ok", "coverage_pct": 99.0, "source": "price_cache"},
            "limit_pool": {"status": "ok", "coverage_pct": 100.0, "source": "eastmoney"},
        },
    }


def _phase_result(*, name="传智教育", code="sz003032"):
    row = {"code": code, "name": name, "底部至今": 12.3, "下跌段": -2.1}
    return {
        "quadrants": {"独立主线": pd.DataFrame([{"板块": "教育"}])},
        "representatives": {"groups": {"独立主线": [row]}},
    }


def test_report_integrity_accepts_complete_structured_report():
    from report_integrity import build_report_integrity, validate_report_integrity

    payload = build_report_integrity(
        report_date="2026-08-07",
        market_date="20260807",
        phase_result=_phase_result(),
        quality=_quality(),
    )

    checked = validate_report_integrity(payload)

    assert checked["ok"] is True
    assert checked["metrics"]["quadrant_rows"] == 1
    assert checked["metrics"]["representative_rows"] == 1
    assert checked["metrics"]["code_fallback_count"] == 0
    assert checked["metrics"]["chinese_name_coverage"] == 100.0


def test_report_integrity_rejects_code_fallback_and_empty_quadrants():
    from report_integrity import (
        ReportIntegrityError,
        build_report_integrity,
        validate_report_integrity,
    )

    payload = build_report_integrity(
        report_date="2026-08-07",
        market_date="2026-08-07",
        phase_result={
            "quadrants": {},
            "representatives": {
                "groups": {"独立主线": [{"code": "sz003032", "name": "sz003032"}]}
            },
        },
        quality=_quality(),
    )

    with pytest.raises(ReportIntegrityError) as exc:
        validate_report_integrity(payload)

    message = str(exc.value)
    assert "四象限" in message
    assert "证券代码代替中文名称" in message


def test_report_integrity_rejects_market_date_mismatch_and_low_price_coverage():
    from report_integrity import (
        ReportIntegrityError,
        build_report_integrity,
        validate_report_integrity,
    )

    payload = build_report_integrity(
        report_date="2026-08-07",
        market_date="2026-08-06",
        phase_result=_phase_result(),
        quality=_quality(price_coverage=72.0),
    )

    with pytest.raises(ReportIntegrityError) as exc:
        validate_report_integrity(payload)

    message = str(exc.value)
    assert "报告日期" in message
    assert "价格覆盖率" in message


def test_report_integrity_requires_degraded_source_disclosure():
    from report_integrity import ReportIntegrityError, validate_report_integrity

    payload = {
        "schema": "report-integrity/v1",
        "report_date": "2026-08-07",
        "market_date": "2026-08-07",
        "metrics": {
            "quadrant_rows": 1,
            "representative_rows": 1,
            "code_fallback_count": 0,
            "chinese_name_coverage": 100.0,
            "price_coverage_pct": 99.0,
            "critical_blocked": [],
            "degraded_modules": ["plates"],
            "quality_disclosures": [],
        },
    }

    with pytest.raises(ReportIntegrityError, match="降级来源未披露"):
        validate_report_integrity(payload)


def test_report_integrity_requires_source_disclosure_for_each_degraded_module():
    from report_integrity import ReportIntegrityError, validate_report_integrity

    payload = {
        "schema": "report-integrity/v1",
        "report_date": "2026-08-07",
        "market_date": "2026-08-07",
        "metrics": {
            "quadrant_rows": 1,
            "representative_rows": 1,
            "code_fallback_count": 0,
            "chinese_name_coverage": 100.0,
            "price_coverage_pct": 99.0,
            "critical_blocked": [],
            "degraded_modules": ["plates", "breadth"],
            "quality_disclosures": ["plates: eastmoney / stale-cache"],
        },
    }

    with pytest.raises(ReportIntegrityError, match="breadth"):
        validate_report_integrity(payload)


def test_validate_rendered_report_reads_integrity_metadata(tmp_path):
    from report_integrity import (
        build_report_integrity,
        render_report_integrity_metadata,
        validate_rendered_report,
    )

    payload = build_report_integrity(
        report_date="2026-08-07",
        market_date="2026-08-07",
        phase_result=_phase_result(),
        quality=_quality(),
    )
    path = tmp_path / "report.html"
    path.write_text(
        '<html><head><meta name="report-date" content="2026-08-07">'
        + render_report_integrity_metadata(payload)
        + "</head><body>ok</body></html>",
        encoding="utf-8",
    )

    checked = validate_rendered_report(path)

    assert checked["ok"] is True
    assert checked["report_date"] == "2026-08-07"



def test_generated_main_report_embeds_structured_integrity_metadata(monkeypatch, tmp_path):
    import phase_resonance
    import 主线强度追踪 as report
    from report_integrity import extract_report_integrity

    output = tmp_path / "report.html"
    monkeypatch.setattr(report, "OUTPUT_HTML", str(output))
    monkeypatch.setenv("AI_ENABLE", "0")
    monkeypatch.setattr(phase_resonance, "build_phase_resonance", lambda: _phase_result())
    monkeypatch.setattr(phase_resonance, "render_phase_resonance_html", lambda _data: "<div>phase</div>")
    empty = pd.DataFrame()
    report_context = {
        "publication_mode": "decision",
        "quality": _quality(),
        "facts": {
            "market_state": {"publication_mode": "decision", "allow_strong_conclusion": True},
            "market_snapshot": {"report_date": "2026-08-07", "limit_up": 83, "limit_down": 4},
        },
    }

    report.generate_html(
        ml_strength=empty, sub_strength=empty, ml_ma={}, sub_ma={},
        ml_thresh={}, sub_thresh={}, leaders={}, dates=["20260807"],
        ratings={}, sub_ratings={}, echelon=[], top30_data={},
        advance_decline={"up": 2800, "down": 2500, "zt": 83, "dt": 4},
        sentiment_df=empty, classified_df=empty, price_df=empty,
        market_state=report_context["facts"]["market_state"],
        report_context=report_context,
    )

    payload = extract_report_integrity(output)
    assert payload["report_date"] == "2026-08-07"
    assert payload["market_date"] == "2026-08-07"
    assert payload["metrics"]["representative_rows"] == 1
    assert payload["metrics"]["chinese_name_coverage"] == 100.0


def test_daily_workflow_runs_integrity_gate_before_pages_deploy():
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily_run.yml").read_text(encoding="utf-8")

    validation_pos = workflow.index("validate_rendered_report")
    deploy_pos = workflow.index("peaceiris/actions-gh-pages@v4")

    assert validation_pos < deploy_pos
    assert "continue-on-error: true" not in workflow[workflow.index("python src/主线强度追踪.py"):deploy_pos]
