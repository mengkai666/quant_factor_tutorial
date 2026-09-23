"""Offline regression tests for the bounded, source-timed live collector."""
from __future__ import annotations

import csv
import importlib
import json
import socket
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest

REPORT = "2026-09-07"
TARGET = "2026-09-08"
CODES = ["sh600000", "sz300750", "bj920821"]


@pytest.fixture(autouse=True)
def offline_sockets(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unit tests must not open network sockets")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def api():
    assert importlib.util.find_spec("live_market_inputs") is not None, "live collector not implemented"
    return importlib.import_module("live_market_inputs")


def at(clock="09:35:20", day=TARGET):
    return datetime.fromisoformat(f"{day}T{clock}+08:00")


def wire(code="sh600000", *, stamp="20260908093510", last="11.00", previous="10.00",
         upper="11.00", lower="9.00", raw_code=None, name="样本", **fields):
    values = [""] * 88
    assigned = {0: {"sh": "1", "sz": "51", "bj": "62"}[code[:2]], 1: name,
                2: raw_code or code[2:], 3: last, 4: previous, 5: "10.00", 6: "1234",
                9: last, 10: "200", 19: "0.00", 20: "0", 30: stamp, 31: "1.00",
                32: "10.00", 33: last, 34: "9.99", 37: "234.5", 38: "1.20",
                47: upper, 48: lower}
    assigned.update({int(key): value for key, value in fields.items()})
    for index, value in assigned.items():
        values[index] = value
    return f'v_{code}="' + "~".join(values) + '";'


class Response:
    status_code = 200
    encoding = None

    def __init__(self, text):
        self.text = text


class Client:
    def __init__(self, bodies=None):
        self.bodies = bodies if bodies is not None else {
            CODES[0]: wire(),
            CODES[1]: wire(CODES[1], last="8.00", upper="12.00", lower="8.00", **{"32": "-20.00"}),
            CODES[2]: wire(CODES[2], last="10.00", upper="13.00", lower="7.00", **{"32": "0.00"}),
        }
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response("\n".join(self.bodies.get(code, "") for code in url.split("q=", 1)[1].split(",")))


def prediction():
    return {
        "event_type": "prediction", "prediction_id": "report-7-revision-1",
        "report_date": REPORT, "target_trade_date": TARGET,
        "generated_at": REPORT + "T20:00:00+08:00", "recorded_at": REPORT + "T20:01:00+08:00",
        "market_snapshot": {"report_date": REPORT, "limit_up": 2, "limit_pool_rows": [
            {"code": CODES[0], "height": 2, "trade_date": REPORT, "source": "report_pool", "类型": "ZT", "大主线": "AI"},
            {"code": CODES[2], "height": 1, "trade_date": REPORT, "source": "report_pool", "类型": "ZT", "大主线": "AI"},
        ]},
        "decision_context": {"date_str": REPORT, "next_trade_date": TARGET,
                             "progression_chain": {"rows": [{"code": CODES[1], "previous_height": 8}]},
                             "publication_mode": "observation"},
        "scenario_plans": [],
    }


def universe():
    return {"codes": list(CODES), "trade_date": TARGET, "scope": "cached_hs_bj_a_share_universe"}


def collect(**kwargs):
    return api().collect_live_market_inputs(
        universe=kwargs.pop("universe", universe()), prediction=kwargs.pop("prediction", prediction()),
        client=kwargs.pop("client", Client()), now=kwargs.pop("now", lambda: at()), **kwargs)


def test_parse_preserves_actual_provider_clock_and_raw_prices():
    parsed = api().parse_tencent_quotes(wire(), requested_codes=CODES)
    row = parsed["quotes"][CODES[0]]
    assert row["code"] == "sh600000"
    assert row["name"] == "样本"
    assert row["source_as_of"] == "2026-09-08T09:35:10+08:00"
    assert row["trade_date"] == TARGET
    assert row["raw_fields"]["30"] == "20260908093510"
    assert row["raw_fields"]["3"] == "11.00"
    assert (row["last"], row["previous_close"], row["upper_limit"], row["lower_limit"]) == (11, 10, 11, 9)
    assert row["at_upper_limit"] is True
    assert row["at_lower_limit"] is False
    assert row["price_basis"] == "raw"
    assert row["amount_10k_cny"] == 234.5


@pytest.mark.parametrize("stamp", ["", "093510", "20260908", "20260230093510", "20260908253510"])
def test_dateless_or_invalid_source_time_never_becomes_today(stamp):
    row = api().parse_tencent_quotes(wire(stamp=stamp), requested_codes=CODES)["quotes"][CODES[0]]
    assert row["source_as_of"] is None
    assert row["trade_date"] is None
    assert row["raw_fields"]["30"] == stamp


@pytest.mark.parametrize("sentinel", ["", "-1", "0", "nan"])
def test_missing_limit_prices_remain_unknown_not_ten_percent(sentinel):
    row = api().parse_tencent_quotes(wire(upper=sentinel, lower=sentinel), requested_codes=CODES)["quotes"][CODES[0]]
    assert row["upper_limit"] is None and row["lower_limit"] is None
    assert row["at_upper_limit"] is None and row["at_lower_limit"] is None


@pytest.mark.parametrize("last", ["0", "-1", "nan", "inf"])
def test_invalid_raw_price_does_not_count_as_a_flat(last):
    result = collect(client=Client({CODES[0]: wire(last=last)}), codes=[CODES[0]])
    assert result["coverage"]["fresh_count"] == 0
    assert "flat_count" not in result["metrics"]
    assert not result["record_eligible"]


def test_duplicate_conflict_code_mismatch_and_unrequested_quote_are_rejected():
    body = wire() + wire(last="10.00") + wire(CODES[1], raw_code="600000") + wire("sh600519")
    parsed = api().parse_tencent_quotes(body, requested_codes=CODES)
    assert not parsed["quotes"]
    assert {r["reason"] for r in parsed["rejected"]} >= {"duplicate_symbol", "code_mismatch", "unrequested_symbol"}


def test_cached_universe_filters_non_a_share_and_date_ineligible_members(tmp_path):
    file = tmp_path / "stock_universe.csv"
    with file.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["code", "list_date", "delist_date", "list_status", "updated_at"])
        writer.writeheader()
        for code in CODES + ["sh000001", "sh510300", "hk00700"]:
            writer.writerow({"code": code, "list_date": "2020-01-01", "list_status": "listed"})
        writer.writerow({"code": "sh600519", "list_date": "2026-09-09", "list_status": "listed"})
        writer.writerow({"code": "sz000001", "list_date": "2020-01-01", "delist_date": TARGET, "list_status": "delisted"})
    loaded = api().load_cached_universe(file, trade_date=TARGET)
    assert loaded["codes"] == CODES
    assert loaded["scope"] == "cached_hs_bj_a_share_universe"
    assert len(loaded["excluded"]) == 5
    assert loaded["cache_path"] == str(file.resolve())
    assert loaded["cache_sha256"]


