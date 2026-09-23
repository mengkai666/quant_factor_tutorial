"""Read-only market inputs and isolated, atomic research report exports.

No importing paths.py here: that module can heal live CSVs at import time.
Production callers pass their resolved runtime paths; CLI defaults use the repo.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo
import csv
import gzip
import io
import json
import os
from pathlib import Path
import tempfile

from research_brief import build_research_brief, validate_research_snapshot, _day, _records, _code

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _require_closed_day(day):
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if not day or day > now.date().isoformat() or (day == now.date().isoformat() and now.hour < 15):
        raise ValueError("报告日尚未收盘或位于未来，不能生成盘后事实简报")


def _calendar(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        days = sorted({_day(row.get("trade_date") or row.get("date")) for row in rows
                       if _day(row.get("trade_date") or row.get("date"))})
    if not days:
        raise ValueError("交易日历为空，不能推测最近5个交易日")
    return days


def load_brief_context(*, audit_path=None, snapshot_dir=DATA / "report_daily_snapshots", report_date=None):
    """Use an explicit audit or the newest authoritative daily snapshot."""
    wanted = _day(report_date) if report_date is not None else None
    if report_date is not None and wanted is None:
        raise ValueError("无效报告日期")
    if audit_path is not None:
        payload = _json(audit_path)
        context = payload.get("context") if isinstance(payload, dict) else None
        if not isinstance(context, dict):
            raise ValueError("输入不是带context的历史审计")
        context = deepcopy(context)
        day = _day(context.get("report_date"))
        if wanted and wanted != day:
            raise ValueError("指定日期与审计日期不一致")
        _require_closed_day(day)
        validate_research_snapshot((context.get("facts") or {}).get("market_snapshot"), report_date=day)
        return context
    directory = Path(snapshot_dir)
    if wanted:
        path = directory / (wanted + ".json")
    else:
        candidates = sorted((p for p in directory.glob("*.json") if _day(p.stem)), key=lambda p: _day(p.stem))
        if not candidates:
            raise ValueError("没有可用的权威每日快照")
        path = candidates[-1]
        wanted = _day(path.stem)
    _require_closed_day(wanted)
    snapshot = _json(path)
    validate_research_snapshot(snapshot, report_date=wanted)
    return {"report_date":wanted,"facts":{"market_snapshot":snapshot}}


def _slice_prices(directory, days, codes):
    rows = []
    for day in days:
        path = Path(directory) / (day + ".csv.gz")
        if not path.exists():
            continue
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if _code(row) in codes and _day(row.get("date")) == day:
                    rows.append(row)
    return rows


def prepare_research_brief(context, *, snapshot_dir=DATA / "report_daily_snapshots",
                           price_slices_dir=DATA / "price_slices", calendar_cache=DATA / "trading_calendar_cache.csv",
                           raw_bar_cache_dir=DATA / "raw_ohlc_slices", price_rows=None,
                           fetch_missing=False, provider=None, **options):
    """Load only required historical days; optionally fill selected-stock OHLC."""
    from data_sources.raw_bar_provider import RawBarProvider, load_raw_bars
    for key in ("max_sectors", "stocks_per_sector", "recent_window", "recent_limit"):
        if key in options and (isinstance(options[key], bool) or not isinstance(options[key], int) or options[key] < 1):
            raise ValueError("数量与回看窗口必须为正整数")
    ctx = deepcopy(context)
    day = _day(ctx.get("report_date"))
    _require_closed_day(day)
    current = validate_research_snapshot((ctx.get("facts") or {}).get("market_snapshot"), report_date=day)
    calendar = _calendar(calendar_cache)
    if day not in calendar:
        raise ValueError("报告日期不在交易日历内")
    through = [d for d in calendar if d <= day]
    window = through[-options.get("recent_window", 5):]
    history, covered, skipped = [], [day], []
    codes = {r["code"] for r in current}
    for d in window:
        if d == day:
            continue
        path = Path(snapshot_dir) / (d + ".json")
        try:
            snapshot = _json(path)
            normalized = validate_research_snapshot(snapshot, report_date=d)
        except (OSError, ValueError, TypeError):
            skipped.append(d)
            continue
        if len(through) >= 2 and d == through[-2]:
            ctx.setdefault("facts", {})["previous_market_snapshot"] = snapshot
        history.extend(normalized)
        codes.update(r["code"] for r in normalized)
        covered.append(d)
    if price_rows is None:
        price_rows = _slice_prices(price_slices_dir, through[-max(6, len(window)):], codes)
    else:
        price_rows = _records(price_rows)
    try:
        bars = load_raw_bars(raw_bar_cache_dir, day)
        collection = {"status":"cached","covered":len(bars)}
    except (OSError, ValueError, TypeError):
        bars = []
        collection = {"status":"unavailable","covered":0}
    if not ctx.get("target_trade_date"):
        ctx["target_trade_date"] = next((d for d in calendar if d > day), None)
    kwargs = dict(price_rows=price_rows, history_rows=history, trading_days=calendar,
                  history_days=covered, raw_bars=bars, **options)
    brief = build_research_brief(ctx, **kwargs)
    if fetch_missing:
        selected = sorted({r["code"] for s in brief["sectors"] for r in s["stocks"]}
                          | {r["code"] for r in brief["recent"]["stocks"]})
        if selected:
            try:
                collected = (provider or RawBarProvider()).fetch_day(selected, day, cache_dir=raw_bar_cache_dir)
            except (OSError, ValueError, RuntimeError) as exc:
                collection = {"status":"unavailable","covered":len(bars),"error":type(exc).__name__}
            else:
                collection = {key:deepcopy(value) for key,value in collected.items() if key != "records"}
                by_code = {_code(row):row for row in bars}
                by_code.update({_code(row):row for row in collected.get("records") or [] if isinstance(row,dict)})
                kwargs["raw_bars"] = list(by_code.values())
                # A newly observed contradiction must not restore the old list.
                brief = build_research_brief(ctx, **kwargs)
    brief["provenance"]["ohlc_collection"] = collection
    brief["provenance"]["history_days_skipped"] = skipped
    return brief


def _atomic_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent,
                                         prefix="." + path.name + "-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def render_research_document(brief, *, html=None, quality=None):
    from report_integrity import (build_research_report_integrity, validate_report_integrity,
                                  render_report_integrity_metadata)
    metadata = build_research_report_integrity(brief, quality=quality)
    validate_report_integrity(metadata)
    if html is None:
        from research_brief_view import render_research_brief
        html = render_research_brief(brief)
    date_meta = f'<meta name="report-date" content="{metadata["report_date"]}">'
    html = html.replace("<head>", "<head>" + date_meta, 1) if "<head>" in html else date_meta + html
    tag = render_report_integrity_metadata(metadata)
    return html.replace("</body>", tag + "</body>") if "</body>" in html else html + tag


def _csv_text(rows, fields):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        clean = {}
        for field in fields:
            value = row.get(field)
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            clean[field] = value
        writer.writerow(clean)
    return "\ufeff" + output.getvalue()


def research_output_paths(report_date, output_dir, *, source_paths=()):
    """Resolve and preflight every output before fetching or writing anything."""
    day = _day(report_date)
    if not day:
        raise ValueError("无效报告日期")
    output = Path(output_dir).resolve()
    paths = {"html":output / f"research_brief_{day}.html", "json":output / f"research_brief_{day}.json",
             "watchlist_csv":output / f"sector_watchlist_{day}.csv", "recent_csv":output / f"recent_boards_{day}.csv"}
    protected = {Path(p).resolve() for p in source_paths}
    if any(path.resolve() in protected for path in paths.values()):
        raise ValueError("输出不能覆盖输入文件")
    return paths


def write_research_brief(brief, output_dir, *, html=None, source_paths=(), quality=None):
    day = _day(brief.get("report_date"))
    if not day or brief.get("purpose") != "research_observation":
        raise ValueError("只允许写出明确日期的研究观察简报")
    paths = research_output_paths(day, output_dir, source_paths=source_paths)
    document = render_research_document(brief, html=html, quality=quality)
    watchlist = [{**r,"sector":s["name"],"sector_rank":s["rank"],"rank":rank,
                  "report_date":day,"purpose":"research_observation"}
                 for s in brief["sectors"] for rank,r in enumerate(s["stocks"],1)]
    recent = [{**r,"report_date":day,"purpose":"research_observation"} for r in brief["recent"]["stocks"] + brief["recent"].get("more_stocks", [])]
    contents = {"html":document,"json":json.dumps(brief,ensure_ascii=False,indent=2,allow_nan=False)+"\n",
        "watchlist_csv":_csv_text(watchlist,["report_date","purpose","sector_rank","sector","rank","code","name","source_date","close","pct_5d","current_height","amount","reason","risk","watch","cancel"]),
        "recent_csv":_csv_text(recent,["report_date","purpose","code","name","sector","source_date","close","current_height","peak_height","limit_up_count","last_limit_date","state","is_st"])}
    for key, content in contents.items():
        _atomic_text(paths[key], content)
    return {key:str(path) for key,path in paths.items()}


def write_research_main_report(brief, output_path, *, quality=None):
    """Write the normal report entry without touching any execution CSV or journal."""
    document = render_research_document(brief, quality=quality)
    _atomic_text(output_path, document)
    return document


def research_site_summary(brief):
    """The site headline describes the research brief, not legacy trade permission."""
    return {"stance":"盘后观察", "head":" · ".join(s["name"] for s in brief["sectors"]),
            "play":f"{len(brief['sectors'])}个板块 / {sum(len(s['stocks']) for s in brief['sectors'])}只重点股 · 近期多板{brief['recent']['total_count']}只",
            "note":" ".join(brief["market"]["narrative"][:2]), "color":"#087E8B",
            "report_kind":"research_observation", "report_date":brief["report_date"]}


def research_latest_update_allowed(path, report_date):
    """A historical replay may create its archive, but must not rewind latest."""
    from report_integrity import parse_report_integrity, validate_report_integrity, ReportIntegrityError
    day = _day(report_date)
    if not day:
        raise ValueError("无效的子页日期")
    path = Path(path)
    if not path.exists():
        return True
    existing = path.read_text(encoding="utf-8")
    if "<!-- research-parent:start -->" not in existing:
        raise ValueError("已有latest页面不是本模块子页，不能覆盖")
    try:
        previous = parse_report_integrity(existing)
    except (ValueError, ReportIntegrityError) as exc:
        raise ValueError("无法确认已有latest页面的子页归属") from exc
    if previous.get("schema") != "report-integrity/v2" or previous.get("purpose") != "research_observation":
        raise ValueError("已有latest页面不是研究子页，不能覆盖")
    try:
        previous = validate_report_integrity(previous)
        previous_day = _day(previous.get("report_date"))
    except (ValueError, ReportIntegrityError):
        return True  # Repair metadata in an identified, owned research page.
    return previous_day is None or day >= previous_day


def research_subpage_paths(report_date, output_dir, *, parent_report, source_paths=()):
    """Preflight the subpage and its alias without claiming ownership of a main page."""
    protected = tuple(source_paths) + (parent_report,)
    paths = research_output_paths(report_date, output_dir, source_paths=protected)
    latest = Path(output_dir).resolve() / "latest.html"
    if latest.resolve() in {Path(p).resolve() for p in protected}:
        raise ValueError("子页latest入口不能覆盖主页面或输入")
    research_latest_update_allowed(latest, report_date)
    paths["latest_html"] = latest
    return paths


def write_research_subpage(brief, output_dir, *, parent_report, source_paths=(), quality=None):
    """Create a separate child and latest alias; never write the main report."""
    from research_brief_view import render_research_brief
    from research_subpages import add_research_parent, relative_report_link
    protected = tuple(source_paths) + (parent_report,)
    paths = research_subpage_paths(brief.get("report_date"), output_dir,
                                   parent_report=parent_report, source_paths=source_paths)
    document = add_research_parent(render_research_brief(brief), relative_report_link(parent_report, Path(output_dir)))
    result = write_research_brief(brief, output_dir, html=document, source_paths=protected, quality=quality)
    if research_latest_update_allowed(paths["latest_html"], brief["report_date"]):
        _atomic_text(paths["latest_html"], Path(result["html"]).read_bytes().decode("utf-8"))
    result["latest_html"] = str(paths["latest_html"])
    return result
