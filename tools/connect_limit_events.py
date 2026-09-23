"""Connect retained closing-pool observations to auditable event facts.

Only the OHLC companion cache and explicit output folder are written. The raw
observations, daily fact snapshot and live decision history are not rewritten.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from event_inputs import prepare_limit_event_facts
from limit_events import load_limit_event_snapshot
from paths import LIMIT_EVENT_SNAPSHOT_DIR, RAW_BAR_CACHE_DIR, OUTPUT_DIR


def connect_events(day, *, archive_dir=LIMIT_EVENT_SNAPSHOT_DIR, cache_dir=RAW_BAR_CACHE_DIR,
                   output_dir=None, fetch_missing=True, provider=None, reference_prices=None):
    day=date.fromisoformat(day).isoformat()
    raw=load_limit_event_snapshot(archive_dir,day)
    if raw is None: raise ValueError("没有该日原始事件归档；先运行当日数据采集，不能从文件名伪造事件。")
    result=prepare_limit_event_facts(raw,cache_dir=cache_dir,provider=provider,fetch_missing=fetch_missing,
                                   reference_prices=reference_prices)
    facts=result['snapshot']
    output=Path(output_dir or Path(OUTPUT_DIR)/'limit_event_inputs')
    output.mkdir(parents=True,exist_ok=True)
    path=output/f'event_facts_{day}.json'
    path.write_text(json.dumps(facts,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    summary={'report_date':day,'rows':len(facts.get('records') or []),'facts_path':str(path.resolve()),
        'raw_state_coverage':{k:facts['raw_field_coverage'][k]['known'] for k in ('limit_up_attempted','broken','reclosed','board_type')},
        'resolved_state_coverage':{k:facts['field_coverage'][k]['known'] for k in ('limit_up_attempted','broken','reclosed','board_type')},
        'collection':result['collection'],'full_market_coverage':bool(facts.get('full_market_coverage')),
        'scope_note':'这里是收盘涨停样本的事件解释，不把样本内回封比例称为全市场回封率，也不授予交易许可。'}
    (output/f'coverage_{day}.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',required=True)
    parser.add_argument('--archive-dir',default=LIMIT_EVENT_SNAPSHOT_DIR)
    parser.add_argument('--cache-dir',default=RAW_BAR_CACHE_DIR)
    parser.add_argument('--output-dir',default=str(Path(OUTPUT_DIR)/'limit_event_inputs'))
    parser.add_argument('--no-fetch',action='store_true',help='仅使用已有同日原始OHLC缓存')
    args=parser.parse_args()
    try:
        result=connect_events(args.date,archive_dir=args.archive_dir,cache_dir=args.cache_dir,
                              output_dir=args.output_dir,fetch_missing=not args.no_fetch)
    except (OSError,ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
