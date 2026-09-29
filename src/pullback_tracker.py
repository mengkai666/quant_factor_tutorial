"""强势板块放量上涨后·回调一笔量化跟踪与战法结合规则引擎 (Pullback Tactic Tracker).

核心职能:
  1. 固化四大定量规则: 放量主升甄别、健康缩量回调、三级健康度与战法匹配、操盘结论与熔断风控。
  2. 结合盘面真实板块与个股现状 (算力PCB、传媒AI、机器人汽配、跌停核按钮群)。
  3. 输出结构化数据与可视化 HTML 组件，无缝融入主线追踪大报告与明日实战预案。
"""
from __future__ import annotations

import os
import json
from html import escape
from typing import Any

# ==============================================================================
# 一、固化四大量化规则体系 (Fixed Quantitative Rules)
# ==============================================================================

PULLBACK_QUANT_RULES = {
    "rule_impulse": {
        "name": "规则一 · 放量主升甄别规则 (Impulse Expansion)",
        "tag": "主升门槛",
        "color": "#3fb950",
        "min_relative_strength_rs": 115.0,
        "min_sector_gain_pct": 8.0,
        "volume_expansion_ratio": 1.30,
        "dragon_height_min": 3,
        "metrics": [
            ("观察窗口", "过去 15 个交易日"),
            ("超额收益", "相对大盘强弱比 RS ≥ 115% 且累计涨幅 ≥ +8.0%"),
            ("成交量放量", "主升浪单日成交额突破 20 日均量的 1.30 倍以上"),
            ("领涨代表", "出过 ≥ 3 连板前排龙头，或拥有百亿级放量大阳容量中军"),
        ],
        "rationale": "非强势板块的回调往往是阴跌无底洞；唯有放量主升过的板块，主力资金介入深、介入成本清晰，其回调一笔才具备博弈二波或中继反弹的高盈亏比价值。"
    },
    "rule_pullback": {
        "name": "规则二 · 健康缩量回调一笔规则 (Healthy Pullback)",
        "tag": "回调尺度",
        "color": "#58a6ff",
        "pullback_min_pct": 3.5,
        "pullback_max_pct": 11.0,
        "volume_shrink_min_pct": 30.0,
        "ma_support_tolerance_pct": 2.5,
        "metrics": [
            ("空间回撤", "从主升波段最高点回撤 3.5% ~ 11.0% (控制在 0.382~0.5 黄金分割位)"),
            ("缩量特征", "回调日成交量较主升峰值量能萎缩 ≥ 30% (30%~60% 最佳，浮筹锁死)"),
            ("支撑依托", "当前价格距离 10日/20日均线或前期突破颈线在 ±2.5% 范围内"),
            ("形态止跌", "回调末端收出缩量十字星、小阴线或长下影探底企稳 K 线"),
        ],
        "rationale": "下跌放量意味着资金不计成本抢跑派发；下跌极度缩量则表明获利盘未出现踩踏，浮筹充分沉淀，属于典型的良性蓄势洗盘。"
    },
    "rule_health_levels": {
        "name": "规则三 · 三级健康度与量化战法匹配规则 (Tactical Mapping)",
        "tag": "战法匹配",
        "color": "#bc8cff",
        "levels": [
            {
                "level": "Level A",
                "name": "蓄势待发 · 二波主升候选",
                "color": "#3fb950",
                "condition": "缩量极致(量比<0.65) + 守住20日生命线 + 核心中军大单托底 + 无跌停核按钮",
                "matched_tactics": "战法 G (严重异动监管与滑窗二波战法) / 战法 B (龙头断板反包律)",
                "action": "列入明日重点关注池；等待早盘 9:25 竞价放量弱转强启动 1~2 成试错"
            },
            {
                "level": "Level B",
                "name": "中继整理 · 分时观察分支",
                "color": "#d29922",
                "condition": "正常缩量回踩，未破箱体下沿，但板块缺乏多股集群助攻，面临退潮分化或长假抽血",
                "matched_tactics": "战法 C (模仿补涨与板块扩散战法) / 战法 A (低位突破再起)",
                "action": "仅作观察标的；必须在早盘有至少 1 只首板小弟助攻联动时才可极小仓位套利"
            },
            {
                "level": "Level C",
                "name": "破位风险 · 严格剔除雷区",
                "color": "#f85149",
                "condition": "放量跌穿 20 日生命线 / 板块内出现批量一字跌停 / 相对强度 RS 破位下行",
                "matched_tactics": "战法 E (接力防守与退潮空间塌陷) / 战法 D (周期见峰暴跌预警)",
                "action": "列入 P-Black 绝对禁买雷区；坚决不抄底、不接飞刀，防范二次加速暴跌"
            }
        ],
        "rationale": "不同健康度的回调对应完全相反的操盘策略：A级抓二波起爆、B级看助攻套利、C级坚决回避防A杀。"
    },
    "rule_execution": {
        "name": "规则四 · 分时执行与风控熔断规则 (Actionable Triggers)",
        "tag": "操盘军规",
        "color": "#e3b341",
        "phases": [
            ("09:25 集合竞价", "观察核心标的量比是否 > 1.5，红开 +1% ~ +3%，且同板块无跌停开盘"),
            ("09:35 开盘前10分", "分时回踩均价线获得支撑，核心中军出现万手级主动性买单推升"),
            ("10:00 盘中确认", "板块内出现至少 2 只连板或首板涨停助攻，确认板块集群共振"),
            ("风控熔断底线", "分时放量跌穿昨日收盘价超过 2% 且 3 分钟不回升，或高标炸板跳水，立即放弃/止损离场"),
        ],
        "rationale": "不把研究名单当买入池；买点必须由盘中客观分时信号触发，不达标坚决空仓防守。"
    }
}

