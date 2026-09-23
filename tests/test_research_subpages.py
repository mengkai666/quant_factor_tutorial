from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote

import pytest
from bs4 import BeautifulSoup
from test_research_brief_entry import prepared


def legacy_html(day='2026-09-07'):
    from report_integrity import build_report_integrity,render_report_integrity_metadata
    meta=build_report_integrity(report_date=day,market_date=day,phase_result={},quality={'raw_coverage_pct':100})
    return ('<!doctype html><html><head><meta name="report-date" content="'+day+'">'
            '<title>原主页面</title></head><body><h1>原主页面</h1><div id="original-chart">原图表</div>'
            +render_report_integrity_metadata(meta)+'</body></html>')


def test_main_entry_keeps_original_markup_and_is_idempotent():
    from research_subpages import add_research_entry,remove_research_entry
    original=legacy_html()
    linked=add_research_entry(original,'research_briefs/latest.html',report_date='2026-09-09')
    assert remove_research_entry(linked)==original
    assert add_research_entry(linked,'research_briefs/latest.html',report_date='2026-09-09')==linked
    doc=BeautifulSoup(linked,'html.parser')
    assert doc.select_one('#original-chart').get_text()=='原图表'
    assert doc.select_one('[data-research-entry]')['href']=='research_briefs/latest.html'
    assert '2026-09-09' in doc.select_one('[data-research-entry]').get_text()
    assert not doc.select('.research-brief')


def test_navigation_escapes_text_and_rejects_script_urls():
    from research_subpages import add_research_entry
    linked=add_research_entry(legacy_html(),'child.html?a=1&b=2',report_date='<script>bad</script>')
    assert '&lt;script&gt;' in linked and '<script>bad</script>' not in linked
    with pytest.raises(ValueError):add_research_entry(legacy_html(),'javascript:alert(1)')


def test_subpage_writer_creates_return_link_and_does_not_overwrite_parent(tmp_path):
    from research_brief_io import write_research_subpage
    parent=tmp_path/'主线强度追踪.html';parent.write_text(legacy_html(),encoding='utf-8')
    before=parent.read_bytes()
    result=write_research_subpage(prepared(),tmp_path/'research_briefs',parent_report=parent)
    child=BeautifulSoup(Path(result['html']).read_text(encoding='utf-8'),'html.parser')
    href=unquote(child.select_one('[data-research-parent]')['href'])
    assert (Path(result['html']).parent/href).resolve()==parent.resolve()
    assert Path(result['latest_html']).read_bytes()==Path(result['html']).read_bytes()
    assert parent.read_bytes()==before
    assert child.select('.research-brief')


def test_subpage_latest_alias_cannot_overwrite_a_source(tmp_path):
    from research_brief_io import write_research_subpage
    directory=tmp_path/'children';directory.mkdir()
    protected=directory/'latest.html';protected.write_text('keep input',encoding='utf-8')
    with pytest.raises(ValueError):
        write_research_subpage(prepared(),directory,parent_report=tmp_path/'parent.html',source_paths=[protected])
    assert protected.read_text(encoding='utf-8')=='keep input'
    assert list(directory.iterdir())==[protected]


def test_publisher_preserves_main_and_dashboard_and_archives_separate_child(tmp_path):
    from publish_site import publish
    from research_brief_io import render_research_document
    from research_subpages import add_research_entry
    main=tmp_path/'main.html';main.write_text(add_research_entry(legacy_html(),'research_briefs/latest.html'),encoding='utf-8')
    original=main.read_bytes()
    child=render_research_document(prepared())
    dashboard='<html><body>原独立看板</body></html>'
    archived,index=publish(main,tmp_path/'site',report_date='2026-09-07',dashboard_html=dashboard,research_html=child)
    archived_doc=BeautifulSoup(Path(archived).read_text(encoding='utf-8'),'html.parser')
    assert archived_doc.select_one('#original-chart').get_text()=='原图表'
    assert not archived_doc.select('.research-brief')
    entry=archived_doc.select_one('[data-research-entry]')['href']
    child_path=(Path(archived).parent/entry).resolve()
    assert child_path==tmp_path/'site/research_briefs/research_brief_2026-09-07.html'
    child_doc=BeautifulSoup(child_path.read_text(encoding='utf-8'),'html.parser')
    back=child_doc.select_one('[data-research-parent]')['href']
    assert (child_path.parent/back).resolve()==Path(archived).resolve()
    latest=BeautifulSoup((tmp_path/'site/latest.html').read_text(encoding='utf-8'),'html.parser')
    assert (tmp_path/'site'/latest.select_one('[data-research-entry]')['href']).resolve()==child_path
    assert 'research_briefs/research_brief_2026-09-07.html' in Path(index).read_text(encoding='utf-8')
    assert (tmp_path/'site/dashboards/2026-09-07.html').read_text(encoding='utf-8')==dashboard
    assert main.read_bytes()==original


