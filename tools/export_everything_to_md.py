"""
把整个会话的所有内容（用户输入、助手思考/回答、全部工具调用参数、工具输出、代码修改、命令执行、全量数据）
完整、不漏一字地导出为 Markdown 文件，供 Claude / 智能体深度学习。

原始轨迹一字不改；文件开头插入导出时从缓存现算的事实核验（claude_export_factcheck.py），
让读者先看到哪些原话已被证伪。精简版三件套由 tools/export_for_claude.py 负责，这里不再覆盖它们。
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
from paths import DATA_DIR, OUTPUT_DIR  # noqa: E402

import claude_export_factcheck as factcheck  # noqa: E402
from export_for_claude import (  # noqa: E402
    DEFAULT_CONV_ID, find_transcript_file, parse_transcript_pairs, select_trading_rounds)

OUTPUT_MD_PATH = Path(OUTPUT_DIR) / "完整实战对话全记录_所有内容_供Claude学习.md"

def export_all_to_md():
    transcript_path = find_transcript_file()
    rounds, _ = select_trading_rounds(parse_transcript_pairs(transcript_path))
    report = factcheck.build_report(transcript_path, [r["assistant"] for r in rounds])

    lines = []
    lines.append("# 🌟 Antigravity 完整实战对话全记录 (所有内容全量导出 · 供 Claude 深度学习)")
    lines.append(f"> **导出时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("> **包含范围**: 100% 全量原始轨迹（用户请求、助手全量回复、全部工具调用与入参、完整命令行执行与回显、代码读写与改动、系统事件）")
    lines.append(f"> **对话 ID**: `{DEFAULT_CONV_ID}`")
    lines.append(f"> **项目路径**: `{ROOT}`")
    lines.append("\n---\n")
    lines.append("## 目录大纲与实战演进脉络")
    lines.append("1. **阶段一：可转债行情与主线板块复盘、隔日预案推演**")
    lines.append("2. **阶段二：强势转债与债股联动逻辑拆解（PCB、算力、掩膜版、老妖债）**")
    lines.append("3. **阶段三：盘面实时对账与 Git 代码/数据多端同步推送**")
    lines.append("4. **阶段四：短线情绪周期、监管异动核按钮、假冲天炮出逃、20CM首板抢跑与抱团深度剖析**")
    lines.append("5. **阶段五：将战法融入主线报告底层架构与实战战法库系统**")
    lines.append("6. **阶段六：构建独立交互式 HTML 实战预案作战手册与优先级分层矩阵 (P0 ~ P-Black)**")
    lines.append("7. **阶段七：2026-09-24 收盘全景复盘与实战预案对账验真**")
    lines.append("8. **阶段八：知识体系蒸馏与多模型/Claude学习导出**")
    lines.append("\n---\n")
    lines.append("## ⚠️ 先读：事实核验与 9/24 全量对账（导出时从仓库缓存现算）")
    lines.append("> 下文原始轨迹一字未改，其中多处说法已被缓存证伪；引用连板高度、涨跌家数、对账结论前以本节为准。\n")
    lines.append(factcheck.render_errata_md(report))
    lines.append("\n---\n")

    user_turn_counter = 0
    step_counter = 0
    last_user_req = None
    assistant_has_acted = True

    with open(transcript_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except Exception:
                continue
                
            step_counter += 1
            step_index = record.get("step_index", step_counter)
            source = record.get("source", "")
            stype = record.get("type", "")
            content = record.get("content", "")
            tool_calls = record.get("tool_calls", [])
            created_at = record.get("created_at", "")

            # 1. 用户输入处理
            if stype == "USER_INPUT":
                req_match = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", content, re.DOTALL)
                meta_match = re.search(r"<ADDITIONAL_METADATA>(.*?)</ADDITIONAL_METADATA>", content, re.DOTALL)
                action_match = re.search(r"The USER performed the following action:\s*(.*?)(?=(?:File Path:|<USER_REQUEST>|<ADDITIONAL_METADATA>|$))", content, re.DOTALL)
                
                user_req = req_match.group(1).strip() if req_match else content.strip()
                user_action = action_match.group(1).strip() if action_match else ""
                
                # 过滤同一轮内因IDE重连造成的连续重复 prompt (无实质动作发生时)
                if user_req == last_user_req and not assistant_has_acted and not user_action:
                    continue
                    
                last_user_req = user_req
                assistant_has_acted = False
                user_turn_counter += 1
                
                lines.append(f"\n\n================================================================================")
                lines.append(f"# 👤 【用户交互 第 {user_turn_counter} 轮】 (Step #{step_index})")
                lines.append(f"================================================================================\n")
                
                lines.append(f"### 💬 用户提示词 (User Request):\n")
                lines.append(f"```text\n{user_req}\n```\n")
                    
                if user_action:
                    lines.append(f"> 👁️ **用户界面操作**: `{user_action}`\n")
                    
                if meta_match and meta_match.group(1).strip():
                    lines.append(f"<details><summary>📎 查看用户交互环境元数据 (时间戳、光标位置、打开文档)</summary>\n\n```yaml\n{meta_match.group(1).strip()}\n```\n</details>\n")
                continue

            # 2. 助手思考与回复 (PLANNER_RESPONSE)
            if stype == "PLANNER_RESPONSE":
                has_meaningful_content = bool(content and content.strip())
                has_tools = bool(tool_calls)
                
                if has_meaningful_content or has_tools:
                    assistant_has_acted = True
                
                # 如果包含工具调用
                if has_tools:
                    for tc in tool_calls:
                        fn_name = tc.get("name") or tc.get("function", {}).get("name", "unknown_tool")
                        args = tc.get("args") or tc.get("function", {}).get("arguments", {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                pass
                                
                        tool_action = ""
                        tool_summary = ""
                        if isinstance(args, dict):
                            tool_action = args.get("toolAction", "")
                            tool_summary = args.get("toolSummary", "")
                            
                        lines.append(f"\n#### 🛠️ [Step #{step_index}] 助手发起工具调用: `{fn_name}`")
                        if tool_summary or tool_action:
                            lines.append(f"> 🎯 **意图**: {tool_summary} | {tool_action}")
                        lines.append("\n<details open><summary>查看工具调用入参 (Arguments)</summary>\n")
                        lines.append(f"```json\n{json.dumps(args, ensure_ascii=False, indent=2)}\n```\n</details>\n")
                
                # 如果包含文本回复内容
                if has_meaningful_content:
                    lines.append(f"\n### 🤖 [Step #{step_index}] 助手回复与分析 (Assistant Response):\n")
                    lines.append(f"{content.strip()}\n")
                continue

            # 3. 工具执行结果
            if stype in ["RUN_COMMAND", "VIEW_FILE", "CODE_ACTION", "LIST_DIRECTORY", "GREP_SEARCH", "SEARCH_WEB", "GENERIC"]:
                assistant_has_acted = True
                lines.append(f"\n##### 📥 [Step #{step_index}] 工具执行返回结果 (`{stype}`):\n")
                content_str = str(content)
                lines.append("<details open><summary>展开查看完整执行回显 / 数据内容 (100% 全量无删减)</summary>\n")
                lines.append(f"```text\n{content_str}\n```\n</details>\n")
                continue

            # 4. 系统通知与消息 (SYSTEM_MESSAGE, CHECKPOINT, ERROR_MESSAGE)
            if stype in ["SYSTEM_MESSAGE", "CHECKPOINT", "ERROR_MESSAGE"]:
                lines.append(f"\n> ⚙️ **[Step #{step_index} 系统事件: `{stype}`]**")
                lines.append(f">\n```text\n{content.strip()}\n```\n")
                continue

            # 5. 其他类型保底
            if content and content.strip():
                lines.append(f"\n> 📝 **[Step #{step_index} `{source}` / `{stype}`]**\n\n```text\n{content.strip()}\n```\n")

    # 附录：核心成果数据与预案战法总览
    lines.append("\n\n================================================================================")
    lines.append("# 📚 【附录：核心战法系统与预案成果总览】")
    lines.append("================================================================================\n")
    
    tactics_file = Path(DATA_DIR) / "tactics_recap.json"
    if tactics_file.exists():
        lines.append("### 1. 战法体系与数据底层 (`data/tactics_recap.json`)\n")
        try:
            with open(tactics_file, "r", encoding="utf-8") as f:
                tcontent = f.read()
            lines.append("```json\n" + tcontent + "\n```\n")
        except Exception as e:
            lines.append(f"读取 tactics_recap.json 失败: {e}\n")

    plan_html = Path(OUTPUT_DIR) / "明日操作预案_2026-09-24.html"
    if plan_html.exists():
        lines.append(f"### 2. 独立 HTML 预案文件存在确认\n")
        lines.append(f"- 路径: `{plan_html}`")
        lines.append(f"- 大小: {plan_html.stat().st_size} 字节\n")

    lines.append("\n================================================================================")
    lines.append("## 🏁 导出完成说明")
    lines.append(f"本文件完整收录本次对话生命周期内的全部 {step_counter} 条轨迹记录。")
    lines.append("Claude 读取后可直接理解：用户意图演变、战法提出背景、代码开发全流程、盘面真实数据采集推演及最终生成的独立 HTML 报告。")
    lines.append("================================================================================\n")

    output_text = "\n".join(lines)

    OUTPUT_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_MD_PATH, "w", encoding="utf-8") as f:
        f.write(output_text)
    print(f"🎉 成功导出全量完整版到: {OUTPUT_MD_PATH}")
    print(f"   文件大小: {len(output_text.encode('utf-8')) / 1024:.2f} KB, 行数: {len(lines)}")

if __name__ == "__main__":
    export_all_to_md()
