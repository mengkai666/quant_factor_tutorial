# -*- coding: utf-8 -*-
"""Bounded Tencent current-quote inputs, not historical phase reconstruction.

The wire protocol and 800-symbol batches follow legacy_tracker's qt.gtimg.cn
client, without importing that runner. Field 30 is the provider's Shanghai
clock; fields 47/48 are its actual price bounds (including ST/ChiNext/BJ).
No percentage-based limit guessing, adjusted prices, cached quote fallback,
scheduling, or strategy/execution permission is provided here.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from market_snapshot import _PHASE_WINDOWS

SOURCE = "tencent_qt"
ENDPOINT = "https://qt.gtimg.cn/q="
SCHEMA = "live-market-inputs/v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_SYMBOLS = 7000
MAX_REQUESTS = 10
BATCH_SIZE = 800
MAX_COLLECTION_SECONDS = 120
MAX_RESPONSE_CHARS = 2_000_000
_STOCK = re.compile(r"(?:sh(?:60|68)\d{4}|sz(?:00|30)\d{4}|bj(?:[48]\d{5}|92\d{4}))\Z")
_WIRE = re.compile(r'v_((?:sh|sz|bj)\d{6})="([^"]*)"\Z')
_UNSUPPORTED = {
    "index_return": "index_series_not_requested_in_stock_universe",
    "mainline_diffusion": "full_mainline_membership_and_live_denominator_unavailable",
    "炸板率": "one_quote_cannot_observe_full_market_limit_attempts_and_reopens",
    "reopen_rate": "one_quote_cannot_observe_full_market_limit_attempts_and_reopens",
    "high_level_feedback": "no_bound_high_level_feedback_measurement_definition",
    "middle_tier_support": "no_bound_middle_tier_support_measurement_definition",
    "rear_rank_dropout": "no_bound_rear_rank_dropout_measurement_definition",
    "index_sector_stock_resonance": "live_index_and_full_sector_series_not_collected",
}


def _date(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError("An explicit valid business date is required")
    return datetime.strptime(text, "%Y-%m-%d").date().isoformat()


def _aware(value: datetime | str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Clock/publication timestamps must have an explicit timezone")
    return parsed.astimezone(SHANGHAI)


def _clock(now: Callable[[], datetime] | None) -> datetime:
    return _aware(now() if now is not None else datetime.now(timezone.utc))


def phase_at(moment: datetime) -> str | None:
    """Return only an actual intraday window from market_snapshot's contract."""
    local = _aware(moment).time()
    return next((phase for phase, (start, end) in _PHASE_WINDOWS.items()
                 if phase != "close" and start <= local <= end), None)


def _number(value: Any, *, positive: bool = False) -> float | None:
    try:
        number = Decimal(str(value))
        if not number.is_finite() or (positive and number <= 0):
            return None
        result = float(number)
        return result if math.isfinite(result) else None
    except (ValueError, InvalidOperation, OverflowError):
        return None


def _codes(values: Iterable[str]) -> list[str]:
    result = []
    for value in values:
        code = str(value).strip().lower()
        if not _STOCK.fullmatch(code):
            raise ValueError(f"Not a canonical SH/SZ/BJ A-share stock code: {value!r}")
        if code not in result:
            result.append(code)
    if not result or len(result) > MAX_SYMBOLS:
        raise ValueError(f"Request must contain 1..{MAX_SYMBOLS} A-share symbols")
    return result


