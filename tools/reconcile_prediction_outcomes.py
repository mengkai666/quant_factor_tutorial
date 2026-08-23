# -*- coding: utf-8 -*-
"""CLI wrapper for the explicit T+1/T+3 reconciliation maintenance job."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from outcome_maintenance import run_reconciliation
from paths import CALENDAR_CACHE, DAILY_SNAPSHOT_DIR, PHASE_SNAPSHOT_HISTORY, PREDICTION_HISTORY


def main() -> int:
    parser = argparse.ArgumentParser(description="回填成熟的 T+1/T+3 预测结果")
    parser.add_argument("--history", default=PREDICTION_HISTORY)
    parser.add_argument("--snapshots", default=DAILY_SNAPSHOT_DIR)
    parser.add_argument("--calendar-cache", default=CALENDAR_CACHE)
    parser.add_argument("--phase-snapshots", default=PHASE_SNAPSHOT_HISTORY)
    args = parser.parse_args()
    result = run_reconciliation(
        history_path=args.history, snapshots_dir=args.snapshots,
        calendar_cache=args.calendar_cache, phase_snapshot_path=args.phase_snapshots,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
