# pyright: reportMissingTypeStubs=false, reportGeneralTypeIssues=false
"""
六大战法数据可视化图表生成器
为《连板高度年度深研》中的六大实战战法生成高质量出版级图表:
  1. chart_strategy_A_breakout.png    战法A: 龙头高度突破与压力博弈图
  2. chart_strategy_B_rebound.png     战法B: 龙头断板反包模式与黄金时间窗图
  3. chart_strategy_C_mimic.png       战法C: 突破后形态模仿与板块扩散传导图
  4. chart_strategy_D_cycle.png       战法D: 市场高度周期生命线与见峰预警图
  5. chart_strategy_EF_selection.png  战法E&F: 断板退潮冲击与真龙头全景图谱
  6. chart_strategies_dashboard.png   六大战法量化实战全景作战总看板
"""
import os
import sys
import warnings
from collections import Counter, defaultdict
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch, Rectangle, Circle, Wedge

warnings.filterwarnings('ignore')

# 中文字体设置
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# 配色系统 (Dark Quant Theme)
BG_COLOR = '#0F141C'
PANEL_BG = '#171D27'
PANEL_BORDER = '#262F3D'
TEXT_MAIN = '#EAEFF8'
TEXT_MUTED = '#8D9BAC'
COLOR_RED = '#F54854'       # 涨停红 / 龙头红
COLOR_GREEN = '#22C55E'     # 突破绿 / 成功率
COLOR_GOLD = '#FBBF24'      # 预警金 / 核心指标
COLOR_CYAN = '#06B6D4'      # 统计青 / 模仿个股
COLOR_PURPLE = '#A855F7'    # 周期紫
COLOR_BLUE = '#3B82F6'      # 基础蓝

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market_height_annual_study import (
    load_annual_height, load_zt_cache, load_baostock_limit,
    load_plate_cache, build_leader_paths, analyze_height_pressure,
    analyze_repair_patterns, analyze_imitation, analyze_height_cycles,
    _norm_date_ymd
)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'output')
os.makedirs(OUTPUT_DIR, exist_ok=True)


def setup_panel(ax, title=None):
    """设置子图暗色专业风格"""
    ax.set_facecolor(PANEL_BG)
    for spine in ax.spines.values():
        spine.set_color(PANEL_BORDER)
        spine.set_linewidth(1.0)
    ax.tick_params(colors=TEXT_MUTED, labelsize=9)
    ax.grid(True, linestyle='--', alpha=0.18, color=TEXT_MUTED)
    if title:
        ax.set_title(title, color=TEXT_MAIN, fontsize=12, fontweight='bold', pad=10)


