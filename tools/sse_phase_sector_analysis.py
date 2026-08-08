"""上证指数见底→震荡 阶段性板块共振分析 (一次性研究脚本)

流程:
  1. 拉上证指数日线, 划分 下跌段 / 见底段 / 震荡段 / 突破日
  2. 拉东财行业板块 + 概念板块 日线历史, 算各段涨跌幅
  3. 交叉: 下跌段跌幅 vs 反弹段涨幅 (相关性 → 超跌反弹 or 新主线)
  4. 板块内代表个股 (价格缓存算区间涨幅 + 涨停缓存看情绪)

输出 JSON 到 output/sse_phase_analysis.json, 便于后续复用。
"""
import os
import sys
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# 必须在 import requests 之前清掉代理: Clash 白名单不含东财域名 (见项目记忆)
for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
    os.environ.pop(k, None)

import pandas as pd  # noqa: E402
import akshare as ak  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import paths  # noqa: E402

OUT_JSON = os.path.join(paths.OUTPUT_DIR, 'sse_phase_analysis.json')

# === 阶段定义 (基于上证指数结构; 端点为收盘价基准日) ===
# 下跌段: 6/22 顶 → 7/17 最低收盘
# 见底段: 7/17 恐慌 → 7/21 V 反转确认 (7/20 盘中最低 3741)
# 震荡段: 7/21 → 8/04 箱体
# 突破日: 8/05
PHASES = {
    '下跌段': ('2026-06-22', '2026-07-17'),
    '见底段': ('2026-07-17', '2026-07-21'),
    '震荡段': ('2026-07-21', '2026-08-04'),
    '突破日': ('2026-08-04', '2026-08-05'),
    '底部至今': ('2026-07-17', '2026-08-05'),
}
START, END = '20260615', '20260806'


def fetch_sse():
    df = ak.stock_zh_index_daily(symbol='sh000001')
    df['date'] = pd.to_datetime(df['date'])
    return df[df['date'] >= '2026-05-20'].reset_index(drop=True)


def _hist(kind, name):
    """拉单个板块日线, 失败重试。kind: 'ind' | 'con'"""
    fn = (ak.stock_board_industry_hist_em if kind == 'ind'
          else ak.stock_board_concept_hist_em)
    for i in range(3):
        try:
            d = fn(symbol=name, start_date=START, end_date=END,
                   period='日k', adjust='')
            if d is not None and len(d):
                d = d.rename(columns={'日期': 'date', '收盘': 'close',
                                      '成交额': 'amount', '涨跌幅': 'pct'})
                d['date'] = pd.to_datetime(d['date']).dt.strftime('%Y-%m-%d')
                return name, d[['date', 'close', 'amount', 'pct']]
        except Exception:
            time.sleep(0.6 * (i + 1))
    return name, None


def fetch_all(kind, names, workers=8):
    res = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_hist, kind, n): n for n in names}
        done = 0
        for f in as_completed(futs):
            n, d = f.result()
            done += 1
            if d is not None:
                res[n] = d
            if done % 40 == 0:
                print(f'    {done}/{len(names)} ...', flush=True)
    print(f'    完成 {len(res)}/{len(names)}')
    return res


def phase_ret(d, s, e):
    """区间收盘涨跌幅 %; 端点日缺失则取最近可用。"""
    ds = d[d.date <= s]
    de = d[d.date <= e]
    if not len(ds) or not len(de):
        return None
    c0, c1 = ds.close.iloc[-1], de.close.iloc[-1]
    if not c0:
        return None
    return round((c1 / c0 - 1) * 100, 2)


def build_table(hists):
    rows = []
    for name, d in hists.items():
        r = {'板块': name}
        for ph, (s, e) in PHASES.items():
            r[ph] = phase_ret(d, s, e)
        # 成交额: 震荡段日均 vs 下跌段日均 (资金流入强度)
        try:
            fall = d[(d.date >= '2026-06-23') & (d.date <= '2026-07-17')].amount.mean()
            osc = d[(d.date >= '2026-07-21') & (d.date <= '2026-08-05')].amount.mean()
            r['量比'] = round(osc / fall, 2) if fall else None
        except Exception:
            r['量比'] = None
        rows.append(r)
    return pd.DataFrame(rows)


def main():
    print('=== 1. 上证指数 ===')
    sse = fetch_sse()
    sse_out = sse.assign(date=sse.date.dt.strftime('%Y-%m-%d'))
    sse_out['pct'] = (sse_out.close.pct_change() * 100).round(2)
    print(sse_out.tail(20)[['date', 'close', 'low', 'pct', 'volume']].to_string(index=False))
    print('\n各阶段上证涨跌幅:')
    for ph, (s, e) in PHASES.items():
        print(f'  {ph}: {phase_ret(sse_out, s, e)}%')

    print('\n=== 2. 行业板块 ===')
    ind_names = ak.stock_board_industry_name_em()['板块名称'].tolist()
    print(f'  {len(ind_names)} 个行业板块')
    ind = fetch_all('ind', ind_names)
    ind_tab = build_table(ind)

    print('\n=== 3. 概念板块 ===')
    con_names = ak.stock_board_concept_name_em()['板块名称'].tolist()
    print(f'  {len(con_names)} 个概念板块')
    con = fetch_all('con', con_names)
    con_tab = build_table(con)

    out = {
        'sse': sse_out.to_dict('records'),
        'phases': PHASES,
        'industry': ind_tab.to_dict('records'),
        'concept': con_tab.to_dict('records'),
        'industry_hist': {k: v.to_dict('records') for k, v in ind.items()},
        'concept_hist': {k: v.to_dict('records') for k, v in con.items()},
    }
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, default=str)
    print(f'\n已存 {OUT_JSON}')

    # 快速预览
    for tab, label in ((ind_tab, '行业'), (con_tab, '概念')):
        t = tab.dropna(subset=['底部至今'])
        print(f'\n--- {label} 底部至今 TOP15 ---')
        print(t.nlargest(15, '底部至今').to_string(index=False))
        print(f'--- {label} 底部至今 BOTTOM8 ---')
        print(t.nsmallest(8, '底部至今').to_string(index=False))


if __name__ == '__main__':
    main()
