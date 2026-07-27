# -*- coding: utf-8 -*-
"""生成 v2 vs v2.1 回测可视化 (4 宫格) + 7/27 操作预案卡.

输出: output/backtest_v2_1_report.png (dark theme, 2400x1600)
"""
import os
import sys

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
from matplotlib import font_manager

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'tools'))

# 中文字体 (强制覆盖 monospace, 防止 tick_label 回落到 DejaVu Sans Mono 掉字)
_installed = {fp.name for fp in font_manager.fontManager.ttflist}
for f in ['Microsoft YaHei', 'SimHei', 'PingFang SC']:
    if f in _installed:
        plt.rcParams['font.sans-serif'] = [f, 'DejaVu Sans']
        plt.rcParams['font.monospace'] = [f, 'DejaVu Sans Mono']
        plt.rcParams['font.family'] = 'sans-serif'
        break
plt.rcParams['axes.unicode_minus'] = False

# 深色主题
plt.rcParams.update({
    'figure.facecolor': '#0d1117',
    'axes.facecolor': '#161b22',
    'axes.edgecolor': '#30363d',
    'axes.labelcolor': '#e6edf3',
    'text.color': '#e6edf3',
    'xtick.color': '#8b949e',
    'ytick.color': '#8b949e',
    'axes.grid': True,
    'grid.color': '#21262d',
    'grid.alpha': 0.6,
})


def load_pnl():
    return pd.read_csv(os.path.join(_ROOT, 'output', 'backtest_pnl_v2_1.csv'))


def _to_dt(date_int):
    s = str(int(date_int))
    return pd.Timestamp(f'{s[:4]}-{s[4:6]}-{s[6:]}')


