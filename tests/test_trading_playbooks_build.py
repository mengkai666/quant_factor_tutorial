from datetime import datetime
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest


SPEC = importlib.util.spec_from_file_location('build_trading_playbooks', Path(__file__).resolve().parents[1] / 'tools/build_trading_playbooks.py')
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def test_default_dates_reject_partial_slice_and_use_exchange_calendar(tmp_path):
    data = tmp_path / 'data'
    slices = data / 'price_slices'
    slices.mkdir(parents=True)
    pd.DataFrame({'trade_date': ['2026-10-28', '2026-10-29', '2026-10-30', '2026-11-02']}).to_csv(data / 'trading_calendar_cache.csv', index=False)
    for day, n in [('2026-10-28', 100), ('2026-10-29', 100), ('2026-10-30', 8)]:
        pd.DataFrame({'date': [day] * n, 'code': [str(i) for i in range(n)], 'close_raw': [10] * n}).to_csv(slices / f'{day}.csv.gz', index=False)
    assert build.resolve_dates(tmp_path, now=datetime(2026, 11, 1, 10)) == ('2026-10-29', '2026-10-30')
    assert build.resolve_dates(tmp_path, '2026-10-30', now=datetime(2026, 11, 1, 10)) == ('2026-10-30', '2026-11-02')
    with pytest.raises(ValueError):
        build.resolve_dates(tmp_path, '2026-10-30', '2026-11-01', now=datetime(2026, 11, 1, 10))


def test_normalization_preserves_negative_history_and_never_infers_buys():
    raw = {'as_of': '2026-10-30', 'plan_date': '2026-11-02',
           'summary': {'headline': '观察甲', 'historical_verdict': '历史中位-8.01%', 'priority_count': 1},
           'history': {'summaries': [{'n': 177, 'median_pct': -8.01}], 'cases': [{'name': '失败', 'bars': [1, 2]}]},
           'candidates': [{'name': '甲', 'tier': '优先', 'reference': {'close': 18.08}}],
           'sources': [{'name': '来源名', 'url': 'https://example.com'}]}
    report = build.normalize_report(raw, 'stock', '2026-10-30', '2026-11-02')
    assert '历史中位-8.01%' in report['summary']
    assert 'triggered' not in report['decision_counts']
    assert report['history_stats'][0]['median_pct'] == -8.01
    assert report['history_paths'][0]['bars'] == [1, 2]
    assert 'bars' not in report['history'][0]
    assert report['candidates'][0]['reference_price'] == 18.08
    assert report['sources'][0]['title'] == '来源名'
    assert isinstance(raw['summary'], dict)


def test_string_summary_is_one_paragraph_not_characters():
    report = build.normalize_report({'as_of': '2026-10-30', 'plan_date': '2026-11-02', 'summary': '等待盘中确认'}, 'cb', '2026-10-30', '2026-11-02')
    assert report['summary'] == ['等待盘中确认']


def test_price_band_and_supplied_risk_fields_are_preserved():
    raw = {'as_of': '2026-10-30', 'plan_date': '2026-11-02', 'candidates': [{
        'entry_zone': [17.99, 18.14], 'planned_risk_pct': 2.04,
        'reward_space_pct': 9.48, 'reference_reward_risk': 4.65}]}
    report = build.normalize_report(raw, 'stock', '2026-10-30', '2026-11-02')
    candidate = report['candidates'][0]
    assert candidate['entry_zone'] == '17.99—18.14元（条件成立后）'
    assert candidate['entry_zone_raw'] == [17.99, 18.14]
    assert candidate['risk_pct'] == 2.04 and candidate['upside_pct'] == 9.48
    assert candidate['rr'] == 4.65


def test_archive_links_are_rebased_but_siblings_stay_local():
    report = build.archive_report({'links': {'原研究': '../abnormal_lianban_study/a.html', '同级': 'stock_baotuan_playbook.html', '官网': 'https://example.com'}})
    assert report['links']['原研究'] == '../../../abnormal_lianban_study/a.html'
    assert report['links']['同级'] == 'stock_baotuan_playbook.html'
    assert report['links']['官网'] == 'https://example.com'


def test_three_report_build_archive_and_csv(tmp_path):
    calls = []
    def make(kind):
        def builder(root, out, as_of, plan_date, refresh=True):
            calls.append((kind, refresh))
            return {'kind': kind, 'title': kind, 'as_of': as_of, 'plan_date': plan_date,
                    'decision_counts': {'priority': 0, 'triggered': 0}, 'candidates': [],
                    'summary': ['等待'], 'history': [], 'sources': []}
        return builder
    result = build.build_reports(tmp_path, '2026-10-30', '2026-11-02', offline=True,
                                 skip_abnormal_refresh=True, builders={k: make(k) for k in build.KINDS})
    assert calls == [('abnormal', False), ('stock', False), ('cb', False)]
    assert result['report_count'] == 3
    out = tmp_path / 'output/trading_playbooks'
    for filename in build.DEFAULT_FILES.values():
        assert (out / filename).exists()
        assert (out / Path(filename).with_suffix('.md')).exists()
        assert (out / Path(filename).with_suffix('.json')).exists()
    assert (out / 'index.html').exists() and (out / 'action_table.csv').exists()
    archive = Path(result['archive'])
    assert (archive / 'index.html').exists()
    assert json.loads((archive / 'manifest.json').read_text(encoding='utf8'))['as_of'] == '2026-10-30'


def test_rerun_preserves_original_frozen_bundle(tmp_path):
    def builder(root, out, as_of, plan_date, refresh=True):
        return {'as_of':as_of,'plan_date':plan_date,'candidates':[],'decision_counts':{'triggered':0}}
    builders = {k:builder for k in build.KINDS}
    first = build.build_reports(tmp_path,'2026-10-30','2026-11-02',offline=True,skip_abnormal_refresh=True,builders=builders)
    frozen = Path(first['archive'])
    before = {p.name:p.read_bytes() for p in frozen.iterdir() if p.is_file()}
    second = build.build_reports(tmp_path,'2026-10-30','2026-11-02',offline=True,skip_abnormal_refresh=True,builders=builders)
    assert Path(second['archive']).parent.name == 'revisions'
    assert before == {p.name:p.read_bytes() for p in frozen.iterdir() if p.is_file()}
    revised = json.loads((tmp_path/'output/trading_playbooks/stock_baotuan_playbook.json').read_text(encoding='utf8'))
    assert '事后研究修订' in revised['revision_note']
    assert revised['trigger_verification']['triggered_count'] is None