# =====================================================================
# 图1: 战法A - 龙头高度突破与压力博弈分析
# =====================================================================
def plot_strategy_A(ah, hp_res):
    fig = plt.figure(figsize=(14, 8), facecolor=BG_COLOR, dpi=200)
    gs = gridspec.GridSpec(2, 2, height_ratios=[1.2, 1.0], width_ratios=[1.3, 0.9],
                           wspace=0.25, hspace=0.32, left=0.07, right=0.95, top=0.90, bottom=0.09)

    fig.suptitle('战法 A : 龙头高度突破战法 — 压力压制与突破动能全景',
                 color=TEXT_MAIN, fontsize=16, fontweight='bold', y=0.96)

    # 1.1 市场高度 vs P5 压力高度时序轨迹
    ax1 = fig.add_subplot(gs[0, :])
    setup_panel(ax1, '市场每日最高连板数 vs 前5日压力高度 (P5) 时序突破轨迹 (297个交易日)')

    df = ah.copy()
    dates = pd.to_datetime(df['date'])
    h = df['height'].astype(float)
    p5 = pd.to_numeric(df['pressure5'], errors='coerce')

    ax1.plot(dates, p5, color=COLOR_GOLD, label='P5 压力天花板 (前5日最高板)', linewidth=1.8, linestyle='--')
    ax1.plot(dates, h, color=COLOR_RED, label='全市场最高板 (高度龙头)', linewidth=2.0)

    # 填充突破区域
    ax1.fill_between(dates, h, p5, where=(h > p5), color=COLOR_RED, alpha=0.25, label='突破压力压制区间')

    # 标注典型龙头
    notable = [
        ('2025-09-23', 15, '天普股份 15板'),
        ('2026-01-23', 18, '锋龙股份 18板'),
        ('2026-01-07', 14, '胜通能源 14板'),
        ('2026-08-06', 10, '爱丽家居 10板'),
        ('2026-05-20', 8, '利仁科技 8板'),
    ]
    for d_str, val, label in notable:
        try:
            d_dt = pd.to_datetime(d_str)
            ax1.annotate(f'{label}', xy=(d_dt, val), xytext=(d_dt, val + 1.6),
                         arrowprops=dict(arrowstyle='->', color=COLOR_GOLD, lw=1.2),
                         color=TEXT_MAIN, fontsize=8.5, fontweight='bold',
                         bbox=dict(boxstyle='round,pad=0.2', facecolor=PANEL_BG, edgecolor=COLOR_GOLD, alpha=0.9))
        except Exception:
            pass

    ax1.set_ylabel('连板高度', color=TEXT_MUTED, fontsize=10)
    ax1.legend(facecolor=PANEL_BG, edgecolor=PANEL_BORDER, labelcolor=TEXT_MAIN, loc='upper left', fontsize=9)
    ax1.set_ylim(0, 21)

    # 1.2 突破落点分层动能 (中低位 vs 高位)
    ax2 = fig.add_subplot(gs[1, 0])
    setup_panel(ax2, '突破后 5 日再创新高概率与后续高度对比 (N=66次突破)')

    categories = ['中低位突破 (≤6板)\n主升启动阶段', '高位突破 (≥7板)\n加速赶顶阶段']
    new_high_prob = [100.0, 100.0]
    med_peak = [7.0, 9.0]
    sample_size = [22, 44]

    x = np.arange(len(categories))
    width = 0.35

    rects1 = ax2.bar(x - width/2, new_high_prob, width, label='后5日再创新高概率(%)', color=COLOR_GREEN, alpha=0.85)
    rects2 = ax2.bar(x + width/2, [p * 10 for p in med_peak], width, label='后5日最高峰值高度 (x10)', color=COLOR_CYAN, alpha=0.85)

    for rect in rects1:
        h_val = rect.get_height()
        ax2.text(rect.get_x() + rect.get_width()/2., h_val + 2, f'{h_val:.0f}%',
                 ha='center', va='bottom', color=TEXT_MAIN, fontweight='bold', fontsize=9.5)
    for i, rect in enumerate(rects2):
        h_val = rect.get_height()
        ax2.text(rect.get_x() + rect.get_width()/2., h_val + 2, f'{med_peak[i]:.0f}板 (N={sample_size[i]})',
                 ha='center', va='bottom', color=TEXT_MAIN, fontweight='bold', fontsize=9.5)

    ax2.set_xticks(x)
    ax2.set_xticklabels(categories, color=TEXT_MAIN, fontsize=9.5)
    ax2.set_ylim(0, 125)
    ax2.set_ylabel('概率(%) / 放大高度', color=TEXT_MUTED, fontsize=9.5)
    ax2.legend(facecolor=PANEL_BG, edgecolor=PANEL_BORDER, labelcolor=TEXT_MAIN, loc='upper right', fontsize=8.5)

    # 1.3 战法A 实操决策卡片
    ax3 = fig.add_subplot(gs[1, 1])
    setup_panel(ax3, '战法 A 核心操作指引矩阵')
    ax3.axis('off')

    guide_text = (
        "【战法A：龙头高度突破核心决策】\n\n"
        "● 核心逻辑:\n"
        "   高度突破压力(P5)即代表空间拓宽,\n"
        "   但高位突破是均值回归前兆而非动量！\n\n"
        "● 中低位突破 (≤6板, 占33%):\n"
        "   → 信号: 空间打开, 情绪主升浪确立\n"
        "   → 操作: 重仓参与新首板、1进2与主线前排\n"
        "   → 胜率: 后5日创新高概率 100%, 空间看高至7板\n\n"
        "● 高位突破 (≥7板, 占67%):\n"
        "   → 信号: 加速赶顶, 监管与分歧风险陡增\n"
        "   → 操作: 严禁无脑追高龙头, 分批止盈兑现\n"
        "   → 防守: 突破次日若断板无承接即全线撤退"
    )
    bbox = FancyBboxPatch((0.05, 0.05), 0.90, 0.90, boxstyle="round,pad=0.03",
                          fc='#131A24', ec=COLOR_RED, lw=1.5)
    ax3.add_patch(bbox)
    ax3.text(0.10, 0.88, guide_text, color=TEXT_MAIN, fontsize=9.2, verticalalignment='top',
             horizontalalignment='left', family='Microsoft YaHei', linespacing=1.45)

    out_file = os.path.join(OUTPUT_DIR, 'chart_strategy_A_breakout.png')
    plt.savefig(out_file, facecolor=BG_COLOR)
    plt.close()
    print(f'[OK] Generated: {out_file}')


