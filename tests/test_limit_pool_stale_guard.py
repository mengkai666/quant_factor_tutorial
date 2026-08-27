# -*- coding: utf-8 -*-
"""涨停池陈旧快照闸门。

病理: 涨停池接口只对当日有效, 盘前跑或失败重试时返回上一交易日的名单, 被写到当日
名下 (2026-08-27 用价格全量体检查出 12 天中招)。闸门放在入库前, 名单与前一交易日
逐条相同就拒收, 让该日空着等下次重试。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from lianban_analysis import (STALE_POOL_JACCARD, STALE_POOL_MIN_CODES,
                              is_stale_pool_snapshot)


def _codes(start, count):
    return [f'{code:06d}' for code in range(start, start + count)]


def test_identical_list_is_rejected():
    """逐条相同 = 典型的陈旧快照 (实测中招那几天 Jaccard 恰为 1.00)。"""
    same = _codes(600000, 40)
    assert is_stale_pool_snapshot(same, list(reversed(same))) is True


def test_normal_continuation_passes():
    """真实相邻日只有连板股重叠 (实测 Jaccard 中位 0.12 / 99 分位 0.47), 必须放行。"""
    today = _codes(600000, 40)
    yesterday = _codes(600032, 40)        # 重合 8 只 = Jaccard 0.11
    assert is_stale_pool_snapshot(today, yesterday) is False


def test_high_but_legal_overlap_passes():
    """连板潮: 40 只里 30 只是昨天的票, Jaccard 0.60, 仍在阈值之下。"""
    today = _codes(600000, 40)
    yesterday = _codes(600010, 40)
    assert is_stale_pool_snapshot(today, yesterday) is False


def test_short_list_is_not_judged():
    """跌停名单常只有 1-2 只, 逐条相同属正常, 样本太小不下结论。"""
    short = _codes(600000, STALE_POOL_MIN_CODES - 1)
    assert is_stale_pool_snapshot(short, short) is False


def test_missing_previous_day_passes():
    """缓存里没有前一交易日 (断档/首次跑) 时无从比对, 放行。"""
    assert is_stale_pool_snapshot(_codes(600000, 40), []) is False


def test_threshold_kept_far_from_normal_distribution():
    """阈值不能滑到正常分布里: 99 分位 0.47, 留足余量。"""
    assert STALE_POOL_JACCARD >= 0.9
