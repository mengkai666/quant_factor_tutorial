from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any
from pathlib import Path

import numpy as np
import pandas as pd

from .models import FetchResult, FetchStatus, ModuleQuality
from .price_provider import PRICE_COLUMNS
from .universe_provider import UNIVERSE_COLUMNS

CRITICAL_MODULES = {"universe", "price_raw", "breadth", "limit_pool"}
DECISION_MODULES = CRITICAL_MODULES | {"echelon", "sector", "history"}


def build_module_quality(
    name: str,
    total: int = 0,
    covered: int = 0,
    source: str = "",
    source_timestamp: str = "",
    missing_fields: list[str] | None = None,
    errors: list[str] | None = None,
    lineage: dict[str, Any] | None = None,
    critical: bool | None = None,
) -> dict[str, Any]:
    """Return the stable module-quality payload consumed by report rendering."""
    total = max(0, int(total or 0))
    covered = max(0, int(covered or 0))
    if total and covered > total:
        raise ValueError(f"covered cannot exceed total for {name}: {covered}>{total}")
    missing = [str(item) for item in (missing_fields or []) if str(item).strip()]
    error_list = [str(item) for item in (errors or []) if str(item).strip()]
    coverage_pct = round(covered / total * 100, 2) if total else 0.0
    explicit_critical = bool(critical) if critical is not None else name in CRITICAL_MODULES
    if error_list and explicit_critical:
        status = "blocked"
    elif error_list or missing or (total > 0 and covered < total):
        status = "degraded"
    elif total == 0 and not source:
        status = "unknown"
    elif total == 0 and not covered:
        status = "unavailable"
    else:
        status = "ok"
    return {
        "name": name, "status": status, "total": total, "covered": covered,
        "coverage_pct": coverage_pct, "source": source,
        "source_timestamp": source_timestamp, "missing_fields": missing,
        "errors": error_list, "lineage": dict(lineage or {}),
        "critical": explicit_critical,
    }