def test_fetch_is_batched_bounded_and_keeps_fetch_time_separate():
    client = Client()
    result = api().fetch_tencent_quotes(CODES, client=client, now=lambda: at(), batch_size=2)
    assert len(client.calls) == 2
    assert client.calls[0][0] == "https://qt.gtimg.cn/q=sh600000,sz300750"
    assert all(c[1]["allow_redirects"] is False and c[1]["timeout"] for c in client.calls)
    assert result["fetched_at"] == "2026-09-08T09:35:20+08:00"
    assert result["quotes"][CODES[0]]["source_as_of"] == "2026-09-08T09:35:10+08:00"
    assert result["quotes"][CODES[0]]["fetched_at"] == result["fetched_at"]


def test_failed_batch_is_not_retried_or_filled_from_old_quotes():
    class Failing(Client):
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            raise TimeoutError("fixture")
    client = Failing()
    result = collect(client=client)
    assert len(client.calls) == 1
    assert result["coverage"]["returned_count"] == 0
    assert result["metrics"] == {}
    assert result["status"] == "unavailable"
    assert result["request_errors"]


def test_full_fresh_collection_matches_main_breadth_and_previous_ladder_denominators():
    before = prediction()
    result = collect(prediction=before)
    assert before == prediction()
    assert result["status"] == "ready"
    assert result["record_eligible"] is True
    assert result["metrics"] == {"up_count": 1, "down_count": 1, "flat_count": 1, "breadth_ratio": .5,
                                  "limit_up": 1, "limit_down": 1, "promotion_rate": .5}
    scope = result["metric_scopes"]["breadth_ratio"]
    assert scope["denominator"] == "up_count + down_count"
    assert scope["denominator_count"] == 2
    assert result["metric_scopes"]["promotion_rate"]["member_codes"] == [CODES[0], CODES[2]]
    assert "mainline_diffusion" not in result["metrics"]
    assert "full_mainline_membership" in result["missing_metrics"]["mainline_diffusion"]
    payload = result["record_payload"]
    assert payload["report_date"] == REPORT and payload["trade_date"] == TARGET
    assert payload["source_lineage"]["prediction_id"] == before["prediction_id"]
    assert payload["source_lineage"]["data_cutoff"] == "2026-09-08T09:35:10+08:00"
    assert payload["captured_at"] == "2026-09-08T09:35:20+08:00"
    from market_snapshot import build_phase_snapshot, snapshot_qualification_issues
    row = build_phase_snapshot(**payload).to_dict()
    assert snapshot_qualification_issues(row, report_date=REPORT, trade_date=TARGET) == []


