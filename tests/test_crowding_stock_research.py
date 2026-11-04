from copy import deepcopy
from html.parser import HTMLParser

import pytest

from crowding_stock_research import (build_stock_research, generate_stock_research_html,
                                    observation, parse_quotes, price_metrics, render_stock_research)


def record(n=30, last='2026-10-26'):
    from datetime import date, timedelta
    end=date.fromisoformat(last)
    return dict(basis='qfq', rows=[dict(date=(end-timedelta(days=n-i-1)).isoformat(), close=10+i,
                                      volume=100+i) for i in range(n)])


def quote(date='2026-10-26', close=39, r1=2.63):
    return dict(code='sz301313',name='凡拓数创',date=date,close=close,r1=r1)


def baseline():
    return dict(requested_date='2026-10-25',market_as_of='2026-10-23',
                tracking=dict(daily=[dict(date='2026-10-23',rows=[dict(code='sz301313',name='凡拓数创',status='已退出',first_seen='2026-10-19')])]),
                automatic_phases=dict(cohorts=[dict(id='x',formed_on='2026-10-22',anchor_name='凡拓数创',codes=['sz301313','sz000530'])]),groups=[])


def research():
    return build_stock_research(baseline(),dict(stocks={'sz301313':record()}),{'sz301313':quote()},
                                {'sz301313':dict(as_of='2026-10-26',pool='核心延续',industry='软件',themes=['物理AI'],sources=[])},
                                {},as_of='2026-10-26')


def test_metrics_are_single_series_and_asof_filtered():
    r=record();r['rows'].append(dict(date='2026-10-27',close=1,volume=100))
    m=price_metrics(r,'2026-10-26')
    assert m['close']==39 and m['r5']==pytest.approx((39/34-1)*100)
    assert m['ma5']==37 and m['dd20']==0 and m['r20']==pytest.approx((39/19-1)*100)
    assert all(x['date']<='2026-10-26' for x in m['series'])


def test_stale_or_missing_prices_never_compute_latest_support():
    m=price_metrics(record(last='2026-10-23'),'2026-10-26')
    assert m['date']=='2026-10-23' and m['ma5'] is None and m['r5'] is None
    assert observation(m,quote(),'2026-10-26')=='仅当日报价 · 多日待验证'
    assert price_metrics({},'2026-10-26')['ma5'] is None


def test_missing_expected_session_pauses_multiday_confirmation():
    r=record();dates=[x['date'] for x in r['rows'][-21:]]
    missing=dates[-3];r['rows']=[x for x in r['rows'] if x['date']!=missing]
    m=price_metrics(r,'2026-10-26',dates)
    assert m['missing_dates']==[missing] and m['above_ma5'] is None and m['r5'] is None


def test_invalid_price_and_duplicate_handling():
    r=record();r['rows'] += [dict(date='2026-10-27',close=float('inf')),dict(date='2026-10-28',close=0)]
    m=price_metrics(r,'2026-10-28')
    assert m['date']=='2026-10-26' and m['ma5'] is None


def test_formal_status_is_retained_without_mutating_inputs():
    b=baseline();before=deepcopy(b)
    r=build_stock_research(b,dict(stocks={'sz301313':record()}),{'sz301313':quote()}, {},{},as_of='2026-10-26')
    assert b==before
    assert r['stocks'][0]['formal_status']=='已退出'
    assert r['stocks'][0]['formal_status_date']=='2026-10-23'
    assert r['stocks'][0]['state']=='价格延续'
    assert r['formal_tracking_updated'] is False and r['automatic_phase_updated'] is False
    assert r['frozen_cohorts'][0]['coverage']==1 and r['frozen_cohorts'][0]['denominator']==2


def test_no_future_quotes_profiles_or_cohorts():
    b=baseline();b['automatic_phases']['cohorts'].append(dict(id='future',formed_on='2026-10-27',anchor_name='future',codes=['future']))
    r=build_stock_research(b,dict(stocks={'sz301313':record()}),{'sz301313':quote()},
                           {'future':dict(as_of='2026-10-27',themes=['future'])},{},as_of='2026-10-23')
    assert len(r['stocks'])==1 and r['stocks'][0]['quote'] is None
    assert r['stocks'][0]['profile']=={} and len(r['frozen_cohorts'])==1


@pytest.mark.parametrize('value',[-9,-10,-12])
def test_deep_drop_is_never_called_strengthening(value):
    assert observation(dict(date='2026-10-26',above_ma5=False,r5=40,dd20=-15),quote(r1=value),'2026-10-26')=='明显转弱 · 暂停强化判断'


def test_rebound_does_not_reset_exited_identity():
    assert observation(dict(date='2026-10-26',above_ma5=True,r5=-8,dd20=-25),quote(r1=13),'2026-10-26')=='深回撤反抽 · 未确认重入'


def test_quote_parser_rejects_missing_bad_or_infinite_values():
    fields=['']*40
    for i,v in {1:'凡拓',3:'67.85',4:'61.44',30:'20261026161457',32:'10.43',33:'70.78',34:'65.01',37:'220114',38:'41.08'}.items():fields[i]=v
    text='v_sz301313="'+'~'.join(fields)+'";'
    q=parse_quotes(text)['sz301313']
    assert q['date']=='2026-10-26' and q['r1']==10.43
    assert not parse_quotes(text.replace('67.85','nan'))
    assert not parse_quotes('v_sz301313="bad";')


def test_render_escapes_source_and_stock_text_and_rejects_javascript_url():
    r=research();s=r['stocks'][0]
    s['name']='evil<script>';s['profile']['risk']='<img src=x onerror=alert(1)>'
    s['profile']['sources']=[dict(url='javascript:alert(1)',title='bad',date='2026-10-26')]
    h=render_stock_research(r)
    assert '<script>' not in h and '<img' not in h and 'javascript:' not in h
    assert '&lt;img' in h


def test_empty_optional_layer_preserves_legacy_report():
    assert render_stock_research(None)==''


def test_editorial_date_requires_explicit_new_copy():
    r=research();r['requested_date']='2026-10-27'
    with pytest.raises(ValueError):generate_stock_research_html(r)


def test_generated_page_anchor_integrity_and_summary_first():
    class Parser(HTMLParser):
        def __init__(self):super().__init__();self.ids=[];self.links=[];self.sections=[]
        def handle_starttag(self,t,attrs):
            a=dict(attrs)
            if a.get('id'):self.ids.append(a['id'])
            if t=='section':self.sections.append(a.get('id'))
            if a.get('href','').startswith('#'):self.links.append(a['href'][1:])
    r=research();h=generate_stock_research_html(r);p=Parser();p.feed(h)
    assert len(p.ids)==len(set(p.ids)) and set(p.links)<=set(p.ids)
    assert p.sections[:3]==['conclusions','previous-tracking','watch-pool']
    assert h.index('id="stock-analysis"')>h.index('id="watch-pool"')
