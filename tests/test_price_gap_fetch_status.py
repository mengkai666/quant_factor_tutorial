# -*- coding: utf-8 -*-
"""价格补缺必须进抓取状态契约 (2026-09-12)。

病理: 价格补缺是全流程里**唯一**一条每天真跑、却完全不进 `data/fetch_status.csv`
的抓取路径 —— 成功/部分/失败、请求数与覆盖数只落在本地 meta 字典里。于是质量闸门、
审计和报告里"价格可用"这句话没有任何抓取证据可核; 而价格是核心数据集, 缺一天会同时
打坏 A/D、连板与收益。

判据口径 (与既有 dataset 一致): status 量的是"**缺口补上了吗**", 不是"我发出去的
请求成功了吗" —— 一只都没抓到却写 success 是 2026-08-31 已经踩过的坑。
"""
import importlib
from datetime import datetime, timezone

import pandas as pd
import pytest


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    """把状态契约指到临时文件, 返回读取工具。"""
    module = importlib.import_module("主线强度追踪")
    import paths

    target = tmp_path / "fetch_status.csv"
    monkeypatch.setattr(paths, "FETCH_STATUS_CACHE", str(target))

    def read():
        if not target.exists():
            return pd.DataFrame()
        return pd.read_csv(target, dtype=str).fillna("")

    return module, read


def _meta(**overrides):
    meta = {
        "fallback_requested": 0,
        "fallback_covered": 0,
        "fallback_deferred": 0,
        "fallback_status": "not_needed",
        "fallback_message": "",
    }
    meta.update(overrides)
    return meta


def test_no_gaps_records_nothing(recorded):
    """没有缺口也没有延后 = 没活要干, 不留一行无信息的 success。"""
    module, read = recorded
    module._record_price_gap_fetch_status(
        _meta(), ["2026-09-10"], datetime.now(timezone.utc),
    )
    assert read().empty


def test_full_coverage_records_success(recorded):
    module, read = recorded
    module._record_price_gap_fetch_status(
        _meta(fallback_requested=12, fallback_covered=12, fallback_status="success"),
        ["2026-09-10"], datetime.now(timezone.utc),
    )
    row = read().iloc[0]
    assert row["dataset"] == "prices"
    assert row["date"] == "2026-09-10"
    assert row["status"] == "success"
    assert row["expected_count"] == "12"
    assert row["actual_count"] == "12"


def test_partial_coverage_is_not_reported_as_success(recorded):
    module, read = recorded
    module._record_price_gap_fetch_status(
        _meta(fallback_requested=500, fallback_covered=180, fallback_status="partial"),
        ["2026-09-10"], datetime.now(timezone.utc),
    )
    assert read().iloc[0]["status"] == "partial"


def test_source_failure_is_recorded_as_failed(recorded):
    module, read = recorded
    module._record_price_gap_fetch_status(
        _meta(fallback_requested=500, fallback_covered=0, fallback_status="failed",
              fallback_message="ProxyError"),
        ["2026-09-10"], datetime.now(timezone.utc),
    )
    row = read().iloc[0]
    assert row["status"] == "failed"
    assert "ProxyError" in row["message"]


def test_deferred_gaps_are_still_disclosed(recorded):
    """缺口被延后 (一只没抓) 也必须留痕, 且不许写成 success。"""
    module, read = recorded
    module._record_price_gap_fetch_status(
        _meta(fallback_requested=0, fallback_covered=0, fallback_deferred=5205,
              fallback_status="deferred"),
        ["2026-09-10"], datetime.now(timezone.utc),
    )
    row = read().iloc[0]
    assert row["status"] == "failed"
    assert row["status"] != "success"


def test_status_is_keyed_by_date_so_a_rerun_replaces_it(recorded):
    """同一天重跑只保留最后一条, 与 FetchStatusStore 的既有契约一致。"""
    module, read = recorded
    for covered, status in ((1, "partial"), (12, "success")):
        module._record_price_gap_fetch_status(
            _meta(fallback_requested=12, fallback_covered=covered, fallback_status=status),
            ["2026-09-10"], datetime.now(timezone.utc),
        )
    frame = read()
    assert len(frame) == 1
    assert frame.iloc[0]["status"] == "success"
