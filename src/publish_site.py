"""站点发布 (Publish Site) — 把每日报告归档成一个"有记忆的网站"。

产品化第一步: 从"一封邮件/一个本地 HTML"变成"一个每天更新、可翻历史的网址"。

职责单一, 不含任何业务计算:
  1. 把当日生成的 OUTPUT_HTML 复制进 site/reports/YYYY-MM-DD.html (归档, 不覆盖历史)
  2. 扫描 site/reports/ 下所有历史报告, 重新生成 site/index.html (首页索引 + 最新报告直达)
  3. 生成 site/latest.html (始终指向最新一期, 方便固定链接分享)

本地与 CI 通用:
  - 本地: 直接在 output/site/ 下累积 (site/ 已 gitignore)
  - CI:   跑批前把 gh-pages 分支内容检出到 output/site/, 跑完部署回 gh-pages (keep_files)
          => 历史报告跨运行持久化

调首页样式/文案只改本文件。
"""
import os
import re
import shutil
from datetime import datetime


def _fmt_date(d):
    """datetime -> 'YYYY-MM-DD' 字符串。"""
    return d.strftime('%Y-%m-%d')


def _scan_reports(reports_dir, max_date=None):
    """扫描不晚于 max_date 的归档，返回 [(date_str, filename)] 按日期倒序。"""
    if not os.path.isdir(reports_dir):
        return []
    if isinstance(max_date, datetime):
        max_date = _fmt_date(max_date)
    elif max_date is not None:
        max_date = str(max_date)
    pat = re.compile(r'^(\d{4}-\d{2}-\d{2})\.html$')
    items = []
    for name in os.listdir(reports_dir):
        m = pat.match(name)
        if m and (max_date is None or m.group(1) <= max_date):
            items.append((m.group(1), name))
    items.sort(key=lambda x: x[0], reverse=True)
    return items


def _esc(s):
    """最小 HTML 转义 (结论文本可能含 < > &)。"""
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _render_verdict(summary):
    """首屏结论卡片: 择时档位 + 一句话理由 + 关键读数 + 数据可信度。

    summary 期望键 (全部可选, 缺失自动降级):
      stance / color / head / play  —— 来自 market_stance.classify_market_stance
      ad / zt / dt / max_h          —— 关键读数
      data_ok (bool) / data_note    —— 数据可信度 (缺失时不显示徽标)
    不传 summary 或为空 -> 返回 '' (首页回落纯归档索引)。
    """
    if not summary:
        return ''
    color = summary.get('color') or '#58a6ff'
    stance = _esc(summary.get('stance') or '—')
    head = _esc(summary.get('head') or '')
    play = _esc(summary.get('play') or '')

    # 关键读数条 (有才显示)
    reads = []
    if summary.get('ad') is not None:
        reads.append(f"A/D <b>{_esc(summary['ad'])}</b>")
    if summary.get('zt') is not None or summary.get('dt') is not None:
        reads.append(f"涨停 <b>{_esc(summary.get('zt', 0))}</b> / 跌停 <b>{_esc(summary.get('dt', 0))}</b>")
    if summary.get('max_h'):
        reads.append(f"最高 <b>{_esc(summary['max_h'])}</b> 板")
    reads_html = ('　|　'.join(reads)) if reads else ''

    # 数据可信度徽标
    badge = ''
    if 'data_ok' in summary:
        if summary['data_ok']:
            badge = ('<span class="badge ok">● 数据完整</span>')
        else:
            note = _esc(summary.get('data_note') or '部分数据缺失/降级')
            badge = (f'<span class="badge warn">▲ 数据降级 · {note}</span>')

    play_html = f'<div class="verdict-play"><b>操作</b> · {play}</div>' if play else ''
    reads_row = f'<div class="verdict-reads">{reads_html}</div>' if reads_html else ''

    return f'''
  <div class="verdict" style="--vc:{color};">
    <div class="verdict-top">
      <span class="verdict-label">今日结论 · 顺逆指数</span>
      {badge}
    </div>
    <div class="verdict-stance">{stance}</div>
    <div class="verdict-head">{head}</div>
    {play_html}
    {reads_row}
  </div>'''


def _render_dashboard_entry(dashboard_date):
    """在 verdict 卡片下方插入的"决策看板"入口卡. dashboard_date 为空则返回空."""
    if not dashboard_date:
        return ''
    return f'''
  <a class="dashboard-entry" href="dashboards/latest.html">
    <div class="de-left">
      <div class="de-label">数据驱动 · 决策看板</div>
      <div class="de-title">6 场景分类器 + 三因子交叉 + 历史胜率</div>
      <div class="de-sub">当日看板 · {dashboard_date} · 覆盖明日 T+1 决策树</div>
    </div>
    <div class="de-right">打开看板 →</div>
  </a>'''


