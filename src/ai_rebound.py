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
import time
import hashlib
import re
from copy import deepcopy
import requests


def _load_dotenv():
    """极简 .env 加载器 (零依赖): 把项目根 .env 的键值注入 os.environ。

    .env 里的值**覆盖** shell/系统环境变量。原因: 本机 Claude Code 客户端会在
    用户环境里塞一套自用的 ANTHROPIC_* (指向自己的中转), 会顶掉项目 .env 的
    api key/base_url。.env 只在本地存在 (已 .gitignore, CI 无此文件), 覆盖不
    影响 CI —— CI 上 GitHub Secrets 直接进 os.environ, 此函数走 return。
    格式: KEY=value, 支持 # 注释与引号。
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
                if key:
                    os.environ[key] = val
    except Exception as e:
        print(f"  [提示] .env 加载跳过: {e}")


_load_dotenv()

# 从环境变量 / GitHub Secrets 读取 (不硬编码)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# 默认 opus-4-8 (当前中转唯一可用; 官方 key 也支持)。可用 ANTHROPIC_MODEL 覆盖。
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
ANTHROPIC_FALLBACK_MODEL = os.environ.get("ANTHROPIC_FALLBACK_MODEL", "").strip()
ANTHROPIC_ENABLE = os.environ.get("ANTHROPIC_ENABLE", "1") == "1"

def _float_env(name, default):
    try:
        value = float(os.environ.get(name, default))
        return value if value >= 0 else default
    except (TypeError, ValueError):
        return default


def _int_env(name, default, *, minimum=1):
    try:
        value = int(os.environ.get(name, default))
        return value if value >= minimum else default
    except (TypeError, ValueError):
        return default


AI_PRIMARY_MAX_ATTEMPTS = _int_env("AI_PRIMARY_MAX_ATTEMPTS", 2)
AI_RETRY_BASE_DELAY = _float_env("AI_RETRY_BASE_DELAY", 0.5)
AI_RETRY_MAX_DELAY = _float_env("AI_RETRY_MAX_DELAY", 2.0)
AI_REQUEST_TIMEOUT = _float_env("AI_REQUEST_TIMEOUT", 120.0)
# 中转地址 (留空 = 官方)。裸域名会自动补 /v1/messages。
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
# 部分中转强制要求显式启用 1M 上下文 beta (如 anyrouter: 不带此头直接 400
# "请启用 1m 上下文")。留空则不发 anthropic-beta 头 (官方/多数中转无需)。
ANTHROPIC_BETA = os.environ.get("ANTHROPIC_BETA", "").strip()

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI_OUTPUT_CACHE_DIR = os.environ.get(
    "AI_OUTPUT_CACHE_DIR", os.path.join(_PROJECT_ROOT, "data", "ai_output_cache")
)

_API_VERSION = "2023-06-01"
_AI_OUTPUT_SANITIZER_VERSION = "2"


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


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "；".join(str(item) for item in value if item not in (None, ""))
    return str(value)


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)] if str(value) else []


def normalize_ai_output(raw: dict) -> tuple[dict, str] | tuple[None, str]:
    """把 canonical 与旧版反弹字段统一为 report AI output/v1。"""
    if not isinstance(raw, dict):
        return None, "invalid"
    canonical_keys = {"facts", "observations", "conditions", "risks", "decision"}
    has_canonical = bool(canonical_keys & set(raw))
    if has_canonical:
        output = {
            "facts": _as_list(raw.get("facts")),
            "observations": _as_list(raw.get("observations")),
            "conditions": _as_list(raw.get("conditions")),
            "risks": _as_list(raw.get("risks")),
            "decision": _as_text(raw.get("decision")),
        }
        return output, "canonical"

    # 旧版字段来自 generate_ai_rebound 的 prompt；保留语义，避免升级后被过滤成空输出。
    observations = []
    for key, label in (("active_comment", "主动主线"), ("follow_comment", "跟随提醒"),
                       ("gap_comment", "梯队结构"), ("evolution", "进化研判")):
        value = _as_text(raw.get(key))
        if value:
            observations.append(f"{label}: {value}")
    risks = []
    gap = _as_text(raw.get("gap_comment"))
    if gap:
        risks.append(gap)
    output = {
        "facts": [_as_text(raw.get("market_summary"))] if _as_text(raw.get("market_summary")) else [],
        "observations": observations,
        "conditions": [],
        "risks": risks,
        "decision": _as_text(raw.get("operation")),
    }
    return output, "legacy"


def _ai_cache_identity(*, input_fingerprint: str, quality_fingerprint: str,
                       publication_mode: str, model: str) -> dict:
    return {
        "input_fingerprint": input_fingerprint,
        "input_quality_fingerprint": quality_fingerprint,
        "publication_mode": publication_mode,
        "model": model,
        "sanitizer_version": _AI_OUTPUT_SANITIZER_VERSION,
    }


def _ai_cache_path(identity: dict) -> str:
    cache_key = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return os.path.join(os.fspath(AI_OUTPUT_CACHE_DIR), f"{cache_key}.json")


def _read_ai_cache(identity: dict) -> dict | None:
    try:
        with open(_ai_cache_path(identity), "r", encoding="utf-8") as handle:
            cached = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(cached, dict) or cached.get("identity") != identity:
        return None
    if cached.get("status") not in {"ok", "sanitized"}:
        return None
    if not isinstance(cached.get("output"), dict):
        return None
    return cached


def _write_ai_cache(identity: dict, *, status: str, output: dict, lineage: dict) -> None:
    cache_dir = os.fspath(AI_OUTPUT_CACHE_DIR)
    os.makedirs(cache_dir, exist_ok=True)
    path = _ai_cache_path(identity)
    temporary = f"{path}.{os.getpid()}.{time.time_ns()}.tmp"
    payload = {
        "schema_version": "ai-output-cache/v1",
        "identity": identity,
        "status": status,
        "output": deepcopy(output),
        "normalized_from": lineage.get("normalized_from"),
        "output_fingerprint": lineage.get("output_fingerprint"),
        "cached_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass


def _cached_ai_result(identity: dict, lineage: dict, failure_reason: str) -> dict | None:
    cached = _read_ai_cache(identity)
    if cached is None:
        return None
    cached_lineage = dict(lineage)
    cached_lineage.update({
        "cache_hit": True,
        "cache_reason": "AI 调用失败，复用相同输入的最近成功结果",
        "upstream_failure": failure_reason,
        "cached_at": cached.get("cached_at"),
        "normalized_from": cached.get("normalized_from"),
        "output_fingerprint": cached.get("output_fingerprint"),
    })
    return {
        "status": cached["status"],
        "reason": "",
        "output": deepcopy(cached["output"]),
        "lineage": cached_lineage,
    }


def _sanitize_output_against_facts(output: dict, facts: dict) -> int:
    """移除缺少昨日逐股快照时无法由输入事实支持的晋级数字。"""
    daily_delta = facts.get("daily_delta") if isinstance(facts, dict) else None
    if not isinstance(daily_delta, dict) or daily_delta.get("available") is not False:
        return 0

    def unsupported(value) -> bool:
        text = str(value or "")
        if "晋级率" in text:
            return True
        if re.search(r"晋级.{0,16}(?:全零|为\s*0|是\s*0|\d+(?:\.\d+)?\s*%)", text):
            return True
        return bool(
            re.search(r"\d+\s*(?:进|→|至)\s*\d+", text)
            and re.search(r"(?:\d+\s*只\s*(?:样本|基数)|\d+\s*/\s*\d+)", text)
        )

    removed = 0
    for key in ("facts", "observations", "conditions", "risks"):
        values = output.get(key, [])
        if not isinstance(values, list):
            values = [str(values)] if values else []
        kept = []
        for item in values:
            if unsupported(item):
                removed += 1
            else:
                kept.append(str(item))
        output[key] = kept
    return removed


def run_guarded_ai(facts: dict, policy, *, caller=None, timeout: float | None = None) -> dict:
    """按发布策略控制 AI 调用、输出范围、缓存和审计指纹。"""
    from report_logic import ReportPolicy, scan_forbidden_semantics

    active = policy if isinstance(policy, ReportPolicy) else ReportPolicy.from_mode(policy)
    payload = {"schema_version": "report-facts/v1", "publication_mode": active.mode, "facts": dict(facts or {})}
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    quality_snapshot = facts.get("quality_snapshot") if isinstance(facts, dict) else None
    quality_payload = quality_snapshot if isinstance(quality_snapshot, dict) else {
        "status": facts.get("quality_status") if isinstance(facts, dict) else None,
    }
    quality_fingerprint = hashlib.sha256(
        json.dumps(quality_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    lineage = {
        "model": ANTHROPIC_MODEL,
        "input_fingerprint": fingerprint,
        "input_quality_status": quality_payload.get("status"),
        "input_quality_fingerprint": quality_fingerprint,
        "publication_mode": active.mode,
        "sanitizer_version": _AI_OUTPUT_SANITIZER_VERSION,
        "cache_hit": False,
    }
    identity = _ai_cache_identity(
        input_fingerprint=fingerprint,
        quality_fingerprint=quality_fingerprint,
        publication_mode=active.mode,
        model=ANTHROPIC_MODEL,
    )
    if not active.allow_ai:
        return {"status": "skipped", "reason": "发布策略禁止 AI", "output": None, "lineage": lineage}

    diagnostics = {}
    try:
        if caller is None:
            raw, diagnostics = generate_ai_rebound(
                payload["facts"], timeout=timeout, return_diagnostics=True,
            )
        else:
            raw = caller(payload)
    except Exception as exc:
        reason = str(exc)
        cached = _cached_ai_result(identity, lineage, reason)
        if cached is not None:
            return cached
        return {"status": "failed", "reason": reason, "output": None, "lineage": lineage}

    if diagnostics:
        for key in ("attempt_count", "http_status"):
            value = diagnostics.get(key)
            if value is not None:
                lineage[key] = value
    output, normalized_from = normalize_ai_output(raw)
    if output is None:
        reason = diagnostics.get("reason") or "AI 无结构化输出"
        cached = _cached_ai_result(identity, lineage, reason)
        if cached is not None:
            return cached
        return {
            "status": "fallback",
            "reason": reason,
            "output": None,
            "lineage": lineage,
        }
    lineage["normalized_from"] = normalized_from
    output["schema_version"] = "ai-output/v1"
    status = "ok"
    unsupported_removed = _sanitize_output_against_facts(output, facts)
    if unsupported_removed:
        lineage["unsupported_metric_items_removed"] = unsupported_removed
        status = "sanitized"
    if not active.allow_actions or not active.allow_positions or not active.allow_probabilities:
        output["decision"] = ""
        for key in ("observations", "conditions", "risks"):
            values = output.get(key, [])
            if not isinstance(values, list):
                values = [str(values)] if values else []
            output[key] = [str(item) for item in values if not scan_forbidden_semantics(str(item), active)]
        status = "sanitized"
    lineage["output_fingerprint"] = hashlib.sha256(
        json.dumps(output, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    try:
        _write_ai_cache(identity, status=status, output=output, lineage=lineage)
    except OSError as exc:
        lineage["cache_write_error"] = f"{type(exc).__name__}: {exc}"
    return {"status": status, "reason": "", "output": output, "lineage": lineage}


def _prompt_height(row: dict, *keys: str) -> int:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            digits = "".join(char for char in str(value) if char.isdigit())
            if digits:
                return int(digits)
    return 0


def _compact_prompt_rows(rows, *, fields: tuple[str, ...], limit: int, priority) -> list[dict]:
    candidates = [dict(row) for row in (rows or []) if isinstance(row, dict)]
    candidates.sort(key=priority, reverse=True)
    return [
        {key: row[key] for key in fields if key in row and row[key] is not None}
        for row in candidates[:limit]
    ]


def _compact_facts_for_prompt(facts: dict) -> dict:
    """缩短模型提示词，同时保留原始事实对象供指纹和审计使用。"""
    compacted = deepcopy(dict(facts or {}))

    market = compacted.get("market_snapshot")
    if isinstance(market, dict):
        rows = market.get("limit_pool_rows")
        if isinstance(rows, list):
            market["limit_pool_row_count"] = len(rows)
            market["limit_pool_rows"] = _compact_prompt_rows(
                rows,
                fields=("code", "name", "height", "mainline", "reason"),
                limit=24,
                priority=lambda row: (
                    _prompt_height(row, "height", "level", "连板高度", "连板数") >= 2,
                    _prompt_height(row, "height", "level", "连板高度", "连板数"),
                ),
            )

    progression = compacted.get("progression_chain")
    if isinstance(progression, dict):
        rows = progression.get("rows")
        if isinstance(rows, list):
            status_priority = {
                "limit_down": 5,
                "broken_negative": 4,
                "promoted": 3,
                "broken_positive": 2,
                "missing": 1,
                "suspended": 0,
            }
            progression["row_count"] = len(rows)
            progression["rows"] = _compact_prompt_rows(
                rows,
                fields=("code", "name", "previous_height", "current_height", "pct_change", "status"),
                limit=20,
                priority=lambda row: (
                    _prompt_height(row, "previous_height", "current_height"),
                    status_priority.get(str(row.get("status") or ""), 0),
                ),
            )

    quality = compacted.get("quality_snapshot")
    if isinstance(quality, dict):
        compact_quality = {
            key: deepcopy(quality[key])
            for key in ("status", "publication_mode", "coverage_pct", "missing_fields", "errors", "reasons")
            if key in quality
        }
        modules = quality.get("modules")
        if isinstance(modules, dict):
            compact_quality["modules"] = {
                name: {
                    key: deepcopy(module[key])
                    for key in ("status", "coverage_pct", "missing_fields", "errors")
                    if key in module
                }
                for name, module in modules.items()
                if isinstance(module, dict)
            }
        compacted["quality_snapshot"] = compact_quality

    return compacted


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
        '  "facts": ["只复述输入中对判断有帮助的客观事实"],\n'
        '  "observations": ["主动主线、跟随盘、轮动或情绪变化的观察"],\n'
        '  "conditions": ["明日需要验证的条件,没有则为空数组"],\n'
        '  "risks": ["梯队断层、数据不足或持续性风险,没有则为空数组"],\n'
        '  "decision": "一句话落地建议; 若发布策略不允许动作则写空字符串"\n'
        "}"
    )


def generate_ai_rebound(
    facts: dict, timeout: float | None = None, *, return_diagnostics: bool = False,
) -> dict | None | tuple[dict | None, dict]:
    """调用 Claude API；主模型有限重试，失败后可切备用模型，且不阻断日报。"""
    def finish(result, *, reason="", attempt_count=0, http_status=None, extra=None):
        if not return_diagnostics:
            return result
        diagnostics = {"reason": reason, "attempt_count": attempt_count}
        if http_status is not None:
            diagnostics["http_status"] = http_status
        if extra:
            diagnostics.update(extra)
        return result, diagnostics

    if not ai_enabled():
        return finish(None, reason="AI 未启用或缺少 API Key")

    request_timeout = AI_REQUEST_TIMEOUT if timeout is None else timeout
    prompt_facts = _compact_facts_for_prompt(facts)
    prompt = _build_prompt(prompt_facts)

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": _API_VERSION,
        "content-type": "application/json",
    }
    if ANTHROPIC_BETA:
        headers["anthropic-beta"] = ANTHROPIC_BETA

    retryable_statuses = {429, 500, 502, 503, 504}
    model_attempts = []
    primary_attempt_count = 0
    fallback_attempt_count = 0
    last_response = None
    last_error = ""
    successful_response = None

    def call_model(model, attempts, allow_retry):
        nonlocal last_response, last_error
        payload = {
            "model": model,
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}],
        }
        for attempt in range(attempts):
            entry = {"model": model, "attempt": attempt + 1}
            model_attempts.append(entry)
            try:
                response = requests.post(
                    _resolve_api_url(), headers=headers, json=payload, timeout=request_timeout,
                )
                last_response = response
                status = getattr(response, "status_code", None)
                entry["http_status"] = status
                if status == 200:
                    return response
                last_error = f"上游接口返回 {status}"
                entry["retryable"] = status in retryable_statuses
                print(
                    f"  [警告] AI 研判 API 返回 {status} "
                    f"(模型 {model} 第{attempt + 1}/{attempts}次): "
                    f"{str(getattr(response, 'text', ''))[:160]}"
                )
                if status not in retryable_statuses or not allow_retry or attempt + 1 >= attempts:
                    return None
                retry_after = None
                try:
                    retry_after = float(getattr(response, "headers", {}).get("Retry-After"))
                except (AttributeError, TypeError, ValueError):
                    retry_after = None
                delay = retry_after if retry_after is not None else AI_RETRY_BASE_DELAY * (2 ** attempt)
                time.sleep(max(0.0, min(delay, AI_RETRY_MAX_DELAY)))
            except requests.RequestException as exc:
                last_error = f"AI 请求失败：{exc}"
                entry["error_type"] = type(exc).__name__
                entry["error"] = str(exc)
                entry["retryable"] = True
                print(f"  [警告] AI 研判请求异常 (模型 {model} 第{attempt + 1}/{attempts}次): {exc}")
                if not allow_retry or attempt + 1 >= attempts:
                    return None
                delay = min(AI_RETRY_BASE_DELAY * (2 ** attempt), AI_RETRY_MAX_DELAY)
                time.sleep(max(0.0, delay))
        return None

    try:
        successful_response = call_model(ANTHROPIC_MODEL, AI_PRIMARY_MAX_ATTEMPTS, True)
        primary_attempt_count = sum(1 for item in model_attempts if item["model"] == ANTHROPIC_MODEL)
        if successful_response is None and ANTHROPIC_FALLBACK_MODEL and ANTHROPIC_FALLBACK_MODEL != ANTHROPIC_MODEL:
            successful_response = call_model(ANTHROPIC_FALLBACK_MODEL, 1, False)
            fallback_attempt_count = sum(1 for item in model_attempts if item["model"] == ANTHROPIC_FALLBACK_MODEL)

        if successful_response is None:
            status = getattr(last_response, "status_code", None)
            primary_statuses = [
                item.get("http_status")
                for item in model_attempts
                if item.get("model") == ANTHROPIC_MODEL
                and item.get("http_status") is not None
            ]
            repeated_primary_failure = (
                primary_attempt_count >= AI_PRIMARY_MAX_ATTEMPTS
                and len(primary_statuses) == primary_attempt_count
                and len(set(primary_statuses)) == 1
                and primary_statuses[0] in retryable_statuses
            )
            reason = (
                f"上游接口连续 {primary_attempt_count} 次返回 {primary_statuses[0]}"
                if repeated_primary_failure
                else last_error or (
                    f"上游接口返回 {status}"
                    if status is not None else "AI 请求未获得响应"
                )
            )
            return finish(
                None,
                reason=reason,
                attempt_count=primary_attempt_count,
                http_status=status,
                extra={
                    "fallback_model": ANTHROPIC_FALLBACK_MODEL,
                    "fallback_attempt_count": fallback_attempt_count,
                    "model_attempts": model_attempts,
                },
            )

        data = successful_response.json()
        blocks = data.get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        status = getattr(successful_response, "status_code", None)
        extra = {
            "fallback_model": ANTHROPIC_FALLBACK_MODEL,
            "fallback_attempt_count": fallback_attempt_count,
            "model_attempts": model_attempts,
        }
        if not text:
            return finish(None, reason="AI 返回空内容", attempt_count=primary_attempt_count,
                          http_status=status, extra=extra)
        parsed = _parse_json(text)
        if parsed is None:
            return finish(None, reason="AI 返回无法解析的结构化结果", attempt_count=primary_attempt_count,
                          http_status=status, extra=extra)
        return finish(parsed, attempt_count=primary_attempt_count, http_status=status, extra=extra)
    except requests.RequestException as exc:
        # 非 post 位置（例如自定义 requests 适配器）也不让 AI 异常冒泡到日报。
        print(f"  [警告] AI 研判调用失败, 回退规则模板: {exc}")
        return finish(None, reason=f"AI 请求失败：{exc}", attempt_count=primary_attempt_count,
                      extra={"fallback_model": ANTHROPIC_FALLBACK_MODEL,
                             "fallback_attempt_count": fallback_attempt_count,
                             "model_attempts": model_attempts})
    except Exception as exc:
        print(f"  [警告] AI 研判调用失败, 回退规则模板: {exc}")
        return finish(None, reason=f"AI 调用失败：{exc}", attempt_count=primary_attempt_count,
                      extra={"fallback_model": ANTHROPIC_FALLBACK_MODEL,
                             "fallback_attempt_count": fallback_attempt_count,
                             "model_attempts": model_attempts})

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


_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")


def scrub_secrets(text) -> str:
    """把可能混进错误信息里的 API key 换成掩码。

    来源备注要印在报告 HTML 里, 而中转的报错文本偶尔会回显 key,
    所以进入渲染前统一过一遍。
    """
    return _SECRET_RE.sub(lambda m: m.group(0)[:6] + "…", str(text or ""))


def provenance_from_diagnostics(diagnostics: dict | None) -> dict:
    """把 generate_ai_rebound 的 diagnostics 折成渲染用的来源标注。

    真正答复的模型可能是**备用模型** (主模型失败后切换), 所以从 model_attempts 里
    取最后一个 http_status==200 的那次, 而不是直接写 ANTHROPIC_MODEL —— 否则备注
    会声称是主模型写的。
    """
    diag = diagnostics or {}
    model = ""
    for item in diag.get("model_attempts") or ():
        if isinstance(item, dict) and item.get("http_status") == 200:
            model = str(item.get("model") or "")
    return {
        "mode": "ai",
        "model": model or ANTHROPIC_MODEL,
        "attempt_count": diag.get("attempt_count"),
        "http_status": diag.get("http_status"),
    }


def render_ai_rebound_html(ai: dict, facts: dict, char_clr: str,
                           provenance: dict | None = None) -> str:
    """把 AI 研判结果渲染成深色卡片, 视觉对齐既有报告风格。

    硬数据 (当日定性描述) 仍来自规则引擎的 facts, AI 只提供解读文字。
    """
    def _esc(s):
        s = str(s or "")
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    market_char = facts.get("market_char", "")
    char_desc = facts.get("char_desc", "")
    provenance = provenance or {"mode": "ai", "model": ANTHROPIC_MODEL}
    if provenance.get("mode") == "rule_fallback":
        provenance_label = "规则降级"
        provenance_detail = str(provenance.get("reason") or "AI 不可用")
        badge_text = "📐 规则模板 · 非 AI"
        badge_clr, badge_bg = "#8b949e", "rgba(139,148,158,0.15)"
        note_text = ("备注: AI 未能出结果, 本卡片文字为<b>规则模板</b>所写 "
                     f"({_esc(provenance_detail)})。")
    else:
        provenance_label = "AI"
        provenance_detail = str(provenance.get("model") or ANTHROPIC_MODEL)
        # 尝试次数 / HTTP 状态由调用方从 generate_ai_rebound 的 diagnostics 透传,
        # 没有也不影响渲染 (老调用方只给 model)。
        extra = []
        if provenance.get("attempt_count"):
            extra.append(f"第{provenance['attempt_count']}次尝试成功")
        if provenance.get("cache_hit"):
            extra.append("命中输出缓存")
        badge_text = f"🤖 AI 分析 · {_esc(provenance_detail)}"
        badge_clr, badge_bg = "#58a6ff", "rgba(88,166,255,0.15)"
        note_text = (
            "备注: 本卡片的<b>解读文字</b>由 AI 生成 "
            f"(模型 {_esc(provenance_detail)}"
            + (", " + _esc(" · ".join(extra)) if extra else "")
            + "); <b>硬数据</b>(涨跌家数 / 梯队高度 / 主线涨停数) 全部来自规则引擎, "
              "AI 只做解读、不可改写数字。"
        )
    badge = (f'<span style="font-size:12px;font-weight:normal;padding:2px 9px;'
             f'border-radius:10px;background:{badge_bg};color:{badge_clr};'
             f'border:1px solid {badge_clr}55;white-space:nowrap;">{badge_text}</span>')

    def _block(label, val, clr="#e6edf3"):
        if not val:
            return ""
        return (f'<div style="margin-top:8px;font-size:14px;line-height:1.7;">'
                f'<span style="color:#8b949e;">{label}:</span> '
                f'<span style="color:{clr};">{_esc(val)}</span></div>')

    def _field(legacy_key, value=None, default=""):
        if value is None:
            value = ai.get(legacy_key)
        if isinstance(value, list):
            value = "；".join(str(item) for item in value if item not in (None, ""))
        return str(value or default)

    observations = ai.get("observations") if isinstance(ai.get("observations"), list) else []
    conditions = ai.get("conditions") if isinstance(ai.get("conditions"), list) else []
    risks = ai.get("risks") if isinstance(ai.get("risks"), list) else []
    market_summary = _esc(_field("market_summary", default=market_char))
    active_comment = _field("active_comment", observations[0] if observations else None)
    follow_comment = _field("follow_comment", observations[1] if len(observations) > 1 else None)
    gap_comment = _field("gap_comment", risks[0] if risks else (observations[2] if len(observations) > 2 else None))
    evolution = _esc(_field("evolution", conditions[0] if conditions else (observations[-1] if observations else None)))
    operation = _esc(_field("operation", ai.get("decision")))
    evolution_block = (
        f'<div style="margin-top:14px;padding:12px 14px;background:rgba(88,166,255,0.08);'
        f'border-left:3px solid #58a6ff;border-radius:8px;">'
        f'<div style="color:#58a6ff;font-weight:bold;font-size:13px;margin-bottom:4px;">'
        f'🔮 AI 进化研判 (规则外洞察)</div>'
        f'<div style="color:#e6edf3;font-size:14px;line-height:1.7;">{evolution}</div></div>'
        if evolution else "")

    operation_block = (
        f'<div style="margin-top:12px;padding:10px 14px;background:rgba(248,81,73,0.08);'
        f'border-radius:8px;color:#ffa657;font-size:14px;font-weight:bold;">'
        f'🎯 操作建议: {operation}</div>'
        if operation else "")

    return f'''
    <div style="background:#0d1117;border:1px solid #30363d;border-left:4px solid {char_clr};
                border-radius:12px;padding:22px;margin-bottom:30px;color:#c9d1d9;">
        <h2 style="color:{char_clr};font-size:19px;margin:0 0 14px;display:flex;align-items:center;gap:10px;">
            🧭 反弹分类复盘 · AI 研判: {market_summary} {badge}
        </h2>
        <div style="color:#e6edf3;font-size:14px;line-height:1.7;">
            <div><span style="color:#8b949e;">当日定性:</span> {_esc(char_desc)}</div>
            {_block("主动主线", active_comment, "#f85149")}
            {_block("跟随提醒", follow_comment, "#d29922")}
            {_block("梯队结构", gap_comment)}
        </div>
        {evolution_block}
        {operation_block}
        <div style="margin-top:14px;padding-top:12px;border-top:1px dashed #30363d;
                    font-size:12px;color:#8b949e;">
            {note_text}<br>
            分析方式: {_esc(provenance_label)} ({_esc(provenance_detail)}) · 基于规则引擎的客观数据，硬数据不可篡改。
            分类框架: 主动反弹(可追) / 跟随反弹(减亏离场) / 高度断层(回避追高)。
        </div>
    </div>'''