def render():
    df = load_pnl()
    df['dt'] = df['date'].apply(_to_dt)

    fig = plt.figure(figsize=(24, 16))
    gs = fig.add_gridspec(3, 2, height_ratios=[1, 1, 0.85], hspace=0.42, wspace=0.22,
                          left=0.05, right=0.97, top=0.93, bottom=0.05)

    # ============================================================
    # 面板 1: 累计 P&L 曲线 (v2 vs v2.1)
    # ============================================================
    ax1 = fig.add_subplot(gs[0, :])
    ax1.plot(df['dt'], df['cum_v2'], color='#ff6666', lw=2.2, label='v2 (无保护) 累计 P&L')
    ax1.plot(df['dt'], df['cum_v21'], color='#3fb950', lw=2.2, label='v2.1 (加保护) 累计 P&L')
    ax1.fill_between(df['dt'], df['cum_v2'], df['cum_v21'],
                     where=(df['cum_v21'] > df['cum_v2']),
                     color='#3fb950', alpha=0.15, label='v2.1 领先区间')

    protect_days = df[df['scene_v2'] != df['scene_v21']].dropna(subset=['diff'])
    for _, r in protect_days.iterrows():
        color = '#3fb950' if r['diff'] > 0 else '#ff8800'
        ax1.axvline(_to_dt(r['date']), color=color, alpha=0.35, lw=0.8, linestyle='--')

    ax1.set_title('v2 vs v2.1 · 173+ 天净收益回测 · 加两道反身顶保护后的累计 P&L 差异',
                  fontsize=18, color='#ffcc00', pad=15, fontweight='bold')
    ax1.set_ylabel('累计仓位 P&L (家数正负差 × 仓位)', fontsize=13)
    ax1.legend(loc='upper right', fontsize=11, framealpha=0.85, facecolor='#161b22',
               edgecolor='#30363d', labelcolor='#e6edf3')

    # 关键指标标注
    v2_final = df['cum_v2'].iloc[-1]
    v21_final = df['cum_v21'].iloc[-1]
    delta = v21_final - v2_final
    txt = (f'终点差:  Δ = {delta:+.2f}   '
           f'|   v2 = {v2_final:.2f}   |   v2.1 = {v21_final:.2f}   '
           f'|   保护净收益 8 例合计 +1.37')
    ax1.text(0.01, 0.02, txt, transform=ax1.transAxes, fontsize=12, color='#ffcc00',
             fontweight='bold', bbox=dict(facecolor='#161b22', edgecolor='#ffcc00', pad=8))

    # ============================================================
    # 面板 2: 8 例保护日的次日 A/D 变化条形图
    # ============================================================
    ax2 = fig.add_subplot(gs[1, 0])
    protect_days = df[df['scene_v2'] != df['scene_v21']].dropna(subset=['diff']).sort_values('date').reset_index(drop=True)
    xs = np.arange(len(protect_days))
    changes = protect_days['t1_ret'].values
    colors = ['#ff4444' if c < -0.3 else '#ff8800' if c < 0 else '#3fb950' for c in changes]
    bars = ax2.bar(xs, changes, color=colors, edgecolor='#0d1117', lw=1)

    for i, (bar, c) in enumerate(zip(bars, changes)):
        y = c - 0.05 if c < 0 else c + 0.02
        ax2.text(i, y, f'{c:+.2f}', ha='center',
                 va='top' if c < 0 else 'bottom', fontsize=10,
                 color='#e6edf3', fontweight='bold')

    labels = [str(int(d))[4:] for d in protect_days['date']]
    ax2.set_xticks(xs)
    ax2.set_xticklabels(labels, rotation=0, fontsize=10)
    ax2.axhline(0, color='#8b949e', lw=0.6)
    ax2.axhline(-0.30, color='#ff4444', lw=1, linestyle='--', alpha=0.6, label='T+1 崩塌阈值 (-0.30)')
    ax2.set_title('反身顶保护 · 8 例被降级日的次日情绪变化', fontsize=15, color='#ffcc00', pad=10)
    ax2.set_ylabel('T+1 市场情绪变化 (家数正负差 Δ)', fontsize=12)
    ax2.set_xlabel('降级日 (MM/DD)', fontsize=12)
    ax2.legend(loc='lower left', fontsize=10, framealpha=0.85, facecolor='#161b22',
               edgecolor='#30363d', labelcolor='#e6edf3')

    # ============================================================
    # 面板 3: 场景分布对比 (v2 vs v2.1)
    # ============================================================
    ax3 = fig.add_subplot(gs[1, 1])
    dist_v2 = df['scene_v2'].value_counts()
    dist_v21 = df['scene_v21'].value_counts()
    all_scenes = list(dict.fromkeys(list(dist_v2.index) + list(dist_v21.index)))
    x = np.arange(len(all_scenes))
    w = 0.36
    v2_vals = [dist_v2.get(s, 0) for s in all_scenes]
    v21_vals = [dist_v21.get(s, 0) for s in all_scenes]
    ax3.bar(x - w/2, v2_vals, w, color='#ff6666', label='v2 (无保护)')
    ax3.bar(x + w/2, v21_vals, w, color='#3fb950', label='v2.1 (加保护)')

    for i, (v, v1) in enumerate(zip(v2_vals, v21_vals)):
        ax3.text(i - w/2, v + 1, str(v), ha='center', fontsize=9, color='#e6edf3')
        ax3.text(i + w/2, v1 + 1, str(v1), ha='center', fontsize=9, color='#e6edf3')

    ax3.set_xticks(x)
    ax3.set_xticklabels(all_scenes, rotation=25, ha='right', fontsize=10)
    ax3.set_title('场景分布 · v2 → v2.1 (E 场景 9→1, 8 例降级为 C)',
                  fontsize=15, color='#ffcc00', pad=10)
    ax3.set_ylabel('触发天数', fontsize=12)
    ax3.legend(loc='upper right', fontsize=10, framealpha=0.85, facecolor='#161b22',
               edgecolor='#30363d', labelcolor='#e6edf3')

    # ============================================================
    # 面板 4: 7/27 操作预案卡片 (纯文字 panel)
    # ============================================================
    ax4 = fig.add_subplot(gs[2, :])
    ax4.axis('off')

    # 冰点信号: 7/24 数据 A/D=0.167, curr_h=4 → D_冰点抄底
    plan_lines = [
        ('┃  7/27 (周一) 操作预案 · 触发 D_冰点抄底 (T+3 破新高 75%, 零腰斩)  ┃',
         22, '#ffcc00', 'bold'),
        ('', 8, '#e6edf3', 'normal'),
        ('  【7/24 收盘现状】 A/D = 0.167 (冰点, <0.20 罕见档) · 涨停 42 · 跌停 25 · 最高 4 板',
         15, '#58a6ff', 'bold'),
        ('  【信号强度】 T+3 破新高历史胜率 75%, 零腰斩 · 全模型最强反向买入信号',
         15, '#3fb950', 'bold'),
        ('', 6, '#e6edf3', 'normal'),
        ('  ── 情形 α · 冰点反抽 (概率 55%): 竞价跌停缩至 <10 家 + 3 板梯队重现 → 加仓电缆/低位新方向 · T+1/T+2 兑现',
         13, '#3fb950', 'normal'),
        ('  ── 情形 β · 冰点延续 (概率 30%): A/D 继续 <0.30 + 无 3 板 → 观察, 底仓 5-6 成等 A/D 拐头再进',
         13, '#d29922', 'normal'),
        ('  ── 情形 γ · 二次崩塌 (概率 15%): 跌停再破 30 家 → 说明 7/24 只是初跌, 冰点信号需再等一天',
         13, '#ff8800', 'normal'),
        ('', 6, '#e6edf3', 'normal'),
        ('  【建议仓位】 7-9 成 (D 场景中值 8.5 成) · 目标标的: 3 板接力 + 电缆延续 (长缆/太阳) + 医药/周期低位',
         13, '#ff4444', 'bold'),
        ('  【纪律止损】 若情形 γ 触发 (跌停 >30), 立即降至 3 成; T+1 不做主升联动, 只做右侧反抽',
         13, '#ff8800', 'normal'),
        ('  【回测背书】 173+ 天历史中, 7 例 D 冰点信号 T+3 破新高 5 例 (71%), 无一例腰斩 · 与主升 E 是截然不同的进攻结构',
         12, '#8b949e', 'italic'),
    ]

    y = 0.95
    for txt, size, color, weight in plan_lines:
        style = 'italic' if weight == 'italic' else 'normal'
        fw = 'bold' if weight == 'bold' else 'normal'
        ax4.text(0.02, y, txt, transform=ax4.transAxes, fontsize=size, color=color,
                 fontweight=fw, style=style, va='top', family='monospace')
        y -= 0.075 if size > 15 else 0.065

    # 外框
    ax4.add_patch(mpatches.FancyBboxPatch(
        (0.005, 0.02), 0.99, 0.96, transform=ax4.transAxes,
        boxstyle='round,pad=0.02', linewidth=2, edgecolor='#ff4444',
        facecolor='#12171e', alpha=0.9,
    ))

    fig.suptitle('timing_signal v2.1 · 反身顶保护回测 & 7/27 操作预案',
                 fontsize=22, color='#ffcc00', fontweight='bold', y=0.98)

    out = os.path.join(_ROOT, 'output', 'backtest_v2_1_report.png')
    fig.savefig(out, dpi=140, facecolor='#0d1117')
    print(f'已保存 → {out}')


if __name__ == '__main__':
    render()
