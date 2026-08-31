# -*- coding: utf-8 -*-
"""价格缓存逐日切片 (src/price_slices.py) 单测。

全部在 tmp_path 里, 不碰真实 data/。重点是三条容易写错的规则:
  · 内容没变就不重写切片 (切片进 git, 无谓改写=每天一堆噪声 diff);
  · 合并时**本地优先** —— 切片不许覆盖本地刚修好的值;
  · 保留上限从最老的开始删。
"""
import gzip
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

import price_slices as PS  # noqa: E402

COLS = ['date', 'code', 'close_raw', 'close_qfq', 'close_legacy',
        'price_basis', 'source', 'source_timestamp']


def _frame(rows):
    return pd.DataFrame(rows, columns=COLS)


def _row(date, code, close=10.0, source='tencent'):
    return [date, code, close, close, None, 'raw', source, date.replace('-', '')]


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    slice_dir = tmp_path / 'price_slices'
    slice_dir.mkdir()
    cache = tmp_path / 'price_history_cache.csv'
    monkeypatch.setattr(PS, 'PRICE_SLICE_DIR', str(slice_dir))
    monkeypatch.setattr(PS, 'PRICE_CACHE', str(cache))
    return slice_dir, cache


def test_export_is_idempotent_on_unchanged_content(sandbox):
    slice_dir, _ = sandbox
    frame = _frame([_row('2026-08-31', 'sh600000'), _row('2026-08-31', 'sz000001')])
    assert PS.export_slices(frame, quiet=True) == ['2026-08-31']
    assert (slice_dir / '2026-08-31.csv.gz').exists()
    # 同样的内容再导一次: 一个字节都不该写
    assert PS.export_slices(frame, quiet=True) == []
    # 内容变了才重写
    changed = _frame([_row('2026-08-31', 'sh600000', close=11.0),
                      _row('2026-08-31', 'sz000001')])
    assert PS.export_slices(changed, quiet=True) == ['2026-08-31']


def test_export_window_and_prune(sandbox):
    slice_dir, _ = sandbox
    rows = [_row(f'2026-08-{d:02d}', 'sh600000') for d in range(1, 13)]
    PS.export_slices(_frame(rows), days=5, quiet=True)
    assert PS.available_dates() == [f'2026-08-{d:02d}' for d in range(8, 13)]
    PS.export_slices(_frame(rows), days=12, keep=3, quiet=True)
    kept = PS.available_dates()
    assert len(kept) == 3 and kept[-1] == '2026-08-12'


def test_merge_fills_missing_day_without_overwriting_local(sandbox):
    _, _cache = sandbox
    # 切片里 08-28 有两只, 且 sh600000 的收盘是"旧值"
    PS.export_slices(_frame([_row('2026-08-28', 'sh600000', close=9.0),
                             _row('2026-08-28', 'sz000001', close=8.0)]), quiet=True)
    # 本地只有 sh600000, 且已被回补修成 9.5 —— 合并后必须保留 9.5
    local = _frame([_row('2026-08-28', 'sh600000', close=9.5)])
    merged, filled = PS.merge_slices(local, quiet=True)
    assert filled == ['2026-08-28(+1)']
    assert len(merged) == 2
    got = merged.set_index('code')['close_raw'].to_dict()
    assert got['sh600000'] == 9.5, '切片覆盖了本地修复值'
    assert got['sz000001'] == 8.0


def test_merge_skips_when_local_not_thinner(sandbox):
    PS.export_slices(_frame([_row('2026-08-28', 'sh600000')]), quiet=True)
    local = _frame([_row('2026-08-28', 'sh600000'), _row('2026-08-28', 'sz000001')])
    merged, filled = PS.merge_slices(local, quiet=True)
    assert filled == [] and len(merged) == 2


def test_sync_writes_cache_back(sandbox):
    _, cache = sandbox
    PS.export_slices(_frame([_row('2026-08-28', 'sh600000'),
                             _row('2026-08-28', 'sz000001')]), quiet=True)
    _frame([_row('2026-08-31', 'sh600000')]).to_csv(cache, index=False)
    filled = PS.sync_cache_from_slices(quiet=True)
    assert filled == ['2026-08-28(+2)']
    back = pd.read_csv(cache, dtype={'code': str, 'date': str})
    assert sorted(back['date'].unique()) == ['2026-08-28', '2026-08-31']
    assert len(back) == 3


def test_missing_and_corrupt_slice_are_harmless(sandbox):
    slice_dir, _ = sandbox
    assert PS.read_slice('2026-01-01').empty
    (slice_dir / '2026-08-28.csv.gz').write_bytes(b'not gzip at all')
    assert PS.read_slice('2026-08-28').empty
    merged, filled = PS.merge_slices(_frame([_row('2026-08-31', 'sh600000')]), quiet=True)
    assert filled == [] and len(merged) == 1


def test_slice_is_real_gzip_with_stable_mtime(sandbox):
    slice_dir, _ = sandbox
    PS.export_slices(_frame([_row('2026-08-31', 'sh600000')]), quiet=True)
    path = slice_dir / '2026-08-31.csv.gz'
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        assert handle.readline().strip().startswith('date,code')
    # mtime=0 固定写死: 否则同内容不同时间戳也算变更, git 天天有 diff
    assert path.read_bytes()[4:8] == b'\x00\x00\x00\x00'


def test_slice_bytes_are_lf_only(sandbox):
    """换行符必须钉成 LF: 否则 Windows 导出的切片和 CI(Linux) 的字节不同,
    每次换机器跑都诈出一个"变更", 且内容比对永远判不出"没变"。"""
    slice_dir, _ = sandbox
    PS.export_slices(_frame([_row('2026-08-31', 'sh600000'),
                             _row('2026-08-31', 'sz000001')]), quiet=True)
    with gzip.open(slice_dir / '2026-08-31.csv.gz', 'rb') as handle:
        raw = handle.read()
    assert b'\r\n' not in raw
    assert raw.count(b'\n') == 3         # 表头 + 2 行
