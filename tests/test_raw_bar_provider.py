from datetime import datetime, timezone

import pytest


def payload(rows=None):
    return {"code": 0, "data": {"sz002702": {"day": rows if rows is not None else [["2026-09-07", "6.17", "6.17", "6.17", "6.17", "94841"]]}}}


def test_raw_bar_parser_retains_actual_ohlc_not_only_close():
    from data_sources.raw_bar_provider import parse_tencent_raw_bars
    rows = parse_tencent_raw_bars("sz002702", "2026-09-07", payload(), captured_at="2026-09-08T10:00:00+08:00")
    assert len(rows) == 1
    assert {key: rows[0][key] for key in ("open_raw", "high_raw", "low_raw", "close_raw")} == dict(open_raw=6.17, high_raw=6.17, low_raw=6.17, close_raw=6.17)
    assert rows[0]["source"] == "tencent_raw"
    assert rows[0]["source_timestamp"] == "2026-09-08T10:00:00+08:00"
    assert rows[0]["price_basis"] == "raw"


@pytest.mark.parametrize("change", ["missing_raw", "wrong_symbol", "business_error", "bad_range", "nonfinite", "duplicate_conflict"])
def test_malformed_raw_bars_are_not_repaired_by_adjusted_or_other_rows(change):
    from data_sources.raw_bar_provider import parse_tencent_raw_bars
    p = payload()
    if change == "missing_raw":
        p["data"]["sz002702"] = {"qfqday": p["data"]["sz002702"]["day"]}
    elif change == "wrong_symbol":
        p["data"]["sz000001"] = p["data"].pop("sz002702")
    elif change == "business_error": p["code"] = -1
    elif change == "bad_range": p["data"]["sz002702"]["day"][0][4] = "8.0"
    elif change == "nonfinite": p["data"]["sz002702"]["day"][0][1] = "nan"
    else: p["data"]["sz002702"]["day"].append(["2026-09-07", "7", "7", "7", "7", "90000"])
    with pytest.raises(ValueError):
        parse_tencent_raw_bars("sz002702", "2026-09-07", p, captured_at="2026-09-08T10:00:00+08:00")


def test_other_days_do_not_fill_a_missing_target_day():
    from data_sources.raw_bar_provider import parse_tencent_raw_bars
    p = payload([["2026-09-08", "6", "6", "6", "6", "10"]])
    assert parse_tencent_raw_bars("sz002702", "2026-09-07", p, captured_at="2026-09-08T10:00:00+08:00") == []


def test_provider_fetches_only_missing_codes_and_keeps_valid_cache(tmp_path):
    from data_sources.raw_bar_provider import RawBarProvider, parse_tencent_raw_bars
    calls = []
    def fetch(code, day, captured):
        calls.append(code)
        p = payload(); p["data"][code] = p["data"].pop("sz002702")
        return parse_tencent_raw_bars(code, day, p, captured_at=captured)
    provider = RawBarProvider(fetcher=fetch, now=lambda: datetime(2026,9,8,2,tzinfo=timezone.utc), max_workers=1)
    first = provider.fetch_day(["sz002702"], "2026-09-07", cache_dir=tmp_path)
    second = provider.fetch_day(["sz002702", "sz000001"], "2026-09-07", cache_dir=tmp_path)
    assert calls == ["sz002702", "sz000001"]
    assert len(first["records"]) == 1 and len(second["records"]) == 2
    assert second["covered"] == second["requested"] == 2


def test_provider_failure_does_not_invent_a_bar_or_destroy_existing_cache(tmp_path):
    from data_sources.raw_bar_provider import RawBarProvider
    def fail(*args): raise RuntimeError("fixture outage")
    result = RawBarProvider(fetcher=fail, now=lambda: datetime(2026,9,8,2,tzinfo=timezone.utc), max_workers=1).fetch_day(["sz002702"], "2026-09-07", cache_dir=tmp_path)
    assert result["covered"] == 0 and result["records"] == []
    assert result["errors"][0]["code"] == "sz002702"


def test_provider_does_not_label_an_unfinished_day_as_daily_close(tmp_path):
    from data_sources.raw_bar_provider import RawBarProvider
    def fail(*args): raise AssertionError("must not request a completed daily bar before close")
    got = RawBarProvider(fetcher=fail, now=lambda: datetime(2026,9,8,2,tzinfo=timezone.utc)).fetch_day(["sz002702"], "2026-09-08", cache_dir=tmp_path)
    assert got["status"] == "not_closed"
    assert got["records"] == []


def test_sina_fallback_preserves_beijing_raw_ohlc():
    from data_sources.raw_bar_provider import parse_sina_raw_bars
    rows = parse_sina_raw_bars("bj920821", "2026-09-07", [{"date": "2026-09-07", "open": 18.29, "high": 23.59, "low": 18.27, "close": 23.59}], captured_at="2026-09-08T10:00:00+00:00")
    assert rows[0]["code"] == "bj920821"
    assert rows[0]["open_raw"] == 18.29 and rows[0]["close_raw"] == 23.59
    assert rows[0]["source"] == "sina_raw"


def test_default_missing_raw_day_uses_declared_fallback(monkeypatch):
    from data_sources import raw_bar_provider as module
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"code": 0, "data": {"bj920821": {"day": []}}}
    class Session:
        def get(self, *args, **kwargs): return Response()
    monkeypatch.setattr(module, "get_session", lambda: Session())
    calls = []
    def fallback(code, day, captured):
        calls.append((code,day))
        return module.parse_sina_raw_bars(code,day,[{"date":day,"open":18.29,"high":23.59,"low":18.27,"close":23.59}],captured_at=captured)
    got=module.RawBarProvider(fallback_fetcher=fallback, now=lambda:datetime(2026,9,8,2,tzinfo=timezone.utc)).fetch_day(["bj920821"],"2026-09-07")
    assert calls == [("bj920821","2026-09-07")]
    assert got["covered"] == 1 and got["records"][0]["source"] == "sina_raw"
