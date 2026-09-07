"""Describe event metric availability without authorizing any strategy.

The population is preserved separately: complete supplied fields never imply
complete market coverage. Zero observed breaks means reclose rate is N/A,
not missing, and certainly not a fabricated 0% success rate.
"""
from __future__ import annotations
from copy import deepcopy
import math


def _dict(value):
    return value if isinstance(value, dict) else {}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _count(value):
    n = _number(value)
    return int(n) if n is not None and n >= 0 and n.is_integer() else None


def _result(status, reason, *, value=None, observed=0, trials=None):
    return {"status": status, "reason": reason, "value": value, "observed": observed, "trials": trials}


def assess_event_metrics(metrics: dict | None) -> dict:
    m = _dict(metrics); coverage = _dict(m.get("event_input_coverage"))
    observed = _dict(coverage.get("observed")); counts = _dict(coverage.get("event_counts"))
    total = _count(coverage.get("source_rows"))
    population = deepcopy(_dict(m.get("event_population")) or _dict(coverage.get("population"))
                          or {"scope": "provided_rows_only", "complete": False})
    # Explicit malformed counters are not equivalent to observed zero events.
    conflict_count = _count(counts.get("inconsistent_rows"))
    conflict = conflict_count is not None and conflict_count > 0
    invalid_coverage = (
        ("source_rows" in coverage and total is None)
        or any(_count(v) is None or (total is not None and _count(v) > total) for v in observed.values())
    )
    if "event_counts" in coverage:
        required = ("attempted", "broken", "reclosed_known_on_broken", "inconsistent_rows")
        invalid_coverage = invalid_coverage or any(_count(counts.get(key)) is None for key in required)
        invalid_coverage = invalid_coverage or any(
            _count(v) is None or (total is not None and _count(v) > total) for v in counts.values()
        )
        # These are independent coverage counts, not the rates' own denominators.
        bounds = [
            (counts.get("broken"), counts.get("attempted")),
            (counts.get("reclosed_known_on_broken"), counts.get("broken")),
            (counts.get("reclosed_known_on_broken"), observed.get("reclosed")),
            (counts.get("attempted"), observed.get("limit_up_attempted")),
            (counts.get("broken"), observed.get("broken")),
        ]
        if "reclosed" in counts:
            bounds.append((counts.get("reclosed"), counts.get("reclosed_known_on_broken")))
        # Reclose observations outside broken rows must fit in the other rows.
        broken = _count(counts.get("broken"))
        reclosed_observed = _count(observed.get("reclosed"))
        if total is not None and broken is not None and reclosed_observed is not None:
            bounds.append((max(0, broken + reclosed_observed - total),
                           counts.get("reclosed_known_on_broken")))
        invalid_coverage = invalid_coverage or any(
            _count(child) is not None and _count(parent) is not None and _count(child) > _count(parent)
            for child, parent in bounds
        )

    def complete(*fields):
        return bool(total and all(_count(observed.get(field)) == total for field in fields))

    def rate(name):
        row = _dict(m.get(name)); trials = _count(row.get("trials")); successes = _count(row.get("successes"))
        value = _number(row.get("rate"))
        if conflict or invalid_coverage:
            return _result("invalid", "事件字段或覆盖计数相互矛盾")
        if not row:
            return _result("missing", "未提供指标")
        if trials is None or successes is None or successes > trials or (total is not None and trials > total):
            return _result("invalid", "样本分母或成功数非法")
        if trials and (value is None or not 0 <= value <= 1 or abs(value - successes / trials) > .001):
            return _result("invalid", "比率与有限计数不一致")
        if not trials:
            return _result("missing", "没有可核验的适用事件分母", trials=0)
        return _result("ready", "适用事件已观测", value=value, observed=successes, trials=trials)

    bomb = rate("bomb_rate")
    if bomb["status"] == "ready" and not complete("limit_up_attempted", "broken"):
        bomb = _result("missing", "封板尝试或炸板标记未完整提供")
    # A verified zero denominator can still prove that no breaks occurred.
    bomb_observed = complete("limit_up_attempted", "broken") and bomb["trials"] is not None
    if counts and bomb_observed and (
            _count(counts.get("attempted")) != bomb["trials"] or _count(counts.get("broken")) != bomb["observed"]):
        bomb = _result("invalid", "事件计数与炸板指标分母不一致")
    reclose = rate("reclose_rate")
    no_breaks = bomb_observed and bomb["status"] != "invalid" and bomb["observed"] == 0
    if no_breaks and not conflict and not invalid_coverage:
        if reclose["status"] == "invalid" or _count(_dict(m.get("reclose_rate")).get("trials")) not in {0, None}:
            reclose = _result("invalid", "无炸板却出现回封分母")
        else:
            reclose = _result("not_applicable", "完整观测表明没有炸板事件，回封率本次不适用", trials=0)
    elif reclose["status"] != "invalid":
        breaks = _count(counts.get("broken")) if counts else bomb.get("observed")
        known = _count(counts.get("reclosed_known_on_broken")) if counts else (breaks if complete("reclosed") else None)
        if bomb["status"] != "ready" or breaks is None or not breaks or known != breaks:
            reclose = _result("missing", "缺少完整炸板事件或其回封观察")
        elif reclose["status"] == "ready" and reclose["trials"] != breaks:
            reclose = _result("invalid", "回封分母不等于已观测炸板事件数")
    if counts and reclose["status"] == "ready" and (
            _count(counts.get("reclosed_known_on_broken")) != reclose["trials"]
            or ("reclosed" in counts and _count(counts.get("reclosed")) != reclose["observed"])):
        reclose = _result("invalid", "回封观察计数与指标不一致")
    board = _dict(m.get("board_structure")); sample = _count(board.get("sample_size"))
    one, turnover = _count(board.get("one_word_count")), _count(board.get("turnover_count"))
    if invalid_coverage or conflict or (sample is not None and total is not None and sample > total):
        board_result = _result("invalid", "板型统计或覆盖矛盾")
    elif not complete("board_type") or not sample:
        board_result = _result("missing", "板型未完整提供")
    elif sample != _count(observed.get("board_type")) or one is None or turnover is None or one + turnover != sample:
        board_result = _result("invalid", "板型数量与样本不一致")
    else:
        board_result = _result("ready", "已提供样本内板型结构", value={"one_word": one, "turnover": turnover}, observed=sample, trials=sample)
    return {"schema_version": "event-qualification/v1", "population": population,
            "metrics": {"bomb_rate": bomb, "reclose_rate": reclose, "board_structure": board_result}}
