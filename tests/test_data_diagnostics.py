"""Offline behavioral contracts for presentation-only, grouped root causes."""
from copy import deepcopy
import json
import math
import socket

from bs4 import BeautifulSoup
import pytest


VALIDATION_ISSUES = [
    "validation_schema_version_mismatch", "validation_strategy_id_mismatch",
    "validation_rule_version_mismatch", "validation_rule_fingerprint_mismatch",
    "validation_outcome_definition_id_mismatch", "validation_status_mismatch",
    "validation_method_mismatch", "validation_source_missing",
    "validation_evidence_ref_missing", "invalid_report_or_target_date",
    "invalid_validation_dates", "validation_sample_count_invalid",
]
EVENTS = ("bomb_rate", "reclose_rate", "board_structure")
INTRADAY = ["auction", "early_0935", "confirm_1000", "afternoon"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("diagnostics tests must not use the network")
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)


def validation(sid, usable=True):
    return {
        "schema_version": "strategy-validation-assessment/v1", "strategy_id": sid,
        "rule_version": "scenario-rules/v1", "rule_fingerprint": "fixture-fingerprint",
        "outcome_definition_id": "trade-plan-net-return/v1",
        "status": "validated" if usable else "unverified",
        "issues": [] if usable else list(VALIDATION_ISSUES),
        "sample_size": 10 if usable else 0,
        "declared_sample_size": 10 if usable else None,
        "source": "fixture_research" if usable else None,
        "evidence_ref": "fixture://independent-validation" if usable else None,
        "evaluated_at": "2026-08-25T16:00:00+08:00" if usable else None,
        "valid_from": "2026-08-26" if usable else None,
        "valid_until": "2026-10-01" if usable else None,
    }


def strategy(sid="selective_mainline_hold", *, status="eligible", usable=True, deps=None):
    summary = validation(sid, usable)
    return {
        "strategy_id": sid, "title": sid, "status": status,
        "plan_permitted": status == "eligible", "validation": summary,
        "required_modules": ["universe", "price_raw", "breadth", "limit_pool", "sector"],
        "event_dependencies": dict(deps or {}), "issues": list(summary["issues"]),
        "recheck_conditions": [] if usable else ["提供独立样本外验证报告；历史命中率不能代替。"],
    }


def event_assessment(*, scope="full_market_limit_up_attempts", complete=True, **states):
    return {
        "schema_version": "event-qualification/v1",
        "population": {"scope": scope, "complete": complete,
                       "report_date": "2026-09-08", "source": "fixture_events"},
        "metrics": {name: {"status": states.get(name, "ready"), "reason": "fixture",
                           "observed": 3, "trials": 10,
                           "value": {"one_word": 1, "turnover": 9} if name == "board_structure" else .3}
                    for name in EVENTS},
    }


def quality(*rows, events=None, ai=False):
    rows = rows or (strategy(),)
    modules = {name: {"status": "ok", "critical": name in {"universe", "price_raw", "breadth", "limit_pool"}}
               for name in ("universe", "price_raw", "price_qfq", "breadth", "limit_pool",
                            "echelon", "sector", "daily_delta", "history", "bomb_metrics", "ai")}
    events = deepcopy(events or event_assessment())
    module_ready = all(item["status"] in {"ready", "not_applicable"} for item in events["metrics"].values())
    modules["bomb_metrics"].update(status="ok" if module_ready else "unavailable", critical=False)
    modules["ai"].update(status="unavailable" if ai else "ok", critical=False,
                         errors=["fixture service timeout"] if ai else [])
    scoped = {
        "schema_version": "strategy-qualification-set/v1", "report_date": "2026-09-08",
        "target_trade_date": "2026-09-09", "core_ready": True, "core_issues": [],
        "strategies": {row["strategy_id"]: deepcopy(row) for row in rows},
        "eligible_strategy_ids": [row["strategy_id"] for row in rows if row["plan_permitted"]],
        "publication_mode": "decision" if any(row["plan_permitted"] for row in rows) else "observation",
        "event_qualification": events, "nonblocking_modules": ["ai"] if ai else [],
    }
    return {"status": "degraded" if ai or not module_ready else "ok", "modules": modules,
            "publication_mode": scoped["publication_mode"], "critical_blocked": [],
            "strategy_qualification": scoped}


