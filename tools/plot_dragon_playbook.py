# pyright: reportMissingTypeStubs=false
"""龙头接替培训手册配图 (6 张, 深色信息图, 每章一张)。
数据全部取自手册已定论的引擎实测值, 零缓存依赖 —— 纯自包含, 稳。
出图统一走 plot_utils.safe_savefig 防 2000px 上限。
"""
import os, sys, io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch
from datetime import date
from matplotlib import font_manager

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'src'))

from console_io import enable_utf8_console  # noqa: E402

enable_utf8_console()  # 输出被重定向到文件/管道时, emoji print 不再撞 Windows GBK
from plot_utils import safe_savefig

OUT = os.path.join(_ROOT, 'output', 'dragon_imgs')
os.makedirs(OUT, exist_ok=True)

# ── 中文字体 (强制覆盖, 防豆腐块) ──
_installed = {fp.name for fp in font_manager.fontManager.ttflist}
for f in ['Microsoft YaHei', 'SimHei', 'PingFang SC']:
    if f in _installed:
        plt.rcParams['font.sans-serif'] = [f, 'DejaVu Sans']
        plt.rcParams['font.monospace'] = [f, 'DejaVu Sans Mono']
        plt.rcParams['font.family'] = 'sans-serif'
        break
plt.rcParams['axes.unicode_minus'] = False

# ── 深色主题 (GitHub 风, 与项目其余出图一致) ──
BG, PANEL, EDGE, TXT, MUTE = '#0d1117', '#161b22', '#30363d', '#e6edf3', '#8b949e'
GREEN, RED, YELLOW, BLUE, ORANGE = '#3fb950', '#f85149', '#d29922', '#58a6ff', '#db6d28'
plt.rcParams.update({
    'figure.facecolor': BG, 'axes.facecolor': PANEL, 'axes.edgecolor': EDGE,
    'axes.labelcolor': TXT, 'text.color': TXT, 'xtick.color': TXT, 'ytick.color': TXT,
    'axes.titlecolor': YELLOW, 'grid.color': EDGE, 'figure.dpi': 100,
})

def _save(fig, name):
    p = os.path.join(OUT, name)
    safe_savefig(fig, p, dpi=150, bbox_inches='tight', facecolor=BG)
    plt.close(fig)
    print(f"  [ok] {name}")


# ════════════════════════════════════════════════════════════
# 图1 · 序 —— 周期时间线 (阶段色带 + 事件节点)
# ════════════════════════════════════════════════════════════
def fig_timeline():
    fig, ax = plt.subplots(figsize=(13, 4.6))
    d = lambda m, day: mdates.date2num(date(2026, m, day))

    # 阶段色带 [起, 止, 颜色, 名称]
    bands = [
        (d(7,15), d(7,22), BLUE,   '酝酿开闸'),
        (d(7,22), d(8,6),  GREEN,  '主升接力'),
        (d(8,6),  d(8,12), YELLOW, '见顶'),
        (d(8,12), d(8,18), ORANGE, '停牌孵化'),
        (d(8,18), d(8,20), RED,    '崩溃'),
        (d(8,20), d(8,25), MUTE,   '退潮'),
    ]
    for x0, x1, c, nm in bands:
        ax.axvspan(x0, x1, color=c, alpha=0.16)
        ax.text((x0+x1)/2, 0.06, nm, ha='center', va='bottom', color=c,
                fontsize=11, fontweight='bold')

    # 事件节点 [日期, 文字, 板高(y), 颜色]
    ev = [
        (d(7,15), '07-15\n多主板高标\n开闸', 2, BLUE),
        (d(7,22), '07-22\n爱丽首板起爆\n真龙确立', 5, GREEN),
        (d(8,6),  '08-06\n见顶 10 板\n周期最高', 10, YELLOW),
        (d(8,12), '08-12\n爱丽停牌\n孵化下一棒', 7, ORANGE),
        (d(8,19), '08-19\n涨停腰斩+跌停井喷\n崩溃盖棺', 3, RED),
        (d(8,25), '08-25\n汉森逆势5连板\n新周期种子', 5, GREEN),
    ]
    xs = [e[0] for e in ev]; ys = [e[2] for e in ev]
    ax.plot(xs, ys, '-', color=MUTE, lw=1.4, alpha=0.5, zorder=1)
    for x, t, y, c in ev:
        ax.scatter([x], [y], s=180, color=c, edgecolor='white', lw=1.2, zorder=3)
        va = 'bottom' if y < 6 else 'top'
        off = 0.9 if y < 6 else -0.9
        ax.annotate(t, (x, y), xytext=(x, y+off), ha='center', va=va,
                    fontsize=9, color=TXT, fontweight='bold', zorder=4)

    ax.set_ylim(-0.5, 12.5)
    ax.set_ylabel('周期最高连板数', fontsize=12)
    ax.set_title('图0 · 一波监管周期的完整生命线 —— 爱丽家居 2026 / 7–8 月 (27 个交易日)',
                 fontsize=15, fontweight='bold', pad=14)
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.grid(axis='y', alpha=0.25)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    fig.text(0.5, -0.02, '读法:先定位「现在在这条线的哪一段」,再决定做多 / 做空 / 空仓',
             ha='center', fontsize=10, color=MUTE, style='italic')
    _save(fig, '0_timeline.png')


