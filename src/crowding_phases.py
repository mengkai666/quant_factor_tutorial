"""逐日价格生命周期分段；不读取人工PERIODS/GROUPS，不证明资金因果。

篮子仅在核心代理确认当日冻结，不用以后赢家补成员；退出/缺价仍留档。
阶段开始日为当日可见结论，observed_since独立记录确认前观察，不回填历史。
"""
from __future__ import annotations

import math
from copy import deepcopy

RULE_VERSION = "crowding-auto-phase/2026-10-25-v1"
RULES = dict(basket_size=6, min_members=2, strong_share=.7, weak_share=.6,
             weak_drawdown=-10, break_median_r1=-2, confirmation_days=2,
             recovery_days=3, low_lookback=3)
STAGES = {
    "waiting": "观察等待", "formation": "形成待验证", "focus": "聚焦强化",
    "divergence": "分歧与过渡", "rotation": "核心换位",
    "pressure": "瓦解压力", "broken": "瓦解",
    "stabilizing": "止跌待验证", "clearance": "出清观察",
    "repair_wait": "修复待验证", "repair": "修复与竞争",
    "trial": "新周期试错", "insufficient": "数据不足",
}
LIMITATION = ("自动边界沿用形成/强化/分歧/换位/瓦解/出清观察/修复/试错的阶段含义，"
              "仅为未校准的价格规则，不自动认定行业主线、风格切换、断板或真实资金出清。"
              "新旧冻结篮子允许并行；首个篮子可能在样本左边界已存在。"
              "使用当前缓存重建，不是当时已发布信号，不生成交易资格。")


def _finite(value):
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _ready(observation, mats):
    date = observation["date"]
    return (observation.get("coverage_ok", False) and mats is not None and
            date in mats["r20"].index and mats["r20"].loc[date].notna().any())