def load_cached_universe(cache_path: str | Path, *, trade_date: str) -> dict[str, Any]:
    """Read existing stock_universe.csv without refreshing or healing any cache.

    This is a *cached* universe, not proof that today's exchange listing roster
    is exhaustive. Its digest, cache update times, and excluded rows travel with
    the artifact; indices, funds, other markets and date-ineligible rows do not.
    """
    target = _date(trade_date)
    path = Path(cache_path).resolve()
    content = path.read_bytes()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if "code" not in (reader.fieldnames or []):
        raise ValueError("Cached universe requires a code column")
    codes, excluded, seen, updated = [], [], set(), set()
    for row in reader:
        code = str(row.get("code") or "").strip().lower()
        reason = None
        if not _STOCK.fullmatch(code):
            reason = "not_a_share_stock"
        else:
            try:
                listed = _date(row["list_date"]) if row.get("list_date") else None
                delisted = _date(row["delist_date"]) if row.get("delist_date") else None
                if listed and listed > target:
                    reason = "not_yet_listed"
                elif delisted and delisted <= target:
                    reason = "already_delisted"
                elif not delisted and str(row.get("list_status") or "").lower() in {"delisted", "d", "退市"}:
                    reason = "delisted_without_valid_date"
            except ValueError:
                reason = "invalid_listing_date"
        if code in seen:
            raise ValueError(f"Duplicate cached-universe code: {code}")
        seen.add(code)
        if reason:
            excluded.append({"code": code, "reason": reason})
        else:
            codes.append(code)
        if row.get("updated_at"):
            updated.add(row["updated_at"])
    return {"codes": _codes(codes), "trade_date": target, "scope": "cached_hs_bj_a_share_universe",
            "cache_path": str(path), "cache_sha256": hashlib.sha256(content).hexdigest(),
            "cache_updated_at": sorted(updated), "excluded": excluded}


def parse_tencent_quotes(text: str, *, requested_codes: Iterable[str]) -> dict[str, Any]:
    """Parse actual provider fields; retain unknowns and reject ambiguous symbols.

    at_*_limit means *last price equals the provider's boundary*, not a claim
    about uninterrupted sealing, a full-market event pool, or a trade fill.
    Volume is left in provider units (STAR and other boards need not agree).
    """
    expected = set(requested_codes)
    quotes, rejected, seen = {}, [], set()
    for line in text.split(";"):
        line = line.strip()
        if not line:
            continue
        match = _WIRE.fullmatch(line)
        if not match:
            rejected.append({"code": None, "reason": "malformed_quote_record"})
            continue
        code, payload = match.groups()
        if code not in expected:
            rejected.append({"code": code, "reason": "unrequested_symbol"})
            continue
        if code in seen:
            quotes.pop(code, None)
            rejected.append({"code": code, "reason": "duplicate_symbol"})
            continue
        seen.add(code)
        parts = payload.split("~")
        if len(parts) <= 30:
            rejected.append({"code": code, "reason": "truncated_quote_record"})
            continue
        if parts[2] != code[2:]:
            rejected.append({"code": code, "reason": "code_mismatch"})
            continue
        if parts[0] != {"sh": "1", "sz": "51", "bj": "62"}[code[:2]]:
            rejected.append({"code": code, "reason": "exchange_mismatch"})
            continue
        raw = {str(i): value for i, value in enumerate(parts)}
        stamp, source_time, issues = parts[30], None, []
        if re.fullmatch(r"\d{14}", stamp):
            try:
                source_time = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI)
            except ValueError:
                pass
        if source_time is None:
            issues.append("source_timestamp_missing" if not stamp else "source_timestamp_invalid")
        row = {"code": code, "name": parts[1], "raw_fields": raw, "price_basis": "raw",
               "source_as_of": source_time.isoformat() if source_time else None,
               "trade_date": source_time.date().isoformat() if source_time else None, "issues": issues}
        for key, index in {"last": 3, "previous_close": 4, "open": 5, "high": 33, "low": 34,
                           "upper_limit": 47, "lower_limit": 48}.items():
            row[key] = _number(raw.get(str(index)), positive=True)
        for key, index in {"change_percent": 32, "volume_provider_units": 6,
                           "amount_10k_cny": 37, "turnover_percent": 38}.items():
            row[key] = _number(raw.get(str(index)))
        for key in ("last", "previous_close"):
            if row[key] is None:
                issues.append("invalid_" + key)
        if row["upper_limit"] is not None and row["lower_limit"] is not None and row["upper_limit"] <= row["lower_limit"]:
            row["upper_limit"] = row["lower_limit"] = None
        for bound, flag, direction in (("upper_limit", "at_upper_limit", 1), ("lower_limit", "at_lower_limit", -1)):
            value, previous, last = row[bound], row["previous_close"], row["last"]
            # Sentinel/contradictory bounds are unknown, never a default +/-10%.
            if value is not None and ((previous is not None and (value - previous) * direction < 0)
                                      or (last is not None and (value - last) * direction < 0)):
                row[bound] = value = None
            row[flag] = (last == value) if last is not None and value is not None else None
            if value is None:
                issues.append(bound + "_missing_or_invalid")
        quotes[code] = row
    return {"quotes": quotes, "rejected": rejected}


