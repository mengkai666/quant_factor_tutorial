# -*- coding: utf-8 -*-
"""LongHu 快照的陈旧检测 (2026-09-12)。

病理: 接口**忽略 Day 参数**, 永远返回最近一个交易日的快照。
    2026-09-12 实测: 请求 2026-09-10 与请求 2026-07-15, 返回的都是
    `date = 2026-09-11`、且 `nums` 逐字段完全相同。

原实现把请求日直接当成返回数据的日期写进结果 (`"date": day.replace('-','')`),
于是调用方 `api_date == d` 的陈旧判据是**自证循环** —— api_date 就是自己写进去的
请求日, 永远成立。修法很简单: 接口自己报了 `date`, 拿它和请求日比。

为什么必须拒: LongHu 是 A/D 的兜底源。用最近一天的涨跌家数去填历史日, 会把
"接口不给历史"伪装成真实历史宽度, 而 A/D 是情绪指数/择时信号/回测的共同输入。
"""
import json

import pytest

from limit_ratio_factor import MarketSentimentFactor


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def post(monkeypatch):
    import limit_ratio_factor as module

    calls = []

    def fake_post(url, data=None, headers=None, verify=None, timeout=None):
        calls.append({"url": url, "data": data})
        return _Response(fake_post.payload)

    fake_post.calls = calls
    monkeypatch.setattr(module.requests, "post", fake_post)
    return fake_post


def _nums(up=643, down=4870):
    return {"SZJS": up, "XDJS": down, "ZT": 40, "DT": 21}


def test_stale_snapshot_date_is_rejected(post):
    """源报的是 2026-09-11, 请求的是 2026-07-15 —— 必须丢弃, 不许改名成请求日。"""
    post.payload = {"errcode": "0", "date": "2026-09-11", "nums": _nums()}

    got = MarketSentimentFactor()._fetch_longhu_sentiment(day="2026-07-15")

    assert got is None


def test_matching_snapshot_date_is_accepted(post):
    """当日盘后请求拿到同一天的快照 = 正常路径, 不能被误伤。"""
    post.payload = {"errcode": "0", "date": "2026-09-11", "nums": _nums()}

    got = MarketSentimentFactor()._fetch_longhu_sentiment(day="2026-09-11")

    assert got is not None
    assert got["up"] == 643 and got["down"] == 4870
    assert got["source"] == "longhu_api"


def test_reported_date_is_the_source_date_not_the_request(post):
    """结果里的日期必须来自源自己申报的字段 (紧凑格式保持向后兼容)。"""
    post.payload = {"errcode": "0", "date": "2026-09-11", "nums": _nums()}

    got = MarketSentimentFactor()._fetch_longhu_sentiment(day="2026-09-11")

    assert got["date"] == "20260911"


def test_source_without_a_date_field_is_not_newly_blocked(post):
    """源没给 date 时保守放行 —— 这道闸门只用来拦"报了另一天"的情形。"""
    post.payload = {"errcode": "0", "nums": _nums()}

    got = MarketSentimentFactor()._fetch_longhu_sentiment(day="2026-09-11")

    assert got is not None
    assert got["date"] == "20260911"
