# -*- coding: utf-8 -*-
"""盘中阶段快照契约。

日报的收盘事实继续保持按日不可变；竞价、9:35、10:00 和午后事实不应
覆盖收盘快照，也不能用收盘值冒充盘中值。本模块提供独立的、可追加的
阶段快照事件格式，供盘面状态机和明日推演后验更新共同消费。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Iterable


SNAPSHOT_SCHEMA = "market-phase-snapshot/v1"
EVENT_TYPE = "market_phase_snapshot"
PHASE_ORDER = ("close", "auction", "early_0935", "confirm_1000", "afternoon")
PHASE_ALIASES = {
    "收盘": "close",
    "close": "close",
    "竞价": "auction",
    "auction": "auction",
    "开盘竞价": "auction",
    "9:35": "early_0935",
    "09:35": "early_0935",
    "early_0935": "early_0935",
    "早盘": "early_0935",
    "10:00": "confirm_1000",
    "10:00前": "confirm_1000",
    "confirm_1000": "confirm_1000",
    "确认": "confirm_1000",
    "午后": "afternoon",
    "afternoon": "afternoon",
}

# 这些是共享语义层最值得优先统一的字段。契约不会为缺失字段补 0。
CORE_METRICS = (
    "index_return",
    "up_count",
    "down_count",
    "flat_count",
    "breadth_ratio",
    "limit_up",
    "limit_down",
    "炸板率",
    "reopen_rate",
    "promotion_rate",
    "high_level_feedback",
    "mainline_diffusion",
    "middle_tier_support",
    "rear_rank_dropout",
    "index_sector_stock_resonance",
)


def normalize_phase(value: Any) -> str:
    text = str(value or "").strip().lower()
    phase = PHASE_ALIASES.get(text)
    if not phase:
        raise ValueError(f"未知盘中阶段: {value!r}；允许值: {', '.join(PHASE_ORDER)}")
    return phase


def _valid_report_date(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError("report_date 必须是 YYYY-MM-DD，不能用当前时间猜测业务日期")
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"report_date 不是有效日期: {text}") from exc
    return text


_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_PHASE_WINDOWS = {
    "close": (time(15, 0), time(23, 59, 59)),
    "auction": (time(9, 25), time(9, 29, 59)),
    "early_0935": (time(9, 35), time(9, 44, 59)),
    "confirm_1000": (time(10, 0), time(10, 15)),
    "afternoon": (time(13, 0), time(14, 59, 59)),
}


def _normalize_captured_at(value: Any, phase: str) -> tuple[str | None, dict[str, Any]]:
    """Validate an observed timestamp and return canonical ISO + quality facts."""
    text = str(value or "").strip()
    if not text:
        return None, {"timestamp_status": "missing", "timezone": None, "phase_window_valid": None}
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("captured_at 必须是带时区的 ISO-8601 时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("captured_at 必须包含明确时区")
    now = datetime.now(timezone.utc)
    if parsed.astimezone(timezone.utc) > now + timedelta(minutes=5):
        raise ValueError("captured_at 不能是未来时间")
    local = parsed.astimezone(_SHANGHAI_TZ)
    start, end = _PHASE_WINDOWS[phase]
    local_clock = local.timetz().replace(tzinfo=None)
    if not (start <= local_clock <= end):
        raise ValueError(f"captured_at 不在 {phase} 阶段允许时间窗内")
    return parsed.isoformat(), {
        "timestamp_status": "valid",
        "timezone": str(parsed.tzinfo),
        "normalized_timezone": "Asia/Shanghai",
        "phase_window_valid": True,
        "data_cutoff": local.isoformat(),
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _stable_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]
    return f"{payload['report_date']}:{payload['phase']}:{digest}"


@dataclass(frozen=True)
class PhaseSnapshot:
    report_date: str
    trade_date: str
    phase: str
    snapshot_id: str
    run_id: str | None
    captured_at: str | None
    metrics: dict[str, Any]
    source_lineage: dict[str, Any]
    quality: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": EVENT_TYPE,
            "snapshot_schema": SNAPSHOT_SCHEMA,
            **asdict(self),
        }


def build_phase_snapshot(
    *,
    report_date: str,
    phase: str,
    trade_date: str | None = None,
    metrics: dict[str, Any] | None = None,
    run_id: str | None = None,
    captured_at: str | None = None,
    source_lineage: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
    snapshot_id: str | None = None,
) -> PhaseSnapshot:
    """构造一个阶段快照；缺失事实保持缺失，不使用收盘值回填。"""
    report_date = _valid_report_date(report_date)
    normalized_phase = normalize_phase(phase)
    normalized_metrics = _json_safe(dict(metrics or {}))
    lineage = _json_safe(dict(source_lineage or {}))
    normalized_captured_at, timestamp_quality = _normalize_captured_at(captured_at, normalized_phase)
    if trade_date is not None:
        normalized_trade_date = _valid_report_date(trade_date)
    elif normalized_captured_at:
        normalized_trade_date = datetime.fromisoformat(normalized_captured_at).astimezone(_SHANGHAI_TZ).date().isoformat()
    else:
        normalized_trade_date = report_date
    if normalized_captured_at:
        observed_date = datetime.fromisoformat(normalized_captured_at).astimezone(_SHANGHAI_TZ).date().isoformat()
        if normalized_trade_date != observed_date:
            raise ValueError("trade_date 必须与 captured_at 的上海交易日一致")
    if normalized_phase == "close" and normalized_trade_date != report_date:
        raise ValueError("close 的 trade_date 必须等于 report_date，不能用次日收盘冒充基准")
    quality_payload = _json_safe(dict(quality or {}))
    quality_payload.setdefault("status", "unknown")
    quality_payload.setdefault("missing_fields", [])
    # Validation facts are computed, never accepted from caller assertions.
    quality_payload.update(timestamp_quality)
    identity = {
        "report_date": report_date,
        "trade_date": normalized_trade_date,
        "phase": normalized_phase,
        "run_id": str(run_id or "") or None,
        "captured_at": normalized_captured_at,
        "metrics": normalized_metrics,
        "source_lineage": lineage,
        "quality": quality_payload,
    }
    return PhaseSnapshot(
        report_date=report_date,
        trade_date=normalized_trade_date,
        phase=normalized_phase,
        snapshot_id=str(snapshot_id or _stable_id(identity)),
        run_id=str(run_id or "") or None,
        captured_at=normalized_captured_at,
        metrics=normalized_metrics,
        source_lineage=lineage,
        quality=quality_payload,
    )


def _coerce_record(value: PhaseSnapshot | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, PhaseSnapshot):
        value = value.to_dict()
    if not isinstance(value, dict):
        raise TypeError("阶段快照必须是 PhaseSnapshot 或 dict")
    # 兼容现有按日不可变收盘快照：只把它映射为 close，绝不把它
    # 映射为 auction/09:35/10:00/afternoon。
    if value.get("snapshot_schema") == "daily-fact-snapshot/v1" and not value.get("phase"):
        report_date = _valid_report_date(value.get("report_date"))
        metrics = {
            key: value[key]
            for key in CORE_METRICS
            if key in value
        }
        return build_phase_snapshot(
            report_date=report_date,
            trade_date=report_date,
            phase="close",
            metrics=metrics,
            run_id=value.get("run_id"),
            captured_at=value.get("captured_at") or value.get("saved_at"),
            source_lineage={
                "source": value.get("snapshot_source") or value.get("source") or "daily_snapshot",
                "source_snapshot_schema": value.get("snapshot_schema"),
                "immutable": value.get("immutable") is True,
            },
            quality={
                "status": "ok" if value.get("immutable") is True else "unknown",
                "missing_fields": [],
            },
            snapshot_id=value.get("snapshot_id"),
        ).to_dict()
    record = dict(value)
    if record.get("snapshot_schema", SNAPSHOT_SCHEMA) != SNAPSHOT_SCHEMA:
        raise ValueError("阶段快照 schema 不匹配")
    # An existing ID is not a validation bypass, including for loaded history.
    rebuilt = build_phase_snapshot(
        report_date=record.get("report_date"), trade_date=record.get("trade_date"),
        phase=record.get("phase"), metrics=record.get("metrics"), run_id=record.get("run_id"),
        captured_at=record.get("captured_at"), source_lineage=record.get("source_lineage"),
        quality=record.get("quality"), snapshot_id=record.get("snapshot_id"),
    )
    return {**record, **rebuilt.to_dict()}


def append_phase_snapshot_once(
    path: str | Path, snapshot: PhaseSnapshot | dict[str, Any],
) -> dict[str, Any]:
    """按 snapshot_id 幂等追加阶段快照，不覆盖旧事件。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = _coerce_record(snapshot)
    snapshot_id = str(record["snapshot_id"])
    if target.exists():
        for raw in target.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                existing = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(existing, dict) and str(existing.get("snapshot_id")) == snapshot_id:
                return {**existing, "appended": False}
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        handle.flush()
    return {**record, "appended": True}


