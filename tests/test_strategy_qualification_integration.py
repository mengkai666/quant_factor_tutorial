from copy import deepcopy

from test_strategy_qualification import plan, quality, record, event_metrics


def context(*, validated=True, phase="close", bad_ai=True):
    from strategy_qualification import qualify_strategies
    from scenario_posterior import build_scenario_posterior_timeline
    from market_snapshot import build_phase_snapshot
    p=plan(); p["trade_candidates"]=[{"code":"sz000001","name":"核心","sector":"AI算力","role":"confirm"}]
    q=quality()
    if not bad_ai: q["modules"]["ai"]["status"]="ok"
    qualified=qualify_strategies([p],quality=q,validation_records=[record(p)] if validated else [],event_metrics=event_metrics(),report_date="2026-09-03",target_trade_date="2026-09-04")
    q["strategy_qualification"]=qualified
    q["publication_mode"]=qualified["publication_mode"]
    dates = {"close": "2026-09-03T15:00:00+08:00", "early_0935": "2026-09-04T09:35:00+08:00"}
    snapshots=[build_phase_snapshot(report_date="2026-09-03",phase="close",captured_at=dates["close"],metrics={"breadth_ratio":.4,"promotion_rate":.7,"limit_down":3},source_lineage={"source":"fixture"},quality={"status":"ok"}).to_dict()]
    if phase != "close": snapshots.append(build_phase_snapshot(report_date="2026-09-03",trade_date="2026-09-04",phase=phase,captured_at=dates[phase],metrics={"breadth_ratio":.7,"promotion_rate":.7,"limit_down":2},source_lineage={"source":"fixture"},quality={"status":"ok"}).to_dict())
    posterior=build_scenario_posterior_timeline([p],snapshots,report_date="2026-09-03",trade_date="2026-09-04",eligible_strategy_ids=qualified["eligible_strategy_ids"])
    return {"date_str":"2026-09-03","next_trade_date":"2026-09-04","data_quality":q,
        "publication_mode":q["publication_mode"],"market_state":{"publication_mode":q["publication_mode"]},
        "market_thesis":{"breadth_relay_state":{"breadth":"weak","relay":"strong"}},
        "mainline_review":{"top1":"AI算力"},"echelon":[{"height":"3板","stock_details":[{"code":"sz000001","name":"核心","ml":"AI算力"}]}],
        "scenario_plans":[p],"scenario_posterior":posterior,"event_metrics":event_metrics()}


def test_validated_postclose_plan_waits_instead_of_being_unassessable():
    from decision_dashboard import build_today_decision,build_today_focus_rows
    ctx=context()
    decision=build_today_decision(ctx)
    ready=decision["readiness"]
    assert ready["data"]["status"]=="ready"
    assert ready["strategy"]["status"]=="applicable"
    assert ready["signal"]["status"]=="not_triggered"
    assert ready["action"]["status"]=="wait_confirmation"
    assert ready["action"]["reason_code"]=="intraday_confirmation_pending"
    assert ready["plan_permitted"] and not ready["execution_ready"]
    assert decision["priority"]["primary"]["code"]=="sz000001"
    csv=build_today_focus_rows(ctx)[0]
    assert csv["条件计划许可"]=="是" and csv["可执行"]=="否"


def test_validated_strategy_can_confirm_intraday_even_if_ai_is_down():
    from decision_dashboard import build_today_decision
    a=build_today_decision(context(phase="early_0935"))
    b=build_today_decision(context(phase="early_0935",bad_ai=False))
    assert a["readiness"]["execution_ready"]
    assert a["readiness"]["action"]==b["readiness"]["action"]


def test_unverified_strategy_remains_observation_with_precise_reason():
    from decision_dashboard import build_today_decision
    got=build_today_decision(context(validated=False))
    assert not got["readiness"]["plan_permitted"]
    assert got["readiness"]["strategy"]["status"]=="unverified"
    assert got["priority"]["primary"] is None
    assert all(issue["module"]!="ai" for issue in got["readiness"]["issues"])


