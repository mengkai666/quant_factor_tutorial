"""同花顺 90 个行业板块指数 × 上证阶段 收益矩阵。

东财板块接口本机不通 (ProxyError/RemoteDisconnected 交替), 改用同花顺板块指数
(akshare stock_board_industry_index_ths), 它给的是真板块指数日线, 与散户看盘一致。

输出 output/ths_sector_phase.csv + 控制台阶段排名。
"""
import os
import sys
import time
import json

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import paths  # noqa: E402

import akshare as ak  # noqa: E402

PHASES = {
    '下跌段': ('2026-06-22', '2026-07-17'),
    '见底段': ('2026-07-17', '2026-07-21'),
    '震荡段': ('2026-07-21', '2026-08-04'),
    '突破日': ('2026-08-04', '2026-08-05'),
    '底部至今': ('2026-07-17', '2026-08-05'),
}
CACHE = os.path.join(paths.DATA_DIR, 'ths_sector_hist.json')


def fetch_all(names, start='20260610', end='20260806'):
    """串行拉 (THS 接口并发易被限), 带本地缓存避免重复拉。"""
    cache = {}
    if os.path.exists(CACHE):
        try:
            with open(CACHE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    todo = [n for n in names if n not in cache]
    print(f'  缓存命中 {len(names) - len(todo)}, 待拉 {len(todo)}')
    for i, n in enumerate(todo, 1):
        for attempt in range(3):
            try:
                d = ak.stock_board_industry_index_ths(
                    symbol=n, start_date=start, end_date=end)
                if d is not None and len(d):
                    d = d.rename(columns={'日期': 'date', '收盘价': 'close',
                                          '成交额': 'amount'})
                    d['date'] = pd.to_datetime(d['date']).dt.strftime('%Y-%m-%d')
                    cache[n] = d[['date', 'close', 'amount']].to_dict('records')
                break
            except Exception:
                time.sleep(1.0 * (attempt + 1))
        if i % 10 == 0:
            print(f'    {i}/{len(todo)} ...', flush=True)
            with open(CACHE, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False)
    with open(CACHE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)
    return cache


def phase_ret(recs, s, e):
    ds = [r for r in recs if r['date'] <= s]
    de = [r for r in recs if r['date'] <= e]
    if not ds or not de:
        return None
    c0, c1 = ds[-1]['close'], de[-1]['close']
    return round((c1 / c0 - 1) * 100, 2) if c0 else None


def main():
    pd.set_option('display.width', 250)
    print('=== 拉取同花顺板块列表 ===')
    summ = ak.stock_board_industry_summary_ths()
    names = summ['板块'].tolist()
    print(f'  {len(names)} 个板块')

    print('=== 拉取板块指数日线 ===')
    hist = fetch_all(names)
    print(f'  成功 {len(hist)}/{len(names)}')

    rows = []
    for n, recs in hist.items():
        r = {'板块': n}
        for ph, (s, e) in PHASES.items():
            r[ph] = phase_ret(recs, s, e)
        # 量能: 震荡+突破段日均成交额 / 下跌段日均
        try:
            fall = [x['amount'] for x in recs if '2026-06-23' <= x['date'] <= '2026-07-17']
            osc = [x['amount'] for x in recs if '2026-07-21' <= x['date'] <= '2026-08-05']
            r['量比'] = round((sum(osc) / len(osc)) / (sum(fall) / len(fall)), 2) \
                if fall and osc else None
        except Exception:
            r['量比'] = None
        rows.append(r)

    t = pd.DataFrame(rows).dropna(subset=['底部至今'])
    out = os.path.join(paths.OUTPUT_DIR, 'ths_sector_phase.csv')
    t.to_csv(out, index=False, encoding='utf-8-sig')

    cols = ['板块'] + list(PHASES.keys()) + ['量比']
    print('\n=== 底部至今 TOP 20 ===')
    print(t.nlargest(20, '底部至今')[cols].to_string(index=False))
    print('\n=== 底部至今 BOTTOM 12 ===')
    print(t.nsmallest(12, '底部至今')[cols].to_string(index=False))
    print('\n=== 震荡段 TOP 15 (箱体里谁在走) ===')
    print(t.nlargest(15, '震荡段')[cols].to_string(index=False))
    print('\n=== 突破日 8/05 TOP 15 (谁点火) ===')
    print(t.nlargest(15, '突破日')[cols].to_string(index=False))
    print('\n=== 下跌段 跌最狠 TOP 12 ===')
    print(t.nsmallest(12, '下跌段')[cols].to_string(index=False))

    c = t[['下跌段', '底部至今']].dropna()
    print(f'\n下跌段 vs 底部至今 相关系数: {c["下跌段"].corr(c["底部至今"]):.3f}')
    c2 = t[['下跌段', '震荡段']].dropna()
    print(f'下跌段 vs 震荡段   相关系数: {c2["下跌段"].corr(c2["震荡段"]):.3f}')
    print(f'\n已存 {out}')


if __name__ == '__main__':
    main()