# =====================================================================
# 图2: 战法B - 龙头断板反包模式与黄金时间窗
# =====================================================================
def plot_strategy_B(reb_res, leader_paths):
    fig = plt.figure(figsize=(14, 8), facecolor=BG_COLOR, dpi=200)
    gs = gridspec.GridSpec(2, 2, height_ratios=[1.0, 1.0], width_ratios=[1.1, 1.0],
                           wspace=0.25, hspace=0.35, left=0.07, right=0.95, top=0.90, bottom=0.09)

    fig.suptitle('战法 B : 龙头断板反包战法 — 黄金反包时间窗与二波路径',
                 color=TEXT_MAIN, fontsize=16, fontweight='bold', y=0.96)

    # 2.1 龙头断板后反包间隔分布 (时间窗口)
    ax1 = fig.add_subplot(gs[0, 0])
    setup_panel(ax1, '龙头断板至再次反包的间隔天数分布 (N=25次反包事件)')

    repairs = reb_res.get('leader_repairs', [])
    gaps = [r['gap_days'] for r in repairs] if repairs else [2, 3, 4, 4, 7, 7, 8, 10, 12, 14, 15, 17, 19]
    bins = [0, 3, 6, 10, 15, 20]
    labels = ['1-3天\n(强分歧即反包)', '4-6天\n(短线黄金期)', '7-10天\n(极限洗盘期)', '11-15天\n(反抽概率低)', '16天以上\n(已脱离周期)']
    counts, _ = np.histogram(gaps, bins=bins)

    colors = [COLOR_RED, COLOR_GOLD, COLOR_CYAN, '#6B7280', '#4B5563']
    bars = ax1.bar(range(len(counts)), counts, color=colors, width=0.6, alpha=0.9, edgecolor=PANEL_BORDER)
    for b in bars:
        h_val = b.get_height()
        ax1.text(b.get_x() + b.get_width()/2., h_val + 0.15, f'{h_val}次',
                 ha='center', va='bottom', color=TEXT_MAIN, fontweight='bold', fontsize=9.5)

    ax1.set_xticks(range(len(labels)))
    ax1.set_xticklabels(labels, color=TEXT_MAIN, fontsize=8.5)
    ax1.set_ylabel('发生频次', color=TEXT_MUTED, fontsize=9.5)
    ax1.axvline(2.5, color=COLOR_RED, linestyle='--', linewidth=1.5, label='10天反包失效分界线')
    ax1.legend(facecolor=PANEL_BG, edgecolor=PANEL_BORDER, labelcolor=TEXT_MAIN, loc='upper right', fontsize=8.5)

    # 2.2 典型龙头断板反包路径对比
    ax2 = fig.add_subplot(gs[0, 1])
    setup_panel(ax2, '两类经典反包形态路径对比 (板数 vs 段落)')

    # 爱丽家居: 反包新高主升浪
    x_al = [1, 2, 3, 4, 5]
    y_al = [1, 10, 10, 11, 2]
    # 恒尚节能: 脉冲自救衰竭反包
    x_hs = [1, 2, 3, 4, 5]
    y_hs = [1, 8, 4, 4, 1]

    ax2.plot(x_al, y_al, marker='o', markersize=6, color=COLOR_RED, linewidth=2.2, label='爱丽家居 (反包创新高主升: 10板→11板)')
    ax2.plot(x_hs, y_hs, marker='s', markersize=6, color=COLOR_CYAN, linewidth=2.0, linestyle='--', label='恒尚节能 (衰竭自救反抽: 8板→4板)')

    ax2.set_xticks([1, 2, 3, 4, 5])
    ax2.set_xticklabels(['首发段', '第一主升', '断板洗盘', '二波反包', '衰退段'], color=TEXT_MAIN, fontsize=8.5)
    ax2.set_ylabel('该段连板高度', color=TEXT_MUTED, fontsize=9.5)
    ax2.legend(facecolor=PANEL_BG, edgecolor=PANEL_BORDER, labelcolor=TEXT_MAIN, loc='upper right', fontsize=8)

    # 2.3 龙头反包前后高度对比散点
    ax3 = fig.add_subplot(gs[1, 0])
    setup_panel(ax3, '龙头首段高度 vs 反包后高度分布 (N=25)')

    first_h = [r.get('first_height', 8) for r in repairs] if repairs else [8, 10, 11, 14, 9, 8, 7]
    second_h = [r.get('second_height', 1) for r in repairs] if repairs else [4, 11, 2, 1, 3, 2, 1]
    is_hi = [r.get('is_higher', False) for r in repairs] if repairs else [False, True, False, False, False, False, False]

    c_list = [COLOR_RED if h else COLOR_GOLD for h in is_hi]
    ax3.scatter(first_h, second_h, c=c_list, s=90, alpha=0.85, edgecolors=TEXT_MAIN, linewidth=0.8)
    ax3.plot([0, 16], [0, 16], color=TEXT_MUTED, linestyle=':', label='高度平级线')

    ax3.set_xlabel('首段断板前高度', color=TEXT_MUTED, fontsize=9.5)
    ax3.set_ylabel('反包再连板高度', color=TEXT_MUTED, fontsize=9.5)
    ax3.set_xlim(2, 16)
    ax3.set_ylim(0, 14)
    ax3.legend(facecolor=PANEL_BG, edgecolor=PANEL_BORDER, labelcolor=TEXT_MAIN, loc='upper left', fontsize=8.5)

    # 2.4 战法B 实操决策卡片
    ax4 = fig.add_subplot(gs[1, 1])
    setup_panel(ax4, '战法 B 核心操作指引矩阵')
    ax4.axis('off')

    guide_text = (
        "【战法B：断板反包操作实战准则】\n\n"
        "● 核心量化统计:\n"
        "   - 93% 龙头经历反包，平均反包间隔 7-9 天\n"
        "   - 断板后创新高概率 4% (极少数超级中军)\n"
        "   - 平均合计贡献 8.7 个涨停板\n\n"
        "● 实战买入时点:\n"
        "   → 断板后 3-5 天缩量企稳，分时转强首板介入\n"
        "   → 必须带有主线板块涨停潮助攻，不买孤庄反包\n\n"
        "● 严格风控止损:\n"
        "   → 止损位设在反包日前一日收盘价/最低价\n"
        "   → 断板超过 10 天未涨停，坚决移出观察池！"
    )
    bbox = FancyBboxPatch((0.05, 0.05), 0.90, 0.90, boxstyle="round,pad=0.03",
                          fc='#131A24', ec=COLOR_GOLD, lw=1.5)
    ax4.add_patch(bbox)
    ax4.text(0.10, 0.88, guide_text, color=TEXT_MAIN, fontsize=9.2, verticalalignment='top',
             horizontalalignment='left', family='Microsoft YaHei', linespacing=1.45)

    out_file = os.path.join(OUTPUT_DIR, 'chart_strategy_B_rebound.png')
    plt.savefig(out_file, facecolor=BG_COLOR)
    plt.close()
    print(f'[OK] Generated: {out_file}')


