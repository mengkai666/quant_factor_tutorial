# -*- coding: utf-8 -*-
"""结构化盘面论点。

该模块只接收已经计算好的事实，不读取文件、不访问网络，也不从展示文案反推
仓位或结论。它是主报告、看板、审计和明日推演共享的盘面语义层。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


_DIMENSION_KEYS = (
    "index_breadth",
    "limit_up_diffusion",
    "relay_quality",
    "high_level_feedback",
    "mainline_structure",
    "index_sector_stock_resonance",
)


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int | None = None) -> int | None:
    """Parse an integer while preserving missing/invalid facts as unknown."""
    number = _number(value)
    return default if number is None else int(number)


def _known_state(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"unknown", "unavailable", "none", "null", "n/a"} or text in {"未知", "缺失", "不可用"}:
        return None
    return text


def _evidence_value(value: Any) -> str:
    return "unknown" if value is None else str(value)


def _cycle_polarity(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if any(token in text for token in ("转升", "反弹", "上升", "回暖", "修复", "bull", "rise", "up")):
        return "positive"
    if any(token in text for token in ("转弱", "下跌", "退潮", "下降", "bear", "fall", "down")):
        return "negative"
    return "neutral"


def _level(value: float | None, *, strong: float, weak: float) -> str:
    if value is None:
        return "unknown"
    if value >= strong:
        return "strong"
    if value <= weak:
        return "weak"
    return "neutral"


def _dimension(state: str, *, score: float | None = None, evidence: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"state": state, "score": score, "evidence": list(evidence)}


@dataclass(frozen=True)
class MarketThesis:
    """可审计的结构化盘面结论。"""

    report_date: str
    dimensions: dict[str, dict[str, Any]]
    breadth_relay_state: dict[str, Any]
    core_conflict: dict[str, Any]
    micro_cycle: dict[str, Any] = field(default_factory=dict)
    phase_resonance: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    evidence_ids: tuple[str, ...] = ()
    unavailable_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        missing = set(_DIMENSION_KEYS) - set(self.dimensions)
        if missing:
            raise ValueError(f"MarketThesis 缺少六维盘面事实: {sorted(missing)}")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence_ids"] = list(self.evidence_ids)
        payload["unavailable_capabilities"] = list(self.unavailable_capabilities)
        return payload


def build_breadth_relay_state(
    breadth_ratio: Any,
    promotion_rate: Any,
    *,
    ladder_integrity: Any = None,
    limit_up: Any = None,
    limit_down: Any = None,
) -> dict[str, Any]:
    """把市场广度和接力质量分开评级，避免普涨被误判为接力强。"""
    breadth = _number(breadth_ratio)
    promotion = _number(promotion_rate)
    integrity = _number(ladder_integrity)
    breadth_level = _level(breadth, strong=0.65, weak=0.35)
    # 晋级率是接力质量主指标；梯队完整度只作为独立证据，
    # 不能把完整但低质量的梯队直接抬升为强接力。
    relay_score = promotion if promotion is not None else integrity
    relay_level = _level(relay_score, strong=0.60, weak=0.35)
    state = f"breadth_{breadth_level}_relay_{relay_level}"
    return {
        "breadth": breadth_level,
        "relay": relay_level,
        "state": state,
        "breadth_ratio": breadth,
        "promotion_rate": promotion,
        "relay_score": relay_score,
        "ladder_integrity": integrity,
        "limit_up": _int(limit_up) if limit_up is not None else None,
        "limit_down": _int(limit_down) if limit_down is not None else None,
    }


def build_market_thesis(
    *,
    report_date: str,
    market_snapshot: dict[str, Any] | None = None,
    progression_chain: dict[str, Any] | None = None,
    ladder_metrics: dict[str, Any] | None = None,
    mainline_concentration: dict[str, Any] | None = None,
    timing: dict[str, Any] | None = None,
    market_state: dict[str, Any] | None = None,
    micro_cycle: dict[str, Any] | None = None,
    phase_resonance: dict[str, Any] | None = None,
    evidence_ids: list[str] | tuple[str, ...] | None = None,
) -> MarketThesis:
    """从已校验事实构建六维盘面论点。"""
    snapshot = dict(market_snapshot or {})
    progression = dict(progression_chain or {})
    ladder = dict(ladder_metrics or {})
    mainline = dict(mainline_concentration or {})
    timing = dict(timing or {})
    state = dict(market_state or {})
    micro = dict(micro_cycle or {})
    phase = dict(phase_resonance or {})

    breadth = _number(snapshot.get("breadth_ratio"))
    limit_up = _int(snapshot.get("limit_up", snapshot.get("zt")))
    limit_down = _int(snapshot.get("limit_down", snapshot.get("dt")))
    height = _int(snapshot.get("max_height", ladder.get("height")))
    promotion = _number(snapshot.get("promotion_rate"), _number(progression.get("promotion_rate")))
    integrity = _number(snapshot.get("ladder_integrity"))
    concentration = _number(snapshot.get("concentration"), _number(mainline.get("top_share")))
    breadth_relay = build_breadth_relay_state(
        breadth, promotion, ladder_integrity=integrity, limit_up=limit_up, limit_down=limit_down,
    )

    breadth_state = breadth_relay["breadth"]
    if limit_up is None and limit_down is None:
        limit_state = "unknown"
    elif limit_up is None or limit_down is None:
        limit_state = "partial"
    else:
        limit_state = "expanding" if limit_up >= 50 and limit_down <= 5 else (
            "contracting" if limit_up <= 20 or limit_down >= 15 else "mixed"
        )
    relay_state = breadth_relay["relay"]
    if limit_down is None:
        feedback_state = "unknown"
    else:
        feedback_state = "negative" if limit_down >= 15 else (
            "positive" if limit_down <= 5 and breadth_state == "strong" else "mixed"
        )
    mainline_state = (
        "unknown" if concentration is None else
        "concentrated" if concentration >= 0.55 else
        "diffuse" if concentration <= 0.30 else "balanced"
    )

    phase_state = _known_state(phase.get("phase") or phase.get("resonance_state") or phase.get("state"))
    micro_state = _known_state(micro.get("status") or micro.get("phase") or micro.get("state"))
    micro_resonance = phase.get("micro_resonance") if isinstance(phase.get("micro_resonance"), dict) else {}
    explicit_resonance = _known_state(
        micro_resonance.get("state") or micro_resonance.get("status") or micro_resonance.get("level")
    )
    conflict_tokens = ("conflict", "diverg", "背离", "冲突", "弱共振", "不共振")
    has_conflict = bool(explicit_resonance and any(token in explicit_resonance.lower() for token in conflict_tokens))
    cycle_polarities = {_cycle_polarity(phase_state), _cycle_polarity(micro_state)} - {"neutral"}
    daily_polarity = (
        "positive" if breadth_state == "strong" and feedback_state != "negative" else
        "negative" if breadth_state == "weak" or feedback_state == "negative" else "neutral"
    )
    if daily_polarity != "neutral" and cycle_polarities and daily_polarity not in cycle_polarities:
        has_conflict = True
    if has_conflict:
        resonance_state = "conflicted"
        index_sector_state = "conflicted"
    elif phase_state and micro_state:
        resonance_state = "confirmed"
        index_sector_state = "resonant"
    elif phase_state or micro_state:
        resonance_state = "partial"
        index_sector_state = "partial"
    else:
        resonance_state = "unverified"
        index_sector_state = "unverified"

    dimensions = {
        "index_breadth": _dimension(
            breadth_state, score=breadth,
            evidence=(f"breadth_ratio={breadth}" if breadth is not None else "breadth_ratio unavailable",),
        ),
        "limit_up_diffusion": _dimension(
            limit_state, score=None,
            evidence=(f"limit_up={_evidence_value(limit_up)}", f"limit_down={_evidence_value(limit_down)}"),
        ),
        "relay_quality": _dimension(
            relay_state, score=breadth_relay.get("relay_score"),
            evidence=(f"promotion_rate={_evidence_value(promotion)}", f"max_height={_evidence_value(height)}"),
        ),
        "high_level_feedback": _dimension(
            feedback_state, score=None,
            evidence=(f"limit_down={_evidence_value(limit_down)}", f"height={_evidence_value(height)}"),
        ),
        "mainline_structure": _dimension(
            mainline_state, score=concentration,
            evidence=(f"mainline={mainline.get('top_mainline') or snapshot.get('mainline_rank') or 'unknown'}",),
        ),
        "index_sector_stock_resonance": _dimension(
            index_sector_state, score=None,
            evidence=(
                f"phase={_evidence_value(phase_state)}",
                f"micro_cycle={_evidence_value(micro_state)}",
                f"resonance={resonance_state}",
            ),
        ),
    }

    if breadth_state == "strong":
        strength_side = "市场广度和低位扩散提供正向赚钱效应"
    elif relay_state == "strong":
        strength_side = "核心接力质量仍有支撑"
    else:
        strength_side = "盘面暂未形成明确的单边优势"
    if breadth_state == "strong" and relay_state in {"weak", "neutral"}:
        risk_side = "普涨扩散尚未向高位晋级和核心接力充分传导"
        resolution = "观察主线核心晋级、板块共振和高位负反馈是否同步改善"
    elif feedback_state == "negative":
        risk_side = "高位负反馈扩大，强势结构可能进入退潮"
        resolution = "观察跌停/炸板是否收敛以及核心标的能否修复"
    elif mainline_state == "diffuse":
        risk_side = "主线集中度不足，资金可能转向快速轮动"
        resolution = "观察是否出现持续两日以上的主线承接和核心梯队"
    else:
        risk_side = "盘面结构仍缺少足够的确认信号"
        resolution = "等待广度、接力与指数—板块—个股共振至少两项同步确认"

    confidence_parts = [x for x in (breadth, promotion, integrity, concentration) if x is not None]
    confidence = round(sum(confidence_parts) / len(confidence_parts), 4) if confidence_parts else None
    unavailable = []
    if not phase:
        unavailable.append("phase_resonance")
    if not micro:
        unavailable.append("micro_cycle")
    if state.get("publication_mode") != "decision":
        unavailable.append("strong_decision")

    return MarketThesis(
        report_date=str(report_date),
        dimensions=dimensions,
        breadth_relay_state=breadth_relay,
        core_conflict={
            "strength_side": strength_side,
            "risk_side": risk_side,
            "resolution_condition": resolution,
            "breadth_state": breadth_state,
            "relay_state": relay_state,
            "feedback_state": feedback_state,
        },
        micro_cycle=micro,
        phase_resonance=phase,
        confidence=confidence,
        evidence_ids=tuple(str(x) for x in (evidence_ids or ())),
        unavailable_capabilities=tuple(unavailable),
    )


__all__ = ["MarketThesis", "build_breadth_relay_state", "build_market_thesis"]



def _json_safe(value: Any, *, depth: int = 0, max_depth: int = 4) -> Any:
    """Convert small semantic summaries to JSON-native values only."""
    if depth > max_depth:
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            safe = _json_safe(item, depth=depth + 1, max_depth=max_depth)
            if safe is not None or item is None:
                result[str(key)] = safe
        return result
    if isinstance(value, (list, tuple)):
        return [safe for item in value if (safe := _json_safe(item, depth=depth + 1, max_depth=max_depth)) is not None]
    return None


def summarize_phase_resonance(result: dict[str, Any] | None) -> dict[str, Any]:
    """Extract the small JSON-safe phase/micro-cycle contract for all consumers.

    ``build_phase_resonance`` intentionally returns DataFrames and HTML fragments
    for rendering. Those objects must not leak into ReportContext, prediction
    snapshots, or audit JSON. This function keeps only semantic fields used for
    tomorrow's inference and drops presentation-only payloads.
    """
    source = result if isinstance(result, dict) else {}
    summary: dict[str, Any] = {}
    for key in ("phase", "phase_shape", "phase_names", "index_ret", "corr", "breadth"):
        if key in source:
            safe = _json_safe(source.get(key))
            if safe is not None:
                summary[key] = safe

    micro = source.get("micro_cycle")
    if isinstance(micro, dict):
        micro_summary = {}
        for key in (
            "status", "signal_date", "confirmation_date", "full_confirmation_date",
            "signal_return", "rising_days", "signal_basis",
        ):
            if key in micro:
                safe = _json_safe(micro.get(key))
                if safe is not None:
                    micro_summary[key] = safe
        # Events are useful only when they are primitive and small. Keep the
        # known event labels but never carry arbitrary nested rendering data.
        events = micro.get("events")
        if isinstance(events, dict):
            event_summary = {}
            for event_name, event in events.items():
                if not isinstance(event, dict):
                    continue
                event_values = {}
                for key in ("date", "high_date", "close_date", "low", "high", "close", "higher_low"):
                    if key in event:
                        safe = _json_safe(event.get(key))
                        if safe is not None:
                            event_values[key] = safe
                if event_values:
                    event_summary[str(event_name)] = event_values
            if event_summary:
                micro_summary["events"] = event_summary
        if micro_summary:
            summary["micro_cycle"] = micro_summary

    resonance = source.get("micro_resonance")
    if isinstance(resonance, dict):
        selected = {}
        for key in ("level", "status", "state", "breadth", "index_return", "excess_return", "reason"):
            if key in resonance:
                safe = _json_safe(resonance.get(key))
                if safe is not None:
                    selected[key] = safe
        if selected:
            summary["micro_resonance"] = selected
    return summary