def load_phase_snapshots(path: str | Path, *, report_date: str | None = None) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    expected_date = _valid_report_date(report_date) if report_date else None
    rows: list[dict[str, Any]] = []
    for raw in target.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict) or value.get("snapshot_schema") != SNAPSHOT_SCHEMA:
            continue
        try:
            record = _coerce_record(value)
        except (TypeError, ValueError):
            continue
        if expected_date and record["report_date"] != expected_date:
            continue
        rows.append(record)
    return rows


def latest_phase_snapshots(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """只返回实际存在的各阶段最新记录，不合成缺失阶段。"""
    latest: dict[str, dict[str, Any]] = {}
    for raw in rows:
        try:
            record = _coerce_record(raw)
        except (TypeError, ValueError):
            continue
        phase = record["phase"]
        previous = latest.get(phase)
        if previous is None:
            latest[phase] = record
            continue
        def _sort_key(item: dict[str, Any]) -> float:
            captured = item.get("captured_at")
            try:
                parsed = datetime.fromisoformat(str(captured).replace("Z", "+00:00")) if captured else None
                epoch = parsed.astimezone(timezone.utc).timestamp() if parsed and parsed.tzinfo else float("-inf")
            except (TypeError, ValueError):
                epoch = float("-inf")
            # Equal/missing times retain append revision order, not hash order.
            return epoch
        old_key = _sort_key(previous)
        new_key = _sort_key(record)
        if new_key >= old_key:
            latest[phase] = record
    return {phase: latest[phase] for phase in PHASE_ORDER if phase in latest}


def snapshot_qualification_issues(
    record: dict[str, Any], *, report_date: str, trade_date: str | None,
) -> list[str]:
    """Audit an observation against a specific forecast; never infer its target."""
    issues: list[str] = []
    phase = record["phase"]
    if record["report_date"] != report_date:
        issues.append("report_date_mismatch")
    expected = report_date if phase == "close" else trade_date
    if expected is None:
        issues.append("target_trade_date_missing")
    elif record["trade_date"] != expected:
        issues.append("target_trade_date_mismatch")
    if phase != "close" and record["trade_date"] <= report_date:
        issues.append("target_trade_date_must_follow_report")
    quality = record.get("quality") or {}
    lineage = record.get("source_lineage") or {}
    if quality.get("status") != "ok":
        issues.append("quality_not_ok")
    if quality.get("used_stale") or lineage.get("used_stale") or quality.get("freshness_level") in {"stale", "expired"}:
        issues.append("stale_observation")
    if str(lineage.get("source") or "").strip().lower() in {"", "unknown", "phase_monitor"}:
        issues.append("source_missing")
    # An immutable close may have an explicit source cutoff but no acquisition
    # timestamp; that is a baseline only. Intraday requires observed time.
    timestamp = record.get("captured_at") or (lineage.get("data_cutoff") if phase == "close" else None)
    try:
        canonical, _ = _normalize_captured_at(timestamp, phase)
        if not canonical:
            issues.append("observation_timestamp_missing")
        elif datetime.fromisoformat(canonical).astimezone(_SHANGHAI_TZ).date().isoformat() != record["trade_date"]:
            issues.append("observation_date_mismatch")
        cutoff = lineage.get("data_cutoff")
        if cutoff:
            source_time, _ = _normalize_captured_at(cutoff, phase)
            observed = datetime.fromisoformat(canonical) if canonical else None
            source_at = datetime.fromisoformat(source_time) if source_time else None
            if source_at and (source_at.astimezone(_SHANGHAI_TZ).date().isoformat() != record["trade_date"] or (observed and source_at > observed)):
                issues.append("source_timestamp_mismatch")
    except (TypeError, ValueError):
        issues.append("observation_timestamp_invalid")
    return list(dict.fromkeys(issues))


def select_bound_phase_snapshots(
    rows: Iterable[dict[str, Any]], *, report_date: str, trade_date: str | None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Keep report-day close plus qualified observations of the intended T+1."""
    report_date = _valid_report_date(report_date)
    if trade_date is not None:
        trade_date = _valid_report_date(trade_date)
        if trade_date <= report_date:
            raise ValueError("目标交易日必须晚于报告日")
    candidates, rejected = [], []
    for raw in rows:
        try:
            record = _coerce_record(raw)
        except (TypeError, ValueError) as exc:
            record = raw if isinstance(raw, dict) else {}
            rejected.append({"snapshot_id": record.get("snapshot_id"), "phase": record.get("phase"),
                             "report_date": record.get("report_date"), "trade_date": record.get("trade_date"),
                             "issues": ["invalid_snapshot:" + str(exc)], "selected_latest": True})
            continue
        expected = report_date if record["phase"] == "close" else trade_date
        if record["report_date"] != report_date or expected is None or record["trade_date"] != expected:
            rejected.append({"snapshot_id": record.get("snapshot_id"), "phase": record["phase"],
                             "report_date": record["report_date"], "trade_date": record["trade_date"],
                             "issues": snapshot_qualification_issues(record, report_date=report_date, trade_date=trade_date),
                             "selected_latest": False})
        else:
            candidates.append(record)
    # Select by observation time BEFORE qualification. New bad data must not
    # make us silently reuse an earlier green observation of the same phase.
    latest = latest_phase_snapshots(candidates)
    accepted = {}
    for record in candidates:
        issues = snapshot_qualification_issues(record, report_date=report_date, trade_date=trade_date)
        chosen = latest[record["phase"]]
        selected = record["snapshot_id"] == chosen["snapshot_id"] and record.get("captured_at") == chosen.get("captured_at")
        if issues:
            rejected.append({"snapshot_id": record["snapshot_id"], "phase": record["phase"],
                             "report_date": record["report_date"], "trade_date": record["trade_date"],
                             "issues": issues, "selected_latest": selected})
        elif selected:
            accepted[record["phase"]] = record
    return {phase: accepted[phase] for phase in PHASE_ORDER if phase in accepted}, rejected


__all__ = [
    "CORE_METRICS", "EVENT_TYPE", "PHASE_ORDER", "SNAPSHOT_SCHEMA", "PhaseSnapshot",
    "append_phase_snapshot_once", "build_phase_snapshot", "latest_phase_snapshots",
    "load_phase_snapshots", "normalize_phase", "select_bound_phase_snapshots", "snapshot_qualification_issues",
]
