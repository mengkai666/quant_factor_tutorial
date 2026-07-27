# -*- coding: utf-8 -*-
"""净收益回测: v2 vs v2.1 · 按仓位模拟 P&L.

代理收益 (无个股回测能力时的最佳近似):
  · 每日"市场收益率 proxy" = (up - down) / (up + down + flat)  ← 家数正负差归一
  · 每日选定仓位 (根据当日场景, 尾盘信号次日执行)
  · 次日按 (position × market_return_t+1) 计仓位 P&L

仓位映射 (取每档中值):
  D_冰点抄底 : 8.5
  E_主升加速 : 6.5 (v2.1: 6-7) / 8.0 (v2 原版: 7-9)
  A+_突破共振: 7.0
  C_高位分歧 : 4.0
  A_突破陷阱 : 3.5
  B_退潮蓄势 : 4.5
  中性震荡   : 5.0
  F_顶部崩塌 : 1.5

输出:
  · v2 累计 P&L, v2.1 累计 P&L, 差值曲线
  · 关键指标: 总收益 / 最大回撤 / 夏普 / 胜率
"""
import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'src'))
sys.path.insert(0, os.path.join(_ROOT, 'tools'))

from backtest_timing_v2_1 import (  # noqa: E402
    classify_v2_1, classify_v2_original, load_daily_facts,
)

# 仓位表 (两版差异只在 E 上: v2 激进 8 成, v2.1 稳健 6.5 成)
POS_V2 = {
    'D_冰点抄底': 8.5, 'E_主升加速': 8.0, 'A+_突破共振': 7.0,
    'C_高位分歧': 4.0, 'A_突破陷阱': 3.5, 'B_退潮蓄势': 4.5,
    '中性震荡': 5.0, 'F_顶部崩塌': 1.5,
}
POS_V21 = dict(POS_V2)
POS_V21['E_主升加速'] = 6.5  # v2.1 从 7-9 收窄到 6-7, 取中值
# C_高位分歧 v2.1 从 3-4 拓到 3-5, 中值 4.0 保持 (承接被降级 E 的日子)


def _pressure_5d(facts, i, prev_h):
    if i == 0:
        return prev_h
    lo = max(0, i - 5)
    vals = facts.iloc[lo:i]['max_h'].tolist()
    return max(vals) if vals else prev_h


def run_pnl():
    facts = load_daily_facts()
    # 市场收益 proxy = (up - down) / (up + down) - 0.5 归零 (中性 = 0)
    facts['mkt_ret'] = (facts['up'] - facts['down']) / (facts['up'] + facts['down']) - 0.5

    rows = []
    for i, r in facts.iterrows():
        curr_h = int(r['max_h'])
        prev_h = int(facts.iloc[i - 1]['max_h']) if i > 0 else 0
        ad = float(r['ad']) if pd.notnull(r['ad']) else None
        ladder = int(r['ladder'])
        zt = int(r['zt_count'])
        dt = int(r['dt_count'])
        p5 = _pressure_5d(facts, i, prev_h)

        scene_v2 = classify_v2_original(curr_h, prev_h, ad, ladder, zt, dt, p5)
        scene_v21 = classify_v2_1(curr_h, prev_h, ad, ladder, zt, dt, p5,
                                  int(r['h3']), int(r['h4']),
                                  int(r['h5']), int(r['h6p']))
        pos_v2 = POS_V2.get(scene_v2, 5.0)
        pos_v21 = POS_V21.get(scene_v21, 5.0)

        # 次日市场收益 (今日尾盘看信号 → 明日执行)
        if i + 1 < len(facts):
            t1_ret = float(facts.iloc[i + 1]['mkt_ret'])
        else:
            t1_ret = 0.0

        rows.append({
            'date': int(r['date']), 'ad': ad, 'max_h': curr_h,
            'scene_v2': scene_v2, 'scene_v21': scene_v21,
            'pos_v2': pos_v2, 'pos_v21': pos_v21,
            't1_ret': t1_ret,
            'pnl_v2': (pos_v2 / 10.0) * t1_ret,
            'pnl_v21': (pos_v21 / 10.0) * t1_ret,
        })

    df = pd.DataFrame(rows)
    df['cum_v2'] = df['pnl_v2'].cumsum()
    df['cum_v21'] = df['pnl_v21'].cumsum()
    df['diff'] = df['cum_v21'] - df['cum_v2']
    return df


def metrics(pnl):
    """(总收益, 最大回撤, 夏普, 胜率, 平均单日)"""
    cum = pnl.cumsum()
    dd = (cum - cum.cummax()).min()
    sharpe = pnl.mean() / pnl.std() * (252 ** 0.5) if pnl.std() > 0 else 0.0
    win = (pnl > 0).mean()
    return cum.iloc[-1], dd, sharpe, win, pnl.mean()


if __name__ == '__main__':
    df = run_pnl()

    print('\n=== 净收益回测 · 203 交易日 ===\n')

    for label, col in [('v2 (无保护)', 'pnl_v2'), ('v2.1 (加保护)', 'pnl_v21')]:
        tot, dd, sh, win, avg = metrics(df[col])
        print(f'  [{label}]')
        print(f'    累计收益:   {tot:+.4f}')
        print(f'    最大回撤:   {dd:.4f}')
        print(f'    夏普比率:   {sh:.2f}')
        print(f'    单日胜率:   {win:.1%}')
        print(f'    平均单日:   {avg:+.5f}\n')

    diff_tot = df['diff'].iloc[-1]
    print(f'  Δ (v2.1 − v2) 累计: {diff_tot:+.4f}')
    print(f'  Δ 最大领先:         {df["diff"].max():+.4f}')
    print(f'  Δ 最大落后:         {df["diff"].min():+.4f}\n')

    # 8 例保护日的贡献
    protected = df[(df['scene_v2'] == 'E_主升加速') & (df['scene_v21'] == 'C_高位分歧')].copy()
    protected['saved'] = protected['pnl_v21'] - protected['pnl_v2']
    print(f'=== 保护日贡献 · {len(protected)} 例 ===')
    print(protected[['date', 'ad', 'max_h', 't1_ret', 'pnl_v2', 'pnl_v21', 'saved']].to_string(index=False))
    print(f'\n  合计"保护"净收益: {protected["saved"].sum():+.4f}')
    print(f'  保护为负的天数:   {(protected["saved"] < 0).sum()}/{len(protected)}')

    out_path = os.path.join(_ROOT, 'output', 'backtest_pnl_v2_1.csv')
    df.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f'\n  明细已导出: {out_path}')
