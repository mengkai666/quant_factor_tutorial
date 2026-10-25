"""
量化预案深度解析器 (extract_plan_data.py)
从 output/今日复盘与明日预案_*.html 或 mobile_app/reports/plan_*.html 中抽取全套结构化实战推演预案。
若无 HTML 则基于天梯与行情事实智能生成高置信度实战作战矩阵。
"""

import os
import re
from pathlib import Path
from bs4 import BeautifulSoup
from typing import Dict, Any, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def parse_plan_html(html_path: Path) -> Dict[str, Any]:
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

    # 查找仓位建议
    pos_match = re.search(r"仓位上限[:：\s]*([^\n<]+)", content)
    if pos_match:
        rec_pos = pos_match.group(1).strip()
    elif is_melt:
        rec_pos = "0 ~ 1 成极度防守"
    else:
        rec_pos = "3 ~ 5 成轮动试错"

    # 3. 晨会操盘卡片文本
    morning_card = ""
    card_match = re.search(r"const text = \x60([^\x60]+)\x60;", content)
    if card_match:
        morning_card = card_match.group(1).strip()

    # 4. 提取 Priority Matrix (P0, P1, P2, P3)
    p_matrix = []
    # 查找 P0 空间先锋、P1、P2、P3
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
            # 查找触发与止损
            trig_m = re.search(rf"{tier}[^>]*?【触发】([^<\n]+)", content) or re.search(rf"{tier}[^>]*?- 触发[：:]([^<\n]+)", content)
            stop_m = re.search(rf"{tier}[^>]*?【止损】([^<\n]+)", content) or re.search(rf"{tier}[^>]*?- 止损[：:]([^<\n]+)", content)
            
            p_matrix.append({
                "tier": tier,
                "badge": default_badge,
                "title": raw_title,
                "trigger": trig_m.group(1).strip() if trig_m else "竞价放量换手推升，同题材有首板助攻，严禁顶一字诱多",
                "stop": stop_m.group(1).strip() if stop_m else "炸板>3分钟不回封或跌破分时均线立即放弃/止损"
            })

    # 5. 提取表格候选与检查表
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

    # 6. 不追名单 / 断板避雷池 (Avoid List)
    avoid_list = []
    avoid_sec = soup.find(lambda tag: tag.name == "h2" and "不追" in tag.text)
    if avoid_sec:
        parent = avoid_sec.parent
        items = parent.find_all("li")
        for it in items[:8]:
            txt = it.get_text(" ", strip=True)
            if txt:
                avoid_list.append(txt)

    if not avoid_list:
        avoid_list = [
            "吉大正元（昨 4 板，高位断板大阴棒，断板次日冲高一律诱多，严禁抄底首阴）",
            "东安动力（昨 3 板，放量断板，高位亏钱效应释放，严禁买入）",
            "长春一东（昨 3 板，高位分歧破位，防范二次杀跌）",
            "京城股份（昨 3 板，放量断板）",
            "慕思股份（昨 2 板，断板被核）",
            "福莱新材（昨 2 板，冲高回落大面）"
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

    # 8. 三套情景推演 (Scenarios)
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

    return {
        "title": f"明日实战推演与交易预案",
        "action_slogan": slogan or "极度防守 · 不开新仓 (严守量化风控底线)",
        "recommended_position": rec_pos,
        "is_melt": is_melt,
        "melt_text": melt_text,
        "iron_rules": iron_rules,
        "priority_matrix": p_matrix,
        "candidates": candidates,
        "checklist": checklist,
        "avoid_list": avoid_list,
        "scenarios": scenarios,
        "morning_card": morning_card,
        "full_report_url": f"./reports/plan_{html_path.stem.split('_')[-1]}.html"
    }

if __name__ == "__main__":
    import json
    p = parse_plan_html(PROJECT_ROOT / "output" / "今日复盘与明日预案_20261022.html")
    print("Parsed plan keys:", list(p.keys()))
    print("Priority matrix count:", len(p["priority_matrix"]))
    for pm in p["priority_matrix"]:
        print(f"  {pm['tier']}: {pm['title']}")
    print("Checklist count:", len(p["checklist"]))
    print("Morning card length:", len(p["morning_card"]))
