# -*- coding: utf-8 -*-
"""情绪缓存合并边界: 本次重算的残缺 A/D 不得覆盖缓存里的全市场真值。

钉的是 2026-09-01 的事故 (详见 src/ad_breadth.py 模块头):
价格缓存补进 legacy 段后, 3 月那些历史日的重算 A/D 从"没有值"变成"窄而非零",
而合并边界只拦 NaN/0, 于是 12 天全市场真值被静默改窄, 20260319 由普涨翻成下跌。
判据 (should_adopt_reconciled_ad) 一直存在, 是这条**写入路径绕开了它** ——
所以这里同时钉判据的语义和"两个合并边界都必须调用它"。
"""
import os
import re
import sys

import pandas as pd
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'src'))

from ad_breadth import (  # noqa: E402
    MIN_MARKET_BREADTH,
    is_ad_incomplete,
    protect_ad_with_cache,
    resolve_ad_pair,
)


def test_nan_is_not_mistaken_for_complete():
    """NaN 必须判成残缺。`float(nan or 0)` 是 nan 而 `nan < 4000` 为 False ——
    直接把 NaN 喂给判据会反向失效, 这是本模块 _as_float 存在的唯一理由。"""
    assert is_ad_incomplete(float('nan'), float('nan')) is True
    assert is_ad_incomplete(pd.NA, pd.NA) is True
    assert is_ad_incomplete('', None) is True


@pytest.mark.parametrize('date_str,narrow,wide', [
    ('20260310', (2808, 561), (4049, 834)),
    ('20260317', (538, 2728), (817, 4082)),
    ('20260319', (335, 3095), (4096, 1347)),   # 方向被翻转的那天
    ('20260325', (3062, 305), (4329, 539)),
])
def test_narrow_recompute_cannot_overwrite_complete_cache(date_str, narrow, wide):
    """legacy 段重算值 (合计 3300~3900, 只覆盖 ~3800 只) 必须输给缓存的全市场真值。"""
    assert is_ad_incomplete(*narrow) is True
    assert is_ad_incomplete(*wide) is False
    assert resolve_ad_pair(*narrow, *wide) == wide


def test_pair_is_decided_together_never_column_by_column():
    """up/down 必须成对取。一列新一列旧会拼出现实中不存在的比值,
    而 ad_ratio 是情绪指数/择时/回测的共同输入。"""
    up, down = resolve_ad_pair(335, 3095, 4096, 1347)
    assert (up, down) == (4096, 1347)          # 不是 (335, 1347) 也不是 (4096, 3095)


def test_new_day_without_cache_keeps_recomputed_value():
    """缓存没有的那天 (最新交易日) 只能靠重算值, 不许被清空。"""
    assert resolve_ad_pair(3386, 2040, None, None) == (3386, 2040)
    assert resolve_ad_pair(3386, 2040, float('nan'), float('nan')) == (3386, 2040)
    # legacy_tracker 会把缺失的 _cache 列 fillna(0), 0/0 同样等于"缓存没这天"
    assert resolve_ad_pair(3369, 561, 0, 0) == (3369, 561)


def test_placeholder_and_genuinely_wider_recompute_still_win():
    """老行为不许丢: 主数据是 0/0 或 NaN 时照旧取缓存; 重算确实更宽时照旧落地。"""
    assert resolve_ad_pair(0, 0, 4049, 834) == (4049, 834)
    assert resolve_ad_pair(float('nan'), float('nan'), 4049, 834) == (4049, 834)
    # 缓存本身残缺 (CI 浅缓存写坏的 71/779) → 完整重算值必须覆盖
    assert resolve_ad_pair(615, 4495, 71, 779) == (615, 4495)
    # 同口径微调 (99.5%) 在容差内 → 采用重算值, 避免每天制造 diff 噪声
    assert resolve_ad_pair(4107, 1248, 4174, 1208) == (4107, 1248)


def test_protect_ad_with_cache_reports_and_rewrites_only_downgrades():
    df = pd.DataFrame({
        '日期': ['20260319', '20260901', '20260707', '20260320'],
        'up': [335.0, 3386.0, 615.0, 585.0],
        'down': [3095.0, 2040.0, 4495.0, 4343.0],
        'up_cache': [4096.0, float('nan'), 71.0, 585.0],
        'down_cache': [1347.0, float('nan'), 779.0, 4343.0],
    })
    kept = protect_ad_with_cache(df)
    assert kept == ['20260319']                       # 只有被降级的那天回退
    assert (df.at[0, 'up'], df.at[0, 'down']) == (4096.0, 1347.0)
    assert (df.at[1, 'up'], df.at[1, 'down']) == (3386.0, 2040.0)
    assert (df.at[2, 'up'], df.at[2, 'down']) == (615.0, 4495.0)
    assert (df.at[3, 'up'], df.at[3, 'down']) == (585.0, 4343.0)


def test_missing_columns_are_tolerated():
    df = pd.DataFrame({'日期': ['20260901'], 'up': [3386.0], 'down': [2040.0]})
    assert protect_ad_with_cache(df) == []


@pytest.mark.parametrize('rel', ['src/主线强度追踪.py', 'src/legacy_tracker.py'])
def test_both_merge_boundaries_route_through_the_judge(rel):
    """判据存在不等于判据生效 —— 两处合并边界都必须调用 protect_ad_with_cache,
    且 up/down 不许再退回列级 fill_mask (那条路会让窄值静默取胜)。"""
    src = open(os.path.join(_ROOT, rel), encoding='utf-8').read()
    assert 'protect_ad_with_cache(sentiment_df)' in src, rel + ' 的合并边界绕开了判据'
    assert 'MIN_MARKET_BREADTH = 4000' not in src, rel + ' 又抄了一份阈值, 判据会漂移'
    # 每处 `fill_mask =` 往上找最近的 `for col in [...]`, 其列名不许含 up/down
    # (列级回填 = 一列可能取新值另一列取旧值, 且只拦 NaN/0, 拦不住"窄而非零")
    for hit in re.finditer(r'fill_mask\s*=', src):
        headers = re.findall(r'for col in \[([^\]]*)\]:', src[:hit.start()])
        assert headers, rel + ' 的 fill_mask 不在 for col 循环里, 请核对本测试的假设'
        cols = {c.strip().strip('\'"') for c in headers[-1].split(',')}
        assert not cols & {'up', 'down'}, (
            rel + ' 的 fill_mask 又回到了 up/down 列级回填: ' + repr(sorted(cols)))


def test_threshold_is_single_sourced():
    assert MIN_MARKET_BREADTH == 4000
    import importlib
    for mod in ('limit_ratio_factor', 'legacy_tracker'):
        assert importlib.import_module(mod).MIN_MARKET_BREADTH is MIN_MARKET_BREADTH
