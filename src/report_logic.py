# -*- coding: utf-8 -*-
"""报表层统一逻辑：口径、质量、情形概率、连板指标和预测闭环。

模块只依赖 Python 标准库，便于单元测试和在主报告入口中安全降级。
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable


_MARKET_PREFIXES_UNSET = object()


_FORBIDDEN_SEMANTICS = {
    "probabilities": ("概率", "胜率", "%概率", "情景占比"),
    "positions": ("空仓观望", "仓位", "满仓", "半仓", "空仓", "几成仓"),
    # 非 decision 模式必须同时屏蔽动作词和容易被读成动作建议的短语。
    # 这些词来自主报告、看板、AI 摘要和 focus pool 的历史输出，统一在
    # 最终渲染层兜底，避免某个视图漏掉门禁。
    "actions": (
        "买入", "卖出", "加仓", "减仓", "清仓", "锁仓", "追高", "抄底",
        "离场", "止损", "止盈", "可追", "回避", "打板", "低吸", "跟随",
        "切忌", "接力", "介入", "开仓", "上车", "兑现", "做多", "做空",
        "建仓", "补仓", "追涨", "高抛", "持仓", "进场", "出场",
    ),
}


@dataclass(frozen=True)
class ReportPolicy:
    """统一控制事实、观察、概率和交易动作的发布能力。"""

    mode: str
    allow_facts: bool = True
    allow_observations: bool = False
    allow_scenarios: bool = False
    allow_probabilities: bool = False
    allow_positions: bool = False
    allow_actions: bool = False
    allow_ai: bool = False
    allow_focus_pool: bool = False

    @classmethod
    def from_mode(cls, mode: Any) -> "ReportPolicy":
        normalized = str(mode or "facts_only").strip().lower()
        if normalized == "decision":
            return cls(normalized, True, True, True, True, True, True, True, True)
        if normalized == "observation":
            return cls(normalized, True, True, True, False, False, False, True, False)
        return cls("facts_only", True, False, False, False, False, False, False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReportContext:
    """一次计算、多端复用的报表上下文。"""

    report_date: str
    policy: ReportPolicy
    quality: dict[str, Any] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)
    observations: dict[str, Any] = field(default_factory=dict)
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    lineage: dict[str, Any] = field(default_factory=dict)
    daily_delta: dict[str, Any] = field(default_factory=dict)
    prediction_review: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "report-context/v1",
            "report_date": self.report_date,
            "publication_mode": self.policy.mode,
            "policy": self.policy.to_dict(),
            "quality": self.quality,
            "facts": self.facts,
            "observations": self.observations if self.policy.allow_observations else {},
            "scenarios": self.scenarios if self.policy.allow_scenarios else [],
            "lineage": self.lineage,
            "daily_delta": self.daily_delta,
            "prediction_review": self.prediction_review,
        }


def apply_price_cache_breadth_calibration(
    advance_decline: dict[str, Any] | None,
    record: dict[str, Any] | None,
    *,
    source_timestamp: str | None = None,
) -> dict[str, Any]:
    """用逐股价格缓存的 A/D 结果校准市场宽度，并同步覆盖数与血缘。"""
    result = dict(advance_decline or {})
    source_chain = list(result.get("source_chain") or [])
    result["source_chain"] = source_chain
    source = dict(record or {})
    try:
        up = max(0, int(float(source.get("up") or 0)))
        down = max(0, int(float(source.get("down") or 0)))
    except (TypeError, ValueError):
        return result
    if up <= 0 or up + down <= 0:
        return result

    result.update({
        "up": up,
        "down": down,
        "flat": None,
        "market_covered": up + down,
        "primary_source": "price_cache",
        "calibration_source": "price_cache",
        "ad_reconciliation_enabled": False,
    })
    if "price_cache" not in source_chain:
        source_chain.append("price_cache")
    timestamp = source_timestamp or source.get("source_timestamp") or source.get("date")
    if timestamp:
        result["source_timestamp"] = str(timestamp)
    date_text = str(source.get("date") or "").replace("-", "")
    if len(date_text) == 8 and date_text.isdigit():
        result["data_timestamp"] = (
            f"{date_text}（价格缓存逐股收盘价计算，平盘不计入 A/D 有效覆盖）"
        )
    return result


def reconcile_limit_pool(
    fupan_ladder: dict[str, Any] | None,
    classified_rows: Iterable[dict[str, Any]] | Any | None,
) -> dict[str, Any]:
    """以 FuPan 天梯为涨停事实池，并对账题材归因池的覆盖与集合差异。"""
    ladder = dict(fupan_ladder or {})
    if isinstance(ladder.get("ladder"), dict):
        ladder = dict(ladder["ladder"])
    category = ladder.get("category") if isinstance(ladder.get("category"), dict) else {}

    authoritative: dict[str, dict[str, Any]] = {}
    for bucket, rows in category.items():
        if not isinstance(rows, (list, tuple)):
            continue
        for raw_row in rows:
            if not isinstance(raw_row, dict):
                continue
            code = normalize_stock_code(
                raw_row.get("code") or raw_row.get("代码") or raw_row.get("symbol")
            )
            if not code:
                continue
            row = dict(raw_row)
            row["code"] = code
            row.setdefault("bucket", str(bucket))
            authoritative.setdefault(code, row)

    rows_source = classified_rows
    if rows_source is None:
        rows: list[dict[str, Any]] = []
    elif hasattr(rows_source, "to_dict"):
        try:
            rows = list(rows_source.to_dict(orient="records"))
        except TypeError:
            converted = rows_source.to_dict()
            rows = list(converted) if isinstance(converted, list) else []
    elif isinstance(rows_source, dict):
        rows = [rows_source]
    else:
        rows = [row for row in rows_source if isinstance(row, dict)]

    classified: dict[str, dict[str, Any]] = {}
    for raw_row in rows:
        code = normalize_stock_code(
            raw_row.get("code") or raw_row.get("代码") or raw_row.get("symbol")
        )
        if code:
            row = dict(raw_row)
            row["code"] = code
            classified.setdefault(code, row)

    authoritative_codes = set(authoritative)
    classified_codes = set(classified)
    matched_codes = authoritative_codes & classified_codes
    fupan_only_codes = authoritative_codes - classified_codes
    cls_only_codes = classified_codes - authoritative_codes
    authoritative_count = len(authoritative_codes)
    matched_count = len(matched_codes)
    coverage_pct = round(
        matched_count / authoritative_count * 100,
        2,
    ) if authoritative_count else 0.0

    warnings: list[str] = []
    if fupan_only_codes:
        warnings.append(
            f"FuPan 涨停事实池中有 {len(fupan_only_codes)} 只尚未完成题材归因"
        )
    if cls_only_codes:
        warnings.append(
            f"题材归因池中有 {len(cls_only_codes)} 只不在 FuPan 当日涨停事实池"
        )

    return {
        "source": "fupan_ladder",
        "classification_source": "classified_limit_pool",
        "authoritative_count": authoritative_count,
        "classified_count": len(classified_codes),
        "matched_count": matched_count,
        "fupan_only_count": len(fupan_only_codes),
        "cls_only_count": len(cls_only_codes),
        "classification_coverage_pct": coverage_pct,
        "authoritative_codes": sorted(authoritative_codes),
        "matched_codes": sorted(matched_codes),
        "fupan_only_codes": sorted(fupan_only_codes),
        "cls_only_codes": sorted(cls_only_codes),
        "authoritative_rows": [authoritative[code] for code in sorted(authoritative)],
        "warnings": warnings,
    }


def policy_from_quality(quality: dict[str, Any] | None) -> ReportPolicy:
    source = dict(quality or {})
    mode = source.get("publication_mode")
    if not mode:
        status = str(source.get("status", "unknown")).lower()
        mode = "decision" if status == "ok" else ("observation" if status == "degraded" else "facts_only")
    return ReportPolicy.from_mode(mode)


def scan_forbidden_semantics(value: Any, policy: ReportPolicy | str) -> list[str]:
    """扫描当前模式不允许出现的交易语义，返回命中的原词。"""
    active = policy if isinstance(policy, ReportPolicy) else ReportPolicy.from_mode(policy)
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    categories: list[str] = []
    if not active.allow_probabilities:
        categories.append("probabilities")
    if not active.allow_positions:
        categories.append("positions")
    if not active.allow_actions:
        categories.append("actions")
    return [term for category in categories for term in _FORBIDDEN_SEMANTICS[category] if term in text]


def neutralize_for_policy(value: Any, policy: ReportPolicy | str) -> str:
    """生产渲染兜底：移除不允许语义并留下中性质量说明。"""
    active = policy if isinstance(policy, ReportPolicy) else ReportPolicy.from_mode(policy)
    text = str(value or "")
    hits = scan_forbidden_semantics(text, active)
    if not hits:
        return text
    for term in sorted(set(hits), key=len, reverse=True):
        text = text.replace(term, "")
    text = re.sub(r"[，,、；;：:]\s*([，,、；;：:]|$)", "，", text).strip(" ，,、；;：:")
    return text or ("当前仅展示已校验事实" if active.mode == "facts_only" else "当前仅展示条件性观察")



def sanitize_html_for_policy(html: Any, policy: ReportPolicy | str) -> str:
    """最终产物兜底：保证降级模式的 HTML 不残留受限语义。"""
    return neutralize_for_policy(html, policy)
def dedupe_quality_messages(values: Iterable[Any] | None) -> list[str]:
    """按展示语义去重质量消息，保持首次出现顺序。

    同一个质量问题经常同时出现在 ``missing_fields``、``errors`` 和
    ``market_state.reason``。报告层不应把同一条告警重复渲染多次。
    """
    result: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result

def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, str) and not value.strip()):
            return default
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_datetime(value: Any) -> datetime | None:
    """解析常见 ISO 时间；无法解析时返回 None，不猜测时间。"""
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

def _parse_date_only(value: Any):
    """仅识别不含时分秒的业务日期，避免把午夜误当作数据采集时刻。"""
    if isinstance(value, datetime):
        return None
    text = str(value or "").strip()
    for pattern, fmt in ((r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"), (r"\d{8}", "%Y%m%d")):
        if not re.fullmatch(pattern, text):
            continue
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            return None
    return None



def binomial_confidence_interval(
    successes: Any,
    trials: Any,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """用 Wilson 区间计算二项命中率置信区间。

    该函数只接受真实成功次数和试验次数。没有样本时返回明确的“样本不足”，
    不会用规则权重或当前单日结果伪造历史统计。
    """
    level = _number(confidence, 0.95)
    if not 0 < level < 1:
        level = 0.95
    n = max(0, _int(trials))
    k = min(n, max(0, _int(successes)))
    if n <= 0:
        return {
            "successes": k,
            "trials": n,
            "rate": None,
            "lower": None,
            "upper": None,
            "confidence_level": level,
            "text": "样本不足",
            "sufficient_sample": False,
        }
    p = k / n
    z = NormalDist().inv_cdf(0.5 + level / 2)
    z2 = z * z
    denominator = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n) / denominator
    lower = max(0.0, centre - margin)
    upper = min(1.0, centre + margin)
    return {
        "successes": k,
        "trials": n,
        "rate": p,
        "lower": lower,
        "upper": upper,
        "confidence_level": level,
        "text": f"{p:.0%}（{level:.0%} CI {lower:.0%}～{upper:.0%}）",
        "sufficient_sample": n >= 10,
    }


def compute_market_ratios(up: Any, down: Any, market_total: Any | None = None) -> dict[str, Any]:
    """同时计算上涨占比和涨跌比，禁止把两者都展示成 A/D。"""
    up_i = max(0, _int(up))
    down_i = max(0, _int(down))
    observed = up_i + down_i
    total = _int(market_total, observed) if market_total is not None else observed
    breadth = up_i / observed if observed else None
    ad = up_i / down_i if down_i else (math.inf if up_i else None)
    return {
        "up": up_i,
        "down": down_i,
        "observed": observed,
        "market_total": total,
        "breadth_ratio": breadth,
        "advance_decline_ratio": ad,
        "valid": observed >= 1000,
    }


def assess_data_quality(
    report_date: str | None = None,
    trade_day: bool = True,
    market_total: Any = 0,
    market_covered: Any = 0,
    primary_source: str | None = None,
    fallback_source: str | None = None,
    used_fallback: bool = False,
    used_stale: bool = False,
    ad_incomplete: bool = False,
    market_scope: str = "沪深北全A",
    market_prefixes: Iterable[str] | object = _MARKET_PREFIXES_UNSET,
    required_market_prefixes: Iterable[str] | None = None,
    missing_fields: Iterable[str] | None = None,
    errors: Iterable[str] | None = None,
    data_timestamp: Any = None,
    source_timestamp: Any = None,
    report_generated_at: Any = None,
    max_delay_minutes: Any = 30,
    stale_after_minutes: Any = 180,
    ad_up: Any = None,
    ad_down: Any = None,
    ad_flat: Any = None,
    ad_reconciliation_enabled: bool = False,
) -> dict[str, Any]:
    """生成可展示、可审计的数据质量摘要。"""
    total = max(0, _int(market_total))
    covered = max(0, _int(market_covered))
    coverage_pct = round(covered / total * 100, 1) if total else 0.0
    missing = dedupe_quality_messages(missing_fields)
    error_list = dedupe_quality_messages(errors)
    # 兼容直接调用本函数的旧代码：没有传入市场前缀时，不把“前缀未知”误判成
    # 真实缺失。生产入口会显式传入实际前缀集合（即使是空集合），此时才执行
    # 沪深北覆盖校验，避免看板通过默认值伪造出 bj 已覆盖。
    prefixes_supplied = market_prefixes is not _MARKET_PREFIXES_UNSET
    raw_prefixes = () if not prefixes_supplied else (market_prefixes or ())
    prefixes = tuple(sorted({str(x).lower().strip() for x in raw_prefixes if str(x).strip()}))
    if required_market_prefixes is not None:
        raw_required = required_market_prefixes
    elif prefixes_supplied and market_scope == "沪深北全A":
        raw_required = ("sh", "sz", "bj")
    else:
        raw_required = ()
    required = tuple(sorted({str(x).lower().strip() for x in raw_required if str(x).strip()}))
    missing_prefixes = sorted(set(required) - set(prefixes))
    if missing_prefixes:
        missing.append("缺少市场代码前缀: " + ",".join(missing_prefixes))

    # 覆盖率必须来自去重后的有效行情记录。涨跌家数是分类统计，不能直接
    # 代替覆盖数；一旦覆盖数超过证券池总数，整份报告进入阻断态。
    if total > 0 and covered > total:
        error_list.append(
            f"COVERAGE_OVERFLOW: 有效覆盖数 {covered} 超过市场总数 {total}"
        )

    # 腾讯逐股源同时返回涨、跌、平三类时，三类之和必须与 covered 对账。
    # FuPan 只有汇总涨跌家数时不启用此校验，避免把未知误判为 0。
    if ad_reconciliation_enabled and total > 0:
        up = max(0, _int(ad_up))
        down = max(0, _int(ad_down))
        flat = max(0, _int(ad_flat))
        if up + down + flat != covered:
            error_list.append(
                "AD_RECONCILIATION_FAILED: 涨跌平家数之和 "
                f"{up + down + flat} 与有效覆盖数 {covered} 不一致"
            )
    missing = dedupe_quality_messages(missing)
    error_list = dedupe_quality_messages(error_list)

    # 新鲜度与“是否使用备用源”分离：备用源可以是实时的，缓存也可能带有
    # 明确的时间戳。没有时间戳时只能标为 unknown，禁止默认伪装成 fresh。
    raw_source_timestamp = source_timestamp or data_timestamp
    source_time = _parse_datetime(raw_source_timestamp)
    source_date_only = _parse_date_only(raw_source_timestamp)
    report_date_only = _parse_date_only(report_date)
    generated_time = _parse_datetime(report_generated_at)
    age_minutes = None
    freshness_level = "unknown"
    freshness_reason = "未提供可核验的数据时间戳"
    if used_stale:
        freshness_level = "stale"
        freshness_reason = "使用历史缓存"
    elif source_date_only and report_date_only and source_date_only == report_date_only:
        age_minutes = 0.0
        freshness_level = "fresh"
        freshness_reason = "数据日期与报告日期一致（收盘日口径）"
    elif source_time and generated_time:
        try:
            source_cmp, generated_cmp = source_time, generated_time
            if source_cmp.tzinfo is not None and generated_cmp.tzinfo is None:
                generated_cmp = generated_cmp.replace(tzinfo=source_cmp.tzinfo)
            elif source_cmp.tzinfo is None and generated_cmp.tzinfo is not None:
                source_cmp = source_cmp.replace(tzinfo=generated_cmp.tzinfo)
            age_minutes = max(0.0, (generated_cmp - source_cmp).total_seconds() / 60)
            delay_limit = max(0.0, _number(max_delay_minutes, 30))
            stale_limit = max(delay_limit, _number(stale_after_minutes, 180))
            if age_minutes <= delay_limit:
                freshness_level = "fresh"
                freshness_reason = f"数据延迟 {age_minutes:.1f} 分钟"
            elif age_minutes <= stale_limit:
                freshness_level = "delayed"
                freshness_reason = f"数据延迟 {age_minutes:.1f} 分钟"
            else:
                freshness_level = "stale"
                freshness_reason = f"数据延迟 {age_minutes:.1f} 分钟，超过陈旧阈值"
        except (TypeError, ValueError):
            freshness_reason = "数据时间戳无法比较"
    if not trade_day:
        status = "non_trading_day"
    elif error_list or ad_incomplete or missing_prefixes or total <= 0 or covered <= 0 or coverage_pct > 100 or coverage_pct < 90:
        status = "blocked"
    elif used_stale or freshness_level in {"delayed", "stale"} or used_fallback or missing or coverage_pct < 98:
        status = "degraded"
    else:
        status = "ok"
    if status == "blocked":
        display_freshness = "blocked"
    elif status == "non_trading_day":
        display_freshness = "unknown"
    else:
        display_freshness = freshness_level
    return {
        "report_date": report_date or "",
        "trade_day": bool(trade_day),
        "market_total": total,
        "market_covered": covered,
        "coverage_pct": coverage_pct,
        "primary_source": primary_source or "未声明",
        "fallback_source": fallback_source or "未配置",
        "used_fallback": bool(used_fallback),
        "used_stale": bool(used_stale),
        "ad_incomplete": bool(ad_incomplete),
        "market_scope": market_scope or "沪深北全A",
        "market_prefixes": list(prefixes),
        "required_market_prefixes": list(required),
        "missing_market_prefixes": missing_prefixes,
        "missing_fields": missing,
        "errors": error_list,
        "ad_up": max(0, _int(ad_up)) if ad_up is not None else None,
        "ad_down": max(0, _int(ad_down)) if ad_down is not None else None,
        "ad_flat": max(0, _int(ad_flat)) if ad_flat is not None else None,
        "status": status,
        "data_timestamp": str(data_timestamp or ""),
        "source_timestamp": str(source_timestamp or ""),
        "report_generated_at": str(report_generated_at or ""),
        "source_age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "freshness_level": display_freshness,
        "freshness_reason": freshness_reason,
    }


def build_market_state(
    quality: dict[str, Any] | None,
    scene: str | None = None,
    historical_samples: Any = None,
    statistics_valid: bool | None = None,
) -> dict[str, Any]:
    """把质量摘要转换成数据层、统计层、决策层三层状态。"""
    quality = quality or {}
    status = str(quality.get("status") or "blocked")
    labels = {
        "ok": "数据正常", "degraded": "数据降级",
        "blocked": "数据阻断", "non_trading_day": "非交易日",
    }
    coverage = _number(quality.get("coverage_pct"), 0.0)
    reasons = []
    if status == "non_trading_day":
        reasons.append("报告日期不是交易日")
    if quality.get("ad_incomplete"):
        reasons.append("全市场涨跌家数未完整就位")
    if quality.get("coverage_pct", 0) > 100:
        reasons.append("有效覆盖数超过市场总数")
    if quality.get("market_total", 0) and coverage < 98:
        reasons.append(f"覆盖率 {coverage:.1f}%")
    if quality.get("used_fallback"):
        reasons.append("使用备用数据源")
    if quality.get("used_stale"):
        reasons.append("使用历史缓存")
    if quality.get("freshness_level") in {"delayed", "stale", "unknown", "blocked"}:
        freshness_reason = quality.get("freshness_reason")
        if freshness_reason:
            reasons.append(str(freshness_reason))
    reasons.extend(str(x) for x in quality.get("missing_fields", []) if str(x).strip())
    reasons.extend(str(x) for x in quality.get("errors", []) if str(x).strip())
    blocked = status in {"blocked", "non_trading_day"}
    sample_value = historical_samples
    if sample_value is None:
        sample_value = quality.get("historical_samples")
    sample_size = max(0, _int(sample_value)) if sample_value is not None else None
    if blocked:
        statistics_status = "blocked"
        statistics_label = "统计不可用"
        allow_probability = False
    elif statistics_valid is False:
        statistics_status = "invalid"
        statistics_label = "统计未通过校验"
        allow_probability = False
    elif sample_size is not None and sample_size > 0 and sample_size < 10:
        statistics_status = "insufficient_sample"
        statistics_label = "样本不足"
        allow_probability = False
    elif sample_size is not None and sample_size >= 10:
        statistics_status = "ok"
        statistics_label = "统计可用"
        allow_probability = True
    else:
        statistics_status = "unverified"
        statistics_label = "统计待核验"
        allow_probability = False

    if blocked:
        decision_status = "blocked"
        decision_label = "禁止强结论"
    elif status == "ok" and statistics_valid is not False and statistics_status == "ok":
        decision_status = "ready"
        decision_label = "可发布规则结论"
    else:
        decision_status = "conditional"
        decision_label = "仅条件性结论"

    data_layer = {
        "status": status,
        "label": labels.get(status, "数据待核验"),
        "can_publish": not blocked,
        "freshness_level": quality.get("freshness_level", "unknown"),
        "coverage_pct": coverage,
    }
    statistics_layer = {
        "status": statistics_status,
        "label": statistics_label,
        "sample_size": sample_size,
        "allow_probability": allow_probability,
        "confidence": "高" if sample_size is not None and sample_size >= 30 else (
            "中" if sample_size is not None and sample_size >= 10 else "低"
        ),
    }
    allow_strong = status == "ok" and statistics_status == "ok"
    allow_observation = status in {"ok", "degraded"} and statistics_status != "blocked"
    publication_mode = (
        "facts_only" if blocked else
        "observation" if not allow_strong else
        "decision"
    )
    decision_layer = {
        "status": decision_status,
        "label": decision_label,
        "allow_strong_conclusion": allow_strong,
        "allow_conditional_conclusion": allow_observation,
        "mode": "strong" if decision_status == "ready" else ("conditional" if not blocked else "blocked"),
        "publication_mode": publication_mode,
    }
    return {
        "status": status,
        "label": labels.get(status, "数据待核验"),
        "can_publish": not blocked,
        "allow_strong_conclusion": allow_strong,
        "allow_conditional_conclusion": allow_observation,
        "allow_observation": allow_observation,
        "allow_focus_pool": allow_strong,
        "publication_mode": publication_mode,
        "allow_scenario_probability": allow_strong,
        "confidence": "高" if status == "ok" else ("中" if status == "degraded" else "低"),
        "reason": "；".join(dedupe_quality_messages(reasons)) or "质量检查通过",
        "scene": str(scene or ""),
        "quality": quality,
        "data_layer": data_layer,
        "statistics_layer": statistics_layer,
        "decision_layer": decision_layer,
    }


def _scenario_weights(
    scene: str | None,
    ad_ratio: Any,
    zt: Any,
    dt: Any,
    curr_h: Any,
    pressure_5d: Any,
    ladder: Any,
    h5: Any,
) -> dict[str, float]:
    """以基础先验叠加盘面证据，返回未归一化权重。"""
    ad = _number(ad_ratio, 0.5)
    zt_i, dt_i = _int(zt), _int(dt)
    curr, pressure, ladder_i, h5_i = _int(curr_h), _int(pressure_5d), _int(ladder), _int(h5)
    weights = {"A": 0.20, "B": 0.30, "C": 0.25, "D": 0.25}
    scene_s = str(scene or "")

    if ad >= 0.65:
        weights["A"] += 0.12
        weights["C"] += 0.04
    elif ad < 0.20:
        weights["D"] += 0.16
        weights["C"] += 0.08
    elif ad < 0.45:
        weights["D"] += 0.14
        weights["C"] += 0.06
    else:
        weights["B"] += 0.06

    if zt_i >= 110:
        weights["A"] += 0.08
        weights["C"] += 0.05
    elif zt_i <= 46:
        weights["D"] += 0.10
    if dt_i > 15:
        weights["D"] += 0.12
    elif dt_i <= 5:
        weights["A"] += 0.04

    if curr > pressure:
        weights["A"] += 0.06
    elif pressure and curr < pressure:
        weights["D"] += 0.06
    if ladder_i >= 12:
        weights["A"] += 0.08
    elif ladder_i < 5:
        weights["D"] += 0.06
    if curr >= 6 and h5_i == 0:
        weights["D"] += 0.12
        weights["A"] -= 0.05

    if "冰点" in scene_s or scene_s.startswith("D_"):
        weights["D"] += 0.08
    elif "主升" in scene_s or scene_s.startswith("E_"):
        weights["A"] += 0.05
    elif "分歧" in scene_s or scene_s.startswith("C_"):
        weights["C"] += 0.08
    return {key: max(0.01, value) for key, value in weights.items()}


def build_scenario_probabilities(**kwargs: Any) -> list[dict[str, Any]]:
    """生成情形统计；规则权重只用于排序，真实样本达标后才对外展示历史比例。"""
    data_quality = kwargs.pop("data_quality", None) or {}
    historical_samples = max(0, _int(kwargs.pop("historical_samples", 0)))
    historical_stats = kwargs.pop("historical_stats", None)
    confidence_level = _number(kwargs.pop("confidence_level", 0.95), 0.95)
    minimum_samples = max(1, _int(kwargs.pop("minimum_samples", 10), 10))
    weights = _scenario_weights(**kwargs)
    total_weight = sum(weights.values()) or 1.0
    model_weights = {code: weights[code] / total_weight for code in weights}
    quality_state = str(data_quality.get("status") or "ok").lower()
    names = {
        "A": ("A · 双龙一字", "attack"),
        "B": ("B · 空间一字 + 接力分歧", "moderate"),
        "C": ("C · 高开分歧 + 二三进阶", "moderate"),
        "D": ("D · 龙头炸板 / 防守", "defense"),
    }

    if quality_state in {"blocked", "non_trading_day", "degraded"}:
        label = "数据未就位" if quality_state in {"blocked", "non_trading_day"} else "条件待校验"
        kind = "suppressed" if quality_state in {"blocked", "non_trading_day"} else "conditional"
        return [{
            "code": code,
            "name": names[code][0],
            "kind": names[code][1],
            "probability": None,
            "probability_pct": None,
            "prob": label,
            "confidence": "低",
            "sample_size": 0,
            "probability_kind": kind,
            "historical_rate": None,
            "confidence_interval": None,
            "confidence_interval_text": "样本不足",
            "confidence_interval_estimated": False,
            "model_weight": model_weights[code],
        } for code in ("A", "B", "C", "D")]

    rows = []
    for code in ("A", "B", "C", "D"):
        stat_row = historical_stats.get(code) if isinstance(historical_stats, dict) else None
        if not isinstance(stat_row, dict):
            stat_row = {}
        stat_trials = max(0, _int(stat_row.get("trials", stat_row.get("sample_size", 0))))
        raw_successes = stat_row.get("successes", stat_row.get("hits"))
        if raw_successes is None:
            raw_successes = stat_row.get("t3_hits", stat_row.get("t1_hits"))
        estimated_successes = False
        if raw_successes is None and stat_trials > 0:
            rate = stat_row.get("rate", stat_row.get("t3_hit_rate", stat_row.get("t1_hit_rate")))
            if rate is not None:
                estimated_successes = True
                raw_successes = round(_number(rate) * stat_trials)
        interval = (
            binomial_confidence_interval(raw_successes, stat_trials, confidence_level)
            if raw_successes is not None and stat_trials > 0
            else None
        )
        sufficient = bool(interval and stat_trials >= minimum_samples)
        historical_rate = interval.get("rate") if interval else None
        published_rate = historical_rate if sufficient else None
        confidence = "高" if stat_trials >= 30 else ("中" if sufficient else "低")
        rows.append({
            "code": code,
            "name": names[code][0],
            "kind": names[code][1],
            "probability": published_rate,
            "probability_pct": round(published_rate * 100) if published_rate is not None else None,
            "prob": f"历史样本 {round(published_rate * 100)}%" if published_rate is not None else "样本不足",
            "confidence": confidence,
            "sample_size": stat_trials,
            "probability_kind": "historical_rate" if sufficient else "insufficient_history",
            "historical_rate": historical_rate if sufficient else None,
            "confidence_interval": interval if sufficient else None,
            "confidence_interval_text": interval.get("text") if sufficient else "样本不足",
            "confidence_interval_estimated": estimated_successes,
            "model_weight": model_weights[code],
            "historical_samples": historical_samples,
        })
    return rows

def normalize_catalyst(value: Any) -> dict[str, str]:
    """清洗催化字段，禁止 Python None 泄漏到 HTML。"""
    if not isinstance(value, dict):
        value = {}
    tag = value.get("tag")
    text = value.get("text")
    url = value.get("url")
    return {
        "tag": str(tag).strip() if tag not in (None, "", "None", "nan") else "无近期催化",
        "text": str(text).strip() if text not in (None, "", "None", "nan") else "",
        "url": str(url).strip() if url not in (None, "", "None", "nan") else "",
    }


def _height(item: Any) -> int:
    raw = item.get("height", item.get("连板数", item.get("连板", 0))) if isinstance(item, dict) else item
    if isinstance(raw, str):
        text = raw.strip()
        if "首板" in text:
            return 1
        match = re.search(r"(?<!\d)(\d+)\s*(?:连板|板)?", text)
        if match:
            raw = match.group(1)
    return max(0, _int(raw))


def _row_code(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("code", "代码", "证券代码", "股票代码"):
        value = normalize_stock_code(item.get(key))
        if value:
            return value
    return ""


def _row_flag(item: Any, keys: Iterable[str]) -> bool | None:
    if not isinstance(item, dict):
        return None
    for key in keys:
        if key not in item:
            continue
        value = item.get(key)
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "是", "成功", "回封", "一字板", "换手板"}:
            return True
        if text in {"0", "false", "no", "n", "否", "失败", "未回封"}:
            return False
    return None


def _row_board_type(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    for key in ("board_type", "板型", "涨停类型", "limit_type", "type"):
        value = str(item.get(key) or "").strip().lower()
        if not value:
            continue
        if "一字" in value or value in {"one_word", "one-word", "一字"}:
            return "one_word"
        if "换手" in value or value in {"turnover", "换手"}:
            return "turnover"
    return None


def _rate_result(successes: int, trials: int, label: str) -> dict[str, Any]:
    if trials <= 0:
        return {"rate": None, "text": "样本不足", "successes": 0, "trials": 0, "label": label}
    rate = successes / trials
    return {
        "rate": round(rate, 4),
        "text": f"{successes}/{trials}（{rate:.0%}）",
        "successes": successes,
        "trials": trials,
        "label": label,
    }


def compute_ladder_metrics(
    echelon: Iterable[Any] | None,
    previous_echelon: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """计算梯队结构，并补充晋级率、炸板和孤悬龙头风险。"""
    items = list(echelon or [])
    heights = [_height(item) for item in items]
    heights = [height for height in heights if height > 0]
    counts = {3: 0, 4: 0, 5: 0, 6: 0}
    board_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0}
    for height in heights:
        bucket = 6 if height >= 6 else height
        if bucket in board_counts:
            board_counts[bucket] += 1
        if bucket in counts:
            counts[bucket] += 1
    max_height = max(heights, default=0)
    present = {height if height < 6 else 6 for height in heights}
    gap_heights = [height for height in range(3, max_height) if height not in present]
    ladder = counts[3] + counts[4] * 2 + counts[5] * 3 + counts[6] * 4
    previous_heights = [_height(item) for item in (previous_echelon or [])]
    previous_heights = [height for height in previous_heights if height > 0]
    previous_max = max(previous_heights, default=0)
    # 定义为今日梯队突破昨日最高高度的占比，避免把同层级个股误算成晋级。
    progressed = sum(1 for height in heights if height > previous_max)
    progression_rate = round(progressed / len(heights), 3) if heights and previous_heights else None
    broken_count = sum(1 for item in items if isinstance(item, dict) and (
        item.get("broken") is True or item.get("炸板") is True or str(item.get("status", "")).lower() in {"broken", "炸板"}
    ))

    # 只有同时具备前后两日证券代码时，才计算真实的晋级/断板率；不能用
    # 今日静态高度分布冒充历史转移率。
    current_by_code = {_row_code(item): item for item in items if _row_code(item)}
    previous_items = [item for item in (previous_echelon or []) if _height(item) > 0]
    previous_by_code = {_row_code(item): item for item in previous_items if _row_code(item)}
    current_sample_size = len(heights)
    current_code_count = len(current_by_code)
    previous_sample_size = len(previous_heights)
    previous_code_count = len(previous_by_code)
    transition_match_count = sum(1 for code in previous_by_code if code in current_by_code)
    transition_coverage_pct = (
        round(transition_match_count / previous_code_count * 100, 2)
        if previous_code_count else None
    )
    transition_rows = []
    for code, previous in previous_by_code.items():
        current = current_by_code.get(code)
        prev_height = _height(previous)
        curr_height = _height(current) if current is not None else 0
        transition_rows.append((prev_height, curr_height))

    advancement: dict[str, dict[str, Any]] = {}
    for level in range(1, 6):
        rows = [(prev, curr) for prev, curr in transition_rows if prev == level]
        successes = sum(1 for prev, curr in rows if curr >= level + 1)
        advancement[f"{level}_to_{level + 1}"] = _rate_result(
            successes, len(rows), f"{level}板→{level + 1}板晋级率"
        )
    broken_trials = [(prev, curr) for prev, curr in transition_rows if prev >= 2]
    broken_successes = sum(1 for prev, curr in broken_trials if curr <= prev)
    broken_rate = _rate_result(broken_successes, len(broken_trials), "昨日连板池今日断板率")

    # 对外单独给出三个常用口径，避免把首板、昨日连板和全涨停池混成一个分母。
    first_board = advancement.get("1_to_2", _rate_result(0, 0, "首板→二板晋级率"))
    streak_rows = [(prev, curr) for prev, curr in transition_rows if prev >= 2]
    streak_successes = sum(1 for prev, curr in streak_rows if curr >= prev + 1)
    streak_promotion = _rate_result(streak_successes, len(streak_rows), "昨日连板池晋级率（高度≥2）")
    streak_current_count = sum(1 for prev, curr in streak_rows if curr >= 2)
    all_limit_up = _rate_result(
        sum(1 for _prev, curr in transition_rows if curr >= 1),
        len(transition_rows),
        "昨日涨停池次日再板率",
    )

    highest_board_count = sum(1 for height in heights if height == max_height) if max_height else 0
    leader_concentration_pct = (
        round(highest_board_count / current_sample_size * 100, 2)
        if current_sample_size else None
    )
    if not current_sample_size:
        sample_status = "not_ready"
        sample_reason = "当前有效梯队为空"
    elif not previous_code_count:
        sample_status = "insufficient"
        sample_reason = "昨日梯队缺少可匹配证券代码"
    elif transition_match_count < 3 or (transition_coverage_pct or 0) < 80:
        sample_status = "conditional"
        sample_reason = "前后日匹配样本不足或覆盖率低于80%"
    else:
        sample_status = "ok"
        sample_reason = "前后日梯队匹配样本和覆盖率满足最低要求"

    bomb_total = 0
    bomb_count = 0
    reclose_total = 0
    reclose_count = 0
    one_word_count = 0
    turnover_count = 0
    board_type_count = 0
    for item in items:
        attempted = _row_flag(item, ("limit_up_attempted", "曾涨停", "炸板样本", "attempted", "封板尝试"))
        broken = _row_flag(item, ("broken", "炸板", "炸板标记"))
        if attempted is not None:
            bomb_total += 1
            if broken is True:
                bomb_count += 1
                reclosed = _row_flag(item, ("reclosed", "回封", "回封成功", "reclose"))
                if reclosed is not None:
                    reclose_total += 1
                    if reclosed:
                        reclose_count += 1
        board_type = _row_board_type(item)
        if board_type:
            board_type_count += 1
            if board_type == "one_word":
                one_word_count += 1
            elif board_type == "turnover":
                turnover_count += 1
    bomb_rate = _rate_result(bomb_count, bomb_total, "炸板率")
    reclose_rate = _rate_result(reclose_count, reclose_total, "炸板后回封率")
    board_structure = {
        "one_word_count": one_word_count if board_type_count else None,
        "turnover_count": turnover_count if board_type_count else None,
        "sample_size": board_type_count,
        "text": (
            f"一字板 {one_word_count} / 换手板 {turnover_count}"
            if board_type_count else "数据未就位"
        ),
    }

    quality_score = None
    quality_text = "数据未就位"
    quality_components: dict[str, Any] = {}
    available_scores = []
    if broken_rate["rate"] is not None:
        quality_components["continuation"] = 1 - broken_rate["rate"]
        available_scores.append((quality_components["continuation"], 0.5))
    if bomb_rate["rate"] is not None:
        quality_components["no_bomb"] = 1 - bomb_rate["rate"]
        available_scores.append((quality_components["no_bomb"], 0.3))
    if reclose_rate["rate"] is not None:
        quality_components["reclose"] = reclose_rate["rate"]
        available_scores.append((quality_components["reclose"], 0.2))
    if available_scores:
        weight = sum(weight for _, weight in available_scores)
        quality_score = round(sum(value * w for value, w in available_scores) / weight * 100, 1)
        quality_text = f"{quality_score:.1f}/100（基于已提供样本）"

    isolated_leader = max_height >= 6 and sum(1 for height in heights if height >= max_height - 1) <= 1
    gap_text = '、'.join(f'缺{height}板' for height in gap_heights) if gap_heights else '无明显断层'
    gap_risk_label = '高' if (max_height >= 6 and (gap_heights or isolated_leader)) else ('中' if gap_heights else '低')
    return {
        "height": max_height, "ladder": ladder, "counts": counts,
        "board_counts": board_counts,
        "first_board_count": board_counts[1],
        "second_board_count": board_counts[2],
        "h3": counts[3], "h4": counts[4], "h5": counts[5], "h6p": counts[6],
        "gap_heights": gap_heights, "gap_risk": bool(max_height >= 6 and (gap_heights or isolated_leader)),
        "gap_text": gap_text, "gap_risk_label": gap_risk_label,
        "height_count": len(heights), "progression_rate": progression_rate,
        "progression_label": "突破昨日最高高度占比",
        "progression_definition": "今日梯队中高度超过昨日最高板的个股数 / 今日有效梯队个股数",
        "progression_denominator": "今日有效梯队个股数",
        "progression_text": (f"{progressed}/{len(heights)}" if previous_heights else "样本不足"),
        "progressed_count": progressed, "previous_count": len(previous_heights),
        "current_sample_size": current_sample_size,
        "current_code_count": current_code_count,
        "previous_sample_size": previous_sample_size,
        "previous_code_count": previous_code_count,
        "transition_match_count": transition_match_count,
        "transition_coverage_pct": transition_coverage_pct,
        "transition_status": sample_status,
        "sample_status": sample_status,
        "sample_reason": sample_reason,
        "highest_board_count": highest_board_count,
        "leader_concentration_pct": leader_concentration_pct,
        "broken_count": broken_count, "isolated_leader": isolated_leader,
        "advancement_rates": advancement,
        "first_board_to_second": first_board,
        "streak_pool_promotion": streak_promotion,
        # 连板池统计的分母必须来自昨日连板池的逐股转移样本，不能用今日静态高度分布替代。
        "streak_pool_sample_size": len(streak_rows),
        "streak_pool_trials": len(streak_rows),
        "streak_pool_observed_sample_size": len(streak_rows),
        "streak_pool_current_count": streak_current_count,
        "all_limit_up_reclose": all_limit_up,
        "broken_rate": broken_rate,
        "bomb_rate": bomb_rate,
        "reclose_rate": reclose_rate,
        "board_structure": board_structure,
        "quality_score": quality_score,
        "quality_text": quality_text,
        "quality_components": quality_components,
    }



def _wilson_interval(successes: Any, trials: Any, confidence: float = 0.95) -> dict[str, Any]:
    """Return a Wilson score interval without treating a zero-sample rate as 0%."""
    successes = max(0, _int(successes))
    trials = max(0, _int(trials))
    if trials <= 0:
        return {"low": None, "high": None, "confidence": confidence, "successes": 0, "trials": 0}
    successes = min(successes, trials)
    z = NormalDist().inv_cdf(0.5 + confidence / 2)
    p = successes / trials
    denominator = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * trials)) / trials) / denominator
    return {
        "low": round(max(0.0, centre - margin), 4),
        "high": round(min(1.0, centre + margin), 4),
        "confidence": confidence,
        "successes": successes,
        "trials": trials,
    }


def build_lianban_review(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Build a conservative连板复盘摘要 from one ladder-metrics result."""
    metrics = metrics if isinstance(metrics, dict) else {}
    board_counts = metrics.get("board_counts") or {}
    first_count = _int(metrics.get("first_board_count", board_counts.get(1, 0)))
    second_count = _int(metrics.get("second_board_count", board_counts.get(2, 0)))
    first_board = metrics.get("first_board_to_second") or metrics.get("advancement_rates", {}).get("1_to_2") or {}
    streak_pool = metrics.get("streak_pool_promotion") or {}
    first_successes = _int(first_board.get("successes"))
    first_trials = _int(first_board.get("trials"))
    streak_trials = _int(streak_pool.get("trials", metrics.get("streak_pool_trials", metrics.get("streak_pool_sample_size"))))
    streak_observed = _int(metrics.get("streak_pool_observed_sample_size", streak_trials))
    streak_current_count = _int(metrics.get("streak_pool_current_count"))
    current_sample_size = _int(metrics.get("current_sample_size", metrics.get("height_count")))
    previous_sample_size = _int(metrics.get("previous_sample_size", metrics.get("previous_count")))
    transition_match_count = _int(metrics.get("transition_match_count"))
    previous_code_count = _int(metrics.get("previous_code_count"))
    transition_coverage_pct = metrics.get("transition_coverage_pct")
    sample_status = str(metrics.get("sample_status") or metrics.get("transition_status") or "").lower()
    if sample_status not in {"ok", "conditional", "insufficient", "not_ready"}:
        if current_sample_size <= 0:
            sample_status = "not_ready"
        elif first_trials > 0 or streak_trials > 0:
            sample_status = "conditional"
        else:
            sample_status = "insufficient"
    if sample_status == "ok" and not (first_trials > 0 and streak_trials > 0):
        sample_status = "insufficient"
    status = sample_status
    first_text = first_board.get("text") if first_trials else "样本不足"
    streak_text = streak_pool.get("text") if streak_trials else "样本不足"
    negative_feedback = {
        "broken_rate": metrics.get("broken_rate") or {"text": "样本不足", "trials": 0},
        "bomb_rate": metrics.get("bomb_rate") or {"text": "样本不足", "trials": 0},
        "reclose_rate": metrics.get("reclose_rate") or {"text": "样本不足", "trials": 0},
        "broken_count": _int(metrics.get("broken_count")),
        "text": "、".join(
            str(item.get("text") or "样本不足")
            for item in (metrics.get("broken_rate") or {}, metrics.get("bomb_rate") or {})
        ) or "样本不足",
    }
    if status == "not_ready":
        conclusion = "连板复盘：当前梯队数据未就位，暂不输出结构性结论。"
    elif status == "insufficient":
        conclusion = "连板复盘：样本不足，不能外推晋级率或连板池强弱。"
    elif status == "conditional":
        conclusion = f"首板→二板 {first_text}；昨日连板池晋级率 {streak_text}。前后日匹配覆盖不足，仅作条件性观察。"
    else:
        conclusion = f"首板→二板 {first_text}；昨日连板池晋级率 {streak_text}。"
    return {
        "status": status,
        "status_label": {
            "ok": "可用", "conditional": "条件性可用",
            "insufficient": "样本不足", "not_ready": "数据未就位",
        }.get(status, "待核验"),
        "current_sample_size": current_sample_size,
        "previous_sample_size": previous_sample_size,
        "transition_sample_size": transition_match_count,
        "transition_match_count": transition_match_count,
        "previous_code_count": previous_code_count,
        "transition_coverage_pct": transition_coverage_pct,
        "highest_board_count": _int(metrics.get("highest_board_count")),
        "leader_concentration_pct": metrics.get("leader_concentration_pct"),
        "sample_status": status,
        "sample_reason": metrics.get("sample_reason") or "未提供样本质量说明",
        "first_board_count": first_count,
        "second_board_count": second_count,
        "board_counts": {int(k): _int(v) for k, v in board_counts.items()} if board_counts else {},
        "first_board_to_second": {
            **first_board,
            "text": first_text,
            "successes": first_successes,
            "trials": first_trials,
        },
        "streak_pool_sample_size": streak_trials,
        "streak_pool_trials": streak_trials,
        "streak_pool_observed_sample_size": streak_observed,
        "streak_pool_current_count": streak_current_count,
        "streak_pool_promotion": {**streak_pool, "text": streak_text, "trials": streak_trials},
        "confidence_interval": _wilson_interval(first_successes, first_trials),
        "negative_feedback": negative_feedback,
        "conclusion": conclusion,
    }


