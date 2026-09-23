import ast
import pytest
from copy import deepcopy
import importlib.util
from pathlib import Path

from test_research_brief import stock, context, prices, DAYS, build


def prepared():
    rows=[stock("sz000001","算力",height=2),stock("sz000002","算力")]
    return build(context(rows),price_rows=prices(rows),trading_days=DAYS)


def test_main_html_keeps_original_layout_and_only_links_to_research_subpage(tmp_path,monkeypatch):
    import pandas as pd
    import socket
    import 主线强度追踪 as report
    from bs4 import BeautifulSoup
    from report_integrity import extract_report_integrity
    def offline(*args,**kwargs):raise OSError('offline layout test')
    monkeypatch.setattr(socket.socket,'connect',offline)
    monkeypatch.setattr(socket.socket,'connect_ex',offline)
    output=tmp_path/'report.html'
    monkeypatch.setattr(report,'OUTPUT_HTML',str(output))
    empty=pd.DataFrame()
    brief=prepared()
    before=deepcopy(brief)
    ctx={'report_date':'2026-09-07','publication_mode':'observation',
         'quality':{'status':'degraded','publication_mode':'observation'},
         'facts':{'market_state':{'publication_mode':'observation','allow_strong_conclusion':False},
                  'market_snapshot':{'report_date':'2026-09-07','limit_up':2,'limit_down':2}},
         'research_brief':brief,
         'research_subpage':{'href':'research_briefs/latest.html','report_date':'2026-09-07'}}
    report.generate_html(ml_strength=empty,sub_strength=empty,ml_ma={},sub_ma={},ml_thresh={},sub_thresh={},
        leaders={},dates=['20260907'],ratings={},sub_ratings={},echelon=[],top30_data={},
        advance_decline={'up':2000,'down':3000,'zt':2,'dt':2},sentiment_df=empty,classified_df=empty,
        price_df=empty,report_context=ctx,phase_resonance_result={'quadrants':{},'representatives':{}})
    html=output.read_text(encoding='utf-8')
    doc=BeautifulSoup(html,'html.parser')
    assert '主线强度追踪系统 V3' in doc.title.get_text()
    assert doc.select('.dashboard-header')
    assert not doc.select('.research-brief')
    assert doc.select_one('[data-research-entry]')['href']=='research_briefs/latest.html'
    assert extract_report_integrity(output)['schema']=='report-integrity/v1'
    assert brief==before


