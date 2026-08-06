# -*- coding: utf-8 -*-
"""
量化择时信号 · 数据驱动 6 场景模型 (v2)

回溯基础: 历史样本仅由报告层按当前可用数据动态计算。
本模块只负责结构分类与仓位动作，不在规则文案中写死历史胜率。

核心因子 (按预测力排序):
  ① ad_ratio  = up / (up + down)       # 情绪水位 (最强单因子)
  ② h_drop    = prev_max_h - max_h     # 断板变化 (最强顶部信号)
  ③ ladder    = h3 + 2h4 + 3h5 + 4h6+  # 梯队完整度
 ④ 突破前压力位 (5日高) + 情绪+梯队联合
  ⑤ 涨停/跌停家数放缩

v2.1 反身性顶部保护:
  7/23 尾盘 A/D=0.78 触发 E_主升加速 建议 7-9 成, 7/24 情绪崩塌 A/D=0.17. 复盘:
  · A/D > 0.75 属于极端过热, 需要反身顶保护
  · 立新 6 板孤峰 + 5 板缺档 = 高低断代崩塌形态, 与 12/22 饱满型 E 本质不同
  ⇒ E 场景新增两道过滤: A/D > 0.75 或 (6+板存在 且 5板缺档) → 降级 C_高位分歧
"""
import pandas as pd


def _to_int(x, default=0):
    """将任意值安全转成 int, 失败返回 default."""
    if x is None:
        return default
    if isinstance(x, str):
        s = x.strip()
        if not s or not s.replace('-', '').isdigit():
            return default
        try:
            return int(s)
        except (ValueError, TypeError):
            return default
    try:
        return int(x)
    except (ValueError, TypeError):
        return default


def _compute_ad_ratio(advance_decline):
    """从 advance_decline 直接算 ad_ratio, 是最权威口径."""
    up = _to_int(advance_decline.get('up', 0))
    down = _to_int(advance_decline.get('down', 0))
    total = up + down
    if total < 1000:  # 家数残缺时保守回落 0.5
        return None, up, down
    return up / total, up, down


def _compute_ladder(echelon):
    """
    梯队分 = h3*1 + h4*2 + h5*3 + h6+*4
    输入 echelon 结构: list[dict], 每项含 'height' 键 (如 '3连板'/'首板')
    """
    if not echelon:
        return None, 0, 0, 0, 0
    counts = {3: 0, 4: 0, 5: 0, 6: 0}  # 6 = 6板及以上
    for item in echelon:
        h_str = str(item.get('height', ''))
        h = 0
        if '连板' in h_str:
            try:
                h = int(h_str.replace('连板', ''))
            except (ValueError, TypeError):
                h = 0
        elif h_str == '首板':
            h = 1
        if h >= 6:
            counts[6] += 1
        elif h in counts:
            counts[h] += 1
    ladder = counts[3] * 1 + counts[4] * 2 + counts[5] * 3 + counts[6] * 4
    return ladder, counts[3], counts[4], counts[5], counts[6]


def _get_5day_pressure(sentiment_df, prev_h):
    """
    从 sentiment_df 取最近 5 个交易日的空间板最大值作为压力位.
    生产环境: sentiment_df 已包含今天前的历史 (含昨日), tail(5) 就是过去 5 日.
    Fallback: 无 sentiment_df 时用 prev_h (至少保住 "突破昨日" 的最低语义).
    """
    if sentiment_df is None or sentiment_df.empty or '连板高度' not in sentiment_df.columns:
        return prev_h
    recent = sentiment_df['连板高度'].dropna().tail(5)
    if len(recent) == 0:
        return prev_h
    try:
        vals = [int(float(v)) for v in recent if pd.notnull(v)]
        return max(vals) if vals else prev_h
    except (ValueError, TypeError):
        return prev_h


