from __future__ import annotations

import json


def _plan():
    return {
        "position": "2 成",
        "priority": {
            "primary": {"code": "sh600001", "name": "首选股", "sector": "AI算力", "role": "attack", "action": "条件确认后执行", "trigger": "联动", "invalid": "失效"},
            "alternates": [{"code": "sz000002", "name": "备选股", "sector": "AI算力", "role": "confirm", "action": "确认后执行", "trigger": "晋级", "invalid": "断板"}],
        },
    }


def test_trade_plan_records_only_primary_and_alternates_when_plan_is_permitted():
    from trade_plan_review import build_trade_plan_records

    got = build_trade_plan_records(
        _plan(), report_date="2026-09-03",
        readiness={"plan_permitted": True},
    )

    assert [row["priority"] for row in got] == ["primary", "alternate"]
    assert [row["code"] for row in got] == ["sh600001", "sz000002"]
    assert all(row["event_type"] == "trade_plan" for row in got)


def test_trade_plan_records_do_not_treat_unpermitted_observation_as_trade_plan():
    from trade_plan_review import build_trade_plan_records

    assert build_trade_plan_records(
        _plan(), report_date="2026-09-03", readiness={"plan_permitted": False}
    ) == []


def test_trade_plan_event_and_outcome_are_append_only_and_idempotent(tmp_path):
    from trade_plan_review import (
        append_trade_plan_once, append_trade_plan_outcome_once,
        build_trade_plan_review, build_trade_plan_records,
    )

    path = tmp_path / "history.jsonl"
    record = build_trade_plan_records(_plan(), report_date="2026-09-03", readiness={"plan_permitted": True})[0]
    assert append_trade_plan_once(path, record)["appended"] is True
    assert append_trade_plan_once(path, record)["appended"] is False
    plan_id = record["plan_id"]
    assert append_trade_plan_outcome_once(path, plan_id, "triggered_not_filled")["appended"] is True
    assert append_trade_plan_outcome_once(path, plan_id, "filled", actual={"net_pnl": 1.2})["appended"] is False

    review = build_trade_plan_review(path, report_date="2026-09-03")
    assert review["plan_count"] == 1
    assert review["outcome_count"] == 1
    assert review["triggered_count"] == 1
    assert review["filled_count"] == 0
    assert review["pnl_known_count"] == 0
    assert review["has_realized_trade_result"] is False
    assert "未成交" in review["note"]


def test_trade_plan_review_only_counts_explicit_filled_net_pnl(tmp_path):
    from trade_plan_review import (
        append_trade_plan_once, append_trade_plan_outcome_once,
        build_trade_plan_review, build_trade_plan_records,
    )

    path = tmp_path / "history.jsonl"
    records = build_trade_plan_records(_plan(), report_date="2026-09-03", readiness={"plan_permitted": True})
    for record in records:
        append_trade_plan_once(path, record)
    append_trade_plan_outcome_once(path, records[0]["plan_id"], "filled", actual={"net_pnl": 1.2})
    append_trade_plan_outcome_once(path, records[1]["plan_id"], "unknown")

    review = build_trade_plan_review(path, report_date="2026-09-03")
    assert review["filled_count"] == 1
    assert review["pnl_known_count"] == 1
    assert review["net_pnl"] == 1.2
    assert review["status_counts"]["unknown"] == 1
    json.dumps(review, ensure_ascii=False)
