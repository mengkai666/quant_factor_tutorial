# -*- coding: utf-8 -*-
"""One-shot public quote collection; preview by default, --record is explicit.

Example (no recording):
  python -B -X utf8 tools/collect_market_phase.py --output-dir output/live-inputs

Every collection also writes a sibling HTML summary. Only a successfully
recorded final decision/readiness may supply its action conclusion.
There is deliberately no --captured-at/--now or historical replay switch.
Tests/embedding callers can inject client, now, history_loader and record_call.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from live_market_inputs import SHANGHAI, collect_live_market_inputs, load_cached_universe, phase_at

# Existing paths.py filenames, read-only here. Importing paths runs CSV healing
# and creates directories, which a preview/probe must not do. The recorder itself
# is imported lazily, only for an explicitly requested, qualified --record call.
DATA = ROOT / "data"


def load_latest_prediction(history_path: str | Path, *, report_date: str | None = None) -> dict[str, Any] | None:
    """Latest report, last appended revision; never fall back to a greener revision."""
    path = Path(history_path)
    if not path.exists():
        return None
    latest = None
    with path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("event_type") != "prediction":
                continue
            date = str(row.get("report_date") or "")
            if report_date is not None and date != report_date:
                continue
            if latest is None or date >= str(latest.get("report_date") or ""):
                latest = row
    return latest


def _safe_destinations(output: Path, history: Path, universe: Path, phase_history: Path, *, record: bool) -> None:
    forbidden = [ROOT / name for name in ("data", "src", "tools", "tests", "docs", ".git")]
    forbidden.extend((history.parent, universe.parent))
    if output == ROOT or any(output.is_relative_to(folder.resolve()) for folder in forbidden):
        raise ValueError("output-dir must be isolated from source, docs, git and input caches")
    if record and phase_history in {history, universe}:
        raise ValueError("phase-history must not alias prediction history or universe cache")


def _record_time(now: Callable[[], datetime] | None) -> datetime:
    current = now() if now is not None else datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Record clock must be timezone-aware")
    return current.astimezone(SHANGHAI)


def _block(result: dict[str, Any], reason: str, status: str) -> None:
    result["issues"].append(reason)
    result.update(status=status, record_eligible=False, record_payload=None)
    result["recording"].update(status="blocked", reason=reason, recorded=False)


def _record_if_current(
    result: dict[str, Any], prediction: dict[str, Any], *, history: Path, phase_history: Path,
    calendar: Path, validation: Path, history_loader: Callable, now: Callable | None,
    record_call: Callable | None,
) -> int:
    if not result["record_eligible"]:
        result["recording"].update(status="blocked", reason=result["status"], recorded=False)
        return 2
    if record_call is None:
        from phase_monitor import record_phase_observation
        record_call = record_phase_observation
    # Import latency is included in the final clock and history checks.
    # Pin the exact revision/context as well as the ID; phase_monitor also checks
    # the prediction_id before appending. This is a preflight, not a history lock.
    current_prediction = history_loader(history, report_date=result["binding"]["report_date"])
    if current_prediction != prediction:
        _block(result, "prediction_changed_before_record", "unbound")
        return 2
    current = _record_time(now)
    result["recording"]["checked_at"] = current.isoformat()
    payload = result["record_payload"]
    observed = datetime.fromisoformat(result["source_as_of"])
    fetched = datetime.fromisoformat(result["fetched_at"])
    if current < fetched or current.date().isoformat() != payload["trade_date"] or phase_at(current) != payload["phase"]:
        _block(result, "record_time_outside_collection_window", "outside_window")
        return 2
    if (current - observed).total_seconds() > result["max_age_seconds"]:
        _block(result, "source_became_stale_before_record", "stale")
        return 2
    response = record_call(
        history_path=history, phase_snapshot_path=phase_history, calendar_cache=calendar,
        validation_path=validation, **payload,
    )
    snapshot = response["snapshot"]
    # Retain the post-replay decision; never rebuild from prediction["decision_context"].
    result["decision"] = response.get("decision") if isinstance(response.get("decision"), dict) else None
    result["decision_context_status"] = response.get("decision_context_status")
    result["recording"].update(status="recorded", recorded=True, snapshot_id=snapshot.get("snapshot_id"),
                                appended=snapshot.get("appended"), prediction_id=response.get("prediction_id"))
    return 0



def render_collection_summary(result: dict[str, Any]) -> str:
    """Render acquisition facts and ONLY the recorder's final decision/readiness.

    No prediction/context argument, qualification refresh or decision builder is
    accepted here. A missing final decision is an explicit evidence-only result.
    """
    def obj(value):
        return value if isinstance(value, dict) else {}

    def esc(value):
        return escape("未知" if value is None or value == "" else str(value), quote=True)

    def permission(value, yes, no):
        return yes if value is True else no if value is False else "未知（未返回）"

    binding, coverage = obj(result.get("binding")), obj(result.get("coverage"))
    recording, quality = obj(result.get("recording")), obj(result.get("quality"))
    recorded = recording.get("status") == "recorded" and recording.get("recorded") is True
    status_labels = {"ready": "采集就绪", "outside_window": "时窗外", "stale": "来源过期",
                     "partial": "新鲜覆盖不足", "unavailable": "输入不可用", "unbound": "预测绑定未通过"}
    phase_labels = {"auction": "竞价", "early_0935": "9:35", "confirm_1000": "10:00", "afternoon": "午后"}
    reasons = {
        "outside_window": "当前不在约定的盘中时间窗；不回填早盘或午后观测。",
        "stale": "行情来源时间已过期，不能作为当前阶段确认。",
        "incomplete_market_coverage": "缓存股票池的同日新鲜覆盖不足，不把样本放大为全市场结论。",
        "source_outside_phase_window": "实际来源时间不属于本次阶段，不以采集时间替代。",
        "prediction_changed_before_record": "回写前预测已变更，本次不沿用旧版本。",
        "prediction_published_after_observation": "预测发布时间晚于行情观察，不能用于回填更早阶段。",
        "record_time_outside_collection_window": "回写前已离开本次真实时间窗，停止追加。",
        "source_became_stale_before_record": "回写前行情已过期，停止追加。",
        "unbound": "报告日、目标日或预测版本未通过绑定检查。",
        "unavailable": "未取得可用的同日新鲜输入。",
    }
    if recorded:
        notice = "阶段已回写；以下行动结论直接来自 recorder 返回的最终 decision/readiness。"
        if recording.get("appended") is False:
            notice += " 同一快照已存在，未重复追加。"
    elif recording.get("status") == "failed_may_have_written":
        notice = "回写调用失败，可能已部分写入；请核对记录，不自动重试。本摘要不生成行动许可。"
    elif recording.get("status") == "not_requested":
        notice = "仅采集：未指定 --record，未回写阶段历史，也未更新行动许可。"
    else:
        notice = "未回写：采集或回写前检查未通过；不据此更新行动许可。"
    reason_codes = list(result.get("issues") or []) + list(binding.get("issues") or [])
    if not recorded and result.get("status") not in {None, "ready"}:
        reason_codes.insert(0, result["status"])
    if quality.get("freshness_level") in {"stale", "mixed"}:
        reason_codes.append("stale")
    if recording.get("reason"):
        reason_codes.append(recording["reason"])
    reason_html = "".join(f"<li>{esc(reasons.get(code, code))}</li>"
                          for code in dict.fromkeys(code for code in reason_codes if isinstance(code, str)))
    facts = [
        ("报告日 → 目标交易日", f'{binding.get("report_date") or "未知"} → {binding.get("target_trade_date") or "未知"}'),
        ("预测 ID（沿用，不重新生成）", binding.get("prediction_id")),
        ("采集开始", result.get("request_started_at")), ("采集完成 fetched_at", result.get("fetched_at")),
        ("来源截至 source_as_of（最早）", result.get("source_as_of")),
        ("来源截至 source_as_of_max（最晚）", result.get("source_as_of_max")),
        ("实际采集阶段", phase_labels.get(result.get("phase"), "时窗外／未形成阶段")),
    ]
    facts_html = "".join(f"<div><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>" for label, value in facts)
    population = esc(coverage.get("universe_count"))
    requested, returned, fresh = (esc(coverage.get(key)) for key in ("requested_count", "returned_count", "fresh_count"))
    coverage_html = (f"<p>请求：{requested} / {population} · 返回：{returned} / {requested} · 同日新鲜：{fresh} / {population}</p>"
                     "<p class=muted>口径：缓存沪深北 A 股股票池，并非已核验的最新上市全名录；缺失不当作零。</p>")
    metrics = obj(result.get("metrics"))
    metric_labels = {"up_count": "上涨家数", "down_count": "下跌家数", "flat_count": "平盘家数",
                     "breadth_ratio": "广度", "limit_up": "涨停家数", "limit_down": "跌停家数", "promotion_rate": "昨日梯队晋级率"}
    metric_items = []
    for key, label in metric_labels.items():
        if key in metrics:
            value = metrics[key]
            if key.endswith("_ratio") or key == "promotion_rate":
                value = f"{value:.1%}" if isinstance(value, (int, float)) and not isinstance(value, bool) else value
            metric_items.append(f"{esc(label)}：{esc(value)}")
    metrics_html = "<p>" + (" · ".join(metric_items) or "无可用的同日新鲜指标；原始行情与缺失原因保留在 JSON。") + "</p>"
    decision = obj(result.get("decision")) if recorded else {}
    readiness = obj(decision.get("readiness"))
    if decision and readiness:
        from decision_readiness import render_decision_readiness
        # Pure renderer: never pass old ctx into build_today_decision/_refresh_scoped_context.
        action_html = ("<section><h2>最终行动摘要</h2>"
                       f'<p class="headline">{esc(decision.get("default_action"))}</p>'
                       f'<p>主线：{esc(decision.get("mainline"))} · 最终模型参考仓位：{esc(decision.get("position"))}（不代表新增仓位许可）</p>'
                       f'<p>计划许可：{esc(permission(readiness.get("plan_permitted"), "条件许可", "未许可"))} · '
                       f'执行状态：{esc(permission(readiness.get("execution_ready"), "已就绪", "不可执行"))}</p>'
                       + render_decision_readiness(readiness) + "</section>")
        qualified = obj(obj(decision.get("strategy_qualification")).get("strategies"))
        qualification_rows = []
        for key, row in qualified.items():
            if not isinstance(row, dict):
                continue
            issues = "；".join(item for item in row.get("issues", []) if isinstance(item, str))
            qualification_rows.append(
                f'<li>{esc(row.get("title") or key)} · {esc(row.get("status"))} · '
                f'{esc(permission(row.get("plan_permitted"), "条件许可", "未许可"))}'
                + (f'<br><span class=muted>{esc(issues)}</span>' if issues else "") + "</li>")
        if qualification_rows:
            action_html += ("<details><summary>最终策略资格（只展示回写结果，不重新计算）</summary><ul>"
                            + "".join(qualification_rows) + "</ul></details>")
    elif recorded:
        action_html = ("<section><h2>行动结论未提供</h2><p>recorder 未返回最终决策 decision/readiness，"
                       "仅展示事实；不从原始旧 context 推导或恢复行动许可。</p></section>")
    else:
        action_html = "<section><h2>未更新行动结论</h2><p>本次只展示采集事实，不展示旧预测中的操作许可。</p></section>"
    return ("<!doctype html><html lang=zh-CN><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>盘中采集与行动摘要</title><style>body{margin:0;background:#0d1117;color:#e6edf3;font:15px/1.7 system-ui,'Microsoft YaHei',sans-serif}"
            "main{max-width:1040px;margin:auto;padding:28px 20px}h1{font-size:26px;margin:0 0 8px}h2{font-size:18px}"
            ".muted,dt{color:#8b949e;font-size:13px}.notice,section,details{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin:16px 0}"
            ".notice{border-left:4px solid #d29922}.headline{font-size:20px;font-weight:700}.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}"
            "dd{margin:4px 0;overflow-wrap:anywhere}summary{cursor:pointer}p{margin:8px 0}</style></head><body><main>"
            "<h1>盘中采集与行动摘要</h1>"
            f'<p class=muted>{esc(status_labels.get(result.get("status"), result.get("status")))}</p>'
            f'<div class=notice><strong>{esc(notice)}</strong>' + (f"<ul>{reason_html}</ul>" if reason_html else "") + "</div>"
            f'<dl class=facts>{facts_html}</dl><section><h2>来源与覆盖</h2>{coverage_html}{metrics_html}</section>'
            + action_html + '<p class=muted>只展示已观察事实及最终回写结论；不回填过去时点，不代表委托或成交。</p></main></body></html>')


def main(
    argv: list[str] | None = None, *, client: Any = None, now: Callable[[], datetime] | None = None,
    history_loader: Callable | None = None, record_call: Callable | None = None,
) -> int:
    """Inject dependencies and pass tmp paths to exercise this real entry offline.

    Exit 0: collection/record succeeded (inspect status for preview quality).
    Exit 2: bad arguments or --record was blocked. Exit 3: record failed and may
    have partially appended through the existing recorder; never blindly retry.
    """
    parser = argparse.ArgumentParser(description="Collect current Tencent market inputs once; no schedules, AI, publishing or orders")
    parser.add_argument("--output-dir", required=True, help="Explicit isolated collection-artifact directory")
    parser.add_argument("--record", action="store_true", help="Opt in to record_phase_observation only for fresh bound inputs in the actual window")
    parser.add_argument("--history", default=str(DATA / "report_prediction_history.jsonl"))
    parser.add_argument("--universe-cache", default=str(DATA / "stock_universe.csv"))
    parser.add_argument("--phase-history", default=str(DATA / "market_phase_snapshots.jsonl"))
    parser.add_argument("--calendar-cache", default=str(DATA / "trading_calendar_cache.csv"))
    parser.add_argument("--validation-file", default=str(DATA / "strategy_validation.json"))
    parser.add_argument("--report-date", help="Optional report-date pin (YYYY-MM-DD); defaults to latest report")
    parser.add_argument("--trade-date", help="Optional target pin; must equal the prediction target and real collection day")
    parser.add_argument("--prediction-id", help="Optional exact prediction revision pin")
    parser.add_argument("--phase", choices=["auction", "early_0935", "confirm_1000", "afternoon"], help="Optional window pin, never a backdating request")
    parser.add_argument("--codes", help="Optional comma-separated cached-universe sample; never treated as full-market coverage")
    parser.add_argument("--max-age-seconds", type=int, default=120, help="Source timestamp age ceiling (1..300, default 120)")
    args = parser.parse_args(argv)
    history, universe_path = Path(args.history).resolve(), Path(args.universe_cache).resolve()
    output, phase_history = Path(args.output_dir).resolve(), Path(args.phase_history).resolve()
    loader = history_loader or load_latest_prediction
    try:
        _safe_destinations(output, history, universe_path, phase_history, record=args.record)
        prediction = loader(history, report_date=args.report_date)
        if prediction is None:
            raise ValueError("No prediction/context found in the supplied history")
        target = args.trade_date or prediction.get("target_trade_date")
        universe = load_cached_universe(universe_path, trade_date=target)
        result = collect_live_market_inputs(
            universe=universe, prediction=prediction, client=client, now=now,
            report_date=args.report_date, trade_date=args.trade_date, prediction_id=args.prediction_id,
            phase=args.phase, codes=args.codes.split(",") if args.codes is not None else None,
            max_age_seconds=args.max_age_seconds,
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    collection_id = uuid.uuid4().hex
    result["collection_id"] = collection_id
    result["recording"] = {"requested": args.record, "status": "pending" if args.record else "not_requested", "recorded": False}
    if result["record_payload"] is not None:
        result["record_payload"]["run_id"] = "live-inputs:" + collection_id
    result["input_paths"] = {"history": str(history), "universe_cache": str(universe_path)}
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(result["fetched_at"]).strftime("%Y%m%dT%H%M%S")
    artifact = output / f"market-inputs-{stamp}-{collection_id}.json"
    summary = artifact.with_suffix(".html")
    result["summary_path"] = str(summary)

    def encoded() -> str:
        return json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + "\n"

    # Keep the acquisition proof even if the optional recorder subsequently fails.
    with artifact.open("x", encoding="utf-8") as handle:
        handle.write(encoded())
    exit_code = 0
    if args.record:
        try:
            exit_code = _record_if_current(
                result, prediction, history=history, phase_history=phase_history,
                calendar=Path(args.calendar_cache).resolve(), validation=Path(args.validation_file).resolve(),
                history_loader=loader, now=now, record_call=record_call,
            )
        except Exception as exc:
            result["recording"].update(status="failed_may_have_written", recorded=None, error=type(exc).__name__)
            exit_code = 3
        artifact.write_text(encoded(), encoding="utf-8")
    with summary.open("x", encoding="utf-8") as handle:
        handle.write(render_collection_summary(result))
    print(json.dumps({"artifact_path": str(artifact), "summary_path": str(summary), "status": result["status"], "phase": result["phase"],
                      "source_as_of": result["source_as_of"], "fetched_at": result["fetched_at"],
                      "metrics": result["metrics"], "recording": result["recording"]}, ensure_ascii=False, allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
