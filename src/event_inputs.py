"""Production wiring for raw event archives and auditable resolved inputs."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import math

from data_sources.raw_bar_provider import RawBarProvider, load_raw_bars, is_valid_raw_bar
from event_facts import resolve_limit_event_facts
from limit_events import LIMIT_EVENT_FIELDS, event_field_coverage


def _rows(value):
    if hasattr(value, "to_dict"): value = value.to_dict("records")
    return [row for row in value or [] if isinstance(row, dict)]


def _usable(row, day):
    if row.get("trade_date") != day or not row.get("code") or not row.get("source"):
        return False
    try:
        stamp = datetime.fromisoformat(str(row.get("source_timestamp")).replace("Z", "+00:00"))
        return stamp.tzinfo is not None
    except (ValueError, TypeError):
        return False


def select_event_observations(existing, current, *, preserve_existing_members=False):
    """Fill incomplete archives using a single compatible whole observation.

    Do not mix competing captures field-by-field, replace known conflicting
    facts, or treat an empty/other-day response as proof that old events vanished.
    This is an availability repair, not an authority/membership reconciliation.
    Offline audit replay can pin both observations and membership to ``existing``;
    only compatible missing observations may then come from ``current``.
    """
    if not isinstance(existing, dict): return deepcopy(current)
    if not isinstance(current, dict) or current.get("trade_date") != existing.get("trade_date"):
        return deepcopy(existing)
    fresh = _rows(current.get("records"))
    if not fresh: return deepcopy(existing)
    if preserve_existing_members:
        by_code = {row.get("code"): row for row in fresh}
        fresh = [{**by_code.get(row.get("code"), row),
                  **{key: deepcopy(row[key]) for key in ("code", "name", "limit_count") if key in row}}
                 for row in _rows(existing.get("records"))]
    day = existing.get("trade_date")
    old = {row.get("code"): row for row in _rows(existing.get("records"))}
    result = deepcopy(existing)
    chosen = []
    upgraded = 0
    for incoming in fresh:
        previous = old.get(incoming.get("code"))
        if not previous or not _usable(previous, day):
            selected = incoming
        elif not _usable(incoming, day):
            selected = previous
        else:
            present = [key for key in LIMIT_EVENT_FIELDS if previous.get(key) is not None]
            compatible = all(incoming.get(key) == previous.get(key) for key in present)
            more = sum(incoming.get(key) is not None for key in LIMIT_EVENT_FIELDS) > len(present)
            selected = incoming if compatible and more else previous
        # The current authoritative identity remains separate from event data.
        selected = deepcopy(selected)
        for key in ("code", "name", "limit_count"):
            if key in incoming: selected[key] = deepcopy(incoming[key])
        upgraded += int(previous != selected)
        chosen.append(selected)
    if not upgraded and chosen == existing.get("records"):
        return deepcopy(existing)
    result["records"] = chosen
    result["row_count"] = len(chosen)
    result["field_coverage"] = event_field_coverage(chosen)
    result["refresh_summary"] = {"method": "compatible_complete_observation/v1", "updated_records": upgraded}
    return result


def prepare_limit_event_facts(snapshot, *, cache_dir=None, provider=None,
                             fetch_missing=False, price_rows=None, reference_prices=None):
    """Resolve facts using validated same-day bars; market facts remain untouched."""
    raw = deepcopy(snapshot) if isinstance(snapshot, dict) else {}
    day = raw.get("trade_date")
    codes = list(dict.fromkeys(row.get("code") for row in _rows(raw.get("records"))
                             if row.get("pool_type") == "ZT" and row.get("code") and row.get("trade_date") == day))
    collection = {"status": "provided", "requested": len(codes), "covered": 0, "errors": []}
    bars = _rows(price_rows)
    if price_rows is None:
        try:
            if fetch_missing:
                collection = (provider or RawBarProvider()).fetch_day(codes, day, cache_dir=cache_dir)
                bars = collection.get("records") or []
            else:
                bars = load_raw_bars(cache_dir, day) if cache_dir is not None else []
                collection["status"] = "cached" if bars else "not_collected"
        except (OSError, ValueError, RuntimeError) as exc:
            collection.update(status="unavailable", errors=[{"error": type(exc).__name__}])
            bars = []
    bars = [row for row in bars if is_valid_raw_bar(row, day) and row.get("code") in codes]
    references = {}
    for row in _rows(reference_prices):
        if str(row.get("date"))[:10] != day: continue
        try:
            value = float(row.get("close_raw"))
            if math.isfinite(value) and value > 0: references[row.get("code")] = value
        except (TypeError, ValueError): pass
    conflicts = [row["code"] for row in bars if row.get("code") in references
                 and not math.isclose(row["close_raw"], references[row["code"]], rel_tol=0, abs_tol=.005)]
    usable = [row for row in bars if row.get("code") not in conflicts]
    resolved = resolve_limit_event_facts(raw, price_rows=usable)
    collection = {key: value for key, value in collection.items() if key != "records"}
    collection["covered"] = len({row.get("code") for row in usable if row.get("date") == day and row.get("code") in codes})
    collection["reference_conflicts"] = conflicts
    resolved["bar_collection"] = deepcopy(collection)
    return {"snapshot": resolved, "collection": collection}