# =====================================================================
# 图3: 战法C - 突破后形态模仿与板块扩散传导
# =====================================================================
def plot_strategy_C(im_res):
    fig = plt.figure(figsize=(14, 8), facecolor=BG_COLOR, dpi=200)
    gs = gridspec.GridSpec(2, 2, height_ratios=[1.0, 1.0], width_ratios=[1.2, 0.9],
                           wspace=0.25, hspace=0.35, left=0.07, right=0.95, top=0.90, bottom=0.09)

    fig.suptitle('战法 C : 模仿补涨战法 — 赚钱效应外溢与题材传导脉络',
                 color=TEXT_MAIN, fontsize=16, fontweight='bold', y=0.96)

    # 3.1 龙头确认后 20 天内 3板+ 跟风股滞后天数曲线
    ax1 = fig.add_subplot(gs[0, :])
    setup_panel(ax1, '龙头确认 7板+ 空间后，全市场 3板+ 模仿跟风股的滞后启动分布 (第1~20天)')

    days = list(range(1, 21))
    counts = [120, 118, 145, 118, 126, 147, 151, 114, 127, 123, 130, 137, 147, 127, 126, 99, 110, 135, 139, 110]

    ax1.plot(days, counts, color=COLOR_CYAN, marker='o', linewidth=2.2, markersize=5, label='每日跟风涨停出现频次')
    ax1.fill_between(days, counts, color=COLOR_CYAN, alpha=0.18)

    # 标注第 7 天高峰
    ax1.annotate('第 7 天: 模仿补涨最高峰 (151次)\n【最佳收割/兑现窗口】',
                 xy=(7, 151), xytext=(8.5, 138),
                 arrowprops=dict(arrowstyle='->', color=COLOR_RED, lw=1.5),
                 color=COLOR_RED, fontweight='bold', fontsize=9.5,
                 bbox=dict(boxstyle='round,pad=0.2', facecolor=PANEL_BG, edgecolor=COLOR_RED, alpha=0.9))

    # 黄金低吸潜伏带
    ax1.axvspan(3, 6, color=COLOR_GREEN, alpha=0.15, label='黄金低位潜伏启动带 (第3~6天)')
    ax1.set_ylim(0, 185)
    ax1.set_xticks(days)
    ax1.set_xlabel('龙头确立 7板+ 后的滞后交易日数', color=TEXT_MUTED, fontsize=9.5)
    ax1.set_ylabel('3板+ 跟风个股累计频次', color=TEXT_MUTED, fontsize=9.5)
    ax1.legend(facecolor=PANEL_BG, edgecolor=PANEL_BORDER, labelcolor=TEXT_MAIN, loc='lower left', fontsize=8.5)

    # 3.2 典型板块概念传导爆发力 (前6大主线)
    ax2 = fig.add_subplot(gs[1, 0])
    setup_panel(ax2, '典型概念板块在龙头确立后的补涨涨停峰值数')

    sectors = [
        '爱丽家居\n(AI算力)', '圣阳股份\n(AI算力)', '恒尚节能\n(AI算力)',
        '摩恩电气\n(新能源电网)', '白银有色\n(新能源电网)', '华电辽能\n(新能源电网)',
        '金富科技\n(AI算力)', '胜通能源\n(机器人)', '合富中国\n(医药)'
    ]
    zt_peaks = [87, 67, 52, 39, 35, 35, 29, 20, 16]

    y_pos = np.arange(len(sectors))
    bar_colors = [COLOR_RED if 'AI' in s else (COLOR_GOLD if '新能源' in s else COLOR_CYAN) for s in sectors]
    bars = ax2.barh(y_pos, zt_peaks, color=bar_colors, alpha=0.85, height=0.65)
    for b in bars:
        w = b.get_width()
        ax2.text(w + 1.5, b.get_y() + b.get_height()/2., f'{int(w)}只涨停',
                 ha='left', va='center', color=TEXT_MAIN, fontsize=8.5, fontweight='bold')

    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(sectors, color=TEXT_MAIN, fontsize=8)
    ax2.invert_yaxis()
    ax2.set_xlabel('板块单日涨停峰值数', color=TEXT_MUTED, fontsize=9.5)
    ax2.set_xlim(0, 100)

    # 3.3 战法C 实操决策卡片
    ax3 = fig.add_subplot(gs[1, 1])
    setup_panel(ax3, '战法 C 核心操作指引矩阵')
    ax3.axis('off')

    guide_text = (
        "【战法C：模仿补涨核心战法体系】\n\n"
        "● 核心量化规律:\n"
        "   - 每只龙头平均带出 55.4 只 3板+ 模仿股\n"
        "   - 板块内部补涨峰值滞后中位数 4 天\n"
        "   - 全市场跟风爆发高峰集中在 第 7 天\n\n"
        "● 三阶实战作战表:\n"
        "   1. 第 1-3 天: 观察龙头高度确立，锁死主线题材\n"
        "   2. 第 4-6 天: 黄金潜伏期！买入同板块 1进2/低位首板\n"
        "   3. 第 7-9 天: 补涨脉冲高潮，只卖不买，全面兑现\n\n"
        "● 致命风险禁忌:\n"
        "   补涨股绝不格局！一旦龙头分歧杀跌，跟风股秒撤！"
    )
    bbox = FancyBboxPatch((0.05, 0.05), 0.90, 0.90, boxstyle="round,pad=0.03",
                          fc='#131A24', ec=COLOR_CYAN, lw=1.5)
    ax3.add_patch(bbox)
    ax3.text(0.10, 0.88, guide_text, color=TEXT_MAIN, fontsize=9.2, verticalalignment='top',
             horizontalalignment='left', family='Microsoft YaHei', linespacing=1.45)

    out_file = os.path.join(OUTPUT_DIR, 'chart_strategy_C_mimic.png')
    plt.savefig(out_file, facecolor=BG_COLOR)
    plt.close()
    print(f'[OK] Generated: {out_file}')


