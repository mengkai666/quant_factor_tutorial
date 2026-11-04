"""Build three linked, dated trading research plans from reproducible evidence.

Default dates follow the latest complete local stock session and the cached
exchange calendar. This command does not schedule updates or place orders.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import hashlib
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import statistics
import subprocess
import sys
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from trading_playbook_view import DEFAULT_FILES, render_index, render_playbook
from trading_playbook_links import integrate_legacy_entries, integrate_main_entries

SHANGHAI = ZoneInfo('Asia/Shanghai')
KINDS = ('abnormal', 'stock', 'cb')
TITLES = {'abnormal': '异动与二波预案', 'stock': '股票抱团预案', 'cb': '转债抱团预案'}


def _archive_directory(out, as_of, plan_date):
    """The first date bundle is immutable; later builds are explicit revisions."""
    original = out / 'archive' / f'{plan_date}_asof_{as_of}'
    if not original.exists():
        return original
    revisions = out / 'revisions'
    revisions.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        target = revisions / f'{plan_date}_asof_{as_of}_r{number:03d}'
        if not target.exists():
            return target
        number += 1


def _snapshot_evidence(out, archive):
    """Keep a content-addressed source snapshot so later refreshes are auditable."""
    import shutil
    sources = []
    roots = [out / name for name in ('abnormal_evidence', 'stock_evidence', 'cb_evidence')]
    for base in roots:
        for source in sorted(base.rglob('*')):
            if not source.is_file():
                continue
            relative = source.relative_to(out)
            target = archive / 'evidence' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            sources.append({'path': relative.as_posix(), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    _json(archive / 'evidence_manifest.json', sources)
    return len(sources)


def _list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _sources(value):
    result = []
    for original in _list(value):
        if not isinstance(original, dict):
            result.append({'title': str(original)})
            continue
        item = dict(original)
        item['title'] = item.get('title') or item.get('name') or '资料来源'
        item.setdefault('date', item.get('as_of') or item.get('retrieved_at'))
        result.append(item)
    return result


def resolve_dates(root: Path, as_of: str | None = None, plan_date: str | None = None,
                  now: datetime | None = None) -> tuple[str, str]:
    """Reject partial latest sessions and never guess exchange holidays."""
    root = Path(root)
    now = now or datetime.now(SHANGHAI)
    if now.tzinfo is not None:
        now = now.astimezone(SHANGHAI)
    safe_date = now.date() if now.hour >= 15 else now.date() - timedelta(days=1)
    calendar = sorted(set(pd.read_csv(root / 'data/trading_calendar_cache.csv')['trade_date'].astype(str)))
    if as_of is None:
        counts = {}
        files = [p for p in sorted((root / 'data/price_slices').glob('*.csv.gz'))
                 if p.name[:10] <= safe_date.isoformat() and p.name[:10] in calendar][-11:]
        for path in files:
            frame = pd.read_csv(path, usecols=['date', 'code', 'close_raw'])
            valid = frame[frame.close_raw.notna() & frame.date.astype(str).eq(path.name[:10])]
            counts[path.name[:10]] = int(valid.code.nunique())
        positive = [n for n in counts.values() if n > 0]
        if not positive:
            raise ValueError('没有可核验的完整价格截面，请先更新本地收盘缓存')
        threshold = statistics.median(positive) * .9
        as_of = max(day for day, n in counts.items() if n >= threshold)
    date.fromisoformat(as_of)
    if as_of > safe_date.isoformat() or as_of not in calendar:
        raise ValueError('as-of 必须是交易日历内已经收盘的交易日')
    if plan_date is None:
        later = [day for day in calendar if day > as_of]
        if not later:
            raise ValueError('交易日历未覆盖下一交易日，请先更新日历')
        plan_date = later[0]
    date.fromisoformat(plan_date)
    if plan_date <= as_of or plan_date not in calendar:
        raise ValueError('plan-date 必须是 as-of 之后、交易日历内的交易日')
    return as_of, plan_date


def normalize_report(raw: dict, kind: str, as_of: str, plan_date: str) -> dict:
    """Adapt presentation fields; preserve all original evidence and decisions."""
    report = deepcopy(raw)
    report['kind'] = kind
    if report.get('as_of') != as_of or report.get('plan_date') != plan_date:
        raise ValueError(f'{kind} 返回日期与请求不符')
    report['filename'] = DEFAULT_FILES[kind]
    report.setdefault('generated_at', datetime.now(SHANGHAI).isoformat(timespec='seconds'))
    report.setdefault('market_status', '收盘后条件预案；盘前与盘中门槛尚待核验')
    report.setdefault('trigger_verification', {'status': 'not_verified', 'triggered_count': None,
                                             'reason': '未取得完整盘中触发与成交证据'})
    summary = report.get('summary', [])
    if isinstance(summary, dict):
        report['summary_metrics'] = deepcopy(summary)
        report['summary'] = [str(summary[k]) for k in ('headline', 'action_now', 'historical_verdict') if summary.get(k)]
        report.setdefault('decision_summary', summary.get('headline') or summary.get('action_now'))
        counts = report.setdefault('decision_counts', {})
        for to, source in (('priority', 'priority_count'), ('triggered', 'triggered_count')):
            if source in summary:
                counts.setdefault(to, summary[source])
        report.setdefault('market_summary', {k: summary[k] for k in (
            'market_risk', 'market_breadth_pct', 'limit_up', 'limit_down', 'max_height', 'market_date', 'market_scope') if k in summary})
    else:
        report['summary'] = _list(summary)
    report['sources'] = _sources(report.get('sources', []))
    report['limitations'] = _list(report.get('limitations', []))
    history = report.get('history', [])
    if isinstance(history, dict):
        report['history_evidence'] = deepcopy(history)
        report['history_stats'] = history.get('summaries', [])
        report['history'] = history.get('cases', [])
        report['limitations'].extend(history.get('limitations', []))
    # Full paths remain in the JSON; the table shows readable case summaries.
    report['history_paths'] = deepcopy(report.get('history', []))
    report['history'] = [{k: v for k, v in item.items() if k not in ('bars', 'daily', 'events', 'path_rows')}
                         if isinstance(item, dict) else {'notes': item}
                         for item in _list(report.get('history', []))]
    for candidate in report.get('candidates', []):
        reference = candidate.get('reference')
        if 'reference_price' not in candidate:
            candidate['reference_price'] = reference.get('close') if isinstance(reference, dict) else candidate.get('price', reference)
        candidate.setdefault('why', candidate.get('reason') or candidate.get('old_pool_status'))
        if candidate.get('upside_pct') is None:
            candidate['upside_pct'] = candidate.get('reward_space_pct')
        if candidate.get('risk_pct') is None:
            candidate['risk_pct'] = candidate.get('planned_risk_pct', candidate.get('risk_to_stop_pct'))
        if candidate.get('rr') is None:
            candidate['rr'] = candidate.get('reference_reward_risk', candidate.get('reward_risk_ratio'))
        zone = candidate.get('entry_zone')
        if isinstance(zone, list) and len(zone) == 2 and all(isinstance(x, (float, int)) for x in zone):
            candidate['entry_zone_raw'] = deepcopy(zone)
            candidate['entry_zone'] = f'{zone[0]:.2f}—{zone[1]:.2f}元（条件成立后）'
        candidate['sources'] = _sources(candidate.get('sources', []))
        evidence = _list(candidate.get('evidence'))
        if isinstance(candidate.get('board_path'), dict):
            evidence.append(candidate['board_path'].get('text', ''))
        announcements = candidate.get('announcements', {})
        if isinstance(announcements, dict):
            candidate['sources'].extend(_sources(announcements.get('items', [])))
        candidate['evidence'] = [v for v in evidence if v]
    links = {TITLES[k]: DEFAULT_FILES[k] for k in KINDS if k != kind}
    links['返回主线追踪报告'] = '../主线强度追踪.html#sec-trading-playbooks'
    links.update(report.get('links', {}))
    report['links'] = links
    return report


def _json(path: Path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def _cell(value):
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    return value


def write_action_table(reports: list[dict], out: Path):
    columns = ['kind', 'as_of', 'plan_date', 'rank', 'tier', 'code', 'name', 'setup', 'actual_status',
               'reference_price', 'entry_trigger', 'entry_zone', 'stop_price', 'target_reference',
               'not_buy', 'holding', 'scenario_up', 'scenario_flat', 'scenario_down', 'missing']
    with (out / 'action_table.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for report in reports:
            for candidate in report.get('candidates', []):
                row = {key: report.get(key) for key in ('kind', 'as_of', 'plan_date')}
                row.update({key: candidate.get(key) for key in columns if key not in row})
                writer.writerow({key: _cell(value) for key, value in row.items()})


def archive_report(report: dict) -> dict:
    """Archive siblings remain relative; links to original evidence gain 2 levels."""
    result = deepcopy(report)
    sibling_names = set(DEFAULT_FILES.values()) | {'index.html'}
    def rebase(href):
        if not isinstance(href, str):
            return href
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc or href.startswith(('#', '/')) or href in sibling_names:
            return href
        return '../../' + href
    result['links'] = {label: rebase(href) for label, href in result.get('links', {}).items()}
    for sources in [result.get('sources', [])] + [c.get('sources', []) for c in result.get('candidates', [])]:
        for source in sources:
            if isinstance(source, dict) and source.get('url'):
                source['url'] = rebase(source['url'])
    return result


def build_reports(root: Path, as_of: str, plan_date: str, *, offline=False,
                  skip_abnormal_refresh=False, builders=None) -> dict:
    root = Path(root).resolve()
    out = root / 'output/trading_playbooks'
    out.mkdir(parents=True, exist_ok=True)
    if not skip_abnormal_refresh:
        command = [sys.executable, str(root / 'tools/generate_abnormal_lianban_study.py'), '--as-of', as_of]
        if offline:
            command.append('--offline')
        subprocess.run(command, cwd=root, check=True)
    if builders is None:
        from abnormal_action_research import build_abnormal_research
        from stock_action_research import build_stock_research
        from cb_action_research import build_cb_research
        builders = {'abnormal': build_abnormal_research, 'stock': build_stock_research, 'cb': build_cb_research}
    reports = []
    for kind in KINDS:
        print(f'[{kind}] 生成 {as_of} 收盘 / {plan_date} 预案', flush=True)
        evidence_out = out if kind == 'cb' else out / f'{kind}_evidence'
        raw = builders[kind](root, evidence_out, as_of, plan_date, refresh=not offline)
        reports.append(normalize_report(raw, kind, as_of, plan_date))
    archive = _archive_directory(out, as_of, plan_date)
    if archive.parent.name == 'revisions':
        for report in reports:
            report['revision_note'] = '事后研究修订：修正覆盖与规则表达；原始冻结预案另存，不能用本版规则追算当时成功率。'
            report['links']['原始冻结预案'] = f'archive/{plan_date}_asof_{as_of}/index.html'
    review = out / f'review_{plan_date}' / 'index.html'
    if review.is_file():
        for report in reports:
            report['links'][f'{plan_date}实际表现复盘'] = f'review_{plan_date}/index.html'
    # Only replace the user-facing report set after all three builds succeed.
    for report in reports:
        render_playbook(report, out, report['filename'])
        _json(out / Path(report['filename']).with_suffix('.json'), report)
    render_index(reports, out)
    write_action_table(reports, out)
    archive.mkdir(parents=True, exist_ok=True)
    evidence_count = _snapshot_evidence(out, archive)
    archived = [archive_report(r) for r in reports]
    for report in archived:
        report['evidence_snapshot'] = 'evidence_manifest.json'
        if isinstance(report.get('data_quality'), dict) and 'raw_dir' in report['data_quality']:
            report['data_quality']['raw_dir'] = 'evidence/cb_evidence/raw'
        render_playbook(report, archive, report['filename'])
        _json(archive / Path(report['filename']).with_suffix('.json'), report)
    render_index(archived, archive)
    write_action_table(archived, archive)
    integrated = integrate_legacy_entries(root, as_of, plan_date)
    manifest = {'as_of': as_of, 'plan_date': plan_date,
                'generated_at': datetime.now(SHANGHAI).isoformat(timespec='seconds'),
                'offline': offline, 'report_count': len(reports), 'archive': str(archive),
                'evidence_files': evidence_count,
                'reports': [{'kind': r['kind'], 'filename': r['filename'], 'decision_counts': r.get('decision_counts', {})} for r in reports],
                'integrated_legacy_entries': integrated,
                'evidence_note': '原始证据见各 *_evidence；归档JSON保留报告证据字段及历史路径，不代表分钟级可成交回测。'}
    _json(out / 'manifest.json', manifest)
    _json(archive / 'manifest.json', manifest)
    manifest['integrated_main_entries'] = integrate_main_entries(root, as_of)
    _json(out / 'manifest.json', manifest)
    _json(archive / 'manifest.json', manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', help='已收盘交易日，默认最新完整本地截面')
    parser.add_argument('--plan-date', help='预案交易日，默认交易日历中下一交易日')
    parser.add_argument('--offline', action='store_true', help='仅使用已有证据快照，不联网')
    parser.add_argument('--skip-abnormal-refresh', action='store_true', help='复用已生成的同日异动研究底稿')
    args = parser.parse_args(argv)
    as_of, plan_date = resolve_dates(ROOT, args.as_of, args.plan_date)
    manifest = build_reports(ROOT, as_of, plan_date, offline=args.offline,
                             skip_abnormal_refresh=args.skip_abnormal_refresh)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
