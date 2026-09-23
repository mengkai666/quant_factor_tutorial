from pathlib import Path
import importlib.util
import sys
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tools'))

def engine():
    assert importlib.util.find_spec('annual_height_research') is not None, 'annual research engine is required'
    import annual_height_research
    return annual_height_research

def test_calendar_year_not_fixed_120_sessions():
    m=engine()
    assert m.year_start('2026-09-15')=='2025-09-16'
    assert m.year_start('2024-02-29')=='2023-03-01'

def test_pressure_is_past_only_and_gap_invalidates_window():
    m=engine(); h=pd.Series([3,4,5,4,3,6,7,None,3,4])
    r=m.pressure_frame(h,5)
    assert r.loc[5,'pressure']==5
    assert r.loc[5,'first_breakout']
    assert not r.loc[6,'first_breakout']
    assert pd.isna(r.loc[9,'pressure'])

def test_pause_does_not_promote_on_suspension_and_unknown_gap_resets():
    m=engine()
    assert m.board_sequence([1,1,0,1,0,1],[False,False,True,False,False,False])==[1,2,0,3,0,1]
    assert m.board_sequence([1,1,None,1])==[1,2,0,1]

def test_repair_paths_keep_first_and_second_runs_separate():
    m=engine(); dates=pd.bdate_range('2026-01-01',periods=13).strftime('%Y-%m-%d')
    b=pd.DataFrame({'boards':[1,2,3,4,0,1,2,3,0,0,1,2,0], 'close':[10,11,12,13,12,13.2,14.5,15.9,14,13,14.3,15.7,14], 'high':[10,11,12,13,12.5,13.2,14.5,15.9,14.2,13.2,14.3,15.7,14.1], 'open':[10]*13, 'volume':[10]*13,'suspended':[False]*13},index=dates)
    r=m.repair_events(b,min_first=3,max_gap=5)
    a=r[0]
    assert (a['first_height'],a['gap_sessions'],a['repair_i'],a['second_height'])==(4,1,5,3)
    assert a['confirmation_i']==6
    assert a['total_limit_ups']==7
    assert a['reclaim_break_high']
    # The break after the second run can be a new 3-board repair; no merging into 7 straight.
    assert r[1]['first_height']==3

def test_repair_missing_day_is_not_a_valid_break():
    m=engine(); b=pd.DataFrame({'boards':[1,2,3,4,0,1,2], 'close':[10,11,12,13,float('nan'),13,14],'open':[10]*7,'high':[14]*7,'volume':[10,10,10,10,0,10,10],'suspended':[False]*7})
    assert m.repair_events(b)==[]

def test_imitation_cannot_use_future_success_as_seed():
    m=engine(); seeds=[dict(code='A',first_height=4,gap_sessions=1,confirmation_i=10,repair_i=9),dict(code='B',first_height=4,gap_sessions=1,confirmation_i=14,repair_i=13)]
    candidate=dict(code='C',first_height=4,gap_sessions=2,repair_i=12)
    assert [s['code'] for s in m.eligible_seeds(seeds,candidate)]==['A']
    assert m.eligible_seeds(seeds,dict(candidate,repair_i=10))==[]

def test_historical_theme_never_looks_forward_or_uses_generic_label():
    m=engine(); cache=pd.DataFrame([dict(date='20260101',code='A',mainline='机器人',sub='减速器'),dict(date='20260110',code='A',mainline='航天',sub='商业航天'),dict(date='20260101',code='B',mainline='其它',sub='其它')])
    book=m.ThemeBook(cache,max_age_days=7)
    assert book.at('A','2026-01-05')['mainline']=='机器人'
    assert book.at('A','2025-12-31') is None
    assert book.at('A','2026-01-09') is None
    assert book.at('B','2026-01-02') is None

def test_trade_obeys_t1_and_limit_order_nonfill():
    m=engine(); b=pd.DataFrame({'open':[10,11,10,10,10], 'close':[10,11,10,11,11], 'high':[10,11,11,11,11], 'low':[10,11,9,10,10], 'reference':[10,10,11,10,11], 'upper':[11,11,12.1,11,12.1], 'lower':[9,9,9.9,9,9.9], 'volume':[10]*5})
    assert m.simulate_trade(b,0,3)['status']=='skip_open_limit'
    with pytest.raises(ValueError):m.simulate_trade(b,1,1)