def latest_shape():
    active = strategy(usable=False, status="unverified", deps={"board_structure": False})
    active["issues"].append("board_structure:missing")
    return quality(active,
                   strategy("breadth_repair", status="research_only", usable=False),
                   strategy("high_level_retreat", status="research_only", usable=False),
                   events=event_assessment(scope="closing_limit_pool", complete=False,
                                           bomb_rate="missing", reclose_rate="missing", board_structure="missing"),
                   ai=True)


def build(q, **kwargs):
    from data_diagnostics import build_data_diagnostics
    return build_data_diagnostics(q, **kwargs)


def by_code(q, **kwargs):
    result = build(q, **kwargs)
    assert len({item["code"] for item in result}) == len(result)
    return {item["code"]: item for item in result}


def assert_public(result):
    for item in result:
        assert set(item) == {"code", "status", "title", "impact", "recovery", "affected_strategies", "details"}
        assert all(isinstance(item[key], str) and item[key] for key in ("code", "status", "title", "impact", "recovery"))
        assert isinstance(item["affected_strategies"], list)
        assert all(isinstance(sid, str) for sid in item["affected_strategies"])
        assert isinstance(item["details"], list)
        for detail in item["details"]:
            assert isinstance(detail, dict)
            assert all(isinstance(key, str) for key in detail)
            assert all(value is None or type(value) in (str, int, float, bool) for value in detail.values())
            assert all(not isinstance(value, float) or math.isfinite(value) for value in detail.values())


def test_panel_groups_latest_shape_without_expanding_raw_issues_or_tables():
    from recap_panels import render_strategy_qualification
    q = latest_shape()
    before = deepcopy(q)
    soup = BeautifulSoup(render_strategy_qualification(q), "html.parser")
    groups = soup.select(".data-diagnostic")
    assert len(groups) == 4, "one validation cause, one event gap, one research scope, one optional AI service"
    assert {item["data-code"] for item in groups} == {"independent_validation", "event_feed", "research_only", "ai_service"}
    assert "9/11" in soup.get_text()
    assert len(soup.select("table")) == 1, "reuse the existing collapsed inspection table"
    for sid in q["strategy_qualification"]["strategies"]:
        inspection = soup.select_one(f'.strategy-inspection[data-strategy-id="{sid}"]')
        assert inspection is not None and not inspection.has_attr("open")
        assert "validation_schema_version_mismatch" in inspection.get_text()
    assert all(not detail.has_attr("open") for detail in soup.select("details"))
    for detail in list(soup.select("details")):
        detail.decompose()
    visible = soup.get_text(" ", strip=True)
    assert "validation_schema_version_mismatch" not in visible
    assert "影响" in visible and "恢复" in visible
    assert q == before


def test_twelve_validation_issues_per_row_are_one_usable_record_cause():
    rows = [strategy(sid, usable=False, status="unverified")
            for sid in ("mainline_continuation", "intraday_divergence_repair", "selective_mainline_hold")]
    q = quality(*rows)
    result = build(q)
    assert_public(result)
    assert len(result) == 1
    issue = result[0]
    assert issue["code"] == "independent_validation"
    assert set(issue["affected_strategies"]) == {row["strategy_id"] for row in rows}
    assert "可用" in issue["title"] and "独立验证" in issue["title"]
    assert "条件计划" in issue["impact"] and "核心行情" in issue["impact"]
    assert "样本外" in issue["recovery"] and "指纹" in issue["recovery"] and "历史" in issue["recovery"]
    assert all(code in json.dumps(issue["details"]) for code in VALIDATION_ISSUES)
    assert not any(word in issue["title"] + issue["impact"] + issue["recovery"]
                   for word in ("文件不存在", "文件缺失", "文件未找到"))


def test_invalid_existing_validation_is_not_misreported_as_physical_file_absence():
    row = strategy(status="unverified")
    row["validation"].update(status="unverified", issues=["validation_rule_fingerprint_mismatch"])
    row["issues"] = ["validation_rule_fingerprint_mismatch"]
    issue = by_code(quality(row))["independent_validation"]
    assert "fixture://independent-validation" in json.dumps(issue["details"])
    assert "validation_rule_fingerprint_mismatch" in json.dumps(issue["details"])
    assert "文件不存在" not in issue["title"] + issue["impact"] + issue["recovery"]