def _classify_scene(curr_h, prev_h, ad, ladder, zt, dt, pressure_5d,
                    h3=0, h4=0, h5=0, h6p=0):
    """
    数据驱动 6 场景分类器. 返回 (scene_code, position_str, action, level, color, desc_tail, win_rate)
    优先级从高到低, 命中即返回.
    h3/h4/h5/h6p 用于识别梯队断代 (E 场景反身顶保护).
    """
    h_drop = prev_h - curr_h if prev_h > 0 else 0

    # ============================================================
    # D · 冰点抄底
    # ============================================================
    if ad is not None and ad < 0.20 and curr_h <= 4:
        return (
            'D_冰点抄底', '7-10成',
            '右侧确认 / 满仓试错',
            '强进攻 (冰点反转)',
            '#f85149',
            f'情绪冰点 (A/D={ad:.2f}, <0.20 罕见档), 杀跌动能枯竭. '
            f'等待止跌和前排确认后试错，样本结果见报告页动态统计.',
            None
        )

    # ============================================================
    # F · 顶部崩塌 (断板 ≥3板 + 跌停 >15)
    # ============================================================
    if h_drop >= 3 and dt > 15:
        return (
            'F_顶部崩塌', '1-2成',
            '强制清仓 / 现金为王',
            '极度危险 (顶部崩塌)',
            '#58a6ff',
            f'断板高度崩塌 ({prev_h}→{curr_h} 板, 断 {h_drop} 板) + 跌停 {dt} 家. '
            f'负反馈扩散风险高，先控制仓位，样本结果见报告页动态统计.',
            None
        )

    # ============================================================
    # A+ · 突破共振 (突破压力位 + 情绪强 + 梯队饱满)
    # 优先级高于 E 主升 —— 三因子命中即锁进攻,不被更宽泛的 E 抢占
    # 突破定义: 相对昨日抬升 AND 破 5 日压力位 (排除高位横盘伪突破)
    # ============================================================
    is_breakout = curr_h > prev_h and (pressure_5d <= 0 or curr_h > pressure_5d)
    if is_breakout and curr_h >= 5:
        # A+ 三因子共振
        if ad is not None and ad > 0.65 and ladder is not None and ladder >= 12:
            return (
                'A+_突破共振', '6-8成',
                '突破进攻 / 三因子共振',
                '强进攻 (真突破)',
                '#ff3232',
                f'突破 {pressure_5d}板压力 + 情绪强 (A/D={ad:.2f}) + 梯队饱满 ({ladder}分). '
                f'结构共振，可小仓确认，主仓底仓不动，样本结果见报告页动态统计.',
                None
            )
        # A 普通突破 - 结构确认不足, 减仓不加仓
        return (
            'A_突破陷阱', '3-4成',
            '突破日减仓 / 不追高',
            '警戒 (突破确认不足)',
            '#ff8800',
            f'空间破 {pressure_5d}板压力至 {curr_h}板，但情绪或梯队未同步确认. '
            f'突破日先观察承接，避免追高，等分歧转强再参与.',
            None
        )

    # ============================================================
    # E · 主升加速 (强而不稳)
    # v2.1 新增两道反身顶保护, 命中即降级为 C_高位分歧:
    #   (1) A/D > 0.75 属极端过热 (历史 E 均值仅 0.57), 反身顶特征
    #   (2) 6+板存在 且 5板缺档 (h6p>=1 and h5==0) = 高低断代崩塌形态
    # ============================================================
    if ad is not None and ad > 0.65 and curr_h >= 6 and (ladder is None or ladder >= 8):
        overheat = ad > 0.75
        broken_ladder = (h6p >= 1) and (h5 == 0)
        if overheat or broken_ladder:
            reasons = []
            if overheat:
                reasons.append(f'情绪极端过热 A/D={ad:.2f} (>0.75 保护阈值)')
            if broken_ladder:
                reasons.append(f'{curr_h}板孤峰+5板缺档 (梯队{h6p}/{h5}/{h4}/{h3}) 高低断代')
            return (
                'C_高位分歧', '3-5成',
                '兑现减仓 / 尾盘不追',
                '防守预警 (反身顶保护)',
                '#ff8800',
                '⚠️ 表面 E 主升, 但触发反身顶保护降级: ' + '; '.join(reasons) + '. '
                '该结构存在高低断代风险，尾盘建议减仓而非加仓. '
                '等 D 冰点信号 (A/D<0.20) 才是真买点.',
                None
            )
        return (
            'E_主升加速', '6-7成',
            '锁仓主升 / 尾盘不追',
            '强进攻 (高潮期,尾盘减半)',
            '#ff4444',
            f'情绪强 (A/D={ad:.2f}) + 空间 {curr_h}板 + 梯队饱满. '
            f'结构偏主升但处于高位，死拿龙一底仓，尾盘绝不加仓，盯 T+2 分歧兑现.',
            None
        )

    # ============================================================
    # C · 高位分歧 (max_h≥6 且情绪弱)
    # ============================================================
    if curr_h >= 6 and ad is not None and ad < 0.40:
        return (
            'C_高位分歧', '3-4成',
            '兑现减仓 / 高位不接',
            '防守预警 (高位分歧)',
            '#ff8800',
            f'空间 {curr_h}板悬高, 但情绪弱 (A/D={ad:.2f}). '
            f'前排逢高兑现，空仓等新主线，样本结果见报告页动态统计.',
            None
        )

    # ============================================================
    # B · 退潮蓄势
    # ============================================================
    if h_drop >= 1 and ad is not None and ad < 0.35 and zt < 80:
        return (
            'B_退潮蓄势', '4-5成',
            '底仓埋伏 / 等冰点',
            '中性偏进攻 (退潮蓄势)',
            '#d29922',
            f'空间断板 ({prev_h}→{curr_h}), 情绪弱 (A/D={ad:.2f}). '
            f'退潮阶段只保留底仓，等待下一轮冰点和前排确认.',
            None
        )

    # ============================================================
    # 默认 · 中性震荡 (保底 5 成)
    # ============================================================
    ad_str = f'A/D={ad:.2f}' if ad is not None else 'A/D未就位'
    return (
        '中性震荡', '5成',
        '结构博弈 / 主线为纲',
        '中性',
        '#d29922',
        f'{ad_str}, 空间 {curr_h}板. 无极端信号, 聚焦主线核心, 后排不接.',
        None
    )


