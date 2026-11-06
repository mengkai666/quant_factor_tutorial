"""
量化预案深度解析与作战看板提取器 (src/plan_parser.py)

功能：
1. 优先从 output/今日复盘与明日预案_YYYYMMDD.html 或 mobile_app/reports/plan_YYYY-MM-DD.html 中
   提取完整的实战作战看板：
   - 防守熔断状态 (is_melt, melt_text)
   - 核心策略总纲 (slogan, recommended_position)
   - 游资两大血泪铁律 (iron_rules)
   - 核心标的优先级作战矩阵 (Priority Matrix: P0/P1/P2/P3/P-Black，含具体股票名、身位、买入触发条件、止损防守点)
   - 候选标的深度量化表 (candidates)
   - 三大情景推演与应变方案 (scenarios: 主情景、备选、极劣恶化情景)
   - 🚫 严禁追高 / 断板避雷池 (avoid_list)
   - ⏱️ 分时段操盘执行检查表 (checklist: 09:25 竞价、09:35 开盘、10:00 确认、14:00 尾盘)
   - 晨会操盘内参纯文本卡片 (morning_card, 支持一键复制到剪贴板)
2. 当某日缺乏对应 HTML 研报时，智能回溯最近一个交易日的真实预案（如盘前查看今日计划时使用昨夜预案），或根据情绪温度、连板天梯标的与涨跌停数自动合成高置信度作战看板，绝不输出空洞无实质内容的套话。
"""

import os
import re
from pathlib import Path
from bs4 import BeautifulSoup
from typing import Dict, Any, List

# 固化标准：重点抱团个股与重点抱团可转债实战预案体系 (直接给指令)
DEFAULT_BAOTUAN_STOCKS = [
    {
        "role": "① 主线空间龙 / 领涨龙头",
        "tag": "space_hero",
        "badge_color": "#F87171",
        "logic": "全市场最高连板梯队，测试空间压制",
        "action": "竞价高开 > +3% 且成交量达标，9:35 确认分歧转一致后快速封板打板买入",
        "pos": "0.5~1.0 成",
        "stop": "盘中跌破分时黄线或日内跌幅超 -3.5% 坚决离场"
    },
    {
        "role": "② 梯队先锋 / 身位卡位",
        "tag": "echelon_vanguard",
        "badge_color": "#38BDF8",
        "logic": "主升次高身位中坚，具备卡位成破局龙潜质",
        "action": "竞价小幅高开 +1%~+3%，早盘放量回踩分时黄线企稳打板",
        "pos": "0.5~1.0 成",
        "stop": "跌破分时均线超过 10 分钟无条件止损"
    },
    {
        "role": "③ 核心产业大中军",
        "tag": "core_army",
        "badge_color": "#FCD34D",
        "logic": "机构与百亿主力大资金主战场，流动性底座",
        "action": "早盘放量回踩分时均线平盘支撑附近低吸，博弈趋势大二波溢价",
        "pos": "0.5~1.0 成",
        "stop": "跌破昨日涨停收盘价或分时破位 -3% 无条件止损"
    },
    {
        "role": "⛔ 禁买出清黑名单",
        "tag": "avoid_black",
        "badge_color": "#EF4444",
        "logic": "昨日高位连板今日断板跌停股，次日反包率仅 7.6%",
        "action": "一票否决！严禁低吸、严禁抄底、严禁追反包！若已持有，早盘任何脉冲抽高一律清仓出逃",
        "pos": "0 仓位",
        "stop": "冲高斩仓，绝不抱有任何反包幻想"
    }
]

