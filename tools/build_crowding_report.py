"""独立生成抱团报告；默认不联网、不发布、不执行交易。

例: python tools/build_crowding_report.py --as-of 2026-10-08 --link-local
加 --refresh-external-evidence 获取开盘啦 / 财联社补充信息；不回填历史资格。
加 --refresh-reference-quotes 才更新独立个股研究缓存，不覆盖全市场价格。
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from crowding_cycle import NAMES, build_crowding_report, day
from crowding_view import export_crowding_reports, render_crowding_teaser_html
from paths import DATA_DIR, OUTPUT_DIR


def refresh_reference_quotes(as_of: str, data_dir: str | Path = DATA_DIR) -> dict:
    import requests
    endpoint = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'

    def fetch(symbol):
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(endpoint, params={'param': f'{symbol},day,,,110,qfq'}, timeout=12)
                response.raise_for_status()
                item = response.json().get('data', {}).get(symbol, {})
            key = 'qfqday' if item.get('qfqday') else 'day'
            rows = [dict(zip(['date','open','close','high','low','volume'], [x[0]]+[float(v) for v in x[1:6]]))
                    for x in item.get(key,[]) if x[0] <= as_of]
            return symbol, dict(basis='qfq' if key=='qfqday' else 'raw', rows=rows, error=None if rows else 'empty')
        except (requests.RequestException, ValueError, TypeError, IndexError) as exc:
            return symbol, dict(rows=[], error=str(exc)[:200])

    with ThreadPoolExecutor(max_workers=4) as pool:
        stocks = dict(pool.map(fetch, NAMES))
    result = dict(source='腾讯证券日K（单源补充，不覆盖全市场缓存）', source_url=endpoint,
                  requested_as_of=as_of, fetched_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), stocks=stocks)
    target = Path(data_dir) / 'crowding_reference_quotes.json'
    # 网络整体故障时保留已有成功数据，错误可见，不静默覆盖。
    if not any(x['rows'] for x in stocks.values()):
        raise RuntimeError('所有参考个股获取失败；原缓存未改写。')
    if target.exists():
        old = json.loads(target.read_text(encoding='utf-8'))
        for symbol, record in stocks.items():
            if not record['rows'] and old.get('stocks', {}).get(symbol, {}).get('rows'):
                record['rows'] = old['stocks'][symbol]['rows']
                record['basis'] = old['stocks'][symbol].get('basis')
                record['retained_old_data'] = True
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(target)
    return result


def link_local_outputs(output_dir: str | Path, as_of: str) -> list[str]:
    """仅为现存本地入口增加模块卡，不重跑全市场、不重建历史归档或发布。"""
    root=Path(output_dir)
    changed=[]
    marker='<!-- crowding-module-entry -->'
    end='<!-- /crowding-module-entry -->'
    for relative, link in [('本地导航入口.html','抱团周期分析_最新.html'),('index.html','抱团周期分析_最新.html'),
                           ('主线强度追踪.html','抱团周期分析_最新.html'),('site/index.html','crowding/latest.html'),
                           ('site/latest.html','crowding/latest.html')]:
        path=root/relative
        if not path.exists():
            continue
        doc=path.read_text(encoding='utf-8')
        card=render_crowding_teaser_html(as_of).replace('href="抱团周期分析_最新.html"',f'href="{link}"')
        card=marker+card+end
        if marker in doc:
            doc=re.sub(re.escape(marker)+r'.*?'+re.escape(end),lambda _:card,doc,flags=re.S)
        elif '</header>' in doc:
            doc=doc.replace('</header>','</header>'+card,1)
        else:
            doc=doc.replace('<body>','<body>'+card,1)
        path.write_text(doc,encoding='utf-8');changed.append(str(path.resolve()))
    return changed


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of',required=True,help='研究截止日期，例如2026-10-08')
    parser.add_argument('--output-dir',default=OUTPUT_DIR)
    parser.add_argument('--data-dir',default=DATA_DIR)
    parser.add_argument('--refresh-reference-quotes',action='store_true')
    parser.add_argument('--refresh-external-evidence',action='store_true',help='显式联网获取开盘啦/财联社外部证据')
    parser.add_argument('--link-local',action='store_true')
    parser.add_argument('--tracking-rules',help='可选JSON参数文件；仅覆盖跟踪阈值，不提供股票名单')
    args=parser.parse_args(argv)
    as_of=day(args.as_of)
    if args.refresh_reference_quotes:
        refresh_reference_quotes(as_of,args.data_dir)
    if args.refresh_external_evidence:
        from crowding_external import refresh_external_evidence
        try:
            refresh_external_evidence(as_of,args.data_dir)
        except RuntimeError as exc:
            print(str(exc),file=sys.stderr)
    rules=json.loads(Path(args.tracking_rules).read_text(encoding='utf-8')) if args.tracking_rules else None
    result=build_crowding_report(as_of,data_dir=args.data_dir,tracking_rules=rules)
    files=export_crowding_reports(result,args.output_dir,site_dir=Path(args.output_dir)/'site')
    if args.link_local:
        files['linked_local_entries']=link_local_outputs(args.output_dir,as_of)
    print(json.dumps(dict(files=files,clocks=result['sources_as_of'],quality=result['quality'],external_evidence=result['external_evidence'].get('summary',{}),external_mode=result['external_evidence']['mode']),ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
