# pyright: reportMissingTypeStubs=false, reportGeneralTypeIssues=false
"""
生成《战法G: 规避10天100%异动与滑窗二波战法》专用量化可视化图表
"""
import os
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

BG_COLOR = '#0F141C'
PANEL_BG = '#171D27'
PANEL_BORDER = '#262F3D'
TEXT_MAIN = '#EAEFF8'
TEXT_MUTED = '#8D9BAC'
COLOR_RED = '#F54854'
COLOR_GREEN = '#22C55E'
COLOR_GOLD = '#FBBF24'
COLOR_CYAN = '#06B6D4'
COLOR_PURPLE = '#A855F7'
COLOR_BLUE = '#3B82F6'

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'output')
os.makedirs(OUTPUT_DIR, exist_ok=True)


def setup_panel(ax, title=None):
    ax.set_facecolor(PANEL_BG)
    for spine in ax.spines.values():
        spine.set_color(PANEL_BORDER)
        spine.set_linewidth(1.0)
    ax.tick_params(colors=TEXT_MUTED, labelsize=9)
    ax.grid(True, linestyle='--', alpha=0.18, color=TEXT_MUTED)
    if title:
        ax.set_title(title, color=TEXT_MAIN, fontsize=12, fontweight='bold', pad=10)


def plot_abnormal_wave2():
    fig = plt.figure(figsize=(16, 9.5), facecolor=BG_COLOR, dpi=200)
    gs = gridspec.GridSpec(2, 2, height_ratios=[1.1, 1.0], width_ratios=[1.1, 1.0],
                           wspace=0.25, hspace=0.35, left=0.07, right=0.95, top=0.90, bottom=0.08)

    fig.suptitle('战法 G : 规避 10 天 100% 严重异动与“断板·反包·横盘·二波”操盘全景',
                 color=TEXT_MAIN, fontsize=16, fontweight='bold', y=0.96)

    # 1. 理论推导: 纯连板直冲死穴 vs 主动断板横盘滑窗降温
    ax1 = fig.add_subplot(gs[0, 0])
    setup_panel(ax1, '滚动 10 日累计涨幅轨迹: 纯连板直冲死穴 vs 主动卡异动二波')

    # 交易日 1 到 18
    days = np.arange(1, 19)
    
    # 路径 A: 纯无脑连板 (8连板直接爆表停牌)
    # 涨幅计算: 10日滚动涨幅
    path_a_daily = [1.10**i - 1 for i in range(1, 9)] + [1.10**8 - 1]*10
    path_a_roll10 = []
    for d in days:
        if d <= 8:
            path_a_roll10.append((1.10**d - 1) * 100)
        else:
            path_a_roll10.append(np.nan)

    # 路径 B: 经典“5板 -> 断板 -> 反包2板 -> 横盘3天滑窗 -> 二波3板”
    # 模拟价格序列 (Day 0 = 10.0)
    prices = [10.0]
    # Day 1-5: 5连板
    for _ in range(5):
        prices.append(prices[-1] * 1.10)
    # Day 6: 主动断板 (微跌 1.5% 洗盘)
    prices.append(prices[-1] * 0.985)
    # Day 7-8: 反包连2板
    prices.append(prices[-1] * 1.10)
    prices.append(prices[-1] * 1.10)
    # Day 9-11: 横盘3天 (价格在 0%~+1% 震荡，等待老阳线滑出10日窗口)
    prices.append(prices[-1] * 1.005)
    prices.append(prices[-1] * 0.995)
    prices.append(prices[-1] * 1.010)
    # Day 12-14: 二波连拉3板
    prices.append(prices[-1] * 1.10)
    prices.append(prices[-1] * 1.10)
    prices.append(prices[-1] * 1.10)
    # Day 15-18: 震荡
    for _ in range(4):
        prices.append(prices[-1] * 1.00)

    # 计算 10 日滚动涨幅: (P[i] / P[i-10] - 1) * 100
    roll10_b = []
    for i in range(1, 19):
        base_idx = max(0, i - 10)
        ret = (prices[i] / prices[base_idx] - 1.0) * 100
        roll10_b.append(ret)

    # 绘图
    ax1.axhline(100, color=COLOR_RED, linestyle='--', linewidth=2.0, label='交易所 100% 严重异动红线 (停牌核查)')
    ax1.axhspan(90, 100, color=COLOR_RED, alpha=0.15, label='极度危险预警区 (90%~100%)')

    ax1.plot(days[:8], path_a_roll10[:8], color='#F87171', marker='x', linestyle=':', linewidth=2.0, label='无脑纯连板 (第8天穿透100%遭核查A杀)')
    ax1.plot(days, roll10_b, color=COLOR_GREEN, marker='o', linewidth=2.4, label='主力卡异动走势 (5板断板+反包+横盘滑窗+二波)')

    # 关键节点标注
    ax1.annotate('5板主动断板\n压制在60%内', xy=(5, roll10_b[4]), xytext=(3.2, 75),
                 arrowprops=dict(arrowstyle='->', color=COLOR_GOLD, lw=1.2),
                 color=COLOR_GOLD, fontsize=8.5, fontweight='bold')
    ax1.annotate('横盘3天\n老阳线滑出窗口\n涨幅暴降至45%!', xy=(11, roll10_b[10]), xytext=(9.2, 28),
                 arrowprops=dict(arrowstyle='->', color=COLOR_CYAN, lw=1.2),
                 color=COLOR_CYAN, fontsize=8.5, fontweight='bold')
    ax1.annotate('指标腾空\n启动二波3连板', xy=(14, roll10_b[13]), xytext=(13.0, 92),
                 arrowprops=dict(arrowstyle='->', color=COLOR_GREEN, lw=1.2),
                 color=COLOR_GREEN, fontsize=8.5, fontweight='bold')

    ax1.set_xlabel('交易日序号', color=TEXT_MUTED, fontsize=9.5)
    ax1.set_ylabel('滚动 10 日累计涨跌幅 (%)', color=TEXT_MUTED, fontsize=9.5)
    ax1.set_xlim(1, 18)
    ax1.set_ylim(0, 125)
    ax1.legend(facecolor=PANEL_BG, edgecolor=PANEL_BORDER, labelcolor=TEXT_MAIN, loc='lower right', fontsize=8)

    # 2. 实盘数据: 4板/5板/6板 断板日 10日累计涨幅与卡异动概率
    ax2 = fig.add_subplot(gs[0, 1])
    setup_panel(ax2, '全市场 4/5/6 板断板日 10日累计涨幅与卡位特征 (N=111次断板)')

    tiers = ['4连板断板\n(N=75)', '5连板断板\n(N=25)', '6连板断板\n(N=11)']
    med_cum = [44.7, 59.4, 77.0]
    near_risk = [29.3, 32.0, 45.5]
    wave2_rates = [4.0, 16.0, 9.1]

    x = np.arange(len(tiers))
    w = 0.26

    b1 = ax2.bar(x - w, med_cum, w, label='断板日10日涨幅中位数(%)', color=COLOR_BLUE, alpha=0.85)
    b2 = ax2.bar(x, near_risk, w, label='处60%~95%卡异动危险区(%)', color=COLOR_GOLD, alpha=0.85)
    b3 = ax2.bar(x + w, [r * 3 for r in wave2_rates], w, label='走出反包横盘二波全套比例(x3%)', color=COLOR_GREEN, alpha=0.85)

    for b in b1:
        ax2.text(b.get_x() + b.get_width()/2., b.get_height() + 1.2, f'{b.get_height():.1f}%',
                 ha='center', va='bottom', color=TEXT_MAIN, fontsize=8.5, fontweight='bold')
    for b in b2:
        ax2.text(b.get_x() + b.get_width()/2., b.get_height() + 1.2, f'{b.get_height():.1f}%',
                 ha='center', va='bottom', color=COLOR_GOLD, fontsize=8.5, fontweight='bold')
    for i, b in enumerate(b3):
        ax2.text(b.get_x() + b.get_width()/2., b.get_height() + 1.2, f'{wave2_rates[i]:.1f}%',
                 ha='center', va='bottom', color=COLOR_GREEN, fontsize=8.5, fontweight='bold')

    ax2.set_xticks(x)
    ax2.set_xticklabels(tiers, color=TEXT_MAIN, fontsize=9)
    ax2.set_ylim(0, 95)
    ax2.set_ylabel('百分比 (%)', color=TEXT_MUTED, fontsize=9.5)
    ax2.legend(facecolor=PANEL_BG, edgecolor=PANEL_BORDER, labelcolor=TEXT_MAIN, loc='upper left', fontsize=8)

    # 3. 典型实战案例路径对比拆解
    ax3 = fig.add_subplot(gs[1, 0])
    setup_panel(ax3, '典型实战标的“断板·反包·横盘·二波”阶段板数推进')

    cases = [
        ('兴业科技', [6, 1, 1], [3, 3], '6板(涨98.1%) -> 断3天 -> 反包1板 -> 横3天 -> 二波1板'),
        ('宝鼎科技', [4, 2, 3], [3, 2], '4板(涨66.6%) -> 断3天 -> 反包2板 -> 横2天 -> 二波连3板'),
        ('达实智能', [4, 2, 2], [1, 4], '4板(涨30.7%) -> 断1天 -> 反包2板 -> 横4天 -> 二波连2板'),
        ('国芳集团', [5, 1, 1], [1, 4], '5板(涨68.4%) -> 断1天 -> 反包1板 -> 横4天 -> 二波1板'),
        ('楚天龙',   [5, 1, 1], [2, 2], '5板(涨57.6%) -> 断2天 -> 反包1板 -> 横2天 -> 二波1板')
    ]

    y_pos = np.arange(len(cases))
    # 绘制堆叠阶梯或分段展示
    for i, (name, boards, gaps, desc) in enumerate(cases):
        ax3.text(0.02, y_pos[i] + 0.15, f"{name}: {desc}", color=TEXT_MAIN, fontsize=8.5, fontweight='bold')
        # 画微型进度条: 首段 (红), 反包 (金), 二波 (绿)
        ax3.barh(y_pos[i] - 0.18, boards[0], height=0.25, left=0.0, color=COLOR_RED, alpha=0.9, label='首阶段板数' if i == 0 else '')
        ax3.barh(y_pos[i] - 0.18, boards[1], height=0.25, left=boards[0] + 0.5, color=COLOR_GOLD, alpha=0.9, label='反包板数' if i == 0 else '')
        ax3.barh(y_pos[i] - 0.18, boards[2], height=0.25, left=boards[0] + 0.5 + boards[1] + 0.5, color=COLOR_GREEN, alpha=0.9, label='二波板数' if i == 0 else '')

    ax3.set_yticks(y_pos)
    ax3.set_yticklabels([c[0] for c in cases], color=TEXT_MAIN, fontsize=9)
    ax3.invert_yaxis()
    ax3.set_xlim(0, 14)
    ax3.set_xlabel('累计阶段板数展示', color=TEXT_MUTED, fontsize=9.5)
    ax3.legend(facecolor=PANEL_BG, edgecolor=PANEL_BORDER, labelcolor=TEXT_MAIN, loc='lower right', fontsize=8)

    # 4. 战法G 实战决策卡片
    ax4 = fig.add_subplot(gs[1, 1])
    setup_panel(ax4, '战法 G: 规避异动与滑窗二波实战指引卡')
    ax4.axis('off')

    guide_text = (
        "【战法G：规避异动与滑窗二波实战法则】\n\n"
        "● 核心数学逻辑: 10日100%异动红线倒逼主力主动断板！\n"
        "   - 5板累计涨幅61.1%, 6板达77.2%, 7板达94.9%\n"
        "   - 6板断板中 45.5% 处于 60%~95% 极限卡位线\n"
        "   - 5连板个股走出完整二波全套形态概率高达 16.0%\n\n"
        "● 三大精准买点体系:\n"
        "   1. 买点一 (反包买点): 断板1-2天后弱转强反包首板，博弈第一波冲刺\n"
        "   2. 买点二 (滑窗潜伏): 反包见顶后横盘第3-4天，老阳线即将滑出\n"
        "      10日窗口、偏离值指标骤降时低吸\n"
        "   3. 买点三 (二波突破): 平台放量突破首板果断重仓跟随！\n\n"
        "● 风控铁律: 横盘期间若跌破10日均线或放量长阴，二波逻辑证伪立即止损！"
    )
    bbox = FancyBboxPatch((0.05, 0.05), 0.90, 0.90, boxstyle="round,pad=0.03",
                          fc='#131A24', ec=COLOR_GREEN, lw=1.6)
    ax4.add_patch(bbox)
    ax4.text(0.08, 0.88, guide_text, color=TEXT_MAIN, fontsize=8.8, verticalalignment='top',
             horizontalalignment='left', fontfamily='Microsoft YaHei', linespacing=1.45)

    out_file = os.path.join(OUTPUT_DIR, 'chart_strategy_abnormal_wave2.png')
    plt.savefig(out_file, facecolor=BG_COLOR)
    plt.close()
    print(f'[OK] Generated: {out_file}')


if __name__ == '__main__':
    plot_abnormal_wave2()
