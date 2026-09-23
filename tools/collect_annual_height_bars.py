"""Bounded, resumable raw daily OHLC collection for annual height research.
Never changes data/ caches; rejects future rows; retains errors and empty symbols.
"""
from __future__ import annotations
import argparse,concurrent.futures as cf,json,threading,time
from pathlib import Path
import pandas as pd
import requests
ROOT=Path(__file__).resolve().parents[1]
LOCAL=threading.local()

def history_url(code,start,end,adjustment='raw'):
    return f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,{start},{end},400,{"qfq" if adjustment=="qfq" else ""}'

def validate_rows(rows,end):
    if not rows:return 'empty'
    if any(str(r[0])>end for r in rows):return 'beyond_cutoff'
    if any(len(r)<6 for r in rows):return 'invalid_schema'
    if len({r[0] for r in rows})!=len(rows):return 'duplicate_dates'
    return 'ok'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);ap.add_argument('--start',default='2025-07-01');ap.add_argument('--end',default='2026-09-15');ap.add_argument('--workers',type=int,default=4);ap.add_argument('--growth',action='store_true');ap.add_argument('--adjustment',choices=['raw','qfq'],default='raw');args=ap.parse_args()
    out=Path(args.out).resolve();raw=out/('qfq_tencent' if args.adjustment=='qfq' else 'raw_tencent');raw.mkdir(parents=True,exist_ok=True)
    previous=ROOT/'output/height_pressure_halfyear_20260915/universe_requested.json'
    codes=set(json.loads(previous.read_text(encoding='utf8')))
    for f in [ROOT/'data/stock_universe.csv',ROOT/'data/baostock_universe_pit.csv']:
        d=pd.read_csv(f,dtype=str)
        if 'code' in d:codes.update(d.code.str.replace('.','',regex=False).dropna())
    prefix=('sh60','sz00','sz30','sh688') if args.growth else ('sh60','sz00')
    codes=sorted(c for c in codes if c.startswith(prefix));codes=['sh000001']+codes
    (out/'universe_requested.json').write_text(json.dumps(codes,ensure_ascii=False),encoding='utf8')
    def fetch(code):
        p=raw/(code+'.json')
        if p.exists():
            o=json.loads(p.read_text(encoding='utf8'));rows=o.get('data',{}).get(code,{}).get('qfqday' if args.adjustment=='qfq' else 'day',o.get('data',{}).get(code,{}).get('day',[]))
            meta=o.get('_research_source',{})
            if meta.get('cutoff')==args.end and meta.get('start')==args.start and validate_rows(rows,args.end)=='ok':return (code,'cached',len(rows),rows[0][0],rows[-1][0],'')
        if not hasattr(LOCAL,'s'):LOCAL.s=requests.Session()
        u=history_url(code,args.start,args.end,args.adjustment)
        try:
            r=LOCAL.s.get(u,timeout=(7,18));r.raise_for_status();o=r.json();rows=o.get('data',{}).get(code,{}).get('qfqday' if args.adjustment=='qfq' else 'day',o.get('data',{}).get(code,{}).get('day',[]));status=validate_rows(rows,args.end)
            if status!='ok':return(code,status,len(rows),'','','rejected history')
            o['_research_source']={'url':r.url,'retrieved_at':pd.Timestamp.now(tz='Asia/Shanghai').isoformat(),'adjustment':args.adjustment,'start':args.start,'cutoff':args.end}
            p.write_text(json.dumps(o,ensure_ascii=False),encoding='utf8')
            return (code,'ok',len(rows),rows[0][0],rows[-1][0],'')
        except Exception as e:return(code,'error',0,'','',str(e)[-350:])
    preflight=fetch('sh000001');print('PREFLIGHT',preflight,flush=True)
    if preflight[1] not in ('ok','cached'):raise RuntimeError(f'Preflight failed; no batch started: {preflight}')
    records=[];start=time.time();streak=0
    def save():pd.DataFrame(records,columns=['code','status','rows','first','last','detail']).to_csv(out/('qfq_acquisition_status.csv' if args.adjustment=='qfq' else 'acquisition_status.csv'),index=False,encoding='utf-8-sig')
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for rec in ex.map(fetch,codes):
            records.append(rec);streak=streak+1 if rec[1]=='error' else 0
            if len(records)%200==0:save();print(len(records),'/',len(codes),'seconds',round(time.time()-start),flush=True)
            if streak>=20:save();raise RuntimeError('20 consecutive network errors; circuit open')
    save();print(pd.Series([r[1] for r in records]).value_counts().to_dict(),flush=True)
if __name__=='__main__':main()
