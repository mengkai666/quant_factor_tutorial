"""市场择时档位 (Market Stance) — 顺指数/逆指数决策 + 转向扳机清单。

数据驱动、可回溯、不含个股。复用主文件已有的 advance_decline / sentiment_df / echelon。
把广度三信号 (A/D 情绪 + 连板梯队 + 跌停承接) 合成一个档位:
  进攻(顺指数) / 防御(逆指数) / 观望
并输出"距离转向还差哪几条"的扳机清单 —— 这是"提前判断"的落地: 不预测涨跌, 只预设扳机。

调判断口径只改下面模块级常量。
"""
import re
import pandas as pd

# ── 阈值 (与主文件 REBOUND_* 口径对齐) ──
STANCE_ATTACK_AD = 1.5      # 进攻档: A/D >= 此值 (且抬升)
STANCE_DEFEND_AD = 0.85     # 防御档: A/D < 此值
STANCE_GAP_MAX_H = 5        # 高度断层: 最高板 >= 此值
STANCE_GAP_MISS = 2         # 且中间缺 >= 此档数
STANCE_DT_SHRINK = 0.5      # 跌停"大幅萎缩": 今日 < 昨日 * 此比例
STANCE_TURN_AD = 1.05       # 转攻确认: A/D 站回此值 (需连续 2 日)


def _heights_from_echelon(echelon):
    """echelon -> 已有连板高度集合 (首板记 1)。"""
    hs = []
    for e in (echelon or []):
        h = str(e.get('height', ''))
        if '首板' in h:
            hs.append(1)
        else:
            m = re.search(r'(\d+)', h)
            if m:
                hs.append(int(m.group(1)))
    return sorted(set(hs), reverse=True)


def _ad_series(sentiment_df, n=3):
    """近 n 日 up/down 比值序列 (最旧->最新)。"""
    if sentiment_df is None or sentiment_df.empty or 'up' not in sentiment_df.columns:
        return []
    up = pd.to_numeric(sentiment_df['up'], errors='coerce').fillna(0).tolist()[-n:]
    dn = pd.to_numeric(sentiment_df['down'], errors='coerce').fillna(0).tolist()[-n:]
    return [round(u / max(d, 1), 2) for u, d in zip(up, dn)]


def _dt_series(sentiment_df, n=2):
    if sentiment_df is None or sentiment_df.empty or 'dt' not in sentiment_df.columns:
        return []
    return [int(x) for x in pd.to_numeric(sentiment_df['dt'], errors='coerce').fillna(0).tolist()[-n:]]


