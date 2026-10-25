from pathlib import Path
import json

import numpy as np
import pandas as pd
import pytest

from crowding_cycle import (_scan, assess_cohort, build_crowding_report, normalize_prices,
                            price_matrices, select_member_series, stock_stats)
from crowding_view import archive_candidate_snapshots, export_crowding_reports, generate_crowding_html


def inputs(end='2026-10-08'):
    dates = pd.bdate_range('2026-07-01', end).strftime('%Y-%m-%d').tolist()
    p = pd.DataFrame([dict(date=d, code=c, close_qfq=10*(1+growth)**i) for i,d in enumerate(dates)
                      for c,growth in [('sh600127',.016),('sh601086',.011),('sh600865',-.001)]])
    sentiment = pd.DataFrame([dict(日期=d.replace('-',''),up=3000,down=2000) for d in dates])
    return dict(prices=p, limits=pd.DataFrame(), calendar=dates, sentiment=sentiment,
                quotes={}, names={'sh600127':'测试甲','sh601086':'测试乙'})


def test_future_rows_cannot_change_past_report():
    args = inputs()
    first = build_crowding_report('2026-09-21',**args)
    future = args['prices'].copy()
    future.loc[future.date > '2026-09-21','close_qfq'] = 10000
    second = build_crowding_report('2026-09-21',**{**args,'prices':future})
    assert first == second
    assert first['groups'] == []  # 截图在10/8才提供，不能提前偷看。
    assert all(x['date'] <= '2026-09-21' for x in first['daily'])


def test_raw_prices_never_fill_qfq_gaps():
    p = pd.DataFrame([dict(date='2026-09-01',code='600127',close_raw=10,close_qfq=np.nan)])
    assert normalize_prices(p,'2026-10-08').empty


def test_calendar_does_not_count_holidays_as_stale():
    args = inputs('2026-09-30')
    report = build_crowding_report('2026-10-07',**args)
    assert report['lag_trading_days'] == 0
    args['calendar'].append('2026-10-08')
    report = build_crowding_report('2026-10-08',**args)
    assert report['lag_trading_days'] == 1
    assert report['market_as_of'] == '2026-09-30'


def test_partial_latest_cross_section_cannot_advance_market_clock():
    args=inputs('2026-09-30'); args['calendar'].append('2026-10-08')
    args['prices']=pd.concat([args['prices'],pd.DataFrame([dict(date='2026-10-08',code='sh600127',close_qfq=20)])])
    r=build_crowding_report('2026-10-08',**args)
    assert r['market_as_of']=='2026-09-30'
    assert r['quality']=='stale'


def test_supplemental_quotes_never_upgrade_market_or_execution():
    args=inputs('2026-09-30'); args['calendar'].append('2026-10-08')
    args['quotes']={'stocks':{'sh600241':{'basis':'qfq','rows':[{'date':d,'close':10+i} for i,d in enumerate(args['calendar'])]}}}
    r=build_crowding_report('2026-10-08',**args)
    assert r['sources_as_of']['reference_quotes']=='2026-10-08'
    assert r['market_as_of']=='2026-09-30'
    assert r['execution']['status']=='not_qualified'
    assert next(g for g in r['groups'] if g['id']=='trial')['assessment']['stage']=='试错待确认'


def test_missing_member_blocks_clearance_in_fixed_cohort():
    strong=dict(r1=5,r5=10,dd20=-1,above_ma5=True)
    assert assess_cohort([strong,{}],was_broken=True,stable_days=3)['stage']=='数据不足'
    assert assess_cohort([strong,strong],was_broken=True,stable_days=3)['stage']=='出清观察'
    weak=dict(r1=-9,r5=-20,dd20=-30,above_ma5=False)
    assert assess_cohort([weak,weak,strong])['stage']=='瓦解'


def test_missing_baseline_or_endpoint_not_backfilled():
    s=pd.Series([10,np.nan,12],index=['2026-09-01','2026-09-02','2026-09-03'])
    assert stock_stats(s,'2026-09-01','2026-09-03')['return_pct'] is None
    assert stock_stats(s,'2026-09-03','2026-09-03')['return_pct'] is None
    assert stock_stats(s,'2026-09-02','2026-09-02')['return_pct'] is None


