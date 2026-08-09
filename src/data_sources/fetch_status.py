from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import os
import tempfile

import pandas as pd

from .models import FetchResult, FetchStatus
from .run_context import current_run_id


STATUS_COLUMNS = [
    "date", "dataset", "scope", "status", "source", "expected_count",
    "actual_count", "message", "started_at", "finished_at", "run_id",
]


class FetchStatusStore:
    def __init__(self, path, run_id: str | None = None):
        self.path = Path(path)
        self.run_id = current_run_id() if run_id is None else str(run_id or "")

    def read(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=STATUS_COLUMNS)
        df = pd.read_csv(self.path, dtype=str).fillna("")
        for col in STATUS_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        return df[STATUS_COLUMNS]

    def record(self, result: FetchResult) -> None:
        if not result.run_id and self.run_id:
            result = replace(result, run_id=self.run_id)
        row = asdict(result)
        row.pop("data", None)
        row["status"] = result.status.value
        row["started_at"] = result.started_at.isoformat()
        row["finished_at"] = result.finished_at.isoformat()
        new = pd.DataFrame([row], columns=STATUS_COLUMNS)
        current = self.read()
        if not current.empty:
            key = ((current["date"] == result.date)
                   & (current["dataset"] == result.dataset)
                   & (current["scope"] == result.scope))
            current = current.loc[~key]
        combined = pd.concat([current, new], ignore_index=True)
        combined = combined.sort_values(["date", "dataset", "scope"]).reset_index(drop=True)
        self._atomic_write(combined)

    def latest(self, date: str, dataset: str, scope: str = "all") -> FetchResult | None:
        df = self.read()
        rows = df[(df["date"] == date) & (df["dataset"] == dataset) & (df["scope"] == scope)]
        if rows.empty:
            return None
        row = rows.iloc[-1]
        return FetchResult(
            dataset=row["dataset"], date=row["date"], source=row["source"],
            status=FetchStatus(row["status"]), expected_count=int(row["expected_count"] or 0),
            actual_count=int(row["actual_count"] or 0), scope=row["scope"],
            message=row["message"], started_at=pd.Timestamp(row["started_at"]).to_pydatetime(),
            finished_at=pd.Timestamp(row["finished_at"]).to_pydatetime(), run_id=row["run_id"],
        )

    def results(self, datasets=None) -> list[FetchResult]:
        """Return recorded statuses as FetchResult objects for quality reporting."""
        df = self.read()
        if datasets is not None:
            df = df[df["dataset"].isin(set(datasets))]
        results = []
        for _, row in df.iterrows():
            try:
                status = FetchStatus(row["status"])
            except ValueError:
                continue
            started_at = self._parse_datetime(row["started_at"])
            finished_at = self._parse_datetime(row["finished_at"]) or started_at
            results.append(FetchResult(
                dataset=row["dataset"], date=row["date"], source=row["source"],
                status=status, expected_count=self._parse_int(row["expected_count"]),
                actual_count=self._parse_int(row["actual_count"]), scope=row["scope"],
                message=row["message"],
                started_at=started_at, finished_at=finished_at,
                run_id=row["run_id"],
            ))
        return results

    @staticmethod
    def _parse_int(value) -> int:
        try:
            return int(float(value)) if str(value).strip() else 0
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _parse_datetime(value) -> datetime:
        try:
            parsed = pd.to_datetime(value, errors="coerce")
            if pd.isna(parsed):
                return datetime.now(timezone.utc)
            result = parsed.to_pydatetime()
            if result.tzinfo is None:
                return result.replace(tzinfo=timezone.utc)
            return result
        except (TypeError, ValueError, OverflowError):
            return datetime.now(timezone.utc)

    def _atomic_write(self, df: pd.DataFrame) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        os.close(fd)
        try:
            df.to_csv(temp_name, index=False)
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
