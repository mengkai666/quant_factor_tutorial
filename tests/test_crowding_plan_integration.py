import json
from pathlib import Path

import pytest
import crowding_next_day_plan as ndp


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    import crowding_plan_data
    import cb_plan_data
    monkeypatch.setattr(ndp, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(ndp, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(ndp, 'score_previous_plan', lambda *args: None)
    monkeypatch.setattr(ndp, '_broken_leaders', lambda *args: [])
    monkeypatch.setattr(ndp, '_ai_beacons', lambda *args: [])
    monkeypatch.setattr(ndp, 'PREDICTION_HISTORY', str(tmp_path / 'predictions.jsonl'))
    monkeypatch.setattr(crowding_plan_data, 'build_crowding_plan_data', lambda *a, **k: {'stocks': {}, 'sectors': {}})
    monkeypatch.setattr(cb_plan_data, 'load_cb_plan_data', lambda *a, **k: {})
    return tmp_path


def test_unverified_readiness_blocks_even_a_positive_decision(isolated):
    plan = ndp.build_next_day_plan({
        'report_date': '2026-11-05', 'target_trade_date': '2026-11-06',
        'today_decision': {'execution_allowed': True, 'position': '4 成', 'default_action': '可开仓'},
        'decision_readiness': {'plan_permitted': False, 'execution_ready': False},
    })
    assert plan['decision']['execution_allowed'] is False
    html = ndp.generate_plan_html(plan)
    assert '当前不开新仓' in html and '4 成' not in html and '可开仓' not in html


def test_next_session_requires_cached_calendar_not_weekday_guess(isolated):
    ctx = {'report_date': '2026-11-05'}
    assert ndp.build_next_day_plan(ctx)['target_date'] == ''
    (isolated / 'trading_calendar_cache.csv').write_text('trade_date\n2026-11-05\n2026-11-06\n', encoding='utf-8')
    assert ndp.build_next_day_plan(ctx)['target_date'] == '2026-11-06'


def test_market_zero_is_not_replaced_by_snapshot(isolated):
    snapshots = isolated / 'report_daily_snapshots'
    snapshots.mkdir()
    (snapshots / '2026-11-05.json').write_text(json.dumps({'limit_up': 84, 'limit_down': 2}), encoding='utf-8')
    plan = ndp.build_next_day_plan({'report_date': '2026-11-05',
        'market_thesis': {'breadth_relay_state': {'limit_up': 0, 'limit_down': 0}}})
    assert plan['facts']['limit_up'] == 0
    assert plan['facts']['limit_down'] == 0


def test_audit_context_wins_and_invalid_audit_does_not_fall_back(isolated):
    (isolated / 'audit').mkdir()
    audit = isolated / 'audit' / '2026-11-05.json'
    audit.write_text(json.dumps({'context': {'report_date':'2026-11-05', 'decision_readiness':{'plan_permitted':False}}}),encoding='utf-8')
    Path(ndp.PREDICTION_HISTORY).write_text(json.dumps({'event_type':'prediction','report_date':'2026-11-05','today_decision':{'execution_allowed':True}}),encoding='utf-8')
    assert ndp.load_plan_context('20261105')['decision_readiness']['plan_permitted'] is False
    audit.write_text('{invalid',encoding='utf-8')
    assert ndp.load_plan_context('2026-11-05') == {'report_date':'2026-11-05'}


def test_historical_export_preserves_latest_and_dates_match(isolated):
    for day,target in [('2026-11-05','2026-11-06'),('2026-11-04','2026-11-05')]:
        plan=ndp.build_next_day_plan({'report_date':day,'target_trade_date':target})
        result=ndp.export_plan_standalone_reports(day,output_dir=str(isolated),plan_data=plan)
    assert result['latest_updated'] is False
    assert 'data-report-date="2026-11-05"' in Path(result['local_latest']).read_text(encoding='utf-8')
    assert 'data-report-date="2026-11-04"' in Path(result['local_dated']).read_text(encoding='utf-8')
    with pytest.raises(ValueError,match='日期不一致'):
        ndp.export_plan_standalone_reports('2026-11-03',output_dir=str(isolated),plan_data=plan)


def test_research_never_becomes_eligible_candidate_and_escapes(isolated):
    plan=ndp.build_next_day_plan({'report_date':'2026-11-05','target_trade_date':'2026-11-06',
        'today_decision':{'candidates':[
            {'name':'非主线股','candidate_eligible':True,'scenario_candidate_eligible':True,'mainline_match':False},
            {'name':'有效候选','candidate_eligible':True,'scenario_candidate_eligible':True,'mainline_match':True,'trigger':'<script>x</script>'},
        ]}})
    plan['crowding']['stocks']={'status':'partial','as_of':'2026-11-05','rows':[
        {'name':'<b>研究股</b>','role':'core','close':12.3,'r1':None,'watch':'等待','invalidation':'转弱'}]}
    html=ndp.generate_plan_html(plan)
    assert '有效候选' in html and '非主线股' not in html
    assert '<b>研究股</b>' not in html and '&lt;b&gt;研究股&lt;/b&gt;' in html
    assert '<script>x</script>' not in html and '&lt;script&gt;x&lt;/script&gt;' in html
    assert '部分字段待补' in html and '仅观察，未获开仓许可' in html
    assert '稳健底仓' not in html and '违规持债过夜' not in html


def test_shared_panel_and_standalone_have_identical_evidence(isolated):
    plan=ndp.build_next_day_plan({'report_date':'2026-11-05','target_trade_date':'2026-11-06'})
    panel=ndp.render_plan_panel_html(plan,is_site_mode=False)
    standalone=ndp.generate_plan_html(plan,is_site_mode=False)
    inner=panel.removeprefix('<!-- crowding-plan-panel:start -->').removesuffix('<!-- crowding-plan-panel:end -->')
    assert inner in standalone
    for label in ['抱团板块','抱团个股','抱团转债','9:25','9:35','10:00','午后']:
        assert label in inner


@pytest.mark.parametrize('readiness', [{}, {'execution_ready':True,'plan_permitted':True,'report_date':'2026-11-04','target_trade_date':'2026-11-06'}])
def test_missing_or_mismatched_permission_date_is_blocked(isolated,readiness):
    p=ndp.build_next_day_plan({'report_date':'2026-11-05','target_trade_date':'2026-11-06',
        'today_decision':{'report_date':'2026-11-05','execution_allowed':True},'decision_readiness':readiness})
    assert not p['decision']['execution_allowed']


def test_wrong_day_snapshot_cannot_fill_market_facts(isolated):
    d=isolated/'report_daily_snapshots';d.mkdir()
    (d/'2026-11-05.json').write_text(json.dumps({'report_date':'2026-11-06','limit_up':999}),encoding='utf-8')
    p=ndp.build_next_day_plan({'report_date':'2026-11-05'})
    assert p['facts']['limit_up'] is None


def test_legacy_latest_meta_date_precedes_target_date_in_title(isolated):
    from plan_page_io import can_update_latest
    latest=isolated/'latest.html'
    latest.write_text('<title>明日预案 2026-11-06</title><meta name="report-date" content="2026-11-05">',encoding='utf-8')
    assert can_update_latest(latest,'2026-11-05',[])
    assert not can_update_latest(latest,'2026-11-04',[])


def test_scorecard_rejects_future_rows_inside_current_slice(isolated,monkeypatch):
    import pandas as pd
    monkeypatch.setattr(ndp,'PRICE_SLICE_DIR',str(isolated))
    pd.DataFrame([{'date':'2026-11-06','code':'sz000001','close_raw':100}]).to_csv(isolated/'2026-11-05.csv.gz',index=False,compression='gzip')
    assert ndp._closes(None,'2026-11-05') == {}


def test_missing_report_date_is_rejected(isolated):
    with pytest.raises(ValueError,match='有效的收盘报告日期'):
        ndp.build_next_day_plan({'target_trade_date':'2026-11-06'})