def test_scoped_mode_does_not_report_unknown_legacy_statistics_as_core_failure():
    from report_logic import resolve_publication_mode,build_market_state
    q=context()["data_quality"]
    assert resolve_publication_mode("decision",quality=q)=="decision"
    state=build_market_state(q,historical_samples=0)
    assert state["statistics_layer"]["sample_size"]==10
    assert state["publication_mode"]=="decision"
    assert q["status"]=="degraded"


def test_runtime_core_change_revokes_prior_scoped_permission():
    from decision_dashboard import build_today_decision
    c=context(phase="early_0935")
    c["data_quality"]["modules"]["price_raw"]["used_stale"]=True
    assert not build_today_decision(c)["readiness"]["execution_ready"]


def test_permitted_other_strategy_cannot_confirm_unvalidated_active_strategy():
    from scenario_posterior import build_scenario_posterior_timeline
    from market_snapshot import build_phase_snapshot
    p1=plan();p2=deepcopy(p1);p2["scenario_id"]="unregistered";p2["prior_probability"]=.99;p1["prior_probability"]=.01
    snap=build_phase_snapshot(report_date="2026-09-03",trade_date="2026-09-04",phase="early_0935",captured_at="2026-09-04T09:35:00+08:00",metrics={"breadth_ratio":.7,"promotion_rate":.7,"limit_down":1},source_lineage={"source":"fixture"},quality={"status":"ok"}).to_dict()
    result=build_scenario_posterior_timeline([p2,p1],[snap],report_date="2026-09-03",trade_date="2026-09-04",eligible_strategy_ids=[p1["scenario_id"]])
    assert result["timeline"][-1]["top_ranked_scenario_id"]=="unregistered"
    assert result["timeline"][-1]["active_scenario_id"]==p1["scenario_id"]


def test_phase_replay_revalidates_dependencies_and_never_upgrades_unapproved_strategy(tmp_path):
    import json
    from phase_monitor import record_phase_observation
    from decision_dashboard import build_today_decision
    from report_closure import build_decision_replay_context
    for validated in (True,False):
        ctx=context(validated=validated)
        history=tmp_path / ("ready.jsonl" if validated else "unverified.jsonl")
        decision=build_today_decision(ctx)
        validation_file=tmp_path / "validation.json"
        validation_file.write_text(json.dumps({"schema_version":"strategy-validation-set/v1","records":[record(ctx["scenario_plans"][0])]}),encoding="utf-8")
        history.write_text(json.dumps({"event_type":"prediction","prediction_id":"p","report_date":"2026-09-03","target_trade_date":"2026-09-04",
            "scenario_plans":ctx["scenario_plans"],"decision_context":build_decision_replay_context(ctx,decision)})+"\n",encoding="utf-8")
        result=record_phase_observation(history_path=history,phase_snapshot_path=tmp_path / ("r.jsonl" if validated else "u.jsonl"),
            report_date="2026-09-03",trade_date="2026-09-04",phase="early_0935",captured_at="2026-09-04T09:35:00+08:00",
            metrics={"breadth_ratio":.7,"promotion_rate":.7,"limit_down":2},source_lineage={"source":"fixture"},quality={"status":"ok"},validation_path=validation_file)
        # Baseline-dependent invalidations are deliberately not confirmed when
        # the report-day close is absent from the imported archive.
        assert not result["decision"]["readiness"]["execution_ready"]
        assert bool(result["decision"]["readiness"]["plan_permitted"]) is validated


def test_changed_strategy_rule_or_required_module_revokes_cached_permission():
    from decision_dashboard import build_today_decision
    c=context(phase="early_0935")
    c["scenario_plans"][0]["trigger_rules"]["early_0935"][0]["value"]=.99
    assert not build_today_decision(c)["readiness"]["execution_ready"]
    c=context(phase="early_0935");c["data_quality"]["modules"]["sector"]["status"]="unavailable"
    assert not build_today_decision(c)["readiness"]["execution_ready"]


