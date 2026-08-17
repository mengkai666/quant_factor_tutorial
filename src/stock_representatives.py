# -*- coding: utf-8 -*-
"""四象限个股代表 (全市场无偏, 独立模块)。

修的问题: 早期版本想给板块配"板块内个股代表", 只能用 data/em_stock_plate_cache.csv
(东财概念归因), 但那份缓存有两个缺陷, 印到报告上就是错:
  1. **选择偏差**: 只覆盖主线天梯里的强势股 (~2300/5190)。在"已经很强的样本"里
     挑强的, 结论必然虚高。
  2. **多标签误标**: 东财概念是多标签 (一只股几十个概念), 投票取众数会串板 ——
     典型如 *ST传智 被打成"消费电子"(它其实是教育)。个股级错误会直接暴露在报告上。

**本模块的口径决定 (关键)**: 不按板块归因去挑代表, 而是**直接在全市场 5190 只上
按象限分类**。象限判定只用价格与涨停 —— 两个无偏真源, 不需要任何板块映射, 所以
从根上不存在归因误标。行业只作为**展示标注** (证监会行业, 来自 baostock, 全市场
单标签互斥), 不参与任何计算。

与上方板块表的衔接: 板块表说"贵金属属于超跌反弹", 本表列出全市场超跌反弹的代表股,
两者靠**同一套象限定义**对齐, 不会出现"指数说涨、代表股说跌"的错配。

⚠️ 为什么不用同花顺/东财行业成分股 (试过, 不可行):
   同花顺行业详情页 (q.10jqka.com.cn/thshy/detail) 每个行业**硬顶 100 只且按当日
   涨跌幅排序** —— 化学制品真实 171 只只能拿到前 100, 拿到的还是"今天涨得最好的
   100 只", 选择偏差比它要替换的东财缓存更重; 其 ajax 接口带 hexin-v token 仍 403,
   并发还会被限流 (401/403)。东财板块接口本机可达性时通时断。详见项目记忆。

数据源 (全部无偏 + 全市场):
  data/price_history_cache.csv   5190 只日收盘 → 各阶段收益
  data/industry_cache.csv        5190 只证监会行业 (单标签, 仅作标注)
  data/涨停历史缓存.csv           涨停/连板 → 龙头认定
  qt.gtimg.cn 批量报价           总市值 → 中军认定 (项目既有可靠快速路径)

用法:
    from stock_representatives import build_representatives, render_representatives_html
    reps = build_representatives(phases, index_drawdown)
"""
from __future__ import annotations

import os

import pandas as pd

from paths import (
    INDUSTRY_CACHE,
    PRICE_CACHE,
    SECURITY_MASTER_CACHE,
    UNIVERSE_CACHE,
    ZT_CACHE_FILE,
)
from time_utils import filter_completed_rows
from data_sources.models import normalize_code
from data_sources.name_resolver import NameResolution, resolve_names
from data_sources.price_provider import build_price_matrix

TOP_N = 6                # 每象限展示几只
MID_CAP_MIN = 100.0      # 中军最小总市值 (亿元)
# 代表股市值下限: 不做这道过滤时, 象限榜会被 30~50 亿的妖股/ST 重组股垄断
# (实测 独立主线 前三名全是 +100% 以上的连板妖股), 它们的涨幅不代表板块逻辑,
# 印在"真正的新方向"标题下是误导。设 0 可关掉此过滤。
REP_CAP_MIN = 50.0
# ST/*ST 保留但打标 —— 它们是真实的资金行为, 不该隐藏; 但必须让读者一眼看出
# 这是重组/炒作而非产业逻辑。


def _load_name_resolution() -> NameResolution:
    """Load report-date security names from the shared master caches."""
    security_master = None
    universe = None
    industry = None
    try:
        security_master = pd.read_csv(SECURITY_MASTER_CACHE, dtype=str)
    except Exception:
        pass
    try:
        universe = pd.read_csv(UNIVERSE_CACHE, dtype=str)
    except Exception:
        pass
    try:
        industry = pd.read_csv(INDUSTRY_CACHE, dtype=str)
    except Exception:
        pass
    current = security_master
    if current is None or current.empty:
        current = universe
    return resolve_names(universe=current, industry=industry)


