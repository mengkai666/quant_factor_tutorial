"""Build and enforce machine-readable integrity metadata for published reports."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


class ReportIntegrityError(RuntimeError):
    """Raised when a rendered report is unsafe to publish."""


_CODE_RE = re.compile(r"^(?:sh|sz|bj)?\d{6}$", re.IGNORECASE)
_CHINESE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_METADATA_RE = re.compile(
    r"""<script\b[^>]*\bid=["']report-integrity["'][^>]*>(.*?)</script>""",
    re.IGNORECASE | re.DOTALL,
)
_DEGRADED_STATUSES = {"degraded", "fallback", "partial", "stale"}


def _date(value: Any) -> str:
    text = str(value or "").strip()
    digits = re.sub(r"\D", "", text)
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return text


def _row_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, pd.DataFrame):
        return len(value.index)
    if isinstance(value, dict):
        return sum(_row_count(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return len(value)
    return 0


def _representative_rows(phase_result: Any) -> list[dict[str, Any]]:
    if not isinstance(phase_result, dict):
        return []
    representatives = phase_result.get("representatives")
    if not isinstance(representatives, dict):
        return []
    groups = representatives.get("groups", representatives)
    if not isinstance(groups, dict):
        return []
    rows: list[dict[str, Any]] = []
    for value in groups.values():
        if isinstance(value, pd.DataFrame):
            rows.extend(value.to_dict("records"))
        elif isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return rows


def _module_disclosure(name: str, item: dict[str, Any]) -> str:
    lineage = item.get("lineage") if isinstance(item.get("lineage"), dict) else {}
    source = str(item.get("source") or lineage.get("source") or "").strip()
    fallback = str(item.get("fallback_source") or lineage.get("fallback_source") or "").strip()
    timestamp = str(item.get("source_timestamp") or lineage.get("source_timestamp") or "").strip()
    details = [part for part in (source, fallback, timestamp) if part]
    return f"{name}: {' / '.join(details)}" if details else ""


_SECTION_DEGRADATIONS = (
    ("phase_quadrants", "quadrant_rows", "四象限板块结果为空, 报告不展示该模块"),
    ("phase_representatives", "representative_rows", "四象限个股代表为空, 报告不展示该模块"),
)


def _section_degradations(quadrant_rows: Any, representative_rows: Any) -> list[tuple[str, str]]:
    """可选的阶段共振/四象限模块缺段清单(模块名, 披露文案)。"""
    counts = {"quadrant_rows": quadrant_rows, "representative_rows": representative_rows}
    items: list[tuple[str, str]] = []
    for module, key, reason in _SECTION_DEGRADATIONS:
        try:
            value = int(counts.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            items.append((module, reason))
    return items


def _quality_metrics(quality: Any) -> tuple[float, list[str], list[str], list[str]]:
    quality = quality if isinstance(quality, dict) else {}
    modules = quality.get("modules") if isinstance(quality.get("modules"), dict) else {}
    price = modules.get("price_raw") or modules.get("price") or {}
    try:
        price_coverage = float(price.get("coverage_pct", quality.get("raw_coverage_pct", 0.0)) or 0.0)
    except (TypeError, ValueError):
        price_coverage = 0.0

    critical_blocked = [str(item) for item in (quality.get("critical_blocked") or []) if str(item)]
    degraded_modules: list[str] = []
    disclosures: list[str] = []
    for name, raw in modules.items():
        item = raw if isinstance(raw, dict) else {}
        status = str(item.get("status") or "").strip().lower()
        lineage = item.get("lineage") if isinstance(item.get("lineage"), dict) else {}
        degraded = (
            status in _DEGRADED_STATUSES
            or bool(item.get("used_fallback") or item.get("used_stale"))
            or bool(lineage.get("used_fallback") or lineage.get("used_stale"))
        )
        if degraded:
            degraded_modules.append(str(name))
            disclosure = _module_disclosure(str(name), item)
            if disclosure:
                disclosures.append(disclosure)

    for item in quality.get("quality_disclosures") or quality.get("disclosures") or []:
        text = str(item).strip()
        if text:
            disclosures.append(text)
    return price_coverage, critical_blocked, sorted(set(degraded_modules)), list(dict.fromkeys(disclosures))


def build_report_integrity(*, report_date: Any, market_date: Any,
                           phase_result: Any, quality: Any) -> dict[str, Any]:
    """Create the report-integrity/v1 payload from structured report inputs."""
    phase_result = phase_result if isinstance(phase_result, dict) else {}
    quadrant_rows = _row_count(phase_result.get("quadrants"))
    rows = _representative_rows(phase_result)
    fallback_count = 0
    chinese_count = 0
    for row in rows:
        code = str(row.get("code") or row.get("代码") or "").strip()
        name = str(row.get("name") or row.get("名称") or "").strip()
        if not name or name.casefold() == code.casefold() or _CODE_RE.fullmatch(name):
            fallback_count += 1
        if _CHINESE_RE.search(name):
            chinese_count += 1
    coverage = round(chinese_count * 100.0 / len(rows), 2) if rows else 0.0
    price_coverage, critical_blocked, degraded_modules, disclosures = _quality_metrics(quality)
    # 阶段共振/四象限是可选模块: 数据不足时报告本就整段不渲染。缺段按“已披露降级”处理,
    # 不再阻断发布 —— 否则一个可选模块哑掉会连带整站与邮件断更(2026-08 实测近半数交易日会中招);
    # 但必须在元数据里显式声明, 由下游 undisclosed 检查兜底, 不允许静默省略。
    for module, reason in _section_degradations(quadrant_rows, len(rows)):
        degraded_modules.append(module)
        disclosures.append(f"{module}: {reason}")
    degraded_modules = sorted(set(degraded_modules))
    disclosures = list(dict.fromkeys(disclosures))
    return {
        "schema": "report-integrity/v1",
        "report_date": _date(report_date),
        "market_date": _date(market_date),
        "metrics": {
            "quadrant_rows": quadrant_rows,
            "representative_rows": len(rows),
            "code_fallback_count": fallback_count,
            "chinese_name_coverage": coverage,
            "price_coverage_pct": round(price_coverage, 2),
            "critical_blocked": critical_blocked,
            "degraded_modules": degraded_modules,
            "quality_disclosures": disclosures,
        },
    }


def validate_report_integrity(payload: Any, *, minimum_chinese_name_coverage: float = 90.0,
                              minimum_price_coverage: float = 90.0) -> dict[str, Any]:
    """Validate a payload and return it with ``ok=True`` or raise with all failures."""
    if isinstance(payload, dict) and payload.get("schema") == "report-integrity/v2":
        return _validate_research_report_integrity(payload)
    if not isinstance(payload, dict) or payload.get("schema") != "report-integrity/v1":
        raise ReportIntegrityError("缺少或无法识别报告完整性元数据")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        raise ReportIntegrityError("报告完整性元数据缺少 metrics")

    errors: list[str] = []
    if _date(payload.get("report_date")) != _date(payload.get("market_date")):
        errors.append(
            f"报告日期 {payload.get('report_date')} 与行情日期 {payload.get('market_date')} 不一致"
        )
    representative_rows = int(metrics.get("representative_rows") or 0)
    fallback_count = int(metrics.get("code_fallback_count") or 0)
    if fallback_count:
        errors.append(f"发现 {fallback_count} 条证券代码代替中文名称")
    chinese_coverage = float(metrics.get("chinese_name_coverage") or 0.0)
    if representative_rows > 0 and chinese_coverage < float(minimum_chinese_name_coverage):
        errors.append(
            f"中文名称覆盖率 {chinese_coverage:.2f}% 低于 {float(minimum_chinese_name_coverage):.2f}%"
        )
    price_coverage = float(metrics.get("price_coverage_pct") or 0.0)
    if price_coverage < float(minimum_price_coverage):
        errors.append(
            f"价格覆盖率 {price_coverage:.2f}% 低于 {float(minimum_price_coverage):.2f}%"
        )
    blocked = [str(item) for item in (metrics.get("critical_blocked") or []) if str(item)]
    if blocked:
        errors.append("核心数据门禁阻断: " + ", ".join(blocked))
    degraded = [str(item).strip() for item in (metrics.get("degraded_modules") or []) if str(item).strip()]
    disclosures = [
        str(item).strip()
        for item in (metrics.get("quality_disclosures") or [])
        if str(item).strip()
    ]
    undisclosed = [
        module
        for module in degraded
        if not any(
            disclosure.casefold().startswith(f"{module.casefold()}:")
            for disclosure in disclosures
        )
    ]
    if undisclosed:
        errors.append("降级来源未披露: " + ", ".join(undisclosed))
    for module, reason in _section_degradations(metrics.get("quadrant_rows"), representative_rows):
        if module not in degraded:
            errors.append(f"{reason}, 但未在完整性元数据中披露")

    if errors:
        raise ReportIntegrityError("报告完整性校验失败: " + "；".join(errors))
    checked = dict(payload)
    checked["metrics"] = dict(metrics)
    checked["ok"] = True
    return checked


def render_report_integrity_metadata(payload: dict[str, Any]) -> str:
    """Render JSON metadata safe for embedding in an HTML ``script`` element."""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'<script type="application/json" id="report-integrity">{text}</script>'


def parse_report_integrity(source: str) -> dict[str, Any]:
    """Read metadata from HTML without creating a temporary report file."""
    match = _METADATA_RE.search(source)
    if not match:
        raise ReportIntegrityError("最终 HTML 缺少 report-integrity 元数据")
    try:
        payload = json.loads(html.unescape(match.group(1)).strip())
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReportIntegrityError(f"report-integrity 元数据无法解析: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReportIntegrityError("report-integrity 元数据必须是 JSON 对象")
    return payload


def validate_rendered_report(path: Any, **kwargs: Any) -> dict[str, Any]:
    """Validate the exact HTML artifact that is about to be published."""
    return validate_report_integrity(extract_report_integrity(path), **kwargs)


def build_research_report_integrity(brief: dict, *, quality=None) -> dict:
    """Describe the actual research tables, without claiming legacy quadrants exist."""
    import math
    if not isinstance(brief, dict) or brief.get("schema_version") != "research-brief/v1":
        raise ReportIntegrityError("研究简报契约无法识别")
    sectors = brief.get("sectors")
    recent = brief.get("recent")
    if not isinstance(sectors, list) or not isinstance(recent, dict) or not isinstance(recent.get("stocks"), list):
        raise ReportIntegrityError("研究简报缺少板块或多板股结构")
    watch = []
    for sector in sectors:
        if not isinstance(sector, dict) or not isinstance(sector.get("stocks"), list):
            raise ReportIntegrityError("板块个股结构无效")
        watch.extend(sector["stocks"])
    if not isinstance(recent.get("more_stocks", []), list):
        raise ReportIntegrityError("近期多板补充记录必须为列表")
    recent_rows = recent["stocks"] + recent.get("more_stocks", [])
    all_rows = watch + recent_rows
    bad_price = bad_identity = bad_date = 0
    day = brief.get("report_date")
    for row in all_rows:
        if not isinstance(row, dict):
            raise ReportIntegrityError("研究股票必须为结构化记录")
        code, name = str(row.get("code") or ""), str(row.get("name") or "").strip()
        bad_identity += int(not re.fullmatch(r"(?:sh|sz|bj)\d{6}", code) or not name or _CODE_RE.fullmatch(name) is not None)
        bad_date += int(row.get("source_date") != day)
        try:
            price = float(row.get("close"))
            valid = not isinstance(row.get("close"), bool) and math.isfinite(price) and price > 0
        except (ValueError, TypeError, OverflowError):
            valid = False
        bad_price += int(not valid)
    duplicate_count = sum(len(rows) - len({r.get("code") for r in rows}) for rows in (watch, recent_rows))
    blocked = quality.get("critical_blocked") or [] if isinstance(quality, dict) else []
    if not isinstance(blocked, list) or any(not isinstance(x, str) for x in blocked):
        raise ReportIntegrityError("核心数据状态格式无效")
    return {"schema":"report-integrity/v2", "layout":"multi-sector-research",
            "purpose":brief.get("purpose"), "report_date":day, "market_date":day,
            "metrics":{"sector_count":len(sectors), "watchlist_rows":len(watch), "recent_rows":len(recent_rows),
                "invalid_price_rows":bad_price, "invalid_identity_rows":bad_identity,
                "wrong_date_rows":bad_date, "duplicate_rows":duplicate_count,
                "authoritative_count":(brief.get("provenance") or {}).get("authoritative_count"),
                "market_limit_up":(brief.get("market") or {}).get("limit_up"), "critical_blocked":list(blocked)}}


def _validate_research_report_integrity(payload: dict) -> dict:
    from datetime import date
    if payload.get("layout") != "multi-sector-research" or payload.get("purpose") != "research_observation":
        raise ReportIntegrityError("研究报告用途或布局无效")
    try:
        day = date.fromisoformat(str(payload.get("report_date"))).isoformat()
        market_day = date.fromisoformat(str(payload.get("market_date"))).isoformat()
    except (TypeError, ValueError) as exc:
        raise ReportIntegrityError("研究报告日期无效") from exc
    if day != market_day:
        raise ReportIntegrityError("研究报告与行情日期不一致")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        raise ReportIntegrityError("研究报告缺少完整性指标")
    required = ("sector_count", "watchlist_rows", "recent_rows", "invalid_price_rows", "invalid_identity_rows",
                "wrong_date_rows", "duplicate_rows", "authoritative_count", "market_limit_up")
    if any(isinstance(metrics.get(k), bool) or not isinstance(metrics.get(k), int) or metrics[k] < 0 for k in required):
        raise ReportIntegrityError("研究报告计数必须为非负整数")
    if metrics["authoritative_count"] != metrics["market_limit_up"]:
        raise ReportIntegrityError("研究报告权威成员数与涨停数不一致")
    failed = [k for k in ("invalid_price_rows", "invalid_identity_rows", "wrong_date_rows", "duplicate_rows") if metrics[k]]
    blocked = metrics.get("critical_blocked")
    if not isinstance(blocked, list) or any(not isinstance(x, str) for x in blocked):
        raise ReportIntegrityError("研究报告核心数据状态无效")
    if failed or blocked:
        raise ReportIntegrityError("研究报告真实性校验失败: " + ", ".join(failed + blocked))
    return {**payload, "metrics":dict(metrics), "ok":True}

def extract_report_integrity(path: Any) -> dict[str, Any]:
    """Read integrity metadata from a rendered HTML report."""
    return parse_report_integrity(Path(path).read_text(encoding="utf-8"))
