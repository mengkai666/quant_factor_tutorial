"""Causal rule selection and full-universe stock audit regression tests."""
import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crowding_cycle import _scan, price_matrices
from crowding_replay import (_outcome, audit_stock, build_screening_replay,
                             choose_rule, hypothesis_grid)
from crowding_replay_view import render_replay_section
from crowding_view import archive_replay_snapshots


def market():
    dates = pd.bdate_range('2026-02-02', '2026-10-23').strftime('%Y-%m-%d').tolist()
    prices = pd.DataFrame([
        dict(date=d, code=c, close=10 * (1 + growth)**i)
        for c, growth in [('A', .016), ('B', .016), ('C', -.001)] + [(f'D{n}', -.002) for n in range(17)]
        for i, d in enumerate(dates)])
    mats = price_matrices(prices, dates)
    daily, _ = _scan(mats, dates, {'A': '甲', 'B': '乙', 'C': '未入选'}, {}, prices.groupby('date').code.nunique())
    return dates, mats, daily


@pytest.fixture(scope='module')
def replay():
    _, mats, daily = market()
    return build_screening_replay(daily, mats, {'A': '甲', 'B': '乙', 'C': '未入选'}, requested_date='2026-10-24')


def event(date, known_on, success):
    return dict(date=date, known_on=known_on, success=success)


def test_training_cannot_read_today_or_future_labels():
    baseline = [event('2026-08-01', '2026-08-07', False)]
    better = [event('2026-08-02', '2026-08-10', True), event('2026-08-03', '2026-08-11', True)]
    result = choose_rule({'baseline': baseline, 'earlier': better}, '2026-08-10', min_samples=1, min_entry_days=1)
    assert result['rule_id'] == 'baseline'
    assert result['comparisons']['earlier']['samples'] == 0
    assert choose_rule({'baseline': baseline, 'earlier': better}, '2026-08-11', min_samples=1, min_entry_days=1)['rule_id'] == 'earlier'


def test_minimum_distinct_entry_days_prevents_single_day_overfitting():
    events = [event('2026-08-01', '2026-08-07', True)] * 100
    assert choose_rule({'baseline': [], 'earlier': events}, '2026-09-01')['rule_id'] == 'baseline'


@pytest.mark.parametrize('values,expected', [([10,11,12,13,14,15], True), ([10,11,12,13,14,9], False), ([10,8,12,13,14,15], False)])
def test_label_is_after_entry_and_checks_adverse_excursion(values, expected):
    result = _outcome(np.array(values, dtype=float), 0, list('abcdef'), [True]*6)
    assert result['success'] is expected
    assert result['known_on'] == 'f'


def test_label_pending_and_missing_are_not_failures():
    assert _outcome(np.ones(5), 0, list('abcde'), [True]*5)['success'] is None
    assert _outcome(np.ones(6), 0, list('abcdef'), [True,True,False,True,True,True])['success'] is None
    assert _outcome(np.array([10,11,np.nan,12,13,14]), 0, list('abcdef'), [True]*6)['known_on'] is None


def test_two_and_six_months_with_prior_indicator_warmup(replay):
    assert replay['windows']['2']['requested_start'] == '2026-08-24'
    assert replay['windows']['6']['requested_start'] == '2026-04-24'
    assert not replay['windows']['6']['missing_prefix']
    assert replay['dates'][0] < '2026-04-24'
    assert replay['windows']['6']['observations'] > replay['windows']['2']['observations']


def test_all_tied_candidates_survive_and_audit_gates_agree(replay):
    for w in replay['windows'].values():
        snap = w['days'][-1]
        assert set(snap['selected']) == {'A','B'}  # never truncate to 10% by code
        for code in snap['selected']:
            result = audit_stock(replay, w['months'], snap['date'], code)
            assert result['selected']
            assert all(g['passed'] for g in result['gates'])


def test_arbitrary_never_selected_stock_has_negative_evidence(replay):
    w = replay['windows']['6']; date = w['days'][-1]['date']
    result = audit_stock(replay, 6, date, 'C')
    assert not result['selected']
    assert result['status'] == '当日未选中'
    assert next(g for g in result['gates'] if g['label']=='5日收益门槛')['passed'] is False
    assert audit_stock(replay, 6, date, 'NOTINUNIVERSE')['status'] == '未覆盖代码'


def test_missing_price_is_unknown_not_repaired_with_future(replay):
    r = copy.deepcopy(replay);date=r['windows']['6']['days'][-1]['date']
    r['prices']['C'][-2] = None
    result = audit_stock(r, 6, date, 'C')
    assert result['status'] == '数据不足 / 不能确认'
    assert all(g['passed'] is None for g in result['gates'][2:])


