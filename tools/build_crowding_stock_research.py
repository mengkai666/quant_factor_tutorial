"""Build a read-only dated supplement from explicit snapshots. No network, trades or pool mutation."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from crowding_stock_research import (build_stock_research, generate_stock_research_html,
                                    parse_quotes, stock_research_markdown)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--snapshot-dir',type=Path,required=True)
    p.add_argument('--as-of',required=True)
    args=p.parse_args()
    out=args.snapshot_dir
    def read(path):return json.loads(path.read_text(encoding='utf-8'))
    r=build_stock_research(read(args.baseline),read(out/'stock_price_snapshot.json'),
                           parse_quotes((out/'tencent_realtime_raw.txt').read_text(encoding='utf-8')),
                           read(out/'stock_profiles.json'),read(out/'crowding_external_evidence.json'),as_of=args.as_of)
    r['market_context'] = read(out/'market_context.json') if (out/'market_context.json').exists() else None
    target=out/f'抱团股个股研究_{args.as_of}'
    target.with_suffix('.json').write_text(json.dumps(r,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    baseline_link=args.baseline.with_suffix('.html').resolve().as_uri()
    target.with_suffix('.html').write_text(generate_stock_research_html(r,baseline_link=baseline_link),encoding='utf-8')
    target.with_suffix('.md').write_text(stock_research_markdown(r),encoding='utf-8')
    print(json.dumps(dict(html=str(target.with_suffix('.html').resolve()),stocks=len(r['stocks']),
                          frozen_cohorts=r['frozen_cohorts'],formal_tracking_updated=False),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