# ==============================================================================
# 二、当前真实市场板块回调跟踪数据 (Current Market Snapshots 2026-09-28/29)
# ==============================================================================

CURRENT_PULLBACK_CANDIDATES = [
    {
        "sector_name": "AI算力硬件 / CPO / PCB",
        "status_tag": "放量主升后中级回调一笔 (缩量42% · 回踩20日线)",
        "health_level": "Level A",
        "health_color": "#3fb950",
        "impulse_stats": "近15日前期大涨 +28.5%，RS=138%，成交额曾超百亿创新高",
        "pullback_stats": "高位回撤 -7.8%，量能萎缩 42%，当前精准依托 20 日均线支撑",
        "core_stocks": "中际旭创 (300308)、新易盛 (300502)、澳弘电子 (605058)、工业富联 (601138)",
        "matched_tactics": "战法 G (严重异动监管与滑窗二波战法) · 人气容量龙趋势承接律",
        "tactic_analysis": "前期加速段逼近 10 天 100% 严重异动红线，当前缩量一笔回调是主力主动控速压制涨幅，等待 T+10 监管窗口滑窗出清，重获 +100% 额度二次起爆。",
        "action_conclusion": "【节前防守观望 · 节后跟踪滑窗二波】",
        "action_desc": "长假前受资金提现影响暂不左侧抄底；重点监控 20 日线中枢支撑，待节后出现单日放量阳线与异动额度释放后，右侧启动二波主升试错。"
    },
    {
        "sector_name": "传媒 / 短剧与AI应用",
        "status_tag": "高位决裂分化 (新华传媒 5板独苗 vs 新华文轩 -8.99% 断板)",
        "health_level": "Level B",
        "health_color": "#d29922",
        "impulse_stats": "近10日传媒指数放量反弹 +12.3%，走出 5 板高度空间龙",
        "pullback_stats": "高标决裂分化，败者放量单边大跌，题材内部缺乏集群梯队",
        "core_stocks": "新华传媒 (600825 · 5板活口)、新华文轩 (601811 · 断板大跌)、天威视讯 (002238 · 跌停)",
        "matched_tactics": "战法 A (龙头高度突破战法) vs 战法 B (断板反包幻觉律) · 双子星卡位生死律",
        "tactic_analysis": "传媒双雄彻底决裂：新华文轩放量大跌，量化统计断板次日反包涨停率仅 7.6%，严禁抄底；新华传媒成为全市场唯一 5 板独苗，属于孤木难支的极限试压期。",
        "action_conclusion": "【新华文轩拉黑名单 · 新华传媒极苛刻试错】",
        "action_desc": "新华文轩今日断板明日冲高一律视为主力自救诱多，坚决不碰；新华传媒仅在 9:25 竞价成交 > 1.2 亿、红开 +2%~+5% 且换手充分时才可轻仓试错，炸板超 3 分钟立即止损。"
    },
    {
        "sector_name": "人形机器人 / 汽车零部件",
        "status_tag": "低位逆市抗跌防守分支 (雪龙集团 3板 · 襄阳轴承 2板)",
        "health_level": "Level B",
        "health_color": "#d29922",
        "impulse_stats": "近5日逆市启动，机器人概念异动扩散，连板梯队走出 2 板、3 板",
        "pullback_stats": "面临 09-28 全市场 56 家跌停退潮，个股逆势分歧换手封板",
        "core_stocks": "雪龙集团 (603949 · 3板)、襄阳轴承 (000678 · 2板)、大业股份 (603278 · 2板)",
        "matched_tactics": "战法 C (模仿补涨与板块扩散战法) · 退潮期假冲天炮出逃律",
        "tactic_analysis": "高标退潮断层时，低位标的承接避险资金打造穿越预期（雪龙集团被游资吹为跨节龙）。但板块缺乏全域集群涨停共振，且面临节前最后两日提现抛压大考。",
        "action_conclusion": "【身位先锋轻仓试错 · 无助攻坚决不追】",
        "action_desc": "雪龙集团 3 进 4 作为身位卡位观察，但开仓前提是早盘必须有至少 1 只首板小弟助攻涨停；若早盘无脑大单顶一字板坚决不排单，谨防天地板大面，仓位上限严格控制在 1 成以内。"
    },
    {
        "sector_name": "高位破位断板踩踏群 (纺织/地产/软件)",
        "status_tag": "极端退潮批量一字跌停 (泰慕士/华远控股/南威软件)",
        "health_level": "Level C",
        "health_color": "#f85149",
        "impulse_stats": "前期走出 3~4 连板中位加速，获利盘丰厚",
        "pullback_stats": "昨日连板今日竞价直接核按钮，收盘巨量大单焊死一字跌停",
        "core_stocks": "泰慕士 (001234 · 跌停)、天威视讯 (002238 · 跌停)、华远控股 (600743 · 跌停)、集泰股份 (002909 · 跌停)",
        "matched_tactics": "战法 E (接力换手防守与退潮空间塌陷战法) · 监管异动红线踩踏律",
        "tactic_analysis": "高位最高板一旦断板，全市场接力高度产生断崖式暴跌，中位股批量无量闷杀，属于情绪退潮的第一阶段集中出清期，具有极强的负反馈杀伤力。",
        "action_conclusion": "【P-Black 绝对禁买黑名单 · 严禁接飞刀】",
        "action_desc": "一字跌停标的坚决不抄底、不搏地天板、不博首阴反包；凡天威视讯、泰慕士等继续大单焊死跌停，全天执行 0 仓位防守熔断！"
    }
]