def build_data_credibility_summary(
    quality: dict[str, Any] | None,
    *,
    report_date: str | None = None,
    report_generated_at: str | None = None,
) -> dict[str, Any]:
    """Normalize module-level quality into a user-facing, auditable summary."""
    quality = quality if isinstance(quality, dict) else {}
    modules: dict[str, dict[str, Any]] = {}
    available: list[str] = []
    degraded: list[str] = []
    unavailable: list[str] = []
    blocked: list[str] = []
    publishable: list[str] = []
    reasons: list[str] = []
    source_failure = 0
    stale = 0
    missing = 0
    used_fallback = bool(quality.get("used_fallback"))
    used_stale = bool(quality.get("used_stale"))
    source_chain = list(quality.get("source_chain") or []) if isinstance(quality.get("source_chain"), (list, tuple)) else []
    freshness_levels: list[str] = []
    market_prefixes = set(str(value).lower().strip() for value in (quality.get("market_prefixes") or ()) if str(value).strip())
    required_market_prefixes = set(str(value).lower().strip() for value in (quality.get("required_market_prefixes") or ()) if str(value).strip())
    for name, raw in (quality.get("modules") or {}).items():
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        module_name = str(name)
        total = max(0, _int(item.get("total", quality.get("market_total", 0))))
        covered = max(0, _int(item.get("covered", 0)))
        raw_pct = round(covered / total * 100, 2) if total else 0.0
        status = str(item.get("status") or ("unavailable" if covered == 0 else "ok")).lower()
        lineage = item.get("lineage") if isinstance(item.get("lineage"), dict) else {}
        used_fallback = used_fallback or bool(item.get("used_fallback") or lineage.get("used_fallback"))
        used_stale = used_stale or bool(item.get("used_stale") or lineage.get("stale") or lineage.get("used_stale"))
        if lineage.get("source_chain") and isinstance(lineage.get("source_chain"), (list, tuple)):
            source_chain.extend(str(value) for value in lineage.get("source_chain") if str(value).strip())
        freshness = str(item.get("freshness_level") or lineage.get("freshness_level") or "").lower()
        if freshness:
            freshness_levels.append(freshness)
        for value in item.get("market_prefixes") or lineage.get("market_prefixes") or ():
            if str(value).strip():
                market_prefixes.add(str(value).lower().strip())
        price_basis = str(lineage.get("price_basis") or "")
        legacy_mixed = price_basis == "legacy_mixed"
        effective_covered = covered
        if legacy_mixed and module_name.startswith("price_"):
            effective_covered = 0
            if status == "ok":
                status = "degraded"
            reasons.append(f"{module_name} 使用 legacy_mixed 价格口径，覆盖不可核验")
        effective_pct = round(effective_covered / total * 100, 2) if total else 0.0
        item.update({"status": status, "total": total, "covered": covered, "coverage_pct": raw_pct, "effective_covered": effective_covered, "effective_coverage_pct": effective_pct, "legacy_mixed": legacy_mixed})
        modules[module_name] = item
        missing_fields = item.get("missing_fields") or []
        errors = item.get("errors") or []
        missing += len(missing_fields) if isinstance(missing_fields, (list, tuple, set)) else int(bool(missing_fields))
        if status in {"unavailable", "blocked", "error", "failed"}:
            if status == "unavailable": unavailable.append(module_name)
            else: blocked.append(module_name)
            source_failure += 1
        elif errors:
            source_failure += 1
        if status == "stale" or item.get("used_stale") or lineage.get("stale"):
            stale += 1
        if status == "degraded" or legacy_mixed: degraded.append(module_name)
        if status == "ok": available.append(module_name)
        if status in {"ok", "degraded", "stale"}:
            publishable.append(module_name)
        for message in list(missing_fields if isinstance(missing_fields, (list, tuple, set)) else [missing_fields]):
            if str(message).strip(): reasons.append(f"{module_name}: {message}")
        for message in list(errors if isinstance(errors, (list, tuple, set)) else [errors]):
            if str(message).strip(): reasons.append(f"{module_name}: {message}")
    reasons.extend(str(value) for value in (quality.get("errors") or []) if str(value).strip())
    reasons.extend(str(value) for value in (quality.get("missing_fields") or []) if str(value).strip())
    overall = str(quality.get("status") or "blocked").lower()
    if blocked: overall = "blocked"
    elif unavailable or degraded or source_failure or stale:
        overall = "degraded" if overall not in {"blocked", "non_trading_day"} else overall
    freshness_level = str(quality.get("freshness_level") or "").lower()
    if not freshness_level:
        freshness_level = next((level for level in ("stale", "delayed", "unknown", "fresh") if level in freshness_levels), "unknown")
    missing_market_prefixes = sorted(required_market_prefixes - market_prefixes)
    primary_source = quality.get("primary_source") or quality.get("source")
    fallback_source = quality.get("fallback_source")
    if primary_source and primary_source not in source_chain:
        source_chain.insert(0, str(primary_source))
    if fallback_source and fallback_source not in source_chain:
        source_chain.append(str(fallback_source))
    return {
        "report_date": report_date or quality.get("report_date") or "",
        "report_generated_at": report_generated_at or quality.get("report_generated_at"),
        "market_scope": quality.get("market_scope") or "沪深北全A",
        "market_total": max(0, _int(quality.get("market_total"))),
        "market_covered": max(0, _int(quality.get("market_covered"))),
        "primary_source": primary_source,
        "fallback_source": fallback_source,
        "source_chain": list(dict.fromkeys(source_chain)),
        "used_fallback": used_fallback,
        "used_stale": used_stale,
        "freshness_level": freshness_level,
        "freshness_reason": quality.get("freshness_reason") or "未提供可核验的新鲜度说明",
        "source_timestamp": quality.get("source_timestamp") or "",
        "data_timestamp": quality.get("data_timestamp") or "",
        "market_prefixes": sorted(market_prefixes),
        "required_market_prefixes": sorted(required_market_prefixes),
        "missing_market_prefixes": missing_market_prefixes,
        "publication_mode": quality.get("publication_mode"),
        "status": overall,
        "modules": modules,
        "available_modules": available,
        "degraded_modules": degraded,
        "unavailable_modules": unavailable,
        "blocked_modules": blocked,
        "publishable_modules": list(dict.fromkeys(publishable)),
        "missing": missing,
        "source_failure": source_failure,
        "stale": stale,
        "reasons": list(dict.fromkeys(reasons)),
    }


