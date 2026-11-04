from abnormal_report_view import render_report


def test_view_preserves_unknown_horizons_and_escapes_input(tmp_path):
    payload = {'meta': {'price_as_of': '2026-10-28'}, 'cases': [{
        'code': 'sz301313', 'name': '<script>bad()</script>',
        'headline': '案例', 'regulation_status': '公告已确认', 'as_of': '2026-10-28',
        'announcement_returns': [{'event_date': '2026-10-23', 'horizon': 10, 'available_sessions': 3,
                                  'return_pct': None, 'complete': False}], 'rows': []}],
        'current': [{'code': 'sz301313', 'name': '凡拓数创', 'analog_n': 0}],
        'sources': [{'title': 'bad', 'url': 'javascript:alert(1)'}]}
    render_report(payload, tmp_path)
    html = (tmp_path/'abnormal_lianban_study.html').read_text(encoding='utf8')
    md = (tmp_path/'abnormal_lianban_study.md').read_text(encoding='utf8')
    assert '<script>bad()' not in html and '&lt;script&gt;bad()' in html
    assert 'javascript:alert' not in html
    assert '|2026-10-23|10日|3|—|否|' in md
    assert '样本不足，不显示上涨空间预测' in html
    assert 'board-filter' in html and 'stock-search' in html
