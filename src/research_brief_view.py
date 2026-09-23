"""Pure, dependency-free HTML view of the research-brief/v1 data contract.

The model owns selection, ranking and dates. This module only formats its
public presentation fields; it never reads inputs, writes reports or publishes.
"""

from html import escape
from math import isfinite


_CSS = """
.research-brief { background:#F5F7FB; color:#16263B; font:15px/1.65 "Segoe UI","Microsoft YaHei",sans-serif; padding:clamp(16px,3vw,40px); }
.research-brief *, .research-brief *::before, .research-brief *::after { box-sizing:border-box; }
.research-brief .rb-content { max-width:1200px; margin:auto; }
.research-brief h1, .research-brief h2, .research-brief h3, .research-brief h4, .research-brief p, .research-brief dl { margin:0; }
.research-brief h1 { font-size:clamp(26px,4vw,40px); line-height:1.25; letter-spacing:.02em; }
.research-brief h2 { font-size:23px; line-height:1.4; margin-bottom:18px; }
.research-brief h3 { font-size:20px; line-height:1.4; }
.research-brief h4 { font-size:18px; line-height:1.5; }
.research-brief .rb-kicker { color:#087E8B; font-weight:600; margin-bottom:8px; }
.research-brief .rb-dates { display:flex; flex-wrap:wrap; gap:8px 24px; margin-top:16px; }
.research-brief time, .research-brief code, .research-brief .rb-close, .research-brief .rb-quotes dd, .research-brief .rb-market-facts dd, .research-brief .rb-rank { font-family:Consolas,"Microsoft YaHei",monospace; font-variant-numeric:tabular-nums; }
.research-brief code { font-size:13px; }
.research-brief nav { display:flex; flex-wrap:wrap; gap:8px 22px; border-bottom:1px solid #DCE3ED; padding:18px 0; margin-bottom:28px; }
.research-brief a { color:#087E8B; text-decoration:underline; text-underline-offset:4px; }
.research-brief a:hover { color:#16263B; }
.research-brief :focus-visible { outline:3px solid #087E8B; outline-offset:4px; }
.research-brief .rb-section { margin-top:32px; scroll-margin-top:20px; }
.research-brief .rb-market-facts { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; padding:20px 24px; background:#FFFFFF; border:1px solid #DCE3ED; border-radius:8px; }
.research-brief dt { font-size:12px; font-weight:600; }
.research-brief dd { margin:0; }
.research-brief .rb-market-facts dd { font-size:26px; font-weight:600; }
.research-brief .rb-narrative { margin-top:16px; max-width:90ch; }
.research-brief .rb-narrative p + p { margin-top:8px; }
.research-brief .rb-sector { background:#FFFFFF; border:1px solid #DCE3ED; border-radius:8px; margin-top:20px; overflow:hidden; }
.research-brief .rb-sector-header { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:16px; padding:20px 24px; border-bottom:2px solid #087E8B; }
.research-brief .rb-rank { color:#087E8B; font-size:24px; margin-right:12px; }
.research-brief .rb-sector-metrics { display:flex; flex-wrap:wrap; gap:8px 20px; }
.research-brief .rb-sector-metrics dd { font-variant-numeric:tabular-nums; }
.research-brief .rb-stocks { list-style:none; margin:0; padding:0; }
.research-brief .rb-stock { display:grid; grid-template-columns:minmax(210px,1fr) minmax(0,2fr); gap:24px; padding:22px 24px; }
.research-brief .rb-stock + .rb-stock { border-top:1px solid #DCE3ED; }
.research-brief .rb-stock-identity, .research-brief .rb-notes > div { min-width:0; overflow-wrap:anywhere; }
.research-brief .rb-subsector { font-size:12px; margin-top:4px; }
.research-brief .rb-height { color:#087E8B; font-size:12px; margin-left:10px; white-space:nowrap; }
.research-brief .rb-quotes { display:flex; flex-wrap:wrap; gap:8px 18px; margin-top:14px; }
.research-brief .rb-quotes dd { font-size:16px; font-weight:600; }
.research-brief .rb-notes { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px 24px; align-content:start; }
.research-brief .rb-notes dt { color:#087E8B; margin-bottom:3px; }
.research-brief .rb-notes dd { font-size:14px; }
.research-brief .rb-up { color:#B74248; }
.research-brief .rb-down { color:#277B62; }
.research-brief .rb-st { display:inline-block; color:#B74248; border:1px solid #B74248; border-radius:4px; font-size:11px; font-weight:700; line-height:1.5; padding:1px 5px; margin-left:8px; vertical-align:middle; }
.research-brief .rb-recent-count { margin-bottom:12px; }
.research-brief .rb-more { margin-top:14px; }
.research-brief .rb-more summary { cursor:pointer; color:#087E8B; font-weight:600; padding:12px 0; }
.research-brief .rb-table-scroll { max-width:100%; overflow-x:auto; background:#FFFFFF; border:1px solid #DCE3ED; border-radius:8px; }
.research-brief table { border-collapse:collapse; width:100%; min-width:1140px; font-size:12px; }
.research-brief caption { text-align:left; padding:16px; font-size:12px; }
.research-brief th, .research-brief td { padding:12px 10px; border-top:1px solid #DCE3ED; text-align:center; vertical-align:middle; white-space:nowrap; font-variant-numeric:tabular-nums; }
.research-brief thead th { background:#F5F7FB; font-weight:600; }
.research-brief th[scope="row"] { text-align:left; font-weight:600; }
.research-brief th code, .research-brief .rb-state { display:block; font-weight:400; }
.research-brief .rb-state { font-size:11px; margin-top:4px; }
.research-brief .rb-limit { color:#087E8B; font-weight:700; }
.research-brief .rb-unknown { opacity:.7; }
.research-brief .rb-next-list { list-style:none; padding:0; margin:0; border-top:2px solid #087E8B; }
.research-brief .rb-next-list li { padding:16px 0; border-bottom:1px solid #DCE3ED; }
.research-brief .rb-next-list h3 { font-size:16px; margin-bottom:6px; }
@media (max-width:760px) {
  .research-brief .rb-market-facts { grid-template-columns:repeat(2,minmax(0,1fr)); padding:16px; }
  .research-brief .rb-stock { grid-template-columns:minmax(0,1fr); gap:16px; padding:18px 16px; }
  .research-brief .rb-sector-header { padding:16px; }
  .research-brief .rb-notes { grid-template-columns:minmax(0,1fr); gap:12px; }
  .research-brief .rb-sector-metrics { gap:8px 16px; }
}
@media print {
  .research-brief { background:#FFFFFF; padding:0; font-size:10pt; }
  .research-brief nav { display:none; }
  .research-brief h1 { font-size:24pt; }
  .research-brief h2 { font-size:16pt; break-after:avoid; }
  .research-brief .rb-section { margin-top:18px; }
  .research-brief .rb-sector { overflow:visible; }
  .research-brief .rb-sector-header { padding:10px; break-after:avoid; }
  .research-brief .rb-stock { padding:12px 10px; gap:16px; break-inside:avoid; }
  .research-brief .rb-notes dd { font-size:9pt; }
  .research-brief .rb-table-scroll { overflow:visible; }
  .research-brief table { min-width:0; font-size:7pt; table-layout:fixed; }
  .research-brief th, .research-brief td { padding:6px 3px; white-space:normal; overflow-wrap:anywhere; }
  .research-brief th code, .research-brief th time { font-size:7pt; }
  .research-brief thead { display:table-header-group; }
  .research-brief tr, .research-brief .rb-next-list li { break-inside:avoid; }
}
"""


