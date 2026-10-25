# -*- coding: utf-8 -*-
import pytest
from unittest.mock import patch
import pandas as pd

from data_completeness_check import check_recent_data_completeness, verify_backfill_completion


def test_data_completeness_check_pass_on_current_cache():
    res = check_recent_data_completeness(window_days=30)
    assert res["status"] == "PASS"
    assert res["all_complete"] is True
    assert len(res["missing_dates"]) == 0


def test_data_completeness_check_detects_gap():
    # 模拟交易日历中多出一个本地缓存不存在的交易日
    mock_dates = ["20261016", "20261015", "20261014"]
    with patch("data_completeness_check.get_trading_dates", return_value=mock_dates):
        res = check_recent_data_completeness(window_days=3)
        assert res["status"] == "GAP_DETECTED"
        assert res["all_complete"] is False
        assert "20261016" in res["missing_dates"]


def test_verify_backfill_completion():
    assert verify_backfill_completion([]) is True
    # 已经存在的日期核验通过
    assert verify_backfill_completion(["20261015"]) is True
