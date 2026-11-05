"""只读同日转债数据，生成事实对照与条件观察预案。"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

try:
    from .cb_plan_data import DATA_DIR, OUTPUT_DIR, load_cb_plan_data, normalize_date, number
except ImportError:
    from cb_plan_data import DATA_DIR, OUTPUT_DIR, load_cb_plan_data, normalize_date, number

SITE_DIR = Path(OUTPUT_DIR) / "site"


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _fmt(value: Any, digits: int = 2, suffix: str = "", signed: bool = False) -> str:
    n = number(value)
    return "—" if n is None else f"{n:+.{digits}f}{suffix}" if signed else f"{n:.{digits}f}{suffix}"


def _pct(value: Any) -> str:
    n = number(value)
    cls = "cb-rise" if n is not None and n > 0 else "cb-fall" if n is not None and n < 0 else "cb-muted"
    return f'<span class="{cls}">{_fmt(n, suffix="%", signed=True)}</span>'


def _load(report_date: str, *, output_dir=None, data_dir=None) -> dict:
    return load_cb_plan_data(report_date, output_dir=OUTPUT_DIR if output_dir is None else output_dir,
                             data_dir=DATA_DIR if data_dir is None else data_dir)


def get_dynamic_cb_tracking_data(report_date: str = "") -> tuple[list[dict], str]:
    """兼容旧调用字段；函数保留旧名，但不会请求行情。"""
    data = _load(report_date)
    items = []
    for row in data["rows"]:
        items.append({**row, "bond_code": row["code"], "bond_name": row["name"], "price": row["close"],
                      "pct_day": row["r1"], "pct_3d": number(row.get("r3")), "size_yi": row["remaining_issue_yi"],
                      "stock_price": row.get("stock_close"), "stock_pct": row["stock_r1"],
                      "role": row["classification"], "role_badge": row["classification"],
                      "role_color": "#78b9e8", "status_color": "#a5b7c9",
                      "stock_status": "同日正股涨跌 " + _fmt(row["stock_r1"], suffix="%", signed=True),
                      "theme": "债股联动观察", "qualitative": row["reason"]})
    status = {"ready": "同日数据已核对", "partial": "同日数据部分待补", "invalid": "数据未采用", "unavailable": "同日数据待补"}[data["status"]]
    return items, f"{status} · {data['as_of'] or '日期待确认'}"


_CSS = """
.cb-report{--cb-bg:#101722;--cb-panel:#172331;--cb-line:#314456;--cb-text:#e8edf3;--cb-muted:#a5b7c9;--cb-accent:#78b9e8;--cb-warn:#ebc477;color:var(--cb-text);font:14px/1.75 'Microsoft YaHei UI','Microsoft YaHei',sans-serif;overflow-wrap:anywhere}
.cb-report *{box-sizing:border-box;min-width:0}.cb-report a{color:var(--cb-accent);text-underline-offset:4px}.cb-report a:focus-visible,.cb-report summary:focus-visible,.cb-scroll:focus-visible{outline:2px solid var(--cb-warn);outline-offset:4px}
.cb-report h1,.cb-report h2,.cb-report h3{line-height:1.4;color:var(--cb-text)}.cb-report h1{font-size:clamp(27px,4vw,43px);margin:18px 0 10px;letter-spacing:.02em}.cb-report h2{font-size:21px;margin:0 0 12px}.cb-report h3{font-size:17px;margin:0 0 4px}.cb-report p{margin:8px 0}
.cb-shell{max-width:1400px;margin:0 auto;padding:30px 34px 60px}.cb-header{padding-bottom:26px;border-bottom:2px solid var(--cb-accent)}.cb-kicker{font-family:Bahnschrift,'Microsoft YaHei UI',sans-serif;letter-spacing:.1em;color:var(--cb-accent);font-size:13px}.cb-muted{color:var(--cb-muted)}.cb-warn{color:var(--cb-warn)}.cb-rise{color:#ff9c9c}.cb-fall{color:#6fc8ad}
.cb-date{display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}.cb-date strong{font:600 25px/1.4 Bahnschrift,'Cascadia Mono',monospace;color:var(--cb-text)}.cb-status{border:1px solid var(--cb-line);padding:2px 9px;border-radius:4px;font-size:12px}
.cb-summary{display:flex;flex-wrap:wrap;gap:14px 34px;padding:20px 0;border-bottom:1px solid var(--cb-line)}.cb-summary div{flex:1;min-width:140px}.cb-summary span{display:block;font-size:12px;color:var(--cb-muted)}.cb-summary b{font:600 24px/1.5 Bahnschrift,'Cascadia Mono',monospace}
.cb-section{margin-top:30px}.cb-scroll{overflow-x:auto;border:1px solid var(--cb-line);border-radius:7px}.cb-table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap;font-variant-numeric:tabular-nums}.cb-table th,.cb-table td{padding:13px 14px;text-align:right;border-bottom:1px solid var(--cb-line)}.cb-table th{color:var(--cb-muted);font-weight:500;background:var(--cb-panel)}.cb-table th:first-child,.cb-table td:first-child{text-align:left;position:sticky;left:0;z-index:1;background:var(--cb-bg)}.cb-table th:first-child{background:var(--cb-panel)}.cb-table td:nth-child(10){text-align:left}.cb-table tbody tr:last-child td{border-bottom:0}.cb-table small{display:block;color:var(--cb-muted)}
.cb-plans{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.cb-plan{padding:19px 20px;background:var(--cb-panel);border:1px solid var(--cb-line);border-radius:7px;display:flex;flex-direction:column}.cb-plan .cb-kind{color:var(--cb-accent);font-size:12px}.cb-plan dl{margin:12px 0 0}.cb-plan dt{color:var(--cb-muted);font-size:12px;margin-top:10px}.cb-plan dd{margin:3px 0 0}.cb-plan footer{margin-top:auto;padding-top:14px;font-size:12px;color:var(--cb-muted);border-top:1px solid var(--cb-line)}
.cb-empty{padding:24px;border:1px solid var(--cb-line);background:var(--cb-panel);border-radius:7px}.cb-notes{padding:18px 20px;border-left:3px solid var(--cb-warn);background:var(--cb-panel)}.cb-notes ul{margin:8px 0;padding-left:20px}.cb-sources{margin-top:22px;color:var(--cb-muted);font-size:12px}.cb-sources summary{cursor:pointer}.cb-sources li{margin:7px 0;word-break:break-word}.cb-foot{margin-top:28px;border-top:1px solid var(--cb-line);padding-top:20px}
.cb-teaser{margin:24px 0;padding:22px;background:var(--cb-bg);border:1px solid var(--cb-line);border-radius:8px}.cb-teaser .cb-table{font-size:12px}.cb-teaser .cb-summary{padding:12px 0;margin-bottom:15px}.cb-teaser .cb-summary b{font-size:20px}
@media(max-width:1000px){.cb-plans{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:650px){.cb-shell{padding:20px 16px 36px}.cb-plans{grid-template-columns:1fr}.cb-teaser{padding:16px}.cb-table th,.cb-table td{padding:11px 10px}.cb-summary{gap:14px}.cb-summary div{min-width:125px}.cb-section{margin-top:24px}}
"""


def _summary(data: dict) -> str:
    s = data.get("summary") or {}
    fields = [("本页观察样本", str(len(data["rows"])), "只"),
              ("当日有成交转债", _fmt(s.get("dated_active_nonzero"), 0), "只"),
              ("同日样本总成交", _fmt(s.get("total_amount_yi")), "亿元"),
              ("成交前15只占比", _fmt(s.get("top15_share_pct")), "%")]
    return '<div class="cb-summary">' + "".join(f'<div><span>{label}</span><b>{value}</b> <small>{unit if value != "—" else ""}</small></div>' for label, value, unit in fields) + '</div>'


def _table(rows: list[dict], compact: bool = False) -> str:
    if not rows:
        return '<div class="cb-empty">该基准日没有可用的逐债观察数据。其他日期的价格不在此页展示。</div>'
    headers = ["转债 / 代码", "收盘", "当日", "5日", "10日", "20日", "成交 / 亿", "转股溢价", "余额 / 亿", "正股 / 当日"]
    body = []
    for row in rows:
        body.append('<tr data-source-date="' + _e(row["source_date"]) + '"><td><b>' + _e(row["name"]) + '</b><small>' + _e(row["code"]) + '</small></td>'
                    + '<td>' + _fmt(row["close"], 3) + '</td>'
                    + ''.join('<td>' + _pct(row[key]) + '</td>' for key in ("r1", "r5", "r10", "r20"))
                    + '<td>' + _fmt(row["amount_yi"]) + '</td><td>' + _fmt(row["premium_pct"], suffix="%") + '</td><td>' + _fmt(row["remaining_issue_yi"], 3) + '</td>'
                    + '<td>' + _e(row["stock_name"] or "正股待核") + '<small>' + _e(row["stock_code"]) + ' · ' + _pct(row["stock_r1"]) + '</small></td></tr>')
    return '<div class="cb-scroll" tabindex="0" role="region" aria-label="可横向滚动的同日转债数据表"><table class="cb-table"><thead><tr>' + ''.join('<th scope="col">' + text + '</th>' for text in headers) + '</tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>'


def _plans(rows: list[dict]) -> str:
    cards = []
    for row in rows:
        conversion = row.get("conversion_start") or "待核对"
        cards.append(f'<article class="cb-plan"><div class="cb-kind">{_e(row["classification"])}</div><h3>{_e(row["name"])}</h3>'
                     f'<div class="cb-muted">{_e(row["code"])} · 正股 {_e(row["stock_name"] or "待核对")}</div><p>{_e(row["reason"])}</p>'
                     f'<dl><dt>下一交易日观察</dt><dd>{_e(row["watch"])}</dd><dt>失效情形</dt><dd>{_e(row["invalidation"])}</dd></dl>'
                     f'<footer><p>转股起始：{_e(conversion)}</p><p>{_e(row["redemption_status"])}</p></footer></article>')
    return '<div class="cb-plans">' + ''.join(cards) + '</div>'


def _notes(data: dict) -> str:
    notes = list(data["warnings"])
    notes.extend(["收盘与区间收益均以本页基准日为准；金额为当日累计成交，余额为尚未转股的面值余额。",
                  "转股溢价以同日债价、正股价和当前转股价计算。未到转股期时，理论转股价值不能立即兑现。",
                  "条件预案用于观察，尚无下一交易日盘中确认。T+0不保证成交或退出价格；交易限制、临时停牌与赎回安排按交易所及发行人公告核对。"])
    return '<aside class="cb-notes"><b>数据口径与使用边界</b><ul>' + ''.join('<li>' + _e(note) + '</li>' for note in dict.fromkeys(notes)) + '</ul></aside>'


def _sources(data: dict) -> str:
    items = []
    for source in data["sources"]:
        text = ' · '.join(str(source.get(key)) for key in ("name", "source_date", "source_timestamp", "description", "source", "path") if source.get(key))
        items.append('<li>' + _e(text) + '</li>')
    return '<details class="cb-sources"><summary>查看日期与来源记录</summary><p>来源采集时间：' + _e(data.get("source_timestamp") or "缺失") + '</p><ul>' + ''.join(items) + '</ul></details>'


def render_cb_teaser_card_html(report_date: str = "", is_site_mode: bool = False, prefix: str = "", *,
                               output_dir=None, data_dir=None) -> str:
    data = _load(report_date, output_dir=output_dir, data_dir=data_dir)
    day = data["as_of"]
    href = (f"{prefix}cb_baotuan/{day}.html" if is_site_mode else f"可转债抱团复盘与追踪_{day.replace('-', '')}.html") if day else "可转债抱团复盘与追踪.html"
    date = data["as_of"] or "待确认"
    headline = "同日数据已核对，观察条件待确认" if data["status"] == "ready" else "同日数据部分待补" if data["rows"] else "同日转债数据待补"
    warnings = '<p class="cb-warn">' + _e('；'.join(data['warnings'])) + '</p>' if data["warnings"] else ''
    return ('<!-- cb-tracking-teaser:start --><style>' + _CSS + '</style>'
            + f'<section id="cb-tracking-teaser" class="cb-report cb-teaser" data-as-of="{_e(date)}" data-report-date="{_e(date)}" data-status="{_e(data["status"])}"><h2>可转债：同日数据与条件预案</h2>'
            + f'<p class="cb-muted">数据基准日 {_e(date)} · {_e(headline)}</p>' + warnings + _summary(data) + _table(data["rows"][:6], True)
            + '<p class="cb-muted">成交排序便于比较活跃程度，不代表风险等级。其余观察样本及逐债失效条件见详细报告。</p>'
            + f'<a href="{_e(href)}">查看同日转债与下一交易日观察条件 →</a></section><!-- cb-tracking-teaser:end -->')


def generate_cb_standalone_html(report_date: str = "", *, output_dir=None, data_dir=None) -> str:
    """保留逐债预案速查功能；所有股票名称与市场数据均来自同日记录。"""
    data = _load(report_date, output_dir=output_dir, data_dir=data_dir)
    date = data["as_of"] or "待确认"
    status = {"ready": "同日字段已核对", "partial": "部分字段待补", "invalid": "数据未采用", "unavailable": "同日数据待补"}[data["status"]]
    lead = "比较债股联动、近期收益与转股溢价，再跟踪下一交易日的承接。"
    if not data["rows"]:
        lead = "该基准日缺少合格数据。补齐同日快照后，再形成逐债观察条件。"
    integrity = json.dumps({"as_of": data["as_of"], "status": data["status"], "rows": len(data["rows"]),
                            "source_timestamp": data.get("source_timestamp")}, ensure_ascii=False).replace("<", "\\u003c")
    main_href = f"site/reports/{date}.html" if data["as_of"] else "主线强度追踪.html"
    return f'''<!doctype html><html lang="zh-CN" data-report-date="{_e(date)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>可转债同日观察与条件预案 · {_e(date)}</title><style>html,body{{margin:0;background:#101722}}{_CSS}</style>
<script type="application/json" id="cb-report-integrity">{integrity}</script></head><body>
<main class="cb-report cb-shell" data-as-of="{_e(date)}" data-report-date="{_e(date)}" data-status="{_e(data['status'])}"><header class="cb-header">
<a href="{_e(main_href)}">← 返回同日主线追踪</a><h1>可转债 · 同日观察</h1><p>{_e(lead)}</p>
<div class="cb-date"><span class="cb-muted">数据基准日</span><strong>{_e(date)}</strong><span class="cb-status">{status}</span></div></header>
{_summary(data)}<section class="cb-section"><h2>债股联动与近期表现</h2><p class="cb-muted">区间收益按交易日计算。按成交额展示；缺失值为“—”。</p>{_table(data['rows'])}</section>
<section class="cb-section"><h2>下一交易日 · 条件预案速查</h2><p class="cb-muted">先核对正股与转债是否同步承接。以下均为待观察条件，尚未确认触发。</p>{_plans(data['rows']) if data['rows'] else '<div class="cb-empty">等待同日数据；暂不生成逐债条件预案。</div>'}</section>
<section class="cb-section">{_notes(data)}</section>{_sources(data)}<footer class="cb-foot"><a href="{_e(main_href)}">返回同日主线追踪 →</a></footer></main></body></html>'''


def _can_update_latest(path: Path, day: str, dates: list[str]) -> bool:
    """同时检查归档与入口日期，重建历史时不回退最新入口。"""
    known = list(dates)
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8-sig")
            found = re.search(r'data-report-date=["\'](\d{4}-\d{2}-\d{2})', content)
            if found:
                known.append(found.group(1))
            else:
                title = re.search(r"<title>(.*?)</title>", content, re.S)
                legacy = re.search(r"20\d{2}-\d{2}-\d{2}|20\d{6}", title.group(1)) if title else None
                if legacy and normalize_date(legacy.group()):
                    known.append(normalize_date(legacy.group()))
                elif not known:
                    return False
        except OSError:
            return False
    return not known or day >= max(known)


def export_cb_tracking_reports(report_date: str = "", output_dir=None, site_dir=None, *, data_dir=None) -> dict:
    """仅导出静态文件；支持隔离输出，历史归档不会覆盖较新的入口。"""
    day = normalize_date(report_date)
    if not day:
        raise ValueError("导出转债报告必须指定有效数据基准日")
    output = Path(OUTPUT_DIR if output_dir is None else output_dir)
    site = Path(site_dir) if site_dir is not None else Path(SITE_DIR) if output_dir is None else output / "site"
    document = generate_cb_standalone_html(day, output_dir=output, data_dir=data_dir)
    destinations = {"output_path": output / "可转债抱团复盘与追踪.html",
                    "site_path": site / "cb_baotuan" / "latest.html",
                    "date_path": site / "cb_baotuan" / f"{day}.html",
                    "reports_path": site / "reports" / "可转债抱团复盘与追踪.html"}
    local_dates = [normalize_date(p.stem.replace("可转债抱团复盘与追踪_", "")) for p in output.glob("可转债抱团复盘与追踪_*.html")]
    site_dates = [normalize_date(p.stem) for p in (site / "cb_baotuan").glob("*.html")]
    dates = [value for value in local_dates + site_dates if value]
    local_update = _can_update_latest(destinations["output_path"], day, dates)
    site_update = _can_update_latest(destinations["site_path"], day, dates)
    for path in destinations.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    local_dated = output / f"可转债抱团复盘与追踪_{day}.html"
    local_dated.write_text(document, encoding="utf-8")
    (output / f"可转债抱团复盘与追踪_{day.replace('-', '')}.html").write_text(document, encoding="utf-8")
    source_link = f'href="site/reports/{day}.html"'
    site_document = document.replace(source_link, f'href="../reports/{day}.html"')
    destinations["date_path"].write_text(site_document, encoding="utf-8")
    (site / "cb_baotuan" / f"{day.replace('-', '')}.html").write_text(site_document, encoding="utf-8")
    if local_update:
        destinations["output_path"].write_text(document, encoding="utf-8")
    if site_update:
        destinations["site_path"].write_text(site_document, encoding="utf-8")
        destinations["reports_path"].write_text(document.replace(source_link, f'href="{day}.html"'), encoding="utf-8")
        (site / "可转债抱团复盘与追踪.html").write_text(document.replace(source_link, f'href="reports/{day}.html"'), encoding="utf-8")
    return {**{key: str(path) for key, path in destinations.items()}, "local_dated": str(local_dated),
            "latest_updated": local_update and site_update}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="只读同日缓存并导出转债观察报告")
    parser.add_argument("--date", required=True, help="数据基准日 YYYY-MM-DD")
    args = parser.parse_args()
    print(export_cb_tracking_reports(args.date)["output_path"])