def generate_timing_signal(sentiment_df, advance_decline, echelon=None):
    """
    数据驱动的量化择时信号 (v2, 6 场景模型).

    Parameters
    ----------
    sentiment_df : pd.DataFrame
        必需列: '连板高度', 可选 'zt'/'涨停数'
        用于取 5 日压力位 + 昨日高度/涨停数
    advance_decline : dict
        必需键: up, down, zt, dt, zt_max_height, zt_max_height_prev, zt_prev
    echelon : list[dict] | None
        可选, 每项含 'height' 键. 提供后启用梯队分, 让 A+ 三因子共振能触发.

    Returns
    -------
    dict:
        action / level / color / desc / position / scene / win_rate
        (向后兼容原 4 键 + 新增 3 键)
    """
    # 兜底: 数据为空
    if not advance_decline:
        return {
            'action': '数据未就位',
            'level': '中性',
            'color': '#d29922',
            'desc': 'A/D 家数或高度未就位, 无法量化. 保持 5 成基准仓位.',
            'position': '5成',
            'scene': 'N/A',
            'win_rate': None
        }

    try:
        # 因子提取
        curr_h = _to_int(advance_decline.get('zt_max_height', 0))
        prev_h = _to_int(advance_decline.get('zt_max_height_prev', 0))
        zt = _to_int(advance_decline.get('zt', 0))
        dt = _to_int(advance_decline.get('dt', 0))
        ad, up, down = _compute_ad_ratio(advance_decline)
        ladder, h3, h4, h5, h6p = _compute_ladder(echelon)
        pressure_5d = _get_5day_pressure(sentiment_df, prev_h)

        # 分类
        scene, position, action, level, color, desc_tail, win_rate = _classify_scene(
            curr_h, prev_h, ad, ladder, zt, dt, pressure_5d,
            h3=h3, h4=h4, h5=h5, h6p=h6p,
        )

        # 因子摘要
        factor_line = (
            f"[因子] 空间{curr_h}板(压力{pressure_5d}板) | "
            f"梯队{ladder}分" + (f" (3/{h3} 4/{h4} 5/{h5} 6+/{h6p})" if ladder is not None else "(无echelon)") +
            f" | 涨停{zt}/跌停{dt} | 昨断{prev_h - curr_h:+d}板."
        )

        return {
            'action': action,
            'level': level,
            'color': color,
            'desc': f"{desc_tail} {factor_line}",
            'position': position + '仓位',
            'scene': scene,
            'win_rate': win_rate
        }
    except Exception as e:
        print(f"择时模块异常: {e}")
        return {
            'action': '震荡应对 / 结构性博弈',
            'level': '中性',
            'color': '#d29922',
            'desc': f'择时模块异常回落基准: {e}',
            'position': '5成仓位',
            'scene': 'ERROR',
            'win_rate': None
        }