def test_provider_bounds_handle_st_five_chinext_twenty_and_beijing_thirty_percent():
    bodies = {CODES[0]: wire(last="9.50", upper="10.50", lower="9.50", name="ST样本"),
              CODES[1]: wire(CODES[1], last="9.00", upper="12.00", lower="8.00"),
              CODES[2]: wire(CODES[2], last="7.00", upper="13.00", lower="7.00")}
    result = collect(client=Client(bodies))
    assert result["metrics"]["limit_down"] == 2  # -5%, -30%, NOT the -10% ChiNext quote.
    assert result["metrics"]["limit_up"] == 0
    assert result["metrics"]["promotion_rate"] == 0


def test_partial_limit_coverage_omits_global_limit_count_but_not_valid_breadth():
    client = Client()
    client.bodies[CODES[1]] = wire(CODES[1], last="8.00", upper="", lower="")
    result = collect(client=client)
    assert result["metrics"]["breadth_ratio"] == .5
    assert "limit_down" not in result["metrics"] and "limit_up" not in result["metrics"]
    assert result["sample_metrics"]["limit_down"] == 0
    assert result["missing_metrics"]["limit_down"] == "provider_lower_limit_missing_or_invalid:1/3"
    assert result["metrics"]["promotion_rate"] == .5


def test_all_flat_is_known_zero_counts_not_a_zero_breadth_ratio():
    result = collect(client=Client({c: wire(c, last="10.00", upper="13.00", lower="7.00") for c in CODES}))
    assert (result["metrics"]["up_count"], result["metrics"]["down_count"], result["metrics"]["flat_count"]) == (0, 0, 3)
    assert "breadth_ratio" not in result["metrics"]
    assert result["missing_metrics"]["breadth_ratio"] == "no_advancing_or_declining_quotes"


@pytest.mark.parametrize("stamp,issue", [
    ("20260907093510", "source_date_mismatch"), ("20260908093000", "stale_source_timestamp"),
    ("", "source_timestamp_missing"), ("20260908093600", "future_source_timestamp"),
])
def test_wrong_day_stale_dateless_and_future_quotes_never_make_a_record(stamp, issue):
    result = collect(client=Client({c: wire(c, stamp=stamp) for c in CODES}))
    assert result["metrics"] == {}
    assert result["record_payload"] is None and result["record_eligible"] is False
    assert result["coverage"]["fresh_count"] == 0
    assert issue in result["coverage"]["rejected_codes"][CODES[0]]


def test_missing_response_preserves_population_and_does_not_scale_up_sample():
    result = collect(client=Client({CODES[0]: wire()}))
    assert result["coverage"]["universe_count"] == 3
    assert result["coverage"]["fresh_count"] == 1
    assert result["coverage"]["missing_codes"] == [CODES[1], CODES[2]]
    assert result["metrics"] == {}
    assert result["sample_metrics"]["up_count"] == 1
    assert result["sample_scope"]["scope"] == "fresh_returned_quotes_only"
    assert not result["record_eligible"]


def test_explicit_subset_is_not_relabelled_as_full_market():
    result = collect(codes=[CODES[0]])
    assert result["coverage"]["requested_count"] == 1 and result["coverage"]["universe_count"] == 3
    assert "breadth_ratio" not in result["metrics"] and "limit_down" not in result["metrics"]
    assert result["sample_metrics"]["breadth_ratio"] == 1
    assert not result["record_eligible"]