def fetch_tencent_quotes(
    codes: Iterable[str], *, client: Any = None, now: Callable[[], datetime] | None = None,
    batch_size: int = BATCH_SIZE,
) -> dict[str, Any]:
    """One pass, no retries: <=7,000 symbols, <=10 batches, finite HTTP timeouts.

    Reuses the established thread-local HTTP helper as-is; does not change any
    proxy, credential or model settings. Inject a .get-compatible client and a
    timezone-aware clock for offline tests. Local time is NEVER a quote cutoff.
    """
    requested = _codes(codes)
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= BATCH_SIZE:
        raise ValueError("batch_size must be in 1..800")
    if math.ceil(len(requested) / batch_size) > MAX_REQUESTS:
        raise ValueError("Request exceeds the ten-batch bound")
    started = _clock(now)
    deadline = time.monotonic() + MAX_COLLECTION_SECONDS
    if client is None:
        from data_sources.http_session import get_session
        client = get_session()
    quotes, rejected, errors = {}, [], []
    for offset in range(0, len(requested), batch_size):
        chunk = requested[offset:offset + batch_size]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            errors.append({"codes": requested[offset:], "reason": "collection_budget_exhausted"})
            break
        try:
            response = client.get(ENDPOINT + ",".join(chunk), timeout=(min(3, remaining), min(10, remaining)),
                                  allow_redirects=False)
            if response.status_code != 200:
                errors.append({"codes": chunk, "reason": f"http_status:{response.status_code}"})
                continue
            response.encoding = "gbk"  # Provider protocol, not a console/file encoding change.
            text = response.text
            fetched = _clock(now).isoformat()
            if len(text) > MAX_RESPONSE_CHARS:
                errors.append({"codes": chunk, "reason": "response_size_exceeded"})
                continue
            parsed = parse_tencent_quotes(text, requested_codes=chunk)
            for row in parsed["quotes"].values():
                row["fetched_at"] = fetched
            quotes.update(parsed["quotes"])
            rejected.extend(parsed["rejected"])
        except (OSError, ValueError) as exc:
            errors.append({"codes": chunk, "reason": "request_failed:" + type(exc).__name__})
    return {"source": SOURCE, "endpoint": ENDPOINT, "requested_codes": requested, "quotes": quotes,
            "rejected": rejected, "request_errors": errors, "request_started_at": started.isoformat(),
            "fetched_at": _clock(now).isoformat()}


