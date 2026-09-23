"""Past-only primitives for annual market-height, repair and imitation research.
Daily bars provide execution proxies, never a promise of an executable fill.
"""
from __future__ import annotations
from bisect import bisect_right
import math
import numpy as np
import pandas as pd


def year_start(end):
    return (pd.Timestamp(end)-pd.DateOffset(years=1)+pd.Timedelta(days=1)).strftime('%Y-%m-%d')


def annual_frame(df,end):
    parsed=pd.to_datetime(df['日期'].astype(str),format='mixed',errors='coerce')
    return df.loc[parsed.between(pd.Timestamp(year_start(end)),pd.Timestamp(end))].copy()


def pressure_frame(height,window=5):
    p=height.shift(1).rolling(window,min_periods=window).max()
    br=height.notna() & p.notna() & height.gt(p)
    return pd.DataFrame({'height':height,'pressure':p,'gap':height-p,'breakout':br,'first_breakout':br & ~br.shift(1,fill_value=False)})


def board_sequence(flags,suspended=None):
    suspended=[False]*len(flags) if suspended is None else suspended
    out=[];n=0
    for flag,pause in zip(flags,suspended):
        if pause:out.append(0);continue
        n=n+1 if pd.notna(flag) and bool(flag) else 0
        out.append(n)
    return out


def repair_events(bars,min_first=3,max_gap=5):
    """Every observable >=3-board break, including no-repair and right-censor outcomes.

    Initial and repaired runs remain separate. A missing/suspended day is NOT a break.
    confirmation_i becomes known only on the second consecutive repaired limit close.
    """
    out=[];h=bars.boards.to_numpy();n=len(h)
    for peak in range(n-1):
        if h[peak]<min_first or h[peak+1]!=0:continue
        br=peak+1;r=bars.iloc[br]
        if pd.isna(r.close) or r.volume<=0 or bool(r.get('suspended',False)):continue
        end=min(n,br+max_gap+1);found=None;gap_missing=False
        for j in range(br+1,end):
            row=bars.iloc[j]
            if pd.isna(row.close) or row.volume<=0 or bool(row.get('suspended',False)):
                gap_missing=True;break
            if h[j]>0:found=j;break
        rec=dict(peak_i=peak,break_i=br,first_height=int(h[peak]),repair_i=found,
                 confirmation_i=None,second_height=0,gap_sessions=None,total_limit_ups=int(h[peak]),
                 status='missing_gap' if gap_missing else ('right_censored' if br+max_gap>=n else 'no_repair'),
                 reclaim_break_high=False,body_engulf=False,second_right_censored=False)
        if found is not None:
            last=found
            while last+1<n and h[last+1]==h[last]+1:last+=1
            second=int(h[last]);rec.update(status='repaired',gap_sessions=found-br,second_height=second,
                total_limit_ups=int(h[peak])+second,confirmation_i=found+1 if second>=2 else None,
                second_right_censored=last==n-1,
                reclaim_break_high=bool(bars.iloc[found].close>r.high),
                body_engulf=bool(bars.iloc[found].close>=max(r.open,r.close) and bars.iloc[found].open<=min(r.open,r.close)))
        out.append(rec)
    return out


def eligible_seeds(seeds,candidate,max_lag=20):
    """Exact first-run height, similar pause (<=3 sessions apart), already public success."""
    i=candidate['repair_i']
    if i is None:return []
    return [s for s in seeds if s['code']!=candidate['code'] and s.get('confirmation_i') is not None
            and 0<i-s['confirmation_i']<=max_lag and s['first_height']==candidate['first_height']
            and abs(s['gap_sessions']-candidate['gap_sessions'])<=3]


class ThemeBook:
    """Nearest prior recorded label only; never forward-fill today's tag into old history."""
    GENERIC={'其它','其他','未知','综合','nan','','None','ST','ST板块','风险警示','壳资源'}
    def __init__(self,frame,max_age_days=7):
        self.max_age_days=max_age_days;self.by_code={}
        f=frame.copy();f['date']=pd.to_datetime(f.date.astype(str),format='mixed',errors='coerce')
        for c,g in f.dropna(subset=['date']).sort_values('date').groupby('code'):
            g=g.drop_duplicates('date',keep='last');self.by_code[c]=(g.date.tolist(),g.to_dict('records'))
    def at(self,code,date):
        if code not in self.by_code:return None
        dates,rows=self.by_code[code];t=pd.Timestamp(date);i=bisect_right(dates,t)-1
        if i<0 or (t-dates[i]).days>self.max_age_days:return None
        row=dict(rows[i])
        if str(row.get('mainline','')) in self.GENERIC:return None
        row['label_date']=dates[i].strftime('%Y-%m-%d');row['label_age_days']=(t-dates[i]).days
        return row