@pytest.mark.parametrize("change", ["missing", "empty", "wrong_day", "wrong_count", "missing_member_limit"])
def test_promotion_needs_actual_report_day_members_and_all_their_bounds(change):
    pred, client = prediction(), Client()
    if change == "missing":
        pred["market_snapshot"].pop("limit_pool_rows")
    elif change == "empty":
        pred["market_snapshot"].update(limit_pool_rows=[], limit_up=0)
    elif change == "wrong_day":
        pred["market_snapshot"]["limit_pool_rows"][0]["trade_date"] = "2026-09-04"
    elif change == "wrong_count":
        pred["market_snapshot"]["limit_up"] = 3
    else:
        client.bodies[CODES[0]] = wire(upper="")
    result = collect(prediction=pred, client=client)
    assert "promotion_rate" not in result["metrics"]
    assert result["missing_metrics"]["promotion_rate"]
    assert result["metrics"]["breadth_ratio"] == .5


@pytest.mark.parametrize("clock,stamp,phase", [
    ("09:25:20", "20260908092510", "auction"), ("09:35:20", "20260908093510", "early_0935"),
    ("10:00:20", "20260908100010", "confirm_1000"), ("13:01:20", "20260908130110", "afternoon"),
])
def test_each_actual_allowed_window_has_a_reachable_record_payload(clock, stamp, phase):
    client = Client({c: wire(c, stamp=stamp) for c in CODES})
    result = collect(client=client, now=lambda: at(clock))
    assert result["record_eligible"] is True
    assert result["phase"] == phase
    assert result["record_payload"]["captured_at"] == at(clock).isoformat()


@pytest.mark.parametrize("clock", ["09:24:59", "09:30:00", "09:45:00", "10:15:01", "12:00:00", "15:00:00", "20:00:00"])
def test_outside_window_cannot_be_backdated_to_requested_morning_phase(clock):
    result = collect(now=lambda: at(clock), phase="early_0935")
    assert result["status"] == "outside_window"
    assert result["record_payload"] is None and not result["record_eligible"]


def test_fresh_quote_from_before_window_does_not_become_that_window():
    result = collect(client=Client({c: wire(c, stamp="20260908093459") for c in CODES}))
    assert not result["record_eligible"]
    assert "source_outside_phase_window" in result["issues"]


@pytest.mark.parametrize("change", ["report", "target", "prediction_id", "context", "late_publication", "missing_publication"])
def test_binding_failures_never_record(change):
    pred, options = prediction(), {}
    if change == "report": options["report_date"] = "2026-09-04"
    elif change == "target": options["trade_date"] = "2026-09-09"
    elif change == "prediction_id": options["prediction_id"] = "old-revision"
    elif change == "context": pred["decision_context"]["next_trade_date"] = "2026-09-09"
    elif change == "late_publication": pred["recorded_at"] = TARGET + "T11:20:32+08:00"
    else:
        pred.pop("generated_at")
        pred.pop("recorded_at")
    result = collect(prediction=pred, **options)
    assert not result["record_eligible"]
    assert result["binding"]["issues"]


def test_clock_crossing_window_during_request_is_not_a_morning_snapshot():
    current = [at("09:44:50")]
    class Late(Client):
        def get(self, url, **kwargs):
            response = super().get(url, **kwargs)
            current[0] = at("09:45:01")
            return response
    result = collect(client=Late(), now=lambda: current[0])
    assert not result["record_eligible"]
    assert result["status"] == "outside_window"


