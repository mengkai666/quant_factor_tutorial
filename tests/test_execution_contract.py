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


def test_execution_contract_accepts_dataframe_and_chinese_columns():
    """价格表既可能来自 DataFrame 也可能是 dict 列表, 列名中英混用, 三种都要成立。"""
    import pandas as pd
    from execution_contract import build_execution_contract

    frame = pd.DataFrame({
        "代码": ["600001", "000002"],
        "日期": ["2026-09-03", "2026-09-03"],
        "close_raw": [10.0, 20.0],
    })
    got = build_execution_contract(
        {"groups": [{"rows": [{"code": "sh600001"}, {"code": "sz000002"}]}]},
        price_df=frame, report_date="2026-09-03",
    )

    assert got["details"]["sh600001"]["reference_close"] == 10.0
    assert got["details"]["sz000002"]["reference_close"] == 20.0


def test_exact_raw_closes_filters_before_materializing_the_whole_price_frame():
    """1M 行价格表只为取当天十几只股票的收盘价, 绝不能先整表 to_dict。

    2026-09-12 cProfile 实测: 渲染层每张卡片各自重建一次决策, 本函数随
    build_today_decision 被调用 5 次, 每次都把整份价格表 to_dict("records"),
    5 次合计 83.5s —— 其中 pandas 的 to_dict 就占 29.8s。按日向量化切片之后,
    转成 dict 的行数应当只剩目标日那几行。
    """
    import pandas as pd
    from execution_contract import build_execution_contract

    materialized: list[int] = []

    class CountingFrame(pd.DataFrame):
        @property
        def _constructor(self):
            return CountingFrame

        def to_dict(self, *args, **kwargs):
            materialized.append(len(self))
            return super().to_dict(*args, **kwargs)

    frame = CountingFrame({
        "code": ["sh600001", "sh600001", "sz000002"],
        "date": ["2026-09-02", "2026-09-03", "2026-09-03"],
        "close_raw": [9.0, 10.0, 20.0],
    })

    got = build_execution_contract(
        {"groups": [{"rows": [{"code": "sh600001"}, {"code": "sz000002"}]}]},
        price_df=frame, report_date="2026-09-03",
    )

    assert got["details"]["sh600001"]["reference_close"] == 10.0
    assert got["details"]["sz000002"]["reference_close"] == 20.0
    assert materialized, "没有走 to_dict 路径, 这个断言失去意义"
    assert max(materialized) < len(frame), (
        f"整表被 materialize ({materialized} 行), 应先按报告日切片"
    )

