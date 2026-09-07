"""Replay can confirm a pinned permission, never create or widen one."""
from copy import deepcopy
import json
import socket

import pytest

import phase_monitor
import report_closure
import strategy_qualification
from decision_dashboard import build_today_decision
from market_snapshot import build_phase_snapshot
from report_closure import build_decision_replay_context
from report_logic import build_market_state
from test_strategy_qualification import record
from test_strategy_qualification_integration import context


STRATEGY = "selective_mainline_hold"
PRIVATE_SAMPLE = "ENVELOPE_PRIVATE_SAMPLE_20260908"



@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("qualification-envelope tests cannot use the network")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


@pytest.fixture
def replay(monkeypatch, tmp_path):
    """Keep the real gate/renderer/posterior; isolate every storage boundary."""
    history = tmp_path / "history.jsonl"
    phases = tmp_path / "phases.jsonl"
    validation = tmp_path / "validation.json"
    state = {}

    def latest_prediction(file, report_date):
        assert file == history
        assert report_date == state["prediction"]["report_date"]
        return deepcopy(state["prediction"])

    def append_snapshot(file, snapshot):
        assert file == phases
        saved = snapshot.to_dict()
        state["snapshots"].append(saved)
        return deepcopy(saved)

    def load_snapshots(file, *, report_date):
        assert file == phases
        assert report_date == state["prediction"]["report_date"]
        return deepcopy(state["snapshots"])

    def load_validation(file):
        assert file == validation
        return {"status": "loaded", "records": deepcopy(state["evidence"])}

    def persist_review(file, decision):
        assert file == history
        state["journal"].append(deepcopy(decision))
        return {"daily_decision": {}, "trade_plan_records": [], "decision_changes": {}}

    monkeypatch.setattr(phase_monitor, "_latest_prediction", latest_prediction)
    monkeypatch.setattr(phase_monitor, "append_phase_snapshot_once", append_snapshot)
    monkeypatch.setattr(phase_monitor, "load_phase_snapshots", load_snapshots)
    monkeypatch.setattr(strategy_qualification, "load_validation_records", load_validation)
    monkeypatch.setattr(report_closure, "persist_decision_review", persist_review)

    def run(ctx, evidence, *, serialized=False, prediction_changes=None):
        before = build_today_decision(ctx)
        # Raw saved contexts exercise phase validation independently of the
        # replay serializer fix; serialized=True covers the end-to-end path.
        saved_context = (build_decision_replay_context(ctx, before)
                         if serialized else deepcopy(ctx))
        prediction = {
            "event_type": "prediction", "prediction_id": "envelope-fixture",
            "report_date": ctx["date_str"], "target_trade_date": ctx["next_trade_date"],
            "scenario_plans": deepcopy(ctx["scenario_plans"]),
            "decision_context": saved_context,
        }
        prediction.update(prediction_changes or {})
        report_date = prediction["report_date"]
        target_date = prediction["target_trade_date"]
        baseline = build_phase_snapshot(
            report_date=report_date, phase="close",
            captured_at=report_date + "T15:00:00+08:00",
            metrics={"breadth_ratio": .4, "promotion_rate": .7, "limit_down": 3},
            source_lineage={"source": "fixture"}, quality={"status": "ok"},
        ).to_dict()
        state.update(prediction=prediction, snapshots=[baseline],
                     evidence=deepcopy(evidence), journal=[])
        result = phase_monitor.record_phase_observation(
            history_path=history, phase_snapshot_path=phases,
            report_date=report_date, trade_date=target_date, phase="early_0935",
            captured_at=target_date + "T09:35:00+08:00",
            metrics={"breadth_ratio": .7, "promotion_rate": .7, "limit_down": 2},
            source_lineage={"source": "fixture"}, quality={"status": "ok"},
            calendar_cache=tmp_path / "calendar.json", validation_path=validation,
        )
        return before, result, deepcopy(state["journal"])

    return run


def assert_denied(result, *, mode=None):
    decision = result["decision"]
    readiness = decision["readiness"]
    assert not readiness["plan_permitted"]
    assert not readiness["execution_ready"]
    assert not decision["execution_allowed"]
    assert decision["priority"]["primary"] is None
    assert decision["priority"]["alternates"] == []
    latest = result["posterior"]["timeline"][-1]
    assert latest["active_scenario_id"] is None
    assert latest["decision_scenario_id"] is None
    if mode is not None:
        assert readiness["publication_mode"] == mode