def test_same_event_gap_and_population_failures_group_across_only_affected_strategies():
    first = strategy("mainline_continuation", status="missing_dependency", deps={name: False for name in EVENTS})
    second = strategy("repair_after_breadth_only", status="missing_dependency", deps={"bomb_rate": False, "reclose_rate": False})
    unaffected = strategy(deps={"board_structure": False})
    for row in (first, second):
        row["issues"] = ["bomb_rate:missing", "reclose_rate:invalid", "bomb_rate:population_unverified",
                         "reclose_rate:population_unverified", "bomb_rate:provenance_unverified"]
    result = build(quality(first, second, unaffected, events=event_assessment(
        scope="closing_limit_pool", complete=False, bomb_rate="missing", reclose_rate="invalid")))
    assert len(result) == 1 and result[0]["code"] == "event_feed"
    issue = result[0]
    assert set(issue["affected_strategies"]) == {first["strategy_id"], second["strategy_id"]}
    assert {d["metric"] for d in issue["details"] if "metric" in d} == {"bomb_rate", "reclose_rate"}
    assert "炸板率" in issue["impact"] and "回封率" in issue["impact"]
    assert "全市场" in issue["recovery"] and "已有" in issue["recovery"]
    assert "核心行情不足" not in issue["impact"]


def test_ready_local_metrics_still_explain_only_the_unverified_market_population():
    row = strategy("mainline_continuation", status="missing_dependency", deps={name: False for name in EVENTS})
    row["issues"] = ["bomb_rate:population_unverified", "reclose_rate:population_unverified"]
    result = build(quality(row, events=event_assessment(scope="closing_limit_pool", complete=False)))
    assert len(result) == 1 and result[0]["code"] == "event_feed"
    issue = result[0]
    assert "总体" in issue["impact"] and "全市场" in issue["recovery"]
    assert {d["status"] for d in issue["details"] if d.get("metric") and "status" in d} == {"ready"}
    assert not any(text in issue["impact"] for text in ("字段未提供", "板型缺失", "封板尝试未提供"))


@pytest.mark.parametrize("source", ["scoped", "quality", "review_readiness"])
def test_metrics_becoming_ready_clear_event_warning_without_hardcoded_bad_module_state(source):
    row = strategy("mainline_continuation", deps={name: False for name in EVENTS})
    q = quality(row)
    q["modules"]["bomb_metrics"]["status"] = "unavailable"  # old aggregate badge is not a metric assessment
    assessment = q["strategy_qualification"].pop("event_qualification")
    if source == "scoped":
        q["strategy_qualification"]["event_qualification"] = assessment
    elif source == "quality":
        q["event_qualification"] = assessment
    else:
        q["review_readiness"] = {"bomb_metrics": {"ready": True, **assessment}}
    assert build(q) == []


def test_candidate_board_readiness_is_not_overridden_by_an_unrelated_global_gap():
    row = strategy(deps={"board_structure": False})
    row["event_qualification"] = event_assessment(scope="closing_limit_pool", complete=False)
    q = quality(row, events=event_assessment(scope="closing_limit_pool", complete=False, board_structure="missing"))
    issue = by_code(q)["event_feed"]
    assert issue["affected_strategies"] == []
    assert q["strategy_qualification"]["strategies"][row["strategy_id"]]["plan_permitted"] is True


def test_research_branches_are_not_missing_core_data_or_unlockable_with_validation():
    q = quality(strategy("breadth_repair", status="research_only", usable=False),
                strategy("high_level_retreat", status="research_only", usable=False))
    result = build(q)
    assert len(result) == 1 and result[0]["code"] == "research_only"
    assert result[0]["status"] == "research_only"
    assert set(result[0]["affected_strategies"]) == {"breadth_repair", "high_level_retreat"}
    assert "不" in result[0]["impact"] and "核心行情" in result[0]["impact"]
    assert "不能" in result[0]["recovery"] and "验证" in result[0]["recovery"]


