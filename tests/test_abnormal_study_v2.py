import numpy as np
import pandas as pd

from abnormal_study_v2 import (board_of, box_features, build_events_and_landmarks,
                               choose_analogs, feature_at, outcome_at, make_current, prepare_prices, build_crossing_events)


def path(prices=None):
    prices = prices or [10, 11, 12, 13, 14, 15, 16, 18, 20, 20.2, 20.3, 20.4, 20.5] + [17] * 14
    n = len(prices)
    return pd.DataFrame({'code': ['sz300001'] * n, 'name': ['测试'] * n, 'board': ['创业板'] * n,
                         'date_idx': range(n), 'date': pd.bdate_range('2026-01-01', periods=n),
                         'close_used': prices, 'close_raw': prices, 'index_close': [100] * n,
                         'is_zt': [0] * 7 + [1] + [0] * (n-8),
                         'entry': [False] * 8 + [True] + [False] * (n-9),
                         'dev10_pct': [85] * n, 'dev30_pct': [90] * n, 'kind': ['预警'] * n, 'window': [10] * n})


def test_feature_prefix_invariance_and_pause_not_reset_by_daily_alert():
    p = path()
    assert feature_at(p, 10) == feature_at(p[p.date_idx <= 10], 10)
    assert feature_at(p, 10)['gap'] == 3
    assert box_features(p, 8, 3) == box_features(p[p.date_idx <= 10], 8, 3)


def test_losing_consolidations_are_kept_and_return_starts_at_landmark():
    p = path()
    _, b, _ = build_events_and_landmarks(p)
    assert set(b.days) == {2, 3, 5}
    assert b.return10.lt(0).all()
    two = b[b.days.eq(2)].iloc[0]
    assert two.landmark_idx == 9
    assert round(two.return10, 6) == round((17/20.2 - 1)*100, 6)
    assert not b.duplicated(['code', 'episode_id', 'days']).any()


def test_missing_unknown_pause_cannot_be_consolidation():
    p = path()
    p.loc[9, 'is_zt'] = np.nan
    assert not box_features(p, 8, 3)['eligible']
    p = path().drop(index=9)
    assert not box_features(p, 8, 3)['eligible']
    assert outcome_at(p, 8, 5)['return'] is None


def test_newhigh_reference_includes_event_day_and_partial_return_is_unknown():
    p = path()
    assert not outcome_at(p, 12, 5)['newhigh']
    assert outcome_at(p.head(16), 12, 10)['return'] is None


def test_matching_excludes_self_immature_different_k_and_duplicates():
    dates = pd.to_datetime(['2026-01-10', '2026-01-11', '2026-02-10', '2026-01-10', '2026-01-10'])
    a = pd.DataFrame({'code': ['other', 'other', 'immature', 'self', 'wrong_k'], 'board': ['创业板']*5,
                      'days': [3, 3, 3, 3, 5], 'window': [10]*5, 'end10': dates,
                      'landmark_date': dates-pd.Timedelta(days=15), 'dev10': [85]*5, 'range_pct': [4]*5})
    cur = {'code': 'self', 'board': '创业板', 'window': 10, 'box_days': 3, 'box_eligible': True,
           'dev10': 85, 'range_pct': 5}
    got = choose_analogs(a, cur, '2026-01-31')
    assert got.code.tolist() == ['other']
    assert choose_analogs(a, {**cur, 'box_days': 4}, '2026-01-31').empty


def test_301_is_chinext():
    assert board_of('sz301313') == '创业板'


def test_first_warning_today_does_not_count_prior_pause_days():
    p = path().head(14).copy()
    p['entry'] = False
    p.loc[13, 'entry'] = True
    p['signal'] = True
    e, b, books = build_events_and_landmarks(p)
    cur, _ = make_current(books, e, b, {'alerts': []}, str(p.iloc[-1].date)[:10], [])
    assert cur[0]['box_days'] == 0
    assert cur[0]['nonlimit_days'] == 1
    assert cur[0]['analog_n'] == 0


def test_later_missing_qfq_cannot_change_previous_events():
    dates = pd.bdate_range('2026-01-01', periods=14)
    p = pd.DataFrame({'code': ['sz300001'] * 14, 'date': dates,
                       'close_raw': [10]*5+[5]*4+[7, 8, 9, 10, 11],
                       'close_qfq': [5]*9+[7, 8, 9, 10, np.nan]})
    ix = pd.DataFrame({'date': dates, 'index_code': ['sz399102']*14, 'index_close': [100]*14})
    m = pd.DataFrame({'code': ['sz300001'], 'name': ['测试']})
    z = pd.DataFrame(columns=['code', 'date'])
    old = prepare_prices(p.head(13), m, ix, z, dates).iloc[-1]
    new = prepare_prices(p, m, ix, z, dates).iloc[-2]
    assert old.dev10_pct == new.dev10_pct and old.signal == new.signal


def test_crossing_clock_starts_at_crossing_not_earlier_warning():
    p = path()
    p['signal'] = p.date_idx.ge(8)
    p['crossed'] = p.date_idx.ge(10)
    x = build_crossing_events({'sz300001': p})
    assert len(x) == 1
    assert x.iloc[0].event_date == p.loc[10, 'date']
    assert round(x.iloc[0].return5, 5) == round((17/20.3 - 1)*100, 5)
