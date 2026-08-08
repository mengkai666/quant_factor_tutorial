"""本地全市场板块阶段收益分析 (不依赖网络)。

数据源:
  data/price_history_cache.csv   全市场 5192 只日收盘 (真源)
  data/industry_cache.csv        证监会行业 (全市场无偏覆盖)
  data/em_stock_plate_cache.csv  东财概念归因 (仅强势股, 有选择偏差, 只用来给概念打标)
  data/涨停历史缓存.csv           涨停/跌停 (情绪与资金强度)

阶段: 见 PHASES。等权中位数收益 (中位数抗个股极值), 同时给出均值与家数。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import paths  # noqa: E402

PHASES = {
    '下跌段': ('2026-06-22', '2026-07-17'),
    '见底段': ('2026-07-17', '2026-07-21'),
    '震荡段': ('2026-07-21', '2026-08-04'),
    '突破日': ('2026-08-04', '2026-08-05'),
    '底部至今': ('2026-07-17', '2026-08-05'),
}


def load_price():
    df = pd.read_csv(paths.PRICE_CACHE)
    df = df[df.close > 0]
    return df.pivot_table(index='date', columns='code', values='close', aggfunc='last')


def phase_returns(px):
    """返回 DataFrame: index=code, 各阶段收益%"""
    dates = list(px.index)

    def near(d, side):
        """side='le' 取 <=d 的最后一个交易日; 'ge' 取 >=d 的第一个"""
        if side == 'le':
            c = [x for x in dates if x <= d]
            return c[-1] if c else None
        c = [x for x in dates if x >= d]
        return c[0] if c else None

    out = {}
    for ph, (s, e) in PHASES.items():
        ds, de = near(s, 'le'), near(e, 'le')
        if not ds or not de:
            continue
        out[ph] = (px.loc[de] / px.loc[ds] - 1) * 100
    r = pd.DataFrame(out)
    # 只保留两端都有价的
    return r.dropna(how='all')


def attach_labels(ret):
    ic = pd.read_csv(paths.INDUSTRY_CACHE)[['code', 'name', 'industry']]
    em = pd.read_csv(paths.EM_PLATE_CACHE, encoding='utf-8-sig')
    em = em[em.date == em.date.max()][['code', 'sub', 'mainline']]
    df = ret.reset_index().rename(columns={'index': 'code', 'code': 'code'})
    df = df.merge(ic, on='code', how='left').merge(em, on='code', how='left')
    return df


def agg(df, col, min_n=5):
    g = df.groupby(col)
    rows = []
    for k, sub in g:
        if len(sub) < min_n:
            continue
        r = {col: k, 'n': len(sub)}
        for ph in PHASES:
            if ph in sub.columns:
                r[ph + '_中位'] = round(sub[ph].median(), 2)
                r[ph + '_均值'] = round(sub[ph].mean(), 2)
        rows.append(r)
    return pd.DataFrame(rows)


def main():
    pd.set_option('display.width', 250)
    print('=== 载入价格缓存 ===')
    px = load_price()
    print('日期', px.index.min(), '→', px.index.max(), '| 股票', px.shape[1])

    ret = phase_returns(px)
    df = attach_labels(ret)
    print('有效个股', len(df))

    # 全市场基准
    print('\n=== 全市场个股中位数收益 (基准) ===')
    for ph in PHASES:
        if ph in df.columns:
            v = df[ph].dropna()
            print(f'  {ph}: 中位 {v.median():.2f}%  均值 {v.mean():.2f}%  '
                  f'上涨占比 {(v > 0).mean() * 100:.0f}%  n={len(v)}')

    for col, label, min_n in (('industry', '证监会行业', 8),
                              ('sub', '东财概念(强势股样本)', 5),
                              ('mainline', '主线', 5)):
        t = agg(df, col, min_n)
        if not len(t):
            continue
        print(f'\n{"=" * 30}\n=== {label} 按[底部至今]排序 ===')
        key = '底部至今_中位'
        cols = [col, 'n'] + [f'{p}_中位' for p in PHASES if f'{p}_中位' in t.columns]
        print(t.nlargest(18, key)[cols].to_string(index=False))
        print(f'--- {label} 垫底 8 ---')
        print(t.nsmallest(8, key)[cols].to_string(index=False))

        # 下跌段 vs 反弹段 相关性: 判断超跌反弹 vs 新主线
        if '下跌段_中位' in t.columns and key in t.columns:
            c = t[['下跌段_中位', key]].dropna()
            if len(c) > 5:
                print(f'  ↳ 下跌段跌幅 vs 底部至今涨幅 相关系数: '
                      f'{c["下跌段_中位"].corr(c[key]):.3f} (负=超跌反弹, 正=强者延续)')

    out = os.path.join(paths.OUTPUT_DIR, 'local_sector_phase.csv')
    df.to_csv(out, index=False, encoding='utf-8-sig')
    print(f'\n个股明细已存 {out}')


if __name__ == '__main__':
    main()