def test_no_applicable_events_are_not_missing_data_and_do_not_block_tolerant_branch():
    waiting = strategy("intraday_divergence_repair", status="not_applicable", deps={"reclose_rate": False})
    waiting["issues"] = ["reclose_rate:no_events"]
    tolerant = strategy("mainline_continuation", deps={"reclose_rate": True})
    result = build(quality(waiting, tolerant, events=event_assessment(reclose_rate="not_applicable")))
    assert len(result) == 1 and result[0]["code"] == "not_applicable"
    assert result[0]["affected_strategies"] == [waiting["strategy_id"]]
    assert "等待真实事件" in result[0]["recovery"]
    assert "验证" in result[0]["recovery"] and "不能" in result[0]["recovery"]


def test_optional_ai_failure_is_service_narrative_only_not_a_market_data_blocker():
    q = quality(ai=True)
    result = build(q)
    assert len(result) == 1
    issue = result[0]
    assert issue["code"] == "ai_service" and issue["status"] == "nonblocking"
    assert issue["affected_strategies"] == []
    assert "不影响" in issue["impact"] and "独立验证" in issue["impact"]
    assert "服务" in issue["recovery"] and "确定性" in issue["recovery"]
    assert "fixture service timeout" in json.dumps(issue["details"])
    assert q["strategy_qualification"]["strategies"]["selective_mainline_hold"]["plan_permitted"] is True


def test_explicit_core_block_is_preserved_and_ai_is_not_reclassified_optional():
    row = strategy(status="blocked_core")
    row["issues"] = ["ai:critical_blocked"]
    q = quality(row, ai=True)
    q["modules"]["ai"]["critical"] = True
    q["critical_blocked"] = ["ai"]
    q["strategy_qualification"].update(core_ready=False, core_issues=["ai:critical_blocked"], nonblocking_modules=[])
    result = by_code(q)
    assert result["core_market"]["status"] == "blocked_core"
    assert result["core_market"]["affected_strategies"] == [row["strategy_id"]]
    assert "ai_service" not in result or result["ai_service"]["status"] != "nonblocking"


def test_repeated_structural_dependency_failure_is_one_cause_not_a_core_failure():
    rows = [strategy(sid, status="missing_dependency") for sid in ("mainline_continuation", "selective_mainline_hold")]
    for row in rows:
        row["issues"] = ["sector:not_ready", "sector:report_date_missing"]
    result = build(quality(*rows))
    assert len(result) == 1 and result[0]["code"] == "strategy_data"
    assert set(result[0]["affected_strategies"]) == {row["strategy_id"] for row in rows}
    assert result[0]["status"] == "missing_dependency"
    assert "sector:not_ready" in json.dumps(result[0]["details"])


def close_only():
    return {"status": "post_close_plan", "observed_phases": ["close"], "pending_phases": list(INTRADAY),
            "target_trade_date": "2026-09-09", "validation_issues": []}


def test_close_only_means_no_intraday_observations_not_fabricated_elapsed_times():
    confirmation = close_only()
    before = deepcopy(confirmation)
    issue = by_code(quality(), phase_confirmation=confirmation)["intraday_observations"]
    assert issue["status"] == "pending"
    assert "尚无盘中观测" in issue["title"] and "盘后条件计划" in issue["impact"]
    assert "真实" in issue["recovery"] and "时间" in issue["recovery"]
    assert not any(word in json.dumps(issue, ensure_ascii=False) for word in ("已过", "超时", "已完成竞价", "T09:35", "T10:00"))
    assert confirmation == before


def test_panel_accepts_explicit_phase_context_and_keeps_default_call_compatible():
    from recap_panels import render_strategy_qualification
    html = render_strategy_qualification(quality(), phase_confirmation=close_only())
    assert BeautifulSoup(html, "html.parser").select_one('.data-diagnostic[data-code="intraday_observations"]') is not None
    assert "尚无盘中观测" in html
    assert "intraday_observations" not in render_strategy_qualification(quality())


def test_embedded_phase_confirmation_is_used_without_guessing_from_missing_context():
    q = quality()
    q["phase_confirmation"] = close_only()
    assert set(by_code(q)) == {"intraday_observations"}
    assert build(quality(), phase_confirmation={"observed_phases": ["close", "early_0935"], "status": "intraday_observed"}) == []
    assert build(quality()) == []