def test_trade_returns_right_censor_instead_of_loss():
    m=engine(); b=pd.DataFrame({'open':[10,10], 'close':[10,10], 'high':[10,10], 'low':[10,10], 'reference':[10,10], 'upper':[11,11], 'lower':[9,9], 'volume':[10,10]})
    assert m.simulate_trade(b,0,5)['status']=='right_censored'

def test_annual_chart_does_not_follow_sector_short_window():
    m=engine(); df=pd.DataFrame({'日期':['20250915','20250916','20260316','20260915','20260916'],'连板高度':[4,5,6,7,8]})
    cut=m.annual_frame(df,'2026-09-15')
    assert cut['日期'].tolist()==['20250916','20260316','20260915']

def test_request_range_and_response_cutoff():
    assert importlib.util.find_spec('collect_annual_height_bars') is not None, 'bounded collector required'
    import collect_annual_height_bars as c
    assert '2025-07-01,2026-09-15,400,' in c.history_url('sh600000','2025-07-01','2026-09-15')
    assert c.validate_rows([['2026-09-16','1','1','1','1','1']],'2026-09-15')=='beyond_cutoff'
    assert c.validate_rows([['2026-09-15','1','1','1','1','1']],'2026-09-15')=='ok'

def test_forward_returns_do_not_cross_unmodeled_adjustment():
    m=engine(); b=pd.DataFrame({'close':[10,5,5.2],'reference':[10,10,5],'volume':[1,1,1],'adjustment_break':[False,True,False]})
    assert pd.isna(m.forward_return(b,0,2))

def test_annual_section_has_independent_window_and_past_pressure(tmp_path):
    assert importlib.util.find_spec('annual_height_view') is not None,'annual section required'
    import annual_height_view as v
    h=pd.DataFrame({'date':['2025-09-16','2026-03-16','2026-09-15'],'height':[4,5,6],'pressure5':[6,4,5],'pressure20':[7,6,7],'leader_names':['甲','乙','丙'],'broken_height':[0,0,0],'broken_names':['','','']})
    p=tmp_path/'history.csv';h.to_csv(p,index=False)
    html=v.render_annual_height_section(pd.DataFrame({'日期':['20260915'],'连板高度':[6]}),as_of='2026-09-15',history_path=p,study_link='专题.html')
    assert '2025-09-16' in html and '2026-09-15' in html
    assert '不含当日' in html and '专题.html' in html
    assert 'lianbanChart' in html
    assert 'Math.max' not in html

def test_main_reports_call_one_annual_renderer():
    for f in ['legacy_tracker.py','主线强度追踪.py']:
        source=(ROOT/'src'/f).read_text(encoding='utf8')
        begin=source.index('# --- 连板高度分析 (上半部) ---')
        stop=source.index('    fupan_html',begin)
        block=source[begin:stop]
        assert 'render_annual_height_section' in block
        assert 'date_set' not in block

def test_adjusted_history_is_separate_from_raw_limit_detection():
    import collect_annual_height_bars as c
    assert c.history_url('sz000001','2025-07-01','2026-09-15',adjustment='qfq').endswith(',400,qfq')

def test_publish_rebases_annual_study_link_without_dangling(tmp_path):
    import annual_height_view as v
    assert hasattr(v,'package_annual_study'),'annual study needs publish-safe sidecar'
    source=tmp_path/'out';source.mkdir();(source/'annual_height_research.html').write_text('<html>专题</html>',encoding='utf8')
    main='<p><a data-annual-height-study href="annual_height_research.html">专题</a></p>'
    archived,latest=v.package_annual_study(main,source,tmp_path/'site')
    assert '../research/annual_height_research.html' in archived
    assert 'href="research/annual_height_research.html"' in latest
    assert (tmp_path/'site/research/annual_height_research.html').exists()
    missing,_=v.package_annual_study(main,tmp_path/'absent',tmp_path/'other')
    assert 'href=' not in missing

