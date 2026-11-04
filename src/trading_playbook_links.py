"""Add recoverable navigation to existing research outputs, not trading signals."""
from html import escape
import json
from pathlib import Path
import re
import shutil

from trading_playbook_view import DEFAULT_FILES, KIND_NAMES
from playbook_status import trigger_status, plan_status

BEGIN = '<!-- DAILY-PLAYBOOK-ENTRY-BEGIN -->'
END = '<!-- DAILY-PLAYBOOK-ENTRY-END -->'
MAIN_BEGIN = '<!-- MAIN-PLAYBOOK-ENTRY-BEGIN -->'
MAIN_END = '<!-- MAIN-PLAYBOOK-ENTRY-END -->'
TOC_BEGIN = '<!-- PLAYBOOK-TOC-BEGIN -->'
TOC_END = '<!-- PLAYBOOK-TOC-END -->'


def remove_main_entry(document):
    for start, end in ((MAIN_BEGIN, MAIN_END), (TOC_BEGIN, TOC_END),
                       ('<!-- PLAYBOOK-SESSION-REVIEW-BEGIN -->', '<!-- PLAYBOOK-SESSION-REVIEW-END -->')):
        document = re.sub(re.escape(start) + r'.*?' + re.escape(end), '', document, flags=re.S)
    return document


def load_report_set(output_dir, report_date):
    """Only pair a main report with a complete, frozen set for its own date."""
    day = str(report_date)
    if len(day) == 8 and day.isdigit():
        day = f'{day[:4]}-{day[4:6]}-{day[6:]}'
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        return None
    archive = Path(output_dir) / 'trading_playbooks/archive'
    for directory in sorted(archive.glob(f'*_asof_{day}'), reverse=True):
        try:
            manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
            reports = [json.loads((directory / Path(filename).with_suffix('.json')).read_text(encoding='utf-8'))
                       for filename in DEFAULT_FILES.values()]
            if not isinstance(manifest, dict) or any(not isinstance(r, dict) for r in reports):
                continue
            if manifest['as_of'] != day or manifest['plan_date'] <= day:
                continue
            if any(r.get('kind') != kind or r.get('as_of') != day
                   or r.get('plan_date') != manifest['plan_date']
                   or not (directory / DEFAULT_FILES[kind]).is_file()
                   for kind, r in zip(DEFAULT_FILES, reports)):
                continue
            if not (directory / 'index.html').is_file():
                continue
            return directory, reports
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def render_main_entry(output_dir, report_date, prefix=''):
    bundle = load_report_set(output_dir, report_date)
    if bundle is None:
        return add_review_entry('', output_dir, report_date, prefix)
    directory, reports = bundle
    href_base = prefix + directory.relative_to(Path(output_dir)).as_posix() + '/'
    cards = []
    for report in reports:
        kind, counts = report['kind'], report.get('decision_counts', {})
        candidates = report.get('candidates', [])
        priority = [c for c in candidates if c.get('tier') in ('priority', '优先', '优先观察', '条件优先')]
        shortlist = priority or [c for c in candidates if c.get('tier') not in ('avoid', '回避', '剔除')]
        names = '、'.join(c['name'] for c in shortlist[:3] if c.get('name')) or '暂无候选'
        verified_count, trigger_label = trigger_status(report)
        counts_text = f"优先 {counts.get('priority', '待核')} · 观察 {counts.get('watch', '待核')} · {trigger_label}" + (f" {verified_count}" if verified_count is not None else '')
        summary = str(report.get('decision_summary') or '等待完整条件确认')
        if verified_count is None:
            summary = re.sub(r'当前0只确认买入', '盘中触发尚未核验', summary)
        reserves = report.get('reserve_candidates', [])
        reserve_text = ('<p class="mp-muted">候补：' + escape('、'.join(x['name'] for x in reserves))
                        + '；须重新收盘确认。</p>') if reserves else ''
        cards.append('<article class="mp-card"><div class="mp-label">' + escape(KIND_NAMES[kind]) + '</div>'
                     + '<h3>' + escape(names) + '</h3><p class="mp-counts">' + escape(counts_text) + '</p>'
                     + '<p>原预案摘要：' + escape(summary) + '</p>'
                     + reserve_text + '<a class="mp-open" href="' + escape(href_base + DEFAULT_FILES[kind], quote=True)
                     + '">进入' + escape(KIND_NAMES[kind]) + '详细报告 →</a></article>')
    return (MAIN_BEGIN + '''<style>
#sec-trading-playbooks{margin:24px 0;padding:22px;border:1px solid #303f51;border-radius:12px;background:#101a27;color:#e6edf3;font:14px/1.7 system-ui;scroll-margin-top:24px}
#sec-trading-playbooks *{box-sizing:border-box;min-width:0}#sec-trading-playbooks h2{margin:0 0 6px;color:#f0f6fc;font-size:21px}
#sec-trading-playbooks .mp-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-top:18px}
#sec-trading-playbooks .mp-card{display:flex;flex-direction:column;padding:18px;border:1px solid #34465a;border-radius:9px;background:#162334;overflow-wrap:anywhere}
#sec-trading-playbooks .mp-card h3{margin:8px 0;font-size:17px;color:#f0f6fc}#sec-trading-playbooks p{margin:7px 0}
#sec-trading-playbooks .mp-label{color:#64d8cb;font-weight:700}#sec-trading-playbooks .mp-muted{color:#a6b7c9}
#sec-trading-playbooks .mp-counts{font-weight:600;color:#d8e5f1}#sec-trading-playbooks a{color:#8dccff;text-decoration:none}
#sec-trading-playbooks .mp-open{margin-top:auto;padding-top:14px;font-weight:700}
@media(max-width:800px){#sec-trading-playbooks .mp-grid{grid-template-columns:1fr}#sec-trading-playbooks{padding:16px}}
</style><section id="sec-trading-playbooks" aria-label="每日三战法详细报告"><h2 id="sec-trading-playbooks-title">每日三战法：当前动作与详细报告</h2>'''
            + '<p class="mp-muted">收盘数据 ' + escape(reports[0]['as_of']) + ' · 预案交易日 ' + escape(reports[0]['plan_date'])
            + ' · 独立条件研究；优先与候补均不等于已触发买入。</p><p class="mp-muted">' + escape(plan_status(reports[0])) + '</p>'
            + '<a href="' + escape(href_base + 'index.html', quote=True) + '">打开三份预案总览 →</a>'
            + ('<p><a href="' + escape(prefix + 'trading_playbooks/review_' + reports[0]['plan_date'] + '/index.html', quote=True) + '">查看' + escape(reports[0]['plan_date']) + '实际表现复盘与优化 →</a></p>' if (Path(output_dir) / 'trading_playbooks' / ('review_' + reports[0]['plan_date']) / 'index.html').is_file() else '')
            + '<div class="mp-grid">' + ''.join(cards) + '</div></section>' + MAIN_END)


