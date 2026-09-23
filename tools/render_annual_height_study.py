"""Render annual height research as self-contained HTML + editable Markdown + stock browser."""
from __future__ import annotations
import argparse,base64,html,io,json,sys
from pathlib import Path
import numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from annual_height_research import wilson
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','SimHei','DejaVu Sans'],'axes.unicode_minus':False,'font.size':10,'figure.facecolor':'#f5f8fc','axes.facecolor':'#ffffff','axes.edgecolor':'#c7d3e0','grid.color':'#dde5ef','text.color':'#203750','axes.labelcolor':'#203750','xtick.color':'#516579','ytick.color':'#516579','svg.fonttype':'none'})

def pct(x):return '—' if pd.isna(x) else f'{x:+.2f}%'
def rate(x):return '—' if pd.isna(x) else f'{x*100:.1f}%'
def table(frame):return frame.to_markdown(index=False)
def count_rate(w,n):return f'{w}/{n}（{w/n*100:.1f}%）' if n else '0/0（不可估计）'

def resolve_case_code(catalog,name):
    matches=catalog[catalog['name'].astype(str).str.strip().eq(name)]['code'].unique()
    if len(matches)!=1:raise ValueError(f'Case name {name!r} is not uniquely mapped: {matches}')
    return str(matches[0])


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);args=ap.parse_args();out=Path(args.out).resolve()
    s=json.loads((out/'study_summary.json').read_text(encoding='utf8'))
    get=lambda n:pd.read_csv(out/n)
    d=get('daily_market.csv');ev=get('breakout_events.csv');lr=get('breakout_leaders.csv');rep=get('repair_events.csv');im=get('imitation_pairs.csv');fol=get('follower_events.csv');tr=get('trade_ledger.csv');st=get('strategy_statistics.csv');paths=get('stock_daily_paths.csv.gz');ep=get('leader_episodes.csv');ba=get('event_baskets.csv');no=get('nonoverlap_baskets.csv');prob=get('breakout_probabilities.csv');joint=get('height_pressure_joint.csv')
    charts={}
    def figure(name,fig):
        fig.tight_layout();fig.savefig(out/(name+'.svg'),format='svg',bbox_inches='tight');fig.savefig(out/(name+'.png'),dpi=150,bbox_inches='tight');plt.close(fig);charts[name]=(out/(name+'.svg')).read_text(encoding='utf8')
    fig,ax=plt.subplots(figsize=(15,4.8));x=np.arange(len(d));ax.plot(x,d.height,color='#286ab4',lw=1.6,label='当日最高板 H');ax.step(x,d.pressure5,color='#15917e',lw=1.3,where='post',label='事前 P5');ax.step(x,d.pressure20,color='#c58b26',lw=1,ls='--',where='post',label='事前 P20')
    ticks=np.arange(0,len(d),20);ax.set_xticks(ticks,d.date.iloc[ticks],rotation=30,ha='right');ax.set_ylabel('连续涨停板数');ax.grid(axis='y',alpha=.6);ax.legend(ncol=3,loc='upper left');ax.set_title('一年高度：突破是市场事件，领涨与可成交收益要另算',loc='left',fontweight='bold')
    for _,r in d.nlargest(1,'height').iterrows():ax.annotate(r.leader_names,xy=(int(r.name),r.height),xytext=(0,8),textcoords='offset points',fontsize=8)
    figure('annual_height_pressure',fig)
    ss=st.query('fold=="全年" and hold==3').copy();order=['突破P5_原龙头','突破P5_次日仍领涨确认','最高板断板_晋级次高','断板后首次再涨停','反包后两板确认','有已知龙头先例_形态模仿','突破后同主题新首板','突破后同主题_当日标签','突破后同细分题材新首板'];splot=ss.set_index('strategy').reindex(order)
    fig,ax=plt.subplots(figsize=(11,5));colors=['#bf5366' if v<0 else '#2b7a9d' for v in splot.median_net];ax.barh(order,splot.median_net,color=colors);ax.axvline(0,c='#8696a7',lw=.8);ax.invert_yaxis();ax.set_xlabel('T+1次日开盘入场、信号后第3交易日收盘退出：已结算净收益中位数 %');ax.set_title('能画出漂亮形态，不代表能买到赚钱',loc='left',fontweight='bold');figure('strategy_medians',fig)
    fig,ax=plt.subplots(figsize=(10,4));labels=['市场后5日创新高','原龙头连续1日晋级','原龙头连续3日晋级','原龙头连续5日晋级','新股票后5日打出更高板'];metrics=['market_new_high5','original_continues1','original_continues3','original_continues5','new_leader5'];pp=prob[prob.window.eq(5)].set_index('metric').reindex(metrics)
    ax.bar(labels,pp.rate*100,color=['#2b7a9d','#15917e','#15917e','#15917e','#c58b26']);ax.errorbar(np.arange(len(labels)),pp.rate*100,yerr=np.array([pp.rate-pp.ci_low,pp.ci_high-pp.rate])*100,fmt='none',color='#4a5c70',capsize=3);ax.set_ylim(0,100);ax.set_ylabel('事件占比 %（Wilson 95%区间）');ax.tick_params(axis='x',labelsize=9);ax.set_title('同一批突破事件，五个不同问题',loc='left',fontweight='bold');figure('leadership_probability',fig)
    def path_chart(code,center,label,number):
        p=paths[paths.code.eq(code)].sort_values('date');i=int(np.argmin(abs(pd.to_datetime(p.date)-pd.Timestamp(center))));p=p.iloc[max(0,i-12):min(len(p),i+21)].reset_index(drop=True)
        if p.empty:return
        fig,(a,b)=plt.subplots(2,1,figsize=(12,4.8),sharex=True,gridspec_kw={'height_ratios':[2,1]});xx=np.arange(len(p))
        for k,r in p.iterrows():
            if pd.isna(r.close) or r.volume<=0:continue
            c='#c5536a' if r.close>=r.open else '#138675';a.vlines(k,r.low,r.high,color=c,lw=.8);a.add_patch(plt.Rectangle((k-.28,min(r.open,r.close)),.56,max(abs(r.close-r.open),.012),color=c,alpha=.8))
        lim=p.boards.gt(0);a.scatter(xx[lim],p.close[lim],s=18,color='#c48b24',zorder=4,label='收盘涨停');a.set_title(label+' · '+code,loc='left',fontweight='bold');a.set_ylabel('未复权价格');a.grid(axis='y',alpha=.5);a.legend(loc='upper left')
        b.bar(xx,p.boards,color='#3272b8',alpha=.8,label='个股连续板数');b.plot(xx,p.market_height,color='#bf526b',lw=1.1,label='市场高度');b.step(xx,p.pressure5,color='#15917e',lw=1,where='post',label='事前P5');b.set_ylabel('板');b.legend(ncol=3,fontsize=8,loc='upper left');ti=np.arange(0,len(p),4);b.set_xticks(ti,p.date.iloc[ti].str[5:],rotation=30);figure(f'case_{number:02}',fig);return f'case_{number:02}'
    # Deliberately include clean continuation, one-bar failures, interrupted chains and repairs.
    cases=[('sh600403','2025-10-20','大有能源：5板突破后到8板'),('sz001331','2025-12-22','胜通能源：突破、停牌与恢复高度'),('sz002931','2026-01-19','锋龙股份：复牌重现高板，不能视作普通单日突破'),('sz003018','2026-03-10','王力安防：5板突破即断板的反例'),('sh600396','2026-03-23','华电辽能：领涨与电力后排观察'),('sh603137','2026-07-06','恒尚节能：低位突破与后续领涨'),('sh603221','2026-07-29','爱丽家居：停牌、真实断板与再涨停须拆开'),('sh603618','2026-02-06','杭电股份：4板→断1日→再2板'),('sz000533','2026-03-16','顺钠股份：4板→断4日→再2板'),('sh600726','2026-03-19','华电能源：同主题与形态重合的观察例'),('sh605179','2026-08-17','一鸣食品：4板再2板，仍不代表交易盈利'),('sz002830','2026-02-06','名雕股份：4板再2板后的失败路径')]
    casefigs=[]
    for n,(c,t,label) in enumerate(cases,1):
        c=resolve_case_code(paths,label.split('：')[0])
        fig=path_chart(c,t,label,n)
        if fig:casefigs.append((fig,label,c,t))
    # Descriptive grouped evidence and explicit incomplete outcomes.
    repairgroups=[]
    for height,g in rep.groupby('first_height'):
        observed=g[g.status.isin(['repaired','no_repair'])];n=len(observed);success=int(observed.second_height.ge(2).sum());repairgroups.append(dict(首轮高度=int(height),全部断板=len(g),观察完整=n,五日内再涨停=int(observed.status.eq('repaired').sum()),再连板至少2=success,占观察完整=count_rate(success,n),缺失或未结束=len(g)-n))
    rg=pd.DataFrame(repairgroups);rg.to_csv(out/'repair_height_summary.csv',index=False,encoding='utf-8-sig')
    fgroups=[]
    for label,mask in [('全部新首板',pd.Series(True,index=fol.index)),('主线相同（≤7自然日旧标签）',fol.same_theme),('细分题材相同',fol.same_subtheme),('当日记录主线相同',fol.exact_day_same_theme)]:
        g=fol[mask];o=g[~g.right_censored];fgroups.append(dict(组别=label,候选数=len(g),完整连板路径=len(o),至少2板=count_rate(int(o.peak_height.ge(2).sum()),len(o)),至少3板=count_rate(int(o.peak_height.ge(3).sum()),len(o)),最高板=int(g.peak_height.max()) if len(g) else None,后5日价格中位数=pct(g.return5.median())))
    fg=pd.DataFrame(fgroups);fg.to_csv(out/'follower_group_summary.csv',index=False,encoding='utf-8-sig')
    # Price/reference and roundtrip cost sensitivity.
    sensitivity=[]
    for strategy in order:
        g=tr[(tr.strategy==strategy)&tr.hold.eq(3)]
        for label,subset in [('全部已结算',g[g.status.eq('resolved')]),('仅独立昨收或复权检查',g[g.status.eq('resolved')&g.reference_quality.ne('原价参考未独立核验')])]:
            for cost in [10,30,60]:
                v=subset.gross_return-cost/100;sensitivity.append(dict(strategy=strategy,scope=label,cost_bps=cost,n=len(v),wins=int(v.gt(0).sum()),mean=v.mean(),median=v.median()))
    sens=pd.DataFrame(sensitivity);sens.to_csv(out/'cost_reference_sensitivity.csv',index=False,encoding='utf-8-sig')
    bstats=[]
    for (strategy,hold),g in ba.groupby(['strategy','hold']):
        v=g.net_return.dropna();bstats.append(dict(strategy=strategy,hold=int(hold),events=len(g),complete=len(v),positive=int(v.gt(0).sum()),mean=v.mean(),median=v.median()))
    bstats=pd.DataFrame(bstats);bstats.to_csv(out/'basket_statistics.csv',index=False,encoding='utf-8-sig')
    # Within-trigger-date theme contrast (still observational, not causal).
    pairs=[]
    tt=tr[tr.hold.eq(3)&tr.status.eq('resolved')]
    for date,g in tt.groupby('signal_date'):
        a=g[g.strategy.eq('突破后同主题新首板')];allg=g[g.strategy.eq('突破后新首板_全组对照')]
        other=allg[~allg.code.isin(a.code)]
        if len(a) and len(other):pairs.append(dict(date=date,same_n=len(a),other_n=len(other),same_mean=a.net_return.mean(),other_mean=other.net_return.mean(),difference=a.net_return.mean()-other.net_return.mean()))
    paired=pd.DataFrame(pairs);paired.to_csv(out/'same_day_theme_contrast.csv',index=False,encoding='utf-8-sig')
    def ps(metric):
        p=prob[(prob.window==5)&(prob.metric==metric)].iloc[0];return f"{int(p.wins)}/{int(p.n)}（{rate(p.rate)}；95%区间{rate(p.ci_low)}–{rate(p.ci_high)}）"
    def strat_table(frame):
        f=frame.copy();return pd.DataFrame({'策略':f.strategy,'信号数':f.signals,'已结算':f.resolved,'未成交':f.unfilled,'未结/不可算':f.unresolved,'赚钱/已结算':[f'{int(w)}/{int(n)}' for w,n in zip(f.wins,f.resolved)],'赚钱率':f.win_rate.map(rate),'均值':f.mean_net.map(pct),'中位数':f.median_net.map(pct),'最差单次':f.worst.map(pct)})
    groups=[]
    for field,title_ in [('origin','突破来源'),('height_band','突破落点')]:
        for value,g in ev[ev.window.eq(5)].groupby(field):
            n=int(g.market_new_high5.notna().sum());wins=int(g.market_new_high5.sum());cont=int(g.original_continues3.sum());groups.append({'分层':title_,'组别':value,'事件数':len(g),'后5日市场创新高':count_rate(wins,n),'原龙头再3日连续晋级':count_rate(cont,int(g.original_continues3.notna().sum()))})
    breakdown=pd.DataFrame(groups);breakdown.to_csv(out/'breakout_origin_height_summary.csv',index=False,encoding='utf-8-sig')
    feedback=[]
    for status,g in fol[fol.same_theme].groupby('leader_feedback'):
        tg=tr[tr.strategy.eq('突破后同主题新首板')&tr.hold.eq(3)].merge(g[['code','signal_date']],on=['code','signal_date'],how='inner');rr=tg[tg.status.eq('resolved')]
        feedback.append({'候选首板收盘时龙头状态':status,'候选数':len(g),'已结算':len(rr),'赚钱率':rate(rr.net_return.gt(0).mean()) if len(rr) else '—','净收益均值':pct(rr.net_return.mean()),'净收益中位数':pct(rr.net_return.median())})
    feedback_table=pd.DataFrame(feedback);feedback_table.to_csv(out/'leader_feedback_followers.csv',index=False,encoding='utf-8-sig')
    space=[]
    for lag,g in fol[fol.same_theme&~fol.right_censored].groupby('lag'):
        ratio=g.peak_height/g.anchor_height;space.append({'突破后第几交易日启动':int(lag),'完整候选':len(g),'后续最高板中位数':float(g.peak_height.median()),'后续最高板90%分位':float(g.peak_height.quantile(.9)),'达到原突破高度':count_rate(int(g.peak_height.ge(g.anchor_height).sum()),len(g)),'相对原突破高度中位数':rate(ratio.median())})
    space_table=pd.DataFrame(space);space_table.to_csv(out/'follower_height_space.csv',index=False,encoding='utf-8-sig')
    volume=[]
    vl=lr.copy();vl['volume_band']=pd.cut(vl.volume_ratio5,[-np.inf,.8,1.5,np.inf],labels=['缩量<0.8','平量0.8–1.5','放量>=1.5'],right=False)
    for label,g in vl.groupby('volume_band',observed=True):
        tt=tr[tr.strategy.eq('突破P5_原龙头')&tr.hold.eq(3)].merge(g[['code','date']].rename(columns={'date':'signal_date'}),on=['code','signal_date'])
        rr=tt[tt.status.eq('resolved')];volume.append({'突破日量比':str(label),'原龙头信号':len(g),'后来至少多1板':int(g.extra_boards.gt(0).sum()),'已结算交易':len(rr),'净收益中位数':pct(rr.net_return.median())})
    volume_table=pd.DataFrame(volume);volume_table.to_csv(out/'breakout_volume_summary.csv',index=False,encoding='utf-8-sig')
    title='一年连板高度、突破后龙头领涨与模仿补涨研究'
    L=[f'# {title}',f'**研究区间：{s["start"]}—{s["end"]}收盘｜{s["trading_days"]}个交易日｜生成：2026-09-16｜研究用途，不是买入指令**','',
    '> 核心结论：高度突破带来的是“可继续观察的市场空间”，不是“原龙头必然继续涨”的保证。原龙头领涨、后排补涨、断板反包、形态模仿必须分别定义、分别计算。',
    '## 01 一页结论',
    f'- **市场延伸与原股延伸差别很大。** P5独立突破共{s["independent_breakouts"]["5"]}次，后5日市场再创新高{ps("market_new_high5")}；原龙头再连续5日晋级仅{ps("original_continues5")}。',
    '- **突破后原龙头领涨是必要的观察层，不是无条件买点。** 需要把“继续封板”“依然最高梯队”“扣成本后可成交”三关拆开；一字高板的涨幅不计作买到的收益。',
    '- **4板断板后再反包多板确实存在，但不是高胜率通用模式。** 下面提供全部4板断板分母、再涨停分母和再2板分母；名雕股份、一鸣食品等说明形态成立也可能亏钱。',
    '- **已知龙头先例的形态模仿样本较少，前后半年方向不稳定。** 不把全年的正均值直接包装为稳定战法。',
    '- **龙头正反馈并不保证同题材后排赚钱。** 第5.1节按候选收盘前已知的龙头反馈拆分；停牌/缺失不可比组另列，不能用混合均值包装补涨规律。',
    '- **同主线、同细分、同日标签结果不一致。** 宽题材含大量噪声；更窄题材在本次样本中并未自动更赚钱。标签覆盖和市场阶段是重要混杂因素。',
    '![一年高度与压力](annual_height_pressure.svg)',
    '## 02 范围、定义与时间点',
    f'覆盖{ s["main_codes"] }只取得原始行情的沪深主板股票，历史证券名单与当前名单取并集；保留19只两源无数据代码的缺失记录。主统计排除已知ST状态，不混创业板/科创板20%、北交所30%。未知风险状态不能保证被完全识别，见第12节。原始OHLC从2025-07-01起，{s["warmup_days"]}个预热交易日不计入全年统计。',
    '| 定义 | 明确口径 |\n|---|---|\n| 市场高度 H | 当日可观察、收盘涨停股票中的最高连续板数，停牌当天不参榜 |\n| P5/P10/P20 | 此前5/10/20个市场交易日H的最大值，不含当日；不能用本日抬升后的展示压力判断本日突破 |\n| 独立突破 | H>P，且前一天不是同一突破状态；连升段只在开始记一次；复牌重现高板可形成新的事件，另披露其特殊性 |\n| 压力位置 | H−P5>0突破；=0触压；=−1压下一板；≤−2深度压缩 |\n| 原龙头 | 突破当日所有并列最高板，不能事后只选继续涨的那一个 |\n| 断板 | 前日有连续涨停，次日有真实成交但不再收盘涨停；停牌/缺数据不是价格断板 |\n| 再涨停 | 首轮≥3板结束后1–5个交易日内再次收盘涨停；不自动称为K线反包 |\n| 收复断板高点 | 再涨停当日收盘超过断板日最高价；与实体反包另列 |\n| 形态先例 | 先例首轮曾在市场最高梯队；再连板≥2且收盘收复首轮峰值后，才成为已知赚钱先例 |\n| 模仿候选 | 候选再涨停前1–20交易日已有上述先例，首轮高度相同、间隔相差≤3日；不得使用候选未来的二波高度筛选 |\n| 新启动补涨候选 | 突破后第1–5日新首板，且此前3交易日没有涨停；同主线/同细分另列，不把突破前已涨的跟随股算作新补涨 |',
    '**重要：路径相似不等于资金模仿意图，同题材先后上涨不等于因果传导。本文称“模仿/补涨候选”，不证明资金因果。**',
    '### 停牌续板：三个数字不能混称',
    '主口径对有独立停牌状态或公司公告证据的日期暂停计数；另给严格按市场日连续的高度。锋龙股份跨2025-12-18—12-24控制权事项停牌时，若把12月17日的首板一并延续，2026-01-23为18板；公司公告从12月25日复牌起算17个涨停。两者起算点不同，不把18板声称为公司披露数字。恢复旧高度也不等于普通每天晋级的新空间。',
    '## 03 压力高度与最高板的关系',
    '同样的绝对高度，必须结合它距离既有压力有多远。下表是逐日描述性晋级比例，不是交易胜率；相邻日高度强相关，不能把每一行当独立试验。',
    table(joint.rename(columns={'height':'绝对高度','state':'压力状态','days':'天数','observed_next':'可观察次日','original_promoted':'原最高股晋级日','rate':'比例'}).assign(比例=lambda x:x['比例'].map(rate))),
    '## 04 核心战法：突破压力高度后的龙头领涨',
    '### 4.1 先拆成三个问题',
    f'1. **空间是否继续打开？** 后5日市场创新高：{ps("market_new_high5")}。\n2. **是不是原龙头在领涨？** 次日原股连续晋级{ps("original_continues1")}；再3日{ps("original_continues3")}；第5日原股仍在最高梯队{ps("original_still_top5")}。\n3. **普通研究订单能否赚钱？** 见下表：信号收盘后，最早下一交易日开盘尝试；不按突破当天涨停价假设成交。',
    '![领涨与市场延伸的差别](leadership_probability.svg)',
    table(strat_table(ss[ss.strategy.isin(['突破P5_原龙头','突破P5_次日仍领涨确认','触压原龙头_对照'])])),
    '“信号后第3交易日”指t收盘确认、t+1开盘建仓、计划t+3收盘退出；不是持有3个完整交易日。触压对照没有匹配题材、月份、流动性，不能据此认定触压策略优于突破策略。',
    '### 4.2 完整突破龙头清单（并列不删、失败不删）',
    table(lr[['date','name','pressure5','breakout_height','chain_peak','extra_boards','return5','chain_right_censored']].rename(columns={'date':'突破日','name':'龙头','pressure5':'事前P5','breakout_height':'突破高度','chain_peak':'原链最终高度','extra_boards':'后续新增板','return5':'后5日价格变化','chain_right_censored':'链未结束'}).assign(后5日价格变化=lambda x:x['后5日价格变化'].map(pct))),
    '**事后最终高度只用于复盘，绝不能放进当天选股条件。** “链最终高度”可跨有证据的停牌；“再连续k市场日”概率不跨停牌，两个指标故意分开。价格变化遇停牌/缺日/除权风险留空。',
    '### 4.3 正常晋级、复牌续板与绝对高度分层',table(breakdown),'两种分层各自覆盖同一事件集，不能相加当作更大样本。复牌高度是恢复已知高位的一种情形，不等于普通单日打开新空间。',
    '### 4.4 突破日量价：只做探索，不把放量等同强势',table(volume_table),'量比=当日成交量/此前5交易日均量，不含当日。仅为小样本分层，并未控制板高、一字板、换手率与流通盘；缺少流通股本时不把成交量比冒充换手率。',
    '### 4.5 领涨战法的条件化执行框架',
    '| 环节 | 观察与约束 |\n|---|---|\n| 触发 | 收盘H首次超过事前P5，同时记录P10/P20；保留全部并列龙头 |\n| 第一道验证 | 下一交易日原龙头是否继续晋级并仍处最高梯队；若新股接替，不再把原股当唯一龙头 |\n| 第二道验证 | 龙头之外是否出现可观察的新增首板、二板晋级和同题材扩散；孤立高板不等于板块赚钱效应 |\n| 可执行性 | 一字涨停、涨停价开盘按不成交处理；不追认盘中“应该能买到”；涨停后确认只能翌日再参与 |\n| 分歧 | 正常换手、真正断板、停牌、复牌分别标记；日线无法判断封单质量与开板承接，需额外盘中证据 |\n| 失效 | 原最高梯队全断，次高无晋级；市场高度跌回压力下且补涨亏损扩大；公司公告否认关键题材；停牌/除权导致统计不可比 |\n| 退出 | 本文统一固定观察时点以便比较；实盘退出还需盘中规则与T+1约束，不把无法成交的止损价计作已退出 |',
    '## 05 龙头之后谁接替：补涨不是“涨得少就该涨”',
    f'在{s["independent_breakouts"]["5"]}次P5突破后的观察窗口发现{s["all_new_followers"]}个新首板候选，{s["theme_comparable_followers"]}个可以与龙头比较缓存主题；其余不是“不同题材”，而是标签证据不足。默认标签只向过去找、最多7个自然日；另作当日标签敏感性。',
    table(fg),
    '### 补涨空间与启动时点',table(space_table),'分母使用突破当时已知的龙头高度，不用未来才知道的龙头最终峰值；本表观察补涨连续板峰值，不把它当成可成交目标价。多数新首板并未延伸成高板，不能套用“龙头到几板，补涨必到其一半”的固定公式。',
    '同主线标签可能覆盖AI算力、新能源电网等大集合；它不是严格同一产业链。龙头改名/概念否认/借壳预期也不等于真实业绩关系，具体交易必须再核对公司公告。',
    '### 5.1 龙头赚钱效应是否仍在：先看已知反馈再看补涨',table(feedback_table),'龙头反馈只使用突破日至候选首板收盘已发生的价格，不看候选未来。领涨正反馈=原龙头平均价格变化>0；仍在原连续板链的数量另列在明细中。停牌或除权不强行比较。本表属于条件关联，不能把正反馈解释成必然带动补涨。',
    '### 5.2 补涨实例与失败实例',
    '以下按事后高度和涨跌展示“研究案例”，不是选股规则；完整候选含失败股见follower_events.csv。',
    table(pd.concat([fol[fol.same_theme].nlargest(8,'peak_height'),fol[fol.same_theme].nsmallest(6,'return5')]).drop_duplicates(['code','signal_date'])[['anchor_date','anchor_names','signal_date','name','theme','peak_height','return5']].rename(columns={'anchor_date':'龙头突破日','anchor_names':'原龙头','signal_date':'后排首板日','name':'后排股票','theme':'记录主线','peak_height':'后续连续板峰值','return5':'后5日价格变化'}).assign(后5日价格变化=lambda x:x['后5日价格变化'].map(pct))),
    f'同一候选启动日期内做粗对照，有{len(paired)}个日期同时存在同主题与其他候选；同主题组减其他组的已结算平均收益差，跨日期均值{pct(paired.difference.mean())}。这只能减轻市场日效应，仍未控制市值、成交额、行业或标签选择，不能解释为同题材的因果收益。',
    '### 5.3 旧次高接力与新首板补涨分开',
    table(strat_table(ss[ss.strategy.isin(['最高板断板_次高全组','最高板断板_晋级次高','突破后同主题新首板','突破后同主题_当日标签','突破后同细分题材新首板','突破后新首板_全组对照'])])),
    '晋级次高的候选名单来自断板前一日，晋级资格在断板日收盘确认，最早下一日入场。它是“已有梯队接替”，不是“低位新首板补涨”。',
    '## 06 个股路径：4板断板后反包多板',
    table(rg),
    f'全年共{s["repair4_breaks"]}次4板后的可见断板起点。不能把其中{s["repair4_success"]}次后续再连板≥2，除以“后来真的修复”的子集来宣传整体高胜率；完整观察分母、未结束和缺失样本已在上表分开。',
    '### 6.1 4板后再多板的全部观察记录',
    table(rep[rep.first_height.eq(4)&rep.second_height.ge(2)][['name','peak_date','break_date','repair_date','gap_sessions','second_height','reclaim_break_high','body_engulf','repair_return5']].rename(columns={'name':'股票','peak_date':'首轮4板日','break_date':'断板日','repair_date':'再涨停日','gap_sessions':'中断交易日','second_height':'第二轮连续板','reclaim_break_high':'收复断板高点','body_engulf':'实体反包','repair_return5':'再涨停后5日变化'}).assign(再涨停后5日变化=lambda x:x['再涨停后5日变化'].map(pct))),
    '### 6.2 再涨停、强反包、二板确认，三种买点不能替代',
    table(strat_table(ss[ss.strategy.isin(['断板后首次再涨停','再涨停且收复断板高点','反包后两板确认'])])),
    '严禁把“4+2”写成“6连板”。形态成立只是价格路径事实；开盘跳空、日内先冲后落、T+1不能当日退出，都可能让最后交易亏损。',
    '## 07 赚钱效应后的形态模仿与题材模仿',
    f'固定规则识别{s["imitation_pairs"]}对形态候选。先例必须在候选再涨停之前已完成第二轮至少2板且价格收复首轮峰值，不使用后来才知道的成功。相同股票不当作模仿自己，多个先例取最近已确认者，避免重复算信号。',
    table(im[['seed_name','seed_confirmation_date','candidate_name','candidate_date','first_height','gap_sessions','candidate_second_height','theme_status','return5']].rename(columns={'seed_name':'已知先例','seed_confirmation_date':'先例确认日','candidate_name':'后续候选','candidate_date':'候选再涨停日','first_height':'首轮高度','gap_sessions':'中断日数','candidate_second_height':'候选第二轮高度','theme_status':'主题证据','return5':'后5日价格变化'}).assign(后5日价格变化=lambda x:x['后5日价格变化'].map(pct))),
    table(strat_table(ss[ss.strategy.isin(['有已知龙头先例_形态模仿','形态模仿且同主题'])])),
    '**同形态且同主题只有4个观察信号，3个赚钱不能推出“75%稳定胜率”。** 同时，候选首轮高度/间隔规则是本次研究预设，但研究主题来自已见过的行情，仍存在研究者选择偏差。',
    '## 08 个股K线与连板路径案例',
    '上图为未复权OHLC，下图同时标出个股板数、市场高度和事前P5。金点为收盘涨停。停牌空档不填交易K线；四板修复、持续领涨与失败路径并列展示。完整股票浏览器可选择代码并切换50/120/全年窗口。']
    interpretations={
      '大有能源':'同日并列突破的远大控股没有复制大有能源的路径，说明突破当天不能事后只留下继续领涨者。应先保留并列名单，再观察原股继续晋级、相对强弱与可成交性。',
      '胜通能源':'持续领涨后发生核查停牌，复牌重新出现在最高梯队。这类恢复旧高度会使P5重新突破，不能与低位正常升板混为一谈；次日一字板尤其不能按已成交处理。',
      '锋龙股份':'控制权事项停牌和涨幅核查停牌叠加。本文跨已证实停牌延续计算18板，公司从12月25日复牌起算17板；这是不同起算点。高辨识度与巨大账面涨幅不说明复牌追入有正收益。',
      '王力安防':'触发突破后没有继续增加原链板数，适合作为“破压当天强，随后走弱”的反例。若把市场随后出现的其他强股收益算给原龙头，会夸大突破领涨战法。',
      '华电辽能':'原龙头晋级与后续电力主线新首板可以在同一时间轴观察，例如新能泰山。它们是统计上的同主线先后启动，不是资金因果证明；同一广义主线中的细分业务还需公告核验。',
      '恒尚节能':'较低高度破压后原链延伸，适合和相同突破落点但立即断板的样本对照。只展示这个成功例会遗漏买点与卖点、跳空及随后回撤对实际收益的影响。',
      '爱丽家居':'必须区分停牌、复牌续板、8月7日真实不涨停和8月10日再涨停。8月7日两源OHLC及独立昨收显示收盘仅约+0.12%，不能沿用旧池11板。形态和算力标签都须以当日可见证据重建。',
      '杭电股份':'4板后只中断1个交易日，再涨停并延伸到2板。它可以在第二轮确认且价格收复首轮峰值后成为后续形态先例，不能在第一天修复前预知其两板结果。',
      '顺钠股份':'4板后中断4个交易日，再形成2板；其确认日期早于华电能源后续修复。两者被规则匹配为相近首轮高度/间隔候选，但顺钠自身修复后的5日价格表现并非持续上涨。',
      '华电能源':'4板、3个交易日中断、再2板的结构与较早确认的顺钠构成同主题形态候选。既要展示这类重合案例，也要披露同形态且同主题全年只有4个信号，不能推广成稳定规律。',
      '一鸣食品':'4板后再2板在图形上成立，但后续价格出现明显回撤。用第二轮板数宣布“战法成功”会掩盖实际入场时点和退出限制；这是形态成功与交易成功不同的直接例子。',
      '名雕股份':'4板后断1日再2板，但再涨停当日未收复断板日高点，随后价格走弱。单纯把“再涨停”命名为“强反包”会高估质量，必须明确是否真正覆盖前日上方抛压。'
    }
    for fig,label,code,center in casefigs:
        name=label.split('：')[0];lines=[f'### {label}',f'![{label}]({fig}.svg)',interpretations.get(name,'')]
        event=lr[lr.code.eq(code)&lr.date.eq(center)]
        if not event.empty:
            row=event.iloc[0];lines.append(f'**可核验路径：** {row.date}事前P5={row.pressure5:g}板，原股在{int(row.breakout_height)}板突破；本轮后来达到{int(row.chain_peak)}板，新增{int(row.extra_boards)}板（最终高度为事后事实）。信号后5日价格变化{pct(row.return5)}。')
        repair=rep[rep.code.eq(code)&rep.repair_date.eq(center)]
        if not repair.empty:
            row=repair.iloc[0];lines.append(f'**可核验路径：** {row.peak_date}首轮{int(row.first_height)}板，{row.break_date}断板，中断{int(row.gap_sessions)}个交易日，{row.repair_date}再涨停后第二轮{int(row.second_height)}板；收复断板高点={"是" if row.reclaim_break_high else "否"}。再涨停后5日价格变化{pct(row.repair_return5)}。')
        strategy='突破P5_原龙头' if not event.empty else '断板后首次再涨停'
        trade=tr[tr.code.eq(code)&tr.signal_date.eq(center)&tr.strategy.eq(strategy)&tr.hold.eq(3)]
        if len(trade):
            row=trade.iloc[0];lines.append(f'**统一交易代理：** {strategy}，状态`{row.status}`，已结算净收益{pct(row.net_return)}；空值并非零收益。')
        L.extend(lines)

    strict=pd.read_csv(out/'daily_market_warmup.csv')
    strict_rows=[]
    for column,label in [('height','有证据停牌暂停计数'),('height_strict','停牌即中断（严格市场日）')]:
        height=strict[column];pressure=height.shift(1).rolling(5,min_periods=5).max();br=height.gt(pressure)&pressure.notna();first=br&~br.shift(1,fill_value=False);first&=strict.date.ge(s['start'])
        idx=list(strict.index[first]);observed=[i for i in idx if i+5<len(strict)];wins=sum(height.iloc[i+1:i+6].max()>height.iloc[i] for i in observed)
        strict_rows.append({'高度定义':label,'全年最高板':int(height[strict.date.ge(s['start'])].max()),'P5独立突破':len(idx),'后5日市场创新高':count_rate(wins,len(observed))})
    strict_table=pd.DataFrame(strict_rows);strict_table.to_csv(out/'suspension_definition_sensitivity.csv',index=False,encoding='utf-8-sig')
    L.extend(['## 09 多层面战法矩阵',
    '| 战法 | 信号与确认 | 更合理的定位 | 否决/失效条件 |\n|---|---|---|---|\n| A 高度突破后原龙头领涨 | 独立破P5；次日原股仍晋级且居最高梯队，再下一日才可检验确认买点 | 市场方向与观察优先级；不无条件追最高 | 断板、接替、新题材否认、停牌再现高板、一字无成交；本样本中位收益不支持裸追 |\n| B 最高断板后晋级次高 | 断板前一日次高名单固定；断板日收盘仍晋级 | 有条件的接替候选，值得独立前向验证 | 原最高与次高一起退潮、候选梯队过高/过密；需保留未成交与尾部风险 |\n| C 突破后低位同题材补涨 | 破高后1–5日新首板，前三日无涨停，主题证据时间≤信号 | 把资金扩散列入观察，不把低价/低涨幅当充要条件 | 仅宽泛概念相同、旧标签、龙头已退潮、后排一字或公告否认 |\n| D 4板等断板后的修复 | 首轮≥3板；1–5日内再涨停；另看收复断板高点与实体反包 | 个股结构复盘与候选，不是通用高胜率买法 | 首轮高点压制未收复、反包后溢价差、确认后过度跳空；本样本多种修复买点中位为负 |\n| E 龙头先例后的形态模仿 | 先例在候选前已确认赚钱结构；相同首轮高度、相近间隔 | 小样本假设，必须前向登记全部候选 | 只凭图形相像、把未来成功当先例、只选赢家；本次前后半年不稳定 |\n| F 形态与题材共振 | E＋当时同题材，且公司公告不矛盾 | 更窄的待验证子集，不因条件更多就自动有效 | 样本极小；4个案例不足以估计稳健胜率 |',
    '### 日常复盘检查单',
    '1. 先确认报告日、板型、停牌与除权，避免错误连板高度。\n2. 记录H、P5、P10、P20及全部最高梯队；不能只留最知名个股。\n3. 区分本日破压、持续晋级、复牌恢复旧高。\n4. 记录原龙头次日状态，再记录新首板和次高晋级；不是见龙头涨就默认后排有溢价。\n5. 反包先写成“首轮N板→断G日→再R板”，另列高点收复，不混几天几板。\n6. 模仿关系先登记先例已知时间、候选时间和主题证据，再等结果；亏损、无成交和未结束照样保留。\n7. 确認可成交、T+1与跌停退出风险；不把“想买但买不到”记作盈利。',
    '## 10 稳健性：半年变化、成本、可成交与事件重叠',
    '前后半年只是时间分段，并非真正训练后锁定的样本外验证。多策略、多条件均会增加偶然发现的机会。',
    table(st[st.strategy.isin(order)&st.hold.eq(3)&st.fold.ne('全年')][['strategy','fold','signals','resolved','mean_net','median_net','win_rate']].rename(columns={'strategy':'策略','fold':'时段','signals':'信号','resolved':'已结算','mean_net':'均值','median_net':'中位数','win_rate':'赚钱率'}).assign(均值=lambda x:x['均值'].map(pct),中位数=lambda x:x['中位数'].map(pct),赚钱率=lambda x:x['赚钱率'].map(rate))),
    '![策略收益中位数](strategy_medians.svg)',
    '### 10.1 停牌定义敏感性',table(strict_table),'这张表只重算市场事件，不表示全部个股策略也以严格口径重新验证。停牌定义改变锚点，必须把普通突破与复牌恢复旧高分别观察。',
    '### 10.2 事件等权篮子与不重叠检验',
    '逐股表不是独立事件表。同日同策略所有候选按等权；未成交份额留现金，未结算/不可建模的篮子留空，不伪造零收益。非重叠检验在前一持仓结束前不接新信号；一旦无法确认退出则停止该策略后续样本，不能静默假设已退出。这会大幅缩小样本，不把结果包装成组合回测。',
    table(bstats[bstats.hold.eq(3)&bstats.strategy.isin(order)].rename(columns={'strategy':'策略','hold':'t后退出日','events':'事件数','complete':'可完整估值','positive':'盈利事件','mean':'均值','median':'中位数'}).assign(均值=lambda x:x['均值'].map(pct),中位数=lambda x:x['中位数'].map(pct))),
    '成本按完整一进一出10/30/60bp敏感性；主表30bp只是统一假设，不是按历史交易所印花税逐日精确计费。对成交量、冲击成本、封单队列和盘口可达性未建模。完整敏感性见cost_reference_sensitivity.csv。',
    '### 10.3 数据参考价质量',
    '独立昨收、复权缓存检查和未独立核验原价三类分开记录。复权缓存不同抓取批次可能出现因子跳变，本文只将可疑跳变作保守排除，不用它修造涨停价。腾讯前复权接口返回501、东财历史接口连接失败，已留错误记录，不声称全量复权校验成功。',
    '## 11 全部策略比较（逐股口径）',table(strat_table(ss)),
    '以上比例只对“已结算”作分母；不是全部信号胜率，更不是可实现的账户收益。完整5日观察、逐股入场/退出、延期退出、无成交和未结状态见trade_ledger.csv。',
    '## 12 数据审计与不可外推的边界',
    '- **来源**：腾讯未复权日线重建板高；已有宝股票历史昨收/停牌/ST状态作独立核验；新浪对研究中的龙头/关键修复样本及无数据代码再核验；公司公告核对特殊停复牌。原始响应和输入哈希全部留存。',
    '- **两源一致≠交易所真值。** 腾讯/新浪OHLC一致只能说明来源交叉一致，不能保证复权参考价、历史ST状态、停牌规则和投资者可成交性无误。',
    '- **已知公告差异**：锋龙17/18板起算不同；爱丽家居8月7日原始收盘仅较前日涨0.12%，不是收盘涨停，旧涨停缓存的11板不直接采用。停牌后恢复高度、真实断板、再涨停必须分别处理。',
    '- **标签不是完整历史数据库**：财联社分类缓存从2025-10-31起、东财分类缓存主要为2026年8月底以后；标签日期只是本地记录日期，并未证明每条标签在盘中已公开。统计不能使用信号日之后的标签，但仍存在历史标签回填/改写风险。',
    '- **范围不穷尽**：19只历史代码两源无日线；深市部分历史风险警示状态、除权参考价缺乏独立覆盖；名单虽含历史样本，仍不能宣称完全无幸存者偏差。',
    '- **缺日及边界**：不把缺失交易日压缩成连续交易日；年末未来窗口不完整留右删失；窗口外或停牌前发生的形态可能影响对起算点的解释。',
    '- **本研究不证明资金因果、不构成个股推荐**：图形模仿和概念联动只是价格/分类关联。没有逐笔成交、订单队列或资金身份，不能声称同一批资金复制战法。',
    '## 13 截止日观察与结论边界',
    f'截至{s["end"]}，主板样本H={s["latest"]["height"]}，P5={int(s["latest"]["pressure5"])}、P10={int(s["latest"]["pressure10"])}、P20={int(s["latest"]["pressure20"])}，最高梯队{s["latest"]["leader_names"]}。这是“触压”而不是“已经突破7板中期压力”。不使用9月16日盘中走势补到本次收盘样本，也不据历史回测直接给出明日买入名单。',
    '## 14 证据文件与复算',
    '- daily_market.csv / daily_market_warmup.csv：一年与预热高度、压力和状态。\n- breakout_events.csv / breakout_leaders.csv：全部突破与原龙头领涨路径。\n- leader_episodes.csv：全部市场领涨阶段，不只最终大龙头。\n- repair_events.csv / repair_height_summary.csv：所有首轮≥3板断板，修复失败及未结束也保留。\n- imitation_pairs.csv：先例—候选配对、时间顺序和主题证据。\n- follower_events.csv / follower_group_summary.csv：新首板候选及后续板高。\n- stock_daily_paths.csv.gz / 个股路径浏览器.html：研究相关个股逐日OHLC与板序列。\n- trade_ledger.csv / event_baskets.csv / nonoverlap_baskets.csv：交易代理与事件等权口径。\n- cost_reference_sensitivity.csv / same_day_theme_contrast.csv：成本、参考价与同日对照。\n- input_manifest.json / acquisition_status.csv / independent_sina_audit.csv / verification.json：原始来源、缺失与校验。',
    '复算命令（在项目根目录）：\n```text\npython tools/build_annual_height_study.py --out output/height_pressure_year_20260916 --end 2026-09-15\npython tools/render_annual_height_study.py --out output/height_pressure_year_20260916\npython -m pytest tests/test_annual_height_research.py -q\npython tools/verify_annual_height_study.py --out output/height_pressure_year_20260916\n```',
    '### 外部规则与公告来源',
    '- 上交所交易机制：https://english.sse.com.cn/start/trading/mechanism/\n- 锋龙股份2026-01-19复牌公告：原文留存AN202601181818070227.json。\n- 锋龙股份2026-01-26风险提示：原文留存AN202601251818397766.json，明确12月25日至1月23日17个交易日涨停。\n- 胜通能源2026-01-06复牌公告：原文留存AN202601051815519487.json。\n- 腾讯逐股来源URL与抓取时间在raw_tencent/*.json的_research_source中；新浪核验原始数据在raw_sina。',
    '\n**研究结论的优先级：可核验路径事实 > 条件概率 > 成交约束收益 > 尚待前向检验的战法假设。**'])
    md='\n\n'.join(L);(out/'一年连板高度与龙头模仿补涨研究.md').write_text(md,encoding='utf8')
    import markdown
    body=markdown.markdown(md,extensions=['tables','fenced_code','toc'])
    for name,svg in charts.items():
        # Embed graphics so the report remains useful when copied as a sidecar.
        import re
        svg=svg[svg.find('<svg'):]
        body=re.sub(r'<img alt="[^"]*" src="'+name+r'\.svg"\s*/?>',lambda m:'<figure>'+svg+'</figure>',body)
    css='''*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#eaf0f7;color:#233850;font:15px/1.8 "Microsoft YaHei",system-ui,sans-serif}header{background:#234a70;color:white;padding:42px max(24px,calc((100vw - 1320px)/2)) 30px}header small{letter-spacing:.18em;color:#b4d5ee}header h1{font-size:32px;line-height:1.35;max-width:1080px;margin:12px 0}nav{position:sticky;top:0;background:#f7fafd;z-index:3;padding:10px 22px;border-bottom:1px solid #c9d7e7;display:flex;gap:20px;overflow:auto;white-space:nowrap}nav a,a{color:#246593;text-decoration:none}main{max-width:1380px;margin:auto;background:#fff;padding:28px 42px 70px}h1{display:none}header h1{display:block}h2{font-size:25px;padding-top:30px;border-bottom:2px solid #96bfdc;color:#234a70;scroll-margin-top:68px}h3{font-size:19px;color:#2b597e;margin-top:30px}blockquote{border-left:5px solid #238a85;background:#e9f5f3;padding:15px 22px;margin:18px 0}table{border-collapse:collapse;font-size:12px;width:100%;margin:18px 0;display:block;overflow:auto;max-height:620px}thead{position:sticky;top:0;background:#e7eff8}th,td{border-bottom:1px solid #d9e3ec;padding:8px 11px;text-align:left;white-space:nowrap}tbody tr:nth-child(even){background:#f4f7fb}figure{margin:25px 0}figure svg{width:100%;height:auto;display:block}pre{overflow:auto;background:#eff4f9;padding:18px;border:1px solid #d3e0ec}p,li{max-width:1250px;overflow-wrap:anywhere}main{min-width:0}pre{max-width:100%}a:hover{text-decoration:underline}.meta{color:#d5e7f5;font-size:13px}.tools{padding:10px 0;font-weight:bold}footer{padding:30px;text-align:center;color:#587087}@media(max-width:800px){main{padding:18px}header{padding:24px}header h1{font-size:25px}table{font-size:11px}nav{gap:14px;padding:9px 15px}}@media print{nav{position:static}table{display:table;font-size:8px;max-height:none}figure,h3{break-inside:avoid}main{padding:0}header{padding:20px}body{background:white}}'''
    nav='<nav>'+''.join(f'<a href="#{a.get("id")}">{html.escape(a.text)}</a>' for a in __import__('bs4').BeautifulSoup(body,'html.parser').find_all('h2')[:10])+'</nav>'
    doc=f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="research-cutoff" content="{s["end"]}"><title>{title}</title><style>{css}</style></head><body><header><small>MARKET HEIGHT / LEADERSHIP / PRICE PATHS</small><h1>{title}</h1><div class="meta">{s["start"]} — {s["end"]} · {s["trading_days"]}个交易日 · {s["main_codes"]}只主板行情样本</div></header>{nav}<main><div class="tools">离线自包含报告 · 表格可横向滚动 · 所有结论保留失败与未完成样本 · <a href="annual_height_paths.html">打开个股路径浏览器 →</a></div>{body}</main><footer>2026-09-16 研究交付｜未发布、未下单｜不构成投资建议</footer></body></html>'
    (out/'一年连板高度与龙头模仿补涨研究.html').write_text(doc,encoding='utf8')
    rules=[dict(id='A',name='突破压力高度后的原龙头领涨',signal='H>P5独立起点；全部并列最高',confirmation='t+1原股仍晋级且最高；确认后最早t+2参与',evidence='breakout_events.csv / breakout_leaders.csv',status='观察框架；裸追未验证'),dict(id='B',name='最高板断板后晋级次高',signal='t-1次高名单固定，t旧最高全断且候选仍晋级',entry='t+1非涨跌停开盘',status='待独立前向验证'),dict(id='C',name='突破后同主题新首板补涨',signal='突破后1-5日首板；此前3日无板；历史主题仅向过去找',status='标签敏感、非因果'),dict(id='D',name='首轮断板后的反包多板',signal='首轮>=3板，中断1-5日再涨停；高点收复与实体反包分开',status='多种简单买点未显优势'),dict(id='E',name='赚钱先例后的形态模仿',signal='先例已确认第二轮>=2且收复原峰；后1-20日同初始高度、间隔差<=3',status='样本小，前后段不稳定')]
    (out/'战法规则_研究版.json').write_text(json.dumps({'as_of':s['end'],'status':'research_only','not_order_instructions':True,'rules':rules},ensure_ascii=False,indent=2),encoding='utf8')
    stock_browser(out,paths,d,rep,lr)
    (out/'annual_height_research.html').write_text(doc,encoding='utf8')
    print('rendered',len(doc),'characters',len(charts),'figures',paths.code.nunique(),'stock paths',flush=True)


