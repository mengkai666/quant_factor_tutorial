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
    assert result["quality_unchanged"]
    assert result["publication_mode"] == "facts_only"
    assert not result["readiness"]["execution_ready"]
    assert Path(result["preview"]).exists()
    assert "离线重算预览" in Path(result["preview"]).read_text(encoding="utf-8")
    assert audit.read_bytes() == before_audit
    assert history.read_bytes() == before_history
    module.build_preview(audit, tmp_path / "preview", calendar_cache=calendar, history_path=history)
    events = (tmp_path / "preview" / "preview_journal_2026-09-03.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) == 2
