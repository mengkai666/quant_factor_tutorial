"""Render the new multi-sector after-close brief without AI, orders or publishing."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/"src") not in sys.path:
    sys.path.insert(0,str(ROOT/"src"))
from research_brief_io import load_brief_context,prepare_research_brief,write_research_subpage,research_subpage_paths
from research_brief import _day
from report_integrity import ReportIntegrityError


def _positive(value):
    result=int(value)
    if result<1:raise argparse.ArgumentTypeError("必须为正整数")
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit",help="可选历史审计；未给出时读取最新权威日快照")
    parser.add_argument("--date",help="可选明确收盘日YYYY-MM-DD，不猜测未来行情")
    parser.add_argument("--output-dir",required=True)
    parser.add_argument("--parent-report",default=str(ROOT/"output/主线强度追踪.html"),help="子页的返回主报告目标；不会改写此文件")
    parser.add_argument("--snapshot-dir",default=str(ROOT/"data/report_daily_snapshots"))
    parser.add_argument("--price-slices-dir",default=str(ROOT/"data/price_slices"))
    parser.add_argument("--calendar-cache",default=str(ROOT/"data/trading_calendar_cache.csv"))
    parser.add_argument("--raw-bar-cache-dir",default=str(ROOT/"data/raw_ohlc_slices"))
    parser.add_argument("--fetch-ohlc",action="store_true",help="仅补重点股和多板主表个股的同日raw OHLC")
    parser.add_argument("--max-sectors",type=_positive,default=5)
    parser.add_argument("--stocks-per-sector",type=_positive,default=3)
    parser.add_argument("--recent-window",type=_positive,default=5)
    parser.add_argument("--recent-limit",type=_positive,default=20,help="多板主表展开行数；其余保留在折叠表及CSV")
    args=parser.parse_args(argv)
    try:
        context=load_brief_context(audit_path=args.audit,snapshot_dir=args.snapshot_dir,report_date=args.date)
        day=_day(context['report_date'])
        read_only_sources=[Path(args.calendar_cache)]
        if args.audit:read_only_sources.append(Path(args.audit))
        read_only_sources.extend(p for p in Path(args.snapshot_dir).glob('*.json') if _day(p.stem))
        read_only_sources.extend(Path(args.price_slices_dir).glob('*.csv.gz'))
        raw_cache=Path(args.raw_bar_cache_dir)/(day+'.json')
        sources=read_only_sources+[raw_cache]
        research_subpage_paths(day,args.output_dir,parent_report=args.parent_report,source_paths=sources)
        if args.fetch_ohlc and raw_cache.resolve() in {p.resolve() for p in read_only_sources}:
            raise ValueError('OHLC缓存写入不能覆盖只读输入')
        brief=prepare_research_brief(context,snapshot_dir=args.snapshot_dir,price_slices_dir=args.price_slices_dir,
            calendar_cache=args.calendar_cache,raw_bar_cache_dir=args.raw_bar_cache_dir,fetch_missing=args.fetch_ohlc,
            max_sectors=args.max_sectors,stocks_per_sector=args.stocks_per_sector,recent_window=args.recent_window,
            recent_limit=args.recent_limit)
        result=write_research_subpage(brief,args.output_dir,parent_report=args.parent_report,source_paths=sources)
    except (OSError,ValueError,ReportIntegrityError) as exc:
        parser.error(str(exc))
    result.update(report_date=brief["report_date"],purpose=brief["purpose"],sector_count=len(brief["sectors"]),
        watchlist_count=sum(len(s["stocks"]) for s in brief["sectors"]),recent_count=brief["recent"]["total_count"])
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
