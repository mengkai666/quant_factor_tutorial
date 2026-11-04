from trading_playbook_view import render_index, render_playbook


def _report():
    return {
        'kind': 'abnormal', 'title': '异动每日预案', 'as_of': '2026-10-30',
        'plan_date': '2026-11-02', 'generated_at': '2026-11-01 12:00',
        'market_status': '周日休市，下一交易日条件预案',
        'decision_summary': ['优先观察测试股，收盘确认后才升级。', '未触发时等待。'],
        'decision_counts': {'priority': 1, 'watch': 0, 'avoid': 0, 'triggered': 0},
        'candidates': [{
            'name': '测试股', 'code': '301313', 'tier': '优先', 'setup': '横盘后再走强',
            'why': ['守住支撑', '放量需确认'], 'reference_price': 63,
            'entry_trigger': '收盘收复 67.85', 'entry_zone': '确认后回踩 67.85 附近',
            'stop_price': 62.01, 'target_reference': 70.78, 'upside_pct': 4.32,
            'risk_pct': 8.61, 'rr': None, 'not_buy': '高开过多不追',
            'holding': '跌破失效位降级', 'scenario_up': '若收复旧高，观察延续',
            'scenario_flat': '若缩量横盘，继续等待', 'scenario_down': '若破位，取消预案',
            'actual_status': '尚未触发', 'missing': ['未有周一成交量'], 'evidence': ['历史样本 6 个'],
        }],
        'history': [{'label': '横盘 3 日', 'n': 6, 'n5': 5, 'n10': 3, 'median5': 2.0, 'median10': None}],
        'rules': [{'title': '确认后介入', 'entry': '收盘确认', 'exit': '结构失效'}],
        'sources': [{'title': '规则来源', 'url': 'https://example.com/rule', 'date': '2026-10-30'}],
    }


def test_actions_precede_statistics_and_counts_are_explicit(tmp_path):
    render_playbook(_report(), tmp_path, 'abnormal.html')
    html = (tmp_path / 'abnormal.html').read_text(encoding='utf8')
    md = (tmp_path / 'abnormal.md').read_text(encoding='utf8')
    assert html.index('现在怎么做') < html.index('逐只预案：') < html.index('历史复盘与统计依据')
    assert '<b>1</b>筛选优先候选' in html
    assert '<b>—</b>盘中触发未核验' in html
    for value in ['收盘收复 67.85', '62.01', '高开过多不追', '若破位，取消预案']:
        assert value in html and value in md
    assert '|横盘 3 日|6|5|3|2.0|—|' in md
    assert 'stock-search' in html and 'tier-filter' in html
    assert 'data-search="测试股 301313"' in html


def test_unknown_counts_and_empty_candidates_do_not_invent_buys(tmp_path):
    render_playbook({'title': '空预案', 'decision_summary': '等待'}, tmp_path, 'empty.html')
    html = (tmp_path / 'empty.html').read_text(encoding='utf8')
    assert '<b>—</b>盘中触发未核验' in html
    assert '<b>0</b>已核验触发数' not in html
    assert '不补凑名单' in html
    assert '本次未列入' in html


def test_unsafe_markup_and_links_are_not_executable(tmp_path):
    report = _report()
    report['candidates'][0]['name'] = '<script>alert(1)</script>'
    report['candidates'][0]['why'] = ['<img src=x onerror=alert(1)>']
    report['sources'] = [{'title': '恶意源', 'url': 'javascript:alert(1)'}]
    report['links'] = {'安全': 'stock.html', '恶意': 'data:text/html,boom'}
    render_playbook(report, tmp_path, 'safe.html')
    html = (tmp_path / 'safe.html').read_text(encoding='utf8')
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert '<img src=x' not in html
    assert 'href="javascript:' not in html and 'href="data:' not in html
    assert 'href="stock.html"' in html


def test_percent_units_missing_values_and_list_text_are_preserved(tmp_path):
    report = _report()
    report['candidates'][0]['risk_pct'] = float('nan')
    report['candidates'][0]['entry_trigger'] = ['第一条件', '第二条件']
    render_playbook(report, tmp_path, 'units')
    html = (tmp_path / 'units.html').read_text(encoding='utf8')
    md = (tmp_path / 'units.md').read_text(encoding='utf8')
    assert '4.32%' in html and '432.00%' not in html
    assert '<dt>至失效位风险</dt><dd>—</dd>' in html
    assert '<li>第一条件</li><li>第二条件</li>' in html
    assert '第一条件；第二条件' in md


def test_index_links_to_all_reports_and_does_not_sum_buys(tmp_path):
    reports = []
    for kind, filename in [('abnormal', 'abnormal.html'), ('stock', 'stock.html'), ('cb', 'cb.html')]:
        report = _report()
        report.update(kind=kind, filename=filename)
        reports.append(report)
    render_index(reports, tmp_path)
    html = (tmp_path / 'index.html').read_text(encoding='utf8')
    for filename in ['abnormal.html', 'stock.html', 'cb.html']:
        assert f'href="{filename}"' in html
    assert html.count('<b>—</b>盘中触发未核验') == 3
    assert '@media print' in html
    assert 'beforeprint' in html


def test_report_cannot_write_outside_output_directory(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        render_playbook(_report(), tmp_path, '../escape.html')


def test_bond_tick_and_long_target_get_readable_full_width_action(tmp_path):
    report = _report()
    report['kind'] = 'cb'
    report['candidates'][0]['reference_price'] = 219.048
    report['candidates'][0]['target_reference'] = '首个压力参考231.238元，再看前日高点240.000元；入场已高于首压或到首压空间不足止损距离2倍则不买。'
    render_playbook(report, tmp_path, 'bond.html')
    html = (tmp_path / 'bond.html').read_text(encoding='utf8')
    assert '<dt>参考收盘</dt><dd>219.048</dd>' in html
    assert '<strong>压力与兑现参考</strong>' in html
    assert '<dt>前高 / 压力参考</dt>' not in html
    assert '.cards>.candidate:only-child{grid-column:1/-1}' in html
