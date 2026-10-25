"""Deterministic replay fixtures: no live fetches or fixed historical basket input."""
from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest

from crowding_tracking import build_dynamic_tracking, tracking_rules
from crowding_tracking_view import render_tracking_section, TRACKING_SCRIPT
from crowding_view import archive_tracking_snapshots, generate_crowding_html, generate_crowding_markdown
from crowding_cycle import build_crowding_report


def fixture(scores, proxies=None, missing=None, metric_missing=None, names=None, rules=None):
    dates = pd.bdate_range('2026-09-01', periods=len(scores)).strftime('%Y-%m-%d').tolist()
    codes = sorted(set().union(*(set(x) for x in scores)))
    mats = {key: pd.DataFrame(value, index=dates, columns=codes, dtype=float)
            for key, value in dict(price=12, ma5=11, r1=1, r5=10, r20=25, dd20=-3).items()}
    for index, symbol in metric_missing or []:
        mats['r5'].at[dates[index], symbol] = np.nan
    daily = [dict(date=d, coverage_ok=i not in (missing or []),
                  confirmed_proxy=proxies[i] if proxies else None,
                  proxy_state='过期' if i in (missing or []) else '候选竞争',
                  candidate_codes=list(s), candidate_scores=[dict(code=c, score=v) for c, v in s.items()],
                  top=list(s)[:6]) for i, (d, s) in enumerate(zip(dates, scores))]
    return build_dynamic_tracking(daily, mats, names or {}, window_start=dates[0], requested_date=dates[-1], rules=rules), daily, mats


def row(r, day, symbol='A'):
    return next(x for x in r['daily'][day]['rows'] if x['code'] == symbol)


def test_admission_strengthening_and_boundary_identity():
    r, _, _ = fixture([{'A': 90}] * 5)
    assert row(r, 0)['status'] == '存量待验证'
    assert not row(r, 0)['active']
    assert row(r, 1)['active'] and row(r, 1)['status'] == '存量跟踪'
    assert row(r, 4)['status'] == '存量强化'
    assert row(r, 4)['pool'] == 'boundary'
    assert row(r, 0)['first_seen_cycle_id'] == r['cycles'][0]['id']
    assert row(r, 0)['matched_cycle_id'] == r['cycles'][0]['id']


def test_new_old_rotation_and_recovery():
    r, _, _ = fixture([{}, {'A': 90}, {'A': 90}, {'A': 90}, {}, {}, {'A': 90}, {'A': 90}],
                      proxies=[None, 'X', 'X', 'Y', 'Y', 'Y', 'Y', 'Y'])
    assert row(r, 1)['status'] == '新生候选'
    assert row(r, 2)['status'] == '新团跟踪'
    assert row(r, 3)['pool'] == 'old' and row(r, 3)['status'] == '老团延续'
    assert row(r, 4)['status'] == '分歧观察'
    assert row(r, 5)['status'] == '已退出'
    assert row(r, 6)['status'] == '旧团修复待验证' and row(r, 6)['pool'] == 'old'
    assert row(r, 7)['status'] == '旧团修复'
    stock = r['stocks'][0]
    assert len(stock['episodes']) == 2 and stock['episodes'][1]['recovery']
    assert stock['episodes'][0]['exited_on'] == r['daily'][5]['date']
    assert row(r, 2)['first_cycle_id'] != row(r, 3)['matched_cycle_id']


def test_all_candidates_and_ties_not_page_top_six():
    scores = {f'C{i:03}': 100 - i for i in range(100)}
    scores['C010'] = scores['C009']
    r, _, _ = fixture([scores, scores])
    assert r['daily'][0]['candidate_count'] == 100
    assert r['daily'][0]['qualified_count'] == 11
    assert row(r, 1, 'C010')['active']
    assert all(s['code'] != 'C011' for s in r['stocks'])


@pytest.mark.parametrize('missing_type', ['cross_section', 'member'])
def test_missing_breaks_confirmation_without_false_exit(missing_type):
    options = dict(missing=[1, 4]) if missing_type == 'cross_section' else dict(metric_missing=[(1, 'A'), (4, 'A')])
    r, _, _ = fixture([{'A': 90}] * 6, **options)
    assert row(r, 1)['status'] == '数据不足' and row(r, 1)['metrics_as_of'] == r['daily'][0]['date']
    assert not row(r, 2)['active'] and row(r, 3)['active']
    assert row(r, 4)['active'] and row(r, 4)['failed_streak'] == 0
    assert row(r, 5)['support_streak'] == 1
    assert r['daily'][1]['qualified_count'] == 0