@pytest.mark.parametrize('bad',['wrong_day','wrong_head','unvalidated'])
def test_invalid_child_is_rejected_before_site_writes(tmp_path,bad):
    from publish_site import publish
    from research_brief_io import render_research_document
    from report_integrity import ReportIntegrityError
    main=tmp_path/'main.html';main.write_text(legacy_html(),encoding='utf-8')
    brief=prepared()
    if bad=='wrong_day':
        brief['report_date']='2026-09-08'
        for s in brief['sectors']:
            for r in s['stocks']:r['source_date']='2026-09-08'
        for r in brief['recent']['stocks']:r['source_date']='2026-09-08'
    child=render_research_document(brief)
    if bad=='wrong_head':child=child.replace('name="report-date" content="2026-09-07"','name="report-date" content="2026-09-08"')
    if bad=='unvalidated':child='<html><body>missing metadata</body></html>'
    with pytest.raises(ReportIntegrityError):publish(main,tmp_path/'site',report_date='2026-09-07',research_html=child)
    assert not (tmp_path/'site').exists()


@pytest.mark.parametrize("marked",[False,True])
def test_subpage_does_not_replace_an_unowned_latest_page(tmp_path,marked):
    from research_brief_io import write_research_subpage
    output=tmp_path/'children';output.mkdir()
    from research_subpages import add_research_parent
    content=add_research_parent(legacy_html(),'../main.html') if marked else legacy_html()
    latest=output/'latest.html';latest.write_text(content,encoding='utf-8')
    before=latest.read_bytes()
    with pytest.raises(ValueError):write_research_subpage(prepared(),output,parent_report=tmp_path/'parent.html')
    assert latest.read_bytes()==before
    assert len(list(output.iterdir()))==1


def test_generated_report_date_matching_ignores_only_owned_navigation_changes(tmp_path):
    from publish_site import publish,resolve_generated_report_date
    from research_brief_io import render_research_document
    from research_subpages import add_research_entry
    main=tmp_path/'main.html'
    main.write_text(add_research_entry(legacy_html(),'research_briefs/latest.html'),encoding='utf-8')
    archived,_=publish(main,tmp_path/'site',report_date='2026-09-07',research_html=render_research_document(prepared()))
    assert resolve_generated_report_date(main,tmp_path/'site/reports',run_date='2026-09-07')=='2026-09-07'
    path=Path(archived)
    path.write_text(path.read_text(encoding='utf-8').replace('原图表','正文被改变'),encoding='utf-8')
    assert resolve_generated_report_date(main,tmp_path/'site/reports',run_date='2026-09-07') is None


def test_publish_without_child_does_not_leave_a_broken_child_entry(tmp_path):
    from publish_site import publish
    from research_subpages import add_research_entry
    main=tmp_path/'main.html';main.write_text(add_research_entry(legacy_html(),'missing-child.html'),encoding='utf-8')
    before=main.read_bytes()
    archived,_=publish(main,tmp_path/'site',report_date='2026-09-07')
    assert 'data-research-entry' not in Path(archived).read_text(encoding='utf-8')
    assert 'data-research-entry' not in (tmp_path/'site/latest.html').read_text(encoding='utf-8')
    assert main.read_bytes()==before