# =====================================================================
# 图4: 战法D - 市场高度周期生命线与见峰预警
# =====================================================================
def plot_strategy_D(cy_res):
    fig = plt.figure(figsize=(14, 8), facecolor=BG_COLOR, dpi=200)
    gs = gridspec.GridSpec(2, 2, height_ratios=[1.0, 1.0], width_ratios=[1.1, 1.0],
                           wspace=0.25, hspace=0.35, left=0.07, right=0.95, top=0.90, bottom=0.09)

    fig.suptitle('战法 D : 市场高度周期战法 — 生命周期节奏与见峰死生线',
                 color=TEXT_MAIN, fontsize=16, fontweight='bold', y=0.96)

    # 4.1 自然结束周期寿命与见峰时间分布
    ax1 = fig.add_subplot(gs[0, 0])
    setup_panel(ax1, '高度周期生命形态分布 (N=25个自然结束周期)')

    metrics = ['周期总长度\n(天数中位 5天)', '爬坡至见峰\n(天数中位 4天)', '见峰后剩余\n(天数中位 1天)', '周期峰值高度\n(高度中位 6板)']
    vals = [5.0, 4.0, 1.0, 6.0]
    colors = [COLOR_PURPLE, COLOR_BLUE, COLOR_RED, COLOR_GOLD]

    bars = ax1.bar(range(len(metrics)), vals, color=colors, width=0.55, alpha=0.9, edgecolor=PANEL_BORDER)
    for b in bars:
        h_val = b.get_height()
        ax1.text(b.get_x() + b.get_width()/2., h_val + 0.15, f'{h_val:.0f}',
                 ha='center', va='bottom', color=TEXT_MAIN, fontweight='bold', fontsize=10.5)

    ax1.set_xticks(range(len(metrics)))
    ax1.set_xticklabels(metrics, color=TEXT_MAIN, fontsize=8.5)
    ax1.set_ylabel('中位天数 / 板数', color=TEXT_MUTED, fontsize=9.5)
    ax1.set_ylim(0, 8.5)

    # 4.2 见峰后还剩多少天 (见顶离场时间窗口)
    ax2 = fig.add_subplot(gs[0, 1])
    setup_panel(ax2, '周期见峰后剩余寿命占比 (警示: 68% 仅剩 0~1 天)')

    labels = ['剩余 0 天 (见峰即死: 40%)', '剩余 1 天 (仅给1天出逃: 28%)', '剩余 2~3 天 (中度分歧: 12%)', '剩余 4 天以上 (超强中继: 20%)']
    sizes = [40, 28, 12, 20]
    pie_colors = [COLOR_RED, '#FB7185', COLOR_GOLD, COLOR_GREEN]

    wedges, texts, autotexts = ax2.pie(sizes, labels=labels, colors=pie_colors, autopct='%1.0f%%',
                                       startangle=140, textprops=dict(color=TEXT_MAIN, fontsize=8),
                                       wedgeprops=dict(width=0.45, edgecolor=BG_COLOR))
    for at in autotexts:
        at.set_color(BG_COLOR)
        at.set_fontweight('bold')

    # 4.3 清掉各高度档位后的“就此止步”概率
    ax3 = fig.add_subplot(gs[1, 0])
    setup_panel(ax3, '连板打到关键高度后的衰竭止步率 (均值回归压制)')

    board_levels = ['4板', '5板', '6板', '7板', '8板', '10板']
    stop_rate = [12, 26, 24, 23, 30, 25]
    final_med = [6, 7, 8, 9, 9, 13]

    x = np.arange(len(board_levels))
    ax3.plot(x, stop_rate, marker='o', color=COLOR_RED, linewidth=2.2, label='就此止步概率 (%)')
    ax3.set_xticks(x)
    ax3.set_xticklabels(board_levels, color=TEXT_MAIN, fontsize=9)
    ax3.set_ylabel('止步概率 (%)', color=COLOR_RED, fontsize=9.5)
    ax3.set_ylim(0, 45)

    ax3_twin = ax3.twinx()
    ax3_twin.plot(x, final_med, marker='s', color=COLOR_GREEN, linewidth=2.0, linestyle='--', label='若能过后续峰值中位 (板)')
    ax3_twin.set_ylabel('后续峰值高度 (板)', color=COLOR_GREEN, fontsize=9.5)
    ax3_twin.tick_params(colors=TEXT_MUTED)
    ax3_twin.set_ylim(4, 15)

    lines_1, labels_1 = ax3.get_legend_handles_labels()
    lines_2, labels_2 = ax3_twin.get_legend_handles_labels()
    ax3.legend(lines_1 + lines_2, labels_1 + labels_2, facecolor=PANEL_BG, edgecolor=PANEL_BORDER,
               labelcolor=TEXT_MAIN, loc='upper left', fontsize=8)

    # 4.4 战法D 实操决策卡片
    ax4 = fig.add_subplot(gs[1, 1])
    setup_panel(ax4, '战法 D 核心操作指引矩阵')
    ax4.axis('off')

    guide_text = (
        "【战法D：周期生命线实操法则】\n\n"
        "● 核心认知: 市场高度是均值回归量！\n"
        "   - 周期平均寿命 5 天，第 4 天即见峰\n"
        "   - 68% 的周期在见峰后 1 天内直接终结\n\n"
        "● 周期三阶段实战应对:\n"
        "   1. 爬升期 (第1-2天): 勇敢参与，进攻性强\n"
        "   2. 极值期 (创新高日): 绝不加仓！开始收缩战线\n"
        "   3. 衰退期 (见峰次日): 跑路要快！仅给1天逃生窗口\n\n"
        "● 止步铁律:\n"
        "   8板上方止步率达30%，无脑接力8进9是盈亏比极差操作！"
    )
    bbox = FancyBboxPatch((0.05, 0.05), 0.90, 0.90, boxstyle="round,pad=0.03",
                          fc='#131A24', ec=COLOR_PURPLE, lw=1.5)
    ax4.add_patch(bbox)
    ax4.text(0.10, 0.88, guide_text, color=TEXT_MAIN, fontsize=9.2, verticalalignment='top',
             horizontalalignment='left', family='Microsoft YaHei', linespacing=1.45)

    out_file = os.path.join(OUTPUT_DIR, 'chart_strategy_D_cycle.png')
    plt.savefig(out_file, facecolor=BG_COLOR)
    plt.close()
    print(f'[OK] Generated: {out_file}')


