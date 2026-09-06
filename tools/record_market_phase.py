# -*- coding: utf-8 -*-
"""Explicit local import only; never fetch quotes, schedule runs or infer fills."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from paths import CALENDAR_CACHE, PHASE_SNAPSHOT_HISTORY, PREDICTION_HISTORY
from phase_monitor import record_phase_observation


def main() -> int:
    parser = argparse.ArgumentParser(description="显式导入盘中事实并验证目标交易日情景（不抓数、不调度、不下单）")
    parser.add_argument("--report-date", required=True, help="预测所属收盘日 YYYY-MM-DD")
    parser.add_argument("--trade-date", required=True, help="真实观察交易日；必须等于预测目标日")
    parser.add_argument("--phase", required=True, choices=["auction", "early_0935", "confirm_1000", "afternoon"])
    parser.add_argument("--captured-at", required=True, help="行情观察时刻，带时区 ISO-8601；不是文件导入时间")
    parser.add_argument("--metrics-json", required=True, help="真实指标 JSON 对象或本地 JSON 文件路径；缺失值用 null")
    parser.add_argument("--source", required=True, help="明确的行情来源，不填泛称 phase_monitor")
    parser.add_argument("--source-as-of", help="来源标注的数据截止时刻（如与观察时刻不同，必须提供）")
    parser.add_argument("--quality-status", required=True, choices=["ok", "degraded", "unknown", "unavailable", "blocked"], help="数据校验结果；导入成功不能自动等同 ok")
    parser.add_argument("--run-id")
    parser.add_argument("--history", default=PREDICTION_HISTORY)
    parser.add_argument("--phase-history", default=PHASE_SNAPSHOT_HISTORY)
    parser.add_argument("--calendar-cache", default=CALENDAR_CACHE, help="旧预测无目标日时只读此日历，不回退工作日推算")
    args = parser.parse_args()
    try:
        text = args.metrics_json.strip()
        metrics = json.loads(text if text.startswith("{") else Path(text).read_text(encoding="utf-8-sig"))
        if not isinstance(metrics, dict):
            raise ValueError("metrics-json 必须是 JSON object")
        lineage = {"source": args.source}
        if args.source_as_of:
            lineage["data_cutoff"] = args.source_as_of
        result = record_phase_observation(
            history_path=args.history, phase_snapshot_path=args.phase_history,
            report_date=args.report_date, trade_date=args.trade_date, phase=args.phase,
            metrics=metrics, captured_at=args.captured_at, run_id=args.run_id,
            source_lineage=lineage, quality={"status": args.quality_status}, calendar_cache=args.calendar_cache,
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
