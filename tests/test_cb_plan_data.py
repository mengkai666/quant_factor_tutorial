"""防止日期重贴标签，以及余额、溢价等字段误导读者。"""
import json

import pytest

from cb_plan_data import SCHEMA, load_cb_plan_data, research_to_snapshot, validate_snapshot


def snapshot(day="2030-04-08"):
    return {"schema_version": SCHEMA, "as_of": day, "source_timestamp": day + "T07:03:00+00:00",
            "summary": {}, "rows": [{"code": "123456", "name": "示例转债", "stock_code": "300001", "stock_name": "示例正股",
            "source_phase": "close", "quote_time": "15:00:00",
            "source_date": day, "stock_source_date": day, "close": 160, "r1": 0, "r5": 7, "r10": -2, "r20": 9,
            "amount_yi": 5, "premium_pct": 0, "remaining_issue_yi": 1.2, "stock_r1": 0,
            "conversion_start": "2029-02-01", "redemption_status": "待核对公司公告"}]}


def research(day="2030-04-08"):
    return {"as_of": day, "retrieved_at": day + "T07:03:00+00:00", "market": {}, "bonds": [{
        "code": "123456", "name": "示例转债", "quote_date": day, "quote_time": "15:00:00", "stock_code": "300001", "stock_name": "示例正股",
        "bond": {"date": day, "close": 160, "day_change_pct": 2, "return_5d_pct": 7, "return_10d_pct": -2, "return_20d_pct": 9},
        "stock": {"date": day, "close": 20, "day_change_pct": 3}, "amount_yi": 5,
        "conversion_premium_pct": 20, "remaining_issue_yi": 1.2, "conversion_start": "2029-02-01",
        "metadata": {"ACTUAL_ISSUE_SCALE": 8, "TRANSFER_PREMIUM_RATIO": 100}, "redemption": {"redeem_icon": "G"}}]}


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_loader_requires_exact_day_and_never_writes(tmp_path):
    path = tmp_path / "data/cb_daily_snapshots/2030-04-08.json"
    write(path, snapshot())
    before = path.read_bytes()
    assert load_cb_plan_data("20300408", data_dir=tmp_path / "data", output_dir=tmp_path / "output")["status"] == "ready"
    missing = load_cb_plan_data("2030-04-09", data_dir=tmp_path / "data", output_dir=tmp_path / "output")
    assert missing["rows"] == [] and missing["as_of"] == "2030-04-09"
    assert path.read_bytes() == before
    assert not (tmp_path / "data/cb_daily_snapshots/2030-04-09.json").exists()


@pytest.mark.parametrize("changed", ["quote_date", "bond", "stock"])
def test_research_rejects_mixed_dates(changed):
    item = research()
    if changed == "quote_date":
        item["bonds"][0][changed] = "2030-04-09"
    else:
        item["bonds"][0][changed]["date"] = "2030-04-07"
    with pytest.raises(ValueError, match="日期未全部对齐"):
        research_to_snapshot(item, "2030-04-08")


def test_canonical_corruption_is_not_hidden_by_compatible_research(tmp_path):
    write(tmp_path / "output/research_any_name/cb_latest_summary.json", research())
    path = tmp_path / "data/cb_daily_snapshots/2030-04-08.json"
    payload = snapshot("2030-04-07")
    write(path, payload)
    result = load_cb_plan_data("2030-04-08", data_dir=tmp_path / "data", output_dir=tmp_path / "output")
    assert result["status"] == "invalid" and not result["rows"]


def test_research_discovery_uses_contents_and_preserves_source_time(tmp_path):
    write(tmp_path / "research_unrelated_label/cb_latest_summary.json", research())
    result = load_cb_plan_data("20300408", data_dir=tmp_path / "empty", output_dir=tmp_path)
    assert result["status"] == "ready"
    assert result["source_timestamp"] == "2030-04-08T07:03:00+00:00"
    assert result["rows"][0]["remaining_issue_yi"] == 1.2
    assert result["rows"][0]["premium_pct"] == 20


def test_absent_balance_and_premium_never_use_issue_size_or_placeholder():
    item = research()
    del item["bonds"][0]["remaining_issue_yi"]
    del item["bonds"][0]["conversion_premium_pct"]
    result = validate_snapshot(research_to_snapshot(item, item["as_of"]), item["as_of"])
    assert result["status"] == "partial"
    assert result["rows"][0]["remaining_issue_yi"] is None
    assert result["rows"][0]["premium_pct"] is None


def test_zero_returns_and_zero_premium_are_known_values():
    result = validate_snapshot(snapshot(), "2030-04-08")
    assert result["status"] == "ready"
    assert result["rows"][0]["r1"] == result["rows"][0]["premium_pct"] == 0


def test_quote_disagreement_invalidates_premium_and_history_disagreement_omits_returns():
    item = research()
    item["bonds"][0].update(jsl_close_matches_sina=False, quote_previous_close_matches_history=False)
    row = validate_snapshot(research_to_snapshot(item, item["as_of"]), item["as_of"])["rows"][0]
    assert row["premium_pct"] is None
    assert all(row[key] is None for key in ("r5", "r10", "r20"))
    assert row["close"] == 160


def test_future_conversion_period_and_redemption_are_not_claimed_safe():
    item = research()
    item["bonds"][0]["conversion_start"] = "2030-10-01"
    result = validate_snapshot(research_to_snapshot(item, item["as_of"]), item["as_of"])
    assert "不能立即兑现" in result["rows"][0]["reason"]
    item["bonds"][0]["redemption"] = {"redeem_icon": "R"}
    result = validate_snapshot(research_to_snapshot(item, item["as_of"]), item["as_of"])
    assert result["rows"][0]["lifecycle_blocked"] is True
    assert result["rows"][0]["classification"] == "交易安排核查"


def test_stock_date_mismatch_and_missing_row_date_invalidate_canonical():
    for field, value in (("source_date", None), ("stock_source_date", "2030-04-09")):
        payload = snapshot()
        payload["rows"][0][field] = value
        assert validate_snapshot(payload, "2030-04-08")["status"] == "invalid"


def test_missing_stock_date_clears_dependent_values_and_comovement():
    payload = snapshot()
    payload["rows"][0].update(stock_r1=3, stock_close=20, r1=2)
    del payload["rows"][0]["stock_source_date"]
    result = validate_snapshot(payload, "2030-04-08")
    assert result["status"] == "partial"
    row = result["rows"][0]
    assert row["classification"] == "数据待补"
    assert all(row[field] is None for field in ("stock_r1", "stock_close", "premium_pct"))
    assert "stock_source_date" in row["missing_fields"]


@pytest.mark.parametrize("quote_time", ["11:30:00", "", None, "25:00:00", "bad"])
def test_canonical_requires_close_evidence(quote_time):
    payload = snapshot()
    payload["rows"][0]["quote_time"] = quote_time
    del payload["rows"][0]["source_phase"]
    assert validate_snapshot(payload, "2030-04-08")["status"] == "invalid"


def test_declared_close_phase_cannot_override_intraday_quote_time():
    payload = snapshot()
    payload["rows"][0]["quote_time"] = "11:30:00"
    assert validate_snapshot(payload, "2030-04-08")["status"] == "invalid"


def test_optional_null_metadata_does_not_crash_read_only_loader():
    payload = snapshot()
    payload.update(warnings=None, sources=None)
    assert validate_snapshot(payload, "2030-04-08")["status"] == "ready"
    malformed = research()
    malformed["bonds"][0]["bond"] = ["错误结构"]
    with pytest.raises(ValueError, match="格式无效"):
        research_to_snapshot(malformed, "2030-04-08")
