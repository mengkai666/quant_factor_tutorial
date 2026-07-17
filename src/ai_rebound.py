"""AI 反弹研判模块 (独立模块, 主文件只 import 调用)。

把规则引擎算好的结构化事实 (facts) 喂给 Claude API, 让 AI 在既有分类框架
(主动反弹可追 / 跟随反弹减亏 / 高度断层回避) 上做研判、按原模板填充, 并自行
进化给出规则没覆盖的独立洞察。

设计原则:
- 硬数据 (涨跌家数、各主线涨停数、连板高度) 由规则引擎给定, AI 不得篡改, 只做解读。
- API 不可用 / 超时 / 返回不可解析时一律返回 None, 由主程序 fallback 回规则模板,
  报告永不开天窗。
- 走系统代理: requests 默认 trust_env=True, 本机 Clash 环境会自动用 HTTPS_PROXY;
  中转/官方地址均可直连。无需特殊处理。

配置 (全部走环境变量 / GitHub Secrets, 不硬编码):
- ANTHROPIC_API_KEY: 密钥 (官方 sk-ant-... 或第三方中转的 key)。
- ANTHROPIC_BASE_URL: 中转/自建网关地址 (如 https://sub.100xlabs.space)。
  留空则用官方 api.anthropic.com。可填裸域名或带 /v1/messages 的完整路径, 两者都兼容。
- ANTHROPIC_MODEL: 模型名, 默认 claude-opus-4-8 (当前中转仅该模型可用,
  sonnet 系列被渠道限制挡掉)。
"""
import os
import json
import requests


def _load_dotenv():
    """极简 .env 加载器 (零依赖): 把项目根 .env 的键值注入 os.environ。

    只在环境里尚无该键时注入 (CI 的 Secrets 优先, 不被本地 .env 覆盖)。
    .env 已在 .gitignore 中, 不入库。格式: KEY=value, 支持 # 注释与引号。
    """
    # src/ai_rebound.py -> 项目根
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as e:
        print(f"  [提示] .env 加载跳过: {e}")


_load_dotenv()

# 从环境变量 / GitHub Secrets 读取 (不硬编码)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# 默认 opus-4-8 (当前中转唯一可用; 官方 key 也支持)。可用 ANTHROPIC_MODEL 覆盖。
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
ANTHROPIC_ENABLE = os.environ.get("ANTHROPIC_ENABLE", "1") == "1"
# 中转地址 (留空 = 官方)。裸域名会自动补 /v1/messages。
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "").strip()

_API_VERSION = "2023-06-01"


def _resolve_api_url() -> str:
    """解析最终请求 URL: 支持裸域名 / 带路径的中转地址 / 官方兜底。"""
    base = ANTHROPIC_BASE_URL.rstrip("/")
    if not base:
        return "https://api.anthropic.com/v1/messages"
    # 已含 messages 路径则原样用; 否则按 Anthropic 原生协议补全
    if "/v1/messages" in base:
        return base
    if base.endswith("/v1"):
        return base + "/messages"
    return base + "/v1/messages"


def ai_enabled() -> bool:
    """是否具备调用 AI 的条件 (有 key 且未被显式关闭)。"""
    return ANTHROPIC_ENABLE and bool(ANTHROPIC_API_KEY)


def _build_prompt(facts: dict) -> str:
    """把结构化事实拼成给 AI 的指令。事实以 JSON 传入, 保证 AI 不臆造数据。"""
    return (
        "你是一名资深 A 股游资打板复盘师。下面是今日收盘后由量化规则引擎算出的"
        "**客观事实数据**(JSON),这些硬数据(涨跌家数、各主线涨停数、连板高度)"
        "是既定事实,你**不得修改或臆造**,只能在此基础上做研判。\n\n"
        f"```json\n{json.dumps(facts, ensure_ascii=False, indent=2)}\n```\n\n"
        "请遵循用户既有的交易分类框架:\n"
        "- 主动反弹(可追): 资金持续主动流入、有梯队支撑的主线。\n"
        "- 跟随反弹(减亏离场): 当日有涨停但近期不持续、无梯队支撑。\n"
        "- 高度断层(回避追高): 龙头孤悬、中间连板缺档,退潮期特征。\n\n"
        "在按此框架研判的同时,请**自行进化**给出规则没有覆盖的独立洞察"
        "(例如:主线间的轮动/接力关系、情绪拐点信号、明日重点跟踪对象、"
        "规则可能误判之处)。语气专业、干练,有交易语感,不说套话废话。\n\n"
        "严格只输出如下 JSON(不要 markdown 代码块、不要多余文字):\n"
        "{\n"
        '  "market_summary": "一句话市场定性研判,可在规则定性基础上修正措辞",\n'
        '  "active_comment": "对主动主线的点评与操作建议,无则写空评价",\n'
        '  "follow_comment": "对跟随盘的提醒,无则留空字符串",\n'
        '  "gap_comment": "对梯队/高度断层结构的解读",\n'
        '  "evolution": "你自行进化的独立判断:规则未覆盖的洞察、轮动关系、明日关注点或风险提示",\n'
        '  "operation": "一句话落地操作建议(仓位/追与不追/规避方向)"\n'
        "}"
    )


