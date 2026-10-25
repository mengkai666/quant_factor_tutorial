from pathlib import Path

p = Path("tools/generate_history_bundles.py")
content = p.read_text(encoding="utf-8")

old_block = '''    # 5. 提取预案
    rec_pos = pos_el.get_text(strip=True) if pos_el else ("4 ~ 6 成仓" if degree >= 50 else "0 ~ 2 成轻仓")
    strategy_module = {
        "title": f"{date_str} 实战推演与交易预案",
        "action_slogan": "顺势博弈核心龙头" if degree >= 65 else ("分歧震荡低吸" if degree >= 40 else "防守防风险，静待冰点出清"),
        "recommended_position": rec_pos,
        "rules": [
            f"大盘情绪读数 {degree}（{status_text}），涨跌家数比 {up_count}:{down_count}。",
            f"连板空间高度锚定在 {max_height_str}，全天涨停 {zt_count} 家，跌停 {dt_count} 家。",
            "严格恪守盘面铁律与仓位纪律，弱势期宁可空仓不可盲目接力后排。"
        ],
        "defense_line": "单日亏损触及本金 2% 或跌破 EMA20 支撑无条件触发止损纪律。"
    }'''

new_block = '''    # 5. 提取预案 (调用 plan_parser 深度提取或智能合成)
    try:
        from plan_parser import get_plan_for_date
        strategy_module = get_plan_for_date(PROJECT_ROOT, date_str, degree, status_text, tianti_stocks, dt_count)
    except Exception as e:
        rec_pos = pos_el.get_text(strip=True) if pos_el else ("4 ~ 6 成仓" if degree >= 50 else "0 ~ 2 成轻仓")
        strategy_module = {
            "title": f"{date_str} 实战推演与交易预案",
            "action_slogan": "顺势博弈核心龙头" if degree >= 65 else ("分歧震荡低吸" if degree >= 40 else "防守防风险，静待冰点出清"),
            "recommended_position": rec_pos,
            "is_melt": degree < 30 or dt_count >= 20,
            "melt_text": f"防守熔断：跌停 {dt_count} 家，严格控制仓位！" if (degree < 30 or dt_count >= 20) else "",
            "priority_matrix": [],
            "checklist": [],
            "avoid_list": [],
            "scenarios": [],
            "rules": [
                f"大盘情绪读数 {degree}（{status_text}），涨跌家数比 {up_count}:{down_count}。",
                f"连板空间高度锚定在 {max_height_str}，全天涨停 {zt_count} 家，跌停 {dt_count} 家。",
                "严格恪守盘面铁律与仓位纪律，弱势期宁可空仓不可盲目接力后排。"
            ],
            "defense_line": "单日亏损触及本金 2% 或跌破 EMA20 支撑无条件触发止损纪律。"
        }'''

assert old_block in content, "old_block not found in generate_history_bundles.py"
content = content.replace(old_block, new_block, 1)

p.write_text(content, encoding="utf-8")
print("✅ tools/generate_history_bundles.py updated successfully!")
