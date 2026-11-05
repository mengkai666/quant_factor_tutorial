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


def source(day="2026-11-05"):
    return (f'<html><head><meta name="report-date" content="{day}"></head><body>'
            '<h2 id="sec-1">原复盘</h2><!-- daily-fupan-continuous:start -->复盘事实<!-- daily-fupan-continuous:end -->'
            '<!-- cb-tracking-teaser:start --><h2 id="sec-2">旧转债结论</h2><!-- cb-tracking-teaser:end -->'
            '<h2 id="sec-3">原主线图</h2><div id="chart">数据</div></body></html>')


PANEL = '<!-- crowding-plan-panel:start --><section><h2>抱团板块</h2><p>未确认触发</p></section><!-- crowding-plan-panel:end -->'
CARD = '<!-- cb-tracking-teaser:start --><section><h2>同日转债</h2><p>核对正股</p></section><!-- cb-tracking-teaser:end -->'


def test_refresh_replaces_old_narrative_and_is_idempotent(module):
    updated = module.update_document(source(), report_date="2026-11-05", panel=PANEL, cb_card=CARD)
    assert "旧转债结论" not in updated
    assert '<div id="chart">数据</div>' in updated
    assert "复盘事实" in updated
    assert updated.count("<!-- crowding-plan-panel:start -->") == 1
    assert 'id="crowding-plan-heading-1"' in updated
    assert 'href="#crowding-plan-heading-1"' in updated
    assert 'id="sec-3"' in updated
    assert module.update_document(updated, report_date="2026-11-05", panel=PANEL, cb_card=CARD) == updated


def test_refresh_rejects_other_date_and_broken_boundaries(module):
    with pytest.raises(ValueError, match="不是指定数据日"):
        module.update_document(source("2026-11-06"), report_date="2026-11-05", panel=PANEL, cb_card=CARD)
    with pytest.raises(ValueError, match="边界重复或不完整"):
        module.update_document(source().replace("<!-- cb-tracking-teaser:end -->", ""),
                               report_date="2026-11-05", panel=PANEL, cb_card=CARD)
    with pytest.raises(ValueError, match="唯一的复盘插入位置"):
        module.update_document(source().replace("<!-- daily-fupan-continuous:end -->", ""),
                               report_date="2026-11-05", panel=PANEL, cb_card=CARD)


def test_report_date_checks_report_level_clock_and_conflict(module):
    assert module._report_date('<section data-report-date="2026-11-05">嵌入研究</section>') is None
    report = source() + '<script type="application/json" id="report-integrity">{"report_date":"2026-11-06"}</script>'
    with pytest.raises(ValueError, match="日期不一致"):
        module._report_date(report)


def _renderers(monkeypatch, calls):
    plan = {"report_date": "2026-11-05", "target_date": "2026-11-06", "decision": {"execution_allowed": False}}
    ndp = SimpleNamespace(
        load_plan_context=lambda *args, **kw: {"report_date": "2026-11-05"},
        build_next_day_plan=lambda *args, **kw: plan,
        render_plan_panel_html=lambda *args, **kw: PANEL,
        export_plan_standalone_reports=lambda *args, **kw: calls.append("plan") or {},
    )
    cb = SimpleNamespace(render_cb_teaser_card_html=lambda *args, **kw: CARD,
                         export_cb_tracking_reports=lambda *args, **kw: calls.append("cb") or {})
    monkeypatch.setitem(sys.modules, "crowding_next_day_plan", ndp)
    monkeypatch.setitem(sys.modules, "cb_baotuan_report", cb)


def test_refresh_writes_only_same_day_main_report_and_keeps_inputs(module, tmp_path, monkeypatch):
    calls = []
    _renderers(monkeypatch, calls)
    output, data = tmp_path / "output", tmp_path / "data"
    output.mkdir(); data.mkdir()
    (output / "site").mkdir()
    main, later = output / "主线强度追踪.html", output / "site/latest.html"
    original = source().encode("utf-8")
    main.write_bytes(original)
    later.write_text(source("2026-11-06"), encoding="utf-8")
    journal = data / "report_prediction_history.jsonl"
    journal.write_bytes(b'{"unchanged":true}\n')
    later_before, journal_before = later.read_bytes(), journal.read_bytes()
    result = module.refresh("2026-11-05", output_dir=output, data_dir=data)
    assert calls == ["plan", "cb"]
    assert len(result["changed_main_reports"]) == 1
    assert Path(result["changed_main_reports"][0]["backup"]).read_bytes() == original
    assert later.read_bytes() == later_before
    assert journal.read_bytes() == journal_before
    assert module.refresh("2026-11-05", output_dir=output, data_dir=data)["changed_main_reports"] == []
    assert Path(result["changed_main_reports"][0]["backup"]).read_bytes() == original


def test_refresh_checks_main_blocks_before_writing_any_pages(module, tmp_path, monkeypatch):
    calls = []
    _renderers(monkeypatch, calls)
    main = tmp_path / "主线强度追踪.html"
    original = source().replace("<!-- cb-tracking-teaser:end -->", "").encode("utf-8")
    main.write_bytes(original)
    with pytest.raises(ValueError, match="边界重复或不完整"):
        module.refresh("2026-11-05", output_dir=tmp_path)
    assert calls == []
    assert main.read_bytes() == original


def test_legacy_links_do_not_reintroduce_old_trades_or_position_permission(module):
    hub = ('<!-- 卡片 1: 今日复盘与明日预案 --><span>仓位上限 ≤ 1.5 成</span>'
           '<!-- 卡片 2: 原入口 --><a href="preserve.html">保留</a>'
           '<!-- 卡片 5: 可转债抱团与新周期追踪作战室 -->🤖 机器人转债集群主升 / 澳弘破位出清'
           '<!-- war-room-hub:end -->')
    updated = module._refresh_legacy_hub(hub, {"decision": {"execution_allowed": False}})
    assert "当前不开新仓" in updated
    assert "1.5 成" not in updated
    assert "机器人转债集群主升" not in updated
    assert '<a href="preserve.html">保留</a>' in updated


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