def _e(value):
    return escape("" if value is None else str(value), quote=True)


def _number(value, places=0, unit="", *, signed=False, scale=1):
    if value is None:
        return ""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return _e(value)
    if not isfinite(value):
        return ""
    value *= scale
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.{places}f}{_e(unit)}"


def _amount(value):
    if isinstance(value, (int, float)):
        for divisor, unit in ((100000000, "亿元"), (10000, "万元")):
            if abs(value) >= divisor:
                return _number(value / divisor, 2, unit)
    return _number(value, 2, "元")


def _percent(value):
    formatted = _number(value, 2, "%", signed=True)
    tone = ""
    if isinstance(value, (int, float)):
        tone = "rb-up" if value > 0 else "rb-down" if value < 0 else ""
    return f'<span class="{tone}">{formatted}</span>' if formatted else ""


def _date(value):
    return f'<time datetime="{_e(value)}">{_e(value)}</time>' if value else ""


def _dl(items, class_name):
    """Only accept already-escaped or internally formatted display values."""
    entries = "".join(
        f"<div><dt>{_e(label)}</dt><dd>{value}</dd></div>"
        for label, value in items if value != ""
    )
    return f'<dl class="{class_name}">{entries}</dl>'


def _section(identifier, title, content):
    return (
        f'<section id="{identifier}" class="rb-section" aria-labelledby="{identifier}-title">'
        f'<h2 id="{identifier}-title">{title}</h2>{content}</section>'
    )