def test_both_reports_show_strategy_specific_blockers_and_nonblocking_ai():
    from decision_dashboard import generate_dashboard_html,generate_dashboard_section
    from bs4 import BeautifulSoup
    for good in (True,False):
        ctx=context(validated=good)
        for render in (generate_dashboard_html,generate_dashboard_section):
            soup=BeautifulSoup(render(ctx),"html.parser")
            panel=soup.select_one('.strategy-qualification')
            assert panel is not None
            assert 'AI文案' in panel.get_text()
            assert '独立验证' in panel.get_text()
            assert ('条件许可' if good else '未验证') in panel.get_text()


def test_validation_samples_are_never_copied_into_public_quality_or_replay():
    import json
    from decision_dashboard import build_today_decision
    from report_closure import build_decision_replay_context
    c=context()
    public=json.dumps(c['data_quality'],ensure_ascii=False)
    assert 'sample-1' not in public
    assert 'validation_records' not in c['data_quality']['strategy_qualification']
    replay=build_decision_replay_context(c,build_today_decision(c))
    assert 'sample-1' not in json.dumps(replay)


def test_old_qualification_cannot_authorize_a_different_report_or_target():
    from decision_dashboard import build_today_decision
    for field,value in [('date_str','2026-09-04'),('next_trade_date','2026-10-05')]:
        c=context(phase='early_0935');c[field]=value
        got=build_today_decision(c)
        assert not got['readiness']['plan_permitted']


def test_missing_required_module_and_stale_event_metadata_revoke_cached_permission():
    from decision_dashboard import build_today_decision
    c=context(phase='early_0935');del c['data_quality']['modules']['sector']
    assert not build_today_decision(c)['readiness']['plan_permitted']
    c=context(phase='early_0935');c['event_metrics']['event_population']['report_date']='2026-09-02'
    assert not build_today_decision(c)['readiness']['plan_permitted']


def test_verified_but_inapplicable_strategy_is_not_reported_as_unverified():
    from scenario_plan import build_scenario_plans
    from strategy_qualification import qualify_strategies
    from decision_dashboard import build_today_decision
    c=context();p=build_scenario_plans(report_date='2026-09-03',market_thesis={'breadth_relay_state':{'breadth':'strong','relay':'strong'}})[1].to_dict()
    c['scenario_plans']=[p]
    c['data_quality']['strategy_qualification']=qualify_strategies([p],quality=quality(),validation_records=[record(p)],event_metrics=event_metrics(),report_date='2026-09-03',target_trade_date='2026-09-04')
    c['publication_mode']='observation';c['data_quality']['publication_mode']='observation'
    c['scenario_posterior']['timeline'][0]['plan_scenario_id']=None
    got=build_today_decision(c)['readiness']
    assert got['strategy']['status']=='not_applicable'
    assert got['action']['reason_code']=='strategy_not_applicable'
    assert not got['plan_permitted']


def test_hard_invalidated_qualified_plan_does_not_become_unverified_again():
    from decision_dashboard import build_today_decision
    c=context(phase='early_0935')
    phase=c['scenario_posterior']['timeline'][-1]
    phase.update(scenario_status='no_valid_scenario',active_scenario_id=None,decision_scenario_id=None,plan_scenario_id=None)
    for row in phase['scenarios']:row['state']='invalidated'
    got=build_today_decision(c)['readiness']
    assert got['action']['reason_code']=='no_valid_scenario'
    assert got['signal']['status']=='invalidated'
    assert not got['execution_ready']


def test_no_candidate_day_retains_verified_strategy_instead_of_becoming_unverified():
    from decision_dashboard import build_today_decision
    c=context();c['echelon']=[]
    result=build_today_decision(c)['readiness']
    assert result['strategy']['status']=='not_applicable'
    assert result['action']['reason_code']=='no_candidates'
    assert not result['plan_permitted']


