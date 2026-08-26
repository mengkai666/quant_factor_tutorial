"""一次性复盘: 最近两周 连板梯队 × 龙头接替 × 板块 × 补涨 × 连板情绪周期。
纯本地缓存, 零联网。ZT梯队真源=涨停历史缓存.csv, A/D真源=价格缓存(重算), 板块=东财概念+申万兜底。
"""
import csv, collections, sys, io
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

WIN = ['20260811','20260812','20260813','20260814','20260817','20260818',
       '20260819','20260820','20260821','20260824','20260825']

def dd(d): return f"{d[4:6]}/{d[6:8]}"

# ── 1. ZT 缓存 → by_day ──
zt = collections.defaultdict(list)   # date -> [(code,name,board)]
dt = collections.defaultdict(list)
alldates=set()
with open('data/涨停历史缓存.csv', encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        d=r['日期']; alldates.add(d)
        b=r.get('连板数','').strip()
        b=int(float(b)) if b else 1
        if r['类型']=='ZT': zt[d].append((r['代码'], r.get('名称',''), b))
        elif r['类型']=='DT': dt[d].append((r['代码'], r.get('名称','')))

# ── 2. 东财概念 mainline/sub, 最新已知兜底 ──
em=collections.defaultdict(dict)   # date->code->(sub,mainline)
em_latest={}                        # code->(sub,mainline) 最新非空
with open('data/em_stock_plate_cache.csv', encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        sub=r.get('sub','').strip(); ml=r.get('mainline','').strip()
        em[r['date']][r['code']]=(sub,ml)
        if ml: em_latest[r['code']]=(sub,ml)
# 申万兜底
sw={}
with open('data/industry_cache.csv', encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        sw[r['code']]=r['industry']

def sector(code, d):
    s=em.get(d,{}).get(code)
    if s and s[1]: return s[1], s[0]
    if code in em_latest: return em_latest[code]
    ind=sw.get(code,'')
    # 申万行业去掉字母前缀数字
    import re
    ind=re.sub(r'^[A-Z]\d+','',ind)
    return ind or '—', ''

# ── 3. 梯队日矩阵 + 龙头 ──
print("="*72)
print("【一】连板梯队日矩阵  (板级: 只数)   最高板股名")
print("="*72)
for d in WIN:
    if d not in zt: continue
    lst=zt[d]
    dist=collections.Counter(b for _,_,b in lst)
    maxb=max(dist) if dist else 0
    # 梯队字符串: 只显示 1..maxb
    cells=[]
    for b in range(1,maxb+1):
        c=dist.get(b,0)
        cells.append(f"{b}板×{c}" if c else f"{b}板×0")
    # 最高板股名
    tops=[f"{n}" for c,n,b in lst if b==maxb]
    dtn=len(dt.get(d,[]))
    print(f"{dd(d)}  ZT{len(lst):3d} DT{dtn:3d} 高{maxb}板 | "+"  ".join(cells))
    print(f"       └ {maxb}板龙头: {', '.join(tops)}")

# ── 4. 龙头接替谱系: 逐日追踪 3板+ 个股的晋级/断板 ──
print()
print("="*72)
print("【二】3板+ 梯队个股 逐日追踪 (晋级链 / 断板)")
print("="*72)
# 建 code->{date:board}
track=collections.defaultdict(dict)
name_of={}
for d in WIN:
    for c,n,b in zt.get(d,[]):
        track[c][d]=b; name_of[c]=n
# 只看曾达到 3板+ 的个股
elite=[c for c in track if max(track[c].values())>=3]
def board_at(c,d): return track[c].get(d,0)
# 按最高板排序
elite.sort(key=lambda c:-max(track[c].values()))
for c in elite:
    seq=[]
    for d in WIN:
        b=track[c].get(d)
        if b: seq.append(f"{dd(d)}:{b}")
        else: seq.append(f"{dd(d)}:· ")
    peak=max(track[c].values())
    sec,sub=sector(c,WIN[-1])
    print(f"{name_of[c]:<6}[{peak}板] {sec[:8]:<9}| "+" ".join(seq))

# ── 5. 晋级率/断板 逐日 ──
print()
print("="*72)
print("【三】梯队晋级率 & 断板 (昨N板→今日去向)")
print("="*72)
prev=None
for d in WIN:
    if d not in zt: continue
    today={c:b for c,_,b in zt[d]}
    if prev:
        pd_, pmap = prev
        # 昨日 2板+ 的股今日晋级(板级+1)/平/断
        adv=stay=broke=0; broke_names=[]
        for c,pb in pmap.items():
            if pb<2: continue
            tb=today.get(c,0)
            if tb>=pb+1: adv+=1
            elif tb>=1: stay+=1
            else:
                broke+=1
                if pb>=3: broke_names.append(f"{name_of.get(c,c)}({pb}板)")
        base=adv+stay+broke
        rate=f"{adv/base*100:.0f}%" if base else "-"
        print(f"{dd(prev[0])}→{dd(d)}: 昨2板+ {base}只 → 晋级{adv} 平/退级{stay} 断板{broke} (晋级率{rate})")
        if broke_names: print(f"        高位断板: {', '.join(broke_names)}")
    prev=(d,today)

# ── 6. A/D 真源重算 (价格缓存) ──
print()
print("="*72)
print("【四】情绪弧: A/D(价格重算) · 涨停/跌停 · 最高板")
print("="*72)
px=pd.read_csv('data/price_history_cache.csv', usecols=['code','date','close_qfq'])
px=px[px['date']>='2026-08-08'].copy()
px['date']=px['date'].str.replace('-','')
px=px.sort_values(['code','date'])
px['prev']=px.groupby('code')['close_qfq'].shift(1)
px['ret']=(px['close_qfq']/px['prev']-1)
ad={}
for d,g in px.groupby('date'):
    up=(g['ret']>0.001).sum(); down=(g['ret']<-0.001).sum()
    ad[d]=(int(up),int(down))
print(f"{'日期':<7}{'涨/跌家数':<16}{'A/D占比':<9}{'涨停':<6}{'跌停':<6}{'最高板':<7}体温")
for d in WIN:
    up,down=ad.get(d,(0,0))
    occ=up/(up+down) if up+down else 0
    ztn=len(zt.get(d,[])); dtn=len(dt.get(d,[]))
    maxb=max((b for _,_,b in zt.get(d,[])), default=0)
    if occ>=0.6: temp='🔥过热'
    elif occ<=0.35: temp='🧊冰点'
    else: temp='中性'
    print(f"{dd(d):<7}{f'{up}/{down}':<16}{occ*100:>5.0f}%   {ztn:<6}{dtn:<6}{maxb:<7}{temp}")

# ── 7. 板块归因: 窗口内 涨停总次数 Top 板块 + 补涨识别 ──
print()
print("="*72)
print("【五】板块热度 (窗口内涨停总次数) + 龙头分布")
print("="*72)
sec_zt=collections.Counter()          # 板块 -> 涨停次数
sec_days=collections.defaultdict(set) # 板块 -> 出现日
sec_stocks=collections.defaultdict(collections.Counter)
for d in WIN:
    for c,n,b in zt.get(d,[]):
        s,_=sector(c,d)
        sec_zt[s]+=1; sec_days[s].add(d); sec_stocks[s][n]+=1
for s,cnt in sec_zt.most_common(14):
    top=', '.join(f"{n}×{k}" for n,k in sec_stocks[s].most_common(4))
    print(f"{s[:10]:<11} 涨停{cnt:3d}次 / {len(sec_days[s])}日 | {top}")