# ════════════════════════════════════════════════════════════
# 图2 · 一二章 —— 核心脊柱接替时序 + 停牌孵化
# ════════════════════════════════════════════════════════════
def fig_succession():
    fig, ax = plt.subplots(figsize=(12, 5.6))
    d = lambda m, day: mdates.date2num(date(2026, m, day))
    # 8 脊柱: 名, 首涨(月,日), 板高, sev, 顺位y
    spine = [
        ('百花医药', (7,15), 7, 3, 8),
        ('云创退',   (7,15), 5, 3, 7),
        ('立新能源', (7,17), 6, 3, 6),
        ('爱丽家居', (7,22), 10, 5, 5),
        ('五洲医疗', (7,22), 4, 5, 4),
        ('传智教育', (7,27), 8, 3, 3),
        ('神雾节能', (7,30), 4, 5, 2),
        ('蓝盾光电', (8,10), 5, 5, 1),
    ]
    sev_color = {5: RED, 3: ORANGE, 2: YELLOW}
    for nm, (m, dd), h, sv, y in spine:
        x = d(m, dd)
        ax.scatter([x], [y], s=90+h*46, color=sev_color[sv],
                   edgecolor='white', lw=1.1, zorder=3)
        ax.text(x, y, str(h), ha='center', va='center', fontsize=9,
                color='white', fontweight='bold', zorder=4)
        star = ' ★锚定龙头' if nm == '爱丽家居' else ''
        # 圈半径随板高变大, 标签偏移量同步放大, 防文字压在圈上
        ax.text(x - (0.55 + h*0.11), y, nm+star, ha='right', va='center', fontsize=10,
                color=YELLOW if star else TXT, fontweight='bold' if star else 'normal')

    # 爱丽停牌段 (08-12~08-18) 虚线框
    ax.plot([d(8,12), d(8,18)], [5, 5], '--', color=BLUE, lw=2, zorder=2)
    ax.scatter([d(8,18)], [5], marker='D', s=70, color=BLUE, edgecolor='white',
               lw=1, zorder=3)
    ax.text(d(8,15), 5.42, '停牌核查', ha='center', fontsize=8.5, color=BLUE)

    # 孵化棒 (非脊柱) —— 停牌窗内首涨的新面孔; 标签纵向错开防重叠
    hatch = [('金螳螂', (8,12), 0.55), ('澳洋健康', (8,12), 0.12),
             ('神奇制药', (8,13), -0.32), ('天山生物', (8,17), 0.12)]
    for nm, (m, dd), y in hatch:
        x = d(m, dd)
        ax.scatter([x], [y], s=70, color=GREEN, marker='^', edgecolor='white', lw=0.8, zorder=3)
        arr = FancyArrowPatch((d(8,15), 4.4), (x, y+0.12), arrowstyle='->',
                              color=GREEN, alpha=0.5, lw=1.1, mutation_scale=11,
                              connectionstyle='arc3,rad=-0.15', zorder=1)
        ax.add_patch(arr)
        ax.text(x+0.35, y, nm, ha='left', va='center', fontsize=8.5, color=GREEN)
    ax.text(d(8,4), 1.5, '停牌 = 资金外溢\n孵化下一棒', ha='center', fontsize=9.5,
            color=GREEN, fontweight='bold')

    ax.set_xlim(mdates.date2num(date(2026,7,11)), mdates.date2num(date(2026,8,20)))
    ax.set_ylim(-0.9, 9)
    ax.set_yticks([])
    ax.set_title('图1 · 核心脊柱接替谱系 —— 圈内数字=最高连板, 圈色=监管等级, ↑=停牌孵化的新棒',
                 fontsize=13.5, fontweight='bold', pad=12)
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.grid(axis='x', alpha=0.2)
    for s in ('top', 'right', 'left'):
        ax.spines[s].set_visible(False)
    leg = [mpatches.Patch(color=RED, label='sev5 复牌 (突破监管压制)'),
           mpatches.Patch(color=ORANGE, label='sev3 严重异动 / 问询'),
           mpatches.Patch(color=GREEN, label='停牌孵化的新棒')]
    ax.legend(handles=leg, loc='upper right', fontsize=9, framealpha=0.85,
              facecolor=PANEL, edgecolor=EDGE, labelcolor=TXT)
    _save(fig, '1_succession.png')


