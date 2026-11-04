import json
from pathlib import Path

from bs4 import BeautifulSoup

from trading_playbook_links import (MAIN_BEGIN, add_main_entry, integrate_main_entries,
                                    load_report_set, package_playbooks, remove_main_entry)
from trading_playbook_links import render_cb_entry
from trading_playbook_view import DEFAULT_FILES, render_playbook
from publish_site import _same_report_content


def make_bundle(root, as_of='2026-10-30', plan_date='2026-11-02'):
    directory = root / 'output/trading_playbooks/archive' / f'{plan_date}_asof_{as_of}'
    directory.mkdir(parents=True)
    manifest = {'as_of': as_of, 'plan_date': plan_date}
    (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
    (directory / 'index.html').write_text('<html>总览</html>', encoding='utf8')
    for kind, filename in DEFAULT_FILES.items():
        report = {'kind': kind, 'as_of': as_of, 'plan_date': plan_date,
                  'decision_counts': {'priority': 1, 'watch': 0, 'triggered': 0},
                  'decision_summary': '<script>unsafe</script>', 'candidates': [],
                  'links': {'返回主线追踪报告': '../../../主线强度追踪.html#sec-trading-playbooks'}}
        render_playbook(report, directory, filename)
        (directory / Path(filename).with_suffix('.json')).write_text(json.dumps(report), encoding='utf8')
    return directory


def main_document(date):
    return f'<html><head><meta name="report-date" content="{date}"></head><body><section>原报告</section><!-- war-room-hub:end --></body></html>'


def test_main_entry_is_dated_escaped_and_idempotent(tmp_path):
    directory = make_bundle(tmp_path)
    original = main_document('2026-10-30')
    first = add_main_entry(original, tmp_path / 'output', '20261030')
    second = add_main_entry(first, tmp_path / 'output', '2026-10-30')
    assert first == second and first.count(MAIN_BEGIN) == 1
    assert remove_main_entry(first) == original
    soup = BeautifulSoup(first, 'html.parser')
    assert soup.select_one('#sec-trading-playbooks script') is None
    for link in soup.select('#sec-trading-playbooks a'):
        assert (tmp_path / 'output' / link['href']).is_file()
    assert '2026-11-02' in soup.select_one('#sec-trading-playbooks').get_text()
    assert _same_report_content(first.encode(), original.encode())


def test_incomplete_or_other_date_never_appears_as_current(tmp_path):
    directory = make_bundle(tmp_path)
    original = main_document('2026-10-29')
    assert add_main_entry(original, tmp_path / 'output', '2026-10-29') == original
    (directory / DEFAULT_FILES['cb']).unlink()
    assert load_report_set(tmp_path / 'output', '2026-10-30') is None
    assert load_report_set(tmp_path / 'output', '../bad-date') is None


def test_packaged_links_and_back_links_work_without_output_parent(tmp_path):
    directory = make_bundle(tmp_path)
    source = directory / DEFAULT_FILES['cb']
    original = source.read_bytes()
    site = tmp_path / 'separate_site'
    package_playbooks(tmp_path / 'output', site, '2026-10-30')
    relative = source.relative_to(tmp_path / 'output')
    child = site / relative
    soup = BeautifulSoup(child.read_text(encoding='utf8'), 'html.parser')
    back = soup.find('a', string='返回主线追踪报告')['href']
    assert (child.parent / back.split('#')[0]).resolve() == (site / 'reports/2026-10-30.html').resolve()
    assert source.read_bytes() == original
    archived = add_main_entry(main_document('2026-10-30'), tmp_path / 'output', '2026-10-30', '../')
    for link in BeautifulSoup(archived, 'html.parser').select('#sec-trading-playbooks a'):
        assert (site / 'reports' / link['href']).is_file()


def test_existing_main_refresh_keeps_other_dates_and_backup(tmp_path):
    make_bundle(tmp_path)
    output = tmp_path / 'output'
    main = output / '主线强度追踪.html'
    main.write_text(main_document('2026-10-30'), encoding='utf8')
    original = main.read_bytes()
    stale = output / 'site/latest.html'
    stale.parent.mkdir(parents=True)
    stale.write_text(main_document('2026-10-29'), encoding='utf8')
    stale_bytes = stale.read_bytes()
    integrate_main_entries(tmp_path, '2026-10-30')
    integrate_main_entries(tmp_path, '2026-10-30')
    assert main.read_text(encoding='utf8').count(MAIN_BEGIN) == 1
    assert stale.read_bytes() == stale_bytes
    assert (output / 'trading_playbooks/main_entry_backup/主线强度追踪.html').read_bytes() == original


def test_legacy_teaser_is_replaced_with_same_date_evidence(tmp_path):
    make_bundle(tmp_path)
    original = main_document('2026-10-30').replace('</body>',
        '<!-- cb-tracking-teaser:start -->旧报价与机器人接力<!-- cb-tracking-teaser:end --></body>')
    updated = add_main_entry(original, tmp_path / 'output', '2026-10-30')
    assert '旧报价与机器人接力' not in updated
    assert updated.count('cb-tracking-teaser:start') == 1
    assert '2026-11-02' in updated
    assert '尚未生成' in render_cb_entry(tmp_path / 'output', '2026-10-29')


def test_toc_link_is_owned_and_does_not_duplicate(tmp_path):
    make_bundle(tmp_path)
    toc = '<!-- section-toc:start --><div style="display:flex;flex-wrap:wrap;gap:6px 18px"><a href="#old">旧目录</a></div><!-- section-toc:end -->'
    original = main_document('2026-10-30').replace('<body>', '<body>' + toc)
    updated = add_main_entry(original, tmp_path / 'output', '2026-10-30')
    assert updated.count('每日三战法 · 详细报告') == 1
    assert add_main_entry(updated, tmp_path / 'output', '2026-10-30') == updated
    assert remove_main_entry(updated) == original
