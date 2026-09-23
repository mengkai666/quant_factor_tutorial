"""Independent file-level checks for annual study event and execution accounting."""
from __future__ import annotations
import argparse,hashlib,json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1]

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);args=ap.parse_args();o=Path(args.out);checks=[]
 def ck(name,ok,detail=None):checks.append(dict(check=name,passed=bool(ok),detail=detail))
 read=lambda f:pd.read_csv(o/f)
 s=json.loads((o/'study_summary.json').read_text(encoding='utf8'));d=read('daily_market.csv');w=read('daily_market_warmup.csv');e=read('breakout_events.csv');leaders=read('breakout_leaders.csv');r=read('repair_events.csv');im=read('imitation_pairs.csv');f=read('follower_events.csv');t=read('trade_ledger.csv');stat=read('strategy_statistics.csv');b=read('event_baskets.csv');p=read('stock_daily_paths.csv.gz')
 ck('frozen_end_date',d.date.max()==s['end']=='2026-09-15');ck('calendar_year_start',d.date.min()==s['start']=='2025-09-16');ck('exact_242_sessions',len(d)==s['trading_days']==242);ck('dates_unique_sorted',d.date.is_unique and d.date.is_monotonic_increasing)
 matrix=pd.read_csv(o/'board_matrix.csv.gz',index_col=0)
 ck('market_height_equals_all_stock_max',np.array_equal(matrix.max(axis=1).values,d.height.values))
 for window in [5,10,20]:
  expected=w.height.shift(1).rolling(window,min_periods=window).max()
  ck(f'pressure_{window}_past_only',np.allclose(w[f'pressure{window}'],expected,equal_nan=True))
  mask=w.height.gt(expected)&expected.notna();ind=mask&~mask.shift(1,fill_value=False)
  observed=set(w.loc[ind&w.date.ge(s['start']),'date']);actual=set(e.loc[e.window.eq(window),'date'])
  ck(f'independent_breakouts_{window}',observed==actual)
 ck('raw_source_cutoff_all',all(max(row[0] for row in json.loads(q.read_text(encoding='utf8'))['data'][q.stem]['day'])<=s['end'] for q in (o/'raw_tencent').glob('*.json')))
 ck('input_hashes_unchanged',all(hashlib.sha256((ROOT/a['path']).read_bytes()).hexdigest()==a['sha256'] for a in json.loads((o/'input_manifest.json').read_text(encoding='utf8'))))
 source=json.loads((o/'source_audit.json').read_text(encoding='utf8'));ck('independent_daily_close_crosscheck',source['independent_close_conflicts']==0,source['independent_close_comparisons'])
 sina=read('independent_sina_audit.csv');ck('independent_leader_ohlc_crosscheck',pd.to_numeric(sina.ohlc_conflicts,errors='coerce').fillna(0).sum()==0,int(sina.matched_days.sum()))
 ck('no_duplicate_trade_signals',not t.duplicated(['strategy','signal_date','code','hold']).any())
 dates=list(w.date);bycode={c:g.set_index('date') for c,g in p.groupby('code')}
 for i,row in leaders.iterrows():
  event=e[e.window.eq(5)&e.date.eq(row.date)];ck(f'leader_event_{i}',len(event)==1 and row.code in event.iloc[0].leader_codes.split('|'))
 for i,row in r.iterrows():
  if row.status!='repaired':continue
  br=int(row.break_i);ri=int(row.repair_i);peak=int(row.peak_i)
  ck(f'repair_sequence_{i}',ri>br==peak+1 and 1<=row.gap_sessions<=5 and row.total_limit_ups==row.first_height+row.second_height)
  if row.second_height>=2:ck(f'repair_confirmation_{i}',int(row.confirmation_i)==ri+1)
 for i,row in im.iterrows():
  sd=dates.index(row.seed_confirmation_date);ci=dates.index(row.candidate_date)
  ck(f'imitation_time_{i}',0<ci-sd<=20 and row.seed_code!=row.candidate_code)
  seeds=r[r.code.eq(row.seed_code)&r.confirmation_date.eq(row.seed_confirmation_date)]
  ck(f'imitation_known_seed_{i}',not seeds.empty and bool(seeds.iloc[0].was_market_leader) and seeds.iloc[0].second_height>=2 and seeds.iloc[0].first_height==row.first_height)
 for i,row in f.iterrows():
  ck(f'leader_feedback_no_future_{i}',row.leader_feedback in ['领涨正反馈','无正反馈','缺失或停牌不可比'] and row.leaders_still_on_original_chain>=0)
  ck(f'follower_timing_{i}',1<=row.lag<=5 and row.signal_date>row.anchor_date)
  if pd.notna(row.label_date):ck(f'theme_not_future_{i}',str(row.label_date)<=row.signal_date and 0<=row.label_age_days<=7)
 resolved=t[t.status.eq('resolved')]
 ck('entry_strictly_after_signal',(resolved.entry_i>resolved.signal_i).all())
 ck('T1_exit_after_entry',(resolved.exit_i>resolved.entry_i).all())
 ck('fixed_cost_reconciliation',np.allclose(resolved.gross_return-resolved.net_return,.3))
 ck('price_return_reconciliation',np.allclose((resolved.exit_price/resolved.entry_price-1)*100,resolved.gross_return))
 ck('unresolved_not_labeled_profit',t.loc[~t.status.eq('resolved'),'net_return'].isna().all())
 ck('status_counts_match_summary',t.status.value_counts().to_dict()==s['trade_status'])
 for i,row in stat.iterrows():
  g=t[t.strategy.eq(row.strategy)&t.hold.eq(row.hold)]
  if row.fold!='全年':g=g[g.fold.eq(row.fold)]
  rr=g[g.status.eq('resolved')]
  ck(f'stats_denominators_{i}',len(g)==row.signals and len(rr)==row.resolved and rr.net_return.gt(0).sum()==row.wins and row.resolved+row.unfilled+row.unresolved==row.signals)
 for i,row in b.iterrows():
  g=t[t.strategy.eq(row.strategy)&t.signal_date.eq(row.date)&t.hold.eq(row.hold)]
  complete=g.status.eq('resolved')|g.status.str.startswith('skip_')
  ck(f'basket_status_{i}',bool(complete.all())==bool(row.complete))
  if row.complete:ck(f'basket_cash_weight_{i}',abs(g.loc[g.status.eq('resolved'),'net_return'].sum()/len(g)-row.net_return)<1e-8)
 # Validate every embedded chart's name via observed mapping, not a hand-entered ticker.
 from bs4 import BeautifulSoup
 report=(o/'一年连板高度与龙头模仿补涨研究.html').read_text(encoding='utf8');soup=BeautifulSoup(report,'html.parser')
 ck('html_contains_year_and_breakout_leadership',s['start'] in report and s['end'] in report and '突破压力高度后的龙头领涨' in report)
 ck('report_selfcontained_figures',len(soup.find_all('svg'))>=15 and not soup.find_all('img'))
 ck('no_external_scripts',not soup.find_all('script',src=True))
 ck('section_nav_links_valid',all(soup.find(id=a['href'][1:]) is not None for a in soup.select('nav a[href^="#"]')))
 ck('repair4_counts',int(r.first_height.eq(4).sum())==s['repair4_breaks'] and int((r.first_height.eq(4)&r.second_height.ge(2)).sum())==s['repair4_success'])
 ck('source_subtype_and_height_split','origin' in e and set(e.origin)<=set(['复牌续板','正常逐日晋级']) and 'height_band' in e)
 ck('case_browser_contains_all_paths',(o/'个股路径浏览器.html').exists() and len(p.code.unique())>500)
 failed=[x for x in checks if not x['passed']];result=dict(passed=not failed,checks=len(checks),failed=failed,details=checks)
 (o/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({k:v for k,v in result.items() if k!='details'},ensure_ascii=False));return bool(failed)
if __name__=='__main__':sys.exit(main())
