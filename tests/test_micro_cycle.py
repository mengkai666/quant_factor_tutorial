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


def test_detect_micro_cycle_separates_signal_close_confirmation_and_full_breakout():
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
    assert result["confirmation_date"] == "2026-08-05"
    assert result["full_confirmation_date"] == "2026-08-06"
    assert result["status"] == "小周期主升"
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
