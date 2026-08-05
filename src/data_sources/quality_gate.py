from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .models import FetchResult, FetchStatus
from .price_provider import PRICE_COLUMNS
from .universe_provider import UNIVERSE_COLUMNS


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
            if result.date == report.target_date and result.status in self.CRITICAL_FETCH_STATUS:
                severity = "warning" if result.dataset == "plates" else "critical"
                report.issues.append(QualityIssue(
                    severity, "fetch_status",
                    f"{result.dataset} is {result.status.value}: {result.message}",
                ))
        return report

    def enforce(self, universe: pd.DataFrame, prices: pd.DataFrame, target_date: str,
                fetch_results: list[FetchResult] | None = None) -> QualityReport:
        report = self.validate(universe, prices, target_date, fetch_results)
        if not report.ok:
            raise DataQualityError(report)
        return report