def build_turning_stock_leaders(
    phases: dict,
    *,
    name_resolution: NameResolution | None = None,
    expected_universe_size: int | None = None,
    top_n: int = 5,
    min_coverage: float = 0.80,
) -> dict:
    """Rank full-A-share returns over the mechanically detected bottom interval."""
    interval = (phases or {}).get("底部至今")
    if not interval or len(interval) != 2 or not os.path.exists(PRICE_CACHE):
        return {"usable": False, "coverage": 0.0, "rows": []}

    names = name_resolution or _load_name_resolution()
    if not names.names:
        return {"usable": False, "coverage": 0.0, "rows": []}

    prices = pd.read_csv(PRICE_CACHE)
    prices = filter_completed_rows(prices, "date")
    matrix = build_price_matrix(prices, "qfq", allow_legacy=True)
    if matrix.empty:
        return {"usable": False, "coverage": 0.0, "rows": []}

    dates = [str(value) for value in matrix.index]

    def near(boundary):
        eligible = [value for value in dates if value <= str(boundary)]
        return eligible[-1] if eligible else None

    start_date, end_date = near(interval[0]), near(interval[1])
    if not start_date or not end_date or start_date == end_date:
        return {"usable": False, "coverage": 0.0, "rows": []}

    candidates = sorted(set(matrix.columns) & set(names.names))
    if not candidates:
        return {"usable": False, "coverage": 0.0, "rows": []}
    start = pd.to_numeric(matrix.loc[start_date, candidates], errors="coerce")
    end = pd.to_numeric(matrix.loc[end_date, candidates], errors="coerce")
    valid = start.notna() & end.notna() & start.gt(0)
    denominator = int(expected_universe_size or len(names.names))
    coverage = round(float(valid.sum()) / denominator, 4) if denominator > 0 else 0.0
    if coverage < min_coverage:
        return {"usable": False, "coverage": coverage, "rows": []}

    returns = ((end[valid] / start[valid] - 1) * 100).dropna()
    ranked = sorted(returns.items(), key=lambda item: (-float(item[1]), str(item[0])))
    rows = []
    for code, value in ranked[:max(0, int(top_n))]:
        normalized = normalize_code(code)
        name = names.names.get(normalized, normalized)
        rows.append({
            "code": normalized,
            "name": name,
            "return": round(float(value), 2),
            "st": "ST" in str(name).upper(),
        })
    return {"usable": True, "coverage": coverage, "rows": rows}

def _phase_returns(phases):
    """全市场个股各阶段收益 %。index=code, columns=阶段名。

    本地缓存可能包含早期 close_legacy 与近期 close_qfq，必须按个股拼接后再算收益。
    """
    if not os.path.exists(PRICE_CACHE):
        return pd.DataFrame()
    df = pd.read_csv(PRICE_CACHE)
    df = filter_completed_rows(df, 'date')
    px = build_price_matrix(df, 'qfq', allow_legacy=True)
    if px.empty:
        return pd.DataFrame()
    dates = [str(value) for value in px.index]

    def near(d):
        c = [x for x in dates if x <= d]
        return c[-1] if c else None

    out = {}
    for p, (s, e) in phases.items():
        ds, de = near(s), near(e)
        if not ds or not de or ds == de:
            continue
        start = pd.to_numeric(px.loc[ds], errors='coerce')
        end = pd.to_numeric(px.loc[de], errors='coerce')
        out[p] = (end / start - 1) * 100
    return pd.DataFrame(out)


def _zt_stats(since):
    """since (YYYYMMDD) 之后每只股的涨停次数与最高连板。"""
    if not os.path.exists(ZT_CACHE_FILE):
        return pd.DataFrame(columns=['code6', '涨停次数', '最高连板'])
    zt = pd.read_csv(ZT_CACHE_FILE, encoding='utf-8-sig')
    zt = filter_completed_rows(zt, '日期')
    zt.columns = [c.strip() for c in zt.columns]
    zt = zt[(zt['类型'] == 'ZT') & (zt['日期'].astype(str) >= since)]
    if zt.empty:
        return pd.DataFrame(columns=['code6', '涨停次数', '最高连板'])
    zt['code6'] = zt['代码'].astype(str).str.extract(r'(\d{6})')[0]
    g = zt.groupby('code6').agg(涨停次数=('日期', 'count'),
                                最高连板=('连板数', 'max')).reset_index()
    return g