def add_main_entry(document, output_dir, report_date, prefix=''):
    document = remove_main_entry(document)
    block = render_main_entry(output_dir, report_date, prefix)
    if not block:
        return document
    # 仅在存在有效同日预案 bundle 时，将同日证据结合进转债板块，绝不擦除抱团数据
    bundle = load_report_set(output_dir, report_date)
    if bundle is not None:
        document = re.sub(r'<!-- cb-tracking-teaser:start -->.*?<!-- cb-tracking-teaser:end -->',
                          lambda _: render_cb_entry(output_dir, report_date), document, flags=re.S)
    hub_end = '<!-- war-room-hub:end -->' 
    if hub_end in document:
        document = document.replace(hub_end, hub_end + block, 1)
    else:
        document = re.sub(r'(<body(?:\s[^>]*)?>)', lambda m: m.group(1) + block, document, count=1, flags=re.I)
    toc = re.search(r'<!-- section-toc:start -->.*?<!-- section-toc:end -->', document, re.S)
    if toc and 'href="#sec-trading-playbooks' not in toc.group():
        link = TOC_BEGIN + '<a href="#sec-trading-playbooks" style="color:#64d8cb;text-decoration:none;white-space:nowrap">每日三战法 · 详细报告</a>' + TOC_END
        updated_toc = re.sub(r'(<div\s+style="display:flex;flex-wrap:wrap;gap:6px 18px">)',
                             lambda m: m.group(1) + link, toc.group(), count=1)
        document = document[:toc.start()] + updated_toc + document[toc.end():]
    return document