def stock_browser(out,paths,d,rep,leaders):
    dataset={};catalog=[]
    for c,g in paths.groupby('code'):
        g=g.sort_values('date');name=str(g['name'].iloc[-1]);catalog.append([c,name,int(g.boards.max())])
        cols=['date','open','high','low','close','boards','market_height','pressure5']
        dataset[c]=json.loads(g[cols].to_json(orient='values',force_ascii=False))
    catalog.sort(key=lambda x:-x[2]);data=json.dumps(dataset,ensure_ascii=False,separators=(',',':')).replace('</','<\\/');options=''.join(f'<option value="{c}">{html.escape(n)} · {c} · 最高{h}板</option>' for c,n,h in catalog)
    body='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>全年个股路径浏览器</title><style>body{font:14px/1.7 "Microsoft YaHei",sans-serif;background:#edf3f8;color:#23415f;margin:0;padding:25px}main{max-width:1400px;margin:auto;background:white;padding:25px}h1{font-size:27px}select,input,button{max-width:100%;padding:9px;border:1px solid #b4cadd;background:white;color:#23415f;font-size:14px;margin:4px}svg{width:100%;height:auto}table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:7px;border-bottom:1px solid #dce6ef;text-align:right}td:first-child,th:first-child{text-align:left}.rows{max-height:420px;overflow:auto}main{min-width:0}#summary{overflow-wrap:anywhere}@media(max-width:650px){body{padding:8px}main{padding:13px}}.note{color:#657b91}a{color:#246593}</style><main><h1>全年个股路径浏览器</h1><p>选择股票查看未复权K线、个股板数、市场高度与事前P5。缺失/停牌留空；连续板数不等于累计涨停数。包括研究涉及的龙头、断板修复股与同主题候选。</p><label>检索 <input id="search" placeholder="名称或代码"></label><select id="stock">OPTIONS</select><select id="window"><option value="50">最近50日</option><option value="120">最近120日</option><option value="999" selected>全年</option></select><div id="summary"></div><div id="chart"></div><p class="note">红K为收≥开，绿K为收＜开；金点为收盘涨停。下方蓝柱为个股板数，绿色线为市场高度，橙线为事前P5。复牌、除权等特殊事件需结合主报告和原始公告。</p><div class="rows"><table><thead><tr><th>日期</th><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th><th>个股板数</th><th>市场高度</th><th>P5</th></tr></thead><tbody id="table"></tbody></table></div><p><a href="一年连板高度与龙头模仿补涨研究.html">返回专题报告</a></p></main><script>const DATA=DATASET;const stock=document.getElementById('stock'),win=document.getElementById('window'),all=Array.from(stock.options).map(x=>[x.value,x.text]);const esc=s=>String(s??'—').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));function draw(){let r=DATA[stock.value];if(!r)return;r=r.slice(-Number(win.value));const W=1280,H=500,pad=55,cw=(W-pad*2)/r.length,good=r.filter(x=>x[4]>0),lo=Math.min(...good.map(x=>x[3]))*.97,hi=Math.max(...good.map(x=>x[2]))*1.03,mh=Math.max(5,...r.map(x=>Math.max(x[5]||0,x[6]||0,x[7]||0))),X=i=>pad+cw*(i+.5),Y=p=>35+(hi-p)/(hi-lo)*275,B=v=>445-v/mh*95;let s='<svg viewBox="0 0 '+W+' '+H+'" role="img"><rect width="100%" height="100%" fill="#fff"/>';for(let j=0;j<5;j++){let y=35+j*275/4,v=hi-j*(hi-lo)/4;s+='<path d="M55 '+y+'H1225" stroke="#e3ebf1"/><text x="5" y="'+y+'" font-size="12" fill="#627b90">'+v.toFixed(2)+'</text>';}r.forEach((v,i)=>{let x=X(i),c=v[4]>=v[1]?'#c5536a':'#138675';if(v[4]>0){s+='<g><title>'+esc(v[0]+' 开'+v[1]+' 高'+v[2]+' 低'+v[3]+' 收'+v[4]+' '+v[5]+'板')+'</title><path d="M'+x+' '+Y(v[2])+'V'+Y(v[3])+'" stroke="'+c+'"/><rect x="'+(x-cw*.3)+'" y="'+Y(Math.max(v[1],v[4]))+'" width="'+Math.max(.8,cw*.6)+'" height="'+Math.max(1,Math.abs(Y(v[1])-Y(v[4])))+'" fill="'+c+'"/>'+(v[5]>0?'<circle cx="'+x+'" cy="'+Y(v[4])+'" r="2.2" fill="#ca951d"/>':'')+'</g>';}s+='<rect x="'+(x-cw*.3)+'" y="'+B(v[5]||0)+'" width="'+Math.max(.8,cw*.6)+'" height="'+(445-B(v[5]||0))+'" fill="#3272b8"/>';if(i%Math.ceil(r.length/12)===0)s+='<text x="'+x+'" y="480" font-size="11" fill="#627b90" text-anchor="middle">'+v[0].slice(5)+'</text>';});[6,7].forEach((k)=>{let pp=r.map((v,i)=>X(i)+','+B(v[k]||0)).join(' ');s+='<polyline points="'+pp+'" fill="none" stroke="'+(k===6?'#158978':'#ca951d')+'" stroke-width="1.3"/>';});document.getElementById('chart').innerHTML=s+'</svg>';document.getElementById('summary').textContent=stock.options[stock.selectedIndex].text+' ｜ '+r[0][0]+'—'+r[r.length-1][0]+' ｜ '+r.length+'个市场交易日';document.getElementById('table').innerHTML=r.slice().reverse().map(v=>'<tr>'+v.map(x=>'<td>'+esc(x)+'</td>').join('')+'</tr>').join('');}stock.onchange=draw;win.onchange=draw;document.getElementById('search').oninput=e=>{let q=e.target.value.trim().toLowerCase();stock.innerHTML=all.filter(x=>x[1].toLowerCase().includes(q)).map(x=>'<option value="'+x[0]+'">'+esc(x[1])+'</option>').join('');draw();};draw();</script></html>'''
    page=body.replace('OPTIONS',options).replace('DATASET',data)
    (out/'个股路径浏览器.html').write_text(page,encoding='utf8')
    (out/'annual_height_paths.html').write_text(page.replace('一年连板高度与龙头模仿补涨研究.html','annual_height_research.html'),encoding='utf8')
if __name__=='__main__':main()
