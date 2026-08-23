# -*- coding: utf-8 -*-
"""leader_tracker 单元测试: 孤峰判定 / 断板×情绪分层 / stale 跳过 / 降级不出 None-nan。"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import leader_tracker as lt  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 孤峰判定
# ─────────────────────────────────────────────────────────────
def _ctx(by_day, real_dates, occ=None, as_of=None):
    return {
        'as_of': as_of or real_dates[-1],
        'by_day': by_day,
        'real_dates': real_dates,
        'stale': set(),
        'name_by_code': {c: c for d in by_day.values() for c in d},
        'occ_by_date': occ or {},
    }


def test_lonely_peak_true_when_high_board_has_no_support():
    ctx = _ctx({'20260101': {'000001': 6, '000002': 1, '000003': 1}}, ['20260101'])
    ident = lt._build_identity(ctx, {})
    grav = lt._build_gravity(ctx, ident, {})
    assert grav['is_lonely_peak'] is True
    assert grav['echelon']['n_at_max_1'] == 0


def test_lonely_peak_false_when_echelon_supports():
    ctx = _ctx({'20260101': {'000001': 6, '000002': 5, '000003': 5}}, ['20260101'])
    ident = lt._build_identity(ctx, {})
    grav = lt._build_gravity(ctx, ident, {})
    assert grav['is_lonely_peak'] is False
    assert grav['echelon']['n_at_max_1'] == 2


# ─────────────────────────────────────────────────────────────
# 断板 × 情绪分层结局表 (实时重算)
# ─────────────────────────────────────────────────────────────
def test_death_signal_stratifies_by_break_day_regime():
    # 三个断板事件: d1 过热断→次日退潮; d3 冰点断→次日反弹; d5 中性断→次日走弱
    by_day = {
        'd0': {'X': 6}, 'd1': {'Y': 1}, 'd2': {'Z': 6}, 'd3': {'W': 1},
        'd4': {'V': 6}, 'd5': {'U': 1}, 'd6': {'T': 1},
    }
    real = ['d0', 'd1', 'd2', 'd3', 'd4', 'd5', 'd6']
    occ = {'d1': 0.70, 'd2': 0.50,   # 过热断, weakened (0.50<0.70)
           'd3': 0.30, 'd4': 0.45,   # 冰点断, 反弹 (0.45>0.30)
           'd5': 0.50, 'd6': 0.40}   # 中性断, weakened
    ctx = _ctx(by_day, real, occ, as_of='d6')
    ds = lt.build_leader_death_signal(ctx)

    tbl = {r['regime']: r for r in ds['table']}
    assert tbl['过热']['n'] == 1 and tbl['过热']['weaken_rate'] == 100
    assert tbl['过热']['ad_delta'] == pytest.approx(-0.20, abs=1e-6)
    assert tbl['冰点']['n'] == 1 and tbl['冰点']['weaken_rate'] == 0
    assert tbl['冰点']['ad_delta'] == pytest.approx(0.15, abs=1e-6)
    assert tbl['中性']['n'] == 1 and tbl['中性']['weaken_rate'] == 100
    assert ds['event_today'] is False  # d6 不是断板日


def test_death_signal_flags_event_today_and_regime():
    by_day = {'a': {'X': 6}, 'b': {'Y': 1}}
    ctx = _ctx(by_day, ['a', 'b'], {'b': 0.70}, as_of='b')
    ds = lt.build_leader_death_signal(ctx)
    assert ds['event_today'] is True
    assert ds['regime'] == '过热'
    assert '过热' in ds['action']


def test_death_signal_ignores_partial_break_when_high_board_survives():
    # 昨日两只高标, 今日仍有一只在池 → 未全员断板, 不计事件
    by_day = {'a': {'X': 6, 'Y': 7}, 'b': {'X': 5}}
    ctx = _ctx(by_day, ['a', 'b'], {'b': 0.70}, as_of='b')
    ds = lt.build_leader_death_signal(ctx)
    assert ds['event_today'] is False
    assert all(r['n'] == 0 for r in ds['table'])


# ─────────────────────────────────────────────────────────────
# stale 鬼影日跳过 (8/06=8/07 副本 → 8/07 不进 real_dates)
# ─────────────────────────────────────────────────────────────
def test_build_context_skips_stale_ghost_day(monkeypatch):
    codes = ['000011', '000012', '000013', '000014', '000015']
    rows = []
    for d, hs in (('20260805', 1), ('20260806', 2), ('20260807', 2)):
        for i, c in enumerate(codes):
            # 8/06 与 8/07 高度完全一致 → 8/07 判 stale; 8/05 不同
            h = hs + (i % 2)
            rows.append({'date': d, 'code': c, 'name': c, 'height': h})
    df = pd.DataFrame(rows)
    monkeypatch.setattr(lt, 'load_zt_cache', lambda *a, **k: df)
    monkeypatch.setattr(lt, 'load_sentiment_cache', lambda *a, **k: pd.DataFrame())

    ctx = lt._build_context()
    assert ctx['as_of'] == '20260807'
    assert '20260807' in ctx['stale']
    assert '20260807' not in ctx['real_dates']
    assert ctx['real_dates'] == ['20260805', '20260806']


# ─────────────────────────────────────────────────────────────
# 渲染: 完整 / 降级不出 None-nan
# ─────────────────────────────────────────────────────────────
def _full_result():
    return {
        'as_of': '20260820',
        'identity': {
            'space_leader': {'code': '603221', 'name': '爱丽家居', 'height': 10,
                             'first_board_date': '20260806', 'consec_days': 5,
                             'today_status': '10板(孤峰候选)', 'theme': '贵金属'},
            'popularity_leader': {'code': '600664', 'name': '哈药股份',
                                  'zt_count_20d': 8, 'height': 3, 'theme': '医药'},
            'top_cohort': [{'code': '603221', 'name': '爱丽家居', 'height': 10}],
        },
        'gravity': {
            'echelon': {'max_h': 10, 'n_at_max': 1, 'n_at_max_1': 0,
                        'n_at_max_2': 0, 'ladder': 4},
            'is_lonely_peak': True, 'lonely_peak_reason': '10板下方无承接, 空中楼阁。',
            'cluster': {'theme': '贵金属', 'count': 3,
                        'members': [{'code': '600547', 'name': '山东黄金', 'height': 3}]},
            'imitation': {'count': 1, 'members': [{'code': '000975', 'name': '银泰黄金', 'height': 2}]},
            'catchup': {'count': 1, 'members': [{'code': '601069', 'name': '西部黄金', 'pct': 6.3}],
                        'partial': True},
        },
        'death_signal': {
            'event_today': True, 'regime': '过热', 'ad_today': 0.72,
            'table': [{'regime': '过热', 'n': 7, 'weaken_rate': 86.0, 'ad_delta': -0.275},
                      {'regime': '中性', 'n': 8, 'weaken_rate': 38.0, 'ad_delta': 0.011},
                      {'regime': '冰点', 'n': 14, 'weaken_rate': 14.0, 'ad_delta': 0.315}],
            'action': '⚠️ 高标今日断板 + 盘面过热 → 历史同类次日 86% 概率退潮。',
        },
        'headline': '⚠️ 高标今日断板 + 盘面过热 → 历史同类次日 86% 概率退潮。',
    }


def test_render_full_has_all_blocks_and_no_none_nan():
    html = lt.render_leader_tracker_html(_full_result())
    for t in ('高标追踪', '爱丽家居', '孤峰', '抱团', '模仿盘', '补涨',
              '生死', '怎么操作', "class='lt-grid'", 'min-width:0', 'max-width:760px'):
        assert t in html, t
    assert 'None' not in html
    assert 'nan' not in html.lower()


def test_render_degrades_without_leader():
    result = {
        'as_of': '20260820',
        'identity': {'space_leader': None, 'popularity_leader': None, 'top_cohort': []},
        'gravity': lt._empty_gravity(),
        'death_signal': {'event_today': False, 'regime': None, 'ad_today': None,
                         'table': [], 'action': '今日无高标断板事件。'},
        'headline': '今日无连板高标, 情绪处冰点/空窗期。',
    }
    html = lt.render_leader_tracker_html(result)
    assert '高标追踪' in html
    assert "class='lt-grid'" not in html   # 无榜单不出空网格
    assert '今日无连板高标' in html
    assert 'None' not in html
    assert 'nan' not in html.lower()


def test_render_empty_input_returns_blank():
    assert lt.render_leader_tracker_html(None) == ''
    assert lt.render_leader_tracker_html({}) == ''