def test_supplied_old_action_plan_cannot_borrow_another_strategys_permission():
    from decision_dashboard import build_today_decision
    c=context(phase='early_0935')
    old=build_today_decision(c)['action_plan']
    old['strategy_id']='unregistered'
    old['groups'][0]['rows'][0]['code']='sz000999'
    result=build_today_decision(c,action_plan=old)
    assert result['priority']['primary']['code']=='sz000001'
    assert result['action_plan']['strategy_id']=='selective_mainline_hold'


def test_public_report_context_uses_scoped_mode_without_changing_module_health():
    from decision_dashboard import build_dashboard_ctx,build_today_decision
    from report_logic import ReportContext,ReportPolicy,build_market_state
    c=context()
    q=c['data_quality']
    rc=ReportContext(report_date=c['date_str'],target_trade_date=c['next_trade_date'],
        policy=ReportPolicy.from_mode('decision'),quality=q,
        facts={'market_state':build_market_state(q),'mainline_review':c['mainline_review'],'strategy_event_metrics':c['event_metrics']},
        scenario_plans=c['scenario_plans'],scenario_posterior=c['scenario_posterior']).to_dict()
    ctx=build_dashboard_ctx(echelon=c['echelon'],report_date=c['date_str'],report_context=rc)
    result=build_today_decision(ctx)
    assert ctx['data_quality']['status']=='degraded'
    assert result['readiness']['plan_permitted']
    assert result['readiness']['action']['status']=='wait_confirmation'


def test_phase_import_rechecks_explicit_evidence_and_keeps_original_permission_ceiling(tmp_path):
    import json
    from market_snapshot import build_phase_snapshot,append_phase_snapshot_once
    from phase_monitor import record_phase_observation
    from report_closure import build_decision_replay_context
    from decision_dashboard import build_today_decision
    c=context()
    history=tmp_path/'history.jsonl';phases=tmp_path/'phases.jsonl';vpath=tmp_path/'evidence.json'
    evidence=record(c['scenario_plans'][0])
    vpath.write_text(json.dumps({'schema_version':'strategy-validation-set/v1','records':[evidence]}),encoding='utf-8')
    history.write_text(json.dumps({'event_type':'prediction','prediction_id':'p1','report_date':'2026-09-03','target_trade_date':'2026-09-04',
        'scenario_plans':c['scenario_plans'],'decision_context':build_decision_replay_context(c,build_today_decision(c))})+'\n',encoding='utf-8')
    append_phase_snapshot_once(phases,build_phase_snapshot(report_date='2026-09-03',phase='close',captured_at='2026-09-03T15:00:00+08:00',
        metrics={'breadth_ratio':.4,'promotion_rate':.7,'limit_down':3},source_lineage={'source':'fixture'},quality={'status':'ok'}))
    args=dict(history_path=history,phase_snapshot_path=phases,report_date='2026-09-03',trade_date='2026-09-04',phase='early_0935',
        metrics={'breadth_ratio':.7,'promotion_rate':.7,'limit_down':2},source_lineage={'source':'fixture'},quality={'status':'ok'},validation_path=vpath)
    ready=record_phase_observation(**args,captured_at='2026-09-04T09:35:00+08:00')
    assert ready['decision']['readiness']['execution_ready']
    evidence['status']='revoked'
    vpath.write_text(json.dumps({'schema_version':'strategy-validation-set/v1','records':[evidence]}),encoding='utf-8')
    revoked=record_phase_observation(**args,captured_at='2026-09-04T09:36:00+08:00')
    assert not revoked['decision']['readiness']['plan_permitted']
    assert not revoked['decision']['readiness']['execution_ready']
    assert 'sample-1' not in history.read_text(encoding='utf-8')


def test_optional_module_warning_is_listed_once_when_multiple_gates_report_it():
    from decision_dashboard import build_today_decision
    readiness = build_today_decision(context())["readiness"]
    warnings = readiness["nonblocking_issues"]
    assert warnings == [
        {"module": "ai", "status": "unavailable"},
        {"module": "bomb_metrics", "status": "unavailable"},
    ]
    assert readiness["action"]["status"] == "wait_confirmation"
