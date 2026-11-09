"""独立子页刷新不能读取或改写主报告。"""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest


@pytest.fixture
def module():
    path = Path(__file__).resolve().parents[1] / "tools" / "refresh_crowding_plan_pages.py"
    spec = importlib.util.spec_from_file_location("refresh_crowding_plan_pages", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _renderers(monkeypatch, calls):
    plan = {"report_date": "2026-11-05", "target_date": "2026-11-06", "decision": {"execution_allowed": False}}
    ndp = SimpleNamespace(
        load_plan_context=lambda *args, **kw: {"report_date": "2026-11-05"},
        build_next_day_plan=lambda *args, **kw: plan,
        export_plan_standalone_reports=lambda *args, **kw: calls.append("plan") or {},
    )
    cb = SimpleNamespace(export_cb_tracking_reports=lambda *args, **kw: calls.append("cb") or {})
    monkeypatch.setitem(sys.modules, "crowding_next_day_plan", ndp)
    monkeypatch.setitem(sys.modules, "cb_baotuan_report", cb)


def test_refresh_never_reads_or_changes_main_reports(module, tmp_path, monkeypatch):
    calls = []
    _renderers(monkeypatch, calls)
    output, data = tmp_path / "output", tmp_path / "data"
    data.mkdir()
    originals = {}
    # 包含无日期、其他日期和损坏边界；子页生成不依赖主报告格式。
    for relative, body in [
        ("主线强度追踪.html", b"main unchanged"),
        ("site/latest.html", b'<meta name="report-date" content="2026-11-06">'),
        ("site/reports/2026-11-05.html", b'<!-- crowding-plan-panel:start -->'),
    ]:
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        originals[path] = body
    journal = data / "report_prediction_history.jsonl"
    journal.write_bytes(b'{"unchanged":true}\n')
    original_read = Path.read_bytes
    original_read_text = Path.read_text

    def guard(method):
        def read(path, *args, **kwargs):
            assert path not in originals, "独立子页不得读取主报告"
            return method(path, *args, **kwargs)
        return read
    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", guard(original_read))
        patch.setattr(Path, "read_text", guard(original_read_text))
        result = module.refresh("2026-11-05", output_dir=output, data_dir=data)
    assert calls == ["plan", "cb"]
    assert result["mode"] == "standalone_subpages_only"
    assert result["main_reports_untouched"] is True
    assert result["changed_main_reports"] == []
    assert all(path.read_bytes() == body for path, body in originals.items())
    assert journal.read_bytes() == b'{"unchanged":true}\n'
    assert not (output / "plan_refresh_backups").exists()


def test_refresh_works_without_main_report(module, tmp_path, monkeypatch):
    calls = []
    _renderers(monkeypatch, calls)
    result = module.refresh("2026-11-05", output_dir=tmp_path)
    assert calls == ["plan", "cb"]
    assert result["main_reports_untouched"] is True
    assert not (tmp_path / "主线强度追踪.html").exists()


@pytest.mark.parametrize("allowed,expected", [(False, "当前不开新仓"), (True, "已通过资格")])
def test_hub_uses_same_day_readiness_instead_of_limit_down_count(monkeypatch, allowed, expected):
    import subpage_hub
    monkeypatch.setitem(sys.modules, "trading_playbook_links", SimpleNamespace(render_main_entry=lambda *args: ""))
    context = {"today_decision": {"execution_allowed": allowed, "report_date": "2026-11-05"},
               "decision_readiness": {"execution_ready": allowed, "plan_permitted": allowed, "report_date": "2026-11-05"}}
    document = subpage_hub.render_war_room_hub_html("2026-11-05", context, {"dt": 0})
    assert expected in document
    assert "1.5 成" not in document
    assert "机器人转债集群主升" not in document
    assert "全线接力爆发" not in document
    assert "一键复制晨会卡片" not in document
    context["decision_readiness"]["report_date"] = "2026-11-04"
    assert "当前不开新仓" in subpage_hub.render_war_room_hub_html("2026-11-05", context, {"dt": 0})
