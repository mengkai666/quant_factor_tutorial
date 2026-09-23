# -*- coding: utf-8 -*-
"""报告必须能自己说清"我是不是最近的一次" (2026-09-12)。

病理: 报告会被归档进 `output/site/reports/{date}.html` 并被邮件转发。读者打开一份
归档件时, 页面只写了绝对日期("报告交易日 2026-09-10"), 没有任何相对今天的时效判断,
于是"三天前的那份"和"今天刚出的那份"在页面上长得一模一样。全文 `过期`/`已过期`/
`历史报告` 出现 0 次。

判据用**交易日**而不是自然日: 周五的收盘报告在周六、周日读都仍然是"最新一份",
按自然日算会误报过期。没有日历时退回自然日, 并明说口径。
"""
import pytest


def test_current_report_has_no_note():
    """报告日就是今天 -> 不该有任何时效警告。"""
    from report_logic import build_freshness_note

    assert build_freshness_note("20260910", today="2026-09-10", trading_days=[]) == ""


def test_latest_closed_report_stays_current_through_the_weekend():
    """周五收盘报告, 周六周日读都还是最新一份 —— 按交易日算必须不报警。"""
    from report_logic import build_freshness_note

    calendar = ["2026-09-10", "2026-09-11"]          # 周四、周五

    assert build_freshness_note("20260911", today="2026-09-12", trading_days=calendar) == ""
    assert build_freshness_note("20260911", today="2026-09-13", trading_days=calendar) == ""


def test_one_elapsed_trading_day_is_reported():
    """09-10 的报告在 09-12(周六)读: 中间已过 09-11 一个交易日。"""
    from report_logic import build_freshness_note

    calendar = ["2026-09-10", "2026-09-11"]
    note = build_freshness_note("20260910", today="2026-09-12", trading_days=calendar)

    assert "1 个交易日" in note
    assert "2026-09-10" in note
    assert "最新报告" in note


def test_multiple_elapsed_trading_days_are_counted():
    from report_logic import build_freshness_note

    calendar = ["2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15"]
    note = build_freshness_note("20260910", today="2026-09-15", trading_days=calendar)

    assert "3 个交易日" in note


def test_falls_back_to_natural_days_and_says_so_when_the_calendar_is_missing():
    """没有日历就不能假装自己知道过了几个交易日 —— 必须换口径并说明。"""
    from report_logic import build_freshness_note

    note = build_freshness_note("20260910", today="2026-09-12", trading_days=None)

    assert "自然日" in note
    assert "交易日" not in note
    assert "2 个自然日" in note


def test_accepts_iso_and_digit_dates_interchangeably():
    from report_logic import build_freshness_note

    calendar = ["2026-09-10", "2026-09-11"]
    assert (build_freshness_note("2026-09-10", today="2026-09-12", trading_days=calendar)
            == build_freshness_note("20260910", today="2026-09-12", trading_days=calendar))


@pytest.mark.parametrize("bad", ["", None, "not-a-date", "2026-13-45", 20260910.0])
def test_bad_report_date_never_raises(bad):
    from report_logic import build_freshness_note

    assert build_freshness_note(bad, today="2026-09-12", trading_days=[]) == ""


def test_future_report_date_is_not_called_stale():
    from report_logic import build_freshness_note

    assert build_freshness_note("20260920", today="2026-09-12", trading_days=[]) == ""


def test_calendar_provider_offers_a_cache_only_lookup():
    """报告渲染不许为了算个天数就去联网拉日历。"""
    from data_sources.calendar_provider import CalendarProvider

    provider = CalendarProvider(source=lambda: (_ for _ in ()).throw(
        AssertionError("cached_trading_days 不该碰数据源")), cache_path=None)

    assert provider.cached_trading_days("2026-09-01", "2026-09-30") == []


def test_calendar_provider_cache_only_lookup_filters_by_range(tmp_path):
    from data_sources.calendar_provider import CalendarProvider

    cache = tmp_path / "calendar.csv"
    cache.write_text("trade_date\n2026-09-10\n2026-09-11\n2026-09-14\n", encoding="utf-8")
    provider = CalendarProvider(cache_path=cache)

    assert provider.cached_trading_days("2026-09-11", "2026-09-14") == [
        "2026-09-11", "2026-09-14"]
