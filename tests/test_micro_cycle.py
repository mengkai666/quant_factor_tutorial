import pandas as pd
import pytest

from data_sources.name_resolver import NameResolution


def test_signal_limit_chain_uses_fact_membership_and_excludes_prior_limit_ups():
    from micro_cycle import build_signal_limit_chain

    starters = ["sh600721", "sh600892", "sh603773", "sz002425", "sz002428", "sz002552", "sz002975"]
    rows = []
    for date in ("20260804", "20260805", "20260806", "20260807"):
        for code in starters + ["sz002963"]:
            rows.append({"日期": date, "类型": "ZT", "代码": code, "连板数": 3})
    rows.extend([
        {"日期": "20260803", "类型": "ZT", "代码": "sz002963", "连板数": 1},
        {"日期": "20260804", "类型": "DT", "代码": "sh600001", "连板数": 0},
    ])
    names = NameResolution(
        names={code: f"股票{index}" for index, code in enumerate(starters, 1)} | {"sz002963": "豪尔赛"},
        sources={}, conflicts=[],
    )

    result = build_signal_limit_chain(
        pd.DataFrame(rows),
        ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"],
        "2026-08-04", "2026-08-07",
        names=names,
        immutable_dates={"2026-08-07"},
    )

    assert result["usable"] is True
    assert result["status"] == "provisional"
    assert result["consecutive_days"] == 4
    assert [row["code"] for row in result["rows"]] == starters
    assert "上游连板数" not in str(result["rows"])
    assert "历史事实交集" in result["hint"]


def test_signal_limit_chain_is_verified_only_when_every_cycle_date_is_immutable():
    from micro_cycle import build_signal_limit_chain

    history = pd.DataFrame([
        {"date": date, "type": "ZT", "code": "sh600001"}
        for date in ("20260804", "20260805", "20260806", "20260807")
    ])
    names = NameResolution(names={"sh600001": "验证股份"}, sources={}, conflicts=[])
    result = build_signal_limit_chain(
        history,
        ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"],
        "2026-08-04", "2026-08-07", names=names,
        immutable_dates={"2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"},
    )
    assert result["status"] == "verified"
    assert result["hint"] == ""


