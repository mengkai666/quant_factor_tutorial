"""结论先行的内容正确性与布局回归（离线，不是浏览器视觉验收）。"""
from copy import deepcopy
from html.parser import HTMLParser

import pandas as pd
import pytest

from crowding_conclusions_view import build_conclusion_summary, render_conclusions, conclusions_markdown
from crowding_cycle import build_crowding_report
from crowding_view import generate_crowding_html, generate_crowding_markdown


@pytest.fixture
def result():
    def row(code, identity, status, active=False, evidence_ok=True, date='2026-10-23'):
        return dict(code=code, name=code+'名称', identity=identity, status=status, active=active,
                    evidence_ok=evidence_ok, metrics_as_of=date, support_streak=2)
    rows = [row('old-live', '旧团', '老团强化', True),
            row('old-repair', '旧团', '旧团修复待验证'),
            row('old-exit', '旧团', '已退出'),
            row('old-missing', '旧团', '数据不足', True, False, '2026-10-15'),
            row('new-live', '新候选/新团', '持续强化', True),
            row('new-risk', '新候选/新团', '分歧观察', True),
            row('new-pending', '新候选/新团', '新生候选'),
            row('new-failed', '新候选/新团', '候选未通过'),
            row('boundary', '边界存量', '分歧观察', True)]
    phase = dict(date='2026-10-23', evidence_as_of='2026-10-23', stage='聚焦强化',
                 stage_since='2026-10-23', observed_since='2026-10-22', confirmed_on='2026-10-23',
                 reason='多数连续2日获得价格支持。', active_cohort_id='fixed', confirmed_proxy='new-live',
                 parallel_risk_count=14)
    return dict(requested_date='2026-10-25', market_as_of='2026-10-23', sources_as_of={'prices':'2026-10-23'},
                automatic_phases=dict(daily=[phase], latest=phase,
                                      cohorts=[dict(id='fixed', formed_on='2026-10-22', codes=['a','b'])]),
                tracking=dict(daily=[dict(date='2026-10-23', coverage_ok=True, rows=rows)]),
                screening_replay=dict(windows={'2':dict(days=[dict(date='2026-10-23', coverage_ok=True,
                      selected={'new-live':dict(pool='new'), 'old-live':dict(pool='old'),
                                'old-repair':dict(pool='repair')})])}),
                groups=[dict(title='历史农业篮子', assessment=dict(date='2026-10-23', stage='瓦解', reason='固定篮子弱势。'))],
                execution=dict(reason='研究观察，不生成自动交易信号。'))


def test_summary_separates_live_pending_exited_and_missing(result):
    original = deepcopy(result)
    s = build_conclusion_summary(result)
    assert [r['code'] for r in s['old_active']] == ['old-live']
    assert [r['code'] for r in s['old_pending']] == ['old-repair']
    assert s['old_exited_count'] == 1 and s['unknown_count'] == 1
    assert [r['code'] for r in s['new_active']] == ['new-live', 'new-risk']
    assert [r['code'] for r in s['new_pending']] == ['new-pending']
    assert [r['code'] for r in s['boundary']] == ['boundary']
    assert s['replay_selected_count'] == 3
    assert s['replay_pools'] == {'new':1, 'old':1, 'repair':1}
    assert result == original  # renderer只读，不改筛选或执行资格


def test_stale_member_not_counted_as_current(result):
    result['tracking']['daily'][0]['rows'][0]['metrics_as_of'] = '2026-10-22'
    s = build_conclusion_summary(result)
    assert not s['old_active']
    assert s['unknown_count'] == 2


def test_incomplete_cross_section_suspends_both_lists(result):
    result['tracking']['daily'][0]['coverage_ok'] = False
    result['screening_replay']['windows']['2']['days'][0]['coverage_ok'] = False
    s = build_conclusion_summary(result)
    assert not s['old_active'] and not s['old_pending'] and not s['new_active'] and not s['new_pending']
    assert not s['boundary'] and s['replay_selected_count'] == 0
    text = render_conclusions(result)
    assert '暂停给出当前有效名单' in text and '暂停给出条件通过结论' in text


