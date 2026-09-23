from copy import deepcopy
import importlib.util
import json
from pathlib import Path


def test_preview_preserves_source_audit_and_official_history(tmp_path):
    tool_path = Path(__file__).resolve().parents[1] / "tools" / "preview_recap_report.py"
    spec = importlib.util.spec_from_file_location("preview_recap_tool", tool_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = {"report_date": "2026-09-03", "publication_mode": "facts_only", "quality": {"status": "blocked", "publication_mode": "facts_only"}, "facts": {"market_snapshot": {"report_date": "2026-09-03", "limit_pool_rows": []}}}
    audit = tmp_path / "source_audit.json"
    audit.write_text(json.dumps({"context": context}), encoding="utf-8")
    history = tmp_path / "official_history.jsonl"
    history.write_text(json.dumps({"event_type": "prediction", "prediction_id": "old", "report_date": "2026-09-02"}) + "\n", encoding="utf-8")
    calendar = tmp_path / "calendar.csv"
    calendar.write_text("trade_date\n2026-09-03\n2026-09-04\n", encoding="utf-8")
    before_audit, before_history = audit.read_bytes(), history.read_bytes()
    result = module.build_preview(audit, tmp_path / "preview", calendar_cache=calendar, history_path=history)
    assert result["target_trade_date"] == "2026-09-04"
    # The preview adds an unverified authorization summary, without changing
    # any original core inputs or the facts-only ceiling.
    assert result["core_modules_unchanged"]
    assert result["source_publication_mode"] == "facts_only"
    assert result["strategy_qualification"]["eligible_strategy_ids"] == []
    assert result["publication_mode"] == "facts_only"
    assert not result["readiness"]["execution_ready"]
    assert Path(result["preview"]).exists()
    assert "离线重算预览" in Path(result["preview"]).read_text(encoding="utf-8")
    assert audit.read_bytes() == before_audit
    assert history.read_bytes() == before_history
    module.build_preview(audit, tmp_path / "preview", calendar_cache=calendar, history_path=history)
    events = (tmp_path / "preview" / "preview_journal_2026-09-03.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) == 2


def test_preview_rebuilds_stale_event_metrics_from_offline_raw_ohlc(tmp_path, monkeypatch):
    """A cached zero-coverage metric must not hide available same-day facts."""
    from bs4 import BeautifulSoup
    from data_sources.raw_bar_provider import RawBarProvider
    from report_logic import compute_ladder_metrics
    from test_event_facts import snapshot, bar

    tool = Path(__file__).resolve().parents[1] / "tools" / "preview_recap_report.py"
    spec = importlib.util.spec_from_file_location("preview_connected_events", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archives, bars = tmp_path / "events", tmp_path / "bars"
    archives.mkdir()
    bars.mkdir()
    raw = snapshot(count=0)
    event_path = archives / "2026-09-07.json"
    event_path.write_text(json.dumps(raw), encoding="utf-8")
    bar_path = bars / "2026-09-07.json"
    bar_path.write_text(json.dumps({"schema_version": "raw-daily-bars/v1",
        "trade_date": "2026-09-07", "records": [bar()]}), encoding="utf-8")
    monkeypatch.setattr(module, "LIMIT_EVENT_SNAPSHOT_DIR", archives)
    monkeypatch.setattr(module, "RAW_BAR_CACHE_DIR", bars, raising=False)

    def no_market_requests(*args, **kwargs):
        raise AssertionError("offline preview must not fetch market data")

    monkeypatch.setattr(RawBarProvider, "fetch_day", no_market_requests)
    context = {"report_date": "2026-09-07", "publication_mode": "facts_only",
        "quality": {"status": "blocked", "publication_mode": "facts_only"},
        "facts": {"market_snapshot": {"report_date": "2026-09-07",
            "limit_pool_rows": raw["records"]}, "limit_event_snapshot": raw,
            "strategy_event_metrics": compute_ladder_metrics(raw["records"])}}
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"context": context}), encoding="utf-8")
    calendar = tmp_path / "calendar.csv"
    calendar.write_text("trade_date\n2026-09-07\n2026-09-08\n", encoding="utf-8")
    originals = {path: path.read_bytes() for path in (audit, event_path, bar_path)}

    result = module.build_preview(audit, tmp_path / "out", calendar_cache=calendar)

    assessment = result["strategy_qualification"]["event_qualification"]
    assert assessment["metrics"]["board_structure"]["status"] == "ready"
    assert assessment["metrics"]["board_structure"]["value"] == {"one_word": 1, "turnover": 0}
    assert assessment["population"]["complete"] is False
    for filename in ("recap_2026-09-07.html", "dashboard_2026-09-07.html", "embedded_2026-09-07.html"):
        soup = BeautifulSoup((tmp_path / "out" / filename).read_text(encoding="utf-8"), "html.parser")
        board = soup.select_one('tr[data-field="board_type"]')
        assert board is not None
        assert "0/1" in board.select_one('[data-coverage="raw"]').get_text()
        assert "1/1" in board.select_one('[data-coverage="resolved"]').get_text()
    assert result["core_modules_unchanged"]
    assert result["publication_mode"] == "facts_only"
    assert not result["readiness"]["execution_ready"]
    assert result["candidate_codes"] == []
    assert all(path.read_bytes() == before for path, before in originals.items())