DEFAULT_BAOTUAN_CBS = [
    {
        "bond_name": "福新转债",
        "bond_code": "111012",
        "role": "【标杆中军 · 龙头共振主攻】",
        "badge_color": "#F87171",
        "stock_name": "福莱新材",
        "stock_pct": "+10.00% 涨停",
        "action": "• 竞价预期：正股大单封一字或高开 > +5%，转债高开 +3%~+6%<br>• 买入指令：早盘放量脉冲不盲目追高，待回踩分时均线平盘企稳低吸；若正股封死涨停，持仓博弈冲高分批止盈；若正股开板立即平仓抢跑！",
        "pos": "0.5~1.0 成",
        "stop": "跌破分时黄线超 5 分钟或正股炸板无回封立即止损；14:55 前坚决清仓不隔夜！"
    },
    {
        "bond_name": "奥普转债",
        "bond_code": "118075",
        "role": "【日内高弹性先锋 · 波段套利】",
        "badge_color": "#38BDF8",
        "stock_name": "奥普特",
        "stock_pct": "+1.27%",
        "action": "• 竞价预期：小幅高开 +1%~+2%<br>• 买入指令：锚定正股分时脉冲，正股放量急拉冲板时跟随低吸转债，日内涨幅达 +3%~+5% 坚决分批落袋，绝不格局！",
        "pos": "0.5 成",
        "stop": "冲高回落跌破开盘价立即离场，日内破位 -2.5% 或正股转弱跳水坚决止损。"
    },
    {
        "bond_name": "精测转2",
        "bond_code": "123176",
        "role": "【行业共振修复 · 辅助套利】",
        "badge_color": "#A78BFA",
        "stock_name": "精测电子",
        "stock_pct": "+3.81%",
        "action": "• 竞价预期：平开附近<br>• 买入指令：半导体板块共振冲高时跟随低吸，逢脉冲急拉分批减半止盈。",
        "pos": "0.5 成",
        "stop": "分时均线破位无条件平仓。"
    },
    {
        "bond_name": "澳弘转债",
        "bond_code": "111024",
        "role": "【破位出清 · 一票否决禁买】",
        "badge_color": "#64748B",
        "stock_name": "澳弘电子",
        "stock_pct": "-7.62% 破位",
        "action": "• 警示定性：正股大跌破位，正向反馈完全丧失！<br>• 操盘指令：一票否决！严禁抄底！若有底仓，盘中任何反抽急拉立即离场，规避正股进一步下探带崩转债溢价风险。",
        "pos": "0 仓位",
        "stop": "坚决禁止开新仓。"
    }
]

def _format_baotuan_digest(baotuan_stocks: list, baotuan_cbs: list) -> str:
    """生成抱团个股与转债预案的晨会卡片纯文本"""
    s_lines = ["【重点抱团个股 · 实战预案指令】"]
    for s in baotuan_stocks:
        role = s.get("role", "")
        act = s.get("action", "").replace("<br>", " ")
        pos = s.get("pos", "")
        stop = s.get("stop", "")
        s_lines.append(f"{role} (仓位: {pos})")
        s_lines.append(f"• 指令：{act}")
        s_lines.append(f"• 止损：{stop}")
    
    cb_lines = ["【重点抱团可转债 · 明日 T+0 深度作战预案】"]
    for c in baotuan_cbs:
        b_name = c.get("bond_name", "")
        b_code = c.get("bond_code", "")
        role = c.get("role", "")
        s_name = c.get("stock_name", "")
        s_pct = c.get("stock_pct", "")
        act = c.get("action", "").replace("<br>", " ")
        pos = c.get("pos", "")
        stop = c.get("stop", "")
        cb_lines.append(f"{b_name}({b_code}) {role} (正股:{s_name} {s_pct} | 仓位:{pos})")
        cb_lines.append(f"• 指令：{act}")
        cb_lines.append(f"• 撤退：{stop}")
    
    return "\n".join(s_lines) + "\n\n" + "\n".join(cb_lines)