class NoStringPrivate(dict):
    def __str__(self):
        raise AssertionError("nested private metadata must never be stringified")

    def __repr__(self):
        raise AssertionError("nested private metadata must never be repr-formatted")


MALFORMED_PATHS = [
    ("strategy_qualification", "strategies", "selective_mainline_hold", "validation", field)
    for field in ("source", "evidence_ref", "evaluated_at", "valid_from", "valid_until", "sample_size",
                  "declared_sample_size", "status", "issues", "rule_fingerprint", "rule_version", "strategy_id")
] + [
    ("strategy_qualification", "strategies", "selective_mainline_hold", field)
    for field in ("title", "strategy_id", "status", "issues", "recheck_conditions", "event_dependencies")
] + [
    ("strategy_qualification", "core_issues"), ("strategy_qualification", "nonblocking_modules"),
    ("strategy_qualification", "event_qualification", "population", "source"),
    ("strategy_qualification", "event_qualification", "population", "scope"),
    ("strategy_qualification", "event_qualification", "metrics", "bomb_rate", "status"),
    ("strategy_qualification", "event_qualification", "metrics", "bomb_rate", "reason"),
    ("modules", "ai", "status"), ("modules", "ai", "errors"), ("modules", "bomb_metrics", "errors"),
]


@pytest.mark.parametrize("field_path", MALFORMED_PATHS, ids=lambda fields: ".".join(fields))
@pytest.mark.parametrize("container", ["object", "list"])
def test_malformed_metadata_is_scalar_allowlisted_never_stringified_or_leaked(field_path, container):
    from recap_panels import render_strategy_qualification
    q = latest_shape()
    private = NoStringPrivate(samples=[{"sample_id": "PRIVATE_DIAGNOSTIC_SENTINEL", "net_return": .17}])
    target = q
    for field in field_path[:-1]:
        target = target[field]
    target[field_path[-1]] = private if container == "object" else [private]
    result = build(q)
    assert_public(result)
    assert "PRIVATE_DIAGNOSTIC_SENTINEL" not in json.dumps(result, ensure_ascii=False, allow_nan=False)
    assert "PRIVATE_DIAGNOSTIC_SENTINEL" not in render_strategy_qualification(q)


def test_private_samples_and_unknown_metadata_do_not_enter_any_public_details():
    from recap_panels import render_strategy_qualification
    q = latest_shape()
    row = q["strategy_qualification"]["strategies"]["selective_mainline_hold"]
    row["validation"]["samples"] = [{"sample_id": "PRIVATE_RAW_SAMPLE"}]
    row["validation"]["unexpected"] = {"samples": [{"sample_id": "PRIVATE_EXTRA_SAMPLE"}]}
    q["validation_records"] = [{"samples": [{"sample_id": "PRIVATE_RECORD_SAMPLE"}]}]
    q["strategy_qualification"]["event_qualification"]["metrics"]["bomb_rate"]["value"] = {"samples": ["PRIVATE_METRIC_SAMPLE"]}
    confirmation = close_only()
    confirmation["validation_issues"] = [{"phase": "early_0935", "snapshot_id": {"samples": ["PRIVATE_PHASE_SAMPLE"]},
                                           "issues": ["future_capture", {"samples": ["PRIVATE_PHASE_ISSUE"]}]}]
    result = build(q, phase_confirmation=confirmation)
    assert_public(result)
    assert "PRIVATE_" not in json.dumps(result, ensure_ascii=False)
    assert "PRIVATE_" not in render_strategy_qualification(q, phase_confirmation=confirmation)
    assert "future_capture" in json.dumps(result)


def test_public_text_is_html_escaped_in_grouped_and_strategy_details():
    from recap_panels import render_strategy_qualification
    q = latest_shape()
    row = q["strategy_qualification"]["strategies"]["selective_mainline_hold"]
    row["title"] = "<script>title()</script>"
    row["issues"].append("<script>issue()</script>")
    row["validation"]["source"] = "<script>source()</script>"
    q["modules"]["ai"]["errors"] = ["<script>service()</script>"]
    html = render_strategy_qualification(q)
    assert BeautifulSoup(html, "html.parser").find("script") is None
    assert all(text in html for text in ("&lt;script&gt;title()", "&lt;script&gt;issue()", "&lt;script&gt;source()", "&lt;script&gt;service()"))