def test_latest_visible_snapshots_ignore_future_and_keep_each_clock(result):
    future = dict(date='2026-10-26', stage='未来错误结论', rows=[], coverage_ok=True, selected={})
    result['automatic_phases']['daily'].append(future)
    result['automatic_phases']['latest'] = future
    result['tracking']['daily'].append(future)
    result['screening_replay']['windows']['2']['days'].append(future)
    result['groups'].append(dict(title='未来篮子', assessment=dict(date='2026-10-26')))
    result['sources_as_of']['prices'] = '2026-10-24'
    s = build_conclusion_summary(result)
    assert s['phase']['date'] == s['tracking_date'] == s['replay_date'] == '2026-10-23'
    assert s['price_date'] == '2026-10-24'
    text = render_conclusions(result)
    assert '未来错误结论' not in text and '未来篮子' not in text
    assert '阶段生效 2026-10-23' in text and '开始观察 2026-10-22' in text
    assert '切换后方历史回放不会改写这里' in text


def test_pending_proxy_is_not_presented_as_confirmed_core(result):
    p = result['automatic_phases']['daily'][0]
    p['confirmed_proxy'] = None
    p['pending_proxy'] = 'new-live'
    text = render_conclusions(result)
    assert '自动阶段的待确认候选：new-live名称' in text
    assert '对应的价格核心代理：' not in text


def test_empty_optional_layers_have_no_invented_conclusion():
    s = build_conclusion_summary({})
    assert not s['phase'] and not s['groups'] and s['replay_selected_count'] == 0
    text = render_conclusions({})
    assert '尚无自动阶段结论' in text and '本期没有可见的历史研究篮子' in text
    assert '聚焦强化' not in text
    assert '暂停给出条件通过结论' in '\n'.join(conclusions_markdown({}))


def test_text_is_escaped_and_risk_count_is_not_market_lines(result):
    result['tracking']['daily'][0]['rows'][0]['name'] = '<img src=x onerror="bad()">'
    result['groups'][0]['title'] = '<script>bad()</script>'
    result['automatic_phases']['daily'][0]['reason'] = 'A&B <img src=x>'
    text = render_conclusions(result)
    assert '<script>' not in text and '<img' not in text
    assert '&lt;script&gt;' in text and 'A&amp;B &lt;img' in text
    assert '14条市场主线' not in text and '14个旧篮子' not in text
    assert '不认定为当前有效抱团' in text and '不代表可成交' in text


def test_long_lists_limit_display_but_preserve_count(result):
    rows = result['tracking']['daily'][0]['rows']
    model = deepcopy(rows[4])
    for i in range(12):
        rows.append(dict(model, code=f'long-{i}', name=f'额外候选{i}'))
    text = render_conclusions(result)
    assert '当前 14 只' in text and '这里只列前8条，共14条' in text
    assert '额外候选11' not in text
    assert len(build_conclusion_summary(result)['new_active']) == 14


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.sections, self.fragments = [], [], []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'section':
            self.sections.append(attrs.get('id'))
        if tag == 'a' and attrs.get('href', '').startswith('#') and attrs['href'] != '#':
            self.fragments.append(attrs['href'][1:])


@pytest.mark.parametrize('site_mode', [False, True])
def test_full_report_conclusions_before_all_analysis(site_mode):
    r = build_crowding_report('2026-10-08', prices=pd.DataFrame(), limits=pd.DataFrame(),
                              sentiment=pd.DataFrame(), calendar=[], quotes={})
    text = generate_crowding_html(r, is_site_mode=site_mode)
    page = Page()
    page.feed(text)
    assert page.sections[:4] == ['conclusions', 'previous-tracking', 'recent-observations', 'analysis-details']
    for anchor in ['logic', 'automatic-phases', 'dynamic-tracking', 'screening-replay', 'external-evidence', 'cycles', 'cohorts']:
        if anchor in page.sections:
            assert page.sections.index(anchor) > 3
    assert len(page.ids) == len(set(page.ids))
    assert set(page.fragments) <= set(page.ids)
    assert text.count('id="crowding-data"') == 1
    md = generate_crowding_markdown(r)
    headings = [line for line in md.splitlines() if line.startswith('## ')]
    assert headings[:4] == ['## 结论先行', '## 此前抱团跟踪', '## 最近观察', '## 详细分析与验证依据']
