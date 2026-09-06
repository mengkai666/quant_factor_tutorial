"""Offline contract tests for limit-pool event retention and quality archives."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from data_sources.limit_pool_provider import LimitPoolProvider
from data_sources.limit_pool_sources import EastmoneyLimitPoolSource, ThsLimitUpSource
from data_sources.models import FetchStatus


TRADE_DATE = "2026-09-04"
FETCHED_AT = datetime(2026, 9, 4, 7, 5, tzinfo=timezone.utc)
EVENT_FIELDS = (
    "limit_up_attempted", "broken", "reclosed", "board_type",
    "first_limit_time", "last_limit_time", "limit_up_fund",
    "turnover_rate", "amount", "float_market_cap", "break_count",
)


def _session(payload):
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    return SimpleNamespace(get=lambda *args, **kwargs: response)


def _provider(zt, dt=None):
    return LimitPoolProvider(
        fetch_zt=lambda _: zt,
        fetch_dt=lambda _: pd.DataFrame() if dt is None else dt,
        now=lambda: FETCHED_AT,
    )


def test_eastmoney_adapter_retains_real_limit_event_fields_and_zero():
    source = EastmoneyLimitPoolSource(session=_session({"rc": 0, "data": {"pool": [
        {"c": "600000", "n": "样本", "lbc": 2, "fbt": 93000,
         "lbt": 145905, "fund": 0, "hs": 0, "amount": 0,
         "ltsz": 450000000, "zbc": 0, "p": 11000},
    ]}}), min_interval=0)

    row = source.fetch_zt(TRADE_DATE).iloc[0].to_dict()

    assert row.get("first_limit_time") == 93000
    assert row.get("last_limit_time") == 145905
    assert row.get("limit_up_fund") == 0
    assert row.get("turnover_rate") == 0
    assert row.get("amount") == 0
    assert row.get("float_market_cap") == 450000000
    assert row.get("break_count") == 0
    assert row.get("board_type") is None
    assert row["event_evidence"]["break_count"] == {"field": "zbc", "value": 0}


def test_ths_adapter_maps_named_fields_without_guessing_numeric_field_ids():
    source = ThsLimitUpSource(session=_session({"status_code": 0, "data": {"info": [
        {"code": "000001", "name": "样本", "high_days": "3天2板",
         "first_limit_up_time": 1788485400, "last_limit_up_time": 1788505140,
         "order_amount": 1200000, "turnover_rate": 1.25,
         "turnover": 8000000, "currency_value": 640000000,
         "open_num": 2, "limit_up_type": "换手板"},
    ]}}))

    row = source.fetch_zt(TRADE_DATE).iloc[0].to_dict()

    assert row.get("first_limit_time") == 1788485400
    assert row.get("last_limit_time") == 1788505140
    assert row.get("limit_up_fund") == 1200000
    assert row.get("turnover_rate") == 1.25
    assert row.get("amount") == 8000000
    assert row.get("float_market_cap") == 640000000
    assert row.get("break_count") == 2
    assert row.get("board_type") == "换手板"
    assert row["event_evidence"]["amount"]["field"] == "turnover"


@pytest.mark.parametrize("raw_count", [None, 0, 2])
def test_eastmoney_adapter_keeps_missing_count_distinct_from_explicit_zero(raw_count):
    source = EastmoneyLimitPoolSource(session=_session({"rc": 0, "data": {"pool": [
        {"c": "600000", "zbc": raw_count}, {"c": "000001"},
    ]}}), min_interval=0)

    frame = source.fetch_zt(TRADE_DATE)

    assert "break_count" in frame.columns
    assert frame.iloc[0]["break_count"] == raw_count
    assert frame.iloc[1]["break_count"] is None


@pytest.mark.parametrize("adapter", ["eastmoney", "ths"])
def test_adapter_labels_fetch_time_as_fetch_time_not_limit_event_time(adapter):
    if adapter == "eastmoney":
        source = EastmoneyLimitPoolSource(
            session=_session({"rc": 0, "data": {"pool": [{"c": "600000"}]}}),
            min_interval=0, now=lambda: FETCHED_AT,
        )
    else:
        source = ThsLimitUpSource(
            session=_session({"status_code": 0, "data": {"info": [{"code": "600000"}]}}),
            now=lambda: FETCHED_AT,
        )

    frame = source.fetch_zt(TRADE_DATE)

    assert frame.attrs["trade_date"] == TRADE_DATE
    assert frame.attrs["trade_date_source"] == "request"
    assert frame.attrs["source_timestamp"] == FETCHED_AT.isoformat()
    assert frame.attrs["source_timestamp_kind"] == "fetched_at"
    assert frame.attrs["fetched_at"] == FETCHED_AT.isoformat()
    assert frame.attrs["source"] == ("eastmoney_push2ex" if adapter == "eastmoney" else "ths_limit_up")


def test_eastmoney_down_pool_does_not_relabel_down_limit_fields_as_up_events():
    source = EastmoneyLimitPoolSource(session=_session({"rc": 0, "data": {"pool": [
        {"c": "600000", "fund": 800, "fbt": 93000, "lbt": 150000,
         "zbc": 2, "hs": 0, "amount": 0, "ltsz": 1000000},
    ]}}), min_interval=0)

    row = source.fetch_dt(TRADE_DATE).iloc[0].to_dict()

    assert row.get("amount") == 0
    assert row.get("turnover_rate") == 0
    assert row.get("float_market_cap") == 1000000
    for field in ("first_limit_time", "last_limit_time", "limit_up_fund", "break_count"):
        assert field not in row


def test_provider_preserves_akshare_fields_without_altering_market_status():
    raw = pd.DataFrame([{
        "代码": "920117", "名称": "样本", "连板数": 2,
        "首次封板时间": "093000", "最后封板时间": "145905",
        "封板资金": 0, "换手率": 0, "成交额": 8000000,
        "流通市值": 640000000, "炸板次数": 0,
    }])

    result = _provider(raw).fetch_day(TRADE_DATE)
    row = result.data.iloc[0].to_dict()

    assert result.status is FetchStatus.SUCCESS
    assert row.get("break_count") == 0
    assert row.get("first_limit_time") == "093000"
    assert row.get("last_limit_time") == "145905"
    assert row.get("limit_up_fund") == 0
    assert row.get("turnover_rate") == 0
    assert row.get("amount") == 8000000
    assert row.get("float_market_cap") == 640000000
    assert row.get("trade_date") == TRADE_DATE
    assert row["date"] == TRADE_DATE
    assert row["source"] == "akshare_em"
    assert row["source_timestamp"] == FETCHED_AT.isoformat()
    assert row["source_timestamp_kind"] == "fetched_at"
    assert row["fetched_at"] == FETCHED_AT.isoformat()
    assert row["trade_date_source"] == "request"
    assert row["event_evidence"]["break_count"]["field"] == "炸板次数"


def test_provider_retains_explicit_false_and_null_without_alias_fallback():
    raw = pd.DataFrame([
        {"code": "600000", "limit_up_attempted": False, "broken": False,
         "reclosed": False, "board_type": "one_word", "limit_up_fund": 0},
        {"code": "000001", "limit_up_attempted": None, "broken": None,
         "reclosed": None, "board_type": None, "limit_up_fund": None,
         "封板资金": 999},
    ], dtype=object)
    original = raw.copy(deep=True)

    frame = _provider(raw).fetch_day(TRADE_DATE).data

    for field in ("limit_up_attempted", "broken", "reclosed"):
        assert field in frame.columns
        assert frame.iloc[0][field] is False
        assert frame.iloc[1][field] is None
    assert frame.iloc[0]["limit_up_fund"] == 0
    assert frame.iloc[1]["limit_up_fund"] is None
    pd.testing.assert_frame_equal(raw, original)


@pytest.mark.parametrize("upstream_time", [None, "2026-09-03T15:00:00+08:00"])
def test_provider_preserves_source_timestamp_instead_of_freshening_old_evidence(upstream_time):
    raw = pd.DataFrame([{
        "code": "600000", "source": "fixture_cache",
        "source_timestamp": upstream_time, "source_timestamp_kind": "quote_time",
        "trade_date": "2026-09-03", "trade_date_source": "source",
        "first_limit_time": "09:30:00",
    }], dtype=object)

    row = _provider(raw).fetch_day(TRADE_DATE).data.iloc[0].to_dict()

    assert row["source"] == "fixture_cache"
    assert row["source_timestamp"] == upstream_time
    assert row["source_timestamp_kind"] == "quote_time"
    assert row["fetched_at"] == FETCHED_AT.isoformat()
    assert row["trade_date"] == "2026-09-03"
    assert row["date"] == TRADE_DATE  # legacy request date is unchanged
    assert row["first_limit_time"] == "09:30:00"


def test_provider_down_pool_keeps_common_numbers_but_not_ambiguous_up_fields():
    dt = pd.DataFrame([{
        "代码": "000001", "最后封板时间": "150000", "封单资金": 700,
        "开板次数": 2, "成交额": 0, "流通市值": 1000000, "换手率": 0,
    }])

    result = _provider(pd.DataFrame(), dt).fetch_day(TRADE_DATE)
    row = result.data.iloc[0].to_dict()

    assert result.status is FetchStatus.SUCCESS
    assert row.get("amount") == 0
    assert row.get("float_market_cap") == 1000000
    assert row.get("turnover_rate") == 0
    for field in ("first_limit_time", "last_limit_time", "limit_up_fund", "break_count"):
        assert field in row and row[field] is None


def test_provider_missing_event_fields_are_unknown_not_a_new_gate():
    result = _provider(pd.DataFrame([{"code": "600000", "close": 11, "high": 11}])).fetch_day(TRADE_DATE)
    row = result.data.iloc[0].to_dict()

    assert result.status is FetchStatus.SUCCESS
    assert result.message == ""
    assert set(EVENT_FIELDS).issubset(row)
    assert all(row[field] is None for field in EVENT_FIELDS)


@pytest.mark.parametrize("adapter", ["eastmoney", "ths"])
def test_successful_empty_adapter_retains_quality_provenance(adapter):
    if adapter == "eastmoney":
        source = EastmoneyLimitPoolSource(
            session=_session({"rc": 0, "data": {"pool": []}}),
            min_interval=0, now=lambda: FETCHED_AT,
        )
    else:
        source = ThsLimitUpSource(
            session=_session({"status_code": 0, "data": {"info": []}}),
            now=lambda: FETCHED_AT,
        )

    frame = source.fetch_zt(TRADE_DATE)

    assert frame.empty
    assert frame.attrs["source_timestamp"] == FETCHED_AT.isoformat()
    assert frame.attrs["discarded_rows"] == 0


# The snapshot API is deliberately imported lazily so adapter tests still run
# while the new module is absent during the first red phase.
def _events():
    return importlib.import_module("limit_events")


def test_ths_numeric_field_ids_are_not_guessed_as_event_fields():
    source = ThsLimitUpSource(session=_session({"status_code": 0, "data": {"info": [
        {"code": "600000", "330323": 93000, "330324": 150000, "9003": 3},
    ]}}))

    row = source.fetch_zt(TRADE_DATE).iloc[0].to_dict()

    assert all(row.get(field) is None for field in EVENT_FIELDS)
    assert row["event_evidence"] == {}


@pytest.mark.parametrize("adapter", ["eastmoney", "ths"])
def test_adapter_preserves_explicit_flags_nulls_and_canonical_precedence(adapter):
    raw = {"code": "600000", "c": "600000", "limit_up_attempted": False,
           "broken": False, "reclosed": False, "board_type": None,
           "limit_up_type": "换手板", "limit_up_fund": None, "fund": 999,
           "order_amount": 999, "source_timestamp": None,
           "source_timestamp_kind": "quote_time", "trade_date": "2026-09-03"}
    if adapter == "eastmoney":
        source = EastmoneyLimitPoolSource(
            session=_session({"rc": 0, "data": {"pool": [raw]}}), min_interval=0)
    else:
        source = ThsLimitUpSource(
            session=_session({"status_code": 0, "data": {"info": [raw]}}))

    row = source.fetch_zt(TRADE_DATE).iloc[0].to_dict()

    assert row["limit_up_attempted"] is False
    assert row["broken"] is False
    assert row["reclosed"] is False
    assert row["board_type"] is None
    assert row["limit_up_fund"] is None
    assert row["source_timestamp"] is None
    assert row["source_timestamp_kind"] == "quote_time"
    assert row["trade_date"] == "2026-09-03"


def test_provider_preserves_frame_provenance_and_original_evidence_without_aliasing():
    raw = pd.DataFrame([{
        "code": "600000", "break_count": 0,
        "event_evidence": {"break_count": {"field": "zbc", "value": 0}},
    }], dtype=object)
    raw.attrs.update(source="cached_em", source_timestamp=None,
                     source_timestamp_kind="quote_time", trade_date="2026-09-03",
                     trade_date_source="source", discarded_rows=2)

    result = _provider(raw).fetch_day(TRADE_DATE)
    row = result.data.iloc[0].to_dict()

    assert result.status is FetchStatus.SUCCESS
    assert row["source"] == "cached_em"
    assert row["source_timestamp"] is None
    assert row["source_timestamp_kind"] == "quote_time"
    assert row["trade_date"] == "2026-09-03"
    assert row["trade_date_source"] == "source"
    assert row["event_evidence"]["break_count"] == {"field": "zbc", "value": 0}
    row["event_evidence"]["break_count"]["value"] = 99
    assert raw.iloc[0]["event_evidence"]["break_count"]["value"] == 0
    assert result.data.attrs["pool_provenance"]["ZT"]["discarded_rows"] == 2


def test_provider_empty_result_preserves_provenance_for_both_pools():
    empty = pd.DataFrame()
    empty.attrs.update(source="empty_fixture", source_timestamp=FETCHED_AT.isoformat(),
                       source_timestamp_kind="fetched_at", trade_date=TRADE_DATE)

    result = _provider(empty, empty).fetch_day(TRADE_DATE)

    assert result.status is FetchStatus.ZERO
    assert result.data.empty
    for pool in ("ZT", "DT"):
        metadata = result.data.attrs["pool_provenance"][pool]
        assert metadata["source"] == "empty_fixture"
        assert metadata["source_timestamp"] == FETCHED_AT.isoformat()
        assert metadata["trade_date"] == TRADE_DATE
        assert metadata["discarded_rows"] == 0


def test_provider_nullable_scalars_become_none_but_false_and_zero_survive():
    raw = pd.DataFrame({"code": ["600000", "000001"],
                        "broken": pd.Series([False, pd.NA], dtype="boolean"),
                        "break_count": pd.Series([0, pd.NA], dtype="Int64"),
                        "amount": [0.0, float("nan")]})

    frame = _provider(raw).fetch_day(TRADE_DATE).data

    assert frame.iloc[0]["broken"] is False
    assert frame.iloc[0]["break_count"] == 0
    assert frame.iloc[0]["amount"] == 0
    for field in ("broken", "break_count", "amount"):
        assert frame.iloc[1][field] is None


def test_new_event_fields_cannot_promote_an_incomplete_pool_fetch():
    provider = LimitPoolProvider(
        fetch_zt=lambda _: pd.DataFrame([{
            "code": "600000", "broken": False, "break_count": 0,
            "limit_up_attempted": True, "board_type": "one_word",
        }]),
        fetch_dt=lambda _: (_ for _ in ()).throw(TimeoutError("DT offline")),
        now=lambda: FETCHED_AT,
    )

    result = provider.fetch_day(TRADE_DATE)

    assert result.status is FetchStatus.PARTIAL
    assert "DT offline" in result.message
    assert result.data.iloc[0]["broken"] is False


def test_event_field_coverage_counts_false_and_zero_as_known_and_empty_as_unknown():
    rows = [{"broken": False, "break_count": 0, "limit_up_fund": 0},
            {"broken": None, "break_count": float("nan"), "limit_up_fund": pd.NA},
            {"broken": True, "break_count": 2}]

    coverage = _events().event_field_coverage(rows)

    assert coverage["broken"] == {"known": 2, "missing": 1, "total": 3, "coverage": 2 / 3}
    assert coverage["break_count"] == {"known": 2, "missing": 1, "total": 3, "coverage": 2 / 3}
    assert coverage["limit_up_fund"] == {"known": 1, "missing": 2, "total": 3, "coverage": 1 / 3}
    assert coverage["board_type"] == {"known": 0, "missing": 3, "total": 3, "coverage": 0.0}
    assert _events().event_field_coverage([])["broken"] == {
        "known": 0, "missing": 0, "total": 0, "coverage": None,
    }


def test_snapshot_is_pure_preserves_unknowns_and_never_infers_a_board_or_market_rate():
    rows = [{"code": "sh600000", "pool_type": "ZT", "close": 11, "high": 11,
             "first_limit_time": "093000", "last_limit_time": "093000"},
            {"code": "sz000001", "pool_type": "ZT", "break_count": 0,
             "broken": False, "limit_up_attempted": True, "reclosed": False}]
    original = deepcopy(rows)

    snapshot = _events().build_limit_event_snapshot(
        rows, TRADE_DATE, source="fixture", source_timestamp=FETCHED_AT,
        source_timestamp_kind="fetched_at")

    assert rows == original
    assert snapshot == _events().build_limit_event_snapshot(
        rows, TRADE_DATE, source="fixture", source_timestamp=FETCHED_AT,
        source_timestamp_kind="fetched_at")
    assert snapshot["trade_date"] == TRADE_DATE
    assert snapshot["row_count"] == 2
    assert snapshot["population_scope"] == "closing_limit_pool"
    assert snapshot["market_break_rate"] is None
    assert snapshot["full_market_coverage"] is False
    first, second = snapshot["records"]
    for field in ("limit_up_attempted", "broken", "reclosed", "board_type", "break_count"):
        assert first[field] is None
    assert first["source"] == "fixture"
    assert first["source_timestamp"] == FETCHED_AT.isoformat()
    assert first["source_timestamp_kind"] == "fetched_at"
    assert first["trade_date_source"] == "request"
    assert second["broken"] is False
    assert second["reclosed"] is False
    assert second["break_count"] == 0
    assert second["board_type"] is None
    assert snapshot["field_coverage"]["broken"]["known"] == 1


def test_snapshot_retains_real_positive_break_count_without_inventing_event_flags():
    snapshot = _events().build_limit_event_snapshot(
        [{"code": "600000", "pool_type": "ZT", "break_count": 2}], TRADE_DATE)

    row = snapshot["records"][0]
    assert row["break_count"] == 2
    assert row["event_evidence"]["break_count"] == {"field": "break_count", "value": 2}
    assert row["broken"] is None
    assert row["reclosed"] is None
    assert row["limit_up_attempted"] is None
    assert snapshot["market_break_rate"] is None


def test_snapshot_preserves_row_provenance_including_nulls_and_reports_date_mismatch():
    frame = pd.DataFrame([
        {"code": "600000", "source": "stale_fixture", "source_timestamp": None,
         "source_timestamp_kind": "quote_time", "trade_date": "2026-09-03",
         "trade_date_source": "source", "break_count": 0},
        {"code": "000001", "source": None, "source_timestamp": None,
         "source_timestamp_kind": None, "trade_date": None,
         "trade_date_source": None, "break_count": None},
    ], dtype=object)
    frame.attrs["source_timestamp"] = FETCHED_AT.isoformat()
    original = frame.copy(deep=True)

    snapshot = _events().build_limit_event_snapshot(frame, TRADE_DATE, source="fallback")

    pd.testing.assert_frame_equal(frame, original)
    first, second = snapshot["records"]
    assert first["source"] == "stale_fixture"
    assert first["source_timestamp"] is None
    assert first["trade_date"] == "2026-09-03"
    assert second["source"] is None
    assert second["source_timestamp"] is None
    assert second["trade_date"] is None
    assert snapshot["quality"]["trade_date_mismatches"] == 1
    assert snapshot["quality"]["missing_trade_date"] == 1
    assert snapshot["quality"]["missing_source_timestamp"] == 2
    assert snapshot["quality"]["missing_source"] == 1


def test_provider_snapshot_roundtrip_preserves_original_break_evidence_and_zero_funding():
    source = EastmoneyLimitPoolSource(
        session=_session({"rc": 0, "data": {"pool": [
            {"c": "600000", "zbc": 0, "fund": 0},
        ]}}), min_interval=0, now=lambda: FETCHED_AT)
    frame = _provider(source.fetch_zt(TRADE_DATE)).fetch_day(TRADE_DATE).data
    original_evidence = deepcopy(frame.iloc[0]["event_evidence"])

    snapshot = _events().build_limit_event_snapshot(frame, TRADE_DATE)
    row = json.loads(json.dumps(snapshot, allow_nan=False))["records"][0]

    assert row["event_evidence"]["break_count"] == {"field": "zbc", "value": 0}
    assert row["limit_up_fund"] == 0
    assert row["source"] == "eastmoney_push2ex"
    assert row["source_timestamp"] == FETCHED_AT.isoformat()
    snapshot["records"][0]["event_evidence"]["break_count"]["value"] = 99
    assert frame.iloc[0]["event_evidence"] == original_evidence


def test_empty_snapshot_keeps_pool_provenance_and_does_not_claim_zero_break_rate():
    frame = pd.DataFrame()
    frame.attrs["pool_provenance"] = {"ZT": {"source": "fixture",
        "source_timestamp": FETCHED_AT.isoformat(), "discarded_rows": 2}}

    snapshot = _events().build_limit_event_snapshot(frame, TRADE_DATE)

    assert snapshot["records"] == []
    assert snapshot["market_break_rate"] is None
    assert snapshot["field_coverage"]["break_count"]["coverage"] is None
    assert snapshot["pool_provenance"]["ZT"]["source"] == "fixture"
    assert snapshot["quality"]["discarded_rows"] == 2


@pytest.mark.parametrize("bad_date", ["../other", "20260904", "2026-02-30", "2026-9-4"])
def test_snapshot_rejects_invalid_archive_partition_dates(bad_date):
    with pytest.raises(ValueError, match="trade_date"):
        _events().build_limit_event_snapshot([], bad_date)


def test_daily_archive_is_idempotent_replaces_only_that_day_and_roundtrips(tmp_path):
    events = _events()
    snapshot = events.build_limit_event_snapshot(
        [{"code": "600000", "name": "样本", "break_count": 0}], TRADE_DATE)
    original = deepcopy(snapshot)
    archive_dir = tmp_path / "limit_events"

    path = events.archive_limit_event_snapshot(snapshot, archive_dir)
    initial_bytes, initial_mtime = path.read_bytes(), path.stat().st_mtime_ns
    repeated = events.archive_limit_event_snapshot(snapshot, archive_dir)

    assert path == archive_dir / "2026-09-04.json"
    assert repeated == path
    assert path.read_bytes() == initial_bytes
    assert path.stat().st_mtime_ns == initial_mtime
    assert snapshot == original
    assert events.load_limit_event_snapshot(archive_dir, TRADE_DATE) == snapshot
    assert events.load_limit_event_snapshot(archive_dir, "2026-09-07") is None
    assert "样本" in path.read_text(encoding="utf-8")

    later = events.build_limit_event_snapshot([], "2026-09-07")
    events.archive_limit_event_snapshot(later, archive_dir)
    updated = events.build_limit_event_snapshot([{"code": "600000", "break_count": 2}], TRADE_DATE)
    events.archive_limit_event_snapshot(updated, archive_dir)
    assert events.load_limit_event_snapshot(archive_dir, TRADE_DATE) == updated
    assert events.load_limit_event_snapshot(archive_dir, "2026-09-07") == later
    assert sorted(p.name for p in archive_dir.iterdir()) == ["2026-09-04.json", "2026-09-07.json"]


def test_daily_archive_failed_replace_leaves_previous_day_file_intact(tmp_path, monkeypatch):
    events = _events()
    snapshot = events.build_limit_event_snapshot([], TRADE_DATE)
    path = events.archive_limit_event_snapshot(snapshot, tmp_path)
    before = path.read_bytes()

    def fail_replace(*_args):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(events.os, "replace", fail_replace)
    updated = events.build_limit_event_snapshot([{"code": "600000"}], TRADE_DATE)
    with pytest.raises(OSError, match="replace failure"):
        events.archive_limit_event_snapshot(updated, tmp_path)

    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_daily_archive_rejects_bad_dates_and_corruption_without_silent_reset(tmp_path):
    events = _events()
    snapshot = events.build_limit_event_snapshot([], TRADE_DATE)
    snapshot["trade_date"] = "../escape"
    with pytest.raises(ValueError, match="trade_date"):
        events.archive_limit_event_snapshot(snapshot, tmp_path)
    assert list(tmp_path.iterdir()) == []

    path = tmp_path / "2026-09-04.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError):
        events.load_limit_event_snapshot(tmp_path, TRADE_DATE)
    wrong_day = events.build_limit_event_snapshot([], "2026-09-07")
    path.write_text(json.dumps(wrong_day), encoding="utf-8")
    with pytest.raises(ValueError, match="trade_date"):
        events.load_limit_event_snapshot(tmp_path, TRADE_DATE)


def test_provider_retains_per_row_events_when_input_index_is_not_unique():
    raw = pd.DataFrame([
        {"code": "600000", "name": "A", "limit_count": 2, "break_count": 0},
        {"code": "000001", "name": "B", "limit_count": 3, "break_count": 2},
    ], index=[5, 5])

    frame = _provider(raw).fetch_day(TRADE_DATE).data

    assert frame["name"].tolist() == ["A", "B"]
    assert frame["limit_count"].tolist() == [2, 3]
    assert frame["break_count"].tolist() == [0, 2]


def test_snapshot_does_not_create_evidence_for_missing_normalized_source_fields():
    snapshot = _events().build_limit_event_snapshot([
        {"code": "600000", "break_count": None, "event_evidence": {}},
        {"code": "000001", "break_count": None,
         "event_evidence": {"break_count": {"field": "zbc", "value": None}}},
    ], TRADE_DATE)

    first, second = snapshot["records"]
    assert "break_count" not in first["event_evidence"]
    assert second["event_evidence"]["break_count"] == {"field": "zbc", "value": None}


def test_snapshot_source_time_and_day_kinds_belong_to_their_row_not_fallbacks():
    frame = pd.DataFrame([{"code": "600000", "source_timestamp": "2026-09-03T15:00:00+08:00",
                           "trade_date": "2026-09-03"}])
    frame.attrs.update(source_timestamp=FETCHED_AT.isoformat(),
                       source_timestamp_kind="fetched_at", trade_date=TRADE_DATE,
                       trade_date_source="request")

    row = _events().build_limit_event_snapshot(frame, TRADE_DATE)["records"][0]

    assert row["source_timestamp_kind"] == "source"
    assert row["trade_date_source"] == "source"


def test_empty_snapshot_preserves_frame_source_timestamp_and_trade_date():
    frame = pd.DataFrame()
    frame.attrs.update(source="empty_cache", source_timestamp=None,
                       source_timestamp_kind="quote_time", trade_date="2026-09-03",
                       trade_date_source="source")

    snapshot = _events().build_limit_event_snapshot(frame, TRADE_DATE)

    assert snapshot["provenance"]["source"] == "empty_cache"
    assert snapshot["provenance"]["source_timestamp"] is None
    assert snapshot["provenance"]["source_timestamp_kind"] == "quote_time"
    assert snapshot["provenance"]["trade_date"] == "2026-09-03"


@pytest.mark.parametrize("adapter", ["eastmoney", "ths"])
def test_adapter_retains_explicit_response_level_provenance(adapter):
    metadata = {"trade_date": "2026-09-03", "source_timestamp": None,
                "source_timestamp_kind": "quote_time"}
    if adapter == "eastmoney":
        source = EastmoneyLimitPoolSource(session=_session({
            "rc": 0, "data": {**metadata, "pool": [{"c": "600000"}]},
        }), min_interval=0, now=lambda: FETCHED_AT)
    else:
        source = ThsLimitUpSource(session=_session({
            "status_code": 0, "data": {**metadata, "info": [{"code": "600000"}]},
        }), now=lambda: FETCHED_AT)

    frame = source.fetch_zt(TRADE_DATE)
    row = frame.iloc[0].to_dict()

    assert row["trade_date"] == "2026-09-03"
    assert row["trade_date_source"] == "source"
    assert row["source_timestamp"] is None
    assert row["source_timestamp_kind"] == "quote_time"
    assert frame.attrs["source_timestamp"] is None
    assert frame.attrs["fetched_at"] == FETCHED_AT.isoformat()


def test_provider_list_records_distinguish_absent_canonical_keys_from_explicit_nulls():
    rows = [
        {"code": "600000", "break_count": None, "炸板次数": 9},
        {"code": "000001", "炸板次数": 0},
    ]
    original = deepcopy(rows)

    frame = _provider(rows).fetch_day(TRADE_DATE).data

    assert frame.iloc[0]["break_count"] is None
    assert frame.iloc[1]["break_count"] == 0
    assert frame.iloc[1]["event_evidence"]["break_count"] == {"field": "炸板次数", "value": 0}
    assert rows == original


@pytest.mark.parametrize("pool_type", ["ZT", "DT"])
@pytest.mark.parametrize("empty", [False, True])
def test_review_eastmoney_qdate_survives_to_archive_and_flags_even_empty_pool(
        tmp_path, pool_type, empty):
    source = EastmoneyLimitPoolSource(session=_session({"rc": 0, "data": {
        "qdate": "20260903", "pool": [] if empty else [{"c": "600000", "zbc": 0}],
    }}), min_interval=0, now=lambda: FETCHED_AT)
    source_frame = (source.fetch_zt if pool_type == "ZT" else source.fetch_dt)(TRADE_DATE)
    result = (_provider(source_frame) if pool_type == "ZT" else
              _provider(pd.DataFrame(), source_frame)).fetch_day(TRADE_DATE)
    events = _events()
    snapshot = events.build_limit_event_snapshot(result.data, TRADE_DATE)
    events.archive_limit_event_snapshot(snapshot, tmp_path)
    archived = events.load_limit_event_snapshot(tmp_path, TRADE_DATE)

    assert archived["quality"]["trade_date_mismatches"] == 1
    assert archived["quality"]["record_trade_date_mismatches"] == (0 if empty else 1)
    assert archived["quality"]["provenance_trade_date_mismatches"] == 1
    assert archived["trade_date"] == TRADE_DATE  # partition, not upstream evidence
    assert result.status is (FetchStatus.ZERO if empty else FetchStatus.SUCCESS)
    assert source_frame.attrs["trade_date"] == "2026-09-03"
    assert source_frame.attrs["trade_date_source"] == "source"
    assert archived["pool_provenance"][pool_type]["trade_date"] == "2026-09-03"
    assert archived["pool_provenance"][pool_type]["trade_date_evidence"] == {
        "field": "qdate", "value": "20260903",
    }
    if empty:
        assert archived["row_count"] == 0
        assert archived["field_coverage"]["break_count"]["coverage"] is None
    else:
        row = archived["records"][0]
        assert row["date"] == TRADE_DATE
        assert row["trade_date"] == "2026-09-03"
        assert row["trade_date_evidence"] == {"field": "qdate", "value": "20260903"}


@pytest.mark.parametrize("explicit_date", [None, "2026-09-02"])
def test_review_eastmoney_qdate_never_overwrites_an_explicit_source_date(explicit_date):
    source = EastmoneyLimitPoolSource(session=_session({"rc": 0, "data": {
        "trade_date": explicit_date, "qdate": "20260903", "pool": [{"c": "600000"}],
    }}), min_interval=0, now=lambda: FETCHED_AT)

    frame = source.fetch_zt(TRADE_DATE)

    assert frame.attrs["trade_date"] == explicit_date
    assert frame.iloc[0]["trade_date"] == explicit_date
    assert frame.attrs["trade_date_evidence"] == {"field": "trade_date", "value": explicit_date}


def test_review_numpy_datetime_timestamp_archives_as_iso_without_losing_false_zero_or_null(tmp_path):
    timestamp = np.datetime64("2026-09-03T07:00:00", "s")
    frame = pd.DataFrame([{"code": "600000", "broken": np.bool_(False),
                           "break_count": np.int64(0), "amount": 0,
                           "limit_up_fund": None}], dtype=object)
    frame.attrs.update(source="cached_numpy", source_timestamp=timestamp,
                       source_timestamp_kind="quote_time")
    frame.attrs["pool_provenance"] = {"ZT": {"source_timestamp": timestamp}}
    events = _events()
    snapshot = events.build_limit_event_snapshot(frame, TRADE_DATE)

    events.archive_limit_event_snapshot(snapshot, tmp_path)
    archived = events.load_limit_event_snapshot(tmp_path, TRADE_DATE)

    assert archived["provenance"]["source_timestamp"] == "2026-09-03T07:00:00"
    assert archived["pool_provenance"]["ZT"]["source_timestamp"] == "2026-09-03T07:00:00"
    row = archived["records"][0]
    assert row["source_timestamp"] == "2026-09-03T07:00:00"
    assert row["source_timestamp_kind"] == "quote_time"
    assert row["broken"] is False
    assert row["break_count"] == 0
    assert row["amount"] == 0
    assert row["limit_up_fund"] is None
    assert frame.attrs["source_timestamp"] == timestamp


@pytest.mark.parametrize("location", ["row", "attrs"])
@pytest.mark.parametrize("source_date, raw_value", [
    ("20260904", "20260904"),
    (20260904, 20260904),
    (np.int64(20260904), 20260904),
    (pd.Timestamp("2026-09-04"), "2026-09-04T00:00:00"),
    (pd.Timestamp("2026-09-04T15:05:00+08:00"), "2026-09-04T15:05:00+08:00"),
])
def test_review_same_day_formats_are_normalized_without_losing_raw_source_evidence(
        tmp_path, location, source_date, raw_value):
    row = {"code": "600000", "broken": False, "break_count": 0}
    if location == "row":
        row["trade_date"] = source_date
    frame = pd.DataFrame([row], dtype=object)
    if location == "attrs":
        frame.attrs["trade_date"] = source_date
    result = _provider(frame).fetch_day(TRADE_DATE)
    events = _events()
    snapshot = events.build_limit_event_snapshot(result.data, TRADE_DATE)
    events.archive_limit_event_snapshot(snapshot, tmp_path)
    archived = events.load_limit_event_snapshot(tmp_path, TRADE_DATE)

    assert archived["quality"]["trade_date_mismatches"] == 0
    assert result.data.iloc[0]["trade_date"] == TRADE_DATE
    observed = archived["records"][0]
    assert observed["trade_date"] == TRADE_DATE
    assert observed["trade_date_source"] == "source"
    assert observed["trade_date_evidence"] == {"field": "trade_date", "value": raw_value}
    assert observed["broken"] is False
    assert observed["break_count"] == 0
    assert result.status is FetchStatus.SUCCESS


def test_review_explicit_null_date_is_not_replaced_by_request_or_frame_date():
    frame = pd.DataFrame([{"code": "600000", "trade_date": None, "date": TRADE_DATE,
                           "broken": False, "break_count": 0}], dtype=object)
    frame.attrs["trade_date"] = TRADE_DATE

    snapshot = _events().build_limit_event_snapshot(_provider(frame).fetch_day(TRADE_DATE).data, TRADE_DATE)

    row = snapshot["records"][0]
    assert row["trade_date"] is None
    assert row["trade_date_evidence"] == {"field": "trade_date", "value": None}
    assert row["broken"] is False
    assert row["break_count"] == 0
    assert snapshot["quality"]["missing_trade_date"] == 1
    assert snapshot["quality"]["trade_date_mismatches"] == 0


def test_review_empty_direct_source_snapshot_reports_wrong_day_without_fabricated_rows():
    frame = pd.DataFrame()
    frame.attrs.update(trade_date="20260903", trade_date_source="source", source="empty_cache")

    snapshot = _events().build_limit_event_snapshot(frame, TRADE_DATE)

    assert snapshot["quality"]["trade_date_mismatches"] == 1
    assert snapshot["quality"]["record_trade_date_mismatches"] == 0
    assert snapshot["provenance"]["trade_date"] == "2026-09-03"
    assert snapshot["provenance"]["trade_date_evidence"] == {"field": "trade_date", "value": "20260903"}
    assert snapshot["records"] == []
    assert snapshot["field_coverage"]["broken"]["total"] == 0
    assert snapshot["field_coverage"]["broken"]["coverage"] is None


def test_review_integer_source_dates_coerced_by_pandas_keep_the_day_and_null_separate():
    # An integer date sharing a pandas column with None becomes an integral float.
    frame = pd.DataFrame({"code": ["600000", "000001"], "trade_date": [20260904, None]})

    snapshot = _events().build_limit_event_snapshot(_provider(frame).fetch_day(TRADE_DATE).data, TRADE_DATE)

    known, missing = snapshot["records"]
    assert known["trade_date"] == TRADE_DATE
    assert known["trade_date_evidence"] == {"field": "trade_date", "value": 20260904.0}
    assert missing["trade_date"] is None
    assert missing["trade_date_evidence"] == {"field": "trade_date", "value": None}
    assert snapshot["quality"]["missing_trade_date"] == 1
    assert snapshot["quality"]["trade_date_mismatches"] == 0