@pytest.mark.parametrize("has_current_evidence", [False, True])
def test_legacy_counts_never_authorize_phase_replay(replay, has_current_evidence):
    ctx = context()
    quality = ctx["data_quality"]
    quality.pop("strategy_qualification")
    for module in quality["modules"].values():
        module["status"] = "ok"
    quality.update(status="ok", publication_mode="decision", decision_degraded=[],
                   historical_samples=25)
    ctx["market_state"] = build_market_state(quality, historical_samples=25)
    evidence = [record(ctx["scenario_plans"][0])] if has_current_evidence else []
    _, result, _ = replay(ctx, evidence)
    assert_denied(result, mode="observation")


@pytest.mark.parametrize("serialized", [False, True])
@pytest.mark.parametrize("mode", ["observation", "facts_only"])
def test_explicit_original_publication_ceiling_cannot_be_lifted(replay, mode, serialized):
    ctx = context()
    ctx["publication_mode"] = mode
    before, result, _ = replay(ctx, [record(ctx["scenario_plans"][0])], serialized=serialized)
    assert not before["readiness"]["plan_permitted"]
    assert_denied(result, mode=mode)


@pytest.mark.parametrize("serialized", [False, True])
def test_new_evidence_cannot_rebind_original_target_date(replay, serialized):
    ctx = context()
    ctx["next_trade_date"] = "2026-09-07"
    before, result, _ = replay(ctx, [record(ctx["scenario_plans"][0])], serialized=serialized)
    assert not before["readiness"]["plan_permitted"]
    assert_denied(result, mode="observation")


@pytest.mark.parametrize("serialized", [False, True])
def test_matching_new_evidence_cannot_certify_edited_old_rules(replay, serialized):
    ctx = context()
    ctx["scenario_plans"][0]["trigger_rules"]["early_0935"][0]["value"] = .65
    fresh_evidence = record(ctx["scenario_plans"][0])
    before, result, _ = replay(ctx, [fresh_evidence], serialized=serialized)
    assert not before["readiness"]["plan_permitted"]
    assert_denied(result, mode="observation")


@pytest.mark.parametrize("field,value", [
    ("report_date", "2026-09-02"), ("target_trade_date", "2026-09-07"),
])
def test_prediction_dates_cannot_rebind_the_saved_context(replay, field, value):
    ctx = context()
    _, result, _ = replay(ctx, [record(ctx["scenario_plans"][0])],
                          prediction_changes={field: value})
    assert_denied(result)


@pytest.mark.parametrize("serialized", [False, True])
@pytest.mark.parametrize("keys,value", [
    (("core_ready",), False),
    (("publication_mode",), "observation"),
    (("publication_mode",), "facts_only"),
    (("eligible_strategy_ids",), []),
    (("strategies", STRATEGY, "plan_permitted"), False),
    (("strategies", STRATEGY, "status"), "unverified"),
    (("strategies", STRATEGY, "rule_fingerprint"), "old-row-rules"),
    (("strategies", STRATEGY, "rule_version"), "old-row-version"),
    (("strategies", STRATEGY, "validation", "status"), "unverified"),
    (("strategies", STRATEGY, "validation", "rule_version"), "old-validation-version"),
    (("strategies", STRATEGY, "validation", "outcome_definition_id"), "market-thesis/v1"),
])
def test_original_permission_is_more_than_a_strategy_id_allowlist(replay, keys, value, serialized):
    ctx = context()
    node = ctx["data_quality"]["strategy_qualification"]
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value
    _, result, _ = replay(ctx, [record(ctx["scenario_plans"][0])], serialized=serialized)
    assert_denied(result)


@pytest.mark.parametrize("change", ["target_date", "rules", "outcome", "dependency"])
def test_replay_context_persists_renderer_revocation_not_stale_input(change):
    ctx = context()
    original = deepcopy(ctx["data_quality"]["strategy_qualification"])
    if change == "target_date":
        ctx["next_trade_date"] = "2026-09-07"
    elif change == "rules":
        ctx["scenario_plans"][0]["trigger_rules"]["early_0935"][0]["value"] = .65
    elif change == "outcome":
        ctx["data_quality"]["strategy_qualification"]["strategies"][STRATEGY]["validation"]["outcome_definition_id"] = "market-thesis/v1"
    else:
        ctx["data_quality"]["modules"]["sector"]["status"] = "unavailable"
    unchanged_input = deepcopy(ctx)
    decision = build_today_decision(ctx)
    assert not decision["readiness"]["plan_permitted"]
    persisted = build_decision_replay_context(ctx, decision)
    effective = persisted["data_quality"]["strategy_qualification"]
    assert effective["eligible_strategy_ids"] == []
    assert not effective["strategies"][STRATEGY]["plan_permitted"]
    assert effective == decision["strategy_qualification"]
    assert persisted["publication_mode"] == decision["readiness"]["publication_mode"]
    assert persisted["data_quality"]["publication_mode"] == decision["readiness"]["publication_mode"]
    assert original["eligible_strategy_ids"] == [STRATEGY]
    assert ctx == unchanged_input


