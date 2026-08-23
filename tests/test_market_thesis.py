# -*- coding: utf-8 -*-
from market_thesis import build_market_thesis


def test_market_thesis_builds_six_dimensions_and_breadth_relay_state():
    thesis = build_market_thesis(
        report_date="2026-08-17",
        market_snapshot={
            "breadth_ratio": 0.8067,
            "limit_up": 106,
            "limit_down": 1,
            "max_height": 4,
            "ladder_integrity": 1.0,
            "concentration": 0.4404,
            "promotion_rate": 0.2381,
            "mainline_rank": "AI算力",
        },
        progression_chain={"promotion_rate": 0.2381},
        ladder_metrics={"height": 4, "ladder": 9, "gap_heights": []},
        mainline_concentration={"top_share": 0.4404, "top_mainline": "AI算力"},
        market_state={"publication_mode": "observation"},
    )

    payload = thesis.to_dict()
    assert set(payload["dimensions"]) == {
        "index_breadth",
        "limit_up_diffusion",
        "relay_quality",
        "high_level_feedback",
        "mainline_structure",
        "index_sector_stock_resonance",
    }
    assert payload["breadth_relay_state"]["breadth"] == "strong"
    assert payload["breadth_relay_state"]["relay"] == "weak"
    assert payload["breadth_relay_state"]["state"] == "breadth_strong_relay_weak"
    assert payload["core_conflict"]["strength_side"]
    assert payload["core_conflict"]["risk_side"]
    assert payload["core_conflict"]["resolution_condition"]


def test_market_thesis_is_structured_not_derived_from_position_text():
    thesis = build_market_thesis(
        report_date="2026-08-17",
        market_snapshot={"breadth_ratio": 0.5, "limit_up": 20, "limit_down": 10, "max_height": 3},
    )
    assert "position" not in thesis.to_dict()["core_conflict"]
    assert all(isinstance(value, dict) for value in thesis.to_dict()["dimensions"].values())
from report_logic import ReportContext, ReportPolicy


def test_report_context_exposes_market_thesis_as_first_class_fact():
    thesis = {
        "breadth_relay_state": {"state": "breadth_strong_relay_weak"},
        "dimensions": {"index_breadth": {"state": "strong"}},
        "core_conflict": {"risk_side": "普涨未向接力传导"},
    }
    payload = ReportContext(
        report_date="2026-08-17",
        policy=ReportPolicy.from_mode("observation"),
        market_thesis=thesis,
    ).to_dict()
    assert payload["market_thesis"] == thesis

def test_summarize_phase_resonance_is_json_safe_and_keeps_decision_fields():
    import pandas as pd
    from market_thesis import summarize_phase_resonance

    result = {
        "phase": "震荡转升",
        "phase_shape": "V型反弹",
        "phase_names": ["下跌段", "底部至今"],
        "index_ret": {"下跌段": -8.2, "底部至今": 5.4},
        "corr": -0.42,
        "breadth": {"底部至今": {"上涨": 0.62}},
        "micro_cycle": {"status": "震荡转升", "signal_date": "2026-08-15", "events": {"x": object()}},
        "micro_resonance": {"level": "核心共振", "breadth": 0.7},
        "turning_summary": "ignored",
        "table": pd.DataFrame({"x": [1]}),
    }

    summary = summarize_phase_resonance(result)

    assert summary["phase"] == "震荡转升"
    assert summary["phase_names"] == ["下跌段", "底部至今"]
    assert summary["index_ret"]["底部至今"] == 5.4
    assert summary["micro_cycle"]["status"] == "震荡转升"
    assert summary["micro_resonance"]["level"] == "核心共振"
    assert "table" not in summary
    assert "turning_summary" not in summary
    assert "events" not in summary["micro_cycle"]


