# -*- coding: utf-8 -*-
"""收尾自检 (src/data_selfcheck.py) 单测。

只测两件真正会出事的事:
  · 窗口自适应: 口径台阶要切掉, 单日空洞要留在窗口里 (纯函数, 零 IO);
  · 缺陷类数解析 + 体检脚本缺失时的降级 (绝不能让自检自己把日报判失败)。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

import data_selfcheck as S  # noqa: E402


def _days(n, start=1):
    """生成 n 个升序日期串 (只要能排序, 不必是真交易日)。"""
    return [f'2026-08-{d:02d}' for d in range(start, start + n)]


def test_window_stops_above_regime_step():
    """口径台阶 (持续的低位) 必须被切掉, 否则老口径整段被新中位数判成覆盖不足。"""
    dates = _days(25)
    levels = {d: (4700 if i < 10 else 5538) for i, d in enumerate(dates)}
    window = S._window_from_levels(dates, levels)
    # 台阶之上共 15 天; 允许 MIN_WINDOW 兜底, 但绝不能把老口径那 10 天圈进来
    assert window == 15
    assert len(dates) - window == 10


def test_window_keeps_single_day_hole():
    """单日塌陷是真空洞, 必须留在窗口里让体检抓到。"""
    dates = _days(25)
    levels = {d: 5538 for d in dates}
    levels[dates[-3]] = 333          # 倒数第三天整段没抓到 (2026-08-28 事故形状)
    window = S._window_from_levels(dates, levels)
    assert window >= 3, '空洞被切出窗口, 体检就永远看不到它'
    assert dates[-window] <= dates[-3]


def test_window_clamped_and_short_cache():
    """窗口有上下限; 缓存太短就整段审。"""
    dates = _days(28)
    assert S._window_from_levels(dates, {d: 5538 for d in dates}) == 28
    short = _days(4)
    assert S._window_from_levels(short, {d: 5538 for d in short}) == 4
    long_dates = [f'2026-{m:02d}-{d:02d}' for m in (5, 6, 7, 8) for d in range(1, 26)]
    assert S._window_from_levels(long_dates, {d: 5538 for d in long_dates}) == S.MAX_WINDOW


def test_count_defects_parses_and_falls_back():
    assert S._count_defects('  ❌ 发现 3 类缺陷:\n     - 覆盖不足') == 3
    assert S._count_defects('一切正常') == 0


def test_missing_audit_script_degrades_not_fails(monkeypatch, capsys):
    """体检脚本不在 → 返回 -1 (不是缺陷), 日报退出码不受影响。"""
    monkeypatch.setattr(S, 'AUDIT_SCRIPT', os.path.join(os.sep, 'nope', 'absent.py'))
    assert S.run_selfcheck(recent=5) == -1
    assert '跳过' in capsys.readouterr().out


def test_regime_window_falls_back_when_cache_unreadable(monkeypatch):
    """窗口现算失败必须回落到兜底天数, 不能抛。"""
    import pandas as pd
    monkeypatch.setattr(pd, 'read_csv', lambda *a, **k: (_ for _ in ()).throw(OSError('boom')))
    window, why = S._regime_window()
    assert window == S.DEFAULT_WINDOW
    assert '兜底' in why