def test_separate_screenshot_groups_and_precursor_identity():
    r=build_crowding_report('2026-10-08',**inputs())
    trial=next(g for g in r['groups'] if g['id']=='trial')
    assert len(trial['members'])==4
    assert '既有趋势' in next(m for m in trial['members'] if m['code']=='sh688137')['role']
    assert len([s for s in r['sources'] if s['id']=='images'])==1
    assert len([p for p in r['periods'] if p['period_kind'] == 'retrospective']) == 7
    json.dumps(r,allow_nan=False)


def test_empty_inputs_degrade_without_inventing_prices():
    r=build_crowding_report('2026-10-08',prices=pd.DataFrame(),limits=pd.DataFrame(),sentiment=pd.DataFrame(),calendar=[],quotes={})
    assert r['market_as_of'] is None and r['quality']=='partial'
    assert r['succession']==[]
    assert r['execution']['status']=='not_qualified'


def test_future_report_gets_rolling_periods_not_permanent_september_story():
    r=build_crowding_report('2027-02-01',**inputs('2027-02-01'))
    assert r['groups']==[]
    assert r['periods'] and all(p['boundary_source'].startswith('固定观察') for p in r['periods'])


def test_html_exports_are_standalone_escaped_and_archive_safe(tmp_path):
    r=build_crowding_report('2026-10-08',**inputs())
    r['groups'][0]['members'][0]['name']='</script><script>alert(1)</script>'
    document=generate_crowding_html(r)
    assert '</script><script>alert(1)</script>' not in document
    assert 'src="http' not in document
    assert 'data-period="6"' in document
    paths=export_crowding_reports(r,tmp_path,site_dir=tmp_path/'site')
    assert all(Path(p).exists() for p in paths.values())
    site=Path(paths['site_latest']).read_text(encoding='utf-8')
    assert 'href="../index.html"' in site
    assert json.loads(Path(paths['json']).read_text(encoding='utf-8'))['schema']=='crowding-cycle/v1'

def test_trial_gates_separate_price_from_global_confirmation():
    args=inputs('2026-09-30');args['calendar'].append('2026-10-08')
    r=build_crowding_report('2026-10-08',**args)
    assert len(r['trial_gates'])==4
    assert all(x['position'] is None for x in r['trial_gates'])
    assert all(x['gates'][1]['status']=='数据缺失/过期' for x in r['trial_gates'])


def test_earlier_html_does_not_disclose_future_screenshot_names():
    r=build_crowding_report('2026-09-21',**inputs())
    doc=generate_crowding_html(r)
    # 战法示例是通用教学文本，需从事前报告中去掉具体9/28候选。
    assert '截图给出的研究链' not in doc
    assert '9/28后不能全叫' not in doc
    assert '时代万恒' not in doc and '近岸蛋白' not in doc


def test_publication_keeps_crowding_and_other_module_arguments(tmp_path,monkeypatch):
    import publish_site
    monkeypatch.setattr(publish_site,'validate_rendered_report',lambda _:None)
    src=tmp_path/'main.html';src.write_text('<html><body><a href="抱团周期分析_最新.html">抱团</a></body></html>',encoding='utf-8')
    result=build_crowding_report('2026-10-08',**inputs())
    doc=generate_crowding_html(result,is_site_mode=True)
    publish_site.publish(str(src),str(tmp_path/'site'),report_date='2026-10-08',crowding_html=doc)
    assert (tmp_path/'site/crowding/2026-10-08.html').exists()
    index=(tmp_path/'site/index.html').read_text(encoding='utf-8')
    assert 'href="crowding/latest.html"' in index
    assert 'href="../crowding/latest.html"' in (tmp_path/'site/reports/2026-10-08.html').read_text(encoding='utf-8')
    assert 'href="site/crowding/latest.html"' in (tmp_path/'本地导航入口.html').read_text(encoding='utf-8')


def test_index_does_not_advertise_missing_crowding_subpage():
    from publish_site import _render_index
    assert 'href="crowding/latest.html"' not in _render_index([], '2026-10-08')

def test_rebuilding_older_report_preserves_newer_latest(tmp_path):
    recent=build_crowding_report('2026-10-08',**inputs())
    files=export_crowding_reports(recent,tmp_path,site_dir=tmp_path/'site')
    old=build_crowding_report('2026-09-30',**inputs())
    export_crowding_reports(old,tmp_path,site_dir=tmp_path/'site')
    for key in ['local_latest','site_latest']:
        assert '<meta name="report-date" content="2026-10-08">' in Path(files[key]).read_text(encoding='utf-8')
    assert (tmp_path/'抱团周期分析_2026-09-30.html').exists()


