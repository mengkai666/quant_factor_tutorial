# -*- coding: utf-8 -*-
"""价格口径标准化：事实行情用 raw，收益/回测用 qfq。"""
from __future__ import annotations

import math
from typing import Any, Iterable

import pandas as pd

from report_logic import normalize_stock_code


def _float(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def normalize_price_rows(rows: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in rows or ():
        row = dict(source)
        code = normalize_stock_code(row.get("code", row.get("代码", "")))
        date = str(row.get("date", row.get("日期", "")) or "").strip()
        basis = str(row.get("price_basis", row.get("adjustment", "")) or "").strip().lower()
        raw = _float(row.get("close_raw"))
        qfq = _float(row.get("close_qfq"))
        legacy_close = _float(row.get("close", row.get("收盘", row.get("收盘价"))))
        legacy_value = _float(row.get("close_legacy"))
        if basis in {"raw", "none", "unadjusted", "不复权"}:
            raw = raw if raw is not None else legacy_close
            qfq = None if "close_qfq" not in row else qfq
            basis = "raw"
        elif basis in {"qfq", "forward", "前复权"}:
            qfq = qfq if qfq is not None else legacy_close
            raw = None if "close_raw" not in row else raw
            basis = "qfq"
        elif basis in {"legacy", "legacy_mixed", "mixed"}:
            legacy_value = legacy_value if legacy_value is not None else legacy_close
            basis = "legacy_mixed"
        elif raw is not None and qfq is not None:
            basis = "raw+qfq"
        elif raw is not None and qfq is None:
            basis = "raw"
        elif qfq is not None and raw is None:
            basis = "qfq"
        elif legacy_close is not None:
            legacy_value = legacy_close
            basis = "legacy_mixed"
        else:
            basis = basis or "unknown"
        result.append({
            "code": code, "date": date, "close_raw": raw, "close_qfq": qfq,
            "close_legacy": legacy_value,
            "price_basis": basis, "source": str(row.get("source", "") or ""),
            "source_timestamp": str(row.get("source_timestamp", row.get("updated_at", "")) or ""),
        })
    return result


def normalize_price_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    """把旧的 ``date,code,close`` 缓存转换为可审计的双口径结构。

    旧单列 close 无法从文件本身判断是不复权还是前复权，因此只进入
    ``close_legacy``，并标记为 ``legacy_mixed``。这保证历史数值仍可用于
    兼容展示，但不会被质量门禁误报成 raw/qfq 覆盖。
    """
    columns = [
        "date", "code", "close_raw", "close_qfq", "close_legacy",
        "price_basis", "source", "source_timestamp",
    ]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    rows = normalize_price_rows(frame.to_dict("records"))
    result = pd.DataFrame(rows, columns=columns)
    if result.empty:
        return result
    result["date"] = result["date"].astype(str).str.strip()
    result["code"] = result["code"].astype(str).str.strip()
    for col in ("close_raw", "close_qfq", "close_legacy"):
        result[col] = pd.to_numeric(result[col], errors="coerce")
    result = result[(result["date"] != "") & (result["code"] != "")]
    return result.reset_index(drop=True)


def _first_number(values: pd.Series) -> float | None:
    for value in values:
        number = _float(value)
        if number is not None:
            return number
    return None


def _first_text(values: pd.Series) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _derive_basis(raw: float | None, qfq: float | None, legacy: float | None) -> str:
    if raw is not None and qfq is not None:
        return "raw+qfq"
    if raw is not None:
        return "raw"
    if qfq is not None:
        return "qfq"
    if legacy is not None:
        return "legacy_mixed"
    return "unknown"


def merge_price_frames(*frames: pd.DataFrame | None) -> pd.DataFrame:
    """按 code/date 合并价格来源，允许同一行同时补齐 raw 与 qfq。"""
    normalized = [normalize_price_frame(frame) for frame in frames if frame is not None]
    normalized = [frame for frame in normalized if not frame.empty]
    if not normalized:
        return normalize_price_frame(None)
    combined = pd.concat(normalized, ignore_index=True)
    rows: list[dict[str, Any]] = []
    for (code, date), group in combined.groupby(["code", "date"], sort=False):
        raw = _first_number(group["close_raw"])
        qfq = _first_number(group["close_qfq"])
        legacy = _first_number(group["close_legacy"])
        rows.append({
            "code": code,
            "date": date,
            "close_raw": raw,
            "close_qfq": qfq,
            "close_legacy": legacy,
            "price_basis": _derive_basis(raw, qfq, legacy),
            "source": _first_text(group["source"]),
            "source_timestamp": _first_text(group["source_timestamp"]),
        })
    result = pd.DataFrame(rows)
    return result.sort_values(["code", "date"]).reset_index(drop=True)


def price_value_column(frame: pd.DataFrame, basis: str = "qfq", *, allow_legacy: bool = True) -> str | None:
    """返回指定口径可用于计算的列名，兼容旧测试/调用方的 close。"""
    basis = str(basis or "qfq").lower()
    preferred = "close_qfq" if basis == "qfq" else "close_raw"
    if preferred in frame.columns and frame[preferred].notna().any():
        return preferred
    if allow_legacy and "close_legacy" in frame.columns and frame["close_legacy"].notna().any():
        return "close_legacy"
    if allow_legacy and "close" in frame.columns and frame["close"].notna().any():
        return "close"
    return None


def validate_price_contract(rows: Iterable[dict[str, Any]] | None) -> dict[str, Any]:
    errors: list[str] = []
    counts = {"raw": 0, "qfq": 0}
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(rows or ()):
        code, date = str(row.get("code", "")), str(row.get("date", ""))
        basis = str(row.get("price_basis", "") or "").lower()
        raw, qfq = _float(row.get("close_raw")), _float(row.get("close_qfq"))
        if not code or not date:
            errors.append(f"第{index + 1}行缺少 code/date")
        if basis not in {"raw", "qfq", "raw+qfq", "legacy_mixed"}:
            errors.append(f"{code}/{date} 价格口径不明确: {basis or 'missing'}")
        if basis in {"raw", "raw+qfq"} and raw is None:
            errors.append(f"{code}/{date} 缺少 close_raw")
        if basis in {"qfq", "raw+qfq"} and qfq is None:
            errors.append(f"{code}/{date} 缺少 close_qfq")
        if basis == "legacy_mixed" and _float(row.get("close_legacy")) is None:
            errors.append(f"{code}/{date} 缺少 close_legacy")
        key = (code, date, basis)
        if key in seen:
            errors.append(f"重复价格记录: {code}/{date}/{basis}")
        seen.add(key)
        if basis in counts:
            counts[basis] += 1
    return {"valid": not errors, "errors": list(dict.fromkeys(errors)), "counts": counts, "total": sum(counts.values())}


def price_coverage(rows: Iterable[dict[str, Any]] | None, universe_codes: Iterable[str], basis: str = "raw") -> dict[str, Any]:
    universe = {normalize_stock_code(code) for code in universe_codes if normalize_stock_code(code)}
    covered = {
        normalize_stock_code(row.get("code"))
        for row in (rows or ())
        if _float(row.get(f"close_{basis}")) is not None
    }
    covered &= universe
    total = len(universe)
    return {"basis": basis, "market_total": total, "market_covered": len(covered), "coverage_pct": round((len(covered) / total * 100) if total else 0.0, 2), "missing_codes": sorted(universe - covered)}