def test_missing_limit_counts_remain_unknown_instead_of_zero():
    thesis = build_market_thesis(
        report_date="2026-08-19",
        market_snapshot={"breadth_ratio": 0.7, "promotion_rate": 0.5},
    )
    dimensions = thesis.to_dict()["dimensions"]
    assert dimensions["limit_up_diffusion"]["state"] in {"unknown", "partial"}
    assert dimensions["limit_up_diffusion"]["state"] != "contracting"
    assert dimensions["limit_up_diffusion"]["evidence"] == ["limit_up=unknown", "limit_down=unknown"]
    assert dimensions["high_level_feedback"]["state"] in {"unknown", "partial"}
    assert dimensions["relay_quality"]["evidence"][-1] == "max_height=unknown"


def test_resonance_requires_known_phase_and_micro_status():
    thesis = build_market_thesis(
        report_date="2026-08-19",
        market_snapshot={"breadth_ratio": 0.6, "limit_up": 50, "limit_down": 2},
        phase_resonance={"phase": "unknown"},
        micro_cycle={"status": "unknown"},
    )
    dimension = thesis.to_dict()["dimensions"]["index_sector_stock_resonance"]
    assert dimension["state"] in {"unknown", "unverified", "partial"}
    assert dimension["state"] != "resonant"


def test_resonance_consumes_micro_status_and_can_be_confirmed():
    thesis = build_market_thesis(
        report_date="2026-08-19",
        market_snapshot={"breadth_ratio": 0.6, "limit_up": 50, "limit_down": 2},
        phase_resonance={"phase": "震荡转升", "state": "confirmed"},
        micro_cycle={"status": "震荡转升"},
    )
    dimension = thesis.to_dict()["dimensions"]["index_sector_stock_resonance"]
    assert dimension["state"] == "resonant"
    assert "micro_cycle=震荡转升" in dimension["evidence"]


def test_report_context_exposes_micro_cycle_and_phase_resonance_directly():
    micro = {"status": "震荡转升"}
    phase = {"phase": "底部至今", "micro_resonance": {"state": "confirmed"}}
    payload = ReportContext(
        report_date="2026-08-19",
        policy=ReportPolicy.from_mode("observation"),
        micro_cycle=micro,
        phase_resonance=phase,
    ).to_dict()
    assert payload["micro_cycle"] == micro
    assert payload["phase_resonance"] == phase


def test_report_context_exposes_calibration_and_posterior_state():
    payload = ReportContext(
        report_date="2026-08-19",
        policy=ReportPolicy.from_mode("observation"),
        scenario_calibration={"schema_version": "scenario-calibration/v1"},
        scenario_posterior={"schema_version": "scenario-posterior/v1"},
        phase_snapshots=[{"phase": "close"}],
    ).to_dict()
    assert payload["scenario_calibration"]["schema_version"] == "scenario-calibration/v1"
    assert payload["scenario_posterior"]["schema_version"] == "scenario-posterior/v1"
    assert payload["phase_snapshots"] == [{"phase": "close"}]


def test_daily_snapshot_does_not_coerce_missing_market_counts_to_zero():
    from pathlib import Path
    source = Path("src/主线强度追踪.py").read_text(encoding="utf-8")
    block = source[source.index("_current_snapshot = {"):source.index("_previous_snapshot =", source.index("_current_snapshot = {"))]
    assert "'limit_up': int(float(_limit_count or advance_decline.get('zt') or 0))" not in block
    assert "'limit_down': int(float(advance_decline.get('dt') or 0))" not in block
    assert "'max_height': _snapshot_max_height" in block


def test_missing_mainline_concentration_is_unknown_not_balanced():
    thesis = build_market_thesis(
        report_date="2026-08-19",
        market_snapshot={"breadth_ratio": 0.5, "limit_up": 30, "limit_down": 5},
    )
    assert thesis.to_dict()["dimensions"]["mainline_structure"]["state"] == "unknown"


def test_positive_cycle_background_conflicting_with_weak_daily_breadth_is_not_resonant():
    thesis = build_market_thesis(
        report_date="2026-08-19",
        market_snapshot={"breadth_ratio": 0.2, "limit_up": 8, "limit_down": 20},
        phase_resonance={"phase": "震荡转升"},
        micro_cycle={"status": "反弹确认"},
    )
    assert thesis.to_dict()["dimensions"]["index_sector_stock_resonance"]["state"] in {"conflicted", "partial"}