def aggregate_report_quality(modules: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """Aggregate module states into a publication-safe report state."""
    modules = dict(modules or {})
    critical_blocked = [
        name for name, item in modules.items()
        if name in CRITICAL_MODULES and str(item.get("status", "unknown")) in {"blocked", "unavailable", "unknown"}
    ]
    decision_degraded = [
        name for name, item in modules.items()
        if name in DECISION_MODULES and str(item.get("status", "unknown")) != "ok"
    ]
    errors, missing_fields = [], []
    for name, item in modules.items():
        errors.extend(f"{name}: {value}" for value in item.get("errors", []) or [])
        missing_fields.extend(f"{name}: {value}" for value in item.get("missing_fields", []) or [])
    if critical_blocked:
        status, publication_mode = "blocked", "facts_only"
    elif decision_degraded:
        status, publication_mode = "degraded", "observation"
    else:
        status, publication_mode = "ok", "decision"
    return {
        "status": status, "publication_mode": publication_mode, "modules": modules,
        "critical_blocked": critical_blocked, "decision_degraded": decision_degraded,
        "errors": list(dict.fromkeys(errors)), "missing_fields": list(dict.fromkeys(missing_fields)),
        "allow_strong_conclusion": status == "ok",
    }



@dataclass(frozen=True)
class QualityIssue:
    severity: str
    code: str
    message: str
    count: int = 0


@dataclass
class QualityReport:
    target_date: str
    issues: list[QualityIssue]

    @property
    def critical(self) -> list[QualityIssue]:
        return [issue for issue in self.issues if issue.severity == "critical"]

    @property
    def warnings(self) -> list[QualityIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.critical

    def to_dict(self):
        return {
            "target_date": self.target_date,
            "ok": self.ok,
            "critical": [asdict(issue) for issue in self.critical],
            "warnings": [asdict(issue) for issue in self.warnings],
        }

    def write_json(self, path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


class DataQualityError(RuntimeError):
    def __init__(self, report: QualityReport):
        self.report = report
        message = "; ".join(f"{issue.code}: {issue.message}" for issue in report.critical)
        super().__init__(message or "market data quality gate failed")


class MarketDataQualityGate:
    VALID_TRADE_STATUS = {"traded", "suspended", "not_listed", "missing"}
    CRITICAL_FETCH_STATUS = {
        FetchStatus.PARTIAL, FetchStatus.FAILED, FetchStatus.STALE,
        FetchStatus.NOT_AVAILABLE,
    }

    def __init__(self, min_coverage: float = 0.90, max_qfq_abs_return: float = 0.25,
                 systematic_switch_ratio: float = 0.80):
        self.min_coverage = min_coverage
        self.max_qfq_abs_return = max_qfq_abs_return
        self.systematic_switch_ratio = systematic_switch_ratio

    def validate(self, universe: pd.DataFrame, prices: pd.DataFrame, target_date: str,
                 fetch_results: list[FetchResult] | None = None) -> QualityReport:
        issues: list[QualityIssue] = []
        universe_missing = set(UNIVERSE_COLUMNS) - set(universe.columns)
        if universe_missing:
            issues.append(QualityIssue("critical", "universe_schema",
                                      f"missing columns {sorted(universe_missing)}"))
        else:
            exchanges = set(universe["exchange"].dropna().astype(str))
            missing_exchanges = {"SH", "SZ", "BJ"} - exchanges
            if missing_exchanges:
                issues.append(QualityIssue("critical", "universe_exchange",
                                          f"missing exchanges {sorted(missing_exchanges)}"))
            duplicates = int(universe.duplicated("code").sum())
            if duplicates:
                issues.append(QualityIssue("critical", "universe_duplicate",
                                          f"duplicate codes {duplicates}", duplicates))

        price_missing = set(PRICE_COLUMNS) - set(prices.columns)
        if price_missing:
            issues.append(QualityIssue("critical", "price_schema",
                                      f"missing columns {sorted(price_missing)}"))
            return self._append_fetch_issues(QualityReport(target_date, issues), fetch_results)

        frame = prices.copy()
        frame["date"] = frame["date"].astype(str)
        parsed_dates = pd.to_datetime(frame["date"], format="%Y-%m-%d", errors="coerce")
        invalid_dates = int(parsed_dates.isna().sum())
        if invalid_dates:
            issues.append(QualityIssue(
                "critical", "price_date",
                f"invalid price dates {invalid_dates}", invalid_dates,
            ))
        duplicate_count = int(frame.duplicated(["date", "code"]).sum())
        if duplicate_count:
            issues.append(QualityIssue("critical", "price_duplicate",
                                      f"duplicate (date, code) rows {duplicate_count}", duplicate_count))
        status_text = frame["trade_status"].fillna("").astype(str).str.strip()
        bad_status = sorted(set(status_text) - self.VALID_TRADE_STATUS)
        if bad_status:
            issues.append(QualityIssue("critical", "trade_status",
                                      f"invalid trade_status {bad_status}"))
        if not universe_missing:
            universe_codes = set(universe["code"].dropna().astype(str))
            price_codes = frame["code"].fillna("").astype(str).str.strip()
            unknown_codes = sorted(set(price_codes) - universe_codes)
            if unknown_codes:
                issues.append(QualityIssue(
                    "critical", "price_code",
                    f"price codes absent from universe {unknown_codes[:10]}",
                    len(unknown_codes),
                ))
            valid_list_status = {"listed", "suspended_listing", "delisted", "unknown"}
            bad_list_status = sorted(
                set(universe["list_status"].dropna().astype(str)) - valid_list_status
            )
            if bad_list_status:
                issues.append(QualityIssue(
                    "critical", "listing_status",
                    f"invalid universe list_status {bad_list_status}", len(bad_list_status),
                ))
            metadata = (universe.drop_duplicates("code")
                        .set_index("code")[["list_date", "delist_date"]])
            status_dates = frame[["code", "date", "trade_status"]].copy()
            status_dates["list_date"] = status_dates["code"].map(metadata["list_date"])
            status_dates["delist_date"] = status_dates["code"].map(metadata["delist_date"])
            status_dates["row_date"] = parsed_dates
            status_dates["list_date"] = pd.to_datetime(status_dates["list_date"], errors="coerce")
            status_dates["delist_date"] = pd.to_datetime(status_dates["delist_date"], errors="coerce")
            active_status = status_dates["trade_status"].isin(["traded", "suspended"])
            before_listing = (
                active_status & status_dates["list_date"].notna()
                & (status_dates["row_date"] < status_dates["list_date"])
            )
            after_listing_marked_unlisted = (
                status_dates["trade_status"].eq("not_listed")
                & status_dates["list_date"].notna()
                & (status_dates["row_date"] >= status_dates["list_date"])
            )
            after_delisting = (
                active_status & status_dates["delist_date"].notna()
                & (status_dates["row_date"] > status_dates["delist_date"])
            )
            listing_errors = int(
                (before_listing | after_listing_marked_unlisted | after_delisting).sum()
            )
            if listing_errors:
                issues.append(QualityIssue(
                    "critical", "listing_status",
                    f"trade_status inconsistent with list/delist dates {listing_errors}",
                    listing_errors,
                ))
        traded = frame["trade_status"] == "traded"
        raw = pd.to_numeric(frame["close_raw"], errors="coerce")
        qfq = pd.to_numeric(frame["close_qfq"], errors="coerce")
        bad_price = int((traded & ((raw <= 0) | (qfq <= 0) | raw.isna() | qfq.isna())).sum())
        if bad_price:
            issues.append(QualityIssue("critical", "non_positive_price",
                                      f"non-positive or missing traded prices {bad_price}", bad_price))
        source_missing = int((traded & ((frame["source_raw"].fillna("") == "") |
                                        (frame["source_qfq"].fillna("") == ""))).sum())
        if source_missing:
            issues.append(QualityIssue("critical", "price_source",
                                      f"traded rows missing source {source_missing}", source_missing))

        if not universe_missing:
            listed = universe[universe["list_status"].isin(["listed", "suspended_listing", "unknown"])]
            expected = len(listed)
            actual = frame[(frame["date"] == target_date) &
                           (frame["trade_status"].isin(["traded", "suspended"]))]["code"].nunique()
            ratio = actual / expected if expected else 0.0
            if ratio < self.min_coverage:
                issues.append(QualityIssue("critical", "price_coverage",
                                          f"coverage {actual}/{expected} ({ratio:.1%}) below {self.min_coverage:.0%}",
                                          expected - actual))

        ordered = frame.sort_values(["code", "date"]).copy()
        ordered["raw"] = pd.to_numeric(ordered["close_raw"], errors="coerce")
        ordered["qfq"] = pd.to_numeric(ordered["close_qfq"], errors="coerce")
        ordered["raw_ret"] = ordered.groupby("code")["raw"].pct_change(fill_method=None)
        ordered["qfq_ret"] = ordered.groupby("code")["qfq"].pct_change(fill_method=None)
        bj = ordered["code"].astype(str).str.startswith("bj")
        limits = np.where(bj, max(0.30, self.max_qfq_abs_return), self.max_qfq_abs_return)
        if "list_date" in universe.columns:
            list_dates = (universe.drop_duplicates("code")
                          .set_index("code")["list_date"])
            ordered["list_date"] = ordered["code"].map(list_dates)
        else:
            ordered["list_date"] = pd.NaT
        ordered["list_date"] = pd.to_datetime(ordered["list_date"], errors="coerce")
        ordered["row_date"] = pd.to_datetime(ordered["date"], errors="coerce")
        ordered["trade_rank"] = (
            ordered["trade_status"].eq("traded")
            .groupby(ordered["code"])
            .cumsum()
        )
        early_listing = (
            ordered["trade_status"].eq("traded")
            & ordered["trade_rank"].between(1, 5)
            & ordered["list_date"].notna()
            & (ordered["row_date"] >= ordered["list_date"])
            & ((ordered["row_date"] - ordered["list_date"]).dt.days <= 10)
        )
        # Beijing's 30% limit can exceed 30% after raw/qfq prices are rounded.
        bj_rounding_ok = (
            bj
            & (ordered["raw_ret"].abs() <= 0.3005)
            & (ordered["qfq_ret"].abs() <= 0.305)
        )
        abnormal = ordered[
            ordered["trade_status"].eq("traded")
            & ~early_listing
            & ~bj_rounding_ok
            & (ordered["qfq_ret"].abs() > (limits + 1e-8))
        ]
        if not abnormal.empty:
            issues.append(QualityIssue("critical", "qfq_jump",
                                      f"qfq jumps above {self.max_qfq_abs_return:.0%}: {len(abnormal)}",
                                      len(abnormal)))

        for date, day in ordered.dropna(subset=["raw_ret", "qfq_ret"]).groupby("date"):
            if len(day) < 2:
                continue
            divergent = ((day["raw_ret"].abs() > self.max_qfq_abs_return) &
                         (day["qfq_ret"].abs() <= self.max_qfq_abs_return))
            ratio = float(divergent.mean())
            if ratio >= self.systematic_switch_ratio:
                issues.append(QualityIssue("critical", "adjustment_switch",
                                          f"systematic adjustment switch on {date}: {ratio:.1%}",
                                          int(divergent.sum())))

        return self._append_fetch_issues(QualityReport(target_date, issues), fetch_results)

    def _append_fetch_issues(self, report: QualityReport,
                             fetch_results: list[FetchResult] | None) -> QualityReport:
        for result in fetch_results or []:
            is_target = result.date == report.target_date
            status_needs_attention = result.status in self.CRITICAL_FETCH_STATUS
            # A successful HTTP response with no plate rows is still not useful
            # for attribution, so surface it explicitly instead of treating it
            # as an invisible ZERO result.
            if result.dataset == "plates" and result.status == FetchStatus.ZERO:
                status_needs_attention = True
            if status_needs_attention:
                severity = "warning" if result.dataset == "plates" or not is_target else "critical"
                code = "fetch_status" if is_target else "historical_fetch_status"
                report.issues.append(QualityIssue(
                    severity, code,
                    f"{result.dataset} on {result.date} is {result.status.value}: {result.message}",
                ))

            if result.dataset == "limit_pool" and isinstance(result.data, pd.DataFrame):
                data = result.data
                if not data.empty and {"pool_type", "code"}.issubset(data.columns):
                    duplicate_count = int(data.duplicated(["pool_type", "code"]).sum())
                    if duplicate_count:
                        severity = "critical" if is_target else "warning"
                        report.issues.append(QualityIssue(
                            severity, "limit_pool_duplicate",
                            f"limit_pool on {result.date} has duplicate pool/code rows {duplicate_count}",
                            duplicate_count,
                        ))
                if "limit_count" in data.columns and not data.empty:
                    counts = pd.to_numeric(data["limit_count"], errors="coerce")
                    invalid_count = int((counts.isna() | (counts < 1)).sum())
                    if invalid_count:
                        severity = "critical" if is_target else "warning"
                        report.issues.append(QualityIssue(
                            severity, "limit_pool_count",
                            f"limit_pool on {result.date} has invalid limit_count rows {invalid_count}",
                            invalid_count,
                        ))

            if result.dataset == "plates" and isinstance(result.data, pd.DataFrame):
                data = result.data
                if not data.empty and "plate_name" in data.columns:
                    missing_names = int(data["plate_name"].fillna("").astype(str).str.strip().eq("").sum())
                    if missing_names:
                        report.issues.append(QualityIssue(
                            "warning", "plate_attribution",
                            f"plates on {result.date} has missing plate names {missing_names}",
                            missing_names,
                        ))
        return report

    def enforce(self, universe: pd.DataFrame, prices: pd.DataFrame, target_date: str,
                fetch_results: list[FetchResult] | None = None) -> QualityReport:
        report = self.validate(universe, prices, target_date, fetch_results)
        if not report.ok:
            raise DataQualityError(report)
        return report

CRITICAL_MODULES = {"universe", "price_raw", "breadth", "limit_pool"}
DECISION_MODULES = CRITICAL_MODULES | {"echelon", "sector", "history"}


def _dedupe(values: Iterable[Any] | None) -> list[str]:
    result: list[str] = []
    for value in values or ():
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def build_module_quality(
    name: str,
    *,
    total: Any = 0,
    covered: Any = 0,
    source: str = "",
    source_timestamp: str = "",
    missing_fields: Iterable[Any] | None = None,
    errors: Iterable[Any] | None = None,
    lineage: dict[str, Any] | None = None,
    critical: bool | None = None,
    ok_threshold: float = 0.98,
    usable_threshold: float = 0.80,
) -> dict[str, Any]:
    try:
        total_i = max(0, int(float(total or 0)))
        covered_i = max(0, int(float(covered or 0)))
    except (TypeError, ValueError):
        total_i, covered_i = 0, 0
    effective_covered = min(covered_i, total_i)
    coverage = effective_covered / total_i if total_i else (1.0 if effective_covered else 0.0)
    raw_coverage = covered_i / total_i if total_i else (1.0 if covered_i else 0.0)
    missing = _dedupe(missing_fields)
    error_list = _dedupe(errors)
    overflow = covered_i > total_i
    if overflow:
        error_list.append(
            f"COVERAGE_OVERFLOW: 有效覆盖数 {covered_i} 超过市场总数 {total_i}"
        )
    error_list = _dedupe(error_list)
    is_critical = name in CRITICAL_MODULES if critical is None else bool(critical)
    if overflow:
        status = "blocked" if is_critical else "unavailable"
    elif error_list and (not covered_i or coverage < usable_threshold):
        status = "blocked" if is_critical else "unavailable"
    elif total_i and coverage < usable_threshold:
        status = "blocked" if is_critical else "unavailable"
    elif error_list or missing or (total_i and coverage < ok_threshold):
        status = "degraded"
    elif covered_i or not total_i:
        status = "ok"
    else:
        status = "unknown"
    item = ModuleQuality(
        name=name, status=status, total=total_i, covered=effective_covered,
        coverage_pct=round(coverage * 100, 2), source=str(source or ""),
        source_timestamp=str(source_timestamp or ""), missing_fields=missing,
        errors=error_list, lineage=dict(lineage or {}), critical=is_critical,
        raw_covered=covered_i, raw_coverage_pct=round(raw_coverage * 100, 2),
    )
    return item.to_dict()


_UNAVAILABLE_STATUSES = {"blocked", "unavailable", "unknown"}


def _module_status(modules: dict[str, dict[str, Any]], name: str) -> str:
    item = modules.get(name)
    if not item:
        return "unknown"
    return str(item.get("status") or "unknown").strip().lower()


def _scope(
    mode: str,
    dependencies: Iterable[str],
    modules: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    deps = list(dependencies)
    return {
        "mode": mode,
        "dependencies": deps,
        "module_statuses": {
            name: _module_status(modules, name)
            for name in deps
        },
    }


def build_publication_scopes(
    modules: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    normalized = {
        str(key): dict(value or {})
        for key, value in (modules or {}).items()
    }

    fact_deps = ("universe", "price_raw", "breadth", "limit_pool")
    fact_statuses = [_module_status(normalized, name) for name in fact_deps]
    if any(status in _UNAVAILABLE_STATUSES for status in fact_statuses):
        market_mode = "unavailable"
    elif any(status != "ok" for status in fact_statuses):
        market_mode = "limited"
    else:
        market_mode = "full"

    lianban_deps = ("limit_pool", "echelon", "history")
    lianban_required = [_module_status(normalized, name) for name in ("limit_pool", "echelon")]
    if any(status in _UNAVAILABLE_STATUSES for status in lianban_required):
        lianban_mode = "unavailable"
    elif any(_module_status(normalized, name) != "ok" for name in lianban_deps):
        lianban_mode = "limited"
    else:
        lianban_mode = "full"

    limit_pool_status = _module_status(normalized, "limit_pool")
    sector_status = _module_status(normalized, "sector")
    if limit_pool_status in _UNAVAILABLE_STATUSES:
        mainline_mode = "unavailable"
    elif limit_pool_status != "ok" or sector_status != "ok":
        mainline_mode = "limited"
    else:
        mainline_mode = "full"

    def hard_dependency_mode(name: str) -> str:
        status = _module_status(normalized, name)
        if status in _UNAVAILABLE_STATUSES:
            return "unavailable"
        return "full" if status == "ok" else "limited"

    def ai_scope_mode() -> str:
        status = _module_status(normalized, "ai")
        if status in _UNAVAILABLE_STATUSES:
            return "unavailable"
        if status != "ok":
            return "limited"
        lineage = dict((normalized.get("ai") or {}).get("lineage") or {})
        publication_mode = str(lineage.get("publication_mode", "") or "").lower()
        input_quality = str(lineage.get("input_quality_status", "") or "").lower()
        if publication_mode in {"facts_only", "observation"}:
            return "limited"
        if input_quality in {"blocked", "degraded"}:
            return "limited"
        return "full"

    return {
        "market_facts": _scope(market_mode, fact_deps, normalized),
        "lianban_review": _scope(lianban_mode, lianban_deps, normalized),
        "mainline_review": _scope(mainline_mode, ("limit_pool", "sector"), normalized),
        "return_analysis": _scope(hard_dependency_mode("price_qfq"), ("price_qfq",), normalized),
        "ai_review": _scope(ai_scope_mode(), ("ai",), normalized),
    }


def aggregate_report_quality(modules: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    normalized = {str(k): dict(v or {}) for k, v in (modules or {}).items()}
    critical_blocked = [name for name, item in normalized.items() if (item.get("critical") or name in CRITICAL_MODULES) and item.get("status") in {"blocked", "unavailable", "unknown"}]
    decision_degraded = [name for name, item in normalized.items() if name in DECISION_MODULES and item.get("status") != "ok"]
    errors = _dedupe(msg for item in normalized.values() for msg in item.get("errors", []))
    missing = _dedupe(msg for item in normalized.values() for msg in item.get("missing_fields", []))
    if critical_blocked:
        status, mode = "blocked", "facts_only"
    elif decision_degraded:
        status, mode = "degraded", "observation"
    else:
        status, mode = "ok", "decision"
    return {
        "status": status,
        "publication_mode": mode,
        "modules": normalized,
        "publication_scopes": build_publication_scopes(normalized),
        "critical_blocked": critical_blocked,
        "decision_degraded": decision_degraded,
        "errors": errors,
        "missing_fields": missing,
        "allow_strong_conclusion": mode == "decision",
    }
