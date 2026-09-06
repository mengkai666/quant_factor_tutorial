"""Observational limit-pool events; never a trading/readiness gate.

Adapters retain only supplied facts, with original field/value evidence. No
state is inferred from pool membership, prices, matching times or break counts.
None (including pandas missing values) stays distinct from 0 and False.

Public consumer APIs:
* event_field_coverage(data): coverage over the supplied rows, NOT the market.
* build_limit_event_snapshot(data, trade_date, *, source=None,
  source_timestamp=None, source_timestamp_kind=None): deterministic JSON-ready
  snapshot of normalized records. trade_date is the requested/archive day;
  each record keeps its normalized upstream day and raw trade_date_evidence;
  mismatches are reported without changing the strict archive partition date.
* archive_limit_event_snapshot(snapshot, archive_dir): atomic daily upsert,
  returning Path; identical content is a no-op. Callers explicitly opt into I/O.
* load_limit_event_snapshot(archive_dir, trade_date): dict, or None if absent.

The current inputs are closing limit pools (including DT if supplied), not the
universe of all intraday limit-up attempts. Coverage is row-level, and the
snapshot intentionally never publishes a full-market break rate. Mixed ZT/DT
input has a mixed-row denominator; filter to ZT before calling for ZT coverage.
quality.trade_date_mismatches includes provenance-only wrong-day observations
(e.g. an empty stale pool); record/provenance counters expose the breakdown.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date as calendar_date, datetime
import json
import math
import os
from pathlib import Path
import re
import tempfile

import pandas as pd


LIMIT_EVENT_FIELDS = (
    "limit_up_attempted", "broken", "reclosed", "board_type",
    "first_limit_time", "last_limit_time", "limit_up_fund",
    "turnover_rate", "amount", "float_market_cap", "break_count",
)
COMMON_EVENT_FIELDS = ("turnover_rate", "amount", "float_market_cap")
EVENT_PROVENANCE_FIELDS = (
    "source", "source_timestamp", "source_timestamp_kind", "fetched_at",
    "trade_date", "trade_date_source", "trade_date_evidence",
)
_MISSING = object()


def _json_value(value):
    """Copy observations into JSON scalars without truthiness-based defaults."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, calendar_date)):
        return value.isoformat()
    # numpy scalars occur in nullable pandas columns and source evidence.
    if hasattr(value, "item"):
        scalar = value.item()
        if type(scalar) is not type(value):
            return _json_value(scalar)
        value = scalar
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _normalized_trade_date(value) -> str | None:
    """Normalize source dates at day granularity without filling unknowns.

    YYYYMMDD strings/integers, ISO dates, and ISO datetime/date scalars are
    accepted. Datetimes retain their supplied calendar day, not a shifted UTC
    day. The archive partition validator remains strict and separate.
    """
    value = _json_value(value)
    # pandas may coerce YYYYMMDD integers to floats in a column containing nulls.
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = value.strip()
    if re.fullmatch(r"[0-9]{8}", value):
        value = f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return calendar_date.fromisoformat(value).isoformat()
        if re.match(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ]", value):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    return None


def extract_limit_event_fields(row: Mapping, *, aliases: Mapping | None = None,
                               fields: Iterable[str] = LIMIT_EVENT_FIELDS) -> dict:
    """Retain supplied fields and raw-key evidence, never infer event states.

    Canonical keys take precedence even when explicitly null. Already-retained
    evidence is copied, not replaced with a synthetic canonical-key origin.
    Aliases must be supplied by a source that knows their semantics (e.g. do
    not pass down-limit 'last seal' aliases as up-limit event fields).
    """
    aliases = aliases or {}
    upstream = row.get("event_evidence")
    upstream = upstream if isinstance(upstream, Mapping) else {}
    values, evidence = {}, {}
    for field in fields:
        key = next((key for key in (field, *aliases.get(field, ())) if key in row), None)
        values[field] = _json_value(row[key]) if key is not None else None
        if key is not None and not (
                isinstance(row.get("event_evidence"), Mapping) and
                field not in upstream and values[field] is None):
            evidence[field] = _json_value(upstream[field]) if field in upstream else {
                "field": key, "value": _json_value(row[key]),
            }
    values["event_evidence"] = evidence
    return values


