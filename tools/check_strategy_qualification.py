"""Read-only inspection of exact strategy validation requirements from an audit.

No command approves a strategy, manufactures backtest samples or writes a
validation file. Evidence must come from a separate, explicitly reviewed study.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from strategy_qualification import (load_validation_records,qualify_strategies,build_strategy_event_input,
                                    RULE_VERSION,STRATEGY_OUTCOME,MIN_VALIDATION_SAMPLES)
from paths import STRATEGY_VALIDATION_FILE,CALENDAR_CACHE
from data_sources.calendar_provider import CalendarProvider


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit',required=True)
    parser.add_argument('--validation-file',default=STRATEGY_VALIDATION_FILE)
    parser.add_argument('--calendar-cache',default=CALENDAR_CACHE)
    args=parser.parse_args()
    try:
        doc=json.loads(Path(args.audit).read_text(encoding='utf-8-sig'))
        ctx=doc.get('context',doc)
        report_date=ctx['report_date']
        target=ctx.get('target_trade_date') or CalendarProvider(cache_path=args.calendar_cache).cached_next_trading_day(report_date)
        loaded=load_validation_records(args.validation_file)
        facts=ctx.get('facts') or {}
        event_input=facts.get('strategy_event_metrics') or build_strategy_event_input(facts.get('limit_event_snapshot'),report_date=report_date)
        result=qualify_strategies(ctx.get('scenario_plans') or [], quality=ctx.get('quality') or {},
            validation_records=loaded['records'],event_metrics=event_input,
            report_date=report_date,target_trade_date=target)
        # Samples can contain detailed research records; this tool prints only
        # assessment metadata/fingerprints, not the full evidence dataset.
        result.pop('validation_records',None)
        result['validation_file_status']=loaded['status']
        result['requirements']={'rule_version':RULE_VERSION,'outcome_definition_id':STRATEGY_OUTCOME,
            'min_unique_samples':MIN_VALIDATION_SAMPLES,'method':'out_of_sample',
            'record_schema':'strategy-validation/v1','set_schema':'strategy-validation-set/v1'}
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (OSError,ValueError,KeyError) as exc:
        parser.error(str(exc))


if __name__=='__main__':
    main()
