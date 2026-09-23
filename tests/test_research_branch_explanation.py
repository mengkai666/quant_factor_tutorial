from copy import deepcopy

from test_strategy_qualification_integration import context
from test_strategy_qualification import quality, event_metrics


def research_context():
    from scenario_plan import build_scenario_plans
    from strategy_qualification import qualify_strategies
    from report_logic import build_market_state
    from market_snapshot import build_phase_snapshot
    from scenario_posterior import build_scenario_posterior_timeline
    ctx=context(validated=False)
    thesis={"breadth_relay_state":{"breadth":"neutral","relay":"weak"}}
    plans=[p.to_dict() for p in build_scenario_plans(report_date="2026-09-03",market_thesis=thesis)]
    q=quality()
    scoped=qualify_strategies(plans,quality=q,validation_records=[],event_metrics=event_metrics(),report_date="2026-09-03",target_trade_date="2026-09-04")
    q['strategy_qualification']=scoped;q['publication_mode']=scoped['publication_mode']
    snap=build_phase_snapshot(report_date="2026-09-03",phase="close",captured_at="2026-09-03T15:00:00+08:00",metrics={"breadth_ratio":.5,"promotion_rate":.3,"limit_down":2},source_lineage={"source":"fixture"},quality={"status":"ok"}).to_dict()
    posterior=build_scenario_posterior_timeline(plans,[snap],report_date="2026-09-03",trade_date="2026-09-04",eligible_strategy_ids=[])
    ctx.update(scenario_plans=plans,market_thesis=thesis,data_quality=q,publication_mode=q['publication_mode'],market_state=build_market_state(q),scenario_posterior=posterior)
    return ctx


def test_research_branch_does_not_promise_validation_will_unlock_it():
    from decision_dashboard import build_today_decision
    ctx=research_context();before=deepcopy(ctx)
    decision=build_today_decision(ctx);ready=decision['readiness']
    assert ready['strategy']['status']=='not_applicable'
    assert ready['action']['reason_code']=='research_only'
    assert not ready['plan_permitted'] and not ready['execution_ready']
    assert not any(i['module']=='strategy_validation' for i in ready['issues'])
    assert '研究' in ready['action']['reason'] or '观察' in ready['action']['reason']
    assert ctx==before


def test_real_core_gap_still_precedes_research_branch_explanation():
    from decision_dashboard import build_today_decision
    ctx=research_context();ctx['data_quality']['modules']['price_raw']['status']='unavailable'
    ready=build_today_decision(ctx)['readiness']
    assert ready['data']['status']=='missing'
    assert ready['action']['reason_code']=='data_unavailable'
    assert not ready['execution_ready']


def test_daily_record_keeps_research_no_trade_separate_from_data_shortage():
    from decision_dashboard import build_today_decision
    from trade_plan_review import build_daily_decision_record
    d=build_today_decision(research_context())
    record=build_daily_decision_record(d["action_plan"], report_date=d["report_date"], readiness=d["readiness"])
    assert record['status']=='no_trade_market_defensive'
    assert record['execution_allowed'] is False


def test_actionable_strategy_without_real_validation_is_still_unverified():
    from decision_dashboard import build_today_decision
    ready=build_today_decision(context(validated=False))['readiness']
    assert ready['strategy']['status']=='unverified'
    assert not ready['plan_permitted']