def _fingerprint(prediction: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(prediction, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _binding(prediction: dict[str, Any], *, report_date: str, trade_date: str,
             prediction_id: str | None, cutoff: datetime, started: datetime) -> dict[str, Any]:
    issues, publications = [], []
    if prediction.get("event_type") != "prediction" or not prediction.get("prediction_id"):
        issues.append("prediction_identity_missing")
    if prediction.get("report_date") != report_date:
        issues.append("report_date_mismatch")
    if prediction.get("target_trade_date") != trade_date:
        issues.append("target_trade_date_mismatch")
    if trade_date <= report_date:
        issues.append("target_must_follow_report_date")
    if prediction_id is not None and prediction_id != prediction.get("prediction_id"):
        issues.append("prediction_id_mismatch")
    if started.date().isoformat() != trade_date:
        issues.append("collection_day_does_not_match_target")
    context = prediction.get("decision_context")
    if not isinstance(context, dict) or not context:
        issues.append("prediction_context_missing")
    elif context.get("date_str") != report_date or context.get("next_trade_date") != trade_date:
        issues.append("prediction_context_date_mismatch")
    for key in ("generated_at", "recorded_at"):
        if prediction.get(key):
            try:
                publications.append(_aware(prediction[key]))
            except (TypeError, ValueError):
                issues.append("prediction_publication_time_invalid")
    if not publications:
        issues.append("prediction_publication_time_missing")
    elif max(publications) > min(cutoff, started):
        issues.append("prediction_published_after_observation")
    return {"report_date": report_date, "target_trade_date": trade_date,
            "prediction_id": prediction.get("prediction_id"), "prediction_fingerprint": _fingerprint(prediction),
            "publication_as_of": max(publications).isoformat() if publications else None,
            "issues": list(dict.fromkeys(issues))}


def _sample(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    up = sum(row["last"] > row["previous_close"] for row in rows)
    down = sum(row["last"] < row["previous_close"] for row in rows)
    result = {"up_count": up, "down_count": down, "flat_count": len(rows) - up - down}
    if up + down:
        result["breadth_ratio"] = round(up / (up + down), 4)
    for name, flag in (("limit_up", "at_upper_limit"), ("limit_down", "at_lower_limit")):
        known = [row[flag] for row in rows if row[flag] is not None]
        if known:
            result[name] = sum(known)
    return result


def _promotion(prediction: dict[str, Any], fresh: dict[str, Any], report_date: str) -> tuple[Any, dict, str | None]:
    snapshot = prediction.get("market_snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("report_date") != report_date:
        return None, {}, "report_day_ladder_snapshot_missing_or_wrong_day"
    members = snapshot.get("limit_pool_rows")
    if not isinstance(members, list):
        return None, {}, "report_day_ladder_members_missing"
    if not members:
        return None, {}, "report_day_ladder_has_no_members"
    codes = []
    for row in members:
        if not isinstance(row, dict):
            return None, {}, "report_day_ladder_member_invalid"
        code = str(row.get("code") or row.get("代码") or "").lower()
        height = _number(row.get("height", row.get("level", row.get("连板数"))))
        if not _STOCK.fullmatch(code) or code in codes or height is None or height < 1 or not height.is_integer():
            return None, {}, "report_day_ladder_member_invalid"
        if row.get("类型") not in (None, "ZT"):
            return None, {}, "report_day_ladder_contains_non_limit_up_member"
        for key in ("trade_date", "report_date", "日期"):
            if row.get(key):
                try:
                    if _date(row[key]) != report_date:
                        return None, {}, "report_day_ladder_member_wrong_day"
                except ValueError:
                    return None, {}, "report_day_ladder_member_wrong_day"
        codes.append(code)
    if snapshot.get("limit_up") is not None and _number(snapshot["limit_up"]) != len(codes):
        return None, {}, "report_day_ladder_member_count_mismatch"
    scope = {"scope": "report_day_limit_up_ladder_members", "report_date": report_date,
             "member_codes": codes, "denominator_count": len(codes),
             "confirmation": "last_equals_provider_upper_limit_not_continuous_sealing"}
    missing = [code for code in codes if code not in fresh or fresh[code]["at_upper_limit"] is None]
    scope["confirmed_bound_count"] = len(codes) - len(missing)
    if missing:
        scope["missing_codes"] = missing
        return None, scope, f"ladder_member_fresh_quote_or_upper_limit_missing:{len(missing)}/{len(codes)}"
    promoted = sum(fresh[code]["at_upper_limit"] for code in codes)
    scope["promoted_count"] = promoted
    return promoted / len(codes), scope, None


def collect_live_market_inputs(
    *, universe: dict[str, Any], prediction: dict[str, Any], client: Any = None,
    now: Callable[[], datetime] | None = None, codes: Iterable[str] | None = None,
    report_date: str | None = None, trade_date: str | None = None, prediction_id: str | None = None,
    phase: str | None = None, max_age_seconds: int = 120, batch_size: int = BATCH_SIZE,
) -> dict[str, Any]:
    """Acquire once and return auditable inputs plus an optional bound record payload.

    Partial samples stay in sample_metrics, never masquerading as the cached
    market population. A missing optional metric is omitted with one reason;
    complete fresh breadth can still be recorded when limit data is missing.
    Only the separate CLI's explicit --record path may persist a phase.
    """
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or not 1 <= max_age_seconds <= 300:
        raise ValueError("max_age_seconds must be in 1..300")
    if phase is not None and (phase not in _PHASE_WINDOWS or phase == "close"):
        raise ValueError("Only an actual intraday phase may be requested")
    report = _date(report_date or prediction.get("report_date"))
    target = _date(trade_date or prediction.get("target_trade_date"))
    population = _codes(universe.get("codes") or [])
    requested = population if codes is None else _codes(codes)
    if not set(requested).issubset(population):
        raise ValueError("Requested sample contains codes outside the cached universe")
    acquired = fetch_tencent_quotes(requested, client=client, now=now, batch_size=batch_size)
    started, fetched = _aware(acquired["request_started_at"]), _aware(acquired["fetched_at"])
    source_times = [_aware(row["source_as_of"]) for row in acquired["quotes"].values() if row["source_as_of"]]
    cutoff = min(source_times) if source_times else None
    fresh, rejected_codes = {}, {}
    for code in requested:
        row = acquired["quotes"].get(code)
        reasons = []
        if row is None:
            reasons.append("quote_missing_or_rejected")
        else:
            reasons.extend(item for item in row["issues"] if item.startswith(("source_timestamp_", "invalid_")))
            if row["source_as_of"]:
                observed = _aware(row["source_as_of"])
                if row["trade_date"] != target:
                    reasons.append("source_date_mismatch")
                if observed > min(_aware(row["fetched_at"]), fetched):
                    reasons.append("future_source_timestamp")
                if (fetched - observed).total_seconds() > max_age_seconds:
                    reasons.append("stale_source_timestamp")
        if reasons:
            rejected_codes[code] = reasons
        else:
            fresh[code] = row
    coverage = {"universe_count": len(population), "requested_count": len(requested),
                "returned_count": len(acquired["quotes"]), "fresh_count": len(fresh),
                "fresh_universe_ratio": len(fresh) / len(population),
                "missing_codes": [code for code in requested if code not in acquired["quotes"]],
                "not_requested_codes": [code for code in population if code not in requested],
                "rejected_codes": rejected_codes}
    sample = _sample(list(fresh.values()))
    sample_scope = {"scope": "fresh_returned_quotes_only", "member_codes": list(fresh),
                    "sample_size": len(fresh), "universe_count": len(population),
                    "upper_limit_covered": sum(r["at_upper_limit"] is not None for r in fresh.values()),
                    "lower_limit_covered": sum(r["at_lower_limit"] is not None for r in fresh.values())}
    metrics, scopes, missing = {}, {}, dict(_UNSUPPORTED)
    complete = len(fresh) == len(population) and set(requested) == set(population)
    market_scope = {"scope": universe.get("scope"), "expected_count": len(population), "covered_count": len(fresh)}
    for name in ("up_count", "down_count", "flat_count", "breadth_ratio", "limit_up", "limit_down"):
        if not complete:
            missing[name] = f"incomplete_cached_universe_fresh_coverage:{len(fresh)}/{len(population)}"
            continue
        if name == "breadth_ratio" and name not in sample:
            missing[name] = "no_advancing_or_declining_quotes"
            continue
        if name in ("limit_up", "limit_down"):
            bound = "upper" if name == "limit_up" else "lower"
            count = sample_scope[bound + "_limit_covered"]
            if count != len(population):
                missing[name] = f"provider_{bound}_limit_missing_or_invalid:{len(population) - count}/{len(population)}"
                continue
        metrics[name] = sample[name]
        scopes[name] = dict(market_scope)
        if name == "breadth_ratio":
            scopes[name].update(denominator="up_count + down_count", denominator_count=sample["up_count"] + sample["down_count"],
                                classification="raw_last_compared_with_provider_previous_close")
    promotion, promotion_scope, promotion_reason = _promotion(prediction, fresh, report)
    if promotion_reason:
        missing["promotion_rate"] = promotion_reason
    else:
        metrics["promotion_rate"] = promotion
        scopes["promotion_rate"] = promotion_scope
    binding = _binding(prediction, report_date=report, trade_date=target, prediction_id=prediction_id,
                       cutoff=cutoff or fetched, started=started)
    if universe.get("trade_date") != target or universe.get("scope") != "cached_hs_bj_a_share_universe":
        binding["issues"].append("universe_date_or_scope_mismatch")
    actual_phase = phase_at(fetched)
    selected_phase = phase or actual_phase
    issues = list(binding["issues"])
    outside = (actual_phase is None or actual_phase != selected_phase or phase_at(started) != selected_phase
               or fetched.date() != started.date() or fetched < started)
    if outside:
        issues.append("outside_window")
    if not complete:
        issues.append("incomplete_market_coverage")
    source_window_ok = bool(fresh) and all(phase_at(_aware(row["source_as_of"])) == selected_phase for row in fresh.values())
    if fresh and not source_window_ok:
        issues.append("source_outside_phase_window")
    if outside:
        status = "outside_window"
    elif binding["issues"]:
        status = "unbound"
    elif not acquired["quotes"]:
        status = "unavailable"
    elif not fresh:
        status = "stale" if any("stale_source_timestamp" in reasons or "source_date_mismatch" in reasons
                                 for reasons in rejected_codes.values()) else "unavailable"
    elif not complete:
        status = "partial"
    elif not source_window_ok:
        status = "outside_window"
    else:
        status = "ready"
    stale = any("stale_source_timestamp" in reasons or "source_date_mismatch" in reasons for reasons in rejected_codes.values())
    quality = {"status": "ok" if status == "ready" else "degraded" if fresh else "unavailable",
               "freshness_level": "mixed" if stale and fresh else "stale" if stale else "fresh" if fresh else "unknown",
               "used_stale": False, "missing_fields": sorted(missing), "missing_metrics": missing,
               "coverage": coverage, "scope": "only_reported_metrics_are_qualified"}
    source_as_of = cutoff.isoformat() if cutoff else None
    source_max = max(source_times).isoformat() if source_times else None
    lineage = {"source": SOURCE, "endpoint": ENDPOINT, "prediction_id": prediction.get("prediction_id"),
               "prediction_fingerprint": binding["prediction_fingerprint"], "data_cutoff": source_as_of,
               "source_as_of": source_as_of, "source_as_of_max": source_max, "timestamp_kind": "provider_field_30",
               "request_started_at": acquired["request_started_at"], "fetched_at": acquired["fetched_at"],
               "price_basis": "raw", "market_scope": universe.get("scope"), "metric_scopes": scopes,
               "universe_cache_sha256": universe.get("cache_sha256"), "used_stale": False}
    eligible = status == "ready" and bool(metrics)
    payload = {"report_date": report, "trade_date": target, "phase": selected_phase,
               "captured_at": acquired["fetched_at"], "metrics": metrics, "source_lineage": lineage,
               "quality": quality} if eligible else None
    return {"schema_version": SCHEMA, **acquired, "universe": universe, "binding": binding, "status": status,
            "phase": actual_phase, "requested_phase": phase, "source_as_of": source_as_of, "source_as_of_max": source_max,
            "coverage": coverage, "metrics": metrics, "metric_scopes": scopes, "missing_metrics": missing,
            "promotion_scope": promotion_scope, "sample_metrics": sample, "sample_scope": sample_scope,
            "issues": issues, "quality": quality, "max_age_seconds": max_age_seconds,
            "record_eligible": eligible, "record_payload": payload}


__all__ = ["parse_tencent_quotes", "fetch_tencent_quotes", "load_cached_universe", "collect_live_market_inputs", "phase_at"]