def test_unknown_core_keeps_previous_cycle():
    r, daily, mats = fixture([{'A': 90}] * 4, proxies=['X', 'X', None, 'X'])
    daily[2]['proxy_state'] = '过期'
    r = build_dynamic_tracking(daily, mats, {}, window_start=daily[0]['date'], requested_date=daily[-1]['date'])
    assert len(r['cycles']) == 1
    assert r['daily'][2]['cycle_id'] == r['daily'][1]['cycle_id']


def test_failed_candidate_remains_in_history_and_future_cannot_rewrite_daily():
    r, daily, mats = fixture([{}, {'A': 90}, {}, {}, {'A': 90}, {'A': 90}])
    assert row(r, 2)['status'] == '候选未通过'
    past = build_dynamic_tracking(daily[:3], {k: v.iloc[:3] for k, v in mats.items()}, {},
                                  window_start=daily[0]['date'], requested_date=daily[2]['date'])
    assert past['daily'] == r['daily'][:3]
    assert row(r, 1)['first_admitted'] is None
    assert row(r, 5)['first_admitted'] is not None


def test_custom_rules_change_membership_and_version():
    scores = {'A': 100, 'B': 90}
    first, _, _ = fixture([scores])
    other, _, _ = fixture([scores], rules=dict(top_fraction=1, admission_days=1))
    assert len(first['stocks']) == 1 and len(other['stocks']) == 2
    assert all(x['active'] for x in other['daily'][0]['rows'])
    assert first['rule_id'] != other['rule_id']


@pytest.mark.parametrize('rules', [[], {'codes': ['A']}, {'admission_days': True}, {'exit_days': 0},
                                   {'top_fraction': 0}, {'top_fraction': float('nan')},
                                   {'max_drawdown_pct': 101}, {'strengthening_days': 1}])
def test_invalid_rules_rejected(rules):
    with pytest.raises(ValueError):
        tracking_rules(rules)


def test_html_escape_and_empty_state():
    r, _, _ = fixture([{'A': 90}], names={'A': '<script>bad</script>'})
    section = render_tracking_section(r)
    assert '&lt;script&gt;bad&lt;/script&gt;' in section
    assert '<script>bad</script>' not in section
    assert all(f'id="tracking-{x}"' in section for x in ('date', 'pool', 'cycle', 'search', 'rows'))
    empty = deepcopy(r); empty['daily'] = []; empty['stocks'] = []; empty['cycles'] = []
    assert '无满足跟踪条件' in render_tracking_section(empty)
    assert 'e.date<=snapshot.date' in TRACKING_SCRIPT
    assert 'tr.dataset.status=r.status' in TRACKING_SCRIPT


def test_tracking_archive_idempotency_collision_and_revision(tmp_path):
    tracking, _, _ = fixture([{'A': 90}, {'A': 90}])
    report = dict(tracking=tracking, requested_date=tracking['requested_date'], sources_as_of={})
    root = archive_tracking_snapshots(report, tmp_path)
    record = tracking['daily'][0]
    target = root / record['date'] / f'{record["version"]}.json'
    before = target.read_bytes()
    saved = json.loads(before)
    assert saved['known_at'] is None and saved['observed_at'].endswith('+08:00')
    archive_tracking_snapshots(report, tmp_path)
    assert target.read_bytes() == before
    record['qualified_count'] = 999
    with pytest.raises(ValueError, match='拒绝覆盖'):
        archive_tracking_snapshots(report, tmp_path)
    assert target.read_bytes() == before
    revised, _, _ = fixture([{'A': 90}], rules={'admission_days': 1})
    report['tracking'] = revised
    archive_tracking_snapshots(report, tmp_path)
    assert len(list(target.parent.glob('*.json'))) == 2