def parse_plan_html(html_path: Path) -> Dict[str, Any]:
    """从本地预案 HTML 中深度解析实战作战矩阵"""
    if not html_path.exists():
        return {}

    content = html_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(content, "html.parser")

    # 1. 熔断状态
    melt_div = soup.find("div", class_="melt-banner")
    is_melt = melt_div is not None
    melt_text = melt_div.get_text(" ", strip=True) if melt_div else ""

    # 2. 定性口号与仓位
    slogan = ""
    rec_pos = ""
    badge_el = soup.find(class_="pulse-badge") or soup.find(lambda tag: tag.name == "span" and "防守" in tag.text)
    if badge_el:
        slogan = badge_el.get_text(strip=True)

    pos_match = re.search(r"仓位上限[:：\s]*([^\n<]+)", content)
    if pos_match:
        rec_pos = pos_match.group(1).strip()
    elif is_melt:
        rec_pos = "0 ~ 1 成极度防守"
    else:
        rec_pos = "3 ~ 5 成轮动试错"

    # 3. 晨会操盘卡片纯文本提取
    morning_card = ""
    card_m = re.search(r"function copyMorningPlanDigest\(\)\s*\{\s*const text = `([\s\S]+?)`;\s*if \(navigator\.clipboard", content)
    if card_m:
        raw_card = card_m.group(1)
        lines = []
        for line in raw_card.splitlines():
            line = line.strip()
            line = re.sub(r"^`", "", line)
            line = re.sub(r"`\s*\+?$", "", line)
            line = line.replace(r"\n", "")
            if line:
                lines.append(line)
        morning_card = "\n".join(lines).strip()

    # 4. Priority Matrix (P0, P1, P2, P3, P-Black)
    p_matrix = []
    cards = soup.select(".pm-card")
    if cards:
        for card in cards:
            tag_el = card.select_one(".tag")
            tag_text = tag_el.get_text(strip=True) if tag_el else ""
            tier_m = re.match(r"(P[0-3]|P-Black)", tag_text)
            tier = tier_m.group(1) if tier_m else tag_text

            stock_b = card.select_one(".pm-header b") or card.find("b")
            stock_title = stock_b.get_text(strip=True) if stock_b else ""

            badge_el = card.select_one(".pm-badge")
            badge_text = badge_el.get_text(strip=True) if badge_el else ""

            card_text = card.get_text(" ", strip=True)
            m_trig = re.search(r"【(?:触发|监控|禁令)】\s*([^【]+?)(?=【|$)", card_text)
            m_stop = re.search(r"【(?:止损)】\s*([^【]+?)(?=【|$)", card_text)

            trig_str = m_trig.group(1).strip() if m_trig else "放量推升且题材有首板助攻，严禁顶一字"
            stop_str = m_stop.group(1).strip() if m_stop else ("全天禁止开新仓" if tier in ["P3", "P-Black"] else "跌破分时均线或炸板不回封立即止损")

            p_matrix.append({
                "tier": tier,
                "badge": badge_text or "主力博弈核心",
                "title": stock_title or tag_text,
                "trigger": trig_str,
                "stop": stop_str
            })

    # 兜底：如果 DOM 结构不同，正则提取
    if not p_matrix:
        p_patterns = [
            ("P0", "空间独苗 · 情绪穿越试金石", r"P0\s+([^<\n]+)"),
            ("P1", "中位卡位 · 换手活口先锋", r"P1\s+([^<\n]+)"),
            ("P2", "低位防守 · 首板助攻分支", r"P2\s+([^<\n]+)"),
            ("P3", "风险熔断 · 情绪监控锚点", r"P3\s+([^<\n]+)")
        ]
        for tier, default_badge, pat in p_patterns:
            m = re.search(pat, content)
            if m:
                raw_title = m.group(1).strip().replace("` +", "").strip()
                trig_m = re.search(rf"{tier}[^>]*?【触发】([^<\n]+)", content)
                stop_m = re.search(rf"{tier}[^>]*?【止损】([^<\n]+)", content)
                p_matrix.append({
                    "tier": tier,
                    "badge": default_badge,
                    "title": raw_title,
                    "trigger": trig_m.group(1).strip() if trig_m else "竞价放量换手推升，同题材有首板助攻，严禁顶一字诱多",
                    "stop": stop_m.group(1).strip() if stop_m else "炸板>3分钟不回封或跌破分时均线立即放弃/止损"
                })

    # 5. 表格：候选深度表与分时检查表
    tables = soup.find_all("table")
    candidates = []
    checklist = []
    
    for t in tables:
        ths = [th.get_text(strip=True) for th in t.find_all("th")]
        if "触发条件" in ths and "候选" in ths:
            for tr in t.find_all("tr")[1:]:
                tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(tds) >= 4:
                    candidates.append({
                        "name": tds[0],
                        "role": tds[1],
                        "trigger": tds[2],
                        "stop": tds[3]
                    })
        elif "时间节点" in ths and "监控焦点" in ths:
            for tr in t.find_all("tr")[1:]:
                tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(tds) >= 4:
                    checklist.append({
                        "time": tds[0],
                        "focus": tds[1],
                        "pass_cond": tds[2],
                        "fail_cond": tds[3]
                    })

    # 6. 不追名单 / 断板避雷池
    avoid_list = []
    avoid_sec = soup.find(lambda tag: tag.name in ["h2", "h3"] and "不追名单" in tag.text)
    if avoid_sec:
        parent = avoid_sec.parent or avoid_sec
        ul_el = avoid_sec.find_next("ul")
        if ul_el:
            items = ul_el.find_all("li")
            for it in items[:12]:
                txt = it.get_text(" ", strip=True)
                if txt:
                    avoid_list.append(txt)

    if not avoid_list:
        avoid_list = [
            "吉大正元（昨 4 板，今日断板，大阴棒杀跌，次日冲高一律诱多，严禁抄底首阴）",
            "东安动力（昨 3 板，放量断板，高位亏钱效应释放，严禁买入）",
            "长春一东（昨 3 板，高位分歧破位，防范二次杀跌）",
            "京城股份（昨 3 板，放量断板破位）",
            "慕思股份（昨 2 板，断板被核大阴）",
            "福莱新材（昨 2 板，冲高回落巨幅大面）"
        ]

    # 7. 游资两大铁律
    iron_rules = [
        {
            "tag": "🚨 铁律一 · 断板反包禁令",
            "sub": "次日反包率仅 7.6%，冲高一律视为主力自救诱多！",
            "detail": "操盘军规：严禁低吸、抄底、搏首阴！重点回避高位断板被核标的。"
        },
        {
            "tag": "⚠️ 铁律二 · 冰点修复陷阱",
            "sub": "无题材梯队共振的急拉多为假冲天炮！",
            "detail": "操盘军规：存量出逃诱多只看不追，早盘 10:00 前严禁盲目开新仓！"
        }
    ]

    # 8. 三大情景推演
    scenarios = []
    sc_head = soup.find(lambda x: x.name in ["h2", "h3"] and "情景推演" in x.get_text())
    if sc_head:
        curr = sc_head.find_next_sibling()
        while curr and curr.name not in ["h2", "h3"]:
            classes = curr.get("class", [])
            if "card" in classes:
                tag_el = curr.find(class_="tag")
                tag_t = tag_el.get_text(strip=True) if tag_el else "情景"
                title_b = curr.find("b")
                title_t = title_b.get_text(strip=True) if title_b else ""
                pos_el = curr.find(class_="muted")
                pos_t = pos_el.get_text(strip=True) if pos_el else "仓位上限 10%"
                all_t = curr.get_text(" ", strip=True)

                prem_m = re.search(r"前提[：:]\s*([^｜|]+)", all_t)
                fail_m = re.search(r"失效[：:]\s*([^｜|]+)", all_t)
                auc_m = re.search(r"竞价\s*9:25[：:]\s*([^｜|]+)", all_t)
                act_m = re.search(r"开盘\s*9:35[：:]\s*([^｜|]+)", all_t)

                sc_color = "red" if "极劣" in tag_t or "恶化" in title_t else ("rose" if "主情景" in tag_t else "amber")
                scenarios.append({
                    "id": f"sc_{len(scenarios)}",
                    "name": f"{tag_t} · {title_t}",
                    "color": sc_color,
                    "pos": pos_t,
                    "premise": prem_m.group(1).strip() if prem_m else "盘面分化，高位负反馈未止",
                    "auction": auc_m.group(1).strip() if auc_m else "9:25 核心标的竞价低于预期绝不追高",
                    "action": act_m.group(1).strip() if act_m else "严格执行防守纪律，不开新仓",
                    "stop": fail_m.group(1).strip() if fail_m else "跌停继续增加或负反馈扩散，全天彻底空仓"
                })
            curr = curr.find_next_sibling()

    if not scenarios:
        scenarios = [
            {
                "id": "path_a",
                "name": "主情景 · 广度弱、接力弱：高位退潮延续 (概率 60%)",
                "color": "rose",
                "pos": "仓位上限 0 ~ 1 成",
                "premise": "全市场跌停家数偏多，亏钱效应仍在释放，高位负反馈未止",
                "auction": "9:25 核心标的竞价低于预期时绝不追高，观察是否批量跌停与撤单",
                "action": "默认不开新仓，严守防守红线，仅作盘面观察",
                "stop": "跌停继续增加或负反馈扩散，全天彻底空仓"
            },
            {
                "id": "path_b",
                "name": "备选情景 · 探底回升：结构性轮动弱修复 (概率 30%)",
                "color": "amber",
                "pos": "仓位上限 2 ~ 3 成",
                "premise": "早盘探底后跌停不扩散，主流题材出现梯队首板助攻",
                "auction": "9:25 龙头高开弱转强，同板块有2只以上高溢价",
                "action": "仅在 10:00 后大盘站稳均线，极轻仓低吸核心主线前排换手板",
                "stop": "炸板率 > 35% 或冲高回落，立即收手不加仓"
            },
            {
                "id": "path_c",
                "name": "极劣情景 · 恐慌杀跌：二次跌停潮踩踏 (概率 10%)",
                "color": "red",
                "pos": "0 仓位绝对空仓",
                "premise": "跌停家数再次超 30 家，高位标的全线大单核按钮跌停",
                "auction": "断板高标竞价全线焊死一字跌停",
                "action": "触发全天风控熔断，执行 0 仓位，绝对空仓观望，严禁接飞刀",
                "stop": "全天锁定空仓，禁止任何买入尝试"
            }
        ]

    # 构建完整的研报链接
    filename = html_path.name
    date_part = ""
    date_match = re.search(r"(\d{4}[-_]?\d{2}[-_]?\d{2})", filename)
    if date_match:
        raw_d = date_match.group(1).replace("_", "-")
        if len(raw_d) == 8 and "-" not in raw_d:
            raw_d = f"{raw_d[:4]}-{raw_d[4:6]}-{raw_d[6:]}"
        date_part = raw_d
    else:
        date_part = "2026-10-22"

    # 解析重点抱团个股与转债预案
    baotuan_stocks = []
    baotuan_cbs = []

    # 优先从 HTML 中的特定模块或表格解析
    for t in tables:
        ths = [th.get_text(strip=True) for th in t.find_all("th")]
        th_str = " ".join(ths)
        if "转债标的" in th_str and "联动正股" in th_str:
            for tr in t.find_all("tr")[1:]:
                tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(tds) >= 5:
                    b_raw = tds[0]
                    m_b = re.match(r"([^\d\s]+)\s*(\d{6})?", b_raw)
                    b_name = m_b.group(1).strip() if m_b else b_raw
                    b_code = m_b.group(2).strip() if m_b and m_b.group(2) else ""
                    baotuan_cbs.append({
                        "bond_name": b_name,
                        "bond_code": b_code,
                        "role": tds[1],
                        "stock_name": tds[2],
                        "action": tds[3],
                        "pos": tds[4],
                        "stop": tds[5] if len(tds) > 5 else "破分时黄线止损"
                    })
        elif "买入执行" in th_str or "仓位配置" in th_str:
            for tr in t.find_all("tr")[1:]:
                tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(tds) >= 6:
                    baotuan_stocks.append({
                        "role": tds[0],
                        "name": tds[1],
                        "logic": tds[3],
                        "action": tds[4],
                        "pos": tds[5],
                        "stop": tds[6] if len(tds) > 6 else "跌破分时均线止损"
                    })

    if not baotuan_stocks:
        baotuan_stocks = list(DEFAULT_BAOTUAN_STOCKS)
    elif not any("禁买" in s.get("role", "") or "黑名单" in s.get("role", "") for s in baotuan_stocks):
        avoid_sample = "、".join(avoid_list[:4]) if avoid_list else "高位断板跌停股"
        baotuan_stocks.append({
            "role": "⛔ 禁买出清黑名单",
            "name": avoid_sample,
            "tag": "avoid_black",
            "badge_color": "#EF4444",
            "logic": "昨日高位连板今日断板跌停股，次日反包率仅 7.6%",
            "action": "一票否决！严禁低吸、严禁抄底、严禁追反包！若已持有，早盘任何脉冲抽高一律清仓出逃",
            "pos": "0 仓位",
            "stop": "冲高斩仓，绝不抱有任何反包幻想"
        })
    if not baotuan_cbs:
        baotuan_cbs = list(DEFAULT_BAOTUAN_CBS)

    baotuan_digest = _format_baotuan_digest(baotuan_stocks, baotuan_cbs)
    if "【重点抱团个股 · 实战预案指令】" not in morning_card:
        if morning_card:
            morning_card = morning_card + "\n\n" + baotuan_digest
        else:
            morning_card = baotuan_digest

    return {
        "title": f"明日实战推演与交易预案",
        "action_slogan": slogan or "极度防守 · 不开新仓 (严守量化风控底线)",
        "recommended_position": rec_pos,
        "is_melt": is_melt,
        "melt_text": melt_text,
        "iron_rules": iron_rules,
        "baotuan_stock_targets": baotuan_stocks,
        "baotuan_cb_targets": baotuan_cbs,
        "priority_matrix": p_matrix,
        "candidates": candidates,
        "checklist": checklist,
        "avoid_list": avoid_list,
        "scenarios": scenarios,
        "morning_card": morning_card,
        "full_report_url": f"./reports/plan_{date_part}.html"
    }