def test_no_mutation_and_returned_details_do_not_alias_input():
    from recap_panels import render_strategy_qualification
    q, confirmation = latest_shape(), close_only()
    before_q, before_phase = deepcopy(q), deepcopy(confirmation)
    result = build(q, phase_confirmation=confirmation)
    render_strategy_qualification(q, phase_confirmation=confirmation)
    result[0]["affected_strategies"].append("not-an-input-strategy")
    if result[0]["details"]:
        result[0]["details"][0]["issue"] = "not-an-input-issue"
    assert q == before_q and confirmation == before_phase
    assert "not-an-input-issue" not in json.dumps(build(q, phase_confirmation=confirmation))


def test_healthy_control_has_no_warning_and_preserves_the_existing_qualification():
    from recap_panels import render_strategy_qualification
    q = quality(strategy("mainline_continuation", deps={name: False for name in EVENTS}))
    before = deepcopy(q)
    assert build(q) == []
    soup = BeautifulSoup(render_strategy_qualification(q), "html.parser")
    assert soup.select(".data-diagnostic") == []
    assert "11/11" in soup.get_text() and "条件许可" in soup.get_text()
    assert "未发现需处理的根因" in soup.get_text()
    assert q == before


@pytest.mark.parametrize("bad", [None, [], 0, {"strategy_qualification": []}, {"strategy_qualification": {"schema_version": []}}])
def test_absent_or_malformed_context_does_not_invent_missing_inputs(bad):
    from recap_panels import render_strategy_qualification
    assert build(bad) == []
    assert render_strategy_qualification(bad) == ""


def test_ready_event_fields_need_population_repair_not_a_repeat_of_flag_derivation():
    row = strategy("mainline_continuation", status="missing_dependency", deps={name: False for name in EVENTS})
    row["issues"] = ["bomb_rate:population_unverified", "reclose_rate:population_unverified"]
    issue = by_code(quality(row, events=event_assessment(scope="closing_limit_pool", complete=False)))["event_feed"]
    assert "已就绪" in issue["recovery"]
    assert "解析已有" not in issue["recovery"] and "补采仍未知的观察" not in issue["recovery"]
    assert "全市场" in issue["recovery"]


def test_replay_permission_ceiling_is_not_mislabeled_as_missing_validation_or_all_clear():
    from recap_panels import render_strategy_qualification
    row = strategy(status="unverified")
    row["issues"] = ["not_authorized_in_original_plan"]
    before = deepcopy(row)
    q = quality(row)
    result = by_code(q)
    assert set(result) == {"original_permission"}
    issue = result["original_permission"]
    assert issue["status"] == "unverified" and issue["affected_strategies"] == [row["strategy_id"]]
    assert "原计划" in issue["impact"] and "不自动升级" in issue["recovery"]
    assert "not_authorized_in_original_plan" in json.dumps(issue["details"])
    html = render_strategy_qualification(q)
    assert "未发现需处理的根因" not in html
    assert q["strategy_qualification"]["strategies"][row["strategy_id"]] == before


def test_not_applicable_branch_without_event_evidence_does_not_assert_zero_events():
    issue = by_code(quality(strategy(status="not_applicable")))["not_applicable"]
    assert "当前没有" not in issue["title"]
    assert "适用" in issue["title"]