def test_publication_rejects_crowding_date_mismatch_before_writes(tmp_path,monkeypatch):
    import pytest
    import publish_site
    monkeypatch.setattr(publish_site,'validate_rendered_report',lambda _:None)
    src=tmp_path/'main.html';src.write_text('<html><body>test</body></html>',encoding='utf-8')
    doc=generate_crowding_html(build_crowding_report('2026-10-08',**inputs()),is_site_mode=True)
    with pytest.raises(publish_site.ReportIntegrityError,match='抱团研究日期'):
        publish_site.publish(str(src),str(tmp_path/'site'),report_date='2026-09-30',crowding_html=doc)
    assert not (tmp_path/'site/crowding').exists()

def test_fresh_prices_do_not_hide_stale_breadth_clock():
    args=inputs()
    args['sentiment']=args['sentiment'][args['sentiment']['日期']<='20260930']
    r=build_crowding_report('2026-10-08',**args)
    assert r['market_as_of']=='2026-10-08'
    assert r['sources_as_of']['breadth']=='2026-09-30'
    assert r['sources_lag_trading_days']['breadth']>0
    assert r['quality']=='stale'
    assert '不同步' in generate_crowding_html(r)


def source_inputs():
    dates = pd.bdate_range('2026-09-01', periods=26).strftime('%Y-%m-%d').tolist()
    local = pd.Series(np.arange(26, dtype=float) + 10, index=dates)
    return dates, local, {'price': pd.DataFrame({'sh600127': local})}


def test_fresh_complete_local_series_replaces_stale_reference_without_splicing():
    dates, local, mats = source_inputs()
    quotes = {'stocks': {'sh600127': {'basis': 'qfq', 'rows': [
        {'date': d, 'close': 200 + i} for i, d in enumerate(dates[:-5])]}}}
    series, info = select_member_series('sh600127', quotes, mats, dates, dates[-1], dates[0])
    pd.testing.assert_series_equal(series, local, check_names=False)
    assert info['source'].startswith('全市场')
    assert info['source_as_of'] == dates[-1]
    assert info['missing_trade_dates'] == []
    assert len(info['source_candidates']) == 2


def test_complete_reference_beats_equal_date_local_series_with_a_hole():
    dates, local, mats = source_inputs()
    mats['price'].loc[dates[-4], 'sh600127'] = np.nan
    quotes = {'stocks': {'sh600127': {'basis': 'qfq', 'rows': [
        {'date': d, 'close': 200 + i} for i, d in enumerate(dates)]}}}
    series, info = select_member_series('sh600127', quotes, mats, dates, dates[-1], dates[0])
    assert info['source'].startswith('腾讯')
    assert series.loc[dates[-4]] == 222
    assert info['missing_trade_dates'] == []


def test_missing_local_price_is_not_filled_from_incomplete_reference():
    dates, local, mats = source_inputs()
    mats['price'].loc[dates[-4], 'sh600127'] = np.nan
    quotes = {'stocks': {'sh600127': {'basis': 'qfq', 'rows': [
        {'date': dates[-4], 'close': 500}, {'date': dates[-1], 'close': 501}]}}}
    series, info = select_member_series('sh600127', quotes, mats, dates, dates[-1], dates[0])
    assert info['source'].startswith('全市场')
    assert pd.isna(series.loc[dates[-4]])
    assert dates[-4] in info['missing_trade_dates']


@pytest.mark.parametrize('basis', ['raw', 'unknown', None])
def test_non_qfq_reference_never_competes_with_local_qfq(basis):
    dates, local, mats = source_inputs()
    quotes = {'stocks': {'sh600127': {'basis': basis, 'rows': [
        {'date': d, 'close': 500} for d in dates]}}}
    series, info = select_member_series('sh600127', quotes, mats, dates, dates[-1], dates[0])
    pd.testing.assert_series_equal(series, local, check_names=False)
    assert len(info['source_candidates']) == 1


def test_twenty_day_drawdown_requires_twenty_contiguous_prices():
    dates, local, mats = source_inputs()
    frame = pd.DataFrame({'date': dates, 'code': 'sh600127', 'close': local.values})
    metrics = price_matrices(frame, dates)
    assert pd.isna(metrics['dd20'].iloc[18, 0])
    assert metrics['dd20'].iloc[19, 0] == 0
    frame.loc[22, 'close'] = np.nan
    metrics = price_matrices(frame, dates)
    assert pd.isna(metrics['dd20'].iloc[-1, 0])
    assert pd.isna(metrics['r5'].iloc[-1, 0])
    assert pd.isna(metrics['r20'].iloc[-1, 0])