def _pick(row, metadata, keys, default=_MISSING):
    for mapping in (row, metadata):
        for key in keys:
            if key in mapping:
                return mapping[key], key
    return default, None


def limit_event_provenance(row: Mapping, metadata: Mapping | None = None, *,
                           trade_date=None, source=None, fetched_at=None,
                           date_aliases: Iterable[str] = ()) -> dict:
    """Row > frame metadata > explicit fallback, with nulls authoritative.

    fetched_at is a new retrieval time only when the caller supplies it; it
    never refreshes an upstream source timestamp, including an explicit null.
    If only an older upstream fetched_at exists, keep it as the evidence time.
    trade_date is an ISO day or None; trade_date_evidence keeps the original
    source field/value (including explicit nulls) across repeated normalization.
    Source-specific date_aliases, such as Eastmoney qdate, never outrank the
    explicit canonical date in the same mapping. Invalid dates stay unknown.
    """
    metadata = metadata or {}
    day_keys = ("trade_date", "date", "交易日", "日期", *date_aliases)
    time_keys = ("source_timestamp", "fetched_at")
    day, day_key = _pick(row, metadata, day_keys, trade_date)
    day_metadata = {} if any(key in row for key in day_keys) else metadata
    time_metadata = {} if any(key in row for key in time_keys) else metadata
    day_evidence, _ = _pick(row, day_metadata, ("trade_date_evidence",))
    if day_evidence is _MISSING:
        day_evidence = {"field": day_key, "value": _json_value(day)} if day_key else None
    timestamp, time_key = _pick(row, metadata, time_keys, fetched_at)
    time_kind = ("fetched_at" if time_key == "fetched_at" or
                 (time_key is None and fetched_at is not None) else
                 "source" if time_key is not None else None)
    return _json_value({
        "source": _pick(row, metadata, ("source",), source)[0],
        "source_timestamp": timestamp,
        "source_timestamp_kind": _pick(row, time_metadata, ("source_timestamp_kind",), time_kind)[0],
        "fetched_at": (fetched_at if fetched_at is not None else
                       _pick(row, metadata, ("fetched_at",), None)[0]),
        "trade_date": _normalized_trade_date(day),
        "trade_date_evidence": day_evidence,
        "trade_date_source": _pick(row, day_metadata, ("trade_date_source",),
                                  "source" if day_key is not None else "request")[0],
    })


def _records(data) -> list[Mapping]:
    if isinstance(data, pd.DataFrame):
        return data.to_dict("records")
    if isinstance(data, (str, bytes, Mapping)) or not isinstance(data, Iterable):
        raise TypeError("data must be a DataFrame or iterable of record mappings")
    rows = list(data)
    if any(not isinstance(row, Mapping) for row in rows):
        raise TypeError("every event record must be a mapping")
    return rows


def event_field_coverage(data) -> dict:
    """Pure per-field {known, missing, total, coverage} over supplied rows.

    False and zero count as known; missing/null/nonfinite scalars do not.
    Empty input has coverage=None rather than a fabricated zero observation.
    This measures availability, not semantic validity or market completeness.
    """
    rows = _records(data)
    total = len(rows)
    coverage = {}
    for field in LIMIT_EVENT_FIELDS:
        known = sum(_json_value(row.get(field)) is not None for row in rows)
        coverage[field] = {"known": known, "missing": total - known, "total": total,
                           "coverage": known / total if total else None}
    return coverage