def resolved_snapshot():
    """Synthetic copy of the parent's documented 93-row metadata shape, not live data."""
    fields = ("first_limit_time", "last_limit_time", "break_count", "limit_up_attempted", "broken", "reclosed",
              "board_type", "limit_up_fund", "turnover_rate", "amount", "float_market_cap")
    derived = {name: 93 for name in ("limit_up_attempted", "broken", "reclosed", "board_type")}
    return {
        "schema_version": 1, "trade_date": "2026-09-08", "row_count": 93,
        "population_scope": "closing_limit_pool", "full_market_coverage": False,
        "market_break_rate": 1.0,  # must never be formatted as a market rate
        "event_metrics": {"reclose_rate": {"successes": 57, "trials": 57, "rate": 1.0}},
        "raw_field_coverage": {name: {"known": 0 if name in derived else 93,
                                      "missing": 93 if name in derived else 0, "total": 93} for name in fields},
        "field_coverage": {name: {"known": 93, "missing": 0, "total": 93} for name in fields},
        "fact_resolution": {"version": "limit-event-facts/v1", "derived_counts": derived, "conflicting_records": 0},
        "provenance": {"source": "fixture_events", "source_timestamp": "2026-09-08T16:00:00+08:00",
                       "source_timestamp_kind": "fetched_at"},
        "records": [{"source": "fixture_events", "source_timestamp": "2026-09-08T16:00:00+08:00",
                     "source_timestamp_kind": "fetched_at", "raw_event_fields": {name: None for name in derived},
                     "derived_event_evidence": {"board_type": {"rule": "same_day_raw_ohlc",
                         "inputs": {"bar": {"samples": ["PRIVATE_OHLC_EVIDENCE"]}}}}}],
        "quality": {"trade_date_mismatches": 0},
    }


def test_resolved_coverage_separates_raw_observations_from_evidenced_rule_interpretation():
    from recap_panels import render_limit_event_coverage
    snapshot = resolved_snapshot()
    before = deepcopy(snapshot)
    html = render_limit_event_coverage(snapshot)
    soup = BeautifulSoup(html, "html.parser")
    assert "原始观测＋有依据的规则解释" in soup.get_text()
    assert "不是新增原始观测" in soup.get_text()
    assert len(soup.select("table")) == 1
    assert not soup.select_one("details").has_attr("open")
    for field in ("limit_up_attempted", "broken", "reclosed", "board_type"):
        row = soup.select_one(f'tr[data-field="{field}"]')
        assert "0/93" in row.select_one('[data-coverage="raw"]').get_text()
        assert "93" in row.select_one('[data-coverage="derived"]').get_text()
        assert "93/93" in row.select_one('[data-coverage="resolved"]').get_text()
    count = soup.select_one('tr[data-field="break_count"]')
    assert "93/93" in count.select_one('[data-coverage="raw"]').get_text()
    assert "closing_limit_pool" in html and "未回封炸板成员" in html and "总体覆盖" in html
    assert "57/57" not in soup.get_text() and "100%" not in soup.get_text()
    assert "PRIVATE_OHLC_EVIDENCE" not in html
    assert snapshot == before


def test_resolved_coverage_without_raw_summary_does_not_label_derived_values_as_original():
    from recap_panels import render_limit_event_coverage
    snapshot = resolved_snapshot()
    snapshot.pop("raw_field_coverage")
    soup = BeautifulSoup(render_limit_event_coverage(snapshot), "html.parser")
    row = soup.select_one('tr[data-field="broken"]')
    raw = row.select_one('[data-coverage="raw"]').get_text()
    assert "未知" in raw and "93/93" not in raw and "0/93" not in raw
    assert "93/93" in row.select_one('[data-coverage="resolved"]').get_text()


def test_legacy_coverage_remains_usable_without_inventing_rule_derivations():
    from recap_panels import render_limit_event_coverage
    snapshot = resolved_snapshot()
    snapshot.pop("fact_resolution")
    snapshot.pop("raw_field_coverage")
    soup = BeautifulSoup(render_limit_event_coverage(snapshot), "html.parser")
    row = soup.select_one('tr[data-field="break_count"]')
    assert "93/93" in row.select_one('[data-coverage="raw"]').get_text()
    assert "93" not in row.select_one('[data-coverage="derived"]').get_text()
    assert "不是全市场炸板率" in soup.get_text()


def test_rule_conflicts_are_visible_without_rewriting_or_displaying_raw_evidence():
    from recap_panels import render_limit_event_coverage
    snapshot = resolved_snapshot()
    snapshot["fact_resolution"]["conflicting_records"] = 2
    before = deepcopy(snapshot)
    html = render_limit_event_coverage(snapshot)
    assert "规则证据冲突" in html and "2 行" in html
    assert "PRIVATE_" not in html and snapshot == before