def _assess(cohort, date, complete, mats, rules, new_lows):
    from crowding_cycle import metrics_at
    codes = cohort["codes"]
    metrics = [metrics_at(mats, code, date) for code in codes] if mats else []
    valid = [m for m in metrics if all(_finite(m.get(k)) for k in ("r1", "r5", "r20", "dd20"))
             and m.get("above_ma5") is not None]
    lows = [new_lows.at[date, code] if new_lows is not None and code in new_lows.columns else None
            for code in codes]
    known_lows = all(_finite(value) for value in lows)
    known = complete and len(codes) >= rules["min_members"] and len(valid) == len(codes) and known_lows
    strong = sum(m["above_ma5"] and m["r5"] > 0 for m in valid) / len(codes) if known else None
    weak = sum(not m["above_ma5"] and m["dd20"] <= rules["weak_drawdown"] for m in valid) / len(codes) if known else None
    base = dict(cohort_id=cohort["id"], coverage=len(valid), denominator=len(codes),
                strong_share=round(strong, 4) if strong is not None else None,
                weak_share=round(weak, 4) if weak is not None else None,
                last_known_stage=cohort.get("last_known_stage"), core_failures=cohort["core_failures"])
    if not known:
        cohort.update(strong_days=0, stable_days=0, recovery_days=0,
                      strong_since=None, stable_since=None, recovery_since=None, rotation_days=0)
        stage, reason, observed = "insufficient", "截面、冻结成员连续价格或交易日历不足；暂停确认，不删除缺失成员。", date
    else:
        median_r1 = sorted(m["r1"] for m in valid)
        size = len(median_r1)
        median_r1 = (median_r1[(size - 1) // 2] + median_r1[size // 2]) / 2
        base["median_r1"] = round(median_r1, 4)
        for key, condition in (("strong", strong >= rules["strong_share"] and weak < rules["weak_share"]),
                               ("stable", not any(lows)),
                               ("recovery", cohort["was_broken"] and strong >= rules["strong_share"] and weak < rules["weak_share"])):
            cohort[key + "_days"] = cohort[key + "_days"] + 1 if condition else 0
            cohort[key + "_since"] = (cohort.get(key + "_since") or date) if condition else None
        if weak >= rules["weak_share"] and median_r1 <= rules["break_median_r1"]:
            cohort["was_broken"] = True
            cohort["last_broken_on"] = date
            # 瓦解终止此前换位观察，旧团恢复必须进入修复路径。
            cohort.update(stable_days=0, recovery_days=0, stable_since=None, recovery_since=None, rotation_days=0)
            stage, reason, observed = "broken", "冻结篮子多数深度回撤且当日中位收益继续下跌；价格瓦解，不证明资金出清。", date
        elif cohort["was_broken"]:
            if cohort["recovery_days"] >= rules["recovery_days"]:
                stage, reason, observed = "repair", "同一旧篮子多数连续3日恢复MA5及正5日收益；保留瓦解历史，不改名新团。", cohort["recovery_since"]
            elif cohort["recovery_days"]:
                stage, reason, observed = "repair_wait", "旧篮子恢复尚不足3个连续完整交易日。", cohort["recovery_since"]
            elif cohort["stable_days"] >= rules["confirmation_days"]:
                stage, reason, observed = "clearance", "同一篮子连续2日无人创前3交易日收盘新低；仅出清观察，不认定出清完成。", cohort["stable_since"]
            elif cohort["stable_days"]:
                stage, reason, observed = "stabilizing", "仅初步止跌，等待连续完整交易日验证。", cohort["stable_since"]
            else:
                stage, reason, observed = "pressure", "旧篮子尚未连续止跌；仍保留历史瓦解与价格压力。", date
        elif weak >= rules["weak_share"] or (cohort["core_failures"] >= 2 and strong < rules["strong_share"]):
            stage, reason, observed = "pressure", "冻结成员深度回撤占比达到门槛，或多次核心失效伴随群体走弱；不将单股失效等同整体瓦解。", date
        elif cohort["strong_days"] >= rules["confirmation_days"]:
            stage, reason, observed = "focus", "冻结篮子多数连续2日在MA5上且5日收益为正，深度回撤占比未达风险门槛。", cohort["strong_since"]
        elif cohort["strong_days"]:
            stage, reason, observed = "formation", "群体价格持续性初现，等待第2个连续完整交易日。", cohort["strong_since"]
        else:
            stage, reason, observed = "divergence", "冻结篮子强弱分化，未达到多日强化或多数瓦解条件。", date
        cohort["last_known_stage"] = STAGES[stage]
    base.update(stage_id=stage, stage=STAGES[stage], reason=reason, observed_since=observed,
                strong_days=cohort["strong_days"], stable_days=cohort["stable_days"],
                recovery_days=cohort["recovery_days"], was_broken=cohort["was_broken"],
                last_broken_on=cohort.get("last_broken_on"), evidence_as_of=date if known else None)
    return base


def build_automatic_phases(daily, mats, names, *, window_start, requested_date, calendar_known=True):
    """先跑全部可见前置行情再裁窗口。滚动窗口不重置篮子/阶段记忆。

    输出每个观察日（包括缺价），相邻同阶段且同篮子合并；无固定5日边界。
    """
    rules = deepcopy(RULES)
    cohorts, records, events = [], [], []
    active, previous_proxy, previous_key, phase_since, phase_observed, phase_confirmed = None, None, None, None, None, None
    if mats is not None:
        prices = mats["price"]
        prior_low = prices.shift(1).rolling(rules["low_lookback"], min_periods=rules["low_lookback"]).min()
        new_lows = prices.lt(prior_low).astype(float).where(prices.notna() & prior_low.notna())
    else:
        new_lows = None
    for observation in daily:
        date = observation["date"]
        if date > requested_date:
            break
        complete = bool(calendar_known and _ready(observation, mats) and not
                        (observation.get("proxy_state") == "过期" and not observation.get("confirmed_proxy")))
        proxy = observation.get("confirmed_proxy") if complete else previous_proxy
        candidate_codes = observation.get("candidate_codes", []) if complete else []
        cohort_created, rotated = False, False
        if complete and proxy != previous_proxy:
            if previous_proxy and active:
                from crowding_cycle import metrics_at
                prior_metrics = metrics_at(mats, previous_proxy, date)
                # 资格过期后新代理接上，也不能把旧成员仍缺价记成价格失败。
                failure = (previous_proxy not in candidate_codes and
                           all(_finite(prior_metrics.get(k)) for k in ("r1", "r5", "r20", "dd20")))
                if failure:
                    active["core_failures"] += 1
                events.append(dict(date=date, kind="core_invalidated" if failure else "core_changed",
                                   cohort_id=active["id"], old_code=previous_proxy, new_code=proxy))
            if proxy:
                matched = next((c for c in reversed(cohorts) if proxy in c["codes"]), None)
                if matched is None:
                    ranked = observation.get("candidate_scores", [])
                    ranked = sorted(ranked, key=lambda x: (-x["score"], x["code"]))
                    codes = [proxy] + [x["code"] for x in ranked if x["code"] != proxy]
                    codes = list(dict.fromkeys(codes))[:rules["basket_size"]]
                    matched = dict(id=f"price-cohort:{date}:{proxy}", formed_on=date, anchor_code=proxy,
                                   anchor_name=names.get(proxy, proxy), codes=codes, was_broken=False,
                                   core_failures=0, strong_days=0, stable_days=0, recovery_days=0,
                                   rotation_days=0, left_censored=not cohorts)
                    cohorts.append(matched)
                    cohort_created = True
                    events.append(dict(date=date, kind="basket_frozen", cohort_id=matched["id"], codes=list(codes)))
                elif matched is active and previous_proxy and not matched["was_broken"]:
                    rotated = True
                    matched["rotation_days"] = 1
                    events.append(dict(date=date, kind="within_basket_rotation", cohort_id=matched["id"], new_code=proxy))
                elif matched is not active:
                    matched["rotation_days"] = 0
                    events.append(dict(date=date, kind="old_basket_return", cohort_id=matched["id"], new_code=proxy))
                active = matched
            previous_proxy = proxy
        snapshots = [_assess(c, date, complete, mats, rules, new_lows) for c in cohorts]
        current = next((s for s in snapshots if active and s["cohort_id"] == active["id"]), None)
        parallel = [s for s in snapshots if not active or s["cohort_id"] != active["id"]]
        if not complete or current and current["stage_id"] == "insufficient":
            stage, reason, observed = "insufficient", "缺少完整交易日历/价格窗口或冻结篮子成员；连续确认归零，保留上一有效阶段。", date
        elif current:
            stage, reason, observed = current["stage_id"], current["reason"], current["observed_since"]
            if active["rotation_days"] and not active["was_broken"] and stage not in ("broken", "pressure"):
                if not rotated:
                    active["rotation_days"] += 1
                if active["rotation_days"] <= rules["confirmation_days"] or stage != "focus":
                    stage, reason, observed = "rotation", "已确认代理在同一冻结篮子内换位；群体不重建，仍需检验同伴持续性。", date
                else:
                    active["rotation_days"] = 0
            if not proxy and stage in ("focus", "formation", "rotation"):
                stage, reason, observed = "divergence", "核心代理已失效或待替换，但同伴尚未多数瓦解；不能继续确认核心强化。", date
            if cohort_created and len(cohorts) > 1 and stage == "formation":
                stage, reason = "trial", "确认代理不属于既有冻结篮子，建立独立新观察篮子；旧篮子同步留档，试错仍待群体持续性。"
            elif not proxy and observation.get("pending_proxy") not in (None, *active["codes"]) and stage in ("clearance", "stabilizing", "divergence"):
                stage, reason, observed = "trial", "出现旧篮子外待确认代理；仅新周期试错，旧团状态独立保留。", date
        else:
            stage = "formation" if candidate_codes else "waiting"
            reason = "已有当日价格候选，核心及群体尚待跨日确认。" if candidate_codes else "完整价格窗口尚无合格候选；允许等待。"
            observed = date
        key = (stage, active["id"] if active else None)
        if key != previous_key:
            phase_since, phase_observed = date, observed
            phase_confirmed = None if stage in ("waiting", "formation", "trial", "repair_wait", "stabilizing", "insufficient") else date
            events.append(dict(date=date, kind="phase_changed", stage=STAGES[stage], cohort_id=key[1],
                               confirmed_on=phase_confirmed, observed_since=phase_observed, reason=reason))
            previous_key = key
        last_known = next((r["stage"] for r in reversed(records) if r["stage_id"] != "insufficient"), None)
        records.append(dict(date=date, stage_id=stage, stage=STAGES[stage], stage_since=phase_since,
                            observed_since=phase_observed, confirmed_on=phase_confirmed, reason=reason,
                            active_cohort_id=key[1], confirmed_proxy=proxy if complete else None,
                            pending_proxy=observation.get("pending_proxy") if complete else None,
                            candidate_count=len(candidate_codes),
                            coverage_ok=complete, evidence_as_of=date if stage != "insufficient" else None,
                            last_known_stage=last_known, cohorts=snapshots,
                            parallel_cohort_ids=[s["cohort_id"] for s in parallel],
                            parallel_risk_count=sum(s["stage_id"] in ("broken", "pressure") for s in parallel),
                            rule_version=RULE_VERSION, knowledge_status="reconstructed_from_current_cache",
                            execution_status="not_qualified"))
    visible = [r for r in records if r["date"] >= window_start]
    segments = []
    for record in visible:
        key = (record["stage_id"], record["active_cohort_id"], record["stage_since"])
        if not segments or key != (segments[-1]["stage_id"], segments[-1]["cohort_id"], segments[-1]["origin_start"]):
            segments.append(dict(id=f"auto-phase:{record['stage_since']}:{record['stage_id']}",
                                 start=record["date"], end=record["date"], origin_start=record["stage_since"],
                                 observed_since=record["observed_since"], confirmed_on=record["confirmed_on"],
                                 stage_id=record["stage_id"], title=record["stage"], cohort_id=record["active_cohort_id"],
                                 left_censored=record["stage_since"] < window_start or not segments,
                                 period_kind="automatic", boundary_source="逐日价格状态迁移；不是资金因果分界",
                                 start_reason=record["reason"], end_reason=record["reason"], observations=0))
        segments[-1].update(end=record["date"], end_reason=record["reason"],
                            observations=segments[-1]["observations"] + 1)
    return dict(rule_version=RULE_VERSION, rules=rules, stage_definitions=STAGES.copy(),
                window_start=window_start, requested_date=requested_date,
                knowledge_status="reconstructed_from_current_cache", limitation=LIMITATION,
                daily=visible, segments=segments, cohorts=deepcopy(cohorts),
                events=[x for x in events if x["date"] >= window_start],
                latest=deepcopy(visible[-1]) if visible else None)
