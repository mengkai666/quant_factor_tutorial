"""Build a reproducible annual height / leadership / repair / follower study.
Run after collect_annual_height_bars.py; reads production caches without changing them.
"""
from __future__ import annotations
import argparse,hashlib,json,sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from annual_height_research import (year_start,pressure_frame,board_sequence,repair_events,
    eligible_seeds,ThemeBook,simulate_trade,forward_return,wilson,breakout_origin,leader_feedback)


def clean(v):
    if isinstance(v,dict):return {str(k):clean(x) for k,x in v.items()}
    if isinstance(v,(list,tuple)):return [clean(x) for x in v]
    if isinstance(v,(np.bool_,)):return bool(v)
    if isinstance(v,(np.integer,)):return int(v)
    if isinstance(v,(float,np.floating)):return float(v) if np.isfinite(v) else None
    if isinstance(v,pd.Timestamp):return v.strftime('%Y-%m-%d')
    return v


def dump(out,name,obj):
    (out/name).write_text(json.dumps(clean(obj),ensure_ascii=False,indent=2),encoding='utf8')


def save(out,name,rows,index=False):
    d=rows if isinstance(rows,pd.DataFrame) else pd.DataFrame(rows)
    d.to_csv(out/name,index=index,encoding='utf-8-sig');return d