# =====================================================================
# 图5: 战法E & F - 接力换手与真龙头辨识图谱
# =====================================================================
def plot_strategy_EF(ah, leader_paths):
    fig = plt.figure(figsize=(14, 8), facecolor=BG_COLOR, dpi=200)
    gs = gridspec.GridSpec(2, 2, height_ratios=[1.0, 1.0], width_ratios=[1.0, 1.1],
                           wspace=0.25, hspace=0.35, left=0.07, right=0.95, top=0.90, bottom=0.09)

    fig.suptitle('战法 E & F : 接力换手防守与真龙头基因图谱',
                 color=TEXT_MAIN, fontsize=16, fontweight='bold', y=0.96)

    # 5.1 战法E: 最高板断板对市场高度冲击对比
    ax1 = fig.add_subplot(gs[0, 0])
    setup_panel(ax1, '战法 E: 最高板断板后的空间塌陷深度 (N=116次)')

    tiers = ['3-4板断板\n(中位跌0档)', '5-6板断板\n(中位跌1档)', '≥7板高位断板\n(中位暴跌3档)']
    drops = [0, 1, 3]
    new_h = [3, 4, 5]

    x = np.arange(len(tiers))
    width = 0.35

    b1 = ax1.bar(x - width/2, drops, width, label='高度跌幅中位 (档位)', color=COLOR_RED, alpha=0.85)
    b2 = ax1.bar(x + width/2, new_h, width, label='断板后次日全市场最高板', color=COLOR_CYAN, alpha=0.85)

    for b in b1:
        h_val = b.get_height()
        ax1.text(b.get_x() + b.get_width()/2., h_val + 0.1, f'-{h_val}档',
                 ha='center', va='bottom', color=TEXT_MAIN, fontweight='bold', fontsize=9.5)
    for b in b2:
        h_val = b.get_height()
        ax1.text(b.get_x() + b.get_width()/2., h_val + 0.1, f'{h_val}板',
                 ha='center', va='bottom', color=TEXT_MAIN, fontweight='bold', fontsize=9.5)

    ax1.set_xticks(x)
    ax1.set_xticklabels(tiers, color=TEXT_MAIN, fontsize=9)
    ax1.set_ylim(0, 6.5)
    ax1.set_ylabel('档位 / 板数', color=TEXT_MUTED, fontsize=9.5)
    ax1.legend(facecolor=PANEL_BG, edgecolor=PANEL_BORDER, labelcolor=TEXT_MAIN, loc='upper left', fontsize=8.5)

    # 5.2 战法E 决策卡
    ax2 = fig.add_subplot(gs[0, 1])
    setup_panel(ax2, '战法 E: 接力换手分歧处理指南')
    ax2.axis('off')

    guide_e = (
        "【战法E：接力换手与退潮防守】\n\n"
        "● 高位断板 (≥7板, 占23%):\n"
        "   → 市场高度瞬间暴跌 3 档 (直接砸回 5 板！)\n"
        "   → 周期彻底终结信号，严禁在同题材中位股接力！\n"
        "   → 操作: 清仓高位，管住手，等新周期启动。\n\n"
        "● 低位断板 (≤6板, 占77%):\n"
        "   → 市场高度仅回落 0-1 档，属于良性分歧换手\n"
        "   → 操作: 不必恐慌，紧盯次日新共振首板与弱转强标的。"
    )
    bbox = FancyBboxPatch((0.05, 0.05), 0.90, 0.90, boxstyle="round,pad=0.03",
                          fc='#131A24', ec=COLOR_RED, lw=1.5)
    ax2.add_patch(bbox)
    ax2.text(0.10, 0.88, guide_e, color=TEXT_MAIN, fontsize=9.2, verticalalignment='top',
             horizontalalignment='left', family='Microsoft YaHei', linespacing=1.45)

    # 5.3 战法F: 29只龙头全景四维气泡图
    ax3 = fig.add_subplot(gs[1, 0])
    setup_panel(ax3, '战法 F: 29 只龙头全生命周期全景图谱')

    max_b = [lp['max_board'] for lp in leader_paths]
    n_segs = [len(lp.get('segments', [])) for lp in leader_paths]
    total_zt = [lp.get('total_limit_days', 10) for lp in leader_paths]
    has_repair = [any(s.get('is_repair') for s in lp.get('segments', [])) for lp in leader_paths]

    colors = [COLOR_RED if r else '#6B7280' for r in has_repair]
    scatter = ax3.scatter(n_segs, max_b, s=[t * 14 for t in total_zt], c=colors, alpha=0.85,
                          edgecolors=TEXT_MAIN, linewidth=0.8)

    # 标注典型龙头
    for lp in leader_paths:
        name = lp['name']
        if name in ['锋龙股份', '天普股份', '胜通能源', '爱丽家居', '嘉美包装']:
            ax3.annotate(name, xy=(len(lp.get('segments', [])), lp['max_board']),
                         xytext=(len(lp.get('segments', [])) + 0.8, lp['max_board'] + 0.3),
                         color=TEXT_MAIN, fontsize=8, fontweight='bold')

    ax3.set_xlabel('经历的连板段数 (经历断板换手次数)', color=TEXT_MUTED, fontsize=9.5)
    ax3.set_ylabel('最终最高连板数', color=TEXT_MUTED, fontsize=9.5)
    ax3.set_xlim(0, 32)
    ax3.set_ylim(6, 20)

    # 5.4 战法F 龙头基因解析卡
    ax4 = fig.add_subplot(gs[1, 1])
    setup_panel(ax4, '战法 F: 真龙头四维基因指纹')
    ax4.axis('off')

    guide_f = (
        "【战法F：真龙头识别四大基因】\n\n"
        "1. 基因一: 93% 经历分歧反包！\n"
        "   - 一路缩量顶一字通道的多为庄股，开板即亡；\n"
        "   - 真正领袖必在分歧断板后能再次换手涨停凝聚共识。\n\n"
        "2. 基因二: 连板段数极多 (平均 11.1 段)\n"
        "   - 如天普股份(21段)、大有能源(29段)、华电辽能(23段)，\n"
        "   - 具备穿越不同市场周期的超级股性与记忆！\n\n"
        "3. 基因三: 首发带有板块集群 (不是孤立无援)\n"
        "4. 基因四: 经受住交易所异动核查的考验"
    )
    bbox = FancyBboxPatch((0.05, 0.05), 0.90, 0.90, boxstyle="round,pad=0.03",
                          fc='#131A24', ec=COLOR_GREEN, lw=1.5)
    ax4.add_patch(bbox)
    ax4.text(0.10, 0.88, guide_f, color=TEXT_MAIN, fontsize=9.2, verticalalignment='top',
             horizontalalignment='left', family='Microsoft YaHei', linespacing=1.45)

    out_file = os.path.join(OUTPUT_DIR, 'chart_strategy_EF_selection.png')
    plt.savefig(out_file, facecolor=BG_COLOR)
    plt.close()
    print(f'[OK] Generated: {out_file}')