# ==============================================================================
# 三、HTML 面板渲染器 (Panel Renderer)
# ==============================================================================

def render_pullback_tracker_panel(
    report_date: str = "2026-09-28",
    candidates: list[dict] | None = None,
    rules: dict | None = None
) -> str:
    """渲染【强势板块·放量主升回调一笔·量化战法跟踪作战室】HTML代码片段."""
    items = candidates or CURRENT_PULLBACK_CANDIDATES
    r = rules or PULLBACK_QUANT_RULES

    # 1. 规则卡片渲染
    rule_blocks = []
    
    # 规则一
    r1 = r.get("rule_impulse", {})
    r1_metrics = "".join(f"<div style='margin-bottom:3px'><b style='color:#c9d1d9'>{escape(k)}：</b><span style='color:#8b949e'>{escape(v)}</span></div>" for k, v in r1.get("metrics", []))
    rule_blocks.append(f"""
    <div style='background:#1c2128;border:1px solid #30363d;border-left:4px solid {r1.get("color")};border-radius:8px;padding:12px 14px;'>
      <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;'>
        <b style='font-size:13.5px;color:#f0f6fc;'>{escape(r1.get("name", ""))}</b>
        <span style='font-size:11px;padding:2px 8px;border-radius:10px;background:rgba(63,185,80,0.12);color:{r1.get("color")};border:1px solid {r1.get("color")};'>{escape(r1.get("tag", ""))}</span>
      </div>
      <div style='font-size:12px;line-height:1.5;margin-bottom:8px;'>{r1_metrics}</div>
      <div style='font-size:11.5px;color:#8b949e;background:rgba(0,0,0,0.25);padding:6px 8px;border-radius:4px;border-left:2px solid {r1.get("color")};'>
        💡 <b>量化机理：</b>{escape(r1.get("rationale", ""))}
      </div>
    </div>
    """)

    # 规则二
    r2 = r.get("rule_pullback", {})
    r2_metrics = "".join(f"<div style='margin-bottom:3px'><b style='color:#c9d1d9'>{escape(k)}：</b><span style='color:#8b949e'>{escape(v)}</span></div>" for k, v in r2.get("metrics", []))
    rule_blocks.append(f"""
    <div style='background:#1c2128;border:1px solid #30363d;border-left:4px solid {r2.get("color")};border-radius:8px;padding:12px 14px;'>
      <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;'>
        <b style='font-size:13.5px;color:#f0f6fc;'>{escape(r2.get("name", ""))}</b>
        <span style='font-size:11px;padding:2px 8px;border-radius:10px;background:rgba(88,166,255,0.12);color:{r2.get("color")};border:1px solid {r2.get("color")};'>{escape(r2.get("tag", ""))}</span>
      </div>
      <div style='font-size:12px;line-height:1.5;margin-bottom:8px;'>{r2_metrics}</div>
      <div style='font-size:11.5px;color:#8b949e;background:rgba(0,0,0,0.25);padding:6px 8px;border-radius:4px;border-left:2px solid {r2.get("color")};'>
        💡 <b>量化机理：</b>{escape(r2.get("rationale", ""))}
      </div>
    </div>
    """)

    # 规则三
    r3 = r.get("rule_health_levels", {})
    r3_levels = "".join(f"<div style='margin-bottom:4px;font-size:11.5px;'><span style='color:{lv.get('color')};font-weight:700;'>[{escape(lv.get('level'))}] {escape(lv.get('name'))}</span>：<span style='color:#c9d1d9'>{escape(lv.get('condition'))}</span></div>" for lv in r3.get("levels", []))
    rule_blocks.append(f"""
    <div style='background:#1c2128;border:1px solid #30363d;border-left:4px solid {r3.get("color")};border-radius:8px;padding:12px 14px;'>
      <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;'>
        <b style='font-size:13.5px;color:#f0f6fc;'>{escape(r3.get("name", ""))}</b>
        <span style='font-size:11px;padding:2px 8px;border-radius:10px;background:rgba(188,140,255,0.12);color:{r3.get("color")};border:1px solid {r3.get("color")};'>{escape(r3.get("tag", ""))}</span>
      </div>
      <div style='line-height:1.5;margin-bottom:8px;'>{r3_levels}</div>
      <div style='font-size:11.5px;color:#8b949e;background:rgba(0,0,0,0.25);padding:6px 8px;border-radius:4px;border-left:2px solid {r3.get("color")};'>
        💡 <b>量化机理：</b>{escape(r3.get("rationale", ""))}
      </div>
    </div>
    """)

    # 规则四
    r4 = r.get("rule_execution", {})
    r4_phases = "".join(f"<div style='margin-bottom:3px'><b style='color:#e3b341'>{escape(k)}：</b><span style='color:#c9d1d9'>{escape(v)}</span></div>" for k, v in r4.get("phases", []))
    rule_blocks.append(f"""
    <div style='background:#1c2128;border:1px solid #30363d;border-left:4px solid {r4.get("color")};border-radius:8px;padding:12px 14px;'>
      <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;'>
        <b style='font-size:13.5px;color:#f0f6fc;'>{escape(r4.get("name", ""))}</b>
        <span style='font-size:11px;padding:2px 8px;border-radius:10px;background:rgba(227,179,65,0.12);color:{r4.get("color")};border:1px solid {r4.get("color")};'>{escape(r4.get("tag", ""))}</span>
      </div>
      <div style='font-size:12px;line-height:1.5;margin-bottom:8px;'>{r4_phases}</div>
      <div style='font-size:11.5px;color:#8b949e;background:rgba(0,0,0,0.25);padding:6px 8px;border-radius:4px;border-left:2px solid {r4.get("color")};'>
        💡 <b>量化机理：</b>{escape(r4.get("rationale", ""))}
      </div>
    </div>
    """)

    rules_grid_html = "".join(rule_blocks)

    # 2. 板块现状与战法跟踪卡片渲染
    candidate_cards = []
    for item in items:
        s_name = escape(item.get("sector_name", ""))
        s_tag = escape(item.get("status_tag", ""))
        h_lvl = escape(item.get("health_level", ""))
        h_clr = item.get("health_color") or "#58a6ff"
        imp_st = escape(item.get("impulse_stats", ""))
        pb_st = escape(item.get("pullback_stats", ""))
        core_st = escape(item.get("core_stocks", ""))
        match_tac = escape(item.get("matched_tactics", ""))
        tac_ana = escape(item.get("tactic_analysis", ""))
        act_conc = escape(item.get("action_conclusion", ""))
        act_desc = escape(item.get("action_desc", ""))

        candidate_cards.append(f"""
        <div style='background:#161b22;border:1px solid #30363d;border-left:4px solid {h_clr};border-radius:10px;padding:16px 18px;margin-bottom:14px;box-shadow:0 3px 10px rgba(0,0,0,0.2);'>
          <div style='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px;'>
            <div style='display:flex;align-items:center;gap:10px;'>
              <b style='font-size:15px;color:#f0f6fc;'>{s_name}</b>
              <span style='font-size:11px;color:{h_clr};background:rgba(255,255,255,0.06);border:1px solid {h_clr};padding:2px 8px;border-radius:10px;font-weight:700;'>{h_lvl}</span>
            </div>
            <div style='font-size:12px;color:#8b949e;'>{s_tag}</div>
          </div>
          
          <div style='display:grid;grid-template-columns:repeat(auto-fit, minmax(260px, 1fr));gap:10px;background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:10px 12px;margin-bottom:10px;font-size:12px;'>
            <div><b style='color:#58a6ff;'>主升爆发指标：</b><span style='color:#c9d1d9;'>{imp_st}</span></div>
            <div><b style='color:#e3b341;'>回调一笔特征：</b><span style='color:#c9d1d9;'>{pb_st}</span></div>
            <div style='grid-column: 1 / -1;'><b style='color:#bc8cff;'>核心监控标的：</b><span style='color:#f0f6fc;font-weight:600;'>{core_st}</span></div>
          </div>

          <div style='font-size:12.5px;color:#c9d1d9;line-height:1.6;margin-bottom:10px;'>
            <b style='color:{h_clr};'>战法结合映射：</b>{match_tac}<br>
            <span style='color:#8b949e;'>深度机理解析：</span>{tac_ana}
          </div>

          <div style='background:rgba(0,0,0,0.3);border:1px solid #21262d;border-left:3px solid {h_clr};border-radius:6px;padding:10px 12px;'>
            <div style='display:flex;align-items:center;gap:8px;margin-bottom:4px;'>
              <b style='font-size:13px;color:{h_clr};'>{act_conc}</b>
            </div>
            <div style='font-size:12px;color:#c9d1d9;line-height:1.5;'>{act_desc}</div>
          </div>
        </div>
        """)

    candidates_html = "".join(candidate_cards)

    return f"""
    <!-- 强势板块放量上涨后·回调一笔量化跟踪面板 -->
    <section class="pullback-tactic-tracker" id="sec-pullback-tracker" style="margin:26px 0 32px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;">
      
      <!-- 标题栏 -->
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:14px;border-bottom:1px solid #30363d;padding-bottom:10px;">
        <div style="display:flex;align-items:center;gap:8px;">
          <h2 class="section-title" style="margin:0;font-size:18px;color:#f0f6fc;display:flex;align-items:center;gap:8px;">
            <span>🌊 强势板块·放量主升回调一笔·量化战法跟踪作战室</span>
          </h2>
          <span style="font-size:11.5px;background:rgba(56,139,253,0.15);color:#58a6ff;border:1px solid rgba(56,139,253,0.3);padding:2px 8px;border-radius:12px;font-weight:600;">
            二波蓄势 × 缩量鉴别 × 战法结合
          </span>
        </div>
        <div style="font-size:12px;color:#8b949e;">基于 {escape(report_date)} 真实数据 · 严守四维量化红线</div>
      </div>

      <!-- 四大量化规则展示卡片 -->
      <div style="margin-bottom:18px;">
        <div style="font-size:13px;font-weight:700;color:#58a6ff;margin-bottom:8px;display:flex;align-items:center;gap:6px;">
          <span>📐 固化四大定量规则标准 (Fixed Quantitative Standards)</span>
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(280px, 1fr));gap:12px;">
          {rules_grid_html}
        </div>
      </div>

      <!-- 当前真实板块跟踪与战法结合对账 -->
      <div>
        <div style="font-size:13px;font-weight:700;color:#e3b341;margin-bottom:10px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px;">
          <span>🎯 现状个股板块与战法结合实战对账池 (Current Market Focus &amp; Action Conclusions)</span>
          <span style="font-size:11.5px;color:#8b949e;font-weight:normal;">严格区分【待观察】与【交易候选】· 无右侧触发绝不进场</span>
        </div>
        <div>
          {candidates_html}
        </div>
      </div>

    </section>
    """