def render_cb_entry(output_dir, report_date):
    bundle = load_report_set(output_dir, report_date)
    from cb_baotuan_report import render_cb_teaser_card_html
    base_teaser = render_cb_teaser_card_html(report_date=str(report_date))

    if bundle is None:
        text = '本交易日的完整转债预案尚未生成；先等待同日数据与筛选结果。'
        evidence_html = f'<div style="margin-top:14px;padding:10px 14px;background:#101a27;border:1px solid #34465a;border-radius:6px;font-size:12.5px;color:#8b949e;"><b style="color:#e3b341;">⚡ 条件预案：</b>{escape(text)}</div>'
    else:
        report = bundle[1][-1]
        text = f"收盘数据 {report['as_of']} · 预案 {report['plan_date']}。" + report['decision_summary']
        evidence_html = f'<div style="margin-top:14px;padding:10px 14px;background:#101a27;border:1px solid #00cccc;border-radius:6px;font-size:12.5px;color:#f0f6fc;"><b style="color:#00cccc;">⚡ 同日转债预案：</b>{escape(text)} <a href="#sec-trading-playbooks" style="color:#58a6ff;margin-left:8px;">查看当前转债预案与候补 →</a></div>'

    end_tag = "<!-- cb-tracking-teaser:end -->"
    if end_tag in base_teaser:
        pos = base_teaser.rfind("</section>")
        if pos != -1:
            return base_teaser[:pos] + evidence_html + "\n" + base_teaser[pos:]
        return base_teaser.replace(end_tag, evidence_html + "\n" + end_tag)
    return "<!-- cb-tracking-teaser:start -->\n" + base_teaser + evidence_html + "\n<!-- cb-tracking-teaser:end -->"

def add_review_entry(document, output_dir, report_date, prefix=''):
    """An after-session review is linked by its own date, never as a new plan."""
    day = str(report_date)
    if len(day) == 8 and day.isdigit():
        day = f'{day[:4]}-{day[4:6]}-{day[6:]}'
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        return document
    review = Path(output_dir) / 'trading_playbooks' / f'review_{day}' / 'index.html'
    if not review.is_file():
        return document
    begin, end = '<!-- PLAYBOOK-SESSION-REVIEW-BEGIN -->', '<!-- PLAYBOOK-SESSION-REVIEW-END -->'
    document = re.sub(re.escape(begin) + '.*?' + re.escape(end), '', document, flags=re.S)
    href = prefix + f'trading_playbooks/review_{day}/index.html'
    block = (begin + '<section id="sec-playbook-review" style="margin:20px 0;padding:20px;border:1px solid #526c82;border-radius:10px">'
             + '<h2>' + escape(day) + '三战法实际表现复盘</h2><p>逐只对照原始预案、当日行情和取消条件；盘中触发与策略收益尚未核验。规则修订与原版分别保留。</p>'
             + '<a href="' + escape(href, quote=True) + '">打开复盘、修复观察与优化记录 →</a></section>' + end)
    marker = '<!-- war-room-hub:end -->'
    if not document:
        return block
    return document.replace(marker, marker + block, 1) if marker in document else re.sub(r'(<body(?:\s[^>]*)?>)', lambda m: m.group(1) + block, document, count=1, flags=re.I)