def _trade_date(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("trade_date must be an ISO YYYY-MM-DD date")
    try:
        calendar_date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("trade_date must be a valid calendar date") from exc
    return value


def _trade_date_quality(records, provenance, pools, expected_day) -> dict:
    """Count wrong-day observations, including sources with no stock rows.

    A pool's bad source day is not counted twice if a bad record in that pool
    already reports the same day. Provenance-only errors remain visible for
    empty pools or contradictory metadata. These are observation counts, not
    stock counts; field coverage always retains its original row denominator.
    """
    bad_records = [row for row in records
                   if row["trade_date"] is not None and row["trade_date"] != expected_day]
    observations = pools.items() if pools else [(None, provenance)]
    bad_provenance = []
    for pool_type, metadata in observations:
        source_day = _normalized_trade_date(metadata.get("trade_date"))
        if source_day is not None and source_day != expected_day:
            bad_provenance.append((pool_type, source_day))
    provenance_only = sum(
        not any(row["trade_date"] == source_day and
                (pool_type is None or row.get("pool_type") == pool_type)
                for row in bad_records)
        for pool_type, source_day in bad_provenance
    )
    return {
        "trade_date_mismatches": len(bad_records) + provenance_only,
        "record_trade_date_mismatches": len(bad_records),
        "provenance_trade_date_mismatches": len(bad_provenance),
    }


def build_limit_event_snapshot(data, trade_date: str, *, source=None,
                               source_timestamp=None, source_timestamp_kind=None) -> dict:
    """Build a pure snapshot from canonical provider records or a DataFrame.

    Explicit row/frame provenance, including nulls, wins over caller fallbacks.
    No current time, I/O, price inference, gate decision or rate extrapolation
    is used. Original first/last times and numeric units remain unchanged.
    """
    day = _trade_date(trade_date)
    metadata = dict(data.attrs) if isinstance(data, pd.DataFrame) else {}
    if source_timestamp is not None:
        metadata.setdefault("source_timestamp", source_timestamp)
    if source_timestamp_kind is not None:
        metadata.setdefault("source_timestamp_kind", source_timestamp_kind)
    records = []
    for row in _records(data):
        record = {key: _json_value(row[key]) for key in
                  ("date", "pool_type", "code", "name", "limit_count") if key in row}
        record.update(extract_limit_event_fields(row))
        record.update(limit_event_provenance(row, metadata, trade_date=day, source=source))
        records.append(record)
    provenance = limit_event_provenance({}, metadata, trade_date=day, source=source)
    pools = _json_value(metadata.get("pool_provenance", {}))
    discarded = (sum(int(pool.get("discarded_rows", 0) or 0) for pool in pools.values())
                 if pools else int(metadata.get("discarded_rows", 0) or 0))
    return {
        "schema_version": 1,
        "trade_date": day,
        "population_scope": "closing_limit_pool",
        "full_market_coverage": False,
        "market_break_rate": None,
        "market_break_rate_reason": "closing pools do not cover all intraday limit-up attempts",
        "row_count": len(records),
        "records": records,
        "provenance": provenance,
        "field_coverage": event_field_coverage(records),
        "pool_provenance": pools,
        "quality": {
            **_trade_date_quality(records, provenance, pools, day),
            "missing_trade_date": sum(row["trade_date"] is None for row in records),
            "missing_source": sum(row["source"] is None for row in records),
            "missing_source_timestamp": sum(row["source_timestamp"] is None for row in records),
            "discarded_rows": discarded,
        },
    }


def _snapshot_day(snapshot) -> str:
    if not isinstance(snapshot, Mapping) or snapshot.get("schema_version") != 1:
        raise ValueError("unsupported limit event snapshot schema_version")
    return _trade_date(snapshot.get("trade_date"))


def archive_limit_event_snapshot(snapshot: Mapping, archive_dir: str | Path) -> Path:
    """Atomically upsert archive_dir/YYYY-MM-DD.json and return its path.

    Same-day revisions replace that day's snapshot, not append duplicates or
    erase other days. Byte-identical content does not touch the file. No I/O
    occurs in the provider/builders; archiving must be explicitly requested.
    A failed replacement leaves the old snapshot intact and removes our temp.
    """
    day = _snapshot_day(snapshot)
    content = (json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2,
                          allow_nan=False) + "\n").encode("utf-8")
    directory = Path(archive_dir)
    target = directory / f"{day}.json"
    if target.is_file() and target.read_bytes() == content:
        return target
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=directory, prefix=f".{day}-",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def load_limit_event_snapshot(archive_dir: str | Path, trade_date: str) -> dict | None:
    """Read one day, returning None only for absence; corruption raises."""
    day = _trade_date(trade_date)
    target = Path(archive_dir) / f"{day}.json"
    try:
        snapshot = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if _snapshot_day(snapshot) != day:
        raise ValueError("snapshot trade_date does not match archive day")
    return snapshot