def build_mainline_review(
    metrics: dict[str, Any] | None,
    *,
    limit_up_count: Any | None = None,
    attribution_source: str | None = None,
) -> dict[str, Any]:
    """Build a conservative mainline summary that respects attribution coverage."""
    metrics = metrics if isinstance(metrics, dict) else {}
    distribution = metrics.get("distribution") if isinstance(metrics.get("distribution"), list) else []
    top3 = [{"name": str(item.get("name") or item.get("mainline") or ""), "share": item.get("share"), "weight": item.get("weight")} for item in distribution[:3] if isinstance(item, dict)]
    top1 = str(metrics.get("top_mainline") or (top3[0]["name"] if top3 else ""))
    authoritative_raw = metrics.get("authoritative_count")
    if authoritative_raw is None:
        authoritative_raw = limit_up_count
    authoritative_count = max(0, _int(authoritative_raw)) if authoritative_raw is not None else None
    attributed_raw = metrics.get("attributed_count")
    if attributed_raw is None and authoritative_count is not None:
        attributed_raw = metrics.get("sample_size")
    attributed_count = max(0, _int(attributed_raw)) if attributed_raw is not None else None
    if authoritative_count is not None and attributed_count is not None:
        attributed_count = min(attributed_count, authoritative_count)
    unattributed_count = (
        max(authoritative_count - attributed_count, 0)
        if authoritative_count is not None and attributed_count is not None else None
    )
    coverage = metrics.get("attribution_coverage_pct")
    try: coverage_value = float(coverage) if coverage is not None else None
    except (TypeError, ValueError): coverage_value = None
    if coverage_value is None and authoritative_count and attributed_count is not None:
        coverage_value = round(attributed_count / authoritative_count * 100, 2)
    level = str(metrics.get("conclusion_level") or "insufficient")
    if not top1:
        conclusion = "主线归因数据未就位，不能形成主线结论。"; level = "insufficient"
    elif coverage_value is not None and coverage_value < 80:
        conclusion = f"已归因样本中的领先方向：{top1}；归因覆盖率 {coverage_value:.2f}%，不外推全市场主线。"; level = "insufficient"
    else:
        conclusion = f"主线集中度领先方向：{top1}。"
    return {
        "top1": top1,
        "top3": top3,
        "hhi": metrics.get("hhi"),
        "limit_up_count": authoritative_count,
        "authoritative_count": authoritative_count,
        "lianban_count": _int(metrics.get("sample_size")),
        "attributed_count": attributed_count,
        "unattributed_count": unattributed_count,
        "attribution_coverage_pct": coverage_value,
        "top_share_attributed_sample": metrics.get("top_share_attributed_sample", metrics.get("top_share")),
        "top_share_authoritative_pool": metrics.get("top_share_authoritative_pool"),
        "attribution_source": attribution_source or metrics.get("attribution_source"),
        "conclusion_level": level,
        "conclusion": conclusion,
    }