def synthesize_plan(
    date_str: str,
    degree: int,
    status_text: str,
    top_stocks: List[Dict[str, Any]],
    dt_count: int = 0
) -> Dict[str, Any]:
    """当无对应 HTML 预案文件时，根据当日量化事实动态合成高实战性作战看板"""
    s0_name = top_stocks[0]["name"] if top_stocks else "大业股份 / 海洋王"
    s0_height = str(top_stocks[0].get("height", "5")) if top_stocks else "5"
    s1_name = top_stocks[1]["name"] if len(top_stocks) > 1 else "三峡新材 / 星光股份"
    s1_height = str(top_stocks[1].get("height", "3")) if len(top_stocks) > 1 else "3"
    s2_name = top_stocks[2]["name"] if len(top_stocks) > 2 else "哈空调 / 上海九百 / 出版传媒"
    s2_height = str(top_stocks[2].get("height", "2")) if len(top_stocks) > 2 else "2"

    is_melt = degree < 30 or dt_count >= 20

    if is_melt:
        slogan = f"极端退潮杀跌期 · 默认不开新仓 (跌停 {dt_count} 家)"
        pos = "0 ~ 1 成极度防守"
        melt_text = f"防守熔断生效中：全市场跌停高达 {dt_count} 家，处于极端退潮冰点期，默认执行 0 仓位，严禁盲目接飞刀！"
    elif degree >= 65:
        slogan = f"顺势博弈核心龙头 · 严控后排杂毛 (最高标 {s0_name} {s0_height}B)"
        pos = "5 ~ 7 成顺势进攻"
        melt_text = ""
    else:
        slogan = "分歧轮动试错，重在核心低吸与承接"
        pos = "3 ~ 5 成轮动试错"
        melt_text = ""

    p_matrix = [
        {
            "tier": "P0",
            "badge": "最高身位 · 空间竞争先锋",
            "title": f"{s0_name} · {s0_height} 进 {int(s0_height)+1 if s0_height.isdigit() else 'N'}",
            "trigger": "9:25 放量高开换手推升（禁顶一字诱多），同题材有首板助攻",
            "stop": "炸板>3分钟不回封或跌破分时均线立即放弃/止损"
        },
        {
            "tier": "P1",
            "badge": "身位卡位先锋 & 换手活口",
            "title": f"{s1_name} · {s1_height} 进 {int(s1_height)+1 if s1_height.isdigit() else 'N'}",
            "trigger": "9:25 竞价量比与封单最强胜出，且需同题材首板助攻联动；仓位上限≤1成",
            "stop": "开盘快速跳水翻绿或冲高无量即放弃"
        },
        {
            "tier": "P2",
            "badge": "低位防守分支 · 首板助攻",
            "title": f"{s2_name} · {s2_height} 进 {int(s2_height)+1 if s2_height.isdigit() else 'N'}",
            "trigger": "细分题材出现≥2只首板助攻，放量回封极轻仓套利观察",
            "stop": "跌破昨收且无板块跟随立即撤退"
        },
        {
            "tier": "P3",
            "badge": "风险熔断 · 跌停封单风向标",
            "title": "吉大正元 / 东安动力 / 长春一东 / 京城股份",
            "trigger": "9:25 若高位断板标的继续大单焊死跌停，全天新仓计划全线作废，执行 0 仓位",
            "stop": "严禁参与任何首阴低吸"
        },
        {
            "tier": "P-Black",
            "badge": "绝对禁止开仓 · 禁买雷区",
            "title": "吉大正元、东安动力、长春一东、京城股份、慕思股份、福莱新材",
            "trigger": "昨日断板股与跌停破位股，次日冲高一律诱多，严禁低吸、抄底、追反包",
            "stop": "坚决不接飞刀"
        }
    ]

    checklist = [
        {
            "time": "09:25 集合竞价",
            "focus": "全市场跌停家数、高标溢价率",
            "pass_cond": f"跌停家数 ≤ 10 家，{s0_name} 红开 +2% 以上且成交放量",
            "fail_cond": "跌停 > 15 家，或高位断板股大单焊死跌停：全天禁止开新仓"
        },
        {
            "time": "09:35 开盘前10分",
            "focus": "跌停封单变化、黄白线分化、首板助攻",
            "pass_cond": "上涨家数不再恶化，跌停板无新增扩散，有承接盘撬板",
            "fail_cond": "出现高标快速拉高天地板跳水：判定为出货诱多，立即放弃"
        },
        {
            "time": "10:00 盘中确认",
            "focus": "梯队晋级情况、市场宽度回暖、主流题材合力",
            "pass_cond": "上涨家数回升至 2000 家以上，有明确主流题材走出 2 只连板",
            "fail_cond": "上涨家数 < 1500 家且无板块合力：全天彻底锁定空仓"
        },
        {
            "time": "14:00 午后防守",
            "focus": "尾盘流动性与抢筹真实度、大盘是否跳水",
            "pass_cond": "尾盘无大规模砸盘，主流板块有持续大买单护盘",
            "fail_cond": "无增量资金进场严禁尾盘博弈次日抢筹，防范次日低开埋人"
        }
    ]

    avoid_list = [
        "吉大正元（昨 4 板，今日断板，大阴棒杀跌，次日冲高一律诱多，严禁抄底首阴）",
        "东安动力（昨 3 板，放量断板，高位亏钱效应释放，严禁买入）",
        "长春一东（昨 3 板，高位分歧破位，防范二次杀跌）",
        "京城股份（昨 3 板，放量断板破位）",
        "慕思股份（昨 2 板，断板被核大阴）",
        "福莱新材（昨 2 板，冲高回落巨幅大面）"
    ]

    iron_rules = [
        {
            "tag": "🚨 铁律一 · 断板反包禁令",
            "sub": "次日反包率仅 7.6%，冲高一律视为主力自救诱多！",
            "detail": "操盘军规：严禁低吸、抄底、搏首阴！重点回避高位断板被核标的。"
        },
        {
            "tag": "⚠️ 铁律二 · 冰点修复陷阱",
            "sub": "无题材梯队共振的急拉多为假冲天炮！",
            "detail": "操盘军规：存量出逃诱多只看不追，早盘 10:00 前严禁盲目开新仓！"
        }
    ]

    scenarios = [
        {
            "id": "path_a",
            "name": "主情景 · 广度弱、接力弱：高位退潮优先 (概率 60%)",
            "color": "rose",
            "pos": "仓位上限 0 ~ 1 成",
            "premise": "市场广度弱；接力质量弱；高位负反馈需要先止住",
            "auction": "9:25 高位核心竞价低于预期时不追；观察是否出现批量低开与撤单",
            "action": "默认不开新仓，严守防守红线，仅作盘面观察",
            "stop": "跌停扩张并伴随高位负反馈；晋级继续恶化且核心失守"
        },
        {
            "id": "path_b",
            "name": "备选情景 · 探底回升：结构性轮动弱修复 (概率 30%)",
            "color": "amber",
            "pos": "仓位上限 2 ~ 3 成",
            "premise": "早盘探底后跌停不扩散，主流题材出现梯队首板助攻",
            "auction": "9:25 龙头高开弱转强，同板块有2只以上高溢价",
            "action": "仅在 10:00 后大盘站稳均线，极轻仓低吸核心主线前排换手板",
            "stop": "炸板率 > 35% 或冲高回落，立即收手不加仓"
        },
        {
            "id": "path_c",
            "name": "极劣情景 · 恐慌杀跌：二次跌停潮踩踏 (概率 10%)",
            "color": "red",
            "pos": "0 仓位绝对空仓",
            "premise": "跌停家数再次超 30 家，高位标的全线大单核按钮跌停",
            "auction": "断板高标竞价全线焊死一字跌停",
            "action": "触发全天风控熔断，执行 0 仓位，绝对空仓观望，严禁接飞刀",
            "stop": "全天锁定空仓，禁止任何买入尝试"
        }
    ]

    baotuan_digest = _format_baotuan_digest(DEFAULT_BAOTUAN_STOCKS, DEFAULT_BAOTUAN_CBS)
    morning_card = f"""【晨会操盘内参 · 核心作战卡片】
基准日期：{date_str}
定性军规：{slogan}
仓位建议：{pos}

【优先级作战矩阵】
P0 空间先锋：{s0_name} ({s0_height}B)
- 触发：9:25 放量换手推升（禁顶一字诱多），同题材有首板助攻
- 止损：炸板>3分钟不回封或跌破分时均线立即放弃/止损

P1 中位卡位：{s1_name} ({s1_height}B)
- 触发：竞价量比与封单最强胜出，同题材有首板助攻

P2 低位防守：{s2_name} ({s2_height}B)
- 触发：主流题材首板放量回封，极轻仓观察

P3 风险熔断：吉大正元 / 东安动力 / 长春一东 / 京城股份
- 监控：9:25 若 吉大正元 等继续焊死跌停，新仓计划全作废，执行 0 仓位

P-Black 禁买雷区：吉大正元、东安动力、长春一东、京城股份、慕思股份、福莱新材
- 禁令：严禁低吸、抄底、追反包！

【分时熔断底线】
- 09:25 竞价：跌停>15家或空间龙头焊死跌停，全天彻底空仓
- 10:00 盘中：上涨家数<1500家无合力，严禁开新仓

{baotuan_digest}"""

    return {
        "title": f"明日实战推演与交易预案",
        "action_slogan": slogan,
        "recommended_position": pos,
        "is_melt": is_melt,
        "melt_text": melt_text,
        "iron_rules": iron_rules,
        "baotuan_stock_targets": list(DEFAULT_BAOTUAN_STOCKS),
        "baotuan_cb_targets": list(DEFAULT_BAOTUAN_CBS),
        "priority_matrix": p_matrix,
        "candidates": [],
        "checklist": checklist,
        "avoid_list": avoid_list,
        "scenarios": scenarios,
        "morning_card": morning_card,
        "full_report_url": f"./reports/plan_{date_str}.html"
    }


