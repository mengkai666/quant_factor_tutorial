"""预案上下文读取与静态页导出，历史重建不会回退最新入口。"""
from pathlib import Path
import json
import re


def load_plan_context(report_date, *, output_dir=None):
    import crowding_next_day_plan as ndp
    day = ndp._iso(report_date)
    path = Path(output_dir or ndp.OUTPUT_DIR) / 'audit' / f'{day}.json'
    if path.exists():
        try:
            ctx = json.loads(path.read_text(encoding='utf-8-sig')).get('context') or {}
            if ndp._iso(ctx.get('report_date')) == day:
                return ctx
        except (OSError, ValueError, TypeError):
            pass
        return {'report_date': day}
    target = None
    history = Path(ndp.PREDICTION_HISTORY)
    if history.exists():
        for line in history.read_text(encoding='utf-8').splitlines():
            try:
                record = json.loads(line)
                if record.get('event_type') == 'prediction' and ndp._iso(record.get('report_date')) == day:
                    target = record
            except ValueError:
                continue
    return target or {'report_date': day}


def generate_plan_html(plan, is_site_mode=True):
    from crowding_plan_view import render_document
    return render_document(plan, is_site_mode=is_site_mode)


def render_plan_panel_html(plan, is_site_mode=False):
    from crowding_plan_view import render_panel
    return ('<!-- crowding-plan-panel:start -->' + render_panel(plan, is_site_mode=is_site_mode)
            + '<!-- crowding-plan-panel:end -->')


def render_plan_teaser_html(report_date):
    import crowding_next_day_plan as ndp
    day = ndp._iso(report_date)
    decision = load_plan_context(day).get('today_decision') or {}
    return (f'<section class="plan-entry" data-report-date="{ndp._e(day)}"><h3>抱团研究与明日操作预案</h3>'
            f'<p>{ndp._e(day)} · {ndp._e(decision.get("mainline") or "主线待确认")} · 板块、股票、转债与情景触发</p>'
            '<a href="/plan/latest.html">打开最新预案</a></section>')


def can_update_latest(latest, day, dated_days):
    import crowding_next_day_plan as ndp
    latest = Path(latest)
    known = list(dated_days)
    if latest.exists():
        try:
            content = latest.read_text(encoding='utf-8')
            found = re.search(r'data-report-date=[\"\'](\d{4}-\d{2}-\d{2})', content)
            if found:
                known.append(found.group(1))
            else:
                report_meta = re.search(r'<meta\b(?=[^>]*\bname=[\"\']report-date[\"\'])(?=[^>]*\bcontent=[\"\']([^\"\']+)[\"\'])[^>]*>', content, re.I)
                if report_meta and ndp._iso(report_meta.group(1)):
                    known.append(ndp._iso(report_meta.group(1)))
                else:
                    title = re.search(r'<title>(.*?)</title>', content, re.S)
                    if title:
                        date = re.search(r'20\d{2}-\d{2}-\d{2}|20\d{6}', title.group(1))
                        if date:
                            known.append(ndp._iso(date.group()))
        except OSError:
            return False
    return not known or day >= max(known)


def export_plan_standalone_reports(report_date, output_dir=None, site_dir=None, plan_data=None):
    import crowding_next_day_plan as ndp
    day = ndp._iso(report_date)
    if not day:
        raise ValueError('预案导出需要有效报告日期')
    base = Path(output_dir or ndp.OUTPUT_DIR)
    site = Path(site_dir or base / 'site') / 'plan'
    base.mkdir(parents=True, exist_ok=True)
    site.mkdir(parents=True, exist_ok=True)
    if plan_data is None:
        ctx = load_plan_context(day, output_dir=str(base))
        plan_data = ndp.build_next_day_plan(ctx, echelon=(ctx.get('decision_context') or {}).get('echelon'), output_dir=str(base))
    if ndp._iso(plan_data.get('report_date')) != day:
        raise ValueError('预案内容日期与导出日期不一致')
    local_latest = base / '今日复盘与明日预案_最新.html'
    standalone_page = base / '抱团研究与明日预案.html'
    site_latest = site / 'latest.html'
    local_dated = base / f'今日复盘与明日预案_{day}.html'
    site_dated = site / f'{day}.html'
    dates = [ndp._iso(p.stem.replace('今日复盘与明日预案_', '')) for p in base.glob('今日复盘与明日预案_*.html') if p.stem != '今日复盘与明日预案_最新']
    site_dates = [ndp._iso(p.stem) for p in site.glob('*.html') if p.stem != 'latest']
    local_update = can_update_latest(local_latest, day, [d for d in dates if d])
    site_update = can_update_latest(site_latest, day, [d for d in site_dates if d])
    local_html, site_html = generate_plan_html(plan_data, False), generate_plan_html(plan_data, True)
    local_dated.write_text(local_html, encoding='utf-8')
    site_dated.write_text(site_html, encoding='utf-8')
    (base / f'今日复盘与明日预案_{day.replace("-", "")}.html').write_text(local_html, encoding='utf-8')
    (site / f'{day.replace("-", "")}.html').write_text(site_html, encoding='utf-8')
    if local_update:
        local_latest.write_text(local_html, encoding='utf-8')
        standalone_page.write_text(local_html, encoding='utf-8')
    if site_update:
        site_latest.write_text(site_html, encoding='utf-8')
    return {'local_latest': str(local_latest), 'local_dated': str(local_dated), 'site_latest': str(site_latest),
            'site_dated': str(site_dated), 'standalone_page': str(standalone_page),
            'latest_updated': local_update and site_update}