# ════════════════════════════════════════════════════════════
# 图3 · 三章 —— 连板晋级率 (低位最难, 反直觉)
# ════════════════════════════════════════════════════════════
def fig_promotion():
    fig, ax = plt.subplots(figsize=(10, 5))
    labels = ['1→2', '2→3', '3→4', '4→5', '5→6', '6→7']
    vals = [12.2, 29.8, 45.0, 39.6, 45.2, 55.3]
    colors = [RED, ORANGE, GREEN, GREEN, GREEN, GREEN]
    bars = ax.bar(labels, vals, color=colors, edgecolor=EDGE, width=0.62)
    for b, v in zip(bars, vals):
        ax.text(b.get_x()+b.get_width()/2, v+1.2, f'{v}%', ha='center',
                fontsize=11, color=TXT, fontweight='bold')
    ax.axhspan(0, 30, color=RED, alpha=0.08)
    ax.text(2.5, 57.5, '1→2、2→3 = 死亡区:一进二最难, 真正的绞肉机在低位 (不在高板)',
            ha='center', fontsize=11, color=RED, fontweight='bold')
    ax.set_ylim(0, 62)
    ax.set_ylabel('晋级成功率 (%)', fontsize=12)
    ax.set_title('图2 · 连板晋级率 —— 越低越难, 3 板以上反而稳 (约 183 交易日样本)',
                 fontsize=13.5, fontweight='bold', pad=12)
    ax.grid(axis='y', alpha=0.25)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    _save(fig, '2_promotion.png')