def compute_mainline_concentration(
    rows: Iterable[Any] | None,
    *,
    authoritative_count: Any | None = None,
    attributed_count: Any | None = None,
) -> dict[str, Any]:
    """按连板高度权重计算主线集中度，并显式反映归因覆盖率。"""
    weights: dict[str, float] = {}
    valid = 0
    for item in rows or ():
        if not isinstance(item, dict):
            continue
        mainline = str(
            item.get("mainline", item.get("大主线", item.get("主线", item.get("sector", "")))) or ""
        ).strip()
        height = _height(item)
        if not mainline or height <= 0:
            continue
        weights[mainline] = weights.get(mainline, 0.0) + max(1.0, float(height))
        valid += 1
    total_weight = sum(weights.values())
    if valid <= 0 or total_weight <= 0:
        return {
            "sample_size": 0, "top_mainline": "", "top_share": None,
            "hhi": None, "text": "数据未就位", "conclusion_level": "insufficient",
            "authoritative_count": max(0, _int(authoritative_count)) if authoritative_count is not None else None,
            "attributed_count": max(0, _int(attributed_count)) if attributed_count is not None else 0,
            "attribution_coverage_pct": 0.0 if authoritative_count else None,
            "top_share_attributed_sample": None,
            "top_share_authoritative_pool": None,
        }
    ordered = sorted(weights.items(), key=lambda pair: (-pair[1], pair[0]))
    shares = [value / total_weight for _, value in ordered]
    top_name, top_weight = ordered[0]
    authoritative = max(0, _int(authoritative_count)) if authoritative_count is not None else 0
    attributed = max(0, _int(attributed_count)) if attributed_count is not None else 0
    if authoritative:
        attributed = min(attributed, authoritative)
        attribution_coverage = round(attributed / authoritative, 4)
    else:
        attribution_coverage = None
    conservative_share = (
        round((top_weight / total_weight) * attribution_coverage, 4)
        if attribution_coverage is not None else None
    )
    conclusion_level = (
        "strong" if attribution_coverage is None or attribution_coverage >= 0.8
        else ("conditional" if attribution_coverage >= 0.6 else "insufficient")
    )
    prefix = "" if conclusion_level == "strong" else "已归因样本中的领先方向："
    return {
        "sample_size": valid,
        "mainline_count": len(ordered),
        "top_mainline": top_name,
        "top_share": round(top_weight / total_weight, 4),
        "hhi": round(sum(share * share for share in shares), 4),
        "text": f"{prefix}{top_name} {top_weight / total_weight:.0%}（{valid}只有效样本）",
        "attribution_coverage_pct": round(attribution_coverage * 100, 2) if attribution_coverage is not None else None,
        "attributed_count": attributed if authoritative else valid,
        "authoritative_count": authoritative or None,
        "top_share_attributed_sample": round(top_weight / total_weight, 4),
        "top_share_authoritative_pool": conservative_share,
        "conclusion_level": conclusion_level,
        "distribution": [{"name": name, "weight": weight, "share": round(weight / total_weight, 4)} for name, weight in ordered],
    }

