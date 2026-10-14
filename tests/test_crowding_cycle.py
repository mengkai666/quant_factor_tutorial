from pathlib import Path
import json

import numpy as np
import pandas as pd

from crowding_cycle import assess_cohort, build_crowding_report, normalize_prices, stock_stats
from crowding_view import export_crowding_reports, generate_crowding_html


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
    assert len(r['periods'])==7
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
