"""使用同日审计和研究缓存刷新本地抱团预案，不运行全市场流水线。

仅在 output/ 下生成独立子页面和刷新清单，不读取或改写主报告。
不抓行情、不追加交易留痕、不发送邮件、不部署站点。
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _date(value):
    value = str(value or "").strip()
    pattern = "%Y%m%d" if re.fullmatch(r"\d{8}", value) else "%Y-%m-%d"
    return datetime.strptime(value, pattern).date().isoformat()


def refresh(report_date, *, output_dir=None, data_dir=None):
    import crowding_next_day_plan as ndp
    import cb_baotuan_report as cb

    day = _date(report_date)
    output = Path(output_dir or ROOT / "output").resolve()
    data = Path(data_dir or ROOT / "data").resolve()
    context = ndp.load_plan_context(day, output_dir=str(output))
    if _date(context.get("report_date")) != day:
        raise ValueError("审计上下文日期不匹配")
    plan = ndp.build_next_day_plan(context, echelon=(context.get("decision_context") or {}).get("echelon"),
                                 output_dir=str(output), data_dir=str(data))
    # 抱团报告只写独立子页，不读取、嵌入或改写主报告。
    plan_files = ndp.export_plan_standalone_reports(day, output_dir=str(output), site_dir=str(output / "site"), plan_data=plan)
    cb_files = cb.export_cb_tracking_reports(day, output_dir=str(output), site_dir=str(output / "site"), data_dir=str(data))
    result = {"report_date": day, "target_trade_date": plan.get("target_date"),
              "execution_allowed": (plan.get("decision") or {}).get("execution_allowed"),
              "mode": "standalone_subpages_only", "network_requested": False, "journal_appended": False,
              "plan_files": plan_files, "cb_files": cb_files, "changed_main_reports": [], "main_reports_untouched": True}
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / f"crowding_plan_refresh_{day}.json"
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["manifest"] = str(manifest)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="收盘数据日期 YYYY-MM-DD")
    parser.add_argument("--output-dir")
    parser.add_argument("--data-dir")
    args = parser.parse_args(argv)
    print(json.dumps(refresh(args.date, output_dir=args.output_dir, data_dir=args.data_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
