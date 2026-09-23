from copy import deepcopy

import pytest

from limit_events import build_limit_event_snapshot


def snapshot(*, count=0, pool="ZT", **extra):
    row = {"code": "sz002702", "name": "测试", "date": "2026-09-07", "pool_type": pool,
           "limit_count": 2, "break_count": count, "first_limit_time": "092500",
           "last_limit_time": "092500", "turnover_rate": 2.0,
           "source": "akshare_em", "source_timestamp": "2026-09-07T16:00:00+08:00", **extra}
    return build_limit_event_snapshot([row], "2026-09-07")


def bar(**extra):
    return {"code": "sz002702", "date": "2026-09-07", "open_raw": 10.0, "high_raw": 10.0,
            "low_raw": 10.0, "close_raw": 10.0, "price_basis": "raw", "source": "fixture_daily_raw",
            "source_timestamp": "2026-09-08T10:00:00+08:00", **extra}


@pytest.mark.parametrize("count,broken,reclosed", [(0, False, False), (1, True, True), (4, True, True)])
def test_known_sealed_members_and_break_counts_become_auditable_facts(count, broken, reclosed):
    from event_facts import resolve_limit_event_facts
    raw = snapshot(count=count)
    before = deepcopy(raw)
    got = resolve_limit_event_facts(raw)
    row = got["records"][0]
    assert row["limit_up_attempted"] is True
    assert row["broken"] is broken
    assert row["reclosed"] is reclosed
    assert row["board_type"] is None
    assert row["raw_event_fields"]["broken"] is None
    assert row["derived_event_evidence"]["broken"]["inputs"]["break_count"] == count
    assert raw == before
    assert got["full_market_coverage"] is False


@pytest.mark.parametrize("count", [None, -1, True, 1.5, float("nan"), "unknown"])
def test_unknown_or_invalid_break_count_is_never_assumed_zero(count):
    from event_facts import resolve_limit_event_facts
    row = resolve_limit_event_facts(snapshot(count=count))["records"][0]
    assert row["limit_up_attempted"] is True
    assert row["broken"] is None and row["reclosed"] is None


def test_down_pool_counts_are_not_up_limit_events():
    from event_facts import resolve_limit_event_facts
    row = resolve_limit_event_facts(snapshot(count=3, pool="DT"))["records"][0]
    assert all(row[key] is None for key in ("limit_up_attempted", "broken", "reclosed", "board_type"))


def test_failed_up_limit_pool_has_observed_attempt_but_no_current_reclose():
    from event_facts import resolve_limit_event_facts
    row = resolve_limit_event_facts(snapshot(count=2, pool="ZB"))["records"][0]
    assert row["limit_up_attempted"] is True and row["broken"] is True
    assert row["reclosed"] is False


@pytest.mark.parametrize("change", [{"trade_date": "2026-09-06"}, {"source": None}, {"source_timestamp": None}])
def test_unproven_provenance_never_derives_flags(change):
    from event_facts import resolve_limit_event_facts
    raw = snapshot(count=1)
    raw["records"][0].update(change)
    row = resolve_limit_event_facts(raw)["records"][0]
    assert all(row[key] is None for key in ("limit_up_attempted", "broken", "reclosed"))


@pytest.mark.parametrize("count,expected", [(1, True), (0, False), (None, None)])
def test_explicit_unknown_remains_raw_but_independent_evidence_can_explain_it(count, expected):
    from event_facts import resolve_limit_event_facts
    raw = snapshot(count=count, broken=None)
    before = deepcopy(raw)
    row = resolve_limit_event_facts(raw)["records"][0]
    assert row["broken"] is expected
    assert row["raw_event_fields"]["broken"] is None
    assert row["event_evidence"]["broken"]["value"] is None
    if count is not None:
        assert row["derived_event_evidence"]["broken"]["inputs"]["break_count"] == count
    assert raw == before


def test_contradictory_observation_remains_a_visible_conflict():
    from event_facts import resolve_limit_event_facts
    from strategy_qualification import build_strategy_event_input
    from event_qualification import assess_event_metrics
    raw = snapshot(count=2, broken=False)
    got = resolve_limit_event_facts(raw)
    assert got["records"][0]["broken"] is False
    assert "broken" in got["records"][0]["event_fact_conflicts"]
    result = assess_event_metrics(build_strategy_event_input(got, report_date="2026-09-07"))
    assert result["metrics"]["bomb_rate"]["status"] == "invalid"


@pytest.mark.parametrize("price,expected", [(bar(), "one_word"), (bar(open_raw=9.8, low_raw=9.7), "turnover")])
def test_board_type_uses_same_day_raw_ohlc_evidence(price, expected):
    from event_facts import resolve_limit_event_facts
    raw = snapshot()
    row = resolve_limit_event_facts(raw, price_rows=[price])["records"][0]
    assert row["board_type"] == expected
    assert row["derived_event_evidence"]["board_type"]["inputs"]["bar"]["source"] == "fixture_daily_raw"


@pytest.mark.parametrize("price", [bar(date="2026-09-08"), bar(price_basis="qfq"), bar(low_raw=None),
    bar(source=None), bar(source_timestamp=None), bar(low_raw=12.0), bar(high_raw=float("inf"))])
def test_bad_or_wrong_day_bars_cannot_supply_board_type(price):
    from event_facts import resolve_limit_event_facts
    assert resolve_limit_event_facts(snapshot(), price_rows=[price])["records"][0]["board_type"] is None


def test_times_and_turnover_alone_do_not_prove_board_type():
    from event_facts import resolve_limit_event_facts
    assert resolve_limit_event_facts(snapshot())["records"][0]["board_type"] is None


def test_resolution_is_idempotent_and_retains_raw_coverage():
    from event_facts import resolve_limit_event_facts
    once = resolve_limit_event_facts(snapshot(count=1), price_rows=[bar(open_raw=9.8, low_raw=9.7)])
    twice = resolve_limit_event_facts(once, price_rows=[bar(open_raw=9.8, low_raw=9.7)])
    assert once == twice
    assert once["raw_field_coverage"]["broken"]["known"] == 0


def test_strategy_input_actually_consumes_known_event_facts():
    from strategy_qualification import build_strategy_event_input
    got = build_strategy_event_input(snapshot(count=2), report_date="2026-09-07")
    assert got["event_input_coverage"]["observed"]["broken"] == 1
    assert got["bomb_rate"]["trials"] == 1
    assert got["event_population"]["complete"] is False


@pytest.mark.parametrize("extra", [{"broken": "true", "reclosed": "yes"}, {"broken": 1, "reclosed": 1}])
def test_equivalent_explicit_flags_are_not_false_conflicts(extra):
    from event_facts import resolve_limit_event_facts
    row = resolve_limit_event_facts(snapshot(count=1, **extra))["records"][0]
    assert row["broken"] is True and row["reclosed"] is True
    assert row["event_fact_conflicts"] == []


def test_equivalent_board_alias_is_normalized_without_conflict():
    from event_facts import resolve_limit_event_facts
    row = resolve_limit_event_facts(snapshot(board_type="一字板"), price_rows=[bar()])["records"][0]
    assert row["board_type"] == "one_word"
    assert row["raw_event_fields"]["board_type"] == "一字板"
    assert row["event_fact_conflicts"] == []
