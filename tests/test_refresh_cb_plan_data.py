import datetime as dt
import json
from pathlib import Path

import pytest

from tools.refresh_cb_plan_data import fetch_snapshot, import_research_summary, main
from test_cb_plan_data import research


def fetchers(day="2030-04-08", *, current_price=160, stock_date=None, redeem_price=160, time="15:00:00"):
    dates = [(dt.date.fromisoformat(day) - dt.timedelta(days=n)).isoformat() for n in range(25, 0, -1)]
    quote = {"code": "123456", "name": "示例转债", "symbol": "sz123456", "quote_date": day,
             "trade": current_price, "quote_close": current_price, "settlement": 100, "changepercent": 60,
             "amount": 1e9, "ticktime": time}
    return {"market": lambda: {"rows": [quote], "retrieved_at": day + "T07:03:00+00:00", "complete_pagination": True},
            "base": lambda: {"rows": [{"SECURITY_CODE": "123456", "CONVERT_STOCK_CODE": "300001", "SECURITY_SHORT_NAME": "示例股", "ACTUAL_ISSUE_SCALE": 8}]},
            "redemption": lambda: {"rows": [{"bond_id": "123456", "price": redeem_price, "sprice": 20, "convert_price": 15,
                                               "curr_iss_amt": 1.2, "convert_dt": "2029-01-01", "redeem_icon": "G"}]},
            "stock_quotes": lambda symbols: {"rows": [{"symbol": "sz300001", "date": stock_date or day, "time": "15:00:00", "close": 20, "previous_close": 19}]},
            "daily": lambda symbol: [{"date": date, "close": 100} for date in dates]}


def test_import_preserves_source_timestamp_and_refuses_other_day(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(research()), encoding="utf-8")
    path = import_research_summary(source, "2030-04-08", data_dir=tmp_path / "data")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["source_timestamp"] == "2030-04-08T07:03:00+00:00"
    assert saved["imported_at"] != saved["source_timestamp"]
    assert saved["sources"][0]["source_timestamp"] == saved["source_timestamp"]
    with pytest.raises(ValueError, match="日期"):
        import_research_summary(source, "2030-04-09", data_dir=tmp_path / "data")
    assert not (tmp_path / "data/cb_daily_snapshots/2030-04-09.json").exists()


@pytest.mark.parametrize("quote_time", ["11:30:00", "", None, "25:00:00"])
def test_import_rejects_intraday_or_missing_close_evidence(tmp_path, quote_time):
    payload = research()
    payload["bonds"][0]["quote_time"] = quote_time
    source = tmp_path / "source.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="收盘报价时间"):
        import_research_summary(source, "2030-04-08", data_dir=tmp_path / "data")
    assert not (tmp_path / "data").exists()


def test_explicit_refresh_calculates_parity_and_actual_remaining_balance():
    payload, raw = fetch_snapshot("2030-04-08", fetchers=fetchers())
    row = payload["rows"][0]
    assert row["premium_pct"] == 20  # 债价除以转股价值后计算溢价率。
    assert row["remaining_issue_yi"] == 1.2  # 使用存续余额，不使用发行规模8亿。
    assert row["r5"] == row["r10"] == row["r20"] == 60
    assert "r3" not in row
    assert raw["market"]["rows"][0]["trade"] == 160


def test_live_wrong_day_or_intraday_cannot_be_saved_as_close():
    with pytest.raises(ValueError, match="其他交易日"):
        fetch_snapshot("2030-04-09", fetchers=fetchers())
    with pytest.raises(ValueError, match="盘中快照"):
        fetch_snapshot("2030-04-08", fetchers=fetchers(time="11:30:00"))


def test_unmatched_redemption_quote_never_produces_zero_premium():
    payload, _ = fetch_snapshot("2030-04-08", fetchers=fetchers(redeem_price=150))
    assert payload["rows"][0]["premium_pct"] is None


def test_wrong_stock_date_cannot_create_false_comovement_or_premium():
    payload, _ = fetch_snapshot("2030-04-08", fetchers=fetchers(stock_date="2030-04-07"))
    assert payload["rows"][0]["stock_r1"] is None
    assert payload["rows"][0]["premium_pct"] is None
    assert payload["rows"][0]["stock_source_date"] is None


def test_default_command_does_not_fetch_or_write_without_same_day_summary(tmp_path, monkeypatch):
    import tools.refresh_cb_plan_data as command
    monkeypatch.setattr(command, "fetch_snapshot", lambda *a, **kw: pytest.fail("implicit network refresh"))
    assert main(["--date", "2030-04-08", "--output-dir", str(tmp_path / "output"), "--data-dir", str(tmp_path / "data")]) == 1
    assert not (tmp_path / "data").exists()


def test_interval_returns_reject_inconsistent_previous_close_even_with_current_bar():
    from tools.refresh_cb_plan_data import _series_returns
    end = dt.date(2030, 4, 8)
    bars = [{"date": (end - dt.timedelta(days=offset)).isoformat(), "close": 100} for offset in range(25, -1, -1)]
    bars[-2]["close"] = 90
    bars[-1]["close"] = 160
    assert all(value is None for value in _series_returns(bars, "2030-04-08", 160, 100).values())
