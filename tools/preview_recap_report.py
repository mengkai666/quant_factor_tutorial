"""Render the recap closure from an existing audit, without market/AI requests.

All new files (including the simulated journal) stay under the preview output
folder. This never edits the source audit, live market caches or live journal.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_sources.calendar_provider import CalendarProvider
from decision_dashboard import (build_dashboard_ctx, build_today_decision, write_today_focus_pool,
                                generate_dashboard_html, generate_dashboard_section, _action_plan_html)
from decision_readiness import render_decision_readiness
from limit_events import build_limit_event_snapshot, load_limit_event_snapshot
from paths import CALENDAR_CACHE, LIMIT_EVENT_SNAPSHOT_DIR, STRATEGY_VALIDATION_FILE
from strategy_qualification import load_validation_records, qualify_strategies, build_strategy_event_input, scoped_qualification
from recap_panels import (render_decision_changes, render_daily_journal, render_trade_history,
                          render_limit_event_coverage, render_scenario_checkpoint, render_strategy_qualification)
from report_closure import persist_decision_review
from scenario_posterior import build_scenario_posterior_timeline


def build_preview(audit_path, output_dir, *, calendar_cache=CALENDAR_CACHE, history_path=None, validation_path=None):
    audit_path, output = Path(audit_path).resolve(), Path(output_dir).resolve()
    audit = json.loads(audit_path.read_text(encoding="utf-8-sig"))
    context = deepcopy(audit["context"])
    report_date = date.fromisoformat(context["report_date"]).isoformat()
    output.mkdir(parents=True, exist_ok=True)
    target = CalendarProvider(cache_path=calendar_cache).cached_next_trading_day(report_date)
    context["target_trade_date"] = target
    facts = context.setdefault("facts", {})
    snapshot = facts.get("market_snapshot") or {}
    events = load_limit_event_snapshot(LIMIT_EVENT_SNAPSHOT_DIR, report_date)
    if events is None:
        events = facts.get("limit_event_snapshot") or build_limit_event_snapshot(snapshot.get("limit_pool_rows") or [], report_date, source="historical_audit")
    facts["limit_event_snapshot"] = events
    scoped = None
    if validation_path is not None or scoped_qualification(context.get("quality")) is None:
        # An old decision badge/count is not independent authorization. Without
        # explicit evidence, annotate legacy previews as unverified, not approved.
        loaded = load_validation_records(validation_path) if validation_path is not None else {"records": []}
        event_input = facts.get("strategy_event_metrics") or build_strategy_event_input(events, report_date=report_date)
        facts["strategy_event_metrics"] = event_input
        scoped = qualify_strategies(context.get("scenario_plans") or [], quality=context.get("quality") or {},
            validation_records=loaded["records"], event_metrics=event_input,
            report_date=report_date, target_trade_date=target)
        context["quality"]["strategy_qualification"] = scoped
        context["quality"]["publication_mode"] = scoped["publication_mode"]
        context["publication_mode"] = scoped["publication_mode"]
    context["scenario_posterior"] = build_scenario_posterior_timeline(
        context.get("scenario_plans") or [], context.get("phase_snapshots") or [],
        report_date=report_date, trade_date=target,
        prediction_id=(context.get("scenario_posterior") or {}).get("prediction_id") or "unbound-preview",
        eligible_strategy_ids=scoped["eligible_strategy_ids"] if scoped is not None else None,
    )
    ctx = build_dashboard_ctx(report_date=report_date, report_context=context, next_trade_date=target)
    for field, source in (("zt", "limit_up"), ("dt", "limit_down"), ("curr_h", "max_height"), ("breadth_ratio", "breadth_ratio")):
        if snapshot.get(source) is not None:
            ctx[field] = snapshot[source]
    # Older audits do not contain A/D counts; never reconstruct them from a
    # rounded ratio or display the builder's zero defaults as observed counts.
    for field, source in (("up", "up_count"), ("down", "down_count")):
        if source in snapshot:
            ctx[field] = snapshot[source]
        else:
            ctx.pop(field, None)
    decision = build_today_decision(ctx)
    effective_qualification = decision.get("strategy_qualification")
    journal = output / f"preview_journal_{report_date}.jsonl"
    if journal == audit_path or (history_path and journal == Path(history_path).resolve()):
        raise ValueError("preview journal cannot overwrite an input")
    journal.write_bytes(Path(history_path).read_bytes() if history_path else b"")
    review = persist_decision_review(journal, decision)
    ctx.update(review)
    context.update(review, decision_readiness=decision["readiness"], today_decision=decision)
    context["preview_only"] = True
    csv_path = output / f"focus_{report_date}.csv"
    write_today_focus_pool(ctx, csv_path, decision=decision)
    note = (f"离线重算预览 · 报告日 {report_date} / 目标日 {target or '待日历确认'}。"
            "只使用已有历史审计，不抓取行情、不调用AI、不下单；新增决策记录是预览，不是当日实际交易记录。")
    banner = f'<div style="padding:12px;background:#252018;color:#e3b341;font-size:12px;line-height:1.6">{escape(note)}</div>'
    full = generate_dashboard_html(ctx).replace("<body>", "<body>" + banner, 1)
    embedded = generate_dashboard_section(ctx)
    (output / f"dashboard_{report_date}.html").write_text(full, encoding="utf-8")
    (output / f"embedded_{report_date}.html").write_text(embedded, encoding="utf-8")
    panels = (render_decision_readiness(decision["readiness"]) + render_strategy_qualification({"strategy_qualification": effective_qualification}) + render_scenario_checkpoint(ctx, decision["readiness"])
              + render_decision_changes(review["decision_changes"]) + _action_plan_html(decision["action_plan"])
              + render_limit_event_coverage(events) + render_daily_journal(ctx) + render_trade_history(ctx))
    compact = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
               '<title>今日复盘与明日推演 · 离线验收</title><style>body{margin:0;background:#0d1117;color:#c9d1d9;font-family:"Segoe UI","Microsoft YaHei",sans-serif}'
               'main{max-width:1100px;margin:auto;padding:20px}h1{font-size:24px;color:#f0f6fc}td,th{padding:6px;border-bottom:1px solid #30363d;vertical-align:top;overflow-wrap:anywhere}'
               'summary:focus-visible{outline:2px solid #58a6ff}summary{cursor:pointer} @media(max-width:600px){main{padding:12px}h1{font-size:21px}}</style></head><body>'
               + banner + '<main><h1>今日复盘 / 明日推演</h1><p>先看操作结论，再看条件候选；来源与历史记录按需展开。</p>'
               + panels + '</main></body></html>')
    preview = output / f"recap_{report_date}.html"
    preview.write_text(compact, encoding="utf-8")
    validation = {
        "source_audit": str(audit_path), "report_date": report_date, "target_trade_date": target,
        "preview_only": True, "source_publication_mode": audit["context"].get("publication_mode"),
        "publication_mode": decision["readiness"]["publication_mode"], "readiness": decision["readiness"],
        "quality_unchanged": audit["context"]["quality"] == context["quality"],
        "core_modules_unchanged": audit["context"]["quality"].get("modules") == context["quality"].get("modules"),
        "strategy_qualification": effective_qualification,
        "candidate_funnel_fingerprint": decision["candidate_funnel"]["fingerprint"],
        "candidate_codes": [row["code"] for row in decision["candidates"]],
        "source_candidate_funnel_fingerprint": (facts.get("candidate_funnel") or {}).get("fingerprint"),
        "priorities": decision["priority"], "preview": str(preview), "csv": str(csv_path),
    }
    (output / f"validation_{report_date}.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    return validation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--output-dir", default=str(ROOT / "output" / "recap_validation"))
    parser.add_argument("--calendar-cache", default=CALENDAR_CACHE)
    parser.add_argument("--validation-file", help="可选显式验证输入；缺失时也可展示新的逐策略未验证原因")
    parser.add_argument("--history", help="可选的只读历史来源；复制至预览目录后才追加模拟记录")
    args = parser.parse_args()
    result = build_preview(args.audit, args.output_dir, calendar_cache=args.calendar_cache, history_path=args.history, validation_path=args.validation_file)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