def cli_module():
    file = Path(__file__).resolve().parents[1] / "tools" / "collect_market_phase.py"
    assert file.exists(), "collect CLI not implemented"
    spec = importlib.util.spec_from_file_location("collect_market_phase_test", file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cli_files(tmp_path):
    data = tmp_path / "input"
    data.mkdir()
    history = data / "report_prediction_history.jsonl"
    history.write_text(json.dumps(prediction(), ensure_ascii=False) + "\n", encoding="utf-8")
    cache = data / "stock_universe.csv"
    cache.write_text("code,list_date,list_status\n" + "".join(f"{c},2020-01-01,listed\n" for c in CODES), encoding="utf-8")
    phases = tmp_path / "recorded" / "phases.jsonl"
    output = tmp_path / "artifacts"
    args = ["--history", str(history), "--universe-cache", str(cache), "--output-dir", str(output),
            "--phase-history", str(phases), "--calendar-cache", str(data / "calendar.csv"),
            "--validation-file", str(data / "validation.json")]
    return args, history, cache, phases, output


def never_record(**kwargs):
    raise AssertionError("Recorder must not be invoked by this case")


def only_artifact(output):
    paths = list(output.glob("market-inputs-*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


def test_cli_preview_acquires_real_client_inputs_and_writes_only_explicit_output(tmp_path, capsys):
    args, history, cache, phases, output = cli_files(tmp_path)
    before = {p: p.read_bytes() for p in (history, cache)}
    client = Client()
    code = cli_module().main(args, client=client, now=lambda: at(), record_call=never_record)
    assert code == 0 and client.calls
    result = only_artifact(output)
    assert result["metrics"]["breadth_ratio"] == .5
    assert result["recording"]["status"] == "not_requested"
    assert not phases.exists()
    assert all(p.read_bytes() == content for p, content in before.items())
    summary = json.loads(capsys.readouterr().out)
    assert Path(summary["artifact_path"]).parent == output


def test_cli_explicit_record_uses_bound_fresh_payload_and_injected_storage(tmp_path, capsys):
    from market_snapshot import append_phase_snapshot_once, build_phase_snapshot
    args, history, cache, phases, output = cli_files(tmp_path)
    before = history.read_bytes()
    calls = []

    def recorder(**kwargs):
        calls.append(deepcopy(kwargs))
        snapshot = build_phase_snapshot(**{key: value for key, value in kwargs.items()
                                          if key not in {"history_path", "phase_snapshot_path", "calendar_cache", "validation_path"}})
        return {"snapshot": append_phase_snapshot_once(kwargs["phase_snapshot_path"], snapshot),
                "prediction_id": kwargs["source_lineage"]["prediction_id"]}

    code = cli_module().main(args + ["--record"], client=Client(), now=lambda: at(), record_call=recorder)
    assert code == 0 and len(calls) == 1
    assert Path(calls[0]["history_path"]) == history
    assert Path(calls[0]["phase_snapshot_path"]) == phases
    assert Path(calls[0]["calendar_cache"]).is_relative_to(tmp_path)
    assert Path(calls[0]["validation_path"]).is_relative_to(tmp_path)
    saved = json.loads(phases.read_text(encoding="utf-8"))
    assert saved["trade_date"] == TARGET and saved["report_date"] == REPORT
    assert saved["source_lineage"]["prediction_id"] == "report-7-revision-1"
    assert saved["metrics"]["promotion_rate"] == .5
    assert saved["quality"]["status"] == "ok"
    assert only_artifact(output)["recording"]["status"] == "recorded"
    assert history.read_bytes() == before


def test_cli_after_close_keeps_actual_late_quotes_but_never_backdates(tmp_path, capsys):
    args, history, cache, phases, output = cli_files(tmp_path)
    client = Client({c: wire(c, stamp="20260908161431") for c in CODES})
    code = cli_module().main(args + ["--record", "--phase", "early_0935"], client=client,
                             now=lambda: at("20:00:00"), record_call=never_record)
    result = only_artifact(output)
    assert code == 2 and result["status"] == "outside_window"
    assert result["source_as_of"] == "2026-09-08T16:14:31+08:00"
    assert result["fetched_at"] == "2026-09-08T20:00:00+08:00"
    assert result["quality"]["freshness_level"] == "stale"
    assert result["recording"]["status"] == "blocked" and not phases.exists()


def test_cli_rechecks_latest_prediction_revision_immediately_before_record(tmp_path, capsys):
    args, history, cache, phases, output = cli_files(tmp_path)
    revisions = [prediction(), {**prediction(), "prediction_id": "new-revision"}]
    reads = []

    def loader(file, *, report_date=None):
        reads.append((Path(file), report_date))
        return revisions.pop(0)

    code = cli_module().main(args + ["--record"], client=Client(), now=lambda: at(),
                             history_loader=loader, record_call=never_record)
    result = only_artifact(output)
    assert code == 2 and len(reads) == 2
    assert reads[-1] == (history, REPORT)
    assert "prediction_changed_before_record" in result["issues"]
    assert result["recording"]["status"] == "blocked" and not phases.exists()


@pytest.mark.parametrize("clock,status", [("09:45:01", "outside_window"), ("09:38:00", "stale")])
def test_cli_rechecks_clock_and_freshness_at_record_not_just_fetch(tmp_path, capsys, clock, status):
    args, history, cache, phases, output = cli_files(tmp_path)
    current, reads = [at()], []

    def loader(file, *, report_date=None):
        reads.append(file)
        if len(reads) == 2:
            current[0] = at(clock)
        return prediction()

    code = cli_module().main(args + ["--record"], client=Client(), now=lambda: current[0],
                             history_loader=loader, record_call=never_record)
    result = only_artifact(output)
    assert code == 2 and result["status"] == status
    assert not phases.exists() and result["recording"]["status"] == "blocked"


def test_history_loader_uses_newest_report_and_last_revision_not_appended_old_report(tmp_path):
    file = tmp_path / "history.jsonl"
    first = prediction()
    revision = {**first, "prediction_id": "newest-revision"}
    older = {**first, "report_date": "2026-09-04", "prediction_id": "appended-old-report"}
    file.write_text("\n".join([json.dumps(first), "not json", json.dumps(revision),
                               json.dumps({"event_type": "trade_plan"}), json.dumps(older)]), encoding="utf-8")
    assert cli_module().load_latest_prediction(file)["prediction_id"] == "newest-revision"
    assert cli_module().load_latest_prediction(file, report_date="2026-09-04")["prediction_id"] == "appended-old-report"


def test_latest_late_published_revision_does_not_fall_back_to_earlier_green_one(tmp_path, capsys):
    args, history, cache, phases, output = cli_files(tmp_path)
    late = prediction()
    late.update(prediction_id="late-rebuild", generated_at=TARGET + "T11:20:00+08:00",
                recorded_at=TARGET + "T11:20:10+08:00")
    with history.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(late) + "\n")
    code = cli_module().main(args + ["--record"], client=Client(), now=lambda: at(), record_call=never_record)
    result = only_artifact(output)
    assert code == 2 and not phases.exists()
    assert result["binding"]["prediction_id"] == "late-rebuild"
    assert "prediction_published_after_observation" in result["binding"]["issues"]


def test_repeated_preview_uses_unique_artifacts_without_overwriting(tmp_path, capsys):
    args, history, cache, phases, output = cli_files(tmp_path)
    module = cli_module()
    for _ in range(2):
        assert module.main(args, client=Client(), now=lambda: at(), record_call=never_record) == 0
    assert len(list(output.glob("market-inputs-*.json"))) == 2
    assert not phases.exists()


def test_cli_rejects_artifact_directory_inside_read_only_input_cache(tmp_path):
    args, history, cache, phases, output = cli_files(tmp_path)
    args[args.index("--output-dir") + 1] = str(history.parent)
    before = history.read_bytes()
    with pytest.raises(SystemExit) as error:
        cli_module().main(args, client=Client(), now=lambda: at(), record_call=never_record)
    assert error.value.code == 2
    assert history.read_bytes() == before and not phases.exists()
    assert not list(history.parent.glob("market-inputs-*.json"))


def test_cli_cannot_alias_phase_storage_to_prediction_history(tmp_path):
    args, history, cache, phases, output = cli_files(tmp_path)
    args[args.index("--phase-history") + 1] = str(history)
    before = history.read_bytes()
    with pytest.raises(SystemExit) as error:
        cli_module().main(args + ["--record"], client=Client(), now=lambda: at(), record_call=never_record)
    assert error.value.code == 2 and history.read_bytes() == before


def test_cli_has_no_user_supplied_clock_or_timestamp_backfill_switch(tmp_path):
    args, history, cache, phases, output = cli_files(tmp_path)
    with pytest.raises(SystemExit) as error:
        cli_module().main(args + ["--captured-at", TARGET + "T09:35:00+08:00"],
                          client=Client(), now=lambda: at(), record_call=never_record)
    assert error.value.code == 2 and not output.exists() and not phases.exists()


def test_equal_provider_bounds_do_not_make_a_flat_both_limit_up_and_limit_down():
    result = collect(client=Client({c: wire(c, last="10.00", upper="10.00", lower="10.00") for c in CODES}))
    assert result["metrics"]["flat_count"] == 3
    assert "limit_up" not in result["metrics"] and "limit_down" not in result["metrics"]
    assert "promotion_rate" not in result["metrics"]


def test_clock_rollback_cannot_accept_quote_later_than_final_collection_time():
    moments = iter([at("09:35:00"), at("09:35:20"), at("09:35:05")])
    result = collect(now=lambda: next(moments))
    assert not result["record_eligible"]
    assert result["coverage"]["fresh_count"] == 0
    assert "future_source_timestamp" in result["coverage"]["rejected_codes"][CODES[0]]


def test_offline_socket_assertions_are_not_swallowed_as_ordinary_provider_failures():
    class AccidentalNetwork(Client):
        def get(self, url, **kwargs):
            socket.create_connection(("example.invalid", 80))
    with pytest.raises(AssertionError, match="network sockets"):
        collect(client=AccidentalNetwork())


def test_default_recorder_import_latency_cannot_cross_the_record_window(tmp_path, monkeypatch, capsys):
    import sys
    from types import ModuleType
    args, history, cache, phases, output = cli_files(tmp_path)
    current = [at("09:44:50")]
    module = ModuleType("phase_monitor")

    def late_import(name):
        if name == "record_phase_observation":
            current[0] = at("09:45:01")
            return never_record
        raise AttributeError(name)

    module.__getattr__ = late_import
    monkeypatch.setitem(sys.modules, "phase_monitor", module)
    client = Client({c: wire(c, stamp="20260908094440") for c in CODES})
    code = cli_module().main(args + ["--record"], client=client, now=lambda: current[0])
    result = only_artifact(output)
    assert code == 2 and result["status"] == "outside_window"
    assert result["recording"]["status"] == "blocked" and not phases.exists()



def final_decision(permitted=False):
    return {
        "report_date": REPORT, "default_action": "按已确认条件计划执行" if permitted else "不开新仓，只观察条件是否成立",
        "mainline": "最终观察主线", "position": "2 成" if permitted else "0 成", "execution_allowed": permitted,
        "readiness": {
            "report_date": REPORT, "plan_permitted": permitted, "execution_ready": permitted,
            "data": {"status": "ready", "label": "核心行情已就位"},
            "strategy": {"status": "eligible" if permitted else "unverified", "label": "条件许可" if permitted else "策略资格已撤销"},
            "signal": {"status": "confirmed" if permitted else "not_evaluable", "label": "真实阶段已确认" if permitted else "不可用于执行确认", "phase": "early_0935"},
            "action": {"status": "enter_plan" if permitted else "no_new_positions", "label": "进入条件计划" if permitted else "不开新仓",
                       "reason": "最终回放满足条件" if permitted else "独立验证记录已撤销，不能沿用旧许可"},
            "recheck_conditions": ["取得有效独立验证后在真实允许阶段重新评估"],
        },
        "action_plan": {"groups": [], "plan_permitted": permitted, "execution_ready": permitted},
        "strategy_qualification": {"schema_version": "strategy-qualification-set/v1", "strategies": {
            "final_strategy": {"strategy_id": "final_strategy", "title": "最终回放策略", "status": "eligible" if permitted else "unverified",
                               "plan_permitted": permitted, "issues": [] if permitted else ["validation_revoked_after_publication"]},
        }},
    }


@pytest.mark.parametrize("clock,stamp,fresh", [("09:35:20", "20260908093510", 3), ("20:00:00", "20260908161431", 0)])
def test_collect_only_always_delivers_readable_html_with_actual_times_and_coverage(tmp_path, capsys, clock, stamp, fresh):
    args, history, cache, phases, output = cli_files(tmp_path)
    client = Client({c: wire(c, stamp=stamp) for c in CODES})
    assert cli_module().main(args, client=client, now=lambda: at(clock), record_call=never_record) == 0
    summary = json.loads(capsys.readouterr().out)
    assert "summary_path" in summary, "CLI must deliver a readable summary, not just JSON"
    html_path = Path(summary["summary_path"])
    assert html_path.parent == output and html_path.suffix == ".html"
    text = html_path.read_text(encoding="utf-8")
    result = only_artifact(output)
    assert result["summary_path"] == str(html_path)
    assert "仅采集" in text and "未指定 --record" in text
    assert result["fetched_at"] in text and result["source_as_of"] in text
    assert f"同日新鲜：{fresh} / 3" in text and "返回：3 / 3" in text
    assert not phases.exists()
    if not fresh:
        assert "时窗外" in text and "过期" in text and "不回填" in text


def test_recorded_summary_uses_only_final_revoked_decision_and_never_rebuilds_old_context(tmp_path, monkeypatch, capsys):
    import decision_dashboard
    import decision_readiness
    import strategy_qualification
    args, history, cache, phases, output = cli_files(tmp_path)
    original = prediction()
    original["decision_context"].update(default_action="旧许可立即买入", execution_allowed=True,
                                        readiness={"execution_ready": True, "plan_permitted": True})
    final = final_decision()
    before = deepcopy(final)

    def forbid_rebuild(*args, **kwargs):
        raise AssertionError("Summary must not recompute qualification from old context")

    monkeypatch.setattr(decision_dashboard, "build_today_decision", forbid_rebuild)
    monkeypatch.setattr(decision_readiness, "build_decision_readiness", forbid_rebuild)
    monkeypatch.setattr(strategy_qualification, "qualify_strategies", forbid_rebuild)
    monkeypatch.setattr(strategy_qualification, "refresh_strategy_qualification", forbid_rebuild)

    def recorder(**kwargs):
        return {"snapshot": {"snapshot_id": "final-phase", "appended": True}, "decision": final,
                "prediction_id": original["prediction_id"]}

    assert cli_module().main(args + ["--record"], client=Client(), now=lambda: at(),
                             history_loader=lambda *a, **k: original, record_call=recorder) == 0
    result = only_artifact(output)
    assert result.get("decision") == final, "Keep the recorder's final decision, not the original context"
    text = Path(result["summary_path"]).read_text(encoding="utf-8")
    assert "阶段已回写" in text and "不开新仓" in text
    assert "独立验证记录已撤销，不能沿用旧许可" in text
    assert "validation_revoked_after_publication" in text
    assert "旧许可立即买入" not in text
    assert 'data-action-status="no_new_positions"' in text
    assert final == before and original["decision_context"]["execution_allowed"] is True


def test_recorded_summary_can_show_the_final_allowed_action_without_creating_a_new_prediction(tmp_path, capsys):
    args, history, cache, phases, output = cli_files(tmp_path)
    original_bytes = history.read_bytes()
    final = final_decision(True)
    assert cli_module().main(args + ["--record"], client=Client(), now=lambda: at(), record_call=lambda **kw: {
        "snapshot": {"snapshot_id": "allowed-phase", "appended": True}, "decision": final,
        "prediction_id": "report-7-revision-1",
    }) == 0
    result = only_artifact(output)
    assert result.get("decision") == final
    text = Path(result["summary_path"]).read_text(encoding="utf-8")
    assert "按已确认条件计划执行" in text and "最终回放满足条件" in text
    assert 'data-action-status="enter_plan"' in text
    assert "不代表委托或成交" in text
    assert result["binding"]["prediction_id"] == "report-7-revision-1"
    assert history.read_bytes() == original_bytes


def test_record_without_final_decision_never_falls_back_to_prediction_context(tmp_path, capsys):
    args, history, cache, phases, output = cli_files(tmp_path)
    old = prediction()
    old["decision_context"]["default_action"] = "旧许可立即买入"
    assert cli_module().main(args + ["--record"], client=Client(), now=lambda: at(),
                             history_loader=lambda *a, **k: old, record_call=lambda **kw: {
                                 "snapshot": {"snapshot_id": "legacy-phase", "appended": True},
                                 "prediction_id": old["prediction_id"], "decision_context_status": "legacy_context_missing",
                             }) == 0
    result = only_artifact(output)
    assert "summary_path" in result
    text = Path(result["summary_path"]).read_text(encoding="utf-8")
    assert "未返回最终决策" in text and "仅展示事实" in text
    assert "旧许可立即买入" not in text and "进入条件计划" not in text


def test_record_failure_summary_is_explicit_about_possible_partial_write(tmp_path, capsys):
    args, history, cache, phases, output = cli_files(tmp_path)

    def failed(**kwargs):
        raise OSError("test partial recorder failure")

    assert cli_module().main(args + ["--record"], client=Client(), now=lambda: at(), record_call=failed) == 3
    result = only_artifact(output)
    assert "summary_path" in result
    text = Path(result["summary_path"]).read_text(encoding="utf-8")
    assert "可能已部分写入" in text and "不自动重试" in text
    assert "阶段已回写" not in text


def test_summary_renderer_is_read_only_and_escapes_untrusted_text():
    result = collect()
    result["recording"] = {"status": "recorded", "recorded": True}
    result["decision"] = final_decision()
    result["binding"]["prediction_id"] = '<script>alert("id")</script>'
    result["decision"]["mainline"] = '<img src=x onerror="alert(1)">'
    result["decision"]["readiness"]["action"]["reason"] = '<script>alert("reason")</script>'
    before = deepcopy(result)
    module = cli_module()
    assert hasattr(module, "render_collection_summary"), "Need a pure final-result renderer"
    html = module.render_collection_summary(result)
    assert "<script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html and "&lt;img" in html
    assert result == before
