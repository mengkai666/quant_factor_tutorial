from copy import deepcopy

from test_event_facts import snapshot, bar


def test_archive_with_raw_missing_values_can_be_completed_from_same_day_rows():
    from event_inputs import select_event_observations
    old = snapshot(count=None)
    fresh = snapshot(count=2)
    before = deepcopy(old)
    got = select_event_observations(old, fresh)
    assert got["records"][0]["break_count"] == 2
    assert old == before
    assert got["records"][0]["code"] == old["records"][0]["code"]


def test_empty_or_wrong_day_refresh_does_not_erase_known_archive():
    from event_inputs import select_event_observations
    old = snapshot(count=1)
    empty = {**deepcopy(old), "records": []}
    assert select_event_observations(old, empty) == old
    wrong = snapshot(count=3); wrong["trade_date"]="2026-09-08"
    assert select_event_observations(old, wrong) == old


def test_known_observations_are_not_spliced_with_conflicting_refresh():
    from event_inputs import select_event_observations
    old = snapshot(count=1)
    changed = snapshot(count=2)
    assert select_event_observations(old, changed)["records"][0]["break_count"] == 1


def test_prepare_inputs_connects_real_bars_and_does_not_mutate_observations(tmp_path):
    from event_inputs import prepare_limit_event_facts
    class Provider:
        def fetch_day(self,codes,day,*,cache_dir):
            assert codes == ["sz002702"] and day=="2026-09-07"
            return {"status":"complete","requested":1,"covered":1,"records":[bar()],"errors":[]}
    raw=snapshot(count=2);before=deepcopy(raw)
    result=prepare_limit_event_facts(raw,cache_dir=tmp_path,provider=Provider(),fetch_missing=True)
    assert result["snapshot"]["records"][0]["board_type"]=="one_word"
    assert result["snapshot"]["records"][0]["broken"] is True
    assert raw==before


def test_reference_price_conflict_cannot_supply_a_board_type(tmp_path):
    from event_inputs import prepare_limit_event_facts
    result=prepare_limit_event_facts(snapshot(),cache_dir=tmp_path,price_rows=[bar()],
        reference_prices=[{"code":"sz002702","date":"2026-09-07","close_raw":8.0}])
    assert result["snapshot"]["records"][0]["board_type"] is None
    assert result["collection"]["reference_conflicts"] == ["sz002702"]


def test_missing_bar_source_does_not_discard_the_already_known_event_counts(tmp_path):
    from event_inputs import prepare_limit_event_facts
    class Provider:
        def fetch_day(self,*args,**kwargs):raise RuntimeError("fixture outage")
    got=prepare_limit_event_facts(snapshot(count=0),cache_dir=tmp_path,provider=Provider(),fetch_missing=True)
    assert got["collection"]["status"] == "unavailable"
    assert got["snapshot"]["records"][0]["broken"] is False
    assert got["snapshot"]["records"][0]["board_type"] is None


def test_invalid_provided_bar_is_not_counted_as_successful_collection(tmp_path):
    from event_inputs import prepare_limit_event_facts
    bad = bar(close_raw=None)
    result = prepare_limit_event_facts(snapshot(), cache_dir=tmp_path, price_rows=[bad],
        reference_prices=[{"code":"sz002702","date":"2026-09-07","close_raw":10.0}])
    assert result["collection"]["covered"] == 0
    assert result["snapshot"]["records"][0]["board_type"] is None


def test_connection_tool_writes_resolved_facts_without_rewriting_raw_archive(tmp_path):
    import importlib.util, json
    from pathlib import Path
    tool = Path(__file__).resolve().parents[1] / "tools" / "connect_limit_events.py"
    spec = importlib.util.spec_from_file_location("connect_limit_events_tool", tool)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    archive = tmp_path / "raw"; archive.mkdir()
    raw_path = archive / "2026-09-07.json"
    raw_path.write_text(json.dumps(snapshot(count=1)),encoding="utf-8")
    before = raw_path.read_bytes()
    from data_sources.raw_bar_provider import RawBarProvider
    provider = RawBarProvider(fetcher=lambda *args: [bar()])
    result = module.connect_events("2026-09-07", archive_dir=archive, cache_dir=tmp_path/"bars", output_dir=tmp_path/"out", provider=provider)
    assert raw_path.read_bytes() == before
    saved=json.loads(Path(result["facts_path"]).read_text(encoding="utf-8"))
    assert saved["field_coverage"]["broken"]["known"] == 1
    assert saved["field_coverage"]["board_type"]["known"] == 1
    assert result["full_market_coverage"] is False


def test_audited_membership_mode_can_enrich_compatible_missing_observations():
    from event_inputs import select_event_observations
    audited = snapshot(count=None)
    archived = snapshot(count=2)
    archived["records"][0].update(name="归档别名", limit_count=9)
    archived["records"].append({**deepcopy(archived["records"][0]), "code":"sz000001"})
    before = deepcopy(audited), deepcopy(archived)
    result = select_event_observations(audited, archived, preserve_existing_members=True)
    assert len(result["records"]) == 1
    row = result["records"][0]
    assert row["break_count"] == 2
    assert row["name"] == audited["records"][0]["name"]
    assert row["limit_count"] == 2
    assert (audited, archived) == before