def test_sentiment_without_height_can_render_other_report_sections(tmp_path):
    import annual_height_view as v
    assert v.render_annual_height_section(pd.DataFrame({'日期':['20260915'],'up':[1]}),as_of='2026-09-15',history_path=tmp_path/'absent.csv')==''

def test_case_codes_match_observed_names():
    import render_annual_height_study as r
    assert hasattr(r,'resolve_case_code'),'case charts must resolve observed name-code mapping'
    catalog=pd.DataFrame({'code':['sh605268','sz003018'],'name':['王力安防','金富科技']})
    assert r.resolve_case_code(catalog,'王力安防')=='sh605268'
    with pytest.raises(ValueError):r.resolve_case_code(catalog,'不存在')

def test_display_height_is_distinct_from_past_pressure(tmp_path):
    import annual_height_view as v
    h=pd.DataFrame({'date':['2026-09-10','2026-09-11','2026-09-14','2026-09-15'],'height':[3,4,5,6],'pressure5':[5,5,5,5],'pressure20':[7,7,7,7],'leader_names':['甲','甲','甲','甲'],'broken_height':[0]*4,'broken_names':['']*4})
    p=tmp_path/'history.csv';h.to_csv(p,index=False)
    doc=v.render_annual_height_section(as_of='2026-09-15',history_path=p)
    assert '展示前高（含当日）' in doc
    assert '龙头峰值标注' in doc

def test_section_replacement_preserves_rest_of_report():
    import annual_height_view as v
    assert hasattr(v,'replace_height_section')
    source='<html>PRE<h2>连板高度分析 (市场高度)</h2><div id="lianbanChart"></div><script>old()</script><h2>OTHER</h2>POST</html>'
    assert v.replace_height_section(source,'NEW')=='<html>PRENEW<h2>OTHER</h2>POST</html>'

def test_annual_history_extends_only_new_closed_pool_days():
    import annual_height_view as v
    assert hasattr(v,'extend_from_cached_pool')
    h=pd.DataFrame({'date':['2026-09-14'],'height':[5],'leader_names':['旧'],'leader_codes':['sz000001']})
    pool=pd.DataFrame({'日期':['20260914','20260915','20260915','20260916'],'代码':['sz000001','sz000001','sz300001','sz000001'],'名称':['旧','新','20cm','盘中'],'连板数':[99,6,10,7],'类型':['ZT']*4})
    f=v.extend_from_cached_pool(h,pool,'2026-09-15')
    assert f.date.tolist()==['2026-09-14','2026-09-15']
    assert f.height.tolist()==[5,6]
    assert f.iloc[-1].leader_names=='新'

def test_breakout_origin_requires_actual_suspension_evidence():
    m=engine();assert hasattr(m,'breakout_origin')
    b=pd.DataFrame({'boards':[3,0,4], 'suspended':[False,True,False]})
    assert m.breakout_origin(b,2)=='复牌续板'
    b.loc[1,'suspended']=False
    assert m.breakout_origin(b,2)=='正常逐日晋级'

def test_money_effect_uses_only_information_at_candidate_close():
    m=engine();assert hasattr(m,'leader_feedback')
    b=pd.DataFrame({'close':[10.,11.,12.,4.],'volume':[1]*4,'reference':[10.,10.,11.,12.],'boards':[5,6,7,0]})
    got=m.leader_feedback({'A':b},['A'],0,2)
    assert got['leader_feedback']=='领涨正反馈'
    assert got['leader_return_known']==pytest.approx(20.)
    assert got['leaders_still_on_original_chain']==1
    b.loc[1,'boards']=0
    assert m.leader_feedback({'A':b},['A'],0,2)['leaders_still_on_original_chain']==0

def test_year_view_counts_observed_days_not_placeholder_gaps(tmp_path):
    import annual_height_view as v
    f=pd.DataFrame({'date':['2026-09-11','2026-09-14','2026-09-15'],'height':[4,float('nan'),5],'pressure5':[5,float('nan'),float('nan')]})
    p=tmp_path/'data.csv';f.to_csv(p,index=False)
    doc=v.render_annual_height_section(as_of='2026-09-15',history_path=p)
    assert '已核验 2 个交易日' in doc and '缺失 1 日' in doc
