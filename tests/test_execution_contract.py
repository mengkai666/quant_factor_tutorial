from __future__ import annotations


def _plan():
    return {
        "groups": [{"code": "attack", "rows": [{"code": "sh600001", "name": "测试股", "role": "attack"}]}]
    }


def test_execution_contract_does_not_invent_entry_or_stop_prices_without_ohlcv():
    from execution_contract import build_execution_contract

    got = build_execution_contract(_plan(), report_date="2026-09-03")
    row = got["details"]["sh600001"]

    assert row["price_status"] == "unavailable"
    assert row["reference_close"] is None
    assert row["entry_price"] is None
    assert row["stop_price"] is None
    assert got["summary"]["actual_order_count"] == 0
    assert "不能计算价格参数" in row["price_note"]


def test_execution_contract_uses_exact_raw_close_only_as_reference():
    from execution_contract import build_execution_contract

    prices = [
        {"code": "sh600001", "date": "2026-09-02", "close_raw": 9.0, "close_qfq": 8.0},
        {"code": "sh600001", "date": "2026-09-03", "close_raw": 10.0, "close_qfq": 9.0},
    ]
    got = build_execution_contract(_plan(), price_df=prices, report_date="2026-09-03")
    row = got["details"]["sh600001"]

    assert row["price_status"] == "close_reference_only"
    assert row["reference_close"] == 10.0
    assert row["reference_date"] == "2026-09-03"
    assert row["entry_price"] is None
    assert row["stop_price"] is None
    assert "不是入场价" in row["price_note"]


def test_execution_contract_requires_next_session_recheck_and_discloses_t_plus_one():
    from execution_contract import build_execution_contract

    got = build_execution_contract(
        _plan(), report_date="2026-09-03", next_trade_date="2026-09-04"
    )
    row = got["details"]["sh600001"]

    assert got["plan_trade_date"] == "2026-09-04"
    assert row["validity_status"] == "next_session_recheck"
    assert "T+1" in row["t_plus_one"]
    assert row["holding_status"] == "not_provided"
    assert "不生成实际加仓/减仓指令" in row["holding_note"]


def test_execution_contract_preserves_explicit_holdings_status():
    from execution_contract import build_execution_contract

    got = build_execution_contract(_plan(), holdings={"sh600001": {"quantity": 100}})
    assert got["holding_status"] == "provided"
    assert got["details"]["sh600001"]["holding_status"] == "provided"