def test_report_integration_dynamic_paths_markdown_and_no_fixed_members():
    dates = pd.bdate_range('2026-07-01', '2026-10-08').strftime('%Y-%m-%d').tolist()
    prices = pd.DataFrame([dict(date=d, code=c, close_qfq=10 * (1 + g)**i)
                          for i, d in enumerate(dates) for c, g in [('sh600001', .02), ('sz000002', .001)]])
    report = build_crowding_report('2026-10-08', prices=prices, calendar=dates, limits=pd.DataFrame(),
                                   sentiment=pd.DataFrame(), quotes={}, names={'sh600001': '动态甲'})
    codes = {x['code'] for x in report['tracking']['stocks']}
    assert codes == {'sh600001'}
    assert report['paths']['sh600001'] and all(x['date'] <= report['requested_date'] for x in report['paths']['sh600001'])
    assert '动态新老抱团追踪' in generate_crowding_html(report)
    markdown = generate_crowding_markdown(report)
    assert '动态新老抱团追踪' in markdown and '动态甲' in markdown


def test_drawdown_uses_raw_precision_not_display_rounding():
    r, daily, mats = fixture([{'A': 90}, {'A': 90}])
    mats['dd20'].loc[:, 'A'] = -15.004
    r = build_dynamic_tracking(daily, mats, {}, window_start=daily[0]['date'], requested_date=daily[-1]['date'])
    assert r['stocks'] == []


def test_missing_window_start_does_not_claim_first_observed_stock_is_new():
    r, _, _ = fixture([{'A': 90}] * 3, missing=[0])
    assert row(r, 1)['pool'] == 'boundary'
    assert r['cycles'][0]['left_censored']


def test_missing_keeps_last_known_status_but_not_today_qualification():
    r, _, _ = fixture([{}, {'A': 90}, {'A': 90}, {'A': 90}], missing=[3])
    assert row(r, 3)['status'] == '数据不足'
    assert row(r, 3)['last_known_status'] == '新团跟踪'
    assert not row(r, 3)['qualified_today']
    assert row(r, 3)['metrics_as_of'] == r['daily'][2]['date']


def test_old_recovery_strengthening_is_visible():
    r, _, _ = fixture([{}, {'A': 90}, {'A': 90}, {}, {}] + [{'A': 90}] * 5)
    assert row(r, -1)['status'] == '旧团修复强化'
    assert row(r, -1)['pool'] == 'old'


def test_ma5_and_drawdown_failure_revoke_after_two_days():
    r, daily, mats = fixture([{}, {'A': 90}, {'A': 90}, {'A': 90}, {'A': 90}])
    mats['ma5'].iloc[3:, :] = 15
    mats['dd20'].iloc[3:, :] = -16
    r = build_dynamic_tracking(daily, mats, {}, window_start=daily[0]['date'], requested_date=daily[-1]['date'])
    assert row(r, 3)['status'] == '分歧观察'
    assert row(r, 4)['status'] == '已退出'
    assert 'MA5' in row(r, 4)['reason'] and '回撤超限' in row(r, 4)['reason']


def test_default_view_excludes_failed_history_and_never_uses_future_chart():
    r, _, _ = fixture([{}, {'A': 90}, {}, {}])
    section = render_tracking_section(r)
    assert '<option value="watch">' in section
    assert 'A / A' not in section
    assert 'draw(snapshot.date)' in TRACKING_SCRIPT
    assert "c.start>snapshot.date" in TRACKING_SCRIPT


def test_competition_and_first_confirmation_are_same_cycle_not_false_old():
    r, _, _ = fixture([{}, {'A': 90}, {'A': 90}, {'A': 90}], proxies=[None, None, None, 'A'])
    assert len(r['cycles']) == 1
    assert row(r, 2)['first_cycle_id'] == row(r, 3)['matched_cycle_id']
    assert row(r, 3)['pool'] == 'new' and row(r, 3)['status'] == '新团跟踪'
    assert r['cycles'][0]['confirmed_on'] == r['daily'][3]['date']
    assert r['daily'][2]['cycle_phase'] == '候选竞争'
    assert r['daily'][3]['cycle_phase'] == '核心价格确认'


def test_future_confirmation_does_not_change_past_cycle_ids_or_pool():
    r, daily, mats = fixture([{}, {'A': 90}, {'A': 90}, {'A': 90}], proxies=[None, None, None, 'A'])
    past = build_dynamic_tracking(daily[:3], mats, {}, window_start=daily[0]['date'], requested_date=daily[2]['date'])
    assert past['daily'] == r['daily'][:3]
    assert past['cycles'][0]['anchor_code'] is None
    assert r['cycles'][0]['anchor_code'] == 'A'
