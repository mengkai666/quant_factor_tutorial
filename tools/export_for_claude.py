"""
导出 Antigravity 对话与核心知识库供 Claude / 其他智能体学习。

生成三份互补文件，全部附带导出时从缓存现算的事实核验（见 claude_export_factcheck.py）：
1. output/conversation_for_claude.md: 交易相关问答实录（去掉 git/导出类操作轮次与本地链接），每轮附取数记录与核验批注
2. output/conversation_for_claude.json: 同一批轮次的标准 LLM 消息格式，核验批注并入助手回复末尾
3. output/claude_knowledge_prompt.md: Claude 预设提示词（数据纪律 + 回测判据 + 分级战法库 + 事实更正与样本外对账 + 作答框架 + 带日期的观察名单）

全量原始轨迹（含全部工具回显）另见 tools/export_everything_to_md.py。
"""

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
from paths import DATA_DIR, OUTPUT_DIR  # noqa: E402

import claude_export_factcheck as factcheck  # noqa: E402

DEFAULT_CONV_ID = "33478f25-e7c7-4eca-8133-aa9062237625"
APP_DATA_DIR = Path(r"C:\Users\mengk\.gemini\antigravity-ide")
OUT_DIR = Path(OUTPUT_DIR)

# 纯操作轮次（推送仓库、导出对话）对学习操盘没有信息量，只会稀释上下文
OPS_ROUND_RE = re.compile(r"git|推送|导出", re.I)
# 智能体回复里的 file:/// 链接在别的机器上打不开，还有把股票名链到 .py 文件的伪链接
LOCAL_LINK_RE = re.compile(r"\[([^\]]+)\]\(file:///[^)]*\)")
MAX_TOOLS_SHOWN = 8


def find_transcript_file(conv_id: str = DEFAULT_CONV_ID) -> Path:
    target_path = APP_DATA_DIR / "brain" / conv_id / ".system_generated" / "logs" / "transcript_full.jsonl"
    if target_path.exists():
        return target_path

    # 查找最新 brain 目录
    brain_dir = APP_DATA_DIR / "brain"
    if brain_dir.exists():
        candidates = sorted(brain_dir.glob("*/.system_generated/logs/transcript_full.jsonl"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            return candidates[0]

    fallback = APP_DATA_DIR / "brain" / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
    if fallback.exists():
        return fallback
    raise FileNotFoundError(f"未找到对话日志文件: {target_path}")


def _tool_summary(tc: dict) -> str:
    # 转录日志的工具调用是 {"name", "args"}；旧逻辑只认 {"function": {...}}，于是全部退化成字面量 "tool"
    fn = tc.get("name") or tc.get("function", {}).get("name") or "tool"
    args = tc.get("args") or tc.get("function", {}).get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    if not isinstance(args, dict):
        return str(fn)
    return str(args.get("toolSummary") or args.get("toolAction") or fn)


def parse_transcript_pairs(transcript_path: Path):
    """提取真正的 User ↔ Assistant 轮次配对，去重冗余中间状态。"""
    rounds = []
    current_user = None
    current_assistant_replies = []
    current_tools = []

    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue

            stype = data.get("type")
            c = data.get("content", "")

            for tc in data.get("tool_calls") or []:
                summary = _tool_summary(tc)
                if summary not in current_tools:
                    current_tools.append(summary)

            if stype == "USER_INPUT":
                req_match = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", c, re.DOTALL)
                clean_req = req_match.group(1).strip() if req_match else c.strip()

                # 过滤掉系统内部注入的纯空或无效请求
                if not clean_req:
                    continue

                # 如果是新的提问
                if clean_req != current_user:
                    if current_user is not None:
                        # 结算上一轮
                        last_reply = current_assistant_replies[-1] if current_assistant_replies else "(完成任务)"
                        rounds.append({
                            "user": current_user,
                            "assistant": last_reply,
                            "tools": current_tools.copy()
                        })
                    current_user = clean_req
                    current_assistant_replies = []
                    current_tools = []

            elif stype == "PLANNER_RESPONSE" and c and c.strip():
                current_assistant_replies.append(c.strip())

    if current_user is not None and current_assistant_replies:
        rounds.append({
            "user": current_user,
            "assistant": current_assistant_replies[-1],
            "tools": current_tools.copy()
        })

    return rounds


def select_trading_rounds(rounds):
    """去掉纯操作轮次并清洗本地链接；返回 (保留轮次, 去掉的提问)。"""
    kept, dropped = [], []
    for r in rounds:
        if OPS_ROUND_RE.search(r["user"]):
            dropped.append(r["user"])
            continue
        kept.append({**r, "assistant": LOCAL_LINK_RE.sub(r"\1", r["assistant"])})
    return kept, dropped


def generate_conversation_markdown(rounds, dropped, report, output_path: Path):
    lines = [
        "# 📜 Antigravity ↔ 用户 实战对话实录（已清洗 · 附事实核验）",
        f"> **导出时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> **对话轮次**: 保留 {len(rounds)} 轮交易相关问答；去掉 {len(dropped)} 轮纯操作请求（{'、'.join(dropped) or '无'}）",
        "> **阅读须知**: 对话里有多处事实与仓库缓存不符，下方「事实核验」先列出，每轮末尾另附核验批注。"
        "引用对话中的数字和连板高度前，以核验结果为准。",
        "",
        "## 🔎 事实核验与 9/24 全量对账（导出时从缓存现算）",
        "",
        factcheck.render_errata_md(report),
        "",
        "---",
        "",
    ]

    for idx, r in enumerate(rounds, 1):
        lines += [f"## 👤 User [第 {idx} 轮]", "", r["user"], "", f"## 🤖 Assistant [第 {idx} 轮]"]
        if r.get("tools"):
            shown = " · ".join(f"`{t}`" for t in r["tools"][:MAX_TOOLS_SHOWN])
            more = f" …等共 {len(r['tools'])} 项" if len(r["tools"]) > MAX_TOOLS_SHOWN else ""
            lines += [f"> 🛠️ **本轮取数与操作**: {shown}{more}", ""]
        lines += [r["assistant"], ""]
        notes = factcheck.annotations_for(r["assistant"], report)
        if notes:
            lines += ["> 🔎 **导出核验批注**（本轮以下说法与缓存不符或有遗漏）:"] + [f"> - {n}" for n in notes] + [""]
        lines += ["---", ""]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"✅ 1. 对话实录已生成: {output_path} ({len(lines)} 行)")


def generate_conversation_json(rounds, report, output_path: Path):
    messages = []
    for r in rounds:
        content = r["assistant"]
        notes = factcheck.annotations_for(content, report)
        if notes:
            content += "\n\n【导出核验批注：以下说法与仓库缓存不符或有遗漏】\n" + "\n".join(f"- {n}" for n in notes)
        messages.append({"role": "user", "content": r["user"]})
        messages.append({"role": "assistant", "content": content})

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)
    print(f"✅ 2. 标准 JSON 消息已生成: {output_path} ({len(messages)} 条消息)")


