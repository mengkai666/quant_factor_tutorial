# -*- coding: utf-8 -*-
"""Record auction/09:35/10:00/afternoon facts and print the updated posterior."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from paths import PHASE_SNAPSHOT_HISTORY, PREDICTION_HISTORY
from phase_monitor import record_phase_observation


def main() -> int:
    parser = argparse.ArgumentParser(description="记录盘中阶段事实并更新场景后验")
    parser.add_argument("--report-date", required=True, help="预测所属收盘日 YYYY-MM-DD")
    parser.add_argument("--trade-date", required=True, help="实际观察交易日 YYYY-MM-DD")
    parser.add_argument("--phase", required=True, choices=["auction", "early_0935", "confirm_1000", "afternoon"])
    parser.add_argument("--captured-at", required=True, help="带时区 ISO-8601 时间")
    parser.add_argument("--metrics-json", required=True, help="指标 JSON 字符串或 JSON 文件路径")
    parser.add_argument("--history", default=PREDICTION_HISTORY)
    parser.add_argument("--phase-history", default=PHASE_SNAPSHOT_HISTORY)
    args = parser.parse_args()
    source = Path(args.metrics_json)
    metrics = json.loads(source.read_text(encoding="utf-8") if source.exists() else args.metrics_json)
    if not isinstance(metrics, dict):
        raise SystemExit("metrics-json 必须是 JSON object")
    result = record_phase_observation(
        history_path=args.history, phase_snapshot_path=args.phase_history,
        report_date=args.report_date, trade_date=args.trade_date,
        phase=args.phase, metrics=metrics, captured_at=args.captured_at,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
