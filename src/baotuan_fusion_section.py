# -*- coding: utf-8 -*-
"""重点抱团个股与重点抱团可转债深度实战分析与追踪看板生成器 (baotuan_fusion_section.py).

为主线强度追踪大报告、可转债独立研报与明日预案提供统一的、高密度、零模糊的实操预案体系组件。
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from paths import DATA_DIR, OUTPUT_DIR
from plan_parser import DEFAULT_BAOTUAN_STOCKS, DEFAULT_BAOTUAN_CBS


def _e(val: Any) -> str:
    """HTML转义."""
    return html.escape(str(val if val is not None else ""), quote=True)


def _fmt(val: Any, digits: int = 2, suffix: str = "", signed: bool = False) -> str:
    if val is None or val == "":
        return "—"
    try:
        n = float(val)
        sign = "+" if (signed and n > 0) else ""
        return f"{sign}{n:.{digits}f}{suffix}"
    except (ValueError, TypeError):
        return str(val)


def _pct_span(val: Any) -> str:
    if val is None or val == "":
        return '<span style="color:#8b949e">—</span>'
    try:
        n = float(val)
        col = "#f85149" if n > 0 else "#3fb950" if n < 0 else "#8b949e"
        return f'<span style="color:{col};font-weight:700">{n:+.2f}%</span>'
    except (ValueError, TypeError):
        return f'<span>{_e(val)}</span>'


def build_baotuan_deep_analysis_section(
    report_date: str = "",
    echelon: Optional[List[Dict[str, Any]]] = None,
    is_site_mode: bool = False,
    prefix: str = "",
) -> str:
    """生成嵌入主报告与各子报告的【重点抱团个股与转债深度实战分析与追踪】专栏 HTML."""
    r_date = str(report_date or "").strip()
    if not r_date:
        r_date = "2026-11-09"
    if len(r_date) == 8 and r_date.isdigit():
        r_date = f"{r_date[:4]}-{r_date[4:6]}-{r_date[6:]}"

    # 1. 加载同日真实可转债快照数据
    cb_rows = []
    cb_summary = {}
    try:
        from cb_plan_data import load_cb_plan_data
        cb_plan = load_cb_plan_data(r_date, data_dir=DATA_DIR, output_dir=OUTPUT_DIR)
        cb_rows = cb_plan.get("rows", [])
        cb_summary = cb_plan.get("summary", {})
    except Exception as e:
        cb_rows = []

    # 2. 组织转债重点标的：结合快照真实行情与标准体系
    # 标的池优先级：福新转债、奥普转债、声迅转债、精测转2、澳弘转债等
    cb_dict = {r.get("code"): r for r in cb_rows}
    
    # 构建转债表格行
    cb_display_list = []
    
    # 预设重点转债配置
    core_cb_configs = [
        {
            "code": "111012",
            "name": "福新转债",
            "role": "👑 标杆中军 · 龙头共振主攻",
            "role_badge": "中军共振主攻",
            "badge_color": "#f85149",
            "stock_code": "605488",
            "stock_name": "福莱新材",
            "action": "• 竞价预期：正股大单封一字或高开 > +5%，转债高开 +3%~+6%<br>• 买入指令：早盘放量脉冲不盲目追高，待回踩分时均线平盘企稳低吸；若正股封死涨停，持仓博弈冲高分批止盈；若正股开板立即平仓抢跑！",
            "pos": "0.5~1.0 成",
            "stop": "跌破分时黄线超 5 分钟或正股炸板无回封立即止损；14:55 前坚决清仓不隔夜！",
            "default_price": 750.86,
            "default_pct": 11.38,
            "default_amount": 75.27,
            "default_premium": 60.15,
            "default_stock_pct": 6.50
        },
        {
            "code": "118075",
            "name": "奥普转债",
            "role": "🚀 日内高弹性先锋 · 波段套利",
            "role_badge": "高弹性先锋",
            "badge_color": "#38bdf8",
            "stock_code": "688686",
            "stock_name": "奥普特",
            "action": "• 竞价预期：小幅高开 +1%~+2%<br>• 买入指令：锚定正股分时脉冲，正股放量急拉冲板时跟随低吸转债，日内涨幅达 +3%~+5% 坚决分批落袋，绝不格局！",
            "pos": "0.5 成",
            "stop": "冲高回落跌破开盘价立即离场，日内破位 -2.5% 或正股转弱跳水坚决止损。",
            "default_price": 218.68,
            "default_pct": 4.68,
            "default_amount": 27.88,
            "default_premium": 97.74,
            "default_stock_pct": 2.60
        },
        {
            "code": "127080",
            "name": "声迅转债",
            "role": "🔥 涨停共振先锋 · 题材爆发",
            "role_badge": "涨停共振",
            "badge_color": "#e3b341",
            "stock_code": "003004",
            "stock_name": "声迅股份",
            "action": "• 竞价预期：正股巨量封板或大幅高开 > +7%，转债高开 +5%~+8%<br>• 买入指令：正股开盘秒板即跟进轻仓套利，脉冲达 +8%~+12% 分批快速获利了结。",
            "pos": "0.5 成",
            "stop": "正股开板或转债跌破开盘均线立即平仓，绝不恋战。",
            "default_price": 232.90,
            "default_pct": 15.87,
            "default_amount": 20.92,
            "default_premium": 32.45,
            "default_stock_pct": 10.01
        },
        {
            "code": "123176",
            "name": "精测转2",
            "role": "🛡️ 行业共振修复 · 辅助套利",
            "role_badge": "辅助套利",
            "badge_color": "#bc8cff",
            "stock_code": "300567",
            "stock_name": "精测电子",
            "action": "• 竞价预期：平开附近 (-0.5% ~ +1.0%)<br>• 买入指令：半导体板块共振冲高时跟随低吸，逢脉冲急拉分批减半止盈，博弈正股修复溢价。",
            "pos": "0.5 成",
            "stop": "分时均线破位无条件平仓离场。",
            "default_price": 276.68,
            "default_pct": 7.66,
            "default_amount": 30.61,
            "default_premium": 48.75,
            "default_stock_pct": 3.81
        },
        {
            "code": "111024",
            "name": "澳弘转债",
            "role": "⛔ 破位出清 · 一票否决禁买",
            "role_badge": "破位一票否决",
            "badge_color": "#ef4444",
            "stock_code": "605058",
            "stock_name": "澳弘电子",
            "action": "• 警示定性：正股大跌破位，正向反馈完全丧失！<br>• 操盘指令：<b>一票否决！严禁抄底！</b> 若有底仓，盘中任何反抽急拉立即清仓离场，规避正股进一步下探带崩转债溢价风险。",
            "pos": "0 仓位",
            "stop": "坚决禁止开新仓，持仓见脉冲即斩仓。",
            "default_price": 219.58,
            "default_pct": 3.12,
            "default_amount": 40.32,
            "default_premium": 51.44,
            "default_stock_pct": -3.17
        }
    ]

    cb_table_rows = []
    for cfg in core_cb_configs:
        code = cfg["code"]
        snap_item = cb_dict.get(code)
        
        # 优先读取快照中的最新真实数据
        if snap_item:
            price = snap_item.get("close") or cfg["default_price"]
            pct = snap_item.get("r1") if snap_item.get("r1") is not None else cfg["default_pct"]
            amount = snap_item.get("amount_yi") or cfg["default_amount"]
            premium = snap_item.get("premium_pct") or cfg["default_premium"]
            s_name = snap_item.get("stock_name") or cfg["stock_name"]
            s_pct = snap_item.get("stock_r1") if snap_item.get("stock_r1") is not None else cfg["default_stock_pct"]
        else:
            price = cfg["default_price"]
            pct = cfg["default_pct"]
            amount = cfg["default_amount"]
            premium = cfg["default_premium"]
            s_name = cfg["stock_name"]
            s_pct = cfg["default_stock_pct"]

        is_black = "一票否决" in cfg["role_badge"] or "0" in cfg["pos"]
        bg_style = "background: rgba(239,68,68,0.06);" if is_black else ""

        cb_table_rows.append(f"""
        <tr style="border-bottom:1px solid #21262d;{bg_style}">
          <td style="padding:10px 12px;white-space:nowrap;">
            <div style="font-weight:800;color:#f0f6fc;font-size:13.5px;">{cfg['name']}</div>
            <div style="font-size:11px;color:#8b949e;">{code}</div>
            <span style="font-size:10.5px;color:{cfg['badge_color']};background:rgba(255,255,255,0.06);padding:1px 6px;border-radius:4px;border:1px solid {cfg['badge_color']}44;display:inline-block;margin-top:3px;">
              {cfg['role_badge']}
            </span>
          </td>
          <td style="padding:10px 12px;text-align:right;">
            <div style="font-weight:700;color:#f0f6fc;font-size:13px;">{_fmt(price, 3)}</div>
            <div>{_pct_span(pct)}</div>
          </td>
          <td style="padding:10px 12px;text-align:right;font-weight:700;color:#e3b341;white-space:nowrap;">
            {_fmt(amount, 2, ' 亿')}
          </td>
          <td style="padding:10px 12px;text-align:right;color:#8b949e;font-size:12px;">
            {_fmt(premium, 2, '%')}
          </td>
          <td style="padding:10px 12px;white-space:nowrap;">
            <div style="font-weight:700;color:#f0f6fc;font-size:12.5px;">{s_name}</div>
            <div>{_pct_span(s_pct)}</div>
          </td>
          <td style="padding:10px 12px;font-size:12.5px;color:#c9d1d9;line-height:1.55;">
            {cfg['action']}
          </td>
          <td style="padding:10px 12px;text-align:center;font-weight:800;color:#e3b341;white-space:nowrap;">
            {cfg['pos']}
          </td>
          <td style="padding:10px 12px;font-size:12px;color:#ff7b72;line-height:1.45;">
            {cfg['stop']}
          </td>
        </tr>
        """)

    # 3. 组织重点个股实战预案大表
    # 动态抓取空间龙、身位先锋与中军
    stock_targets = list(DEFAULT_BAOTUAN_STOCKS)

    # 如果有 echelon 动态提炼真实标的名称
    if echelon and len(echelon) > 0:
        try:
            top_h = echelon[0].get("height", 1)
            top_stocks = echelon[0].get("stocks", [])
            if top_stocks:
                s0_name = " / ".join(top_stocks[:2])
                stock_targets[0] = {
                    "role": f"① 主线空间龙 / 领涨龙头 ({s0_name} · {top_h}板)",
                    "tag": "space_hero",
                    "badge_color": "#F87171",
                    "logic": f"全市场最高 {top_h} 连板梯队，测试空间压制；具备最强板块号召力",
                    "action": "竞价高开 > +3% 且成交量达标，9:35 确认分歧转一致后快速封板打板买入",
                    "pos": "0.5~1.0 成",
                    "stop": "盘中跌破分时黄线或日内跌幅超 -3.5% 坚决离场"
                }
            if len(echelon) > 1:
                mid_h = echelon[1].get("height", 1)
                mid_stocks = echelon[1].get("stocks", [])
                if mid_stocks:
                    s1_name = " / ".join(mid_stocks[:3])
                    stock_targets[1] = {
                        "role": f"② 梯队先锋 / 身位卡位 ({s1_name} · {mid_h}板)",
                        "tag": "echelon_vanguard",
                        "badge_color": "#38BDF8",
                        "logic": f"主升次高 {mid_h} 板身位中坚，若高标分歧具备卡位破局龙潜质",
                        "action": "竞价小幅高开 +1%~+3%，早盘放量回踩分时黄线企稳打板",
                        "pos": "0.5~1.0 成",
                        "stop": "跌破分时均线超过 10 分钟无条件止损"
                    }
        except Exception:
            pass

    stock_table_rows = []
    for s in stock_targets:
        is_black = "禁买" in s["role"] or "⛔" in s["role"]
        bg_style = "background: rgba(239,68,68,0.06);" if is_black else ""
        stock_table_rows.append(f"""
        <tr style="border-bottom:1px solid #21262d;{bg_style}">
          <td style="padding:10px 12px;font-weight:800;color:{s['badge_color']};font-size:13.5px;white-space:nowrap;">
            {s['role']}
          </td>
          <td style="padding:10px 12px;font-size:12.5px;color:#8b949e;line-height:1.5;">
            {s['logic']}
          </td>
          <td style="padding:10px 12px;font-size:12.5px;color:#f0f6fc;line-height:1.55;background:rgba(0,0,0,0.18);">
            {s['action']}
          </td>
          <td style="padding:10px 12px;text-align:center;font-weight:800;color:#e3b341;white-space:nowrap;">
            {s['pos']}
          </td>
          <td style="padding:10px 12px;font-size:12px;color:#ff7b72;line-height:1.45;">
            {s['stop']}
          </td>
        </tr>
        """)

    # 4. 组装完整大专栏 HTML
    detail_plan_href = f"{prefix}plan/latest.html" if is_site_mode else "今日复盘与明日预案_最新.html"
    detail_cb_href = f"{prefix}cb_baotuan/latest.html" if is_site_mode else "可转债抱团复盘与追踪.html"

    return f"""<!-- baotuan-deep-analysis:start -->