def test_signal_limit_chain_normalizes_compact_trading_dates_to_iso_output():
    from micro_cycle import build_signal_limit_chain

    history = pd.DataFrame([
        {"date": date, "type": "ZT", "code": "sh600001"}
        for date in ("20260804", "20260805", "20260806", "20260807")
    ])
    names = NameResolution(names={"sh600001": "验证股份"}, sources={}, conflicts=[])
    result = build_signal_limit_chain(
        history,
        ["20260803", "20260804", "20260805", "20260806", "20260807"],
        "20260804", "20260807", names=names,
        immutable_dates={"20260804", "20260805", "20260806", "20260807"},
    )

    assert result["usable"] is True
    assert result["dates"] == ["2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]
    assert result["status"] == "verified"


def test_signal_limit_chain_sorts_reverse_dates_before_excluding_immediately_previous_pool():
    from micro_cycle import build_signal_limit_chain

    history = pd.DataFrame([
        {"date": date, "type": "ZT", "code": code}
        for date, code in [
            ("20260804", "sh600001"),
            ("20260805", "sh600001"),
            ("20260805", "sh600002"),
            ("20260806", "sh600001"),
            ("20260806", "sh600002"),
            ("20260807", "sh600001"),
            ("20260807", "sh600002"),
        ]
    ])
    names = NameResolution(
        names={"sh600001": "前序股份", "sh600002": "新入股份"},
        sources={}, conflicts=[],
    )
    result = build_signal_limit_chain(
        history,
        ["2026-08-07", "2026-08-06", "2026-08-05", "2026-08-04", "2026-08-03"],
        "2026-08-05", "2026-08-07", names=names,
    )

    assert result["dates"] == ["2026-08-05", "2026-08-06", "2026-08-07"]
    assert [row["code"] for row in result["rows"]] == ["sh600002"]


def _index_fixture():
    values = [
        ("2026-07-17", 3745.174, 3869.215, 3764.155),
        ("2026-07-20", 3741.110, 3831.659, 3796.281),
        ("2026-07-21", 3743.360, 3864.600, 3864.367),
        ("2026-07-22", 3839.665, 3884.435, 3867.034),
        ("2026-07-23", 3851.706, 3878.832, 3876.777),
        ("2026-07-24", 3808.636, 3861.040, 3814.198),
        ("2026-07-27", 3793.449, 3858.310, 3858.245),
        ("2026-07-28", 3797.373, 3844.012, 3813.315),
        ("2026-07-29", 3782.481, 3845.766, 3828.469),
        ("2026-07-30", 3767.503, 3839.341, 3804.693),
        ("2026-07-31", 3822.374, 3847.093, 3832.262),
        ("2026-08-03", 3797.643, 3827.636, 3809.663),
        ("2026-08-04", 3799.524, 3831.940, 3822.285),
        ("2026-08-05", 3815.122, 3884.397, 3878.430),
        ("2026-08-06", 3864.273, 3902.054, 3900.352),
        ("2026-08-07", 3885.625, 3940.935, 3940.037),
    ]
    return [
        {"date": date, "low": low, "high": high, "close": close}
        for date, low, high, close in values
    ]


def test_detect_micro_cycle_rejects_a_noise_width_close_breakout():
    """8/05 收盘只高出第一反弹收盘高 0.04% —— 噪音不算突破, 确认推到 8/06。"""
    from micro_cycle import detect_micro_cycle

    det = {
        "bottom": {"date": "2026-07-17", "close": 3764.155},
        "index_series": _index_fixture(),
    }
    result = detect_micro_cycle(
        det,
        daily_limit_counts={"2026-08-03": 101, "2026-08-04": 140},
    )

    assert result["events"]["final_stop"]["date"] == "2026-07-20"
    assert result["events"]["rebound_high"]["high_date"] == "2026-07-22"
    assert result["events"]["rebound_high"]["close_date"] == "2026-07-23"
    assert result["events"]["secondary_bottom"]["date"] == "2026-07-30"
    assert result["events"]["secondary_bottom"]["higher_low"] is True
    assert result["events"]["retest"]["date"] == "2026-08-03"
    assert result["signal_date"] == "2026-08-04"
    # 8/05 收 3878.430, 第一反弹收盘高 3876.777 -> 只 +0.04%, 被幅度门槛挡掉
    assert result["confirmation_date"] == "2026-08-06"
    assert result["full_confirmation_date"] == "2026-08-06"
    assert result["rebound_amp"] == pytest.approx(3.83, abs=0.01)
    assert result["criteria"] == "micro-cycle/v2"
    assert result["status"] == "小周期主升"
    assert result["stalled"] is False
    assert result["rising_days"] == 4
    assert result["signal_return"] == pytest.approx(3.08, abs=0.01)
    assert result["signal_basis"] == "price+limit_pool"


def test_detect_micro_cycle_does_not_claim_turn_up_before_rebound_high_breaks():
    from micro_cycle import detect_micro_cycle

    rows = _index_fixture()[:-3]
    rows[-1] = {**rows[-1], "close": 3822.285, "high": 3831.940}
    result = detect_micro_cycle({
        "bottom": {"date": "2026-07-17", "close": 3764.155},
        "index_series": rows,
    })

    assert result["confirmation_date"] == ""
    assert result["status"] == "震荡筑底"
    assert result["signal_basis"] == "price_only"


def test_detect_micro_cycle_does_not_promote_a_cycle_after_secondary_low_breaks_support():
    from micro_cycle import detect_micro_cycle

    rows = _index_fixture()
    rows = [
        {**row, "low": 3735.0} if row["date"] == "2026-07-30" else row
        for row in rows
    ]

    result = detect_micro_cycle({
        "bottom": {"date": "2026-07-17", "close": 3764.155},
        "index_series": rows,
    })

    assert result["events"]["secondary_bottom"]["higher_low"] is False
    assert result["status"] == "探底未完成"
    assert result["signal_date"] == ""
    assert result["confirmation_date"] == ""
    assert result["full_confirmation_date"] == ""
    assert result["rising_days"] == 0
    assert result["signal_return"] is None
    assert result["signal_basis"] == "unavailable"


@pytest.mark.parametrize(
    "counts",
    [
        {"2026-08-03": 101},
        {"2026-08-04": 140},
    ],
)
def test_detect_micro_cycle_keeps_price_signal_when_limit_counts_are_incomplete(counts):
    from micro_cycle import detect_micro_cycle

    result = detect_micro_cycle(
        {
            "bottom": {"date": "2026-07-17", "close": 3764.155},
            "index_series": _index_fixture(),
        },
        daily_limit_counts=counts,
    )

    assert result["signal_date"] == "2026-08-04"
    assert result["signal_basis"] == "price_only"


def test_detect_micro_cycle_counts_only_the_uninterrupted_rise_from_signal():
    from micro_cycle import detect_micro_cycle

    rows = _index_fixture()
    rows = [
        {**row, "close": 3877.0} if row["date"] == "2026-08-06" else row
        for row in rows
    ]
    rows.extend([
        {"date": "2026-08-10", "low": 3930.0, "high": 3960.0, "close": 3950.0},
        {"date": "2026-08-11", "low": 3940.0, "high": 3970.0, "close": 3960.0},
    ])

    result = detect_micro_cycle({
        "bottom": {"date": "2026-07-17", "close": 3764.155},
        "index_series": rows,
    })

    assert result["signal_date"] == "2026-08-04"
    assert result["rising_days"] == 2
    assert result["status"] == "震荡转升"


def test_detect_micro_cycle_still_separates_close_breakout_from_the_full_breakout():
    """第一反弹留长上影时两级确认仍分得开: 收盘先过线, 盘中高点后过。"""
    from micro_cycle import detect_micro_cycle

    rows = [
        {**row, "high": 3950.0} if row["date"] == "2026-07-22" else row
        for row in _index_fixture()
    ]
    rows.append({"date": "2026-08-10", "low": 3930.0, "high": 3975.0, "close": 3960.0})

    result = detect_micro_cycle({
        "bottom": {"date": "2026-07-17", "close": 3764.155},
        "index_series": rows,
    })

    assert result["events"]["rebound_high"]["high_date"] == "2026-07-22"
    assert result["events"]["rebound_high"]["close_date"] == "2026-07-23"
    assert result["confirmation_date"] == "2026-08-06"
    assert result["full_confirmation_date"] == "2026-08-10"
    assert result["status"] == "小周期主升"


def _shallow_rebound_fixture(rebound_high):
    """止跌低点 3741, 第一反弹高点由参数给 —— 用来卡反弹幅度门槛。"""
    values = [
        ("2026-07-17", 3745.0, 3790.0, 3760.0),
        ("2026-07-20", 3741.0, 3775.0, 3765.0),
        ("2026-07-21", 3750.0, 3800.0, 3795.0),
        ("2026-07-22", 3780.0, rebound_high, 3805.0),
        ("2026-07-23", 3775.0, 3800.0, 3785.0),
        ("2026-07-24", 3760.0, 3790.0, 3770.0),
        ("2026-07-27", 3765.0, 3800.0, 3795.0),
        ("2026-07-28", 3790.0, 3830.0, 3825.0),
        ("2026-07-29", 3800.0, 3840.0, 3835.0),
    ]
    return [
        {"date": date, "low": low, "high": high, "close": close}
        for date, low, high, close in values
    ]


def test_detect_micro_cycle_needs_a_real_first_rebound_before_the_template_applies():
    """反弹只有 1.8% 时整套模板不适用; 抬到 2.4% 同一根 K 就认。"""
    from micro_cycle import detect_micro_cycle

    det = {"bottom": {"date": "2026-07-17", "close": 3760.0}}

    shallow = detect_micro_cycle({**det, "index_series": _shallow_rebound_fixture(3810.0)})
    assert shallow["rebound_amp"] == pytest.approx(1.84, abs=0.01)
    assert shallow["status"] == "探底未完成"
    assert shallow["events"] == {}
    assert shallow["signal_date"] == ""
    assert shallow["criteria"] == "micro-cycle/v2"

    deep = detect_micro_cycle({**det, "index_series": _shallow_rebound_fixture(3830.0)})
    assert deep["rebound_amp"] == pytest.approx(2.38, abs=0.01)
    assert deep["events"]["rebound_high"]["high_date"] == "2026-07-22"


def _index_fixture_through_september():
    """真实 sh000001 日线: 8/06 全面突破之后推到 8/18 见顶, 再横到 9/03。"""
    values = [
        ("2026-08-10", 3938.620, 3967.590, 3966.590),
        ("2026-08-11", 3930.640, 3966.390, 3934.090),
        ("2026-08-12", 3927.550, 3950.620, 3946.680),
        ("2026-08-13", 3924.640, 3968.480, 3926.970),
        ("2026-08-14", 3903.700, 3932.640, 3927.180),
        ("2026-08-17", 3924.470, 3983.510, 3982.650),
        ("2026-08-18", 3955.600, 3994.180, 3990.304),
        ("2026-08-19", 3879.580, 3961.140, 3894.420),
        ("2026-08-20", 3888.100, 3925.060, 3903.720),
        ("2026-08-21", 3883.790, 3912.130, 3905.200),
        ("2026-08-24", 3855.350, 3910.240, 3882.008),
        ("2026-08-25", 3850.860, 3896.210, 3889.450),
        ("2026-08-26", 3881.740, 3926.430, 3912.520),
        ("2026-08-27", 3909.310, 3958.030, 3956.570),
        ("2026-08-28", 3947.800, 3970.310, 3952.180),
        ("2026-08-31", 3926.500, 3986.300, 3986.300),
        ("2026-09-01", 3976.470, 3995.180, 3979.890),
        ("2026-09-02", 3932.250, 3965.810, 3941.390),
        ("2026-09-03", 3930.450, 3968.110, 3942.088),
    ]
    return _index_fixture() + [
        {"date": date, "low": low, "high": high, "close": close}
        for date, low, high, close in values
    ]


def test_detect_micro_cycle_expires_main_advance_once_the_peak_stops_being_taken_out():
    """8/06 那波是真的, 但一个月后不能还挂着"小周期主升"。"""
    from micro_cycle import detect_micro_cycle

    result = detect_micro_cycle(
        {
            "bottom": {"date": "2026-07-17", "close": 3764.155},
            "index_series": _index_fixture_through_september(),
        },
        daily_limit_counts={"2026-08-03": 101, "2026-08-04": 140},
    )

    # 六个节点仍是历史上那六个 —— 事实不动, 只有结论会过期。
    assert result["signal_date"] == "2026-08-04"
    assert result["confirmation_date"] == "2026-08-06"
    assert result["full_confirmation_date"] == "2026-08-06"
    assert result["rising_days"] == 5
    assert result["status"] == "主升告一段落"
    assert result["stalled"] is True
    assert result["peak_date"] == "2026-08-18"
    assert result["peak_return"] == pytest.approx(4.40, abs=0.01)
    assert result["fade_from_peak"] == pytest.approx(-1.21, abs=0.01)
    assert result["bars_since_peak"] == 12
    assert result["bars_since_window"] == 20
    assert result["signal_return"] == pytest.approx(3.13, abs=0.01)


def test_detect_micro_cycle_expires_main_advance_on_giveback_before_the_bar_count():
    """回吐够深就不必等满 5 根 K: 峰值后第 2 根就 -3% 也要降档。"""
    from micro_cycle import detect_micro_cycle

    rows = _index_fixture() + [
        {"date": "2026-08-10", "low": 3930.0, "high": 3970.0, "close": 3966.0},
        {"date": "2026-08-11", "low": 3830.0, "high": 3900.0, "close": 3840.0},
    ]

    result = detect_micro_cycle({
        "bottom": {"date": "2026-07-17", "close": 3764.155},
        "index_series": rows,
    })

    assert result["peak_date"] == "2026-08-10"
    assert result["bars_since_peak"] == 1
    assert result["fade_from_peak"] == pytest.approx(-3.18, abs=0.01)
    assert result["stalled"] is True
    assert result["status"] == "主升告一段落"


def test_phase_payload_does_not_publish_resonance_before_close_confirmation(monkeypatch):
    import phase_resonance

    monkeypatch.setattr(
        phase_resonance,
        "detect_micro_cycle",
        lambda *_args, **_kwargs: {
            "status": "震荡筑底",
            "signal_date": "2026-08-04",
            "confirmation_date": "",
            "full_confirmation_date": "",
            "events": {},
        },
    )
    monkeypatch.setattr(phase_resonance, "_read_csv", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr(phase_resonance, "resolve_names", lambda **_kwargs: object())
    monkeypatch.setattr(
        phase_resonance,
        "build_signal_limit_chain",
        lambda *_args, **_kwargs: {"usable": True, "rows": [{"code": "sh600001"}]},
    )
    monkeypatch.setattr(
        phase_resonance,
        "build_sector_return_table",
        lambda *_args, **_kwargs: pd.DataFrame([{"name": "半导体", "return": 8.0}]),
    )
    monkeypatch.setattr(phase_resonance, "build_price_matrix", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr(
        phase_resonance,
        "build_market_resonance",
        lambda *_args, **_kwargs: {"mainlines": [{"name": "AI算力"}]},
        raising=False,
    )

    payload = phase_resonance._build_micro_cycle_payload(
        {
            "latest": {"date": "2026-08-04"},
            "index_series": [{"date": "2026-08-04"}],
        },
        {},
    )

    assert payload["micro_cycle"]["signal_date"] == "2026-08-04"
    assert payload["micro_chain"] == {}
    assert payload["micro_resonance"] == {}


def test_phase_payload_filters_price_cache_to_historical_report_cutoff(monkeypatch):
    import phase_resonance

    prices = pd.DataFrame([
        {"date": 20260804, "code": "sh600001", "close_legacy": 10.0, "close_qfq": None},
        {"date": 20260807, "code": "sh600001", "close_legacy": 12.0, "close_qfq": 18.0},
        {"date": 20260810, "code": "sh600001", "close_legacy": 20.0, "close_qfq": 40.0},
    ])
    captured = {}

    def read_cache(path, **_kwargs):
        return prices.copy() if path == phase_resonance.PRICE_CACHE else pd.DataFrame()

    def capture_resonance(_history, raw_matrix, qfq_matrix, *_args, **kwargs):
        captured["raw_matrix"] = raw_matrix.copy()
        captured["qfq_matrix"] = qfq_matrix.copy()
        captured["previous_date"] = kwargs.get("previous_date")
        return {}

    monkeypatch.setattr(
        phase_resonance,
        "detect_micro_cycle",
        lambda *_args, **_kwargs: {
            "status": "小周期主升",
            "signal_date": "2026-08-04",
            "confirmation_date": "2026-08-05",
            "full_confirmation_date": "2026-08-06",
            "signal_return": 3.0,
            "events": {},
        },
    )
    monkeypatch.setattr(phase_resonance, "_read_csv", read_cache)
    monkeypatch.setattr(phase_resonance, "resolve_names", lambda **_kwargs: object())
    monkeypatch.setattr(phase_resonance, "_immutable_snapshot_dates", lambda: set())
    monkeypatch.setattr(
        phase_resonance,
        "build_signal_limit_chain",
        lambda *_args, **_kwargs: {"usable": False, "rows": []},
    )
    monkeypatch.setattr(
        phase_resonance,
        "build_sector_return_table",
        lambda *_args, **_kwargs: pd.DataFrame(),
    )
    monkeypatch.setattr(
        phase_resonance, "build_market_resonance", capture_resonance, raising=False,
    )

    phase_resonance._build_micro_cycle_payload(
        {
            "latest": {"date": "2026-08-07"},
            "index_series": [
                {"date": "2026-08-04"},
                {"date": "2026-08-05"},
                {"date": "2026-08-06"},
                {"date": "2026-08-07"},
            ],
        },
        {},
    )

    assert list(captured["raw_matrix"].index) == ["20260804", "20260807"]
    assert list(captured["qfq_matrix"].index) == ["20260804", "20260807"]
    assert captured["raw_matrix"].loc["20260804", "sh600001"] == 10.0
    assert captured["raw_matrix"].loc["20260807", "sh600001"] == 12.0
    assert captured["qfq_matrix"].loc["20260804", "sh600001"] == 15.0
    assert captured["qfq_matrix"].loc["20260807", "sh600001"] == 18.0
    assert captured["previous_date"] == "2026-08-06"


def test_build_sector_return_table_uses_period_returns_and_returns_empty_frame_for_empty_cache():
    from micro_cycle import build_sector_return_table

    cache = {
        "半导体": [
            {"date": "2026-08-03", "close": 90.0},
            {"date": "2026-08-04", "close": 100.0},
            {"date": "2026-08-07", "close": 115.0},
        ],
        "医疗服务": [
            {"date": "2026-08-04", "close": 100.0},
            {"date": "2026-08-07", "close": 108.0},
        ],
    }

    result = build_sector_return_table(cache, "2026-08-04", "2026-08-07", 3.0)

    assert result.to_dict("records") == [
        {"name": "半导体", "return": 15.0, "excess_return": 12.0},
        {"name": "医疗服务", "return": 8.0, "excess_return": 5.0},
    ]
    empty = build_sector_return_table({}, "2026-08-04", "2026-08-07", 3.0)
    assert empty.empty
    assert list(empty.columns) == ["name", "return", "excess_return"]


def test_cycle_resonance_separates_strong_industries_from_confirmed_mainlines():
    from micro_cycle import build_cycle_resonance

    codes = ["sz002552", "sz002428", "sh603773", "sz002975", "sh600721", "sz002425", "sh600892"]
    chain = {
        "usable": True,
        "rows": [{"code": code, "name": name} for code, name in zip(codes, [
            "宝鼎科技", "云南锗业", "沃格光电", "博杰股份", "百花医药", "凯撒文化", "大晟文化",
        ])],
    }
    sector_returns = pd.DataFrame([
        {"name": "电子化学品", "return": 17.14, "excess_return": 14.06},
        {"name": "元件", "return": 15.22, "excess_return": 12.14},
        {"name": "贵金属", "return": 15.13, "excess_return": 12.05},
        {"name": "半导体", "return": 11.53, "excess_return": 8.45},
        {"name": "医疗服务", "return": 9.08, "excess_return": 6.00},
    ])
    price_matrix = pd.DataFrame(
        [[10, 10, 10, 10, 10, 10, 10], [12.6767, 12.6763, 12.6760, 12.4081, 12.6731, 11.5622, 11.4758]],
        index=["2026-08-04", "2026-08-07"], columns=codes,
    )
    cls = pd.DataFrame([
        {"date": "20260807", "code": code, "sub": sub, "mainline": mainline}
        for code, sub, mainline in [
            ("sz002552", "PCB", "AI算力"), ("sz002428", "光通信", "AI算力"),
            ("sh603773", "PCB", "AI算力"), ("sz002975", "液冷", "AI算力"),
            ("sh600721", "医药", "医药"), ("sz002425", "AI应用", "AI应用"),
            ("sh600892", "传媒", "AI应用"),
        ]
    ])

    result = build_cycle_resonance(
        sector_returns, chain, price_matrix, "2026-08-04", "2026-08-07",
        cls_attribution=cls, em_attribution=None,
    )

    assert [row["name"] for row in result["strong_industries"][:3]] == ["电子化学品", "元件", "贵金属"]
    levels = {row["name"]: row["level"] for row in result["mainlines"]}
    assert levels == {"AI算力": "核心共振", "医药": "次级共振", "AI应用": "连板跟随"}
    leaders = {row["name"]: [stock["name"] for stock in row["leaders"]] for row in result["mainlines"]}
    assert leaders["AI算力"] == ["宝鼎科技", "云南锗业", "沃格光电", "博杰股份"]
    assert "贵金属" not in levels


def test_cycle_resonance_requires_two_chain_stocks_for_core_level():
    from micro_cycle import build_cycle_resonance

    codes = ["sh600001", "sh600002"]
    chain = {
        "usable": True,
        "rows": [
            {"code": "sh600001", "name": "医药甲"},
            {"code": "sh600002", "name": "医药乙"},
        ],
    }
    sectors = pd.DataFrame([
        {"name": "医疗服务", "return": 9.0, "excess_return": 6.0},
        {"name": "生物制品", "return": 8.0, "excess_return": 5.0},
    ])
    prices = pd.DataFrame(
        [[10.0, 10.0], [11.0, 12.0]],
        index=["2026-08-04", "2026-08-07"], columns=codes,
    )
    cls = pd.DataFrame([
        {"date": "20260807", "code": code, "sub": "医药", "mainline": "医药"}
        for code in codes
    ])

    result = build_cycle_resonance(
        sectors, chain, prices, "2026-08-04", "2026-08-07",
        cls_attribution=cls,
    )

    assert result["mainlines"][0]["level"] == "核心共振"


def test_cycle_resonance_prefers_latest_valid_cls_and_keeps_unattributed_codes():
    from micro_cycle import build_cycle_resonance

    chain = {"usable": True, "rows": [
        {"code": "sh600001", "name": "甲"}, {"code": "sh600002", "name": "乙"},
    ]}
    prices = pd.DataFrame(
        [[10.0, 10.0], [12.0, 11.0]],
        index=["2026-08-04", "2026-08-07"], columns=["sh600001", "sh600002"],
    )
    cls = pd.DataFrame([
        {"date": "20260806", "code": "sh600001", "sub": "PCB", "mainline": "AI算力"},
        {"date": "20260807", "code": "sh600001", "sub": "其它", "mainline": "其它"},
    ])
    em = pd.DataFrame([
        {"date": "20260807", "code": "sh600001", "sub": "传媒", "mainline": "AI应用"},
    ])

    result = build_cycle_resonance(
        pd.DataFrame([
            {"name": "半导体", "return": 12.0, "excess_return": 9.0},
        ]), chain, prices, "2026-08-04", "2026-08-07",
        cls_attribution=cls, em_attribution=em,
    )

    assert result["mainlines"][0]["name"] == "AI算力"
    assert result["mainlines"][0]["level"] == "次级共振"
    assert result["unattributed_count"] == 1
    assert result["attribution_coverage"] == 0.5


def test_cycle_resonance_hides_numeric_returns_below_eighty_percent_qfq_coverage():
    from micro_cycle import build_cycle_resonance

    chain = {"usable": True, "rows": [
        {"code": "sh600001", "name": "甲"}, {"code": "sh600002", "name": "乙"},
    ]}
    prices = pd.DataFrame(
        [[10.0], [12.0]],
        index=["2026-08-04", "2026-08-07"], columns=["sh600001"],
    )
    cls = pd.DataFrame([
        {"date": "20260807", "code": code, "sub": "传媒", "mainline": "AI应用"}
        for code in ("sh600001", "sh600002")
    ])
    result = build_cycle_resonance(
        pd.DataFrame(), chain, prices, "2026-08-04", "2026-08-07",
        cls_attribution=cls,
    )

    assert result["leader_coverage"] == 0.5
    assert [row["return"] for row in result["mainlines"][0]["leaders"]] == [None, None]


def test_cycle_resonance_omits_unconfirmed_singleton_mainline():
    from micro_cycle import build_cycle_resonance

    chain = {"usable": True, "rows": [{"code": "sh600001", "name": "甲"}]}
    prices = pd.DataFrame(
        [[10.0], [12.0]],
        index=["2026-08-04", "2026-08-07"], columns=["sh600001"],
    )
    cls = pd.DataFrame([
        {"date": "20260807", "code": "sh600001", "sub": "传媒", "mainline": "AI应用"},
    ])

    result = build_cycle_resonance(
        pd.DataFrame(), chain, prices, "2026-08-04", "2026-08-07", cls_attribution=cls,
    )

    assert result["attribution_coverage"] == 1.0
    assert result["mainlines"] == []


def test_unknown_index_return_keeps_excess_return_unknown_and_cannot_confirm_industry():
    from micro_cycle import build_cycle_resonance, build_sector_return_table

    sectors = build_sector_return_table({
        "半导体": [
            {"date": "2026-08-04", "close": 100.0},
            {"date": "2026-08-07", "close": 115.0},
        ],
    }, "2026-08-04", "2026-08-07", None)
    chain = {"usable": True, "rows": [{"code": "sh600001", "name": "甲"}]}
    prices = pd.DataFrame(
        [[10.0], [12.0]],
        index=["2026-08-04", "2026-08-07"], columns=["sh600001"],
    )
    cls = pd.DataFrame([
        {"date": "20260807", "code": "sh600001", "sub": "PCB", "mainline": "AI算力"},
    ])

    result = build_cycle_resonance(
        sectors, chain, prices, "2026-08-04", "2026-08-07", cls_attribution=cls,
    )

    assert sectors.loc[0, "excess_return"] is None
    assert result["strong_industries"] == []
    assert result["mainlines"] == []


def test_market_resonance_uses_full_report_day_pool_and_keeps_continuous_core_separate():
    from micro_cycle import build_market_resonance

    history = pd.DataFrame([
        {"日期": "20260810", "类型": "ZT", "代码": "600001", "名称": "算力龙头", "连板数": 5},
        {"日期": "20260810", "类型": "ZT", "代码": "sh600002", "名称": "算力中军", "连板数": 2},
        {"日期": "20260810", "类型": "ZT", "代码": "sz000003", "名称": "算力首板", "连板数": 1},
        {"日期": "20260810", "类型": "ZT", "代码": "sh600004", "名称": "创新药甲", "连板数": 3},
        {"日期": "20260810", "类型": "ZT", "代码": "sh600005", "名称": "创新药乙", "连板数": 1},
        {"日期": "20260807", "类型": "ZT", "代码": "sh600099", "名称": "旧样本", "连板数": 4},
    ])
    cls = pd.DataFrame([
        {"date": "20260810", "code": "600001", "sub": "算力", "mainline": "AI算力"},
        {"date": "20260810", "code": "sh600002", "sub": "算力", "mainline": "AI算力"},
        {"date": "20260810", "code": "sz000003", "sub": "算力", "mainline": "AI算力"},
        {"date": "20260810", "code": "sh600004", "sub": "创新药", "mainline": "医药"},
        {"date": "20260810", "code": "sh600005", "sub": "创新药", "mainline": "医药"},
        # 旧日期不能给报告日未归因股票补标签。
        {"date": "20260807", "code": "sh600099", "sub": "算力", "mainline": "AI算力"},
    ])
    raw = pd.DataFrame(
        [[10, 10, 10, 10, 10], [11, 10.8, 10.5, 11.5, 10.6]],
        index=["2026-08-07", "2026-08-10"],
        columns=["sh600001", "sh600002", "sz000003", "sh600004", "sh600005"],
    )
    qfq = pd.DataFrame(
        [[8, 10, 10, 9, 10], [11, 10.8, 10.5, 11.5, 10.6]],
        index=["2026-08-04", "2026-08-10"],
        columns=raw.columns,
    )
    sectors = pd.DataFrame([
        {"name": "电子化学品", "return": 14.0, "excess_return": 10.0},
        {"name": "半导体", "return": 12.0, "excess_return": 8.0},
        {"name": "医疗服务", "return": 8.0, "excess_return": 4.0},
    ])
    names = NameResolution(
        names={code: name for code, name in [
            ("sh600001", "算力龙头"), ("sh600002", "算力中军"),
            ("sz000003", "算力首板"), ("sh600004", "创新药甲"),
            ("sh600005", "创新药乙"),
        ]},
        sources={}, conflicts=[],
    )
    core = {"usable": True, "rows": [{"code": "sh600004", "name": "创新药甲"}]}

    result = build_market_resonance(
        history, raw, qfq, sectors, "2026-08-04", "2026-08-10",
        previous_date="2026-08-07", names=names, cls_attribution=cls,
        continuous_core=core,
    )

    assert result["daily_sectors"][0]["name"] == "算力"
    assert result["daily_sectors"][0]["limit_count"] == 3
    assert result["daily_sectors"][0]["max_height"] == 5
    assert [row["name"] for row in result["daily_sectors"][0]["leaders"]] == [
        "算力龙头", "算力中军", "算力首板",
    ]
    assert result["daily_sectors"][0]["leaders"][0]["return"] == 10.0
    assert result["mainlines"][0]["name"] == "AI算力"
    assert result["mainlines"][0]["limit_count"] == 3
    assert result["mainlines"][0]["max_height"] == 5
    assert result["cycle_sectors"][0]["name"] == "电子化学品"
    assert result["cycle_sectors"][0]["return"] == 14.0
    assert "算力龙头" in [row["name"] for row in result["cycle_sectors"][0]["leaders"]]
    assert result["continuous_core"] == [{
        "code": "sh600004", "name": "创新药甲", "return": pytest.approx(27.78, abs=0.01),
    }]


def test_market_resonance_limits_leaders_and_reports_small_missing_return_hint():
    from micro_cycle import build_market_resonance

    history = pd.DataFrame([
        {"date": "20260810", "type": "ZT", "code": f"sh60000{i}", "name": f"股票{i}", "height": i}
        for i in range(1, 6)
    ])
    cls = pd.DataFrame([
        {"date": "20260810", "code": f"sh60000{i}", "sub": "机器人", "mainline": "机器人"}
        for i in range(1, 6)
    ])
    result = build_market_resonance(
        history, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
        "2026-08-04", "2026-08-10", previous_date="2026-08-07",
        names=NameResolution(names={}, sources={}, conflicts=[]), cls_attribution=cls,
        continuous_core={"usable": False, "rows": []},
    )

    assert len(result["daily_sectors"][0]["leaders"]) == 3
    assert len(result["mainlines"][0]["leaders"]) == 3
    assert "部分个股收益暂缺" in result["hint"]
