from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from abnormal_action_research import candidate_from_rows, flat_scenarios, build_abnormal_research


def bars():
    return [{'date':f'2026-10-{i:02d}', 'open':10., 'high':11., 'low':9.8, 'close':10., 'volume':100.} for i in range(1, 31)]


def test_priority_is_not_triggered():
    rows=bars(); rows[-1].update(close=10.9)
    item={'code':'sz002724','name':'示例','box_eligible':True}
    c=candidate_from_rows(item, rows, '2026-11-02')
    assert c['tier']=='优先'
    assert c['trigger_verification'] == {'status': 'not_verified', 'confirmed': None}
    assert '未核验' in c['actual_status']
    assert c['entry_upper']>=c['entry_lower']
    assert c['risk_pct'] is None and c['upside_pct'] is None
    assert 'T+1' in c['holding']


def test_bad_historical_cohort_cannot_be_priority():
    rows=bars(); rows[-1].update(close=10.9)
    item={'code':'sh605488','name':'示例','box_eligible':True,'analog_n':6,'median10':-5.,'p25_10':-20.,'p75_10':10.}
    assert candidate_from_rows(item,rows,'2026-11-02')['tier']=='观察'


def test_broken_fantuo_is_not_relabelled_new_support_buy():
    item={'code':'sz301313','name':'凡拓数创','box_eligible':False}
    c=candidate_from_rows(item,bars(),'2026-11-02')
    assert c['tier']=='回避' and c['entry_zone'].startswith('无')
    assert '下移' in c['scenario_down']
    assert c['entry_lower'] is None and c['entry_upper'] is None
    assert c['stop_price'] is None and c['target_reference'] is None
    assert c['trigger_verification']['status'] == 'not_applicable'
    assert c['executable'] is False
    assert '仍不新开仓' in c['scenario_up']


@pytest.mark.parametrize('code', ['sz301313', 'sz300006'])
@pytest.mark.parametrize('as_of, plan_date', [('2026-11-02', '2026-11-03'),
                                           ('2026-10-30', '2026-11-03')])
def test_fixed_case_levels_cannot_roll_into_a_new_plan(code, as_of, plan_date):
    rows = bars()
    rows[-1]['date'] = as_of
    c = candidate_from_rows({'code': code, 'name': '案例', 'box_eligible': True}, rows, plan_date)
    assert c['tier'] == '观察'
    assert c['entry_lower'] is None and c['entry_upper'] is None
    assert c['stop_price'] is None and c['target_reference'] is None
    assert c['trigger_verification']['status'] == 'not_applicable'
    assert '适用期' in c['setup']
    active_text = ' '.join(str(c[key]) for key in ('entry_trigger', 'entry_zone', 'not_buy',
                                                  'scenario_up', 'scenario_flat', 'scenario_down'))
    assert not any(level in active_text for level in ('61.44', '62.01', '9.75', '10.48'))


def test_laimei_intraday_cancellation_is_not_reset_by_close_recovery():
    c = candidate_from_rows({'code': 'sz300006', 'name': '莱美药业'}, bars(), '2026-11-02')
    assert c['cancellation'] == {'rule_version': '2026-11-03', 'intraday_at_or_below': 9.75,
                                  'same_day_restart': False}
    assert '不能恢复' in c['scenario_down']
    assert c['trigger_verification']['status'] == 'not_verified'


def test_general_avoid_card_has_no_active_entry_or_stop():
    rows = bars()
    rows[-1]['close'] = 9.0
    c = candidate_from_rows({'code': 'sz000001', 'name': '测试股'}, rows, '2026-11-02')
    assert c['tier'] == '回避'
    assert all(c[key] is None for key in ('entry_lower', 'entry_upper', 'stop_price',
                                         'target_reference', 'upside_pct', 'risk_pct', 'rr'))
    assert c['trigger_verification']['status'] == 'not_applicable'
    assert '不执行' in c['entry_trigger']


def test_frozen_price_windows_and_missing_calendar():
    days=pd.bdate_range('2026-09-01',periods=31).strftime('%Y-%m-%d').tolist()
    p=pd.DataFrame({'date':days,'code':'sz300006','close_qfq':[10.]*21+[20.]*10})
    ix=pd.DataFrame({'date':days,'index_code':'sz399102','close':100.})
    r=flat_scenarios(p,ix,days,'sz300006',days[-1])
    assert [x['flat_days'] for x in r]==[0,1,2,3,5]
    assert r[0]['dev10']==100 and r[1]['dev10']==0
    assert r[-1]['dev30']==100
    assert flat_scenarios(p.drop(25),ix,days,'sz300006',days[-1])==[]


def test_stale_payload_rejected(tmp_path):
    folder=tmp_path/'output/abnormal_lianban_study';folder.mkdir(parents=True)
    (folder/'study_payload.json').write_text('{"meta":{"price_as_of":"2026-10-28"}}',encoding='utf-8')
    with pytest.raises(ValueError,match='日期'):
        build_abnormal_research(tmp_path,tmp_path/'out','2026-10-30','2026-11-02',False)