def test_future_price_mutation_cannot_change_prior_daily_policy():
    dates, mats, daily = market()
    first = build_screening_replay(daily, mats, {}, requested_date='2026-10-24', min_samples=1, min_entry_days=1)
    changed = {k:v.copy() for k,v in mats.items()}
    for k,v in changed.items():
        if k != 'price':
            v.loc[v.index>'2026-09-01'] = np.nan
        else:
            v.loc[v.index>'2026-09-01'] *= .1
    second = build_screening_replay(daily, changed, {}, requested_date='2026-10-24', min_samples=1, min_entry_days=1)
    for months in ('2','6'):
        a=[d for d in first['windows'][months]['days'] if d['date']<='2026-09-01']
        b=[d for d in second['windows'][months]['days'] if d['date']<='2026-09-01']
        assert a==b


def test_frozen_training_purges_labels_maturing_during_holdout(replay):
    for w in replay['windows'].values():
        for v in w['validation'].values():
            p=v['policy'];assert p['trained_before']==w['holdout_start']
            assert p['latest_label_date'] is None or p['latest_label_date']<w['holdout_start']
            assert all(s['latest_label_date'] is None or s['latest_label_date']<w['holdout_start'] for s in p['comparisons'].values())


def test_immutable_snapshots_do_not_archive_future_prices_or_labels(replay,tmp_path):
    root=archive_replay_snapshots({'screening_replay':replay},tmp_path)
    files=list(root.rglob('*.json'));assert files
    contents={f:f.read_bytes() for f in files}
    archive_replay_snapshots({'screening_replay':replay},tmp_path)
    assert all(f.read_bytes()==content for f,content in contents.items())
    record=json.loads(files[0].read_text(encoding='utf-8'))
    assert 'prices' not in record and 'events' not in record
    assert record['knowledge_status']=='reconstructed_from_current_cache' and record['known_at'] is None


def test_replay_ui_empty_and_safe_names(replay):
    assert '暂不能验选' in render_replay_section(None)
    r=copy.deepcopy(replay);r['names']['A']='</td><script>evil</script>'
    markup=render_replay_section(r)
    assert '<script>evil</script>' not in markup
    assert '&lt;script&gt;evil&lt;/script&gt;' in markup
    assert all(k in markup for k in ('replay-window','replay-prev','replay-first','replay-stock','replay-history'))


def test_left_boundary_not_mislabeled_newborn(replay):
    for w in replay['windows'].values():
        assert w['days'][0]['pools']['A']=='boundary'
        assert w['days'][0]['pools']['B']=='boundary'


def test_grid_and_sample_validation():
    assert len(hypothesis_grid())==8
    assert hypothesis_grid({'admission_days':3})[0]['rules']['admission_days']==3
    with pytest.raises(ValueError):
        build_screening_replay([],None,{},requested_date='2026-10-24',min_samples=0)


def test_short_history_does_not_claim_full_halfyear():
    dates,mats,daily=market()
    mats={k:v.loc[v.index>='2026-09-01'] for k,v in mats.items()}
    short=build_screening_replay([d for d in daily if d['date']>='2026-09-01'],mats,{},requested_date='2026-10-24')
    assert short['windows']['6']['missing_prefix']


def test_all_candidates_not_just_six_displayed_leaders():
    dates = pd.bdate_range('2026-02-02','2026-10-23').strftime('%Y-%m-%d').tolist()
    prices = pd.DataFrame([dict(date=d,code=f'S{c}',close=10*(1+(.016 if c<8 else -.001))**i)
                           for c in range(50) for i,d in enumerate(dates)])
    mats=price_matrices(prices,dates)
    daily,_=_scan(mats,dates,{}, {},prices.groupby('date').code.nunique())
    assert len(daily[-1]['top'])==6
    replay=build_screening_replay(daily,mats,{},requested_date='2026-10-24')
    assert len(replay['windows']['6']['days'][-1]['selected'])==8


def test_flat_price_ma5_float_boundary_is_not_false_failure(replay):
    r=copy.deepcopy(replay);date=r['windows']['6']['days'][-1]['date']
    r['prices']['A'][-5:]=[26.38]*5
    a=audit_stock(r,6,date,'A')
    assert next(g for g in a['gates'] if g['label']=='收盘不低于MA5')['passed'] is True


def test_global_missing_day_prohibits_selection():
    _, mats, daily=market()
    target='2026-09-01'
    for d in daily:
        if d['date']==target:
            d['coverage_ok']=False
    r=build_screening_replay(daily,mats,{},requested_date='2026-10-24')
    for w in r['windows'].values():
        d=next(d for d in w['days'] if d['date']==target)
        assert not d['selected']
        a=audit_stock(r,w['months'],target,'A')
        assert a['status']=='数据不足 / 不能确认'
        assert all(g['passed'] is None for g in a['gates'][2:])