def scan_inputs(scores):
    dates = pd.bdate_range('2026-08-01', periods=30).strftime('%Y-%m-%d').tolist()
    frame = pd.DataFrame([{'date': d, 'code': symbol, 'close': 10 + i}
                          for i, d in enumerate(dates) for symbol in scores])
    mats = price_matrices(frame, dates)
    scan_dates = dates[-len(next(iter(scores.values()))):]
    for symbol, values in scores.items():
        mats['score'].loc[scan_dates, symbol] = values
    return mats, scan_dates, pd.Series(50, index=dates)


def test_invalidated_proxy_is_removed_and_requires_two_days_to_reconfirm():
    mats, dates, counts = scan_inputs({'sh600127': [100, 100, np.nan, 100, 100]})
    daily, transitions = _scan(mats, dates, {}, {}, counts)
    assert daily[1]['confirmed_proxy'] == 'sh600127'
    assert daily[2]['confirmed_proxy'] is None
    assert daily[2]['historical_confirmed_proxy'] == 'sh600127'
    assert daily[2]['proxy_state'] == '失效'
    assert daily[2]['invalidation_reason']
    assert daily[3]['confirmed_proxy'] is None
    assert daily[3]['pending_proxy'] == 'sh600127'
    assert daily[4]['confirmed_proxy'] == 'sh600127'
    assert daily[4]['proxy_state'] == '有效'
    assert daily[4]['state_since'] == dates[4]
    assert daily[4]['invalidation_reason'] is None
    assert [t['date'] for t in transitions] == [dates[1], dates[2], dates[4]]


@pytest.mark.parametrize('missing_kind', ['cross_section', 'member_window'])
def test_missing_data_expires_proxy_without_claiming_price_failure(missing_kind):
    mats, dates, counts = scan_inputs({'sh600127': [100, 100, np.nan]})
    if missing_kind == 'cross_section':
        counts.loc[dates[-1]] = 1
    else:
        mats['r20'].loc[dates[-1], 'sh600127'] = np.nan
    daily, _ = _scan(mats, dates, {}, {}, counts)
    assert daily[-1]['proxy_state'] == '过期'
    assert daily[-1]['confirmed_proxy'] is None
    assert daily[-1]['historical_confirmed_proxy'] == 'sh600127'


def test_proxy_switch_needs_two_days_of_at_least_two_point_advantage():
    mats, dates, counts = scan_inputs({'sh600127': [95] * 6,
                                      'sh601086': [np.nan, np.nan, 96, 96, 98, 98]})
    daily, _ = _scan(mats, dates, {}, {}, counts)
    assert daily[3]['confirmed_proxy'] == 'sh600127'
    assert daily[3]['pending_proxy'] is None
    assert daily[4]['confirmed_proxy'] == 'sh600127'
    assert daily[4]['pending_proxy'] == 'sh601086'
    assert daily[5]['confirmed_proxy'] == 'sh601086'


def test_repaired_old_cohort_keeps_break_history_and_missing_data_blocks_upgrade():
    strong = dict(r1=2, r5=10, dd20=-1, above_ma5=True)
    assert assess_cohort([strong] * 3, was_broken=True, recovery_days=1)['stage'] == '瓦解观察'
    assert assess_cohort([strong] * 3, was_broken=True, recovery_days=2)['stage'] == '修复待验证'
    assert assess_cohort([strong] * 3, was_broken=True, recovery_days=5)['stage'] == '旧团修复'
    assert assess_cohort([strong, {}], was_broken=True, recovery_days=5)['stage'] == '数据不足'