def generate_ai_rebound(facts: dict, timeout: int = 45) -> dict | None:
    """调用 Claude API 生成研判, 返回结构化 dict; 任何失败返回 None。

    返回字段: market_summary / active_comment / follow_comment /
             gap_comment / evolution / operation
    """
    if not ai_enabled():
        return None
    try:
        payload = {
            "model": ANTHROPIC_MODEL,
            "max_tokens": 1500,
            "messages": [
                {"role": "user", "content": _build_prompt(facts)},
            ],
        }
        headers = {
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }
        resp = requests.post(_resolve_api_url(), headers=headers, json=payload, timeout=timeout)
        if resp.status_code != 200:
            print(f"  [警告] AI 研判 API 返回 {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
        # Claude messages API: content 是 block 数组, 取 text
        blocks = data.get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        if not text:
            return None
        return _parse_json(text)
    except Exception as e:
        print(f"  [警告] AI 研判调用失败, 回退规则模板: {e}")
        return None


def _parse_json(text: str) -> dict | None:
    """从模型输出里稳健地抽出 JSON (容忍 ```json 代码块包裹)。"""
    t = text.strip()
    if t.startswith("```"):
        # 去掉首行 ```json / ``` 和末尾 ```
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    # 兜底: 截取第一个 { 到最后一个 }
    if not t.startswith("{"):
        s, e = t.find("{"), t.rfind("}")
        if s != -1 and e != -1 and e > s:
            t = t[s:e + 1]
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except Exception as e:
        print(f"  [警告] AI 研判返回无法解析为 JSON: {e}")
    return None


def render_ai_rebound_html(ai: dict, facts: dict, char_clr: str) -> str:
    """把 AI 研判结果渲染成深色卡片, 视觉对齐既有报告风格。

    硬数据 (当日定性描述) 仍来自规则引擎的 facts, AI 只提供解读文字。
    """
    def _esc(s):
        s = str(s or "")
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    market_char = facts.get("market_char", "")
    char_desc = facts.get("char_desc", "")

    def _block(label, val, clr="#e6edf3"):
        if not val:
            return ""
        return (f'<div style="margin-top:8px;font-size:14px;line-height:1.7;">'
                f'<span style="color:#8b949e;">{label}:</span> '
                f'<span style="color:{clr};">{_esc(val)}</span></div>')

    evolution = _esc(ai.get("evolution", ""))
    evolution_block = (
        f'<div style="margin-top:14px;padding:12px 14px;background:rgba(88,166,255,0.08);'
        f'border-left:3px solid #58a6ff;border-radius:8px;">'
        f'<div style="color:#58a6ff;font-weight:bold;font-size:13px;margin-bottom:4px;">'
        f'🔮 AI 进化研判 (规则外洞察)</div>'
        f'<div style="color:#e6edf3;font-size:14px;line-height:1.7;">{evolution}</div></div>'
        if evolution else "")

    operation = _esc(ai.get("operation", ""))
    operation_block = (
        f'<div style="margin-top:12px;padding:10px 14px;background:rgba(248,81,73,0.08);'
        f'border-radius:8px;color:#ffa657;font-size:14px;font-weight:bold;">'
        f'🎯 操作建议: {operation}</div>'
        if operation else "")

    return f'''
    <div style="background:#0d1117;border:1px solid #30363d;border-left:4px solid {char_clr};
                border-radius:12px;padding:22px;margin-bottom:30px;color:#c9d1d9;">
        <h2 style="color:{char_clr};font-size:19px;margin:0 0 14px;display:flex;align-items:center;gap:10px;">
            🧭 反弹分类复盘 · AI 研判: {_esc(ai.get("market_summary", market_char))}
        </h2>
        <div style="color:#e6edf3;font-size:14px;line-height:1.7;">
            <div><span style="color:#8b949e;">当日定性:</span> {_esc(char_desc)}</div>
            {_block("主动主线", ai.get("active_comment", ""), "#f85149")}
            {_block("跟随提醒", ai.get("follow_comment", ""), "#d29922")}
            {_block("梯队结构", ai.get("gap_comment", ""))}
        </div>
        {evolution_block}
        {operation_block}
        <div style="margin-top:14px;padding-top:12px;border-top:1px dashed #30363d;
                    font-size:12px;color:#8b949e;">
            由 Claude ({_esc(ANTHROPIC_MODEL)}) 基于规则引擎的客观数据研判生成 · 硬数据不可篡改, AI 仅做解读与进化。
            分类框架: 主动反弹(可追) / 跟随反弹(减亏离场) / 高度断层(回避追高)。
        </div>
    </div>'''