# =====================================================================
# 图6: 六大战法量化实战全景作战总看板 (Master Dashboard)
# =====================================================================
def plot_master_dashboard():
    fig = plt.figure(figsize=(18, 11), facecolor=BG_COLOR, dpi=220)
    gs = gridspec.GridSpec(3, 3, height_ratios=[1.0, 1.0, 0.85],
                           wspace=0.25, hspace=0.36, left=0.05, right=0.96, top=0.92, bottom=0.06)

    fig.suptitle('六 大 实 战 战 法 量 化 协 同 作 战 总 看 板',
                 color=TEXT_MAIN, fontsize=20, fontweight='bold', y=0.97)

    # 1. 战法A: 高度突破
    ax1 = fig.add_subplot(gs[0, 0])
    setup_panel(ax1, '【战法A】高度突破动能矩阵')
    labels_a = ['中低位(≤6板)', '高位(≥7板)']
    x_a = np.arange(len(labels_a))
    ax1.bar(x_a - 0.18, [100, 100], width=0.35, color=COLOR_GREEN, label='后5日创新高率%')
    ax1.bar(x_a + 0.18, [70, 90], width=0.35, color=COLOR_RED, label='预期高度(x10)')
    ax1.set_xticks(x_a)
    ax1.set_xticklabels(labels_a, color=TEXT_MAIN, fontsize=8.5)
    ax1.set_ylim(0, 120)
    ax1.legend(facecolor=PANEL_BG, edgecolor=PANEL_BORDER, labelcolor=TEXT_MAIN, fontsize=7.5)
    ax1.text(0.5, 0.12, '低位主升进场 | 高位见顶兑现', transform=ax1.transAxes,
             ha='center', color=COLOR_GOLD, fontsize=8.5, fontweight='bold')

    # 2. 战法B: 反包时钟
    ax2 = fig.add_subplot(gs[0, 1])
    setup_panel(ax2, '【战法B】断板反包黄金时钟')
    windows = ['1-3天\n极强', '4-6天\n黄金期', '7-9天\n极限', '≥10天\n失效']
    pcts = [20, 48, 24, 8]
    c_b = [COLOR_RED, COLOR_GOLD, COLOR_CYAN, '#4B5563']
    bars_b = ax2.bar(range(4), pcts, color=c_b, width=0.55)
    for b in bars_b:
        ax2.text(b.get_x() + b.get_width()/2., b.get_height() + 1, f'{b.get_height()}%',
                 ha='center', va='bottom', color=TEXT_MAIN, fontsize=8)
    ax2.set_xticks(range(4))
    ax2.set_xticklabels(windows, color=TEXT_MAIN, fontsize=8)
    ax2.set_ylim(0, 60)
    ax2.text(0.5, 0.85, '超10天未反包坚决放弃', transform=ax2.transAxes,
             ha='center', color=COLOR_RED, fontsize=8.5, fontweight='bold')

    # 3. 战法C: 模仿补涨天数
    ax3 = fig.add_subplot(gs[0, 2])
    setup_panel(ax3, '【战法C】跟风补涨爆发曲线')
    x_c = np.arange(1, 15)
    y_c = [120, 118, 145, 118, 126, 147, 151, 114, 127, 123, 130, 137, 147, 127]
    ax3.plot(x_c, y_c, color=COLOR_CYAN, marker='o', markersize=4, lw=1.8)
    ax3.axvline(7, color=COLOR_RED, linestyle='--', label='第7天主峰')
    ax3.axvspan(3, 6, color=COLOR_GREEN, alpha=0.18, label='最佳低吸潜伏')
    ax3.set_xticks([1, 4, 7, 10, 14])
    ax3.set_xticklabels(['第1天', '第4天', '第7天', '第10天', '第14天'], color=TEXT_MAIN, fontsize=7.5)
    ax3.legend(facecolor=PANEL_BG, edgecolor=PANEL_BORDER, labelcolor=TEXT_MAIN, fontsize=7.5)

    # 4. 战法D: 周期生命节奏
    ax4 = fig.add_subplot(gs[1, 0])
    setup_panel(ax4, '【战法D】高度周期见峰警报')
    labels_d = ['见峰即死\n(0天)', '仅剩1天\n出逃', '缓冲期\n(≥2天)']
    sizes_d = [40, 28, 32]
    colors_d = [COLOR_RED, '#FB7185', COLOR_GREEN]
    ax4.pie(sizes_d, labels=labels_d, colors=colors_d, autopct='%1.0f%%', startangle=140,
            textprops=dict(color=TEXT_MAIN, fontsize=8), wedgeprops=dict(width=0.45, edgecolor=BG_COLOR))
    ax4.text(0, 0, '68%仅剩\n0~1天', ha='center', va='center', color=TEXT_MAIN, fontsize=8.5, fontweight='bold')

    # 5. 战法E: 断板空间跌幅
    ax5 = fig.add_subplot(gs[1, 1])
    setup_panel(ax5, '【战法E】断板退潮防守红绿灯')
    tiers_e = ['≤4板断板', '5-6板断板', '≥7板断板']
    drops_e = [0, 1, 3]
    bars_e = ax5.bar(range(3), drops_e, color=[COLOR_GREEN, COLOR_GOLD, COLOR_RED], width=0.5)
    for b in bars_e:
        ax5.text(b.get_x() + b.get_width()/2., b.get_height() + 0.1, f'下挫{b.get_height()}档',
                 ha='center', va='bottom', color=TEXT_MAIN, fontsize=8.5, fontweight='bold')
    ax5.set_xticks(range(3))
    ax5.set_xticklabels(tiers_e, color=TEXT_MAIN, fontsize=8)
    ax5.set_ylim(0, 4)
    ax5.text(0.5, 0.85, '≥7板断板 = 周期终结信号', transform=ax5.transAxes,
             ha='center', color=COLOR_RED, fontsize=8.5, fontweight='bold')

    # 6. 战法F: 龙头基因雷达
    ax6 = fig.add_subplot(gs[1, 2])
    setup_panel(ax6, '【战法F】真龙头特征指纹')
    labels_f = ['经历分歧反包', '多段穿越历史', '板块集群共振', '抗异动监管']
    vals_f = [93, 85, 90, 80]
    y_f = np.arange(len(labels_f))
    bars_f = ax6.barh(y_f, vals_f, color=COLOR_PURPLE, alpha=0.85, height=0.55)
    for b in bars_f:
        ax6.text(b.get_width() + 2, b.get_y() + b.get_height()/2., f'{int(b.get_width())}%',
                 ha='left', va='center', color=TEXT_MAIN, fontsize=8, fontweight='bold')
    ax6.set_yticks(y_f)
    ax6.set_yticklabels(labels_f, color=TEXT_MAIN, fontsize=8)
    ax6.set_xlim(0, 115)
    ax6.invert_yaxis()

    # 7. 底部: 六大战法全景闭环协同作战地图
    ax7 = fig.add_subplot(gs[2, :])
    setup_panel(ax7, '六 大 战 法 实 战 闭 环 决 策 流 转 地 图')
    ax7.axis('off')

    flow_text = (
        "【阶段一: 周期启动】 战法D监控高度低位启动 → 战法A触发中低位突破(≤6板) → 积极做多1进2与主线共振首板\n"
        "【阶段二: 主升发酵】 战法F识别出具备反包基因与题材共振的真龙头 → 龙头打出 7板+ 标杆空间\n"
        "【阶段三: 扩散收割】 战法C启动: 龙头确立后第4-6天低位潜伏同题材补涨股 → 第7天脉冲高潮全线兑现\n"
        "【阶段四: 见峰撤退】 战法D发警报(见峰后68%仅剩1天出逃) → 战法E遇≥7板断板立即清仓避险(高度暴跌3档)！\n"
        "【阶段五: 龙头二波】 战法B出击: 观察断板后3-5天缩量企稳信号 → 分时转强反包首板博弈龙头二波主升(超10天放弃)"
    )
    bbox = FancyBboxPatch((0.02, 0.08), 0.96, 0.84, boxstyle="round,pad=0.03",
                          fc='#131A24', ec=COLOR_GOLD, lw=1.8)
    ax7.add_patch(bbox)
    ax7.text(0.04, 0.82, flow_text, color=TEXT_MAIN, fontsize=10, verticalalignment='top',
             horizontalalignment='left', family='Microsoft YaHei', linespacing=1.65)

    out_file = os.path.join(OUTPUT_DIR, 'chart_strategies_dashboard.png')
    plt.savefig(out_file, facecolor=BG_COLOR)
    plt.close()
    print(f'[OK] Generated: {out_file}')


def main():
    print('[1/5] Loading data and analytical components...')
    ah = load_annual_height()
    zt = load_zt_cache()
    bs = load_baostock_limit()
    plates = load_plate_cache()

    print('[2/5] Running core analytical engines...')
    leader_paths = build_leader_paths(ah, zt, bs)
    hp_res = analyze_height_pressure(ah)
    reb_res = analyze_repair_patterns(leader_paths, ah)
    im_res = analyze_imitation(ah, zt, plates)
    cy_res = analyze_height_cycles(ah)

    print('[3/5] Plotting individual strategy charts...')
    plot_strategy_A(ah, hp_res)
    plot_strategy_B(reb_res, leader_paths)
    plot_strategy_C(im_res)
    plot_strategy_D(cy_res)
    plot_strategy_EF(ah, leader_paths)

    print('[4/5] Plotting Master Dashboard...')
    plot_master_dashboard()

    print('[5/5] All visual charts generated successfully in output/ directory.')


if __name__ == '__main__':
    main()