def wilson(wins,n,z=1.959963984540054):
    if not n:return (None,None)
    p=wins/n;den=1+z*z/n;c=(p+z*z/(2*n))/den;d=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return max(0,c-d),min(1,c+d)


def simulate_trade(bars,signal_i,hold=3,cost_bps=30,max_delay=5):
    if hold<2:raise ValueError('T+1 requires hold >= 2 sessions')
    entry=signal_i+1;planned=signal_i+hold
    if entry>=len(bars):return dict(status='right_censored')
    e=bars.iloc[entry]
    if pd.isna(e.open) or e.volume<=0:return dict(status='skip_no_entry_bar')
    if e.open>=e.upper-.005 or e.open<=e.lower+.005:return dict(status='skip_open_limit')
    base=dict(entry_i=entry,planned_exit_i=planned,entry_price=float(e.open))
    if planned>=len(bars):return dict(status='right_censored',**base)
    exit_i=planned
    while exit_i<len(bars) and exit_i<=planned+max_delay:
        r=bars.iloc[exit_i]
        if pd.notna(r.close) and r.volume>0 and r.close>r.lower+.005:break
        exit_i+=1
    if exit_i>=len(bars) or exit_i>planned+max_delay:return dict(status='unresolved_exit',**base)
    win=bars.iloc[entry:exit_i+1]
    if win[['close','reference','open']].isna().any().any():return dict(status='missing_path',exit_i=exit_i,**base)
    actual_prev=bars.close.shift(1).iloc[entry:exit_i+1]
    if ((win.reference-actual_prev).abs()>.015).any():return dict(status='corporate_action_unmodeled',exit_i=exit_i,**base)
    gross=(float(bars.iloc[exit_i].close)/float(e.open)-1)*100
    return dict(status='resolved',exit_i=exit_i,delay_days=exit_i-planned,exit_price=float(bars.iloc[exit_i].close),
                gross_return=gross,net_return=gross-cost_bps/100,mae=(float(win.low.min())/float(e.open)-1)*100,
                mfe=(float(win.high.max())/float(e.open)-1)*100,**base)


def forward_return(bars,i,k):
    if i+k>=len(bars):return np.nan
    w=bars.iloc[i:i+k+1]
    if w.close.isna().any() or w.volume.le(0).any():return np.nan
    if 'adjustment_break' in w and w.adjustment_break.iloc[1:].any():return np.nan
    if ((w.reference.iloc[1:]-w.close.shift(1).iloc[1:]).abs()>.015).any():return np.nan
    return (float(w.close.iloc[-1]/w.close.iloc[0])-1)*100


def breakout_origin(bars,i):
    """An immediately preceding *evidenced* halt is not an ordinary day-by-day breakout."""
    if i>0 and bool(bars.iloc[i-1].get('suspended',False)) and bars.iloc[i].boards>0:
        return '复牌续板'
    return '正常逐日晋级'


def leader_feedback(bars,codes,anchor_i,known_i):
    """Leader money-effect observable by candidate close, never after it."""
    if known_i<=anchor_i:raise ValueError('candidate must follow anchor')
    returns=[];continuing=0
    for code in codes:
        b=bars[code];value=forward_return(b,anchor_i,known_i-anchor_i)
        returns.append(value);start=float(b.boards.iloc[anchor_i])
        if all(float(b.boards.iloc[j])==start+j-anchor_i for j in range(anchor_i+1,known_i+1)):continuing+=1
    known=bool(returns) and all(np.isfinite(returns))
    mean=float(np.mean(returns)) if known else np.nan
    status='缺失或停牌不可比' if not known else ('领涨正反馈' if mean>0 else '无正反馈')
    return dict(leader_feedback=status,leader_return_known=mean,leaders_still_on_original_chain=continuing)
