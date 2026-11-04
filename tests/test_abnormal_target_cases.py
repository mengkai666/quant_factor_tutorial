from datetime import datetime, timezone, timedelta
import json

import pytest

from abnormal_target_cases import _closed_cutoff, _narrative, announcement_returns, build_target_cases, enrich_rows


def bar(date, close, high=None, low=None):
    return {"date": date, "open": close, "close": close, "high": high or close,
            "low": low or close, "volume": 1000.0}


def test_touching_limit_one_cent_below_is_not_a_closed_limit():
    rows = enrich_rows([bar("2026-10-15", 8.13), bar("2026-10-16", 9.75, 9.76)])
    assert rows[-1]["limit_price"] == 9.76
    assert rows[-1]["touched_limit"] is True
    assert rows[-1]["is_limit"] is False
    assert rows[-1]["limit_label"] == "触板未封"


def test_announcement_horizon_does_not_use_partial_window():
    rows = [bar("2026-10-23", 61.44), bar("2026-10-26", 67.85),
            bar("2026-10-27", 64.40), bar("2026-10-28", 63.0)]
    values = announcement_returns(rows, "2026-10-23")
    assert values[0]["return_pct"] == pytest.approx(2.5390625)
    assert values[0]["complete"] is True
    assert all(v["return_pct"] is None for v in values[1:])
    assert all(v["available_sessions"] == 3 for v in values)


def test_intraday_row_is_excluded_using_shanghai_close():
    now = datetime(2026, 10, 29, 11, tzinfo=timezone(timedelta(hours=8)))
    assert _closed_cutoff("2026-10-29", now) == "2026-10-28"
    assert _closed_cutoff("2026-10-27", now) == "2026-10-27"


def test_laimei_new_high_updates_conclusion():
    dates = ["2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20",
             "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27", "2026-10-28"]
    prices = [7.9, 8.13, 9.75, 9.17, 7.75, 9.3, 8.12, 7.82, 8.85, 8.27, 9.92]
    raw = [bar(d, c, 9.76 if d == "2026-10-16" else c) for d, c in zip(dates, prices)]
    rows = enrich_rows(raw)
    case = _narrative("sz300006", rows, "2026-10-28")
    assert rows[-1]["is_limit"] is True
    assert "二波突破候选" in case["headline"]
    assert "严重异动未确认" in case["status"]
    assert "收复" in case["conclusion"]


def test_offline_replay_filters_later_bars_without_mutating_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr("abnormal_target_cases._closed_cutoff", lambda as_of: as_of)
    out = tmp_path / "output"
    out.mkdir()
    snapshot = out / "target_ohlc_snapshot.json"
    snapshot.write_text(json.dumps({"by_code": {"sz300006": {
        "rows": [bar("2026-10-26", 8.85), bar("2026-10-27", 8.27), bar("2026-10-28", 9.92)],
        "fetched_at": "2026-10-29T11:00:00+08:00"}}}), encoding="utf-8")
    original = snapshot.read_bytes()
    value = build_target_cases(tmp_path, out, "2026-10-27", refresh=False)
    assert value["cases"][0]["latest"]["date"] == "2026-10-27"
    assert all(row["date"] <= "2026-10-27" for row in value["cases"][0]["rows"])
    assert "二波突破候选" not in value["cases"][0]["headline"]
    assert snapshot.read_bytes() == original
    json.dumps(value, allow_nan=False)
