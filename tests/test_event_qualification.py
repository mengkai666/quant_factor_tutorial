from copy import deepcopy
import pytest
from report_logic import compute_ladder_metrics


def metrics(broken=False,reclosed=False):
    return compute_ladder_metrics([{"code":"sz000001","height":2,"limit_up_attempted":True,"broken":broken,"reclosed":reclosed,"board_type":"turnover"}])


def test_zero_events_are_not_applicable_not_missing_or_zero_percent():
    from event_qualification import assess_event_metrics
    result=assess_event_metrics(metrics())
    assert result["metrics"]["bomb_rate"]["status"]=="ready"
    assert result["metrics"]["reclose_rate"]["status"]=="not_applicable"
    assert result["metrics"]["reclose_rate"]["value"] is None
    assert result["population"]["complete"] is False


def test_existing_break_without_reclose_observation_is_missing():
    from event_qualification import assess_event_metrics
    assert assess_event_metrics(metrics(True,None))["metrics"]["reclose_rate"]["status"]=="missing"


def test_complete_reclose_evidence_is_ready():
    from event_qualification import assess_event_metrics
    got=assess_event_metrics(metrics(True,True))
    assert got["metrics"]["reclose_rate"]["status"]=="ready"
    assert got["metrics"]["reclose_rate"]["value"]==1.0


def test_missing_flags_cannot_be_turned_into_no_events():
    from event_qualification import assess_event_metrics
    for value in ({},compute_ladder_metrics([]),compute_ladder_metrics([{"code":"sz000001","height":2}])):
        assert assess_event_metrics(value)["metrics"]["reclose_rate"]["status"]=="missing"


@pytest.mark.parametrize("patch", [{"trials":-1},{"successes":2},{"rate":float("nan")},{"rate":True},{"trials":True},{"rate":.5}])
def test_invalid_counts_and_rates_are_never_ready(patch):
    from event_qualification import assess_event_metrics
    m=metrics();m["bomb_rate"].update(patch)
    assert assess_event_metrics(m)["metrics"]["bomb_rate"]["status"]=="invalid"


def test_contradictory_flags_are_invalid():
    from event_qualification import assess_event_metrics
    m=metrics();m["event_input_coverage"]["event_counts"]["inconsistent_rows"]=1
    assert assess_event_metrics(m)["metrics"]["reclose_rate"]["status"]=="invalid"


def test_population_and_input_are_preserved_without_claiming_market_coverage():
    from event_qualification import assess_event_metrics
    m=metrics();m["event_population"]={"scope":"closing_limit_pool","complete":False,"source":"fixture"}
    before=deepcopy(m)
    result=assess_event_metrics(m)
    assert m==before
    assert result["population"]==m["event_population"]


def test_quality_gate_records_no_break_reclose_as_not_applicable():
    from data_sources.quality_gate import apply_review_readiness_gates
    from test_strategy_qualification import quality
    got=apply_review_readiness_gates(quality(),daily_delta={"available":True},ladder_metrics=metrics(),ai_result={"status":"ok"})
    assert got["review_readiness"]["bomb_metrics"]["metrics"]["reclose_rate"]["status"]=="not_applicable"
    assert "reclose_rate" not in got["review_readiness"]["bomb_metrics"]["missing"]


@pytest.mark.parametrize("value", [True, float("nan"), -1, None])
def test_invalid_conflict_count_is_not_silently_zero(value):
    from event_qualification import assess_event_metrics
    m = metrics(True, True)
    m["event_input_coverage"]["event_counts"]["inconsistent_rows"] = value
    result = assess_event_metrics(m)
    assert all(row["status"] == "invalid" for row in result["metrics"].values())


def test_legacy_denominator_is_not_proof_of_reclose_observation():
    from event_qualification import assess_event_metrics
    m = metrics(True, True)
    del m["event_input_coverage"]["event_counts"]
    m["event_input_coverage"]["observed"]["reclosed"] = 0
    assert assess_event_metrics(m)["metrics"]["reclose_rate"]["status"] == "missing"


def test_legacy_complete_flags_can_still_prove_reclose_observation():
    from event_qualification import assess_event_metrics
    m = metrics(True, True)
    del m["event_input_coverage"]["event_counts"]
    assert assess_event_metrics(m)["metrics"]["reclose_rate"]["status"] == "ready"


