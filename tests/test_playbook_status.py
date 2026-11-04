from datetime import datetime
import pytest
from playbook_status import plan_status, trigger_status
from trading_playbook_view import render_playbook


def test_unverified_legacy_zero_never_claims_verified_zero():
    assert trigger_status({'decision_counts': {'triggered': 0}}) == (None, '盘中触发未核验')
    for n in (0, 2):
        assert trigger_status({'trigger_verification': {'status': 'verified', 'triggered_count': n}}) == (n, '已核验触发数')
    assert trigger_status({'trigger_verification': {'status': 'verified', 'triggered_count': -1}})[0] is None


@pytest.mark.parametrize('now,expected', [(datetime(2026,11,1,18), '尚待核验'),
    (datetime(2026,11,2,10), '进行中'), (datetime(2026,11,2,15), '已结束'),
    (datetime(2026,11,3,8), '已结束')])
def test_plan_clock_uses_absolute_session(now, expected):
    assert expected in plan_status({'plan_date': '2026-11-02'}, now)


def test_repair_and_excluded_keep_reasons_but_hide_raw_nested_metrics(tmp_path):
    report = {'plan_date':'2026-11-02', 'repair_watchlist': [{'name':'震裕转02','code':'123282','reason':'上市日线不足40根','recheck':'重新完整筛选','execution_eligible':False}],
              'excluded': [{'name':'奥普','code':'118075','reason':'溢价超过50%','bond_metrics':{'secret_raw_field':999}}]}
    render_playbook(report,tmp_path,'report.html')
    html=(tmp_path/'report.html').read_text(encoding='utf8')
    assert '震裕转02' in html and '上市日线不足40根' in html and '溢价超过50%' in html
    assert 'secret_raw_field' not in html
    assert 'data-list-search="repair-watch"' in html and 'data-list-search="excluded-list"' in html
    assert 'value="watch"' not in html