def brief_for_day(day):
    from test_research_brief import stock,context,prices,build
    rows=[stock('sz000001',height=2,day=day)]
    ctx=context(rows);ctx['report_date']=day;ctx['facts']['market_snapshot']['report_date']=day
    ctx['target_trade_date']=None
    return build(ctx,price_rows=prices(rows,day=day),trading_days=[day],history_days=[day])


def test_historical_child_export_does_not_downgrade_latest_alias(tmp_path):
    from research_brief_io import write_research_subpage
    from report_integrity import extract_report_integrity
    output=tmp_path/'children'
    write_research_subpage(brief_for_day('2026-09-09'),output,parent_report=tmp_path/'main.html')
    before=(output/'latest.html').read_bytes()
    old=write_research_subpage(brief_for_day('2026-09-07'),output,parent_report=tmp_path/'main.html')
    assert Path(old['html']).exists()
    assert (output/'latest.html').read_bytes()==before
    assert extract_report_integrity(output/'latest.html')['report_date']=='2026-09-09'


def test_historical_publication_keeps_latest_research_child(tmp_path):
    from publish_site import publish
    from research_brief_io import render_research_document
    from report_integrity import extract_report_integrity
    site=tmp_path/'site'
    for day in ('2026-09-09','2026-09-07'):
        main=tmp_path/(day+'.html');main.write_text(legacy_html(day),encoding='utf-8')
        publish(main,site,report_date=day,research_html=render_research_document(brief_for_day(day)))
    assert extract_report_integrity(site/'research_briefs/latest.html')['report_date']=='2026-09-09'
    assert (site/'research_briefs/research_brief_2026-09-07.html').exists()


@pytest.mark.parametrize('relative',['research_briefs/latest.html','research_briefs/research_brief_2026-09-07.html','reports/2026-09-07.html','latest.html','index.html'])
def test_publish_destinations_cannot_overwrite_the_main_input(tmp_path,relative):
    from publish_site import publish
    from research_brief_io import render_research_document
    from research_subpages import add_research_parent
    from report_integrity import ReportIntegrityError
    site=tmp_path/'site'
    source=site/relative;source.parent.mkdir(parents=True,exist_ok=True)
    content=legacy_html()
    if relative=='research_briefs/latest.html':
        content=add_research_parent(render_research_document(prepared()),'../main.html')
    source.write_text(content,encoding='utf-8')
    before={p:p.read_bytes() for p in site.rglob('*') if p.is_file()}
    with pytest.raises((ValueError,ReportIntegrityError)):
        publish(source,site,report_date='2026-09-07',research_html=render_research_document(prepared()))
    assert {p:p.read_bytes() for p in site.rglob('*') if p.is_file()}==before


def test_missing_child_body_is_rejected_before_parent_links_are_written(tmp_path):
    import re
    from publish_site import publish
    from research_brief_io import render_research_document
    source=tmp_path/'main.html';source.write_text(legacy_html(),encoding='utf-8')
    child=render_research_document(prepared())
    child=re.sub(r'<body\b[^>]*>|</body>','',child)
    with pytest.raises(ValueError):publish(source,tmp_path/'site',report_date='2026-09-07',research_html=child)
    assert not (tmp_path/'site').exists()


def test_legacy_without_date_meta_matches_archive_despite_owned_navigation(tmp_path):
    from publish_site import publish,resolve_generated_report_date
    from research_brief_io import render_research_document
    from research_subpages import add_research_entry
    source=tmp_path/'main.html'
    content=legacy_html().replace('<meta name="report-date" content="2026-09-07">','')
    source.write_text(add_research_entry(content,'research_briefs/latest.html'),encoding='utf-8')
    archived,_=publish(source,tmp_path/'site',report_date='2026-09-07',research_html=render_research_document(prepared()))
    assert resolve_generated_report_date(source,tmp_path/'site/reports',run_date='2026-09-07')=='2026-09-07'
    path=Path(archived);path.write_text(path.read_text(encoding='utf-8').replace('原图表','正文不同'),encoding='utf-8')
    assert resolve_generated_report_date(source,tmp_path/'site/reports',run_date='2026-09-07') is None