def load_inputs(out,end):
    index=json.loads((out/'raw_tencent/sh000001.json').read_text(encoding='utf8'))['data']['sh000001']['day']
    dates=pd.Index([r[0] for r in index if r[0]<=end],name='date')
    z=pd.read_csv(ROOT/'data/涨停历史缓存.csv',dtype={'日期':str,'代码':str})
    z['date']=pd.to_datetime(z.日期).dt.strftime('%Y-%m-%d');z=z[z.date<=end]
    histories={c:g.sort_values('date').drop_duplicates('date').set_index('date').名称 for c,g in z.groupby('代码')}
    bs=pd.read_csv(ROOT/'data/baostock_close_long.csv',dtype={'code':str,'date':str})
    bs=bs[bs.date.between(dates[0],end)].drop_duplicates(['code','date'],keep='last')
    bg={c:g.set_index('date') for c,g in bs.groupby('code') if c.startswith(('sh60','sz00'))}
    u=pd.read_csv(ROOT/'data/stock_universe.csv',dtype=str).drop_duplicates('code').set_index('code')
    risk=set(bs.loc[bs.isST.eq(1),'code'])|set(z.loc[z.名称.str.contains('ST|退',case=False,na=False),'代码'])
    risk |= set(u.index[u.name.str.contains('ST|退',case=False,na=False)])
    pauses=json.loads((out/'verified_suspensions.json').read_text(encoding='utf8')) if (out/'verified_suspensions.json').exists() else []
    pc=pd.read_csv(ROOT/'data/price_history_cache.csv',usecols=['code','date','close_raw','close_qfq'],dtype={'code':str,'date':str})
    pc=pc[pc.date.between(dates[0],end)].drop_duplicates(['code','date'],keep='last')
    pg={c:g.set_index('date') for c,g in pc.groupby('code')}
    names={};bars={};coverage=[];conflicts=[];matched=0;hashes=[]
    for p in sorted((out/'raw_tencent').glob('*.json')):
        code=p.stem
        if not code.startswith(('sh60','sz00')):continue
        raw=p.read_bytes();hashes.append(dict(path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(raw).hexdigest()))
        obj=json.loads(raw);d=obj['data'][code];rows=d.get('day',[])
        b=pd.DataFrame([r[:6] for r in rows],columns=['date','open','close','high','low','volume']).set_index('date')
        b=b[~b.index.duplicated(keep='last')].reindex(dates).apply(pd.to_numeric,errors='coerce')
        qt=d.get('qt',{}).get(code,[]);name=qt[1] if len(qt)>1 else (u.loc[code,'name'] if code in u.index else code)
        names[code]=name
        b['reference']=b.close.ffill().shift(1);b['risk_status']=np.nan;b['suspended']=False;b['reference_verified']=False
        if code in bg:
            aux=bg[code].reindex(dates);agree=(aux.close-b.close).abs()<.011
            matched+=int(agree.sum());bad=aux.close.notna()&b.close.notna()&~agree
            conflicts.extend(dict(code=code,date=t,raw=float(b.loc[t,'close']),independent=float(aux.loc[t,'close'])) for t in dates[bad])
            b.loc[agree,'reference']=aux.loc[agree,'preclose'];b.loc[agree,'risk_status']=aux.loc[agree,'isST'];b.loc[agree,'reference_verified']=True
            b['suspended']=aux.tradestatus.eq(0)
        if code in histories:
            hn=histories[code].reindex(dates);have=hn.notna()
            b.loc[have,'risk_status']=hn[have].str.contains('ST|退',case=False,na=False).astype(int)
        for pause in pauses:
            if pause['code']==code:
                mask=(dates>=pause['start'])&(dates<pause['resume'])&b.volume.fillna(0).le(0)
                b.loc[mask,'suspended']=True
        b['eligible']=b.risk_status.eq(0)|(b.risk_status.isna()&(code not in risk))
        ipo=pd.Series(False,index=dates)
        listing=u.loc[code,'list_date'] if code in u.index else None
        if listing and pd.notna(listing):
            listed=dates[dates>=str(listing)]
            if str(listing)>=dates[0]:ipo.loc[listed[:5]]=True
        else:
            actual=b.index[b.close.notna()]
            if len(actual) and actual[0]>dates[0]:ipo.loc[actual[:5]]=True
        b['upper']=np.floor(b.reference*1.1*100+.500000001)/100
        b['lower']=np.floor(b.reference*.9*100+.500000001)/100
        valid=b.close.notna()&b.volume.gt(0)&~b.suspended
        b['limit_up']=valid&b.eligible&~ipo&((b.close-b.upper).abs()<.0051)
        b['boards']=board_sequence(b.limit_up,b.suspended)
        b['boards_strict']=board_sequence(b.limit_up)
        b['volume_ratio5']=b.volume/b.volume.shift(1).rolling(5,min_periods=5).mean()
        # Adj-factor changes identify corporate actions even without independent preclose.
        b['adjustment_break']=False
        b['adjustment_checked']=False
        if code in pg:
            pcg=pg[code].reindex(dates);agree=(pcg.close_raw-b.close).abs()<.011
            factor=(pcg.close_qfq/b.close).where(agree)
            b['adjustment_checked']=factor.notna()&factor.shift(1).notna()
            # Different cache epochs may overflag, so these are exclusions, not corrections.
            b['adjustment_break']=factor.div(factor.shift(1)).sub(1).abs()>.0015
        q=out/'qfq_tencent'/(code+'.json')
        if q.exists():
            qd=json.loads(q.read_text(encoding='utf8'))['data'][code];qr=qd.get('qfqday',qd.get('day',[]))
            qclose=pd.Series({r[0]:float(r[2]) for r in qr}).reindex(dates)
            factor=qclose/b.close
            b['adjustment_break']=factor.div(factor.shift(1)).sub(1).abs()>.0015
            b['q_close']=qclose
            b['adjustment_checked']=qclose.notna()&qclose.shift(1).notna()
        bars[code]=b
        actual=b.index[b.close.notna()]
        coverage.append(dict(code=code,name=name,first=actual[0],last=actual[-1],rows=len(actual),eligible_days=int((valid&b.eligible).sum()),verified_reference_days=int(b.reference_verified.sum()),unknown_risk_days=int((valid&b.risk_status.isna()).sum()),known_risk=code in risk))
    save(out,'bar_coverage.csv',coverage);save(out,'source_close_conflicts.csv',pd.DataFrame(conflicts,columns=['code','date','raw','independent']))
    dump(out,'source_audit.json',dict(independent_close_comparisons=matched,independent_close_conflicts=len(conflicts),known_risk_codes=len(risk),raw_files=len(bars),note='未知风险状态且曾有ST证据的日期剔除；其他未知状态按普通10%样本，历史ST遗漏风险仍需披露。'))
    inputs=['data/baostock_close_long.csv','data/涨停历史缓存.csv','data/cls_plate_cache.csv','data/em_stock_plate_cache.csv','data/stock_universe.csv','data/price_history_cache.csv']
    for file in inputs:hashes.append(dict(path=file,sha256=hashlib.sha256((ROOT/file).read_bytes()).hexdigest()))
    dump(out,'input_manifest.json',hashes)
    def name_at(c,d):
        if c in histories:
            past=histories[c].loc[histories[c].index<=d]
            if len(past):return str(past.iloc[-1]).strip()
        return names.get(c,c)
    return dates,bars,names,name_at,z


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);ap.add_argument('--end',default='2026-09-15');args=ap.parse_args()
    out=Path(args.out).resolve();end=args.end;start=year_start(end)
    dates,bars,names,name_at,z=load_inputs(out,end);N=len(dates);study=np.array(dates>=start);study_idx=np.where(study)[0]
    print('INPUT',len(bars),'symbols',int(study.sum()),'study sessions',flush=True)
    h=pd.DataFrame({c:b.boards for c,b in bars.items()},index=dates);hs=pd.DataFrame({c:b.boards_strict for c,b in bars.items()},index=dates)
    daily=pd.DataFrame(index=dates);daily['height']=h.max(axis=1);daily['height_strict']=hs.max(axis=1);daily['limit_count']=h.gt(0).sum(axis=1)
    daily['available_bars']=pd.DataFrame({c:b.close.notna()&b.volume.gt(0) for c,b in bars.items()}).sum(axis=1)
    for w in [5,10,20]:
        f=pressure_frame(daily.height,w);daily[f'pressure{w}']=f.pressure;daily[f'breakout{w}']=f.first_breakout
    daily['gap']=daily.height-daily.pressure5
    daily['state']=np.select([daily.gap.gt(0),daily.gap.eq(0),daily.gap.eq(-1)],['突破','触压','压下一板'],default='深度压缩')
    daily['fold']=np.where(dates<'2026-03-16','前半段','后半段')
    daily['leader_codes']=['|'.join(h.columns[h.iloc[i].eq(daily.height.iloc[i])]) if daily.height.iloc[i]>0 else '' for i in range(N)]
    daily['leader_names']=['、'.join(name_at(c,dates[i]) for c in daily.leader_codes.iloc[i].split('|') if c) for i in range(N)]
    daily['broken_height']=0;daily['broken_names']='';daily['prior_break_pressure']=np.nan
    previous_break=np.nan
    for i in range(1,N):
        daily.loc[dates[i],'prior_break_pressure']=previous_break
        old=int(daily.height.iloc[i-1]);cs=daily.leader_codes.iloc[i-1].split('|')
        observed=cs and all(c in bars and pd.notna(bars[c].iloc[i].close) and bars[c].iloc[i].volume>0 and not bars[c].iloc[i].suspended for c in cs)
        if old>=2 and observed and not any(h.iloc[i][c]==old+1 for c in cs):
            daily.loc[dates[i],'broken_height']=old;daily.loc[dates[i],'broken_names']=daily.leader_names.iloc[i-1];previous_break=old
    daily['next_height']=daily.height.shift(-1)
    for k in [1,3,5,10]:
        daily[f'future_max{k}']=[float(daily.height.iloc[i+1:i+k+1].max()) if i+k<N else np.nan for i in range(N)]
    save(out,'daily_market.csv',daily.loc[study],True);save(out,'daily_market_warmup.csv',daily,True)
    save(out,'board_matrix.csv.gz',h.loc[study],True)
    events=[];leader_rows=[];trades=[];breaks=[];repairs=[];leader_episodes=[];signal_codes=set()
    def trade(strategy,i,c,anchor='',meta=None):
        if i is None or i>=N or not study[i]:return
        signal_codes.add(c)
        for hold in [3,5]:
            t=simulate_trade(bars[c],i,hold)
            ei=t.get('entry_i');xi=t.get('exit_i')
            if ei is not None and xi is not None and bars[c].adjustment_break.iloc[ei:xi+1].any():
                for key in ['net_return','gross_return','mae','mfe']:t.pop(key,None)
                t['status']='corporate_action_unmodeled'
            if ei is not None:
                last=xi if xi is not None else min(N-1,i+hold)
                segment=bars[c].iloc[ei:last+1]
                t['reference_quality']='独立昨收' if segment.reference_verified.all() else ('复权缓存检查' if segment.adjustment_checked.all() else '原价参考未独立核验')
            else:t['reference_quality']='未建仓'
            t.update(strategy=strategy,signal_date=dates[i],signal_i=i,code=c,name=name_at(c,dates[i]),hold=hold,anchor=anchor,fold=daily.fold.iloc[i],height=int(daily.height.iloc[i]),pressure5=daily.pressure5.iloc[i])
            if meta:t.update(meta)
            for key in ['entry_i','exit_i','planned_exit_i']:
                if key in t and t[key]<N:t[key.replace('_i','_date')]=dates[t[key]]
            trades.append(t)
    # Breakout cohorts: every tied leader, all future outcomes, independent onset per window.
    for i in study_idx:
        top=int(daily.height.iloc[i]);leaders=[c for c in daily.leader_codes.iloc[i].split('|') if c]
        for w in [5,10,20]:
            if not daily[f'breakout{w}'].iloc[i]:continue
            event=dict(date=dates[i],i=i,window=w,height=top,pressure=float(daily[f'pressure{w}'].iloc[i]),leader_codes='|'.join(leaders),leader_names=daily.leader_names.iloc[i],fold=daily.fold.iloc[i])
            for k in [1,3,5,10]:
                event[f'market_new_high{k}']=float(daily[f'future_max{k}'].iloc[i]>top) if i+k<N else np.nan
                # Exact trajectory, not just endpoint; reinstatement after a break is not continuation.
                event[f'original_continues{k}']=float(any(all(h.iloc[i+j][c]==top+j for j in range(1,k+1)) for c in leaders)) if i+k<N else np.nan
                event[f'original_still_top{k}']=float(any(h.iloc[i+k][c]>0 and h.iloc[i+k][c]==daily.height.iloc[i+k] for c in leaders)) if i+k<N else np.nan
                vals=[forward_return(bars[c],i,k) for c in leaders];event[f'leader_return{k}']=float(np.mean(vals)) if all(np.isfinite(vals)) else np.nan
                event[f'new_leader{k}']=float(any(any(c not in leaders for c in daily.leader_codes.iloc[j].split('|')) and daily.height.iloc[j]>top for j in range(i+1,i+k+1))) if i+k<N else np.nan
            event['origin']='复牌续板' if any(breakout_origin(bars[c],i)=='复牌续板' for c in leaders) else '正常逐日晋级'
            event['height_band']='≤6板' if top<=6 else '≥7板'
            events.append(event)
            if w==5:
                for c in leaders:
                    b=bars[c];last=i
                    while last+1<N:
                        nxt=last+1
                        while nxt<N and b.suspended.iloc[nxt]:nxt+=1
                        if nxt<N and b.boards.iloc[nxt]==b.boards.iloc[last]+1:last=nxt
                        else:break
                    leader_rows.append(dict(date=dates[i],code=c,name=name_at(c,dates[i]),pressure5=daily.pressure5.iloc[i],breakout_height=top,extra_boards=int(b.boards.iloc[last])-top,chain_peak=int(b.boards.iloc[last]),chain_peak_date=dates[last],chain_right_censored=last==N-1,return3=forward_return(b,i,3),return5=forward_return(b,i,5),return10=forward_return(b,i,10),volume_ratio5=float(b.volume_ratio5.iloc[i]),fold=daily.fold.iloc[i],origin=breakout_origin(b,i)))
                    trade('突破P5_原龙头',i,c)
                    if i+1<N and b.boards.iloc[i+1]==top+1 and h.iloc[i+1][c]==daily.height.iloc[i+1]:trade('突破P5_次日仍领涨确认',i+1,c,dates[i])
        if daily.state.iloc[i]=='触压':
            for c in leaders:trade('触压原龙头_对照',i,c)
        if i>0 and daily.height.iloc[i-1]>=3 and daily.broken_height.iloc[i]>0:
            oldtop=int(daily.height.iloc[i-1]);prev=h.iloc[i-1];lower=prev[(prev>0)&(prev<oldtop)]
            second=int(lower.max()) if len(lower) else 0
            if second>=2:
                cs=list(lower.index[lower.eq(second)]);sv=[c for c in cs if h.iloc[i][c]==second+1]
                breaks.append(dict(date=dates[i],old_height=oldtop,old_names=daily.leader_names.iloc[i-1],second_height=second,candidates='|'.join(cs),survivors='|'.join(sv),survivor_count=len(sv),recover5=float(daily.future_max5.iloc[i]>=oldtop) if i+5<N else np.nan))
                for c in cs:trade('最高板断板_次高全组',i,c)
                for c in sv:trade('最高板断板_晋级次高',i,c)
    # Full >=3 board path census; did not repair is a first-class outcome.
    for c,b in bars.items():
        # Every run which led the market, not only the eventual tallest winners.
        seq=b.boards.to_numpy()
        for peak in range(N):
            nxt=peak+1
            while nxt<N and b.suspended.iloc[nxt]:nxt+=1
            if seq[peak]<3 or (nxt<N and seq[nxt]==seq[peak]+1):continue
            if not study[peak]:continue
            si=peak
            while si>0 and (seq[si-1]>0 or b.suspended.iloc[si-1]):si-=1
            led=[j for j in range(si,peak+1) if seq[j]>=3 and seq[j]==daily.height.iloc[j]]
            if not led:continue
            leader_episodes.append(dict(code=c,name=name_at(c,dates[peak]),start=dates[si],peak_date=dates[peak],peak=int(seq[peak]),first_leading=dates[led[0]],leading_days=len(led),pressure_at_first_leading=daily.pressure5.iloc[led[0]],broke_pressure=bool(any(seq[j]>daily.pressure5.iloc[j] for j in led)),right_censored=peak==N-1,return_after5=forward_return(b,peak,5),return_after10=forward_return(b,peak,10)))
        for r in repair_events(b):
            peak=r['peak_i'];br=r['break_i'];ri=r['repair_i'];conf=r['confirmation_i']
            if not study[br]:continue
            begin=max(0,peak-r['first_height']+1)
            r.update(code=c,name=name_at(c,dates[br]),peak_date=dates[peak],break_date=dates[br],repair_date=dates[ri] if ri is not None else '',confirmation_date=dates[conf] if conf is not None else '',was_market_leader=bool(any(seq[j]>=3 and seq[j]==daily.height.iloc[j] for j in range(begin,peak+1))),fold=daily.fold.iloc[br],market_state_at_break=daily.state.iloc[br],break_return=forward_return(b,peak,1))
            r['seed_known_i']=conf if conf is not None and r['was_market_leader'] and b.close.iloc[conf]>b.close.iloc[peak] else None
            if ri is not None:
                r.update(repair_market_state=daily.state.iloc[ri],repair_volume_ratio5=b.volume_ratio5.iloc[ri],repair_return5=forward_return(b,ri,5))
                trade('断板后首次再涨停',ri,c,dates[br],dict(first_height=r['first_height']))
                if r['reclaim_break_high']:trade('再涨停且收复断板高点',ri,c,dates[br],dict(first_height=r['first_height']))
                if conf is not None:trade('反包后两板确认',conf,c,dates[br],dict(first_height=r['first_height']))
            repairs.append(r)
    # Imitation seeds become available only after confirmed second run and restored peak price.
    seeds=[dict(r,confirmation_i=r['seed_known_i']) for r in repairs if r.get('seed_known_i') is not None]
    imit=[]
    frames=[]
    for path in ['data/cls_plate_cache.csv','data/em_stock_plate_cache.csv']:
        f=pd.read_csv(ROOT/path,dtype={'date':str,'code':str});f['source']=path;frames.append(f)
    themes=ThemeBook(pd.concat(frames,ignore_index=True));themes_exact=ThemeBook(pd.concat(frames,ignore_index=True),max_age_days=0)
    for r in repairs:
        if r['repair_i'] is None:continue
        matches=eligible_seeds(seeds,r)
        if not matches:continue
        seed=max(matches,key=lambda s:s['confirmation_i']);ri=r['repair_i']
        ct=themes.at(r['code'],dates[ri]);st=themes.at(seed['code'],dates[ri]);same=bool(ct and st and ct['mainline']==st['mainline'])
        pair=dict(seed_code=seed['code'],seed_name=seed['name'],seed_confirmation_date=dates[seed['confirmation_i']],candidate_code=r['code'],candidate_name=r['name'],candidate_date=dates[ri],lag=ri-seed['confirmation_i'],first_height=r['first_height'],gap_sessions=r['gap_sessions'],candidate_second_height=r['second_height'],candidate_right_censored=r['second_right_censored'],same_recorded_theme=same,theme_status='同主题' if same else ('不同主题' if ct and st else '标签不足'),theme=ct['mainline'] if same else '',return5=r.get('repair_return5'),fold=r['fold'])
        imit.append(pair);trade('有已知龙头先例_形态模仿',ri,r['code'],seed['code'],dict(first_height=r['first_height']))
        if same:trade('形态模仿且同主题',ri,r['code'],seed['code'],dict(first_height=r['first_height']))
    # New first boards during +1..+5 after P5 breakout. Latest prior anchor; one stock/date once.
    followers=[];ev5=[e for e in events if e['window']==5];anchors={}
    for e in ev5:
        for j in range(e['i']+1,min(N,e['i']+6)):anchors[j]=e
    for j,e in sorted(anchors.items()):
        leaders=e['leader_codes'].split('|');lts={c:themes.at(c,dates[j]) for c in leaders};exact_lts={c:themes_exact.at(c,dates[j]) for c in leaders}
        # Existing followers recorded separately so they are not called post-breakout launches.
        cs=list(h.columns[h.iloc[j].eq(1)])
        for c in cs:
            if c in leaders or h[c].iloc[max(0,j-3):j].gt(0).any():continue
            ct=themes.at(c,dates[j]);same=[lc for lc,t in lts.items() if ct and t and t['mainline']==ct['mainline']]
            fine=[lc for lc in same if ct.get('sub') not in ThemeBook.GENERIC and ct.get('sub')==lts[lc].get('sub')]
            exact_ct=themes_exact.at(c,dates[j]);same_exact=[lc for lc,t in exact_lts.items() if exact_ct and t and t['mainline']==exact_ct['mainline']]
            last=j
            while last+1<N and h.iloc[last+1][c]==h.iloc[last][c]+1:last+=1
            r=dict(anchor_date=e['date'],anchor_names=e['leader_names'],anchor_height=e['height'],signal_date=dates[j],signal_i=j,code=c,name=name_at(c,dates[j]),lag=j-e['i'],theme=ct['mainline'] if ct else '',label_date=ct['label_date'] if ct else '',label_age_days=ct['label_age_days'] if ct else np.nan,same_theme=bool(same),same_subtheme=bool(fine),exact_day_same_theme=bool(same_exact),theme_comparable=bool(ct and any(lts.values())),matching_leaders='|'.join(same),peak_height=int(h.iloc[last][c]),right_censored=last==N-1,return3=forward_return(bars[c],j,3),return5=forward_return(bars[c],j,5),return10=forward_return(bars[c],j,10),fold=daily.fold.iloc[j])
            r.update(leader_feedback(bars,leaders,e['i'],j))
            followers.append(r)
            trade('突破后新首板_全组对照',j,c,e['date'])
            if same:
                trade('突破后同主题新首板',j,c,e['date'])
                if r['leader_feedback']=='领涨正反馈':trade('龙头仍正反馈_同主题新首板',j,c,e['date'])
                elif r['leader_feedback']=='无正反馈':trade('龙头无正反馈_同主题新首板',j,c,e['date'])
            if fine:trade('突破后同细分题材新首板',j,c,e['date'])
            if same_exact:trade('突破后同主题_当日标签',j,c,e['date'])
    ev=save(out,'breakout_events.csv',events);lr=save(out,'breakout_leaders.csv',leader_rows);rp=save(out,'repair_events.csv',repairs);im=save(out,'imitation_pairs.csv',imit);fo=save(out,'follower_events.csv',followers)
    save(out,'leader_episodes.csv',leader_episodes);save(out,'top_break_events.csv',breaks)
    tr=save(out,'trade_ledger.csv',trades)
    dump(out,'signal_codes.json',sorted(signal_codes))
    # Full stock paths for every leader/repair/follower signal code, not only chosen chart examples.
    pathcodes=set(lr.code)|set(rp.code)|set(fo.loc[fo.same_theme,'code'])
    paths=[]
    for c in sorted(pathcodes):
        b=bars[c].loc[study].copy();b['code']=c;b['name']=names[c];b['market_height']=daily.loc[study,'height'];b['pressure5']=daily.loc[study,'pressure5'];b['date']=b.index
        paths.append(b[['date','code','name','open','high','low','close','volume','boards','boards_strict','limit_up','suspended','eligible','market_height','pressure5','volume_ratio5','reference','upper','lower','reference_verified','adjustment_break']])
    save(out,'stock_daily_paths.csv.gz',pd.concat(paths,ignore_index=True))
    stats=[]
    for keys,g in tr.groupby(['strategy','hold','fold']):
        stats.append(strategy_stat(keys,g))
    for (strategy,hold),g in tr.groupby(['strategy','hold']):stats.append(strategy_stat((strategy,hold,'全年'),g))
    st=save(out,'strategy_statistics.csv',stats)
    # Event baskets keep skipped entries as cash; unresolved baskets never become zero-return winners.
    baskets=[]
    for (strategy,date,hold),g in tr.groupby(['strategy','signal_date','hold']):
        done=g.status.eq('resolved');cash=g.status.str.startswith('skip_');complete=bool((done|cash).all())
        ret=float(g.loc[done,'net_return'].sum()/len(g)) if complete else np.nan
        baskets.append(dict(strategy=strategy,date=date,hold=hold,candidates=len(g),filled_resolved=int(done.sum()),unfilled=int(cash.sum()),unresolved=int((~(done|cash)).sum()),complete=complete,net_return=ret,fold=g.fold.iloc[0]))
    basket=save(out,'event_baskets.csv',baskets)
    # Non-overlapping calendar-time sensitivity, first available signal only, prior exit known.
    nonoverlap=[]
    for (strategy,hold),g in basket.groupby(['strategy','hold']):
        free=-1
        for _,r in g.sort_values('date').iterrows():
            i=dates.get_loc(r.date)
            if i<=free:continue
            tg=tr[(tr.strategy==strategy)&(tr.signal_date==r.date)&tr.hold.eq(hold)]
            nonoverlap.append(r.to_dict())
            free=int(tg.exit_i.max()) if tg.exit_i.notna().any() else i+hold
            if (tg.entry_i.notna() & ~tg.status.isin(['resolved'])).any():free=N
    save(out,'nonoverlap_baskets.csv',nonoverlap)
    joint=[]
    for (height,state),g in daily.loc[study].groupby(['height','state']):
        indices=[dates.get_loc(d) for d in g.index if dates.get_loc(d)+1<N]
        promoted=[any(h.iloc[i+1][c]==height+1 for c in daily.leader_codes.iloc[i].split('|') if c) for i in indices]
        joint.append(dict(height=int(height),state=state,days=len(g),observed_next=len(indices),original_promoted=sum(promoted),rate=sum(promoted)/len(promoted) if promoted else np.nan))
    save(out,'height_pressure_joint.csv',joint)
    old=z[(z['类型']=='ZT')&z.代码.str.startswith(('sh60','sz00'))&~z.名称.str.contains('ST|退',case=False,na=False)].copy()
    old['连板数']=pd.to_numeric(old.连板数,errors='coerce');oh=old.groupby('date')['连板数'].max()
    cmp=daily.loc[study,['height','height_strict']].copy();cmp['cached_height']=oh;cmp['difference']=cmp.height-cmp.cached_height;save(out,'height_cache_comparison.csv',cmp,True)
    # Numeric summaries are generated, not hand-entered in narrative.
    probabilities=[]
    for w,g in ev.groupby('window'):
        for metric in ['market_new_high1','market_new_high3','market_new_high5','original_continues1','original_continues3','original_continues5','original_still_top5','new_leader5']:
            v=g[metric].dropna();wins=int(v.sum());lo,hi=wilson(wins,len(v));probabilities.append(dict(window=int(w),metric=metric,wins=wins,n=len(v),rate=wins/len(v) if len(v) else None,ci_low=lo,ci_high=hi))
    save(out,'breakout_probabilities.csv',probabilities)
    summary=dict(start=start,end=end,trading_days=int(study.sum()),warmup_days=int((~study).sum()),main_codes=len(bars),max_height=int(daily.loc[study].height.max()),height_max_dates=list(daily.loc[study].index[daily.loc[study].height.eq(daily.loc[study].height.max())]),independent_breakouts=ev.groupby('window').size().to_dict(),leader_episodes=len(leader_episodes),distinct_leaders=len({x['code'] for x in leader_episodes}),breaks=len(rp),repaired=int(rp.status.eq('repaired').sum()),repair_ge2=int(rp.second_height.ge(2).sum()),repair4_breaks=int(rp.first_height.eq(4).sum()),repair4_success=int((rp.first_height.eq(4)&rp.second_height.ge(2)).sum()),imitation_pairs=len(im),same_theme_followers=int(fo.same_theme.sum()),same_subtheme_followers=int(fo.same_subtheme.sum()),exact_day_followers=int(fo.exact_day_same_theme.sum()),all_new_followers=len(fo),theme_comparable_followers=int(fo.theme_comparable.sum()),trade_status=tr.status.value_counts().to_dict(),latest=daily.iloc[-1].to_dict())
    dump(out,'study_summary.json',summary)
    print(json.dumps(clean(summary),ensure_ascii=False),flush=True)


def strategy_stat(keys,g):
    strategy,hold,fold=keys;r=g[g.status.eq('resolved')];n=len(r);wins=int(r.net_return.gt(0).sum());lo,hi=wilson(wins,n)
    return dict(strategy=strategy,hold=int(hold),fold=fold,signals=len(g),resolved=n,unfilled=int(g.status.str.startswith('skip_').sum()),unresolved=int((~g.status.eq('resolved')&~g.status.str.startswith('skip_')).sum()),wins=wins,win_rate=wins/n if n else np.nan,ci_low=lo,ci_high=hi,mean_net=r.net_return.mean(),median_net=r.net_return.median(),p10_net=r.net_return.quantile(.1),worst=r.net_return.min(),median_mae=r.mae.median(),profit_factor=r.loc[r.net_return.gt(0),'net_return'].sum()/(-r.loc[r.net_return.lt(0),'net_return'].sum()) if r.net_return.lt(0).any() else np.nan)
if __name__=='__main__':main()