# ════════════════════════════════════════════════════════════
# 图4 · 三章 —— 退潮期晋级率崩塌 (量化退潮信号)
# ════════════════════════════════════════════════════════════
def fig_retreat():
    fig, ax = plt.subplots(figsize=(10, 5))
    labels = ['08-11→12', '08-13→14', '08-18→19', '08-19→20']
    vals = [56, 27, 17, 14]
    ax.plot(labels, vals, '-o', color=RED, lw=2.4, markersize=11,
            markeredgecolor='white', markeredgewidth=1.2)
    for x, v in zip(labels, vals):
        ax.text(x, v+2.5, f'{v}%', ha='center', fontsize=12, color=TXT, fontweight='bold')
    ax.axhline(30, color=YELLOW, lw=1.4, linestyle='--')
    ax.text(3.15, 31, '30% 退潮阈值', ha='right', color=YELLOW, fontsize=10, va='bottom')
    ax.axhspan(0, 30, color=RED, alpha=0.08)
    ax.annotate('连续跌破 30%\n= 退潮确认, 收手空仓', (2, 17), xytext=(1.1, 44),
                fontsize=11, color=RED, fontweight='bold',
                arrowprops=dict(arrowstyle='->', color=RED, lw=1.4))
    ax.set_ylim(0, 62)
    ax.set_ylabel('昨 2 板+ 次日晋级率 (%)', fontsize=12)
    ax.set_title('图3 · 退潮期梯队晋级率系统性崩塌 —— 56% → 14% 的滑梯',
                 fontsize=13.5, fontweight='bold', pad=12)
    ax.grid(axis='y', alpha=0.25)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    _save(fig, '3_retreat.png')


# ════════════════════════════════════════════════════════════
# 图5 · 四章 —— 情绪弧 & 崩溃日 (涨停腰斩 + 跌停井喷)
# ════════════════════════════════════════════════════════════
def fig_crash():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 5),
                                   gridspec_kw={'width_ratios': [1.5, 1]})
    # 左: 情绪弧 (采样点, 涨停 vs 跌停双线)
    days = ['08-11', '08-12', '08-17', '08-19', '08-25']
    zt = [58, 92, 106, 36, 65]
    dt = [1, 0, 1, 118, 2]
    ax1.plot(days, zt, '-o', color=GREEN, lw=2.2, markersize=8, label='涨停家数',
             markeredgecolor='white', markeredgewidth=1)
    ax1.plot(days, dt, '-o', color=RED, lw=2.2, markersize=8, label='跌停家数',
             markeredgecolor='white', markeredgewidth=1)
    for x, v in zip(days, zt):
        ax1.text(x, v+4, str(v), ha='center', fontsize=9, color=GREEN, fontweight='bold')
    for x, v in zip(days, dt):
        ax1.text(x, v+4, str(v), ha='center', fontsize=9, color=RED, fontweight='bold')
    ax1.axvspan(2.5, 3.5, color=RED, alpha=0.12)
    ax1.annotate('崩溃日 08-19\n涨停腰斩 + 跌停井喷', (3, 118), xytext=(0.05, 108),
                 fontsize=10.5, color=RED, fontweight='bold', ha='left',
                 arrowprops=dict(arrowstyle='->', color=RED, lw=1.4))
    ax1.set_ylabel('家数', fontsize=12)
    ax1.set_title('情绪弧 · 涨停 / 跌停双线', fontsize=13, fontweight='bold', pad=10)
    ax1.legend(loc='upper left', fontsize=10, framealpha=0.85, facecolor=PANEL,
               edgecolor=EDGE, labelcolor=TXT)
    ax1.grid(alpha=0.22)
    for s in ('top', 'right'):
        ax1.spines[s].set_visible(False)

    # 右: 08-19 崩溃日前后对比双柱
    x = [0, 1]
    prev = [79, 5]
    now = [36, 118]
    w = 0.36
    ax2.bar([i-w/2 for i in x], prev, w, color=MUTE, label='08-18 (前一日)')
    ax2.bar([i+w/2 for i in x], now, w, color=[GREEN, RED], label='08-19 (崩溃日)')
    for i, (p, n) in enumerate(zip(prev, now)):
        ax2.text(i-w/2, p+3, str(p), ha='center', fontsize=10, color=TXT)
        ax2.text(i+w/2, n+3, str(n), ha='center', fontsize=10, color=TXT, fontweight='bold')
    ax2.set_xticks(x)
    ax2.set_xticklabels(['涨停\n79→36 (-54%)', '跌停\n5→118 (×23.6)'], fontsize=10)
    ax2.set_title('崩溃日双重触发', fontsize=13, fontweight='bold', pad=10)
    ax2.grid(axis='y', alpha=0.22)
    for s in ('top', 'right'):
        ax2.spines[s].set_visible(False)

    fig.suptitle('图4 · 情绪何时逃命 —— 涨停腰斩 + 跌停井喷 + 高标一字 = 崩溃盖棺',
                 fontsize=14, fontweight='bold', color=YELLOW, y=1.0)
    _save(fig, '4_crash.png')