COVERAGE_MALFORMED_PATHS = [
    ("row_count",), ("trade_date",), ("population_scope",), ("full_market_coverage",),
    ("records",), ("records", 0, "source"), ("records", 0, "source_timestamp"), ("records", 0, "source_timestamp_kind"),
    ("provenance", "source"), ("provenance", "source_timestamp"),
    ("field_coverage", "broken", "known"), ("field_coverage", "broken", "total"),
    ("field_coverage", "broken", "missing"), ("raw_field_coverage", "broken", "known"),
    ("raw_field_coverage", "broken", "total"), ("fact_resolution", "version"),
    ("fact_resolution", "derived_counts", "broken"), ("fact_resolution", "conflicting_records"),
    ("quality", "trade_date_mismatches"),
]


@pytest.mark.parametrize("field_path", COVERAGE_MALFORMED_PATHS, ids=lambda fields: ".".join(map(str, fields)))
def test_coverage_panel_never_stringifies_nested_private_metadata(field_path):
    from recap_panels import render_limit_event_coverage
    snapshot = resolved_snapshot()
    target = snapshot
    for field in field_path[:-1]:
        target = target[field]
    target[field_path[-1]] = NoStringPrivate(samples=["PRIVATE_COVERAGE_SENTINEL"])
    assert "PRIVATE_" not in render_limit_event_coverage(snapshot)


def test_coverage_panel_escapes_scalar_source_and_rule_metadata():
    from recap_panels import render_limit_event_coverage
    snapshot = resolved_snapshot()
    snapshot["fact_resolution"]["version"] = "<script>rule()</script>"
    snapshot["records"][0]["source"] = "<script>source()</script>"
    html = render_limit_event_coverage(snapshot)
    assert BeautifulSoup(html, "html.parser").find("script") is None
    assert "&lt;script&gt;rule()" in html and "&lt;script&gt;source()" in html


def test_current_scoped_metrics_override_old_global_gaps_and_explain_missing_population_members():
    row = strategy("mainline_continuation", status="missing_dependency", deps={name: False for name in EVENTS})
    row["issues"] = ["bomb_rate:population_unverified", "reclose_rate:population_unverified"]
    q = quality(row, events=event_assessment(scope="closing_limit_pool", complete=False))
    old = event_assessment(bomb_rate="missing", reclose_rate="missing", board_structure="missing")
    q["event_qualification"] = old
    q["review_readiness"] = {"bomb_metrics": {"ready": False, "missing": list(EVENTS), **old}}
    q["modules"]["bomb_metrics"].update(status="unavailable", missing_fields=list(EVENTS),
                                        errors=["旧汇总：未收到封板尝试、开板、回封与板型字段"])
    issue = by_code(q)["event_feed"]
    visible = issue["title"] + issue["impact"] + issue["recovery"]
    assert "未回封炸板成员" in visible and "总体覆盖" in visible
    assert "原始炸板次数" in visible and "不" in visible
    assert "观察尚不可用" not in visible and "未收到" not in visible and "板型缺失" not in visible
    assert "解析已有" not in issue["recovery"]
    assert {d["status"] for d in issue["details"] if d.get("metric") and "status" in d} == {"ready"}


def test_unverified_general_population_does_not_guess_which_event_members_are_absent():
    row = strategy("mainline_continuation", status="missing_dependency", deps={name: False for name in EVENTS})
    row["issues"] = ["bomb_rate:population_unverified", "reclose_rate:population_unverified"]
    issue = by_code(quality(row, events=event_assessment(complete=False)))["event_feed"]
    assert "总体覆盖" in issue["impact"] + issue["recovery"]
    assert "未回封炸板成员" not in issue["impact"] + issue["recovery"]


def test_strategy_local_closing_scope_keeps_its_population_gap_despite_healthy_global_scope():
    row = strategy("mainline_continuation", status="missing_dependency", deps={name: False for name in EVENTS})
    row["event_qualification"] = event_assessment(scope="closing_limit_pool", complete=False)
    row["issues"] = ["bomb_rate:population_unverified", "reclose_rate:population_unverified"]
    issue = by_code(quality(row))["event_feed"]
    assert "未回封炸板成员" in issue["impact"] + issue["recovery"]
    assert issue["affected_strategies"] == [row["strategy_id"]]
