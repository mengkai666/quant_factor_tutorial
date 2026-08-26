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
    if int(metrics.get("quadrant_rows") or 0) <= 0:
        errors.append("四象限板块结果为空")
    if int(metrics.get("representative_rows") or 0) <= 0:
        errors.append("四象限个股代表为空")
    fallback_count = int(metrics.get("code_fallback_count") or 0)
    if fallback_count:
        errors.append(f"发现 {fallback_count} 条证券代码代替中文名称")
    chinese_coverage = float(metrics.get("chinese_name_coverage") or 0.0)
    if chinese_coverage < float(minimum_chinese_name_coverage):
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


def extract_report_integrity(path: Any) -> dict[str, Any]:
    """Read integrity metadata from a rendered HTML report."""
    source = Path(path).read_text(encoding="utf-8")
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
