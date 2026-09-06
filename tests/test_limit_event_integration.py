import importlib
import json

import pandas as pd


def test_normal_cache_write_archives_source_events_without_changing_legacy_table(tmp_path, monkeypatch):
    import lianban_analysis as module
    from limit_events import load_limit_event_snapshot
    monkeypatch.setattr(module, "CACHE_FILE", str(tmp_path / "zt.csv"))
    monkeypatch.setattr(module, "_trim_cache", lambda *a, **kw: None)
    source = pd.DataFrame([{"code": "sz000001", "name": "测试", "pool_type": "ZT", "date": "2026-09-04",
        "limit_count": 2, "break_count": 0, "broken": False, "amount": 0,
        "source": "fixture_feed", "source_timestamp": "2026-09-04T15:01:00+08:00", "source_timestamp_kind": "fetched_at"}])
    legacy = module._legacy_pool_with_events(source, trade_date="2026-09-04", include_count=True)
    assert list(legacy.columns) == ["代码", "名称", "连板数"]
    module._save_cache({"20260904": legacy}, {})
    snapshot = load_limit_event_snapshot(tmp_path / "limit_events", "2026-09-04")
    assert snapshot["records"][0]["broken"] is False
    assert snapshot["records"][0]["break_count"] == 0
    assert snapshot["market_break_rate"] is None
    assert snapshot["field_coverage"]["amount"]["known"] == 1


def test_daily_fact_conversions_preserve_real_event_fields():
    module = importlib.import_module("主线强度追踪")
    fields = {"break_count": 0, "broken": False, "reclosed": None,
              "source": "fixture_feed", "source_timestamp": "2026-09-04T15:01:00+08:00"}
    local = pd.DataFrame([{**fields, "代码": "000001", "名称": "测试", "连板数": 2, "日期": "20260904"}])
    built = module._build_local_daily_fact_input("20260904", local)
    row = built["category"]["local_cache"][0]
    assert all(row[key] == value for key, value in fields.items())
    restored = module._snapshot_to_fact_input({"report_date": "2026-09-04", "limit_pool_rows": [
        {**fields, "code": "sz000001", "name": "测试", "height": 2}]})
    row = restored["category"]["snapshot"][0]
    assert all(row[key] == value for key, value in fields.items())


def test_observation_merge_never_changes_the_authoritative_pool_or_uses_wrong_day():
    from report_closure import merge_limit_event_observations
    facts = [{"code": "sz000001", "name": "事实名", "height": 2}]
    observed = {"trade_date": "2026-09-04", "records": [
        {"code": "sz000001", "name": "旧名", "trade_date": "2026-09-03", "broken": False},
        {"code": "sz000002", "trade_date": "2026-09-04", "broken": True}]}
    assert merge_limit_event_observations(facts, observed, report_date="2026-09-04") == facts
    observed["records"][0].update(trade_date="2026-09-04", break_count=0)
    result = merge_limit_event_observations(facts, observed, report_date="2026-09-04")
    assert len(result) == 1
    assert result[0]["name"] == "事实名"
    assert result[0]["broken"] is False
    assert result[0]["break_count"] == 0
