# -*- coding: utf-8 -*-
"""报告日边界 (time_utils) 契约测试。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

_HEADER = "日期,类型,代码,名称,连板数\n"


def _write_cache(path, dates):
    with open(path, "w", encoding="utf-8-sig") as handle:
        handle.write(_HEADER)
        for day in dates:
            handle.write(f"{day},ZT,sz000001,平安银行,1\n")


def test_latest_date_memo_invalidates_when_cache_is_rewritten_mid_run(tmp_path, monkeypatch):
    """同一进程内涨停缓存被追加后, get_latest_date 必须看到新交易日。

    回归 2026-08-11 事故: 第 1 步 fetch_zt_pool_data 会先调用
    get_cached_trading_dates() -> get_latest_date() (此时缓存最新仅到前一交易日),
    随后才把当日涨停写入缓存。旧实现 memo 只按 (REPORT_DATE, cutoff) 缓存, 二者
    全程不变, 于是第 2 步复用了陈旧的 memo, 当日被 trade_dates_set 过滤掉,
    update_price_cache 收到的 max 日期退回前一日并直接命中"缓存已完整"提前返回,
    整份报告渲染成昨天。
    """
    import paths
    import time_utils

    cache = tmp_path / "涨停历史缓存.csv"
    monkeypatch.setattr(paths, "ZT_CACHE_FILE", str(cache))
    monkeypatch.setenv("REPORT_DATE", "20260811")
    monkeypatch.setattr(time_utils, "_cached_latest_date", None)
    monkeypatch.setattr(time_utils, "_cached_latest_date_key", None)

    _write_cache(cache, ["20260807", "20260810"])
    assert time_utils.get_latest_date().strftime("%Y%m%d") == "20260810"

    _write_cache(cache, ["20260807", "20260810", "20260811"])
    assert time_utils.get_latest_date().strftime("%Y%m%d") == "20260811"


def test_latest_date_memo_still_serves_repeat_calls_without_cache_change(tmp_path, monkeypatch):
    """缓存未变时仍走 memo, 不退化成每次重读 CSV。"""
    import paths
    import time_utils

    cache = tmp_path / "涨停历史缓存.csv"
    monkeypatch.setattr(paths, "ZT_CACHE_FILE", str(cache))
    monkeypatch.setenv("REPORT_DATE", "20260811")
    monkeypatch.setattr(time_utils, "_cached_latest_date", None)
    monkeypatch.setattr(time_utils, "_cached_latest_date_key", None)

    _write_cache(cache, ["20260810", "20260811"])
    first = time_utils.get_latest_date()

    reads = []
    real_read_csv = time_utils.pd.read_csv

    def _counting_read_csv(*args, **kwargs):
        reads.append(args[0] if args else kwargs.get("filepath_or_buffer"))
        return real_read_csv(*args, **kwargs)

    monkeypatch.setattr(time_utils.pd, "read_csv", _counting_read_csv)
    second = time_utils.get_latest_date()

    assert second == first
    assert reads == []


def test_latest_date_falls_back_to_cutoff_when_cache_is_missing(tmp_path, monkeypatch):
    """缓存文件缺失时回落到报告截止日, 且不污染后续 memo。"""
    import paths
    import time_utils

    monkeypatch.setattr(paths, "ZT_CACHE_FILE", str(tmp_path / "missing.csv"))
    monkeypatch.setenv("REPORT_DATE", "20260811")
    monkeypatch.setattr(time_utils, "_cached_latest_date", None)
    monkeypatch.setattr(time_utils, "_cached_latest_date_key", None)

    assert time_utils.get_latest_date().strftime("%Y%m%d") == "20260811"