def test_cli_runs_a_real_fixture_report_with_research_csvs(tmp_path,capsys):
    from test_research_brief_io import inputs
    rows,ctx,audit,paths=inputs(tmp_path)
    source=Path(__file__).resolve().parents[1]/"tools"/"render_research_brief.py"
    spec=importlib.util.spec_from_file_location("render_research_brief_cli",source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    args=["--audit",str(audit),"--output-dir",str(tmp_path/"new")]
    for arg,path in paths.items():
        args += ["--"+arg.replace("_","-"),str(path)]
    result=module.main(args)
    assert result==0
    import json,csv
    printed=json.loads(capsys.readouterr().out)
    assert printed["sector_count"]==2 and printed["watchlist_count"]==6
    assert Path(printed["latest_html"]).exists()
    assert "data-research-parent" in Path(printed["html"]).read_text(encoding="utf-8")
    with Path(printed["watchlist_csv"]).open(encoding="utf-8-sig",newline="") as f:
        assert len(list(csv.DictReader(f)))==6
    assert "逐策略资格" not in Path(printed["html"]).read_text(encoding="utf-8")


def test_local_site_uses_embedded_research_date_and_business_summary(tmp_path):
    from research_brief_io import write_research_brief,research_site_summary
    from publish_site import publish
    brief=prepared()
    files=write_research_brief(brief,tmp_path/"report")
    html=Path(files["html"]).read_text(encoding="utf-8")
    archived,index=publish(files["html"],tmp_path/"site",report_date="2026-09-08",
                           summary=research_site_summary(brief),dashboard_html=html)
    assert Path(archived).name=="2026-09-07.html"
    assert (tmp_path/"site"/"dashboards"/"2026-09-07.html").read_text(encoding="utf-8")==html
    home=Path(index).read_text(encoding="utf-8")
    assert "算力" in home and "近期多板" in home
    assert "数据待核验" not in home


def test_cli_refuses_to_overwrite_its_calendar_input(tmp_path):
    import pytest
    from test_research_brief_io import inputs
    rows,ctx,audit,paths=inputs(tmp_path)
    source=Path(__file__).resolve().parents[1]/'tools'/'render_research_brief.py'
    spec=importlib.util.spec_from_file_location('brief_calendar_guard',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    output=tmp_path/'out';output.mkdir()
    calendar=output/'sector_watchlist_2026-09-07.csv'
    calendar.write_bytes(paths['calendar_cache'].read_bytes())
    paths['calendar_cache']=calendar
    before=calendar.read_bytes()
    args=['--audit',str(audit),'--output-dir',str(output)]
    for name,path in paths.items():args+=['--'+name.replace('_','-'),str(path)]
    with pytest.raises(SystemExit) as error:module.main(args)
    assert error.value.code==2
    assert calendar.read_bytes()==before
    assert list(output.iterdir())==[calendar]


@pytest.mark.parametrize("audit_date",["2026-09-07","20260907",20260907])
def test_cli_fetch_cache_cannot_overwrite_the_audit_input(tmp_path,monkeypatch,audit_date):
    import json,pytest
    from test_research_brief_io import inputs
    from data_sources.raw_bar_provider import RawBarProvider
    rows,ctx,audit,paths=inputs(tmp_path)
    ctx['report_date']=audit_date
    combined=paths['raw_bar_cache_dir']/'2026-09-07.json'
    combined.write_text(json.dumps({'context':ctx,'schema_version':'raw-daily-bars/v1',
        'trade_date':'2026-09-07','records':[]}),encoding='utf-8')
    before=combined.read_bytes()
    def fetch(self,codes,day,*,cache_dir):
        combined.write_text(json.dumps({'schema_version':'raw-daily-bars/v1','trade_date':day,'records':[]}),encoding='utf-8')
        return {'status':'partial','records':[],'covered':0,'requested':len(codes),'errors':[]}
    monkeypatch.setattr(RawBarProvider,'fetch_day',fetch)
    source=Path(__file__).resolve().parents[1]/'tools'/'render_research_brief.py'
    spec=importlib.util.spec_from_file_location('brief_cache_guard',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    args=['--audit',str(combined),'--output-dir',str(tmp_path/'out'),'--fetch-ohlc']
    for name,path in paths.items():args+=['--'+name.replace('_','-'),str(path)]
    with pytest.raises(SystemExit) as error:module.main(args)
    assert error.value.code==2
    assert combined.read_bytes()==before
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('audit_date',['20260907',20260907])
def test_cli_accepts_canonicalized_audit_dates_without_a_collision(tmp_path,capsys,audit_date):
    import json
    from test_research_brief_io import inputs
    rows,ctx,audit,paths=inputs(tmp_path)
    ctx['report_date']=audit_date
    audit.write_text(json.dumps({'context':ctx}),encoding='utf-8')
    before=audit.read_bytes()
    source=Path(__file__).resolve().parents[1]/'tools'/'render_research_brief.py'
    spec=importlib.util.spec_from_file_location('brief_numeric_date',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    args=['--audit',str(audit),'--output-dir',str(tmp_path/'out')]
    for name,path in paths.items():args+=['--'+name.replace('_','-'),str(path)]
    assert module.main(args)==0
    result=json.loads(capsys.readouterr().out)
    assert result['report_date']=='2026-09-07' and result['watchlist_count']==6
    assert audit.read_bytes()==before