def test_fixed_basket_recovery_counter_resets_on_missing_member():
    args = inputs()
    dates = args['calendar']
    broken_date = dates.index('2026-08-31')
    values = [100 if i < broken_date else (80, 60, 40)[i - broken_date]
              if i < broken_date + 3 else 40 * 1.1 ** (i - broken_date - 2)
              for i in range(len(dates))]
    args['prices'] = pd.DataFrame([dict(date=d, code=symbol, close_qfq=value)
                                   for d, value in zip(dates, values)
                                   for symbol in ['sh600371', 'sh600108', 'sh600127']])
    report = build_crowding_report('2026-10-08', **args)
    farm = next(g for g in report['groups'] if g['id'] == 'farm')
    assert farm['was_broken'] is True
    assert farm['assessment']['stage'] == '旧团修复'
    assert {'瓦解', '修复待验证', '旧团修复'} <= {h['stage'] for h in farm['history']}
    args['prices'] = args['prices'][~((args['prices'].date == '2026-09-18') &
                                     (args['prices'].code == 'sh600371'))]
    changed = build_crowding_report('2026-10-08', **args)
    incomplete = next(g for g in changed['groups'] if g['id'] == 'farm')
    missing_day = next(h for h in incomplete['history'] if h['date'] == '2026-09-18')
    assert missing_day['stage'] == '数据不足'
    assert missing_day['recovery_days'] == 0
    assert missing_day['last_known_state'] == '旧团修复'
    assert incomplete['assessment']['stage'] == '数据不足'
    assert incomplete['basket_id'] == farm['basket_id']
    assert incomplete['member_codes'] == farm['member_codes']


def test_old_basket_stability_is_context_not_a_mandatory_trial_gate():
    report = build_crowding_report('2026-10-08', **inputs())
    assert report['trial_gates']
    for candidate in report['trial_gates']:
        assert candidate['gates'][0]['required'] is False
        assert candidate['position'] is None
        assert candidate['action'] == '保留观察，不升级为已确认新周期'


def test_rolling_periods_cover_entire_window_including_historical_gaps_and_tail():
    report = build_crowding_report('2026-10-20', **inputs('2026-10-20'))
    rolling = [p for p in report['periods'] if p['period_kind'] == 'rolling']
    dates = [d['date'] for d in report['daily']]
    covered = [d for p in rolling for d in dates if p['start'] <= d <= p['end']]
    assert covered == dates
    assert rolling[0]['start'] == dates[0]
    assert rolling[-1]['end'] == '2026-10-20'
    assert max(p['end'] for p in report['periods'] if p['period_kind'] == 'retrospective') == '2026-10-08'
    assert all(p['start'] <= p['end'] <= report['requested_date'] for p in rolling)
    document = generate_crowding_html(report)
    last_index = len(report['periods']) - 1
    assert f'data-period="{last_index}" aria-pressed="true"' in document
    assert '全市场价格持续性候选（不是连板高度）' in document


def candidate_report(scores):
    mats, dates, counts = scan_inputs(scores)
    daily, _ = _scan(mats, dates, {}, {}, counts)
    return dict(daily=daily, requested_date=dates[-1], sources_as_of={'prices': dates[-1]})


def test_candidate_snapshot_retains_all_candidates_not_just_displayed_top_six():
    report = candidate_report({f'sh60000{i}': [100 - i] for i in range(8)})
    record = report['daily'][-1]
    assert len(record['top']) == 6
    assert record['candidate_count'] == 8
    assert len(record['candidate_codes']) == len(record['candidate_scores']) == 8


def test_snapshot_is_immutable_idempotent_and_versions_cache_revisions(tmp_path):
    report = candidate_report({'sh600127': [100, 100]})
    root = archive_candidate_snapshots(report, tmp_path)
    record = report['daily'][-1]
    path = root / record['date'] / f'{record["candidate_version"]}.json'
    original = path.read_bytes()
    archive_candidate_snapshots(report, tmp_path)
    assert path.read_bytes() == original
    saved = json.loads(original)
    assert saved['known_at'] is None
    assert saved['knowledge_status'] == 'reconstructed_from_current_cache'
    assert saved['observed_at'].endswith('+08:00')
    revision = candidate_report({'sh600127': [100, 99]})
    archive_candidate_snapshots(revision, tmp_path)
    assert len(list(path.parent.glob('*.json'))) == 2
    assert path.read_bytes() == original


def test_snapshot_collision_never_overwrites_saved_candidate(tmp_path):
    report = candidate_report({'sh600127': [100]})
    root = archive_candidate_snapshots(report, tmp_path)
    record = report['daily'][0]
    path = root / record['date'] / f'{record["candidate_version"]}.json'
    original = path.read_bytes()
    record['candidate_count'] = 999
    with pytest.raises(ValueError, match='拒绝覆盖'):
        archive_candidate_snapshots(report, tmp_path)
    assert path.read_bytes() == original