# ════════════════════════════════════════════════════════════
# 图6 · 五章 —— 主板 / 创业板跨板带领
# ════════════════════════════════════════════════════════════
def fig_crossboard():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.8),
                                   gridspec_kw={'width_ratios': [1, 1.3]})
    # 左: 板别分布饼图
    sizes = [15, 14, 4, 1]
    labels = ['沪主板 15', '深主板 14', '创业板 4', '北交所 1']
    cols = [BLUE, '#388bfd', ORANGE, MUTE]
    wedges, texts, autotexts = ax1.pie(
        sizes, labels=labels, colors=cols, autopct='%1.0f%%',
        startangle=90, textprops={'color': TXT, 'fontsize': 10},
        wedgeprops={'edgecolor': BG, 'linewidth': 2})
    for at in autotexts:
        at.set_color('white'); at.set_fontweight('bold'); at.set_fontsize(10)
    ax1.set_title('34 只盖章高标 · 板别分布 (主板占 85%)', fontsize=12,
                  fontweight='bold', pad=14, color=TXT)

    # 右: 创业板 4 只启动时序 vs 主板开闸
    d = lambda m, day: mdates.date2num(date(2026, m, day))
    ax2.axvline(d(7,15), color=BLUE, lw=2, linestyle='--')
    ax2.text(d(7,16), 4.6, '主板 07-15 开闸定方向', color=BLUE, fontsize=9.5,
             ha='left', va='center', fontweight='bold')
    cyb = [('五洲医疗', (7,22), 4), ('欣天科技', (8,4), 4), ('蓝盾光电', (8,10), 5), ('天山生物', (8,17), 2)]
    for i, (nm, (m, dd), h) in enumerate(cyb):
        x = d(m, dd); y = i+1
        big = nm == '蓝盾光电'
        ax2.scatter([x], [y], s=120+h*40, color=RED if big else ORANGE,
                    edgecolor='white', lw=1.1, zorder=3)
        ax2.text(x+0.6, y, f'{nm} ({h}板)' + ('  +180.9% 弹性最大' if big else ''),
                 va='center', fontsize=10, color=RED if big else TXT,
                 fontweight='bold' if big else 'normal')
    ax2.set_ylim(0.3, 5.2)
    ax2.set_yticks([])
    ax2.set_xlim(d(7,13), d(8,26))
    ax2.set_title('创业板高标全部晚启动 · 跟随补涨但弹性最大', fontsize=12,
                  fontweight='bold', pad=14, color=TXT)
    ax2.xaxis.set_major_locator(mdates.DayLocator(interval=5))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax2.grid(axis='x', alpha=0.2)
    for s in ('top', 'right', 'left'):
        ax2.spines[s].set_visible(False)

    fig.suptitle('图5 · 主板定方向、创业板放弹性 —— 主板先开闸, 创业板晚到但空间大 (20cm)',
                 fontsize=14, fontweight='bold', color=YELLOW, y=1.06)
    fig.subplots_adjust(top=0.80)
    _save(fig, '5_crossboard.png')


if __name__ == '__main__':
    print("生成龙头接替手册配图 →", OUT)
    fig_timeline()
    fig_succession()
    fig_promotion()
    fig_retreat()
    fig_crash()
    fig_crossboard()
    print("完成 6 张。")