def _fetch_mktcap(codes, chunk=500):
    """腾讯批量报价取总市值 (亿元)。返回 {code: 亿元}。失败返回 {} (中军判定降级)。"""
    import requests
    s = requests.Session()
    s.trust_env = True
    out = {}
    for i in range(0, len(codes), chunk):
        part = codes[i:i + chunk]
        try:
            r = s.get('https://qt.gtimg.cn/q=' + ','.join(part), timeout=15)
            r.encoding = 'gbk'
            for line in r.text.strip().split('\n'):
                if '"' not in line:
                    continue
                try:
                    code = line.split('=')[0].split('_')[-1].strip()
                    f = line.split('"')[1].split('~')
                    if len(f) > 45 and f[45]:
                        out[code] = float(f[45])      # f45 = 总市值(亿元)
                except Exception:
                    continue
        except Exception:
            continue
    return out


def build_representatives(phases, index_drawdown, fetch_cap=True):
    """按四象限挑全市场代表股。

    phases: {阶段名: (start, end)}, 来自 phase_resonance.detect_phases
    index_drawdown: 指数下跌段幅度 % (负数), 作抗跌基准线

    象限判定 (与板块层同一套定义, 只是粒度到个股):
      独立主线 —— 下跌段跌幅小于指数 (抗跌) 且 底部至今进前 30%
      超跌反弹 —— 下跌段跌幅大于指数 (跌深) 且 底部至今进前 30%
      深度受损 —— 跌深 且 底部至今进后 30%
      防御退潮 —— 抗跌 但 底部至今进后 30%
    每象限内再标角色: 龙头(有涨停) / 中军(市值≥100亿且无涨停) / 弹性(其余)
    """
    if '下跌段' not in phases or '底部至今' not in phases:
        return None
    ret = _phase_returns(phases)
    if ret.empty or '下跌段' not in ret.columns or '底部至今' not in ret.columns:
        return None

    df = ret.reset_index().rename(columns={'index': 'code'})
    if 'code' not in df.columns:
        df = df.rename(columns={df.columns[0]: 'code'})

    # 中文名称来自当前证券主表；行业缓存只负责展示行业和最终兜底。
    # GitHub Actions 冷缓存时 industry_cache 可能不存在，名称也不能退化成证券代码。
    name_resolution = _load_name_resolution()
    df['name'] = df['code'].map(name_resolution.names)
    try:
        ic = pd.read_csv(INDUSTRY_CACHE, dtype=str)
        keep = [column for column in ('code', 'name', 'industry') if column in ic.columns]
        ic = ic[keep].copy()
        if 'code' in ic.columns:
            ic['code'] = ic['code'].map(normalize_code)
            ic = ic.drop_duplicates('code', keep='last')
            if 'name' in ic.columns:
                ic = ic.rename(columns={'name': 'industry_name'})
            df = df.merge(ic, on='code', how='left')
    except Exception:
        pass

    if 'industry_name' in df.columns:
        df['name'] = df['name'].fillna(df['industry_name'])
    if 'industry' not in df.columns:
        df['industry'] = ''
    df['name'] = df['name'].where(
        df['name'].notna() & (df['name'].astype(str).str.strip() != ''),
        df['code'],
    )
    df['industry'] = df['industry'].fillna('')

    # 涨停 (龙头认定)
    since = phases['底部至今'][0].replace('-', '')
    df['code6'] = df['code'].str.extract(r'(\d{6})')[0]
    df = df.merge(_zt_stats(since), on='code6', how='left')
    df['涨停次数'] = df['涨停次数'].fillna(0).astype(int)

    df = df.dropna(subset=['下跌段', '底部至今'])
    if df.empty:
        return None

    # 市值 (中军认定); 失败则中军判定降级为"抗跌+无涨停"
    cap_ok = False
    if fetch_cap:
        caps = _fetch_mktcap(df['code'].tolist())
        if len(caps) > len(df) * 0.5:
            df['市值'] = df['code'].map(caps)
            cap_ok = True
    if not cap_ok:
        df['市值'] = None

    strong = df['底部至今'].quantile(0.70)
    weak = df['底部至今'].quantile(0.30)
    base = index_drawdown
    groups = {
        '独立主线': df[(df['下跌段'] > base) & (df['底部至今'] >= strong)],
        '超跌反弹': df[(df['下跌段'] <= base) & (df['底部至今'] >= strong)],
        '防御退潮': df[(df['下跌段'] > base) & (df['底部至今'] < weak)],
        '深度受损': df[(df['下跌段'] <= base) & (df['底部至今'] < weak)],
    }

    def role(r):
        if r['涨停次数'] > 0:
            return '龙头'
        cap = r.get('市值')
        if cap is not None and pd.notna(cap) and cap >= MID_CAP_MIN:
            return '中军'
        return '弹性'

    out = {}
    for k, g in groups.items():
        if g.empty:
            continue
        g = g.copy()
        g['角色'] = g.apply(role, axis=1)
        g['ST'] = g['name'].astype(str).str.contains('ST', case=False, na=False)
        # 市值下限只过滤"展示的代表", 不影响上面的象限阈值 (阈值必须全市场算才无偏)
        if REP_CAP_MIN > 0 and cap_ok:
            big = g[g['市值'].notna() & (g['市值'] >= REP_CAP_MIN)]
            if len(big) >= 3:      # 过滤后太少就不过滤, 免得象限空掉
                g = big
        # 领涨象限取涨幅最大, 掉队象限取涨幅最小 (都是"最能代表该象限"的极端)
        asc = k in ('防御退潮', '深度受损')
        picks = []
        # 每个角色至少给一个名额, 保证龙头/中军/弹性都露面, 再按涨幅补满
        for rl in ('龙头', '中军', '弹性'):
            sub = g[g['角色'] == rl]
            if not sub.empty:
                picks.append(sub.nsmallest(1, '底部至今') if asc
                             else sub.nlargest(1, '底部至今'))
        rest = g[~g['code'].isin(pd.concat(picks)['code'])] if picks else g
        n_rest = max(0, TOP_N - sum(len(p) for p in picks))
        if n_rest and not rest.empty:
            picks.append(rest.nsmallest(n_rest, '底部至今') if asc
                         else rest.nlargest(n_rest, '底部至今'))
        sel = pd.concat(picks).sort_values('底部至今', ascending=asc)
        out[k] = sel.to_dict('records')

    return {'groups': out, 'n_total': len(df), 'cap_ok': cap_ok,
            'strong_q': round(strong, 2), 'weak_q': round(weak, 2),
            'base': base, 'phases': phases}