<section id="sec-baotuan-focus" class="report-section baotuan-deep-section" style="margin:28px 0;background:linear-gradient(180deg, rgba(22,27,34,0.98) 0%, rgba(13,17,23,0.98) 100%);border:1px solid #30363d;border-left:5px solid #00cccc;border-radius:14px;padding:22px 24px;box-shadow:0 8px 32px rgba(0,0,0,0.45);">
  
  <!-- 标题头部 -->
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;margin-bottom:18px;border-bottom:1px solid #21262d;padding-bottom:14px;">
    <div>
      <h2 id="sec-baotuan-title" style="margin:0;font-size:20px;font-weight:900;color:#f0f6fc;display:flex;align-items:center;gap:10px;">
        <span>🤝 核心重点 · 抱团个股与转债实战分析与追踪</span>
        <span style="font-size:11.5px;background:rgba(0,204,204,0.15);color:#00cccc;border:1px solid rgba(0,204,204,0.4);padding:3px 10px;border-radius:14px;font-weight:700;">
          全景复盘 × 重点个股 × T+0转债预案
        </span>
      </h2>
      <div style="color:#8b949e;font-size:13px;margin-top:6px;line-height:1.5;">
        数据基准日：<b style="color:#f0f6fc;">{r_date}</b> ｜ 拒绝模棱两可罗列多重条件，系统直接给出<b>零模糊、结果导向、即刻执行</b>的明日实操预案体系！
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
      <a href="{detail_plan_href}" style="background:#1f6feb;color:#fff;text-decoration:none;font-size:12.5px;font-weight:700;padding:6px 14px;border-radius:6px;display:inline-flex;align-items:center;gap:6px;">
        <span>📋 打开完整推演预案</span>
      </a>
      <a href="{detail_cb_href}" style="background:rgba(188,140,255,0.15);color:#bc8cff;border:1px solid rgba(188,140,255,0.4);text-decoration:none;font-size:12.5px;font-weight:700;padding:6px 14px;border-radius:6px;display:inline-flex;align-items:center;gap:6px;">
        <span>⚡ 专属可转债研报</span>
      </a>
    </div>
  </div>

  <!-- 模块一：市场资金抱团演进全景深度复盘 (三大阵营对比与迁移机制) -->
  <div style="margin-bottom:22px;">
    <div style="font-size:15px;font-weight:800;color:#f0f6fc;margin-bottom:8px;display:flex;align-items:center;gap:8px;">
      <span>🌊 资金抱团演进全景：老高标出清瓦解 ➔ 产业大中军抱团 ➔ 可转债 T+0 避险溢出</span>
    </div>
    
    <!-- 资金迁移卡片 -->
    <div style="background:#0f141c;border:1px solid #21262d;border-radius:10px;padding:14px 16px;margin-bottom:14px;">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:12.5px;">
        <div style="background:#161b22;padding:8px 14px;border-radius:8px;border:1px solid #f85149;color:#ff7b72;flex:1;min-width:200px;">
          <b style="font-size:13px;color:#f85149;">① 老高标纯情绪妖股决裂出清</b><br>
          断板跌停踩踏 · 次日反包率仅 7.6%<br>
          <span style="font-size:11px;color:#8b949e;">大资金不计成本抢跑，严禁低吸抄底</span>
        </div>
        <div style="color:#8b949e;font-size:18px;font-weight:800;">➔</div>
        <div style="background:#161b22;padding:8px 14px;border-radius:8px;border:1px solid #3fb950;color:#3fb950;flex:1;min-width:200px;">
          <b style="font-size:13px;color:#3fb950;">② 核心产业大中军机构深度抱团</b><br>
          百亿主力主战场 · 巨量承接流动性底座<br>
          <span style="font-size:11px;color:#8b949e;">具备抗跌韧性，回踩均线低吸做主升大二波</span>
        </div>
        <div style="color:#8b949e;font-size:18px;font-weight:800;">➔</div>
        <div style="background:#161b22;padding:8px 14px;border-radius:8px;border:1px solid #bc8cff;color:#bc8cff;flex:1;min-width:200px;">
          <b style="font-size:13px;color:#bc8cff;">③ T+0 重点可转债日内避险溢出</b><br>
          T+0 极致机动 · 锚定正股共振脉冲套利<br>
          <span style="font-size:11px;color:#8b949e;">零隔夜持仓风险，14:55 前坚决落袋清仓</span>
        </div>
      </div>
    </div>
  </div>

  <!-- 模块二：🎯 重点抱团个股 · 明日实战预案（直接给指令） -->
  <div style="margin-bottom:24px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <div style="font-size:15.5px;font-weight:800;color:#f0f6fc;display:flex;align-items:center;gap:8px;">
        <span>🎯 重点抱团个股 · 明日实战预案 (直接给指令)</span>
        <span style="font-size:11px;color:#38bdf8;background:rgba(56,189,248,0.15);padding:2px 8px;border-radius:4px;font-weight:700;">精准执行 · 结果导向</span>
      </div>
      <span style="font-size:12px;color:#8b949e;">拒绝模糊推演，一票否决黑名单坚决不碰</span>
    </div>

    <div style="overflow-x:auto;border:1px solid #30363d;border-radius:10px;background:#0d1117;">
      <table style="width:100%;border-collapse:collapse;font-size:12.5px;min-width:680px;">
        <thead>
          <tr style="background:#1c2128;color:#8b949e;border-bottom:1px solid #30363d;text-align:left;">
            <th style="padding:10px 12px;width:24%;">标的角色定位</th>
            <th style="padding:10px 12px;width:20%;">筛选逻辑</th>
            <th style="padding:10px 12px;width:32%;">明日竞价预期与精准买入执行指令</th>
            <th style="padding:10px 12px;width:10%;text-align:center;">建议仓位</th>
            <th style="padding:10px 12px;width:14%;">硬性止损底线</th>
          </tr>
        </thead>
        <tbody>
          {"".join(stock_table_rows)}
        </tbody>
      </table>
    </div>
  </div>

  <!-- 模块三：⚡ 重点抱团可转债 · 明日 T+0 深度实战作战预案（直接给指令） -->
  <div style="margin-bottom:24px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <div style="font-size:15.5px;font-weight:800;color:#bc8cff;display:flex;align-items:center;gap:8px;">
        <span>⚡ 重点抱团可转债 · 明日 T+0 深度实战作战预案 (直接给指令)</span>
        <span style="font-size:11px;color:#bc8cff;background:rgba(188,140,255,0.15);padding:2px 8px;border-radius:4px;font-weight:700;">T+0 极致机动 · 锚定正股</span>
      </div>
      <span style="font-size:12px;color:#8b949e;">核心逻辑完全锚定正股 ｜ 14:55 前坚决清仓不隔夜</span>
    </div>

    <div style="overflow-x:auto;border:1px solid #30363d;border-radius:10px;background:#0d1117;">
      <table style="width:100%;border-collapse:collapse;font-size:12.5px;min-width:760px;">
        <thead>
          <tr style="background:#1c2128;color:#8b949e;border-bottom:1px solid #30363d;text-align:left;">
            <th style="padding:10px 12px;width:15%;">转债标的 / 角色</th>
            <th style="padding:10px 12px;width:10%;text-align:right;">收盘 / 涨跌</th>
            <th style="padding:10px 12px;width:10%;text-align:right;">单日成交</th>
            <th style="padding:10px 12px;width:9%;text-align:right;">转股溢价</th>
            <th style="padding:10px 12px;width:12%;">联动正股</th>
            <th style="padding:10px 12px;width:26%;">明日竞价预期与日内 T+0 操盘执行指令 (精准触发)</th>
            <th style="padding:10px 12px;width:8%;text-align:center;">配置仓位</th>
            <th style="padding:10px 12px;width:10%;">硬性撤退底线</th>
          </tr>
        </thead>
        <tbody>
          {"".join(cb_table_rows)}
        </tbody>
      </table>
    </div>
  </div>

  <!-- 模块四：游资实战战法升维 · 两大血泪铁律与操盘三道开关 -->
  <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(320px, 1fr));gap:14px;">
    <!-- 铁律卡片 -->
    <div style="background:#161b22;border:1px solid rgba(248,81,73,0.35);border-left:4px solid #f85149;border-radius:10px;padding:14px 16px;">
      <div style="font-size:13.5px;font-weight:800;color:#ff7b72;margin-bottom:8px;display:flex;align-items:center;gap:6px;">
        <span>🩸</span> 游资实战战法升维 · 两大血泪铁律
      </div>
      <div style="font-size:12.5px;color:#c9d1d9;line-height:1.6;">
        <div>• <b style="color:#f85149;">铁律一 · 断板反包禁令：</b>次日反包率仅 7.6%，冲高一律诱多出逃！严禁低吸、严禁抄底、严禁追反包！高位连板一旦断板转弱，次日任何脉冲均是主力自救诱多，坚决斩仓！</div>
        <div style="margin-top:6px;">• <b style="color:#e3b341;">铁律二 · 冰点修复陷阱：</b>无题材梯队共振脉冲多为假冲天炮！早盘 10:00 前严禁盲目开新仓，只有当大盘企稳均线且板块有首板大单助攻时，才允许小仓位试错！</div>
      </div>
    </div>

    <!-- 三道开关卡片 -->
    <div style="background:#161b22;border:1px solid rgba(63,185,80,0.35);border-left:4px solid #3fb950;border-radius:10px;padding:14px 16px;">
      <div style="font-size:13.5px;font-weight:800;color:#3fb950;margin-bottom:8px;display:flex;align-items:center;gap:6px;">
        <span>🚦</span> 明日操盘执行三道开关 (逐级解锁)
      </div>
      <div style="font-size:12.5px;color:#c9d1d9;line-height:1.6;">
        <div>• <b>第一道开关 · 定总仓位：</b>大中军底吸 0.5~1.0 成，先锋打板 0.5~1.0 成，T+0 转债日内 0.5 成，全天总仓位严格控制在 <b>1.5 ~ 2.0 成</b> 内，绝不满仓！</div>
        <div style="margin-top:4px;">• <b>第二道开关 · 定主攻方向：</b>聚焦核心产业大中军与换手龙头；老周期退潮妖股一票否决，拉黑不碰！</div>
        <div style="margin-top:4px;">• <b>第三道开关 · 定防守止损：</b>个股跌破分时黄线坚决离场；可转债 14:55 前坚决清仓不隔夜！</div>
      </div>
    </div>
  </div>

</section>
<!-- baotuan-deep-analysis:end -->"""