@pytest.mark.parametrize("serialized", [False, True])
def test_unchanged_valid_envelope_confirms_and_keeps_raw_samples_private(replay, serialized):
    ctx = context()
    unchanged_input = deepcopy(ctx)
    evidence = record(ctx["scenario_plans"][0])
    evidence["samples"][0]["sample_id"] = PRIVATE_SAMPLE
    before, result, journal = replay(ctx, [evidence], serialized=serialized)
    assert before["readiness"]["plan_permitted"]
    assert not before["readiness"]["execution_ready"]
    readiness = result["decision"]["readiness"]
    assert readiness["publication_mode"] == "decision"
    assert readiness["plan_permitted"] and readiness["execution_ready"]
    assert readiness["action"]["reason_code"] == "confirmed_plan"
    assert result["posterior"]["timeline"][-1]["decision_scenario_id"] == STRATEGY
    persisted = build_decision_replay_context(ctx, result["decision"])
    public = json.dumps([result, persisted, journal], ensure_ascii=False)
    assert PRIVATE_SAMPLE not in public
    assert '"samples"' not in public
    assert ctx == unchanged_input


def test_fresh_revocation_is_persisted_and_cannot_be_renewed_by_replay(replay):
    ctx = context()
    evidence = record(ctx["scenario_plans"][0])
    evidence["status"] = "revoked"
    evidence["samples"][0]["sample_id"] = PRIVATE_SAMPLE
    _, result, journal = replay(ctx, [evidence])
    assert_denied(result, mode="observation")
    persisted = build_decision_replay_context(ctx, result["decision"])
    assert persisted["data_quality"]["strategy_qualification"]["eligible_strategy_ids"] == []
    assert PRIVATE_SAMPLE not in json.dumps([result, persisted, journal])
    _, again, _ = replay(persisted, [record(persisted["scenario_plans"][0])])
    assert_denied(again, mode="observation")


@pytest.mark.parametrize("serialized", [False, True])
@pytest.mark.parametrize("source", ["data_quality", "market_state"])
@pytest.mark.parametrize("mode", ["observation", "facts_only"])
def test_original_mode_ceiling_in_saved_gate_layers_survives_replay(replay, source, mode, serialized):
    ctx = context()
    ctx[source]["publication_mode"] = mode
    _, result, _ = replay(ctx, [record(ctx["scenario_plans"][0])], serialized=serialized)
    assert_denied(result, mode=mode)


def test_missing_original_context_keeps_posterior_analytical(replay):
    ctx = context()
    _, result, journal = replay(ctx, [], prediction_changes={"decision_context": None})
    assert "decision" not in result
    assert result["decision_context_status"] == "legacy_context_missing"
    assert result["posterior"]["timeline"][-1]["active_scenario_id"] is None
    assert result["posterior"]["timeline"][-1]["decision_scenario_id"] is None
    assert journal == []


@pytest.mark.parametrize("field", ["source", "evidence_ref", "unexpected_payload"])
def test_replay_context_never_recopies_private_cached_validation_metadata(field):
    ctx = context()
    validation = ctx["data_quality"]["strategy_qualification"]["strategies"][STRATEGY]["validation"]
    validation[field] = {"samples": [{"sample_id": PRIVATE_SAMPLE}]}
    unchanged_input = deepcopy(ctx)
    decision = build_today_decision(ctx)
    assert PRIVATE_SAMPLE not in json.dumps(decision)
    persisted = build_decision_replay_context(ctx, decision)
    assert PRIVATE_SAMPLE not in json.dumps(persisted)
    assert '"samples"' not in json.dumps(persisted)
    assert persisted["data_quality"]["strategy_qualification"] == decision["strategy_qualification"]
    assert ctx == unchanged_input