def _st_badge(stock):
    return '<span class="rb-st" aria-label="ST风险标记">ST</span>' if stock.get("is_st") else ""


def _market(market):
    facts = _dl([
        ("涨停家数", _number(market.get("limit_up"), unit="只")),
        ("跌停家数", _number(market.get("limit_down"), unit="只")),
        ("最高连板", _number(market.get("max_height"), unit="板")),
        ("上涨占比", _number(market.get("breadth_ratio"), 1, "%", scale=100)),
    ], "rb-market-facts")
    narrative = "".join(f"<p>{_e(line)}</p>" for line in market.get("narrative", []))
    return facts + f'<div class="rb-narrative">{narrative}</div>'


def _stock_row(stock, sector_name):
    height = _number(stock.get("current_height"), unit="连板")
    height_label = "首板" if stock.get("current_height") == 1 else f"当前{height}"
    height_tag = f'<span class="rb-height">{height_label}</span>' if height else ""
    figures = _dl([
        ("收盘", _number(stock.get("close"), 2, "元")),
        ("近5日涨幅", _percent(stock.get("pct_5d"))),
        ("成交额", _amount(stock.get("amount"))),
    ], "rb-quotes")
    notes = _dl([
        (label, _e(stock.get(key)))
        for label, key in (("排序理由", "reason"), ("风险", "risk"),
                           ("观察", "watch"), ("取消观察", "cancel"))
    ], "rb-notes")
    return (
        f'<li class="rb-stock" data-sector="{_e(sector_name)}" data-code="{_e(stock["code"])}">'
        '<div class="rb-stock-identity">'
        f'<h4>{_e(stock["name"])}{_st_badge(stock)}</h4>'
        f'<code>{_e(stock["code"])}</code>{height_tag}'
        f'<p class="rb-subsector">{"题材：" if stock.get("sub_sector") else ""}{_e(stock.get("sub_sector"))}</p>{figures}'
        f'</div>{notes}</li>'
    )


def _sectors(sectors):
    panels = []
    for sector in sectors:
        name = sector["name"]
        metrics = _dl([
            ("涨停", _number(sector.get("limit_up_count"), unit="只")),
            ("连板", _number(sector.get("streak_count"), unit="只")),
            ("最高", _number(sector.get("max_height"), unit="板")),
            ("涨停样本占比", _number(sector.get("share_pct"), 1, "%")),
            ("较前一交易日", _number(sector.get("change"), unit="只", signed=True)),
        ], "rb-sector-metrics")
        rows = "".join(_stock_row(stock, name) for stock in sector["stocks"])
        panels.append(
            f'<article class="rb-sector" data-sector="{_e(name)}">'
            f'<header class="rb-sector-header"><h3><span class="rb-rank">#{_number(sector.get("rank"))}</span>'
            f'{_e(name)}</h3>{metrics}</header><ol class="rb-stocks">{rows}</ol></article>'
        )
    return "".join(panels)


def _trajectory_cell(point):
    state = point.get("limit_up")
    if state is True:
        height = _number(point.get("height"), unit="板")
        label, class_name = "涨停" + (f" · {height}" if height else ""), "rb-limit"
    elif state is False:
        label, class_name = "未涨停", "rb-absent"
    else:
        label, class_name = "未覆盖", "rb-unknown"
    return f'<td><span class="{class_name}">{label}</span></td>'


