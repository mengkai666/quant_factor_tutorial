# -*- coding: utf-8 -*-
"""锚定日前复权补齐。

病理: 报告日 qfq 覆盖不足会触发逐股网络补 qfq (5000+ 只, 十几分钟), 而前复权锚点
就在最新一根 K 线上, qfq(锚定日) == raw(锚定日) 是恒等式 —— 抓回来的值与 raw 逐股
相同, 纯白跑。只对锚定日成立, 历史日锚点已右移 (实测最大偏 10.23%), 不能碰。
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from anchor_qfq import QFQ_ANCHOR_TAG, fill_anchor_day_qfq, normalize_price_date


def _frame():
    return pd.DataFrame([
        # 锚定日: 有 raw 缺 qfq → 应补
        {'code': 'sh600000', 'date': '2026-08-27', 'close_raw': 10.5,
         'close_qfq': None, 'close_legacy': None, 'price_basis': 'raw',
         'source': 'tencent', 'source_timestamp': ''},
        # 锚定日: 已有 qfq → 不覆盖
        {'code': 'sz000001', 'date': '2026-08-27', 'close_raw': 20.0,
         'close_qfq': 19.1, 'close_legacy': None, 'price_basis': 'raw+qfq',
         'source': 'tencent', 'source_timestamp': ''},
        # 锚定日: 没有 raw → 不动
        {'code': 'bj430001', 'date': '2026-08-27', 'close_raw': None,
         'close_qfq': None, 'close_legacy': 8.0, 'price_basis': 'legacy_mixed',
         'source': 'legacy', 'source_timestamp': ''},
        # 历史日: 锚点已右移, 一行不许动
        {'code': 'sh600000', 'date': '2026-08-24', 'close_raw': 9.9,
         'close_qfq': None, 'close_legacy': None, 'price_basis': 'raw',
         'source': 'sina_raw', 'source_timestamp': ''},
    ])


def test_fills_anchor_day_only():
    out, filled = fill_anchor_day_qfq(_frame(), '2026-08-27')
    assert filled == 1
    anchor = out[(out['code'] == 'sh600000') & (out['date'] == '2026-08-27')].iloc[0]
    assert anchor['close_qfq'] == 10.5          # 恒等于 raw
    assert anchor['price_basis'] == 'raw+qfq'
    assert QFQ_ANCHOR_TAG in anchor['source']   # 来源可追溯
    history = out[(out['code'] == 'sh600000') & (out['date'] == '2026-08-24')].iloc[0]
    assert pd.isna(history['close_qfq'])        # 历史日不许补


def test_never_overwrites_existing_qfq():
    out, _ = fill_anchor_day_qfq(_frame(), '2026-08-27')
    row = out[out['code'] == 'sz000001'].iloc[0]
    assert row['close_qfq'] == 19.1
    assert row['source'] == 'tencent'


def test_skips_rows_without_raw():
    out, _ = fill_anchor_day_qfq(_frame(), '2026-08-27')
    row = out[out['code'] == 'bj430001'].iloc[0]
    assert pd.isna(row['close_qfq'])


def test_accepts_compact_date_and_code_filter():
    out, filled = fill_anchor_day_qfq(_frame(), '20260827', codes=['sz000001'])
    assert filled == 0                          # sh600000 不在 codes 里
    assert pd.isna(out[out['code'] == 'sh600000'].iloc[0]['close_qfq'])
    assert normalize_price_date('20260827') == '2026-08-27'


def test_no_work_returns_original_object():
    frame = _frame()
    out, filled = fill_anchor_day_qfq(frame, '2026-08-21')
    assert filled == 0 and out is frame         # 无可填不复制 50 万行


def test_empty_and_missing_columns_are_safe():
    assert fill_anchor_day_qfq(pd.DataFrame(), '2026-08-27')[1] == 0
    assert fill_anchor_day_qfq(None, '2026-08-27')[1] == 0
    bare = pd.DataFrame([{'code': 'sh600000', 'date': '2026-08-27', 'close': 1.0}])
    assert fill_anchor_day_qfq(bare, '2026-08-27')[1] == 0


def test_refuses_when_anchor_is_not_the_newest_bar():
    """frame 里还有更晚的行 = 传进来的这天已不是锚点, qfq≠raw, 必须拒填。"""
    frame = _frame()
    out, filled = fill_anchor_day_qfq(frame, '2026-08-24')
    assert filled == 0 and out is frame