def save_prediction_snapshot(path: str | Path, **fields: Any) -> dict[str, Any]:
    """以 report_date 为幂等键保存预测快照。

    日报经常会因为补数、重跑或 CI 重试而在同一天执行多次。重复追加同一
    日期会让后续胜率统计把一条预测算成多条样本，因此默认替换同日旧记录。
    通过 ``replace_existing=False`` 可临时保留历史重跑记录。
    """
    replace_existing = bool(fields.pop("replace_existing", True))
    row = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "report_date": str(fields.pop("report_date", "")),
        **fields,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = load_prediction_snapshots(path)
    if replace_existing and row["report_date"]:
        rows = [old for old in rows if str(old.get("report_date", "")) != row["report_date"]]
    rows.append(row)
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for value in rows:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
    tmp_path.replace(path)
    return row


def load_prediction_snapshots(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _direction_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "up" if value else "down"
    text = str(value).strip().lower()
    if text in {"up", "上涨", "上行", "positive", "1", "true"}:
        return "up"
    if text in {"down", "下跌", "下行", "negative", "0", "false"}:
        return "down"
    if text in {"flat", "平盘", "震荡", "neutral"}:
        return "flat"
    return None


def _horizon_actual(actual: dict[str, Any], horizon: str) -> dict[str, Any]:
    """兼容嵌套 T+1/T+3 和旧版平铺 actual 结构。"""
    nested = actual.get(horizon)
    if isinstance(nested, dict):
        return nested
    prefix = f"{horizon}_"
    flat = {key[len(prefix):]: value for key, value in actual.items() if str(key).startswith(prefix)}
    if flat:
        return flat
    if horizon == "t1" and any(key in actual for key in ("market_up", "market_direction", "focus_hits", "max_height")):
        return actual
    return {}


def _evaluate_horizon(prediction: dict[str, Any], actual: dict[str, Any], horizon: str) -> dict[str, Any]:
    expected_pool = {str(x) for x in prediction.get("focus_pool", [])}
    block = _horizon_actual(actual, horizon)
    if not block:
        return {
            "evaluated": False,
            "reason": f"{horizon.upper()}数据尚未就位",
            "market_direction": None,
            "focus_pool_hit": None,
            "space_height_observed": None,
            "hit": None,
            "sample_size": 0,
            "failure_codes": ["ACTUAL_DATA_INCOMPLETE"],
            "failure_reasons": [f"{horizon.upper()}数据尚未就位"],
            "primary_failure_reason": f"{horizon.upper()}数据尚未就位",
            "success_factors": [],
        }

    focus_available = "focus_hits" in block
    actual_hits = {str(x) for x in block.get("focus_hits", [])}
    pool_hit = (bool(expected_pool & actual_hits) if focus_available else None) if expected_pool else True
    direction = _direction_value(block.get("market_direction"))
    if direction is None and "market_up" in block:
        direction = _direction_value(block.get("market_up"))
    height_present = "max_height" in block
    max_height = _int(block.get("max_height"))
    height_ok = max_height > 0 if height_present else None
    required_present = any(key in block for key in ("market_up", "market_direction", "focus_hits", "max_height"))
    evaluated = bool(block.get("evaluated", required_present))
    if not evaluated:
        return {
            "evaluated": False,
            "reason": f"{horizon.upper()}数据尚未就位",
            "market_direction": direction,
            "focus_pool_hit": None,
            "space_height_observed": None,
            "hit": None,
            "sample_size": _int(block.get("sample_size")),
            "failure_codes": ["ACTUAL_DATA_INCOMPLETE"],
            "failure_reasons": [f"{horizon.upper()}数据尚未就位"],
            "primary_failure_reason": f"{horizon.upper()}数据尚未就位",
            "success_factors": [],
        }

    market_ok = None if direction is None else direction == "up"
    factors = {"focus_pool": pool_hit, "market": market_ok, "height_observed": height_ok}
    failure_codes = []
    failure_reasons = []
    success_factors = []
    if market_ok is None:
        failure_codes.append("ACTUAL_DATA_INCOMPLETE")
        failure_reasons.append("市场方向数据缺失，无法验证市场判断")
    elif market_ok:
        success_factors.append("市场方向符合预期")
    else:
        failure_codes.append("MARKET_DIRECTION_MISMATCH")
        failure_reasons.append("市场方向与预测不一致")
    if pool_hit is None:
        failure_codes.append("ACTUAL_DATA_INCOMPLETE")
        failure_reasons.append("聚焦池实际命中数据缺失")
    elif pool_hit:
        success_factors.append("聚焦池命中")
    else:
        failure_codes.append("FOCUS_POOL_MISS")
        failure_reasons.append("聚焦池没有命中")
    if height_ok is None:
        failure_codes.append("ACTUAL_DATA_INCOMPLETE")
        failure_reasons.append("空间高度数据缺失，无法确认高度表现")
    elif height_ok:
        success_factors.append("空间高度达到确认条件")
    else:
        failure_codes.append("HEIGHT_NOT_CONFIRMED")
        failure_reasons.append("空间高度未达到确认要求")
    hit = bool(all(value is True for value in factors.values()))
    return {
        "evaluated": True,
        "reason": "；".join(failure_reasons),
        "market_direction": direction or "unknown",
        "focus_pool_hit": pool_hit,
        "space_height_observed": height_ok,
        "hit": hit,
        "factors": factors,
        "failure_codes": failure_codes,
        "failure_reasons": failure_reasons,
        "primary_failure_reason": failure_reasons[0] if failure_reasons else "",
        "success_factors": success_factors,
        "sample_size": _int(block.get("sample_size"), 1),
        "actual": block,
    }


def evaluate_prediction(prediction: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    """分别评估 T+1/T+3，避免只有 T+1 时误报整条预测已完成。"""
    actual = actual if isinstance(actual, dict) else {}
    t1 = _evaluate_horizon(prediction, actual, "t1")
    t3 = _evaluate_horizon(prediction, actual, "t3")
    evaluated_any = bool(t1["evaluated"] or t3["evaluated"])
    evaluated_all = bool(t1["evaluated"] and t3["evaluated"])
    selected = t3 if t3["evaluated"] else t1
    sample_size = max(_int(t1.get("sample_size")), _int(t3.get("sample_size")))
    confidence = "高" if sample_size >= 30 else ("中" if sample_size >= 10 else ("低" if evaluated_any else "低"))
    return {
        "report_date": prediction.get("report_date", ""),
        "evaluated": evaluated_any,
        "completed": evaluated_all,
        "completion_status": "T+1/T+3均已完成" if evaluated_all else ("仅T+1已完成" if t1["evaluated"] else "数据未就位"),
        "hit": selected.get("hit"),
        "market_direction": {"t1": t1.get("market_direction"), "t3": t3.get("market_direction")},
        "focus_pool_hit": selected.get("focus_pool_hit"),
        "space_height_observed": selected.get("space_height_observed"),
        "t1_hit": t1.get("hit"),
        "t3_hit": t3.get("hit"),
        "sample_size": sample_size,
        "confidence": confidence,
        "t1": t1,
        "t3": t3,
        "reason": "；".join(x for x in (t1.get("reason"), t3.get("reason")) if x),
        "failure_codes_by_horizon": {
            "t1": t1.get("failure_codes", []),
            "t3": t3.get("failure_codes", []),
        },
        "failure_reasons_by_horizon": {
            "t1": t1.get("failure_reasons", []),
            "t3": t3.get("failure_reasons", []),
        },
        "primary_failure_reason_by_horizon": {
            "t1": t1.get("primary_failure_reason", ""),
            "t3": t3.get("primary_failure_reason", ""),
        },
        "success_factors_by_horizon": {
            "t1": t1.get("success_factors", []),
            "t3": t3.get("success_factors", []),
        },
        "actual": actual,
    }


def normalize_stock_code(value: Any) -> str:
    """将证券代码统一为 sh/sz/bj + 六位数字，覆盖常见来源格式。"""
    raw = str(value or "").strip().lower().replace(" ", "")
    if not raw:
        return ""
    raw = raw.replace("_", "").replace("-", "")
    market = ""
    for suffix, prefix in ((".sh", "sh"), (".sz", "sz"), (".bj", "bj"), ("sh", "sh"), ("sz", "sz"), ("bj", "bj")):
        if raw.endswith(suffix):
            market, raw = prefix, raw[:-len(suffix)]
            break
    if "." in raw:
        left, right = raw.split(".", 1)
        if right in {"sh", "sz", "bj"}:
            market, raw = right, left
    if raw.startswith(("sh", "sz", "bj")):
        market, raw = raw[:2], raw[2:]
    digits = "".join(re.findall(r"\d", raw))
    if not digits:
        return ""
    digits = digits[-6:].zfill(6)
    if not market:
        if digits.startswith(("920", "430", "830", "870", "400")):
            market = "bj"
        elif digits.startswith(("600", "601", "603", "605", "688", "689")):
            market = "sh"
        else:
            market = "sz"
    return f"{market}{digits}"


def summarize_market_universe(codes: Iterable[Any] | None, required_market_prefixes: Iterable[str] = ("sh", "sz", "bj")) -> dict[str, Any]:
    """汇总真实股票池覆盖范围，禁止用目标市场范围替代实际代码覆盖。"""
    normalized = []
    errors = []
    for raw in codes or []:
        code = normalize_stock_code(raw)
        if not code:
            if str(raw or "").strip():
                errors.append(f"无法标准化证券代码: {raw}")
            continue
        normalized.append(code)
    normalized = sorted(set(normalized))
    prefixes = sorted({code[:2] for code in normalized if code[:2] in {"sh", "sz", "bj"}})
    required = sorted({str(item).lower().strip() for item in (required_market_prefixes or ()) if str(item).strip()})
    missing = sorted(set(required) - set(prefixes))
    scope = "沪深北全A" if not missing and set(required) >= {"sh", "sz", "bj"} else (
        "、".join(prefix.upper() for prefix in prefixes) if prefixes else "未知市场范围"
    )
    missing_fields = [f"缺少市场代码前缀: {','.join(missing)}"] if missing else []
    return {
        "codes": normalized,
        "market_total": len(normalized),
        "market_prefixes": prefixes,
        "required_market_prefixes": required,
        "missing_market_prefixes": missing,
        "market_scope": scope,
        "missing_fields": missing_fields,
        "errors": sorted(set(errors)),
    }


def filter_tradeable_pool(
    rows: Iterable[dict[str, Any]] | None,
    min_turnover: float = 0.0,
    include_bj: bool = True,
    security_master: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """过滤 ST、停牌、退市、不可交易和低流动性个股，并统一代码格式。

    security_master 可用代码映射证券状态，优先覆盖旧缓存里的名称/状态，
    避免摘帽或状态变更后仍沿用历史股票池。
    """
    result = []
    for source in rows or []:
        if not isinstance(source, dict):
            continue
        row = dict(source)
        code = normalize_stock_code(row.get("code", row.get("代码", "")))
        name = str(row.get("name", row.get("名称", row.get("股票", ""))) or "")
        master = security_master.get(code, {}) if isinstance(security_master, dict) else {}
        if not isinstance(master, dict):
            master = {"status": master}
        status = str(master.get("status", row.get("status", "")) or "").strip().lower()
        if not code or (code.startswith("bj") and not include_bj):
            continue
        if master.get("name"):
            name = str(master.get("name"))
        is_st = master.get("is_st", row.get("is_st"))
        if "st" in name.lower() or "*st" in name.lower() or is_st is True:
            continue
        if status in {"suspended", "停牌", "delisted", "退市", "terminated", "终止上市", "inactive", "非存续"}:
            continue
        if master.get("tradable", row.get("tradable")) is False or master.get("可交易", row.get("可交易")) is False:
            continue
        if row.get("suspended") is True or row.get("停牌") is True:
            continue
        turnover = _number(row.get("turnover", row.get("成交额", 0)), 0.0)
        if turnover < float(min_turnover):
            continue
        row["code"] = code
        row["name"] = name
        row["market"] = code[:2]
        row["tradeable"] = True
        result.append(row)
    return result
