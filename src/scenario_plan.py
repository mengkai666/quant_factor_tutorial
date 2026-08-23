# -*- coding: utf-8 -*-
"""动态明日推演场景。

场景是结构化盘面状态的条件树，不是固定 A/B/C/D 文案模板。每个场景
都明确 T+1 时间窗、失效条件、仓位边界，以及观察池和交易候选的分离。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


def _state(thesis: dict[str, Any] | None, key: str, fallback: str = "unknown") -> str:
    source = thesis if isinstance(thesis, dict) else {}
    breadth_relay = source.get("breadth_relay_state") if isinstance(source.get("breadth_relay_state"), dict) else {}
    dimensions = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    if key == "breadth":
        value = breadth_relay.get("breadth") or (dimensions.get("index_breadth") or {}).get("state")
    elif key == "relay":
        value = breadth_relay.get("relay") or (dimensions.get("relay_quality") or {}).get("state")
    else:
        value = (dimensions.get(key) or {}).get("state")
    return str(value or fallback).strip().lower()


def _safe_probability(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= 1 else None


_PHASE_KEYS = ("auction", "early_0935", "confirm_1000", "afternoon")


def _rule(
    rule_id: str, metric: str, operator: str, *, value: Any = None,
    baseline_metric: str | None = None, weight: float = 1.0, required: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "rule_id": rule_id, "metric": metric, "operator": operator,
        "weight": float(weight), "required": bool(required),
    }
    if value is not None:
        payload["value"] = value
    if baseline_metric:
        payload["baseline_metric"] = baseline_metric
    return payload


def _rules_for_scenario(
    scenario_id: str, threshold_adjustments: dict[str, Any] | None = None,
) -> tuple[dict[str, tuple[dict[str, Any], ...]], tuple[dict[str, Any], ...]]:
    """Single machine-readable rule source shared by posterior and rendering."""
    repair_ids = {
        "repair_after_breadth_only", "breadth_repair", "repair_confirmation",
        "intraday_divergence_repair", "low_level_diffusion",
    }
    retreat_ids = {"high_level_retreat", "risk_off_observation"}
    if scenario_id in {"mainline_continuation", "selective_mainline_hold"}:
        phases = {
            "auction": (
                _rule(f"{scenario_id}:auction:breadth", "breadth_ratio", "gte", value=.55),
                _rule(f"{scenario_id}:auction:risk", "limit_down", "lte_baseline", baseline_metric="limit_down", required=False),
            ),
            "early_0935": (
                _rule(f"{scenario_id}:early:breadth", "breadth_ratio", "gte", value=.60),
                _rule(f"{scenario_id}:early:relay", "promotion_rate", "gte", value=.55),
            ),
            "confirm_1000": (
                _rule(f"{scenario_id}:confirm:relay", "promotion_rate", "gte", value=.60),
                _rule(f"{scenario_id}:confirm:diffusion", "mainline_diffusion", "gte", value=.50, required=False),
            ),
            "afternoon": (
                _rule(f"{scenario_id}:afternoon:diffusion", "mainline_diffusion", "gte", value=.50),
                _rule(f"{scenario_id}:afternoon:risk", "limit_down", "lte", value=5, required=False),
            ),
        }
        invalidation = (
            _rule(f"{scenario_id}:invalid:risk", "limit_down", "gt_baseline", baseline_metric="limit_down"),
            _rule(f"{scenario_id}:invalid:relay", "promotion_rate", "lt", value=.35),
        )
    elif scenario_id in repair_ids:
        phases = {
            "auction": (
                _rule(f"{scenario_id}:auction:risk", "limit_down", "lte_baseline", baseline_metric="limit_down"),
            ),
            "early_0935": (
                _rule(f"{scenario_id}:early:breadth", "breadth_ratio", "gte_baseline", baseline_metric="breadth_ratio"),
            ),
            "confirm_1000": (
                _rule(f"{scenario_id}:confirm:relay", "promotion_rate", "gte_baseline", baseline_metric="promotion_rate"),
            ),
            "afternoon": (
                _rule(f"{scenario_id}:afternoon:diffusion", "mainline_diffusion", "gte", value=.50),
            ),
        }
        invalidation = (
            _rule(f"{scenario_id}:invalid:risk", "limit_down", "gt_baseline", baseline_metric="limit_down"),
            _rule(f"{scenario_id}:invalid:relay", "promotion_rate", "lt_baseline", baseline_metric="promotion_rate"),
        )
    elif scenario_id in retreat_ids:
        phases = {
            "auction": (
                _rule(f"{scenario_id}:auction:relay", "promotion_rate", "lt_baseline", baseline_metric="promotion_rate"),
            ),
            "early_0935": (
                _rule(f"{scenario_id}:early:risk", "limit_down", "gt_baseline", baseline_metric="limit_down"),
            ),
            "confirm_1000": (
                _rule(f"{scenario_id}:confirm:breadth", "breadth_ratio", "lt_baseline", baseline_metric="breadth_ratio"),
            ),
            "afternoon": (
                _rule(f"{scenario_id}:afternoon:diffusion", "mainline_diffusion", "lt", value=.50, required=False),
            ),
        }
        invalidation = (
            _rule(f"{scenario_id}:invalid:breadth", "breadth_ratio", "gte_baseline", baseline_metric="breadth_ratio"),
            _rule(f"{scenario_id}:invalid:relay", "promotion_rate", "gte_baseline", baseline_metric="promotion_rate"),
        )
    else:
        phases = {
            phase: (_rule(f"{scenario_id}:{phase}:breadth", "breadth_ratio", "gte_baseline", baseline_metric="breadth_ratio"),)
            for phase in _PHASE_KEYS
        }
        invalidation = (_rule(f"{scenario_id}:invalid:risk", "limit_down", "gt_baseline", baseline_metric="limit_down"),)
    adjustments = threshold_adjustments if isinstance(threshold_adjustments, dict) else {}
    calibrated_phases: dict[str, tuple[dict[str, Any], ...]] = {}
    for phase, rules in phases.items():
        calibrated: list[dict[str, Any]] = []
        for rule in rules:
            updated = dict(rule)
            override = adjustments.get(str(rule.get("metric")))
            if isinstance(override, dict) and _safe_probability(override.get("value")) is not None:
                # Ratios are in [0,1]. Absolute-count thresholds remain supported below.
                updated["operator"] = str(override.get("operator") or updated.get("operator"))
                updated["value"] = float(override["value"])
                updated.pop("baseline_metric", None)
                updated["calibrated"] = True
            elif isinstance(override, dict):
                try:
                    updated["operator"] = str(override.get("operator") or updated.get("operator"))
                    updated["value"] = float(override["value"])
                    updated.pop("baseline_metric", None)
                    updated["calibrated"] = True
                except (KeyError, TypeError, ValueError):
                    pass
            calibrated.append(updated)
        calibrated_phases[phase] = tuple(calibrated)
    return calibrated_phases, invalidation


@dataclass(frozen=True)
class ScenarioPlan:
    scenario_id: str
    scenario_type: str
    title: str
    probability: float | None
    prior_probability: float | None
    probability_source: str
    calibration_sample_size: int
    premise: tuple[str, ...]
    auction_triggers: tuple[str, ...]
    early_session_triggers: tuple[str, ...]
    confirmation_triggers: tuple[str, ...]
    afternoon_triggers: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    position_floor: float
    position_ceiling: float
    position_adjustments: tuple[str, ...]
    observation_roles: tuple[str, ...]
    observation_pool: tuple[dict[str, Any], ...]
    trade_candidates: tuple[dict[str, Any], ...]
    trigger_rules: dict[str, tuple[dict[str, Any], ...]]
    invalidation_rules: tuple[dict[str, Any], ...]
    position_adjustment_rules: tuple[dict[str, Any], ...]
    outcome_definition_id: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.scenario_id or not self.scenario_type:
            raise ValueError("场景必须有稳定的 scenario_id 和 scenario_type")
        if not 0 <= self.position_floor <= self.position_ceiling <= 1:
            raise ValueError("仓位边界必须满足 0 <= floor <= ceiling <= 1")
        if self.probability is not None and not 0 <= self.probability <= 1:
            raise ValueError("场景概率必须在 0 到 1 之间")
        for field_name in (
            "premise", "auction_triggers", "early_session_triggers",
            "confirmation_triggers", "afternoon_triggers",
            "invalidation_conditions", "position_adjustments",
            "observation_roles", "evidence_ids",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} 不能为空")
        if set(self.trigger_rules) != set(_PHASE_KEYS):
            raise ValueError("trigger_rules 必须覆盖竞价、9:35、10:00 和午后")
        if not self.invalidation_rules or not self.position_adjustment_rules:
            raise ValueError("必须提供结构化失效规则和仓位调整规则")
        observation_codes = {str(row.get("code") or row.get("代码") or "") for row in self.observation_pool}
        trade_codes = {str(row.get("code") or row.get("代码") or "") for row in self.trade_candidates}
        if not trade_codes <= observation_codes:
            raise ValueError("交易候选必须是观察池的子集")
        if not self.outcome_definition_id:
            raise ValueError("必须绑定 outcome_definition_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "scenario-plan/v1",
            "scenario_id": self.scenario_id,
            "scenario_type": self.scenario_type,
            "title": self.title,
            "probability": self.probability,
            "prior_probability": self.prior_probability,
            "probability_source": self.probability_source,
            "calibration_sample_size": self.calibration_sample_size,
            "premise": list(self.premise),
            "auction_triggers": list(self.auction_triggers),
            "early_session_triggers": list(self.early_session_triggers),
            "confirmation_triggers": list(self.confirmation_triggers),
            "afternoon_triggers": list(self.afternoon_triggers),
            "invalidation_conditions": list(self.invalidation_conditions),
            "position_floor": self.position_floor,
            "position_ceiling": self.position_ceiling,
            "position_adjustments": list(self.position_adjustments),
            "observation_roles": list(self.observation_roles),
            "observation_pool": [dict(row) for row in self.observation_pool],
            "trade_candidates": [dict(row) for row in self.trade_candidates],
            "trigger_rules": {phase: [dict(rule) for rule in rules] for phase, rules in self.trigger_rules.items()},
            "invalidation_rules": [dict(rule) for rule in self.invalidation_rules],
            "position_adjustment_rules": [dict(rule) for rule in self.position_adjustment_rules],
            "outcome_definition_id": self.outcome_definition_id,
            "evidence_ids": list(self.evidence_ids),
        }


def _plan(
    *, scenario_id: str, scenario_type: str, title: str, premise: Iterable[str],
    auction: Iterable[str], early: Iterable[str], confirmation: Iterable[str],
    afternoon: Iterable[str], invalidation: Iterable[str], floor: float, ceiling: float,
    adjustments: Iterable[str], roles: Iterable[str], candidates: Iterable[dict[str, Any]] = (),
    observations: Iterable[dict[str, Any]] = (), probability: float | None = None,
    prior_probability: float | None = None, evidence: Iterable[str],
    threshold_adjustments: dict[str, Any] | None = None, calibration_sample_size: int = 0,
) -> ScenarioPlan:
    trigger_rules, invalidation_rules = _rules_for_scenario(scenario_id, threshold_adjustments)
    normalized_probability = _safe_probability(probability)
    normalized_prior = _safe_probability(prior_probability)
    probability_source = "historical_calibration" if normalized_probability is not None and calibration_sample_size > 0 else (
        "supplied" if normalized_probability is not None else "unavailable"
    )
    midpoint = round((float(floor) + float(ceiling)) / 2, 4)
    position_rules = (
        {"rule_id": f"{scenario_id}:position:confirmed", "condition": "all_required_triggers", "action": "set_target", "target": float(ceiling)},
        {"rule_id": f"{scenario_id}:position:partial", "condition": "partial_confirmation", "action": "set_target", "target": midpoint},
        {"rule_id": f"{scenario_id}:position:invalidated", "condition": "any_invalidation", "action": "set_target", "target": float(floor)},
    )
    return ScenarioPlan(
        scenario_id=scenario_id, scenario_type=scenario_type, title=title,
        probability=normalized_probability, prior_probability=normalized_prior, probability_source=probability_source,
        calibration_sample_size=int(calibration_sample_size or 0), premise=tuple(premise),
        auction_triggers=tuple(auction), early_session_triggers=tuple(early),
        confirmation_triggers=tuple(confirmation), afternoon_triggers=tuple(afternoon),
        invalidation_conditions=tuple(invalidation), position_floor=float(floor),
        position_ceiling=float(ceiling), position_adjustments=tuple(adjustments),
        observation_roles=tuple(roles), observation_pool=tuple(dict(row) for row in observations),
        trade_candidates=tuple(dict(row) for row in candidates), trigger_rules=trigger_rules,
        invalidation_rules=invalidation_rules, position_adjustment_rules=position_rules,
        outcome_definition_id="market-thesis/v1", evidence_ids=tuple(evidence),
    )


def build_scenario_plans(
    *, report_date: str, market_thesis: dict[str, Any] | None = None,
    market_snapshot: dict[str, Any] | None = None,
    progression_chain: dict[str, Any] | None = None,
    focus_pool: Iterable[dict[str, Any]] | None = None,
    probabilities: dict[str, Any] | None = None,
    prior_probabilities: dict[str, Any] | None = None,
    threshold_adjustments: dict[str, Any] | None = None,
) -> list[ScenarioPlan]:
    """根据广度×接力状态生成 2~4 个可验证场景。"""
    thesis = market_thesis if isinstance(market_thesis, dict) else {}
    snapshot = market_snapshot if isinstance(market_snapshot, dict) else {}
    breadth = _state(thesis, "breadth")
    relay = _state(thesis, "relay")
    feedback = _state(thesis, "high_level_feedback")
    mainline = _state(thesis, "mainline_structure")
    state = f"breadth_{breadth}_relay_{relay}"
    limit_down = snapshot.get("limit_down")
    try:
        limit_down_text = str(int(float(limit_down))) if limit_down is not None else "缺失"
    except (TypeError, ValueError):
        limit_down_text = "缺失"
    candidates = tuple(dict(row) for row in (focus_pool or ()) if isinstance(row, dict))
    probabilities = probabilities if isinstance(probabilities, dict) else {}
    prior_probabilities = prior_probabilities if isinstance(prior_probabilities, dict) else {}
    threshold_adjustments = threshold_adjustments if isinstance(threshold_adjustments, dict) else {}
    ev = ("market_thesis", "market_snapshot", f"report_date:{report_date}")

    def calibrated_plan(**kwargs: Any) -> ScenarioPlan:
        scenario_id = str(kwargs.get("scenario_id") or "")
        calibration = threshold_adjustments.get(scenario_id)
        calibration = calibration if isinstance(calibration, dict) else {}
        kwargs["threshold_adjustments"] = calibration.get("thresholds") if isinstance(calibration.get("thresholds"), dict) else {}
        kwargs["calibration_sample_size"] = int(calibration.get("sample_size") or 0)
        kwargs["prior_probability"] = prior_probabilities.get(scenario_id)
        return _plan(**kwargs)

    common_invalidation = (
        "竞价与早盘触发方向相反",
        "10:00 前核心观察标的无法维持强势",
    )
    if state == "breadth_strong_relay_strong":
        return [
            calibrated_plan(
                scenario_id="mainline_continuation", scenario_type="主线延续",
                title="主线延续：广度与接力共振",
                premise=("市场广度强", "晋级/接力质量强", "高位反馈未转负"),
                auction=("核心与次高位竞价不弱于昨日收盘", "至少一个中位梯队出现主动溢价"),
                early=("9:35 前核心不炸板", "上涨家数保持在强区"),
                confirmation=("10:00 前晋级数量不低于昨日", "主线涨停扩散而非单点抱团"),
                afternoon=("午后核心回封或主线扩散继续", "尾盘无集中炸板"),
                invalidation=(*common_invalidation, "高位反馈转为负面且跌停扩张"),
                floor=.5, ceiling=.8, adjustments=("竞价和9:35同时确认则上调至上限", "只剩单一核心则回落至中枢"),
                roles=("observation_pool", "trade_candidates"), candidates=candidates[:3],
                probability=probabilities.get("mainline_continuation"), observations=candidates, evidence=ev,
            ),
            calibrated_plan(
                scenario_id="intraday_divergence_repair", scenario_type="分歧修复",
                title="分歧修复：强势未破但扩散变慢",
                premise=("广度仍强", "接力出现局部分歧", "主线结构仍完整"),
                auction=("核心换手竞价而非一字加速", "中位股没有批量低开"),
                early=("9:35 前炸板率可控", "回封意愿强于主动砸盘"),
                confirmation=("10:00 前至少两只中位标的完成回封", "跌停不扩散"),
                afternoon=("午后分歧转一致才允许加仓", "否则保持观察仓"),
                invalidation=(*common_invalidation, "高位核心跌破关键价且无回封"),
                floor=.2, ceiling=.5, adjustments=("确认回封后从观察仓增加", "10:00未修复则降至2成以下"),
                roles=("observation_pool", "trade_candidates"), candidates=candidates[:2],
                probability=probabilities.get("intraday_divergence_repair"), observations=candidates, evidence=ev,
            ),
        ]
    if state == "breadth_strong_relay_weak":
        return [
            calibrated_plan(
                scenario_id="repair_after_breadth_only", scenario_type="分歧修复",
                title="广度强、接力弱：等待晋级修复",
                premise=("市场广度强", "晋级率偏弱", "普涨尚未传导为有效接力"),
                auction=("竞价不追高位加速", "观察中位梯队是否出现真实承接"),
                early=("9:35 前涨跌家数保持强区", "高位不出现连续负反馈"),
                confirmation=("10:00 前晋级数量较昨日改善", "至少一个中位梯队主动回封"),
                afternoon=("午后扩散到主线第二梯队才可加仓", "否则只保留观察仓"),
                invalidation=(*common_invalidation, "竞价后晋级继续恶化", f"跌停家数明显高于当前{limit_down_text}家"),
                floor=.1, ceiling=.4, adjustments=("确认晋级修复才增加仓位", "只有广度没有接力不超过4成"),
                roles=("observation_pool", "trade_candidates"), candidates=candidates[:2],
                probability=probabilities.get("repair_after_breadth_only"), observations=candidates, evidence=ev + ("breadth_relay_state",),
            ),
            calibrated_plan(
                scenario_id="low_level_diffusion", scenario_type="低位扩散",
                title="低位扩散：高位不追，观察新梯队",
                premise=("宽度提供试错环境", "高位接力不稳定", f"主线结构={mainline}"),
                auction=("低位首板/二板竞价有主动性", "高位不出现批量加速"),
                early=("9:35 前低位涨停扩散", "主线中军未明显转弱"),
                confirmation=("10:00 前低位梯队出现两级以上结构", "高位负反馈未扩大"),
                afternoon=("午后低位梯队能够回封才转入候选", "只拉单点则继续观察"),
                invalidation=("低位扩散无法形成梯队", "高位负反馈扩大", "跌停家数快速上升"),
                floor=.0, ceiling=.3, adjustments=("只允许从观察仓起步", "低位梯队成形后才提升至3成"),
                roles=("observation_pool",), candidates=(),
                probability=probabilities.get("low_level_diffusion"), observations=candidates, evidence=ev,
            ),
            calibrated_plan(
                scenario_id="high_level_retreat", scenario_type="高位退潮",
                title="高位退潮：广度暂强也不等于可追高",
                premise=("接力质量弱", "高位反馈存在脆弱性", "宽度与高度背离"),
                auction=("高位核心竞价低于预期", "中位梯队无主动溢价"),
                early=("9:35 前高位炸板增加", "宽度强但高度继续下压"),
                confirmation=("10:00 前无有效晋级修复", "高位负反馈向中位扩散"),
                afternoon=("午后只记录修复信号", "不把单只回封视为退潮结束"),
                invalidation=("高位核心重新转强并带动中位晋级", "跌停不扩散且结构修复"),
                floor=.0, ceiling=.2, adjustments=("不追高位", "仅在修复场景确认后恢复观察仓"),
                roles=("observation_pool",), candidates=(),
                probability=probabilities.get("high_level_retreat"), observations=candidates, evidence=ev,
            ),
        ]
    if state == "breadth_weak_relay_weak":
        return [
            calibrated_plan(
                scenario_id="high_level_retreat", scenario_type="高位退潮",
                title="广度弱、接力弱：高位退潮优先",
                premise=("市场广度弱", "接力质量弱", "高位负反馈需要先止住"),
                auction=("高位核心竞价低于预期时不追", "观察是否出现批量低开与撤单"),
                early=("9:35 前跌停和炸板不再扩张", "高位反馈不继续向中位传导"),
                confirmation=("10:00 前至少看到宽度或晋级一项修复", "否则维持风险优先"),
                afternoon=("午后只记录修复信号", "修复未扩散前不启动交易候选"),
                invalidation=("跌停扩张并伴随高位负反馈", "晋级继续恶化且核心失守"),
                floor=.0, ceiling=.2, adjustments=("空仓或仅保留观察仓", "修复确认后再逐步恢复"),
                roles=("observation_pool",), candidates=(),
                probability=probabilities.get("high_level_retreat"), observations=candidates, evidence=ev,
            ),
            calibrated_plan(
                scenario_id="risk_off_observation", scenario_type="风险观察",
                title="风险观察：等待宽度与接力同时止跌",
                premise=("宽度弱", "接力弱", f"当前跌停约{limit_down_text}家"),
                auction=("竞价只记录状态，不把高开当作反转", "观察核心是否出现承接"),
                early=("9:35 前上涨家数不再恶化", "高位负反馈收敛"),
                confirmation=("10:00 前宽度和接力至少一项明确改善", "改善不能只来自单只个股"),
                afternoon=("午后确认修复能否扩散", "未扩散则继续防守"),
                invalidation=("跌停继续增加", "负反馈向中位梯队扩散"),
                floor=.0, ceiling=.1, adjustments=("不主动开仓", "仅在双确认后恢复观察仓"),
                roles=("observation_pool",), candidates=(),
                probability=probabilities.get("risk_off_observation"), observations=candidates, evidence=ev,
            ),
        ]
    if state == "breadth_weak_relay_strong":
        return [
            calibrated_plan(
                scenario_id="selective_mainline_hold", scenario_type="核心抱团",
                title="宽度弱、接力强：只做选择性核心",
                premise=("市场宽度弱", "局部接力仍强", "赚钱效应集中"),
                auction=("核心竞价强且后排不盲目跟涨", "不以指数低开单独否定核心"),
                early=("9:35 前核心强于市场", "非核心继续弱化"),
                confirmation=("10:00 前核心完成换手并保持强于市场", "接力未出现批量断裂"),
                afternoon=("午后只保留核心强度", "不扩散到后排则不加仓"),
                invalidation=("核心跌破竞价低点", "接力批量断裂", "跌停继续扩大"),
                floor=.1, ceiling=.35, adjustments=("只允许核心仓", "宽度未修复不提升总仓位"),
                roles=("observation_pool", "trade_candidates"), candidates=candidates[:1],
                probability=probabilities.get("selective_mainline_hold"), observations=candidates, evidence=ev,
            ),
            calibrated_plan(
                scenario_id="breadth_repair", scenario_type="宽度修复",
                title="宽度修复：等待赚钱效应从点到面",
                premise=("接力尚可但市场参与面弱", "修复需要宽度确认"),
                auction=("指数和主要板块竞价不再走弱", "上涨家数改善"),
                early=("9:35 前上涨占比明显回升", "核心不因宽度修复而补跌"),
                confirmation=("10:00 前上涨家数与涨停扩散同步改善", "接力保持稳定"),
                afternoon=("午后宽度持续才允许扩大观察仓", "否则回到核心抱团"),
                invalidation=("宽度继续恶化", "核心接力转弱"),
                floor=.0, ceiling=.35, adjustments=("宽度确认后逐步增加", "不做提前抢跑"),
                roles=("observation_pool",), candidates=(),
                probability=probabilities.get("breadth_repair"), observations=candidates, evidence=ev,
            ),
        ]

    return [
        calibrated_plan(
            scenario_id="risk_off_observation", scenario_type="高位退潮" if breadth == "weak" else "等待确认",
            title="风险优先：等待盘面重新给出方向",
            premise=(f"市场广度={breadth}", f"接力质量={relay}", f"高位反馈={feedback}"),
            auction=("竞价只做状态观察", "不把单一高开视为趋势确认"),
            early=("9:35 前观察涨跌家数和核心反馈", "记录是否出现批量负反馈"),
            confirmation=("10:00 前必须同时看到宽度和接力改善", "否则维持防守"),
            afternoon=("午后只跟踪修复是否扩散", "不满足条件不启动交易候选"),
            invalidation=("跌停扩张并伴随高位负反馈", "接力继续恶化"),
            floor=.0, ceiling=.2 if breadth == "weak" else .3,
            adjustments=("只保留观察仓", "两项确认同时满足才上调"),
            roles=("observation_pool",), candidates=(),
            probability=probabilities.get("risk_off_observation"), observations=candidates, evidence=ev,
        ),
        calibrated_plan(
            scenario_id="repair_confirmation", scenario_type="分歧修复",
            title="修复候选：等待结构从局部到整体",
            premise=("当前状态存在修复可能", "修复必须经过时间窗确认"),
            auction=("核心不再低开破位", "中位梯队出现承接"),
            early=("9:35 前负反馈收敛", "上涨家数不继续恶化"),
            confirmation=("10:00 前核心与中位同时稳定", "晋级或回封出现改善"),
            afternoon=("午后扩散成立才提高仓位", "单点脉冲不计为修复"),
            invalidation=("10:00 前没有结构性改善", "跌停和炸板同步增加"),
            floor=.0, ceiling=.35, adjustments=("确认后从0成逐步增加", "无法确认则保持空仓"),
            roles=("observation_pool",), candidates=(),
            probability=probabilities.get("repair_confirmation"), observations=candidates, evidence=ev,
        ),
    ]