def test_preview_preserves_embedded_fact_evidence_and_audit_membership(tmp_path, monkeypatch):
    """An older archive must not erase a self-contained audited fact proof."""
    from event_facts import resolve_limit_event_facts
    from test_event_facts import snapshot, bar

    tool = Path(__file__).resolve().parents[1] / "tools" / "preview_recap_report.py"
    spec = importlib.util.spec_from_file_location("preview_retained_proof", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archives = tmp_path / "events"
    archives.mkdir()
    raw = snapshot(count=0)
    resolved = resolve_limit_event_facts(raw, price_rows=[bar()])
    # A later/other capture may have extra members; it is not the audited pool.
    archived = deepcopy(raw)
    archived["records"].append({**deepcopy(raw["records"][0]), "code": "sz000001", "name": "其他批次成员"})
    archived["row_count"] = 2
    archive_path = archives / "2026-09-07.json"
    archive_path.write_text(json.dumps(archived), encoding="utf-8")
    monkeypatch.setattr(module, "LIMIT_EVENT_SNAPSHOT_DIR", archives)
    monkeypatch.setattr(module, "RAW_BAR_CACHE_DIR", tmp_path / "no-bars", raising=False)
    context = {"report_date": "2026-09-07", "publication_mode": "facts_only",
        "quality": {"status": "blocked", "publication_mode": "facts_only"},
        "facts": {"market_snapshot": {"report_date": "2026-09-07", "limit_pool_rows": raw["records"]},
            "limit_event_snapshot": resolved}}
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"context": context}), encoding="utf-8")
    calendar = tmp_path / "calendar.csv"
    calendar.write_text("trade_date\n2026-09-07\n2026-09-08\n", encoding="utf-8")
    before = audit.read_bytes(), archive_path.read_bytes()

    result = module.build_preview(audit, tmp_path / "out", calendar_cache=calendar)

    board = result["strategy_qualification"]["event_qualification"]["metrics"]["board_structure"]
    assert board["status"] == "ready"
    assert board["trials"] == 1
    assert board["value"] == {"one_word": 1, "turnover": 0}
    assert (audit.read_bytes(), archive_path.read_bytes()) == before
    assert result["candidate_codes"] == []


def test_conflicting_later_archive_cannot_replace_audited_event_facts(tmp_path, monkeypatch):
    from event_facts import resolve_limit_event_facts
    from test_event_facts import snapshot, bar

    tool = Path(__file__).resolve().parents[1] / "tools" / "preview_recap_report.py"
    spec = importlib.util.spec_from_file_location("preview_conflicting_capture", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = snapshot(count=0)
    audited = resolve_limit_event_facts(raw, price_rows=[bar()])
    archived = snapshot(count=2, source_timestamp="2026-09-07T17:00:00+08:00")
    archived["records"].append({**deepcopy(archived["records"][0]), "code": "sz000001", "name": "外部成员"})
    archived["row_count"] = 2
    archives = tmp_path / "events"
    archives.mkdir()
    archive_path = archives / "2026-09-07.json"
    archive_path.write_text(json.dumps(archived), encoding="utf-8")
    monkeypatch.setattr(module, "LIMIT_EVENT_SNAPSHOT_DIR", archives)
    monkeypatch.setattr(module, "RAW_BAR_CACHE_DIR", tmp_path / "no-bars")
    context = {"report_date": "2026-09-07", "publication_mode": "facts_only",
        "quality": {"status": "blocked", "publication_mode": "facts_only"},
        "facts": {"market_snapshot": {"report_date": "2026-09-07", "limit_pool_rows": raw["records"]},
                  "limit_event_snapshot": audited}}
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"context": context}), encoding="utf-8")
    calendar = tmp_path / "calendar.csv"
    calendar.write_text("trade_date\n2026-09-07\n2026-09-08\n", encoding="utf-8")
    before = audit.read_bytes(), archive_path.read_bytes()

    result = module.build_preview(audit, tmp_path / "out", calendar_cache=calendar)

    events = result["strategy_qualification"]["event_qualification"]
    assert events["metrics"]["bomb_rate"]["observed"] == 0
    assert events["metrics"]["bomb_rate"]["trials"] == 1
    assert events["metrics"]["reclose_rate"]["status"] == "not_applicable"
    assert events["metrics"]["board_structure"]["value"] == {"one_word": 1, "turnover": 0}
    assert events["population"]["source_timestamp"] == "2026-09-07T16:00:00+08:00"
    assert (audit.read_bytes(), archive_path.read_bytes()) == before
    assert result["candidate_codes"] == []