def classify_market_stance(advance_decline, sentiment_df, echelon, regime=None):
    """三信号合成档位 + 转向扳机清单。返回 dict。"""
    up = float(advance_decline.get('up', 0) or 0)
    down = float(advance_decline.get('down', 0) or 0)
    ad = round(up / max(down, 1), 2)
    zt = int(advance_decline.get('zt', 0) or 0)
    dt = int(advance_decline.get('dt', 0) or 0)

    ads = _ad_series(sentiment_df, 3)
    ad_rising = len(ads) >= 3 and ads[-1] > ads[-2] > ads[-3]
    ad_falling_streak = len(ads) >= 3 and all(r < 1 for r in ads)

    dts = _dt_series(sentiment_df, 2)
    dt_prev = dts[-2] if len(dts) >= 2 else dt
    dt_shrink = dt_prev > 0 and dt < dt_prev * STANCE_DT_SHRINK

    heights = _heights_from_echelon(echelon)
    max_h = heights[0] if heights else 0
    present = set(heights)
    missing = [h for h in range(1, max_h) if h not in present]
    gap = max_h >= STANCE_GAP_MAX_H and len(missing) >= STANCE_GAP_MISS
    healthy_echelon = max_h >= 3 and not gap

    # ── 档位判定 ──
    if ad >= STANCE_ATTACK_AD and ad_rising and healthy_echelon and dt <= zt:
        stance, clr = '进攻档 · 顺指数', '#f85149'
        head = f'A/D {ad} 强且抬升 + 梯队健康(最高{max_h}板) + 跌停可控, 风险偏好回升。'
        play = '可超配高 beta(科技成长), 防御品种低配。方向由环境定, 个股只负责择时。'
    elif ad < STANCE_DEFEND_AD or gap or (dt > zt * 2):
        stance, clr = '防御档 · 逆指数', '#58a6ff'
        why = []
        if ad < STANCE_DEFEND_AD:
            why.append(f'A/D {ad} 弱')
        if ad_falling_streak:
            why.append(f'近{len(ads)}日比值均<1 (持续退潮)')
        if gap:
            why.append(f'高度断层(最高{max_h}板中间缺{len(missing)}档)')
        if dt > zt * 2:
            why.append(f'跌停{dt} vs 涨停{zt} (承接崩塌)')
        head = ' + '.join(why) + '。'
        play = '防御为主、控回撤。避险抱团(医药类)可作对冲工具, 但属防御非转势, 情绪一好反要警惕。科技反抽按诱多处理。'
    else:
        stance, clr = '观望档 · 轻仓', '#d29922'
        head = f'A/D {ad} 处分歧区、梯队半死不活, 信号不干净。'
        play = '不押方向, 空仓等破位。此档最易两头挨打, 宁可等。'

    if regime:
        stance = regime.get('title') or stance
        clr = regime.get('color') or clr
        head = regime.get('reason') or head
        play = regime.get('action') or play

    # ── 转向扳机清单 (提前判断: 预设扳机, 非预测) ──
    triggers = [
        {'name': '扳机① 继续恶化 → 防御加码',
         'cond': f'A/D<0.5 或跌停不缩(仍>{max(dt // 2, 50)})',
         'hit': ad < 0.5 or (dt_prev > 0 and dt >= dt_prev)},
        {'name': '扳机② 企稳 → 转观望备战',
         'cond': f'跌停较昨大幅萎缩(<{int(dt_prev * STANCE_DT_SHRINK)}) 且 A/D 回{STANCE_DEFEND_AD}~{STANCE_TURN_AD}',
         'hit': dt_shrink and STANCE_DEFEND_AD <= ad < STANCE_ATTACK_AD},
        {'name': '扳机③ 右侧转攻 → 才谈顺指数',
         'cond': f'A/D 连续2日≥{STANCE_TURN_AD} + 梯队≥3板不断档 + 科技主线重聚',
         'hit': len(ads) >= 2 and ads[-1] >= STANCE_TURN_AD and ads[-2] >= STANCE_TURN_AD and healthy_echelon},
    ]

    return {
        'stance': stance, 'color': clr, 'head': head, 'play': play,
        'ad': ad, 'ad_series': ads, 'dt': dt, 'zt': zt, 'max_h': max_h,
        'triggers': triggers,
    }


def render_stance_html(res):
    """档位卡片 + 扳机清单。深色风格, 与报告一致。"""
    if not res:
        return ''
    trig_rows = ''
    for t in res['triggers']:
        mark = '✅ 已触发' if t['hit'] else '⬜ 未满足'
        mclr = res['color'] if t['hit'] else '#8b949e'
        trig_rows += (
            f'<tr>'
            f'<td style="padding:8px 12px;color:#e6edf3;font-weight:bold;">{t["name"]}</td>'
            f'<td style="padding:8px 12px;color:#8b949e;font-size:13px;">{t["cond"]}</td>'
            f'<td style="padding:8px 12px;color:{mclr};font-weight:bold;white-space:nowrap;">{mark}</td>'
            f'</tr>'
        )
    ad_seq = ' → '.join(str(x) for x in res['ad_series']) if res['ad_series'] else '—'
    return f'''
    <div style="background:rgba(0,0,0,0.5);border:2px solid {res['color']};border-radius:12px;padding:20px;margin-bottom:30px;box-shadow:0 0 15px {res['color']}40;">
        <div style="color:#8b949e;font-size:13px;font-weight:bold;text-transform:uppercase;margin-bottom:6px;">📐 择时档位 · 顺逆指数决策 (Market Stance)</div>
        <div style="font-size:26px;font-weight:800;color:{res['color']};margin-bottom:8px;">{res['stance']}</div>
        <div style="color:#e6edf3;font-size:14px;margin-bottom:6px;">{res['head']}</div>
        <div style="color:#e6edf3;font-size:14px;margin-bottom:14px;"><b>操作:</b> {res['play']}</div>
        <div style="color:#8b949e;font-size:12px;margin-bottom:10px;">近3日 A/D 比值: {ad_seq}　|　今日 涨停{res['zt']}/跌停{res['dt']}　|　最高{res['max_h']}板</div>
        <div style="color:#8b949e;font-size:13px;font-weight:bold;margin-bottom:6px;">🎯 转向扳机 (提前判断: 哪个先亮看哪个, 缺一条都只算观望):</div>
        <table style="width:100%;border-collapse:collapse;background:rgba(255,255,255,0.02);border-radius:8px;overflow:hidden;">
            {trig_rows}
        </table>
    </div>
    '''
