# -*- coding: utf-8 -*-
"""每日复盘决策看板生成器 (v1, 数据驱动 6 场景版)

从盘面数据 + timing_signal 分类结果, 一键渲染出人眼可读的策略看板。
本模块只做视图, 不做业务计算; 所有数值来自调用方注入的 ctx dict。

调用位置: src/主线强度追踪.py::main() 内, publish 前后。
产出:     output/复盘决策看板_YYYY-MM-DD.html (单文件、无外部依赖)

ctx 字段约定 (缺失字段自动降级为 '—'):
  date_str        : 'YYYY-MM-DD'  报告口径日期
  scene           : 'E_主升加速'   timing_signal 场景码
  action          : '锁仓主升 / 去弱留强'
  level           : '强进攻 (高潮期)'
  color           : '#ff4444'      场景主色
  position        : '7-9成仓位'
  win_rate        : 0.71           历史 T+3 破新高胜率
  desc            : 场景一句话说明
  # 三因子
  curr_h          : 6              空间板
  prev_h          : 5              昨日空间板
  pressure_5d     : 5              5日压力位
  zt              : 128            涨停家数
  dt              : 8              跌停家数
  ad_ratio        : 0.778          A/D 比
  ladder          : 10             梯队分
  h3/h4/h5/h6p    : int            各高度家数
  # 决策 4 情形 (T+1 情形树)
  scenarios       : list[dict]     可选, 默认套用模板
  # 历史同型样本 (用于底部对照表)
  history_cases   : list[dict]     可选, 默认为空
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any


def build_dashboard_ctx(timing=None, advance_decline=None, sentiment_df=None,
                        echelon=None, report_date=None, focus_df=None) -> dict:
    """从 timing + 盘面数据组装看板 ctx (供 generate_dashboard_* 使用).

    把因子提取逻辑集中在这里, 避免 main 与 generate_html 两处重复构造.
    任何字段缺失都安全降级, 不抛异常.
    """
    timing = timing or {}
    advance_decline = advance_decline or {}
    try:
        from timing_signal import (
            _compute_ad_ratio, _compute_ladder, _get_5day_pressure, _to_int,
        )
    except Exception:
        # 极端情况: timing_signal 不可用, 用本地兜底
        def _to_int(x, default=0):
            try:
                return int(x)
            except Exception:
                return default

        def _compute_ad_ratio(ad):
            up = _to_int(ad.get('up', 0)); dn = _to_int(ad.get('down', 0))
            tot = up + dn
            return (up / tot if tot >= 1000 else None), up, dn

        def _compute_ladder(ech):
            return None, 0, 0, 0, 0

        def _get_5day_pressure(sdf, prev_h):
            return prev_h

    curr_h = _to_int(advance_decline.get('zt_max_height', 0))
    prev_h = _to_int(advance_decline.get('zt_max_height_prev', 0))
    zt = _to_int(advance_decline.get('zt', 0))
    dt = _to_int(advance_decline.get('dt', 0))
    zt_prev = _to_int(advance_decline.get('zt_prev', 0))
    ad_val, _up, _dn = _compute_ad_ratio(advance_decline)
    ladder, h3, h4, h5, h6p = _compute_ladder(echelon)
    pressure = _get_5day_pressure(sentiment_df, prev_h if prev_h > 0 else curr_h)

    date_key = str(report_date) if report_date else datetime.now().strftime('%Y-%m-%d')
    if len(date_key) == 8 and date_key.isdigit():
        date_key = f'{date_key[:4]}-{date_key[4:6]}-{date_key[6:]}'

    return {
        'date_str': date_key,
        'scene': timing.get('scene'),
        'action': timing.get('action'),
        'level': timing.get('level'),
        'color': timing.get('color'),
        'position': timing.get('position'),
        'win_rate': timing.get('win_rate'),
        'desc': timing.get('desc'),
        'curr_h': curr_h, 'prev_h': prev_h, 'pressure_5d': pressure,
        'zt': zt, 'dt': dt, 'zt_prev': zt_prev,
        'ad_ratio': ad_val,
        'ladder': ladder, 'h3': h3, 'h4': h4, 'h5': h5, 'h6p': h6p,
        'focus_df': focus_df,
    }


def _fmt(v: Any, default: str = '—') -> str:
    if v is None:
        return default
    if isinstance(v, float):
        return f'{v:.2f}'
    return str(v)


def _win_rate_color(wr: float | None) -> str:
    if wr is None:
        return '#8b949e'
    if wr >= 0.65:
        return '#3fb950'
    if wr >= 0.45:
        return '#d29922'
    return '#ff4444'


def _split_focus_pool(focus_df) -> dict:
    """把 focus_pool DataFrame 按'策略池'字段拆成三桶, 便于挂到 4 情形.
    返回: {'space': [...], 'core': [...], 'midcore': [...]}
      space: 高连板追打 (空间博弈池 + 主升接力池, 用于 A/B/C)
      midcore: 中军低吸 (核心中军低吸池, 用于 D 或 C 换车)
    每个元素形如 {'name': '爱丽家居', 'plate': '并购重组50%', 'cond': '...', 'stop': '...'}
    """
    buckets = {'space': [], 'midcore': []}
    if focus_df is None:
        return buckets
    try:
        if hasattr(focus_df, 'empty') and focus_df.empty:
            return buckets
    except Exception:
        return buckets
    try:
        for _, row in focus_df.iterrows():
            pool = str(row.get('策略池', ''))
            entry = {
                'name': str(row.get('股票', '')).strip(),
                'plate': str(row.get('板块', '')).strip(),
                'cond':  str(row.get('入场条件', '')).strip(),
                'stop':  str(row.get('防守位', '')).strip(),
            }
            if not entry['name']:
                continue
            if '空间博弈' in pool or '主升接力' in pool:
                buckets['space'].append(entry)
            elif '中军' in pool or '低吸' in pool:
                buckets['midcore'].append(entry)
    except Exception:
        pass
    return buckets


def _clean_plate(plate: str) -> str:
    """把 focus_pool 的'板块'字段清洗成可读题材名, 无意义时返回空串.
    - '并购重组50%' / 'IDC电力设备50%' -> 去掉尾部的"数字+%"投票占比 -> '并购重组' / 'IDC电力设备'
    - '/' / '' -> 空串 (占位符, 不显示)
    - '10日' / '5日' -> 空串 (中军池这里存的是 N日窗口, 不是真题材)
    """
    p = (plate or '').strip()
    if not p or p == '/':
        return ''
    # 中军低吸池的"板块"字段实为 N日窗口 (如 '10日'), 非真题材
    if re.fullmatch(r'\d+日', p):
        return ''
    # 只取第一个题材 (可能逗号分隔), 去掉尾部的"数字+%"投票占比
    p = p.split(',')[0].strip()
    p = re.sub(r'\d+%$', '', p).strip()
    return p


def _fmt_stock(entry: dict) -> str:
    """一只票的紧凑话术: '爱丽家居 (并购重组)'; 板块无意义时只显示股名."""
    name = entry.get('name', '')
    plate_short = _clean_plate(entry.get('plate', ''))
    if plate_short:
        return f'{name} ({plate_short})'
    return name


def _render_focus_table(buckets: dict) -> str:
    """把 focus_pool 拆好的 space/midcore 两桶渲染成两张表 (独立看板用).

    空表则整个 section 省略, 避免占版面显示空表。
    """
    space = buckets.get('space', [])
    midcore = buckets.get('midcore', [])
    if not space and not midcore:
        return ''

    def _row(entry: dict) -> str:
        return (
            f'<tr>'
            f'<td class="fp-name"><b>{_esc(entry.get("name", ""))}</b></td>'
            f'<td class="fp-plate">{_esc(_clean_plate(entry.get("plate", "")) or "—")}</td>'
            f'<td class="fp-entry">{_esc(entry.get("cond", ""))}</td>'
            f'<td class="fp-stop">{_esc(entry.get("stop", ""))}</td>'
            f'</tr>'
        )

    parts = []
    if space:
        space_rows = ''.join(_row(x) for x in space)
        parts.append(f'''
        <div class="fp-block fp-space">
          <div class="fp-block-title">🚀 空间博弈 / 主升接力池 · {len(space)} 只
            <span class="fp-block-sub">高连板追打 · 对应 A / B / C 场景</span></div>
          <table class="fp-table"><thead><tr>
            <th>标的</th><th>主线</th><th>入场条件</th><th>防守位</th>
          </tr></thead><tbody>{space_rows}</tbody></table>
        </div>''')
    if midcore:
        mid_rows = ''.join(_row(x) for x in midcore)
        parts.append(f'''
        <div class="fp-block fp-midcore">
          <div class="fp-block-title">🛡️ 核心中军低吸池 · {len(midcore)} 只
            <span class="fp-block-sub">深蹲抄底 · 对应 D 场景或 C 场景换车备胎</span></div>
          <table class="fp-table"><thead><tr>
            <th>标的</th><th>周期</th><th>入场条件</th><th>防守位</th>
          </tr></thead><tbody>{mid_rows}</tbody></table>
        </div>''')

    return f'''
    <div class="section-title">明日核心股票池 · 逐只落地</div>
    <div class="fp-wrap">{''.join(parts)}</div>'''


def _default_scenarios(curr_h: int, prev_h: int, focus_df=None) -> list[dict]:
    """T+1 4 情形树的默认模板. 从 focus_df 挂具体标的:
    - A (双龙一字): 空间池最强 2 只锁仓
    - B (空间一字+接力分歧): 换车 space 池第 2-3 只
    - C (高开分歧+二三进阶): space 池首选 + midcore 池 1 只
    - D (龙头炸板): midcore 池深蹲 + 前排清仓
    focus_df 为空时话术自动降级为主线级别 (不 hardcode 具体股名).
    """
    buckets = _split_focus_pool(focus_df)
    space = buckets['space']
    midcore = buckets['midcore']

    # A: 前 2 只空间强势股锁仓
    if space[:2]:
        a_head = space[0]
        a_items = [
            f'前排持仓不动 · 锁仓核心: {_fmt_stock(a_head)}',
            f'盯 {curr_h - 1}板梯队是否秒板 → 主线延续' if curr_h > 1 else '盯高度是否新高',
            '盘中不追后排 (主升诱多)',
            '目标: 分歧日再兑现',
        ]
    else:
        a_items = ['前排持仓不动 / 场内锁仓', '盘中不追后排 (主升诱多)',
                   f'盯 {curr_h - 1}板梯队是否秒板 → 主线延续',
                   '目标: 分歧日再兑现']

    # B: 换车到空间池第 2-3 只 (次强梯队, 低吸接力)
    if len(space) >= 2:
        switch_targets = ', '.join(_fmt_stock(x) for x in space[1:3])
        b_items = [
            '前排减仓 30-50% · 兑现主升',
            f'低吸换车: {switch_targets}',
            '不追高位孤峰 (胜率 <40%)',
            '警惕孤峰塌陷',
        ]
    else:
        b_items = ['前排减仓 30-50%', '换车次高梯队低吸',
                   '不接高位孤峰回封', '警惕孤峰塌陷']

    # C: 空间池首选 + midcore 池 1 只 (三因子共振买点)
    c_items = ['★ 三因子共振时才启动 (A/D>0.65 + 梯队≥12 + 破压)']
    if space[:1]:
        c_items.append(f'加仓接力核心: {_fmt_stock(space[0])}')
    if midcore[:1]:
        c_items.append(f'低吸预备: {_fmt_stock(midcore[0])}')
    else:
        c_items.append('主线中军低吸做备胎')
    c_items.append('目标周期 3-5 天')

    # D: 前排清仓 + midcore 深蹲 (只留防守低吸)
    d_items = ['F 顶部崩塌预警 (断板 ≥ 3 + 跌停 >15)', '立即清仓前排 · 不抄任何高位股']
    if midcore[:2]:
        deep_targets = ', '.join(_fmt_stock(x) for x in midcore[:2])
        d_items.append(f'仅留深蹲低吸: {deep_targets}')
    else:
        d_items.append('全线离场观望')

    return [
        {'kind': 'attack', 'name': 'A · 双龙一字', 'prob': '概率 20%',
         'items': a_items, 'pos': '仓位 · 7-8 成'},
        {'kind': 'moderate', 'name': 'B · 空间一字 + 接力分歧', 'prob': '概率 30%',
         'items': b_items, 'pos': '仓位 · 4-5 成'},
        {'kind': 'attack', 'name': 'C · 高开分歧 + 二三进阶', 'prob': '概率 25%',
         'items': c_items, 'pos': '仓位 · 8-9 成 ⭐'},
        {'kind': 'defense', 'name': 'D · 龙头炸板', 'prob': '概率 25%',
         'items': d_items, 'pos': '仓位 · 1-2 成'},
    ]


def _esc(s: Any) -> str:
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _factor_row(name: str, value: str, ok: str, hint: str) -> str:
    icon_class = {'✅': 'check-ok', '❌': 'check-fail', '❓': 'check-warn', '⚠️': 'check-warn'}.get(
        ok[:1] if ok else '', 'check-warn')
    return (f'<tr><td>{_esc(name)}</td><td><b>{_esc(value)}</b></td>'
            f'<td><span class="{icon_class}">{_esc(ok)}</span></td>'
            f'<td>{_esc(hint)}</td></tr>')


def _scen_card(s: dict) -> str:
    items = ''.join(f'<li>{_esc(x)}</li>' for x in s.get('items', []))
    return (f'<div class="scen-card {s.get("kind", "moderate")}">'
            f'<div class="head"><span class="name">{_esc(s.get("name", ""))}</span>'
            f'<span class="prob">{_esc(s.get("prob", ""))}</span></div>'
            f'<ul>{items}</ul>'
            f'<div class="pos">{_esc(s.get("pos", ""))}</div>'
            f'</div>')


def _history_row(c: dict) -> str:
    result = c.get('result', '')
    cls = 'win' if '⭐' in result or '✓' in result else ('lose' if '❌' in result else 'neutral')
    return (f'<tr class="{cls}"><td>{_esc(c.get("date"))}</td>'
            f'<td>{_esc(c.get("curr_h"))}板</td>'
            f'<td>{_esc(c.get("zt"))}</td>'
            f'<td>{_esc(c.get("ladder"))}</td>'
            f'<td>{_esc(c.get("t1"))}</td>'
            f'<td>{_esc(c.get("t2"))}</td>'
            f'<td>{_esc(c.get("t3"))}</td>'
            f'<td>{_esc(result)}</td></tr>')


def generate_dashboard_html(ctx: dict) -> str:
    """把 ctx 渲染成一份完整的看板 HTML (single-file, 无外部依赖)."""
    # 数据取值 + 兜底
    date_str = ctx.get('date_str') or datetime.now().strftime('%Y-%m-%d')
    scene = ctx.get('scene', '中性震荡')
    action = ctx.get('action', '结构博弈 / 主线为纲')
    level = ctx.get('level', '中性')
    color = ctx.get('color', '#d29922')
    position = ctx.get('position', '5成仓位')
    win_rate = ctx.get('win_rate')
    desc = ctx.get('desc', '')

    curr_h = int(ctx.get('curr_h', 0) or 0)
    prev_h = int(ctx.get('prev_h', 0) or 0)
    pressure_5d = int(ctx.get('pressure_5d', 0) or 0)
    zt = int(ctx.get('zt', 0) or 0)
    dt = int(ctx.get('dt', 0) or 0)
    ad = ctx.get('ad_ratio')
    ad_str = f'{ad:.3f}' if isinstance(ad, (int, float)) else '未取到'
    ladder = ctx.get('ladder')
    h3 = ctx.get('h3', 0)
    h4 = ctx.get('h4', 0)
    h5 = ctx.get('h5', 0)
    h6p = ctx.get('h6p', 0)

    zt_prev = int(ctx.get('zt_prev', 0) or 0)
    zt_boom = (zt / zt_prev) if zt_prev > 0 else None
    focus_df = ctx.get('focus_df')

    scenarios = ctx.get('scenarios') or _default_scenarios(curr_h, prev_h, focus_df=focus_df)
    history_cases = ctx.get('history_cases') or []

    # 三因子交叉表
    ap_ad_ok = '✅ 达标' if isinstance(ad, (int, float)) and ad > 0.65 else (
        '⚠️ 中档' if isinstance(ad, (int, float)) and ad >= 0.5 else '❌ 未达')
    ap_ladder_ok = '✅ 达标' if isinstance(ladder, (int, float)) and ladder >= 12 else '❌ 未达'
    breakout_ok = '✅ 达标' if curr_h > pressure_5d else '❌ 未达'
    factor_rows = ''.join([
        _factor_row('① 空间板突破 5 日压力',
                    f'{curr_h}板 vs 前压力 {pressure_5d}板', breakout_ok,
                    f'昨断 {curr_h - prev_h:+d} 板'),
        _factor_row('② 情绪 A/D > 0.65 (A+ 门槛)',
                    ad_str, ap_ad_ok,
                    f'涨停 {zt}, 跌停 {dt}'),
        _factor_row('③ 梯队分 ≥ 12 (A+ 门槛)',
                    f'{ladder}分' if ladder is not None else '—', ap_ladder_ok,
                    f'3板 {h3} / 4板 {h4} / 5板 {h5} / 6+板 {h6p}'),
    ])

    scen_cards = ''.join(_scen_card(s) for s in scenarios)
    hist_rows = ''.join(_history_row(c) for c in history_cases) or (
        '<tr><td colspan="8" style="color:#6e7681;padding:20px;">暂无历史同型样本 (需累计更多回测)</td></tr>')

    # 明日核心股票池表 (从 focus_df 拆桶后逐只列出)
    focus_buckets = _split_focus_pool(focus_df)
    focus_rows_html = _render_focus_table(focus_buckets)

    wr_color = _win_rate_color(win_rate)
    wr_str = f'{win_rate * 100:.0f}%' if isinstance(win_rate, (int, float)) else '—'

    # KPI 板块
    boom_str = f'×{zt_boom:.2f}' if zt_boom else '—'
    kpi_html = f'''
    <div class="grid">
      <div class="card">
        <h3>空间板</h3>
        <div class="kpi red">{curr_h}板</div>
        <div class="hint">前 5 日压力位 {pressure_5d}板 · 昨 {prev_h}板 ({curr_h - prev_h:+d})</div>
      </div>
      <div class="card">
        <h3>涨停家数</h3>
        <div class="kpi red">{zt}</div>
        <div class="hint">昨日 {zt_prev} · 环比 {boom_str}</div>
      </div>
      <div class="card">
        <h3>跌停家数</h3>
        <div class="kpi green">{dt}</div>
        <div class="hint">亏钱效应阈值: > 15 转防守</div>
      </div>
      <div class="card">
        <h3>情绪 A/D</h3>
        <div class="kpi yellow">{ad_str}</div>
        <div class="hint">A+ 门槛 &gt; 0.65 · E 主升 &gt; 0.65</div>
      </div>
      <div class="card">
        <h3>梯队分</h3>
        <div class="kpi blue">{_fmt(ladder)}</div>
        <div class="hint">h3×1 + h4×2 + h5×3 + h6+×4</div>
      </div>
      <div class="card">
        <h3>历史胜率 (T+3 破新高)</h3>
        <div class="kpi" style="color:{wr_color};">{wr_str}</div>
        <div class="hint">{_esc(level)}</div>
      </div>
    </div>'''

    css = '''
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      background: #0d1117; color: #e6edf3;
      font-family: 'Microsoft YaHei', 'PingFang SC', -apple-system, sans-serif;
      padding: 24px; line-height: 1.6; min-height: 100vh;
    }
    .wrap { max-width: 1200px; margin: 0 auto; }
    .back {
      display: inline-block; padding: 6px 14px; margin-bottom: 12px;
      background: #161b22; color: #58a6ff; text-decoration: none;
      border: 1px solid #30363d; border-radius: 6px; font-size: 13px;
    }
    .back:hover { border-color: #58a6ff; }
    .hero {
      background: linear-gradient(135deg, color-mix(in srgb, var(--sc) 20%, transparent),
                                       color-mix(in srgb, var(--sc) 8%, transparent));
      border: 2px solid var(--sc); border-radius: 16px;
      padding: 24px 28px; margin-bottom: 20px;
      display: flex; align-items: center; justify-content: space-between;
      gap: 20px; flex-wrap: wrap;
      box-shadow: 0 0 30px color-mix(in srgb, var(--sc) 22%, transparent);
    }
    .hero .left h1 { font-size: 28px; font-weight: 800; color: var(--sc); margin-bottom: 6px; }
    .hero .left .sub { color: #c9d1d9; font-size: 14px; }
    .hero .left .date { color: #8b949e; font-size: 13px; margin-top: 4px; }
    .hero .right {
      text-align: right;
      background: color-mix(in srgb, var(--sc) 22%, transparent);
      padding: 14px 22px; border-radius: 10px;
      border: 1px solid var(--sc);
    }
    .hero .right .pos-label { color: color-mix(in srgb, var(--sc) 70%, #ffffff);
                              font-size: 13px; margin-bottom: 4px; }
    .hero .right .pos-value { font-size: 24px; font-weight: 800; color: #fff; }
    .hero .right .win { color: #ffcc00; font-size: 13px; margin-top: 4px; }
    .hero-desc {
      grid-column: 1 / -1; color: #c9d1d9; font-size: 14px;
      padding-top: 12px; margin-top: 12px;
      border-top: 1px solid color-mix(in srgb, var(--sc) 35%, transparent);
      width: 100%;
    }
    .grid {
      display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px; margin-bottom: 24px;
    }
    .card {
      background: rgba(22, 27, 34, 0.7);
      border: 1px solid rgba(48, 54, 61, 0.8);
      border-radius: 12px; padding: 16px 18px;
    }
    .card h3 {
      font-size: 13px; color: #d29922; margin-bottom: 8px;
      border-left: 3px solid #d29922; padding-left: 8px;
    }
    .kpi { font-size: 24px; font-weight: 800; color: #fff; }
    .kpi.green { color: #3fb950; }
    .kpi.red { color: #ff4444; }
    .kpi.yellow { color: #ffcc00; }
    .kpi.blue { color: #58a6ff; }
    .hint { color: #8b949e; font-size: 12px; margin-top: 4px; }

    .section-title {
      font-size: 18px; font-weight: 700; color: #ffcc00;
      margin: 28px 0 14px; padding-left: 10px;
      border-left: 4px solid #ffcc00;
    }
    table.factor, table.history {
      width: 100%; background: rgba(22, 27, 34, 0.7);
      border-radius: 12px; border-collapse: separate;
      border-spacing: 0; overflow: hidden; margin-bottom: 8px;
    }
    table.factor th, table.factor td,
    table.history th, table.history td {
      padding: 11px 14px; border-bottom: 1px solid rgba(48, 54, 61, 0.6);
      text-align: left; font-size: 13.5px;
    }
    table.factor th, table.history th {
      background: rgba(30, 35, 42, 0.9); color: #8b949e;
      font-size: 12px; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    table.history td { text-align: center; }
    table.history tr.win td { color: #3fb950; }
    table.history tr.lose td { color: #ff6666; }
    table.history tr.neutral td { color: #d29922; }
    .check-ok { color: #3fb950; font-weight: 700; }
    .check-fail { color: #ff4444; font-weight: 700; }
    .check-warn { color: #ffcc00; font-weight: 700; }

    .scenario-tree {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 14px;
    }
    .scen-card {
      background: rgba(22, 27, 34, 0.75);
      border: 2px solid; border-radius: 12px;
      padding: 16px 18px;
    }
    .scen-card .head {
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 10px;
    }
    .scen-card .name { font-size: 15px; font-weight: 700; }
    .scen-card .prob { font-size: 12px; color: #8b949e; }
    .scen-card ul { margin-left: 18px; font-size: 13px; color: #e6edf3; line-height: 1.7; }
    .scen-card .pos {
      margin-top: 10px; padding: 6px 10px; border-radius: 6px;
      font-size: 13px; font-weight: 700; display: inline-block;
    }
    .attack { border-color: #ff4444; }
    .attack .name, .attack .pos { color: #ff4444; }
    .attack .pos { background: rgba(255, 68, 68, 0.15); }
    .moderate { border-color: #d29922; }
    .moderate .name, .moderate .pos { color: #d29922; }
    .moderate .pos { background: rgba(210, 153, 34, 0.15); }
    .defense { border-color: #58a6ff; }
    .defense .name, .defense .pos { color: #58a6ff; }
    .defense .pos { background: rgba(88, 166, 255, 0.15); }
    .critical { border-color: #ff8800; }
    .critical .name, .critical .pos { color: #ff8800; }
    .critical .pos { background: rgba(255, 136, 0, 0.15); }

    /* 明日核心股票池 (fp-*) */
    .fp-block { margin-bottom: 20px; }
    .fp-block-title {
      font-size: 15px; font-weight: 700; color: #e6edf3;
      padding: 10px 14px; border-radius: 8px 8px 0 0;
      background: rgba(30, 35, 42, 0.9);
      border-left: 4px solid;
    }
    .fp-space .fp-block-title { border-left-color: #ff4444; color: #ff8888; }
    .fp-midcore .fp-block-title { border-left-color: #58a6ff; color: #79b8ff; }
    .fp-block-sub { color: #8b949e; font-size: 12px; font-weight: 400; margin-left: 10px; }
    table.fp-table {
      width: 100%; background: rgba(22, 27, 34, 0.7);
      border-radius: 0 0 8px 8px; border-collapse: separate;
      border-spacing: 0; overflow: hidden;
    }
    table.fp-table th, table.fp-table td {
      padding: 10px 14px; border-bottom: 1px solid rgba(48, 54, 61, 0.6);
      text-align: left; font-size: 13px; vertical-align: top;
    }
    table.fp-table th {
      background: rgba(30, 35, 42, 0.5); color: #8b949e;
      font-size: 11px; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    table.fp-table tbody tr:hover { background: rgba(88, 166, 255, 0.05); }
    table.fp-table tbody tr:last-child td { border-bottom: none; }
    .fp-name { width: 100px; color: #ffcc00; }
    .fp-plate { width: 130px; color: #79b8ff; font-size: 12px; }
    .fp-entry { color: #c9d1d9; line-height: 1.55; }
    .fp-stop { width: 200px; color: #ff8888; font-size: 12px; }

    footer {
      margin-top: 40px; padding-top: 20px; text-align: center;
      color: #6e7681; font-size: 12px;
      border-top: 1px solid rgba(48, 54, 61, 0.6);
    }
    @media (max-width: 640px) {
      .hero { flex-direction: column; align-items: stretch; }
      .hero .right { text-align: left; }
      .fp-plate, .fp-stop { width: auto; }
    }
    '''

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{_esc(date_str)} 复盘决策看板 · 6 场景模型</title>
<style>{css}</style>
</head>
<body>
<div class="wrap" style="--sc:{_esc(color)};">
  <a class="back" href="../index.html">← 返回首页</a>
  <div class="hero">
    <div class="left">
      <h1>{_esc(scene)} · {_esc(action)}</h1>
      <div class="sub">{_esc(level)}</div>
      <div class="date">报告日期 {_esc(date_str)}</div>
    </div>
    <div class="right">
      <div class="pos-label">建议仓位</div>
      <div class="pos-value">{_esc(position)}</div>
      <div class="win">历史 T+3 破新高 {wr_str}</div>
    </div>
    <div class="hero-desc">{_esc(desc)}</div>
  </div>

  {kpi_html}

  <div class="section-title">场景判定 · 三因子交叉</div>
  <table class="factor">
    <thead><tr><th>因子</th><th>当前读数</th><th>是否达标</th><th>辅助说明</th></tr></thead>
    <tbody>{factor_rows}</tbody>
  </table>

  <div class="section-title">明日 T+1 · 4 情形决策树</div>
  <div class="scenario-tree">{scen_cards}</div>

  <div class="section-title">明日核心股票池 · 具体标的与入场条件</div>
  {focus_rows_html}

  <div class="section-title">历史同型样本 (T+1/T+2/T+3 走势对照)</div>
  <table class="history">
    <thead><tr>
      <th>日期</th><th>空间板</th><th>涨停</th><th>梯队分</th>
      <th>T+1</th><th>T+2</th><th>T+3</th><th>结局</th>
    </tr></thead>
    <tbody>{hist_rows}</tbody>
  </table>

  <footer>
    连板情绪分析 · 6 场景数据驱动模型 · 每日自动跑批生成
    <br>数据基于 173 天历史回测, 胜率均为经验概率, 仅供研究参考, 不构成投资建议
  </footer>
</div>
</body>
</html>'''


def save_dashboard(ctx: dict, output_path: str) -> str:
    """把 ctx 渲染成 HTML 并写盘, 返回文件路径."""
    html = generate_dashboard_html(ctx)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return output_path


def generate_dashboard_section(ctx: dict) -> str:
    """把看板渲染为可内嵌进主报告顶部的 HTML section (含独立作用域 CSS).

    所有 class 加 `dbd-` 前缀 (dashboard-embedded 缩写), 避免与主报告的
    .card / .hero / .grid 等类名冲突. 返回一段可以直接插入主报告 body 的
    片段 (含 <style>...</style> + <section class="dbd-wrap">...</section>).
    """
    date_str = ctx.get('date_str') or datetime.now().strftime('%Y-%m-%d')
    scene = ctx.get('scene', '中性震荡')
    action = ctx.get('action', '结构博弈 / 主线为纲')
    level = ctx.get('level', '中性')
    color = ctx.get('color', '#d29922')
    position = ctx.get('position', '5成仓位')
    win_rate = ctx.get('win_rate')
    desc = ctx.get('desc', '')

    curr_h = int(ctx.get('curr_h', 0) or 0)
    prev_h = int(ctx.get('prev_h', 0) or 0)
    pressure_5d = int(ctx.get('pressure_5d', 0) or 0)
    zt = int(ctx.get('zt', 0) or 0)
    dt = int(ctx.get('dt', 0) or 0)
    ad = ctx.get('ad_ratio')
    ad_str = f'{ad:.3f}' if isinstance(ad, (int, float)) else '未取到'
    ladder = ctx.get('ladder')
    h3 = ctx.get('h3', 0)
    h4 = ctx.get('h4', 0)
    h5 = ctx.get('h5', 0)
    h6p = ctx.get('h6p', 0)
    zt_prev = int(ctx.get('zt_prev', 0) or 0)
    zt_boom = (zt / zt_prev) if zt_prev > 0 else None
    focus_df = ctx.get('focus_df')

    scenarios = ctx.get('scenarios') or _default_scenarios(curr_h, prev_h, focus_df=focus_df)

    # 因子交叉表
    ap_ad_ok = '✅ 达标' if isinstance(ad, (int, float)) and ad > 0.65 else (
        '⚠️ 中档' if isinstance(ad, (int, float)) and ad >= 0.5 else '❌ 未达')
    ap_ladder_ok = '✅ 达标' if isinstance(ladder, (int, float)) and ladder >= 12 else '❌ 未达'
    breakout_ok = '✅ 达标' if curr_h > pressure_5d else '❌ 未达'

    def _fr(name, value, ok, hint):
        icon_class = {'✅': 'dbd-check-ok', '❌': 'dbd-check-fail',
                      '❓': 'dbd-check-warn', '⚠️': 'dbd-check-warn'}.get(
            ok[:1] if ok else '', 'dbd-check-warn')
        return (f'<tr><td>{_esc(name)}</td><td><b>{_esc(value)}</b></td>'
                f'<td><span class="{icon_class}">{_esc(ok)}</span></td>'
                f'<td>{_esc(hint)}</td></tr>')

    factor_rows = ''.join([
        _fr('① 空间板突破 5 日压力',
            f'{curr_h}板 vs 前压力 {pressure_5d}板', breakout_ok,
            f'昨断 {curr_h - prev_h:+d} 板'),
        _fr('② 情绪 A/D > 0.65 (A+ 门槛)',
            ad_str, ap_ad_ok,
            f'涨停 {zt}, 跌停 {dt}'),
        _fr('③ 梯队分 ≥ 12 (A+ 门槛)',
            f'{ladder}分' if ladder is not None else '—', ap_ladder_ok,
            f'3板 {h3} / 4板 {h4} / 5板 {h5} / 6+板 {h6p}'),
    ])

    def _sc(s):
        items = ''.join(f'<li>{_esc(x)}</li>' for x in s.get('items', []))
        kind = s.get('kind', 'moderate')
        return (f'<div class="dbd-scen dbd-{kind}">'
                f'<div class="dbd-scen-head"><span class="dbd-scen-name">{_esc(s.get("name", ""))}</span>'
                f'<span class="dbd-scen-prob">{_esc(s.get("prob", ""))}</span></div>'
                f'<ul>{items}</ul>'
                f'<div class="dbd-scen-pos">{_esc(s.get("pos", ""))}</div>'
                f'</div>')

    scen_cards = ''.join(_sc(s) for s in scenarios)

    # 内嵌简版 focus 表 (与独立看板同源, class 加 dbd- 前缀避免冲突)
    focus_rows_inline = ''
    try:
        if focus_df is not None and hasattr(focus_df, 'empty') and not focus_df.empty:
            _rows = []
            for _, r in focus_df.iterrows():
                _rows.append(
                    f'<tr>'
                    f'<td class="dbd-fp-name">{_esc(r.get("股票", ""))}</td>'
                    f'<td class="dbd-fp-plate">{_esc(_clean_plate(str(r.get("板块", ""))) or "—")}</td>'
                    f'<td class="dbd-fp-pool">{_esc(r.get("策略池", ""))}</td>'
                    f'<td class="dbd-fp-entry">{_esc(r.get("入场条件", ""))}</td>'
                    f'<td class="dbd-fp-stop">{_esc(r.get("防守位", ""))}</td>'
                    f'</tr>'
                )
            focus_rows_inline = (
                '<table class="dbd-fp-table"><thead><tr>'
                '<th>标的</th><th>板块</th><th>策略池</th><th>入场条件</th><th>防守位</th>'
                '</tr></thead><tbody>' + ''.join(_rows) + '</tbody></table>'
            )
    except Exception:
        pass

    wr_color = _win_rate_color(win_rate)
    wr_str = f'{win_rate * 100:.0f}%' if isinstance(win_rate, (int, float)) else '—'
    boom_str = f'×{zt_boom:.2f}' if zt_boom else '—'

    kpi_html = f'''
    <div class="dbd-grid">
      <div class="dbd-card"><h4>空间板</h4><div class="dbd-kpi dbd-red">{curr_h}板</div>
        <div class="dbd-hint">5日压力 {pressure_5d}板 · 昨 {prev_h}板 ({curr_h - prev_h:+d})</div></div>
      <div class="dbd-card"><h4>涨停家数</h4><div class="dbd-kpi dbd-red">{zt}</div>
        <div class="dbd-hint">昨日 {zt_prev} · {boom_str}</div></div>
      <div class="dbd-card"><h4>跌停家数</h4><div class="dbd-kpi dbd-green">{dt}</div>
        <div class="dbd-hint">阈值 &gt; 15 转防守</div></div>
      <div class="dbd-card"><h4>情绪 A/D</h4><div class="dbd-kpi dbd-yellow">{ad_str}</div>
        <div class="dbd-hint">A+ / E 门槛 &gt; 0.65</div></div>
      <div class="dbd-card"><h4>梯队分</h4><div class="dbd-kpi dbd-blue">{_fmt(ladder)}</div>
        <div class="dbd-hint">h3×1 + h4×2 + h5×3 + h6+×4</div></div>
      <div class="dbd-card"><h4>历史胜率 (T+3)</h4><div class="dbd-kpi" style="color:{wr_color};">{wr_str}</div>
        <div class="dbd-hint">{_esc(level)}</div></div>
    </div>'''

    css = f'''
    <style>
    .dbd-wrap {{ --dbd-sc: {color}; font-family: inherit; margin: 24px 0 32px; }}
    .dbd-hero {{
      background: linear-gradient(135deg, color-mix(in srgb, var(--dbd-sc) 20%, transparent),
                                       color-mix(in srgb, var(--dbd-sc) 8%, transparent));
      border: 2px solid var(--dbd-sc); border-radius: 14px;
      padding: 20px 24px; margin-bottom: 16px;
      display: flex; align-items: center; justify-content: space-between;
      gap: 20px; flex-wrap: wrap;
      box-shadow: 0 0 30px color-mix(in srgb, var(--dbd-sc) 22%, transparent);
    }}
    .dbd-hero .dbd-left h2 {{ font-size: 22px; font-weight: 800; color: var(--dbd-sc); margin-bottom: 4px; }}
    .dbd-hero .dbd-left .dbd-sub {{ color: #c9d1d9; font-size: 13px; }}
    .dbd-hero .dbd-right {{
      text-align: right;
      background: color-mix(in srgb, var(--dbd-sc) 22%, transparent);
      padding: 12px 20px; border-radius: 10px; border: 1px solid var(--dbd-sc);
    }}
    .dbd-hero .dbd-pos-label {{ color: color-mix(in srgb, var(--dbd-sc) 70%, #ffffff);
                                 font-size: 12px; margin-bottom: 4px; }}
    .dbd-hero .dbd-pos-value {{ font-size: 22px; font-weight: 800; color: #fff; }}
    .dbd-hero .dbd-win {{ color: #ffcc00; font-size: 12px; margin-top: 4px; }}
    .dbd-desc {{
      grid-column: 1 / -1; color: #c9d1d9; font-size: 13px;
      padding-top: 10px; margin-top: 10px;
      border-top: 1px solid color-mix(in srgb, var(--dbd-sc) 35%, transparent);
      width: 100%;
    }}
    .dbd-grid {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px; margin-bottom: 18px;
    }}
    .dbd-card {{
      background: rgba(22, 27, 34, 0.7);
      border: 1px solid rgba(48, 54, 61, 0.8);
      border-radius: 10px; padding: 14px 16px;
    }}
    .dbd-card h4 {{
      font-size: 12px; color: #d29922; margin-bottom: 6px; font-weight: 600;
      border-left: 3px solid #d29922; padding-left: 8px;
    }}
    .dbd-kpi {{ font-size: 22px; font-weight: 800; color: #fff; }}
    .dbd-kpi.dbd-green {{ color: #3fb950; }}
    .dbd-kpi.dbd-red {{ color: #ff4444; }}
    .dbd-kpi.dbd-yellow {{ color: #ffcc00; }}
    .dbd-kpi.dbd-blue {{ color: #58a6ff; }}
    .dbd-hint {{ color: #8b949e; font-size: 11px; margin-top: 3px; }}
    .dbd-section-title {{
      font-size: 15px; font-weight: 700; color: #ffcc00;
      margin: 20px 0 10px; padding-left: 9px;
      border-left: 3px solid #ffcc00;
    }}
    .dbd-factor {{
      width: 100%; background: rgba(22, 27, 34, 0.7);
      border-radius: 10px; border-collapse: separate; border-spacing: 0;
      overflow: hidden; margin-bottom: 8px;
    }}
    .dbd-factor th, .dbd-factor td {{
      padding: 10px 12px; border-bottom: 1px solid rgba(48, 54, 61, 0.6);
      text-align: left; font-size: 13px;
    }}
    .dbd-factor th {{
      background: rgba(30, 35, 42, 0.9); color: #8b949e;
      font-size: 11px; font-weight: 600; text-transform: uppercase;
    }}
    .dbd-check-ok {{ color: #3fb950; font-weight: 700; }}
    .dbd-check-fail {{ color: #ff4444; font-weight: 700; }}
    .dbd-check-warn {{ color: #ffcc00; font-weight: 700; }}
    .dbd-tree {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
    }}
    .dbd-scen {{
      background: rgba(22, 27, 34, 0.75);
      border: 2px solid; border-radius: 10px; padding: 14px 16px;
    }}
    .dbd-scen-head {{
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 8px;
    }}
    .dbd-scen-name {{ font-size: 14px; font-weight: 700; }}
    .dbd-scen-prob {{ font-size: 11px; color: #8b949e; }}
    .dbd-scen ul {{ margin-left: 16px; font-size: 12px; color: #e6edf3; line-height: 1.6; }}
    .dbd-scen-pos {{
      margin-top: 8px; padding: 5px 9px; border-radius: 5px;
      font-size: 12px; font-weight: 700; display: inline-block;
    }}
    .dbd-attack {{ border-color: #ff4444; }}
    .dbd-attack .dbd-scen-name, .dbd-attack .dbd-scen-pos {{ color: #ff4444; }}
    .dbd-attack .dbd-scen-pos {{ background: rgba(255, 68, 68, 0.15); }}
    .dbd-moderate {{ border-color: #d29922; }}
    .dbd-moderate .dbd-scen-name, .dbd-moderate .dbd-scen-pos {{ color: #d29922; }}
    .dbd-moderate .dbd-scen-pos {{ background: rgba(210, 153, 34, 0.15); }}
    .dbd-defense {{ border-color: #58a6ff; }}
    .dbd-defense .dbd-scen-name, .dbd-defense .dbd-scen-pos {{ color: #58a6ff; }}
    .dbd-defense .dbd-scen-pos {{ background: rgba(88, 166, 255, 0.15); }}
    .dbd-critical {{ border-color: #ff8800; }}
    .dbd-critical .dbd-scen-name, .dbd-critical .dbd-scen-pos {{ color: #ff8800; }}
    .dbd-critical .dbd-scen-pos {{ background: rgba(255, 136, 0, 0.15); }}
    .dbd-full-link {{
      display: inline-block; margin-top: 10px; padding: 6px 14px;
      background: #161b22; color: #58a6ff; text-decoration: none;
      border: 1px solid #30363d; border-radius: 6px; font-size: 12px;
    }}
    .dbd-full-link:hover {{ border-color: #58a6ff; }}
    .dbd-fp-table {{
      width: 100%; background: rgba(22, 27, 34, 0.7);
      border-radius: 10px; border-collapse: separate; border-spacing: 0;
      overflow: hidden; margin-bottom: 8px;
    }}
    .dbd-fp-table th, .dbd-fp-table td {{
      padding: 10px 12px; border-bottom: 1px solid rgba(48, 54, 61, 0.6);
      text-align: left; font-size: 12.5px; vertical-align: top;
    }}
    .dbd-fp-table th {{
      background: rgba(30, 35, 42, 0.9); color: #8b949e;
      font-size: 11px; font-weight: 600; text-transform: uppercase;
    }}
    .dbd-fp-table tbody tr:last-child td {{ border-bottom: none; }}
    .dbd-fp-name {{ width: 100px; color: #ffcc00; font-weight: 700; }}
    .dbd-fp-plate {{ width: 130px; color: #79b8ff; font-size: 11.5px; }}
    .dbd-fp-pool {{ width: 130px; color: #d29922; font-size: 11.5px; }}
    .dbd-fp-entry {{ color: #c9d1d9; line-height: 1.55; }}
    .dbd-fp-stop {{ width: 180px; color: #ff8888; font-size: 11.5px; }}
    @media (max-width: 640px) {{
      .dbd-hero {{ flex-direction: column; align-items: stretch; }}
      .dbd-hero .dbd-right {{ text-align: left; }}
    }}
    </style>'''

    return f'''{css}
<section class="dbd-wrap">
  <div class="dbd-hero">
    <div class="dbd-left">
      <h2>今日决策看板 · {_esc(scene)} · {_esc(action)}</h2>
      <div class="dbd-sub">{_esc(level)} · 报告日期 {_esc(date_str)}</div>
    </div>
    <div class="dbd-right">
      <div class="dbd-pos-label">建议仓位</div>
      <div class="dbd-pos-value">{_esc(position)}</div>
      <div class="dbd-win">历史 T+3 破新高 {wr_str}</div>
    </div>
    <div class="dbd-desc">{_esc(desc)}</div>
  </div>

  {kpi_html}

  <div class="dbd-section-title">场景判定 · 三因子交叉</div>
  <table class="dbd-factor">
    <thead><tr><th>因子</th><th>当前读数</th><th>是否达标</th><th>辅助说明</th></tr></thead>
    <tbody>{factor_rows}</tbody>
  </table>

  <div class="dbd-section-title">明日 T+1 · 4 情形决策树</div>
  <div class="dbd-tree">{scen_cards}</div>

  <div class="dbd-section-title">明日核心股票池 · 具体标的与入场条件</div>
  {focus_rows_inline}
</section>'''