@pytest.mark.parametrize("change", ["attempts_exceed_rows", "known_exceeds_breaks", "known_exceeds_observed", "reclosed_exceeds_known", "board_understates_observed"])
def test_cross_field_counts_cannot_exceed_their_observed_population(change):
    from event_qualification import assess_event_metrics
    m = metrics(True, True)
    counts = m["event_input_coverage"]["event_counts"]
    if change == "attempts_exceed_rows":
        counts["attempted"] = 2
        m["bomb_rate"].update(trials=2, successes=1, rate=.5)
    elif change == "known_exceeds_breaks":
        counts["reclosed_known_on_broken"] = 2
    elif change == "known_exceeds_observed":
        m["event_input_coverage"]["observed"]["reclosed"] = 0
    elif change == "reclosed_exceeds_known":
        counts["reclosed_known_on_broken"] = 0
    else:
        m["event_input_coverage"]["source_rows"] = 2
        m["event_input_coverage"]["observed"]["board_type"] = 2
    target = "board_structure" if change == "board_understates_observed" else "reclose_rate"
    assert assess_event_metrics(m)["metrics"][target]["status"] == "invalid"


@pytest.mark.parametrize("broken_rows, observed_reclosed", [(1, 3), (2, 2)])
def test_reclose_observations_must_fit_inside_and_outside_broken_rows(broken_rows, observed_reclosed):
    from event_qualification import assess_event_metrics
    m = compute_ladder_metrics([
        {"code": f"sz00000{i + 1}", "height": 2, "limit_up_attempted": True,
         "broken": i < broken_rows, "reclosed": None if i < broken_rows else False,
         "board_type": "turnover"}
        for i in range(3)
    ])
    # No broken row was observed for reclose; the claimed observations cannot
    # all fit in the remaining non-broken rows, even with partial coverage.
    m["event_input_coverage"]["observed"]["reclosed"] = observed_reclosed
    result = assess_event_metrics(m)
    assert all(row["status"] == "invalid" for row in result["metrics"].values())


@pytest.mark.parametrize("broken_reclosed, other_reclosed, expected", [
    (None, None, "missing"),
    (None, False, "missing"),
    (False, None, "ready"),
    (False, False, "ready"),
])
def test_reclose_observation_is_required_only_on_broken_rows(broken_reclosed, other_reclosed, expected):
    from event_qualification import assess_event_metrics
    m = compute_ladder_metrics([
        {"code": "sz000001", "height": 2, "limit_up_attempted": True,
         "broken": True, "reclosed": broken_reclosed, "board_type": "turnover"},
        {"code": "sz000002", "height": 2, "limit_up_attempted": True,
         "broken": False, "reclosed": other_reclosed, "board_type": "turnover"},
    ])
    result = assess_event_metrics(m)
    assert result["metrics"]["bomb_rate"]["status"] == "ready"
    assert result["metrics"]["reclose_rate"]["status"] == expected
    assert result["population"]["complete"] is False
    if expected == "ready":
        assert result["metrics"]["reclose_rate"]["value"] == 0.0
        assert result["metrics"]["reclose_rate"]["trials"] == 1


def _no_attempt_metrics():
    return compute_ladder_metrics([
        {"code": "sz000001", "height": 2, "limit_up_attempted": False,
         "broken": False, "reclosed": None, "board_type": "turnover"},
    ])


@pytest.mark.parametrize("legacy", [False, True])
def test_fully_observed_zero_attempts_still_make_reclose_not_applicable(legacy):
    from event_qualification import assess_event_metrics
    m = _no_attempt_metrics()
    if legacy:
        del m["event_input_coverage"]["event_counts"]
    result = assess_event_metrics(m)
    assert result["metrics"]["bomb_rate"]["status"] == "missing"
    assert result["metrics"]["reclose_rate"]["status"] == "not_applicable"
    assert result["metrics"]["reclose_rate"]["value"] is None
    assert result["metrics"]["reclose_rate"]["trials"] == 0
    assert result["population"]["complete"] is False


def test_zero_bomb_denominator_must_agree_with_fully_observed_attempt_count():
    from event_qualification import assess_event_metrics
    m = _no_attempt_metrics()
    m["event_input_coverage"]["event_counts"]["attempted"] = 1
    result = assess_event_metrics(m)
    assert result["metrics"]["bomb_rate"]["status"] == "invalid"
    assert result["metrics"]["reclose_rate"]["status"] == "missing"