def _recent_table(stocks, days, caption):
    headers = ["股票", "板块", "当日收盘", "当前连板", "窗口最高板", "窗口涨停次数", "最近涨停日"]
    headers += [_date(day) for day in days]
    head = "".join(f'<th scope="col">{label}</th>' for label in headers)
    rows = []
    for stock in stocks:
        close = _number(stock.get("close"), 2, "元") or "—"
        cells = "".join(f"<td>{value or '—'}</td>" for value in [
            _e(stock.get("sector")), f'<span class="rb-close">{close}</span>',
            _number(stock.get("current_height"), unit="板"),
            _number(stock.get("peak_height"), unit="板"),
            _number(stock.get("limit_up_count"), unit="次"), _date(stock.get("last_limit_date")),
        ])
        # Align by the model's date keys, never by list position; missing is unknown.
        trajectory = {point["date"]: point for point in stock.get("trajectory", [])}
        cells += "".join(_trajectory_cell(trajectory.get(day, {})) for day in days)
        rows.append(
            f'<tr data-recent-code="{_e(stock["code"])}"><th scope="row">'
            f'{_e(stock["name"])}{_st_badge(stock)}<code>{_e(stock["code"])}</code>'
            f'<span class="rb-state">{_e(stock.get("state"))}</span></th>{cells}</tr>'
        )
    return (
        '<div class="rb-table-scroll" tabindex="0" role="region" aria-labelledby="rb-recent-title">'
        f'<table><caption>{_e(caption)}</caption><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def _recent(recent):
    stocks = recent.get("stocks", [])
    more_stocks = recent.get("more_stocks", [])
    if not stocks and not more_stocks:
        if recent.get("complete") is not True:
            covered = len(recent.get("coverage_days") or [])
            total = len(recent.get("window_days") or [])
            scope = f"仅覆盖{covered}/{total}个交易日" if total else "历史窗口尚未读取"
            return f"<p>{scope}，多板统计暂不展示。</p>"
        return "<p>本次未列出近期多板观察股。</p>"
    days = recent.get("window_days", [])
    shown_count = len(stocks)
    expanded_count = shown_count + len(more_stocks)
    total_count = _number(recent.get("total_count", expanded_count))
    content = f'<p class="rb-recent-count">共{total_count}只 · 默认展示{shown_count}只</p>'
    caption = "当前连板、窗口最高板与窗口涨停次数分别统计。未涨停仅表示未在当日完整涨停池中；未覆盖不作判断。"
    if recent.get("complete") is False:
        caption += f' 已覆盖{len(recent.get("coverage_days", []))}/{len(days)}个交易日。'
    if stocks:
        content += _recent_table(stocks, days, caption)
    if more_stocks:
        more_caption = f"其余{len(more_stocks)}只 · 当日收盘与逐日涨停轨迹。"
        content += (
            '<details class="rb-more"><summary>'
            f'查看其余{len(more_stocks)}只（展开后共展示{expanded_count}只）</summary>'
            f'{_recent_table(more_stocks, days, more_caption)}</details>'
        )
    return content


def render_research_brief(brief, *, embedded=False) -> str:
    """Return a standalone document or a scoped-style section, without I/O.

    Input is the already-ranked public model. Optional figures are omitted,
    zero remains zero, and private/provenance fields are never serialized.
    """
    title = _e(brief["title"])
    dates = "".join(
        f'<span>{label} {_date(brief.get(key))}</span>'
        for label, key in (("报告日", "report_date"), ("下一交易日", "target_trade_date"))
        if brief.get(key)
    )
    navigation = "".join(
        f'<a href="#{identifier}">{label}</a>'
        for identifier, label in (("rb-market", "市场"), ("rb-sectors", "板块三股"),
                                  ("rb-recent", "近期多板"), ("rb-next", "次日观察"))
    )
    next_notes = "".join(
        f'<li><h3>{_e(note["title"])}</h3><p>{_e(note["detail"])}</p></li>'
        for note in brief.get("next_session", [])
    )
    sections = (
        _section("rb-market", "市场事实与判断", _market(brief.get("market", {})))
        + _section("rb-sectors", "多板块三股", _sectors(brief.get("sectors", [])))
        + _section("rb-recent", "近期多板轨迹", _recent(brief.get("recent", {})))
        + _section("rb-next", "次日观察", f'<ol class="rb-next-list">{next_notes}</ol>')
    )
    fragment = (
        f'<section class="research-brief" lang="zh-CN" data-purpose="{_e(brief["purpose"])}" aria-labelledby="rb-title">'
        + (f"<style>{_CSS}</style>" if embedded else "")
        + '<div class="rb-content"><header><p class="rb-kicker">收盘研究 · 板块观察</p>'
        + f'<h1 id="rb-title">{title}</h1><div class="rb-dates">{dates}</div></header>'
        + f'<nav aria-label="简报导航">{navigation}</nav>{sections}</div></section>'
    )
    if embedded:
        return fragment
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{title}</title><style>{_CSS}</style></head>'
        f'<body style="margin:0"><main>{fragment}</main></body></html>'
    )