def package_playbooks(output_dir, site_dir, report_date):
    """Copy reviewable research pages and their local evidence for site links."""
    output_dir, site_dir = Path(output_dir), Path(site_dir)
    bundle = load_report_set(output_dir, report_date)
    if bundle is None:
        return []
    archive, reports = bundle
    files = list(archive.glob('*'))
    # The frozen bundle is what the dated main page links to. Current siblings
    # are only refreshed when they match the same business date.
    current = output_dir / 'trading_playbooks'
    try:
        manifest = json.loads((current / 'manifest.json').read_text(encoding='utf-8'))
        if manifest['as_of'] == reports[0]['as_of'] and manifest['plan_date'] == reports[0]['plan_date']:
            files.extend(current.glob('*'))
    except (OSError, ValueError, KeyError):
        pass
    copied = []
    for source in files:
        if not source.is_file() or source.suffix not in {'.html', '.md', '.json', '.csv'}:
            continue
        target = site_dir / source.relative_to(output_dir)
        if target.resolve() == source.resolve():
            raise ValueError('研究站点目标不能覆盖研究源文件')
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == '.html':
            document = source.read_text(encoding='utf-8')
            parent = ('../../../reports/' + reports[0]['as_of'] + '.html') if source.parent == archive else '../latest.html'
            document = re.sub(r'href="(?:\.\./)+主线强度追踪\.html#sec-trading-playbooks"',
                              lambda _: 'href="' + parent + '#sec-trading-playbooks"', document)
            target.write_text(document, encoding='utf-8')
        else:
            shutil.copy2(source, target)
        copied.append(str(target))
    # Original abnormal research is linked from both current and dated plans.
    evidence = output_dir / 'abnormal_lianban_study'
    for source in evidence.glob('*'):
        if source.is_file() and source.suffix in {'.html', '.md', '.csv', '.json', '.png', '.svg'} and not source.name.startswith('qa_'):
            target = site_dir / source.relative_to(output_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    review = current / ('review_' + reports[0]['plan_date'])
    selected = [review / 'index.html', review / '复盘与优化.md', review / 'verification.json']
    for folder in ('stock_evidence', 'cb_evidence'):
        selected.extend((review / folder).rglob('*'))
    for source in selected:
        if not source.is_file() or source.suffix not in {'.html', '.md', '.json'}:
            continue
        target = site_dir / source.relative_to(output_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == '.html':
            document = source.read_text(encoding='utf-8').replace('../../主线强度追踪.html#sec-trading-playbooks', '../../latest.html#sec-playbook-review').replace('../../主线强度追踪.html#sec-playbook-review', '../../latest.html#sec-playbook-review')
            target.write_text(document, encoding='utf-8')
        else:
            shutil.copy2(source, target)
    return copied


def integrate_main_entries(root, as_of):
    """Update only owned navigation on existing same-date main outputs."""
    output = Path(root) / 'output'
    results = []
    for path, prefix in ((output / '主线强度追踪.html', ''),
                         (output / 'site/latest.html', ''),
                         (output / f'site/reports/{as_of}.html', '../')):
        if not path.is_file():
            continue
        document = path.read_text(encoding='utf-8')
        if not re.search(r'<meta\s+name=[\"\']report-date[\"\']\s+content=[\"\']' + re.escape(as_of) + r'[\"\']', document):
            continue
        updated = add_review_entry(add_main_entry(document, output, as_of, prefix), output, as_of, prefix)
        if updated != document:
            backup = output / 'trading_playbooks/main_entry_backup' / path.relative_to(output)
            if not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)
            path.write_text(updated, encoding='utf-8')
        results.append(str(path))
    package_playbooks(output, output / 'site', as_of)
    return results


def banner(prefix, as_of, plan_date, legacy_cb=False):
    warning = '此页旧转债案例仅作历史背景，不是今日行情或买入名单。' if legacy_cb else '保留本页历史复盘；具体名单、介入条件和取消条件请看新的行动预案。'
    return (BEGIN + '<aside style="margin:16px auto;padding:20px;max-width:1160px;border:2px solid #0f766e;border-radius:12px;background:#edf9f6;color:#143c38;font:15px/1.7 system-ui">'
            + f'<strong>先看每日行动预案 · 行情截至 {escape(as_of)} · 计划 {escape(plan_date)}</strong>'
            + f'<p>{warning}优先候选不等于已触发买点；未满足条件可以不买。</p><nav>'
            + ' ｜ '.join(f'<a style="color:#075e55" href="{prefix}{filename}">{title}</a>' for filename,title in [
                ('index.html','三报告总入口'),('abnormal_playbook.html','异动与二波'),('stock_baotuan_playbook.html','股票抱团'),('cb_baotuan_playbook.html','转债抱团')])
            + '</nav></aside>' + END)


def integrate_legacy_entries(root, as_of, plan_date):
    root = Path(root)
    backup = root/'output/trading_playbooks/legacy_entry_backup'
    results=[]
    for relative,prefix,is_cb in [
        ('output/abnormal_lianban_study/abnormal_lianban_study.html','../trading_playbooks/',False),
        ('output/抱团周期分析_最新.html','trading_playbooks/',False),
        ('output/可转债抱团复盘与追踪.html','trading_playbooks/',True),
    ]:
        path=root/relative
        if not path.exists():
            continue
        original=path.read_text(encoding='utf-8')
        target=backup/path.name
        if not target.exists():
            backup.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,target)
        block=banner(prefix,as_of,plan_date,is_cb)
        if BEGIN in original:
            updated=re.sub(re.escape(BEGIN)+r'.*?'+re.escape(END),lambda _:block,original,flags=re.S)
        else:
            updated=re.sub(r'(<body(?:\s[^>]*)?>)',lambda m:m.group(1)+block,original,count=1,flags=re.I)
        if updated!=original:
            path.write_text(updated,encoding='utf-8')
        results.append(str(path))
    return results