def _render_index(reports, updated_at, summary=None, dashboard_date=None):
    """生成首页 HTML。reports: [(date_str, filename)] 已按日期倒序。

    dashboard_date: 提供后在首页顶部插入"当日决策看板"卡片入口。

    summary: 可选结论 dict, 渲染到首屏 (见 _render_verdict)。缺失时首页为纯归档索引。
    """
    if not reports:
        latest_date, latest_file = '—', ''
    else:
        latest_date, latest_file = reports[0]

    verdict_html = _render_verdict(summary)

    # 归档列表按 "年-月" 分组, 更像产品的时间线
    groups = {}
    for date_str, fname in reports:
        ym = date_str[:7]  # YYYY-MM
        groups.setdefault(ym, []).append((date_str, fname))

    archive_html = []
    for ym in sorted(groups.keys(), reverse=True):
        rows = ''.join(
            f'<a class="day" href="reports/{fname}">{date_str[8:]}</a>'
            for date_str, fname in groups[ym]
        )
        archive_html.append(
            f'<div class="month"><div class="month-label">{ym}</div>'
            f'<div class="days">{rows}</div></div>'
        )
    archive_block = ''.join(archive_html) or '<p class="empty">暂无历史报告</p>'

    total = len(reports)
    latest_href = f'reports/{latest_file}' if latest_file else '#'

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>主线强度追踪 · A股短线主线终端</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #0d1117; color: #e6edf3; line-height: 1.6;
    min-height: 100vh; padding: 48px 20px;
  }}
  .wrap {{ max-width: 880px; margin: 0 auto; }}
  header {{ margin-bottom: 40px; }}
  h1 {{ font-size: 28px; font-weight: 700; letter-spacing: -0.5px; }}
  .tagline {{ color: #8b949e; font-size: 15px; margin-top: 8px; }}
  .hero {{
    background: linear-gradient(135deg, #161b22, #1c2128);
    border: 1px solid #30363d; border-radius: 14px;
    padding: 28px 32px; margin: 32px 0 40px;
    display: flex; align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: 20px;
  }}
  .hero-info .label {{ color: #8b949e; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; }}
  .hero-info .date {{ font-size: 32px; font-weight: 700; margin-top: 4px; }}
  .hero-info .sub {{ color: #8b949e; font-size: 13px; margin-top: 6px; }}
  .btn {{
    background: #238636; color: #fff; text-decoration: none;
    padding: 12px 28px; border-radius: 10px; font-size: 15px; font-weight: 600;
    transition: background .15s; white-space: nowrap;
  }}
  .btn:hover {{ background: #2ea043; }}
  .section-title {{ font-size: 14px; color: #8b949e; text-transform: uppercase;
    letter-spacing: 1px; margin-bottom: 16px; }}
  .month {{ margin-bottom: 20px; }}
  .month-label {{ font-size: 13px; color: #6e7681; margin-bottom: 8px; }}
  .days {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .day {{
    display: inline-block; min-width: 44px; text-align: center;
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 8px 12px; color: #58a6ff; text-decoration: none; font-size: 14px;
    transition: border-color .15s, background .15s;
  }}
  .day:hover {{ border-color: #58a6ff; background: #1c2333; }}
  .empty {{ color: #6e7681; }}
  .verdict {{
    background: linear-gradient(135deg, #12171e, #171d26);
    border: 2px solid var(--vc); border-left-width: 6px; border-radius: 14px;
    padding: 24px 28px; margin: 0 0 24px;
    box-shadow: 0 0 24px color-mix(in srgb, var(--vc) 22%, transparent);
  }}
  .verdict-top {{ display: flex; align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: 10px; margin-bottom: 10px; }}
  .verdict-label {{ color: #8b949e; font-size: 12px; text-transform: uppercase;
    letter-spacing: 1px; font-weight: 700; }}
  .badge {{ font-size: 12px; font-weight: 700; padding: 4px 10px; border-radius: 999px; }}
  .badge.ok {{ color: #3fb950; background: rgba(63,185,80,.12); border: 1px solid rgba(63,185,80,.35); }}
  .badge.warn {{ color: #d29922; background: rgba(210,153,34,.12); border: 1px solid rgba(210,153,34,.4); }}
  .verdict-stance {{ font-size: 30px; font-weight: 800; color: var(--vc); margin-bottom: 8px; letter-spacing: -0.5px; }}
  .verdict-head {{ color: #e6edf3; font-size: 15px; margin-bottom: 6px; }}
  .verdict-play {{ color: #c9d1d9; font-size: 14px; margin-bottom: 10px; }}
  .verdict-play b {{ color: var(--vc); }}
  .verdict-reads {{ color: #8b949e; font-size: 13px; padding-top: 8px; border-top: 1px solid #21262d; }}
  .verdict-reads b {{ color: #e6edf3; }}
  .dashboard-entry {{
    display: flex; align-items: center; justify-content: space-between;
    gap: 20px; text-decoration: none;
    background: linear-gradient(135deg, #1a1e2e, #16202e);
    border: 1px solid #30363d; border-left: 4px solid #58a6ff;
    border-radius: 12px; padding: 18px 24px; margin-bottom: 20px;
    transition: border-color .15s, transform .15s, box-shadow .15s;
  }}
  .dashboard-entry:hover {{ border-color: #58a6ff; transform: translateX(2px);
    box-shadow: 0 4px 16px rgba(88,166,255,.18); }}
  .de-label {{ color: #58a6ff; font-size: 11px; text-transform: uppercase;
    letter-spacing: 1.5px; font-weight: 700; }}
  .de-title {{ color: #e6edf3; font-size: 16px; font-weight: 700; margin-top: 3px; }}
  .de-sub {{ color: #8b949e; font-size: 12px; margin-top: 4px; }}
  .de-right {{ color: #58a6ff; font-size: 14px; font-weight: 600; white-space: nowrap; }}
  footer {{ margin-top: 48px; color: #6e7681; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>主线强度追踪</h1>
    <div class="tagline">每天收盘后自动更新 · 告诉你明天主线在哪、该进攻还是防御</div>
  </header>

  {verdict_html}

  {_render_dashboard_entry(dashboard_date)}

  <div class="hero">
    <div class="hero-info">
      <div class="label">最新报告</div>
      <div class="date">{latest_date}</div>
      <div class="sub">共 {total} 期历史 · 更新于 {updated_at}</div>
    </div>
    <a class="btn" href="{latest_href}">查看最新报告 →</a>
  </div>

  <div class="section-title">历史归档</div>
  {archive_block}

  <footer>数据自动跑批生成 · 仅供研究参考, 不构成投资建议</footer>
</div>
</body>
</html>'''


def publish(output_html, site_dir, report_date=None, summary=None, dashboard_html=None):
    """把 output_html 归档进 site_dir 并重建首页。

    Args:
        output_html:    当日生成的报告 HTML 绝对路径。
        site_dir:       站点根目录 (本地为 output/site, CI 为检出的 gh-pages)。
        report_date:    datetime, 报告日期口径 (默认取缓存最新日, 与系统其它模块一致)。
        summary:        可选结论 dict (择时档位 + 数据可信度), 渲染到首页首屏; 缺失则首页为纯归档索引。
        dashboard_html: 可选决策看板 HTML 字符串; 提供后归档到 dashboards/YYYY-MM-DD.html
                        并生成 dashboards/latest.html, 首页顶部加入口。

    Returns:
        (archived_path, index_path) 或 None (源文件不存在时)。
    """
    if not os.path.exists(output_html):
        print(f"  [publish] 源报告不存在, 跳过发布: {output_html}")
        return None

    if report_date is None:
        try:
            from time_utils import get_latest_date
            report_date = get_latest_date()
        except Exception:
            report_date = datetime.now()

    reports_dir = os.path.join(site_dir, 'reports')
    os.makedirs(reports_dir, exist_ok=True)

    date_str = _fmt_date(report_date)
    archived = os.path.join(reports_dir, f'{date_str}.html')
    shutil.copyfile(output_html, archived)

    # latest.html: 固定链接, 始终等于最新一期
    shutil.copyfile(output_html, os.path.join(site_dir, 'latest.html'))

    # === 决策看板归档 (方案 B 第 3 步) ===
    dashboard_date = None
    if dashboard_html:
        dashboards_dir = os.path.join(site_dir, 'dashboards')
        os.makedirs(dashboards_dir, exist_ok=True)
        dash_archived = os.path.join(dashboards_dir, f'{date_str}.html')
        with open(dash_archived, 'w', encoding='utf-8') as f:
            f.write(dashboard_html)
        # dashboards/latest.html: 固定链接, 始终等于最新看板
        with open(os.path.join(dashboards_dir, 'latest.html'), 'w', encoding='utf-8') as f:
            f.write(dashboard_html)
        dashboard_date = date_str
        print(f"  [publish] 已归档决策看板 {date_str} → {dash_archived}")

    reports = _scan_reports(reports_dir, max_date=date_str)
    generated_now = datetime.now()
    updated_at = (generated_now.strftime('%Y-%m-%d %H:%M') if _fmt_date(generated_now) <= date_str
                  else f'{date_str}（报告口径）')
    index_path = os.path.join(site_dir, 'index.html')
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(_render_index(reports, updated_at, summary, dashboard_date=dashboard_date))

    print(f"  [publish] 已归档 {date_str} → {archived}")
    print(f"  [publish] 首页已重建 ({len(reports)} 期) → {index_path}")
    return archived, index_path


def open_site(url=None):
    """在系统默认浏览器中打开 GitHub Pages 站点。

    本地跑批结束后调用; CI (无桌面) 不应调用。
    url 缺省取 paths.SITE_URL; 可用环境变量 SITE_URL 覆盖。
    返回 True 表示已发起打开, False 表示跳过/失败。
    """
    if url is None:
        try:
            from paths import SITE_URL as _default
            url = _default
        except Exception:
            url = os.environ.get(
                'SITE_URL',
                'https://mengkai666.github.io/quant_factor_tutorial/',
            )
    if not url:
        print('  [publish] SITE_URL 为空, 跳过打开站点')
        return False
    try:
        import webbrowser
        print(f'  🌐 正在浏览器中打开 GitHub Pages: {url}')
        webbrowser.open(url)
        return True
    except Exception as e:
        print(f'  [警告] 打开 GitHub Pages 失败: {e}')
        return False