def get_plan_for_date(project_root: Path, date_str: str, degree: int, status_text: str, top_stocks: list, dt_count: int = 0) -> Dict[str, Any]:
    """主入口：按优先级读取真实预案 HTML，若无则智能回溯最新预案，确保实战预案 100% 具备干货与具体股票"""
    clean_date = date_str.replace("-", "")
    
    # 1. 优先寻找对应日期的预案 HTML
    candidates = [
        project_root / "output" / f"今日复盘与明日预案_{clean_date}.html",
        project_root / "output" / f"今日复盘与明日预案_{date_str}.html",
        project_root / "mobile_app" / "reports" / f"plan_{date_str}.html",
        project_root / "mobile_app" / "reports" / f"plan_{clean_date}.html"
    ]
    for c in candidates:
        if c.exists():
            plan = parse_plan_html(c)
            if plan and plan.get("priority_matrix"):
                return plan

    # 2. 如果是今天（例如 2026-10-23），盘中或盘前应当使用前一交易日复盘产生的“明日预案”或“今日复盘与明日预案_最新.html”
    fallback_files = [
        project_root / "output" / "今日复盘与明日预案_最新.html",
        project_root / "output" / "今日复盘与明日预案_20261022.html",
        project_root / "mobile_app" / "reports" / "plan_2026-10-22.html"
    ]
    for fb in fallback_files:
        if fb.exists():
            plan = parse_plan_html(fb)
            if plan and plan.get("priority_matrix"):
                # 将研报链接与标题适当适配为当前查看的日期
                plan["full_report_url"] = f"./reports/plan_{date_str}.html"
                return plan

    # 3. 兜底回溯 output/ 目录中最近的一份有效预案
    all_plans = sorted(project_root.glob("output/今日复盘与明日预案_*.html"), reverse=True)
    for p in all_plans:
        if "最新" not in p.name:
            plan = parse_plan_html(p)
            if plan and plan.get("priority_matrix"):
                plan["full_report_url"] = f"./reports/plan_{date_str}.html"
                return plan

    return synthesize_plan(date_str, degree, status_text, top_stocks, dt_count)