_Q_META = {
    '独立主线': ('#3fb950', '抗跌 + 领涨'),
    '超跌反弹': ('#d29922', '跌深 + 弹猛'),
    '防御退潮': ('#8b949e', '抗跌 但 涨不动'),
    '深度受损': ('#f85149', '跌深 且 没修复'),
}
_ROLE_CLR = {'龙头': '#f85149', '中军': '#58a6ff', '弹性': '#8b949e'}


def _c(v):
    if v is None or pd.isna(v):
        return '#8b949e'
    return '#f85149' if v > 0 else ('#3fb950' if v < 0 else '#8b949e')


def _short_ind(s):
    """'C39计算机、通信和其他电子设备制造业' -> '计算机通信电子' (去码去冗字, 截断)。"""
    s = str(s or '')
    s = s[3:] if len(s) > 3 and s[0].isalpha() else s
    s = s.replace('制造业', '').replace('和其他', '').replace('业', '')
    s = s.replace('、', '').replace('及', '')
    return s[:8] if s else '—'


def render_representatives_html(reps):
    """四象限个股代表表格。reps 为 None 返回空串。"""
    if not reps or not reps.get('groups'):
        return ''
    blocks = ''
    for q, (clr, cond) in _Q_META.items():
        rows_data = reps['groups'].get(q)
        if not rows_data:
            continue
        rows = ''
        for r in rows_data:
            cap = r.get('市值')
            cap_s = (f'{cap:.0f}亿' if cap is not None and pd.notna(cap) else '—')
            zt = r.get('涨停次数') or 0
            lb = r.get('最高连板')
            zt_s = (f'{zt}次' + (f'/{int(lb)}板' if lb and pd.notna(lb) and lb > 1 else '')
                    if zt else '—')
            rl = r.get('角色', '')
            st_badge = (
                '<span style="color:#d29922;font-size:10px;font-weight:bold;">ST炒作</span>'
                if r.get('ST') else ''
            )
            rows += (
                f'<tr style="border-bottom:1px solid rgba(48,54,61,0.4);">'
                f'<td style="padding:5px 7px;color:#e6edf3;white-space:nowrap;">'
                f'{r["name"]}'
                f'{st_badge}'
                f'<span style="color:#6e7681;font-size:10px;"> {r["code"]}</span></td>'
                f'<td style="padding:5px 7px;"><span style="color:{_ROLE_CLR.get(rl, "#8b949e")};'
                f'font-size:11px;font-weight:bold;">{rl}</span></td>'
                f'<td style="padding:5px 7px;color:#8b949e;font-size:11px;white-space:nowrap;">'
                f'{_short_ind(r.get("industry"))}</td>'
                f'<td style="padding:5px 7px;color:{_c(r.get("下跌段"))};text-align:right;'
                f'font-size:12px;">{r["下跌段"]:+.1f}</td>'
                f'<td style="padding:5px 7px;color:{_c(r.get("底部至今"))};text-align:right;'
                f'font-weight:bold;font-size:12px;">{r["底部至今"]:+.1f}%</td>'
                f'<td style="padding:5px 7px;color:#8b949e;text-align:right;font-size:11px;'
                f'white-space:nowrap;">{zt_s}</td>'
                f'<td style="padding:5px 7px;color:#8b949e;text-align:right;font-size:11px;'
                f'white-space:nowrap;">{cap_s}</td>'
                f'</tr>')
        blocks += (
            f'<div style="flex:1;min-width:340px;background:rgba(255,255,255,0.03);'
            f'border-left:3px solid {clr};border-radius:6px;padding:10px 12px;">'
            f'<div style="color:{clr};font-size:13px;font-weight:bold;margin-bottom:6px;">{q} '
            f'<span style="color:#8b949e;font-size:11px;font-weight:normal;">({cond})</span></div>'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<tr style="color:#6e7681;font-size:10px;">'
            f'<td style="padding:2px 7px;">个股</td><td style="padding:2px 7px;">角色</td>'
            f'<td style="padding:2px 7px;">行业</td>'
            f'<td style="padding:2px 7px;text-align:right;">下跌段</td>'
            f'<td style="padding:2px 7px;text-align:right;">底部至今</td>'
            f'<td style="padding:2px 7px;text-align:right;">涨停</td>'
            f'<td style="padding:2px 7px;text-align:right;">市值</td></tr>'
            f'{rows}</table></div>')

    cap_note = '' if reps.get('cap_ok') else ' (市值未取到, 中军判定与市值下限已降级)'
    floor_note = (f' · 代表股市值 ≥{REP_CAP_MIN:.0f}亿 (滤掉垄断榜单的妖股, 阈值仍按全市场算)'
                  if REP_CAP_MIN > 0 and reps.get('cap_ok') else '')
    return f'''
      <div style="color:#8b949e;font-size:12px;font-weight:bold;margin:16px 0 2px;">
        👥 四象限个股代表 —— 全市场 {reps['n_total']} 只按同一套象限定义直接分类{cap_note}
      </div>
      <div style="color:#6e7681;font-size:11px;margin-bottom:6px;">
        角色: <span style="color:#f85149;">龙头</span>=区间内有涨停 ·
        <span style="color:#58a6ff;">中军</span>=市值≥{MID_CAP_MIN:.0f}亿且无涨停 ·
        <span style="color:#8b949e;">弹性</span>=其余 ｜
        象限阈值: 抗跌线 {reps['base']:.1f}% · 强 ≥{reps['strong_q']}% · 弱 &lt;{reps['weak_q']}%{floor_note} ｜
        行业为证监会分类(单标签), 仅作标注不参与计算
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;">{blocks}</div>
    '''


if __name__ == '__main__':
    from phase_resonance import build_phase_resonance
    r = build_phase_resonance()
    if not r:
        print('无有效阶段结构')
    else:
        reps = build_representatives(r['det']['phases'], r['det']['drawdown'])
        if not reps:
            print('代表股计算失败')
        else:
            print(f"全市场 {reps['n_total']} 只 | 市值可用 {reps['cap_ok']} | "
                  f"强 ≥{reps['strong_q']}% 弱 <{reps['weak_q']}% 抗跌线 {reps['base']}%")
            for q, lst in reps['groups'].items():
                print(f'\n[{q}]')
                for x in lst:
                    cap = x.get('市值')
                    print(f"  {x['name']:<8} {x['code']} {x.get('角色',''):<4} "
                          f"下跌{x['下跌段']:>7.1f} 底部至今{x['底部至今']:>7.1f}% "
                          f"涨停{x.get('涨停次数',0)} 市值{f'{cap:.0f}亿' if cap and pd.notna(cap) else '—'} "
                          f"[{_short_ind(x.get('industry'))}]")
