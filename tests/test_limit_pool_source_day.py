# -*- coding: utf-8 -*-
"""涨停池: 源申报日期与请求日不一致时的**既定处置 = 记录 + 披露, 不是丢弃**。

⚠️ 这个文件存在的意义是**防止一次已经犯过的错**。
    2026-09-12 的数据获取复盘里, 我提出"源报的 qdate ≠ 请求日就在 provider 层直接拒绝该源",
    并真的实现了。全量回归当场打红 5 条测试, 因为项目**早就有**一套更完整的处置:

        东财 push2ex 的 `qdate` 由 `limit_event_provenance` 抽成 `trade_date` 证据,
        与请求日不一致的记录**照常入库**, 但:
          · 行级 `trade_date` 保留源的原始值, `trade_date_evidence` 保留字段名与原值;
          · `build_limit_event_snapshot` 把它统计成
            `quality.trade_date_mismatches / record_… / provenance_…`;
          · `recap_panels` 把这个计数渲染成报告里的披露。

    也就是说: **数据被"标注", 而不是被"丢弃"** —— 这才是这个项目的既定哲学
    (与降级模块要逐条披露、而不是静默丢掉, 是同一套原则)。在 provider 层拒绝的后果是
    那个计数永远是 0, 报告里的披露变成死代码, 真出现陈旧源时反而只剩一句"源失败",
    丢失了"它答的是哪一天"这个最关键的信息。

    要加"更硬的拒绝"之前请先读 tests/test_limit_events.py::
    test_review_eastmoney_qdate_survives_to_archive_and_flags_even_empty_pool —— 它逐条钉住了
    上文的行为 (含空池这种没有名单可比的情形)。
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from data_sources.limit_pool_provider import LimitPoolProvider
from data_sources.limit_pool_sources import EastmoneyLimitPoolSource
from data_sources.models import FetchStatus

TRADE_DATE = "2026-09-04"
SOURCE_DATE = "20260903"          # 源给的是**前一天**
FETCHED_AT = datetime(2026, 9, 4, 7, 5, tzinfo=timezone.utc)


def _session(payload):
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    return SimpleNamespace(get=lambda *args, **kwargs: response)


def _provider(zt, dt=None):
    return LimitPoolProvider(
        fetch_zt=lambda _: zt,
        fetch_dt=lambda _: pd.DataFrame() if dt is None else dt,
        now=lambda: FETCHED_AT,
    )


def _source(**extra):
    data = {"pool": [], "qdate": int(SOURCE_DATE)}
    data.update(extra)
    return EastmoneyLimitPoolSource(session=_session({"rc": 0, "data": data}),
                                    min_interval=0, now=lambda: FETCHED_AT)


def _events():
    import importlib
    return importlib.import_module("limit_events")


def test_a_wrong_day_source_is_recorded_not_silently_dropped():
    """源报了另一天: 记录照常入库, 且**保留源自己的日期作为证据**。"""
    frame = _source().fetch_zt(TRADE_DATE)

    assert frame.attrs["trade_date"] == "2026-09-03"
    assert frame.attrs["trade_date_source"] == "source"
    # 证据保留源的**原值原样** (接口给的是整数 20260903, 就记整数, 不顺手"美化")。
    assert frame.attrs["trade_date_evidence"] == {
        "field": "qdate", "value": int(SOURCE_DATE),
    }


def test_wrong_day_source_still_returns_a_result_so_it_can_be_flagged():
    """返回状态保持既有语义 (空池 = ZERO): 拒绝发生在**披露层**, 不在抓取层。"""
    frame = _source().fetch_zt(TRADE_DATE)
    result = _provider(frame).fetch_day(TRADE_DATE)

    assert result.status is FetchStatus.ZERO
    assert result.data.attrs["pool_provenance"]["ZT"]["trade_date"] == "2026-09-03"


def test_wrong_day_is_counted_as_a_mismatch_in_the_archived_quality(tmp_path):
    """这才是这道问题的**处置点**: 归档质量里必须数得出来。"""
    events = _events()
    frame = _source().fetch_zt(TRADE_DATE)
    result = _provider(frame).fetch_day(TRADE_DATE)

    snapshot = events.build_limit_event_snapshot(result.data, TRADE_DATE)
    events.archive_limit_event_snapshot(snapshot, tmp_path)
    archived = events.load_limit_event_snapshot(tmp_path, TRADE_DATE)

    assert archived["quality"]["trade_date_mismatches"] == 1
    assert archived["quality"]["provenance_trade_date_mismatches"] == 1
    # 归档的**分区**日期仍然是请求日 —— 错的是上游证据, 不是归档位置。
    assert archived["trade_date"] == TRADE_DATE


def test_matching_source_date_yields_no_mismatch():
    """同一份流程在源日期正确时不得误报 —— 披露要有意义就必须是准的。"""
    events = _events()
    source = EastmoneyLimitPoolSource(
        session=_session({"rc": 0, "data": {"pool": [{"c": "600000", "n": "样本", "lbc": 1}],
                                            "qdate": 20260904}}),
        min_interval=0, now=lambda: FETCHED_AT,
    )
    result = _provider(source.fetch_zt(TRADE_DATE)).fetch_day(TRADE_DATE)

    snapshot = events.build_limit_event_snapshot(result.data, TRADE_DATE)

    assert snapshot["quality"]["trade_date_mismatches"] == 0
    assert result.status is FetchStatus.SUCCESS