def generate_claude_prompt_knowledge(report, output_path: Path):
    tactics_file = Path(DATA_DIR) / "tactics_recap.json"
    tactics_data = {}
    if tactics_file.exists():
        try:
            with open(tactics_file, "r", encoding="utf-8") as f:
                tactics_data = json.load(f)
        except (OSError, ValueError):
            pass

    lines = [
        "# 🧠 Claude 预设：A 股超短情绪周期与可转债 T+0 复盘助手",
        f"> 由 tools/export_for_claude.py 生成（{datetime.now().strftime('%Y-%m-%d %H:%M')}），勿手改；"
        f"涨停缓存数据截至 {report.get('latest_cache_day') or '未知'}。",
        "",
        "## 0. 数据纪律（优先级最高）",
        "1. 只使用用户提供或工具实际抓取的数据。缺失就写「未提供」，不得补编竞价量、封单金额、成交额、涨跌家数或连板数。",
        "2. 每个数字注明日期与来源。连板数、炸板、跌停名单以涨停缓存为准；对话和战法库里的叙述有过错误（第 6 节）。",
        "3. 情景概率：有回测就给基础概率并写明样本；没有回测只写高 / 中 / 低，不写「35%」「50%」这类凭空数字。",
        "4. 结论先行，同时标注置信度与证据来源；把「不开仓规则」和「涨跌预测」分开写。",
        "5. 复盘时对昨日预案逐条记分（成立 / 落空 / 无法判定），黑名单也要记，不能只挑命中的讲。",
        "",
        "## 1. 角色",
        "你协助用户做 A 股超短情绪周期、涨停接力与可转债 T+0 的复盘和次日预案。用户自己下单；"
        "你负责把盘面事实、历史规律和风险讲清楚，给出带触发条件的计划。",
        "",
        "## 2. 项目回测判据（引用时带上样本）",
        "",
        "| 主题 | 规律 | 来源与样本 |",
        "|---|---|---|",
    ]
    lines += [f"| {topic} | {fact} | {src} |" for topic, fact, src in factcheck.BACKTEST_FACTS]
    lines += [
        "",
        "## 3. 战法库（证据分级）",
        "> 🔴 与回测冲突 · 🟡 部分有回测 · ⚪ 个案或未回测。只有回测支撑的部分能当规律用，其余只能当观察线索。",
        "",
    ]
    for t in tactics_data.get("tactics_system", []):
        grade, note = factcheck.TACTIC_EVIDENCE.get(t.get("name", ""), ("⚪ 未分级", "未经核验，按经验假设对待。"))
        lines += [
            f"### 《{t.get('name', '')}》（{t.get('sub_title', '')}）· {grade}",
            f"- **原理**: {t.get('principle', '')}",
            f"- **纪律**: {t.get('rule', '')}",
            f"- **证据**: {note}",
            "",
        ]
    lines += [
        "## 4. 优先级分层与仓位上限",
        "- **P0 进攻（≤1.5 成）**：主线最强标的。必须写明开仓条件（如「高开 >2% 且封板」），条件不满足当天不开仓。",
        "- **P1 卡位 / 容量中军（≤1.0 成）**：同样写触发条件；低吸必须配止损位。",
        "- **P2 防御避险（≤0.5 成）**：9/24 样本里避险方向与大盘同跌，不能默认它抗跌。",
        "- **P3 观察（0 成）**：只用来判断情绪。",
        "- **P-Black（0 成）**：是「不开仓规则」，不是「次日必跌」预测。入选条件：逼近或触发严重异动"
        "（10 日偏离 100% / 30 日 200%）；断板次日冲板回落；正股走弱但转债溢价虚高。",
        "- **总仓位**：趋势闸门处于下跌档、或上涨占比 <0.35 的退潮日，总仓位不超过 2~3 成。",
        "",
        "## 5. 可转债 T+0 纪律",
        "- **分时均价线是止损线**：跌破且 3 分钟内收不回就走，不过夜。9/24 澳弘转债盘中最低较前收约 -20%，不止损就是重伤。",
        "- **跟随正股**：正股封板坚决时转债才有溢价；正股炸板或回落，转债先走。",
        "- **溢价与强赎**：转股溢价率 >50% 且价格 >200 元的非主线转债不留隔夜仓；临近强赎条件的先查公告。",
        "",
        "## 6. 已知事实更正与 9/24 样本外对账",
        "",
        factcheck.render_errata_md(report),
        "",
        "## 7. 作答框架",
        "0. **数据说明**：交易日、数据来源、缺哪些数据。",
        "1. **大盘体检**：指数、成交额、上涨占比、涨停 / 跌停家数、晋级率；对照第 2 节判据判断冰点或过热。",
        "2. **全市场连板梯队**：最高板和次高板必须列出（9/24 预案就漏掉了 9/23 的 4 板新华文轩）。",
        "3. **板块与主线**：用涨停归属和价格说话，不写无法验证的「主力意图」。",
        "4. **优先级分层**：每个标的写开仓条件、止损位、仓位上限。",
        "5. **情景分支**：触发条件必须是收盘后能核对的指标；要考虑「指数大跌、短线情绪独立」这类背离情形（9/24 就是）。",
        "6. **风控**：总仓位、黑名单、转债纪律。",
        "7. **次日复盘**：按第 0 节第 5 条逐条记分。",
        "",
    ]

    plan = tactics_data.get("tomorrow_plan") or {}
    report_date = str(tactics_data.get("report_date") or "")
    try:
        age = (date.today() - date.fromisoformat(report_date)).days
    except ValueError:
        age = None
    stale = age is None or age > 1
    lines += [
        "## 8. 当前观察名单（动态）",
        f"> 数据日期 {report_date or '未知'}，目标交易日 {tactics_data.get('target_date') or '未知'}，"
        + (f"距今 {age} 天。" if age is not None else "")
        + ("**⚠️ 已过期：只作历史参考，不得当作当前名单使用。**" if stale else ""),
        "",
        "| 标的 | 角色 | 触发条件 | 防守 | 仓位 |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {t.get('name', '')}（{t.get('code', '')}） | {t.get('role', '')} | {t.get('trigger', '')} | "
              f"{t.get('defense', '')} | {t.get('position', '')} |" for t in plan.get("focus_targets") or []]

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"✅ 3. Claude 专精知识库已生成: {output_path} ({len(lines)} 行)")


def main():
    transcript_path = find_transcript_file()
    print(f"📂 读取对话日志: {transcript_path}")

    rounds, dropped = select_trading_rounds(parse_transcript_pairs(transcript_path))
    print(f"🔍 保留 {len(rounds)} 轮交易问答，去掉 {len(dropped)} 轮操作请求")

    report = factcheck.build_report(transcript_path, [r["assistant"] for r in rounds])
    flagged = sum(c["status"] != factcheck.OK for c in report["claims"])
    print(f"🔎 事实核验: {len(report['claims'])} 条断言中 {flagged} 条与缓存不符或有遗漏")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    generate_conversation_markdown(rounds, dropped, report, OUT_DIR / "conversation_for_claude.md")
    generate_conversation_json(rounds, report, OUT_DIR / "conversation_for_claude.json")
    generate_claude_prompt_knowledge(report, OUT_DIR / "claude_knowledge_prompt.md")

    print("\n🎉 全部导出完成！output/ 下的三份文件均已附事实核验。")


if __name__ == "__main__":
    main()
