import json

import cb_baotuan_report as report
from test_cb_plan_data import snapshot


def bind(tmp_path, monkeypatch, payload=None):
    data, output = tmp_path / "data", tmp_path / "output"
    monkeypatch.setattr(report, "DATA_DIR", data)
    monkeypatch.setattr(report, "OUTPUT_DIR", output)
    monkeypatch.setattr(report, "SITE_DIR", output / "site")
    if payload is not None:
        path = data / "cb_daily_snapshots" / "2030-04-08.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(payload), encoding="utf-8")


def test_compatibility_never_duplicates_one_day_as_three_day_return(tmp_path, monkeypatch):
    bind(tmp_path, monkeypatch, snapshot())
    items, label = report.get_dynamic_cb_tracking_data("20300408")
    assert items[0]["pct_day"] == 0
    assert items[0]["pct_3d"] is None
    assert items[0]["size_yi"] == 1.2
    assert "2030-04-08" in label


def test_report_only_uses_supplied_names_and_preserves_conditional_plan_feature(tmp_path, monkeypatch):
    payload = snapshot()
    payload["rows"][0]["name"] = '<script>alert("bad")</script>演示债'
    bind(tmp_path, monkeypatch, payload)
    page = report.generate_cb_standalone_html("2030-04-08")
    assert "条件预案速查" in page and "失效情形" in page
    assert "演示债" in page and '<script>alert("bad")</script>' not in page
    assert "3日累计" not in page
    for stale in ("奥普转债", "澳弘转债", "福新转债", "稳健底仓", "违规隔夜", "无涨跌幅限制", "零隔夜风险", "毫不犹豫", "归零风险"):
        assert stale not in page
    assert 'data-as-of="2030-04-08"' in page
    assert "15只占比" in page and "65%+" not in page
    assert not (tmp_path / "output").exists()  # 渲染不写文件。


def test_missing_date_is_visible_without_fixed_historical_examples(tmp_path, monkeypatch):
    bind(tmp_path, monkeypatch)
    page = report.generate_cb_standalone_html("2030-04-09")
    assert "缺少合格数据" in page and "2030-04-09" in page
    assert "2026-10-23" not in page
    assert report.get_dynamic_cb_tracking_data("2030-04-09")[0] == []


def test_export_keeps_paths_and_correct_site_navigation(tmp_path, monkeypatch):
    bind(tmp_path, monkeypatch, snapshot())
    paths = report.export_cb_tracking_reports("20300408")
    expected = {"output_path", "site_path", "date_path", "reports_path"}
    assert expected <= set(paths)
    from pathlib import Path
    assert all(Path(paths[key]).exists() for key in expected)
    assert 'href="../reports/2030-04-08.html"' in Path(paths["site_path"]).read_text(encoding="utf-8")
    assert 'href="site/reports/2030-04-08.html"' in Path(paths["output_path"]).read_text(encoding="utf-8")
    assert Path(paths["date_path"]).name == "2030-04-08.html"


def test_teaser_keeps_embedding_markers_and_missing_numbers(tmp_path, monkeypatch):
    payload = snapshot()
    payload["rows"][0]["premium_pct"] = None
    bind(tmp_path, monkeypatch, payload)
    page = report.render_cb_teaser_card_html("20300408", True, "../")
    assert "<!-- cb-tracking-teaser:start -->" in page
    assert "<!-- cb-tracking-teaser:end -->" in page
    assert 'href="../cb_baotuan/2030-04-08.html"' in page
    assert 'data-report-date="2030-04-08"' in page
    assert "—" in page and "部分" in page


def test_teaser_uses_explicit_isolated_data_directory(tmp_path):
    data, output = tmp_path / "data", tmp_path / "output"
    cache = data / "cb_daily_snapshots"
    cache.mkdir(parents=True)
    (cache / "2030-04-08.json").write_text(json.dumps(snapshot()), encoding="utf-8")
    page = report.render_cb_teaser_card_html("2030-04-08", output_dir=output, data_dir=data)
    assert "示例转债" in page and 'data-status="ready"' in page
    assert not output.exists()


def test_historical_rebuild_preserves_latest_and_uses_isolated_output(tmp_path):
    from pathlib import Path
    data, output = tmp_path / "data", tmp_path / "isolated"
    current = snapshot("2030-04-09")
    older = snapshot("2030-04-08")
    cache = data / "cb_daily_snapshots"
    cache.mkdir(parents=True)
    for payload in (current, older):
        (cache / f"{payload['as_of']}.json").write_text(json.dumps(payload), encoding="utf-8")
    latest = report.export_cb_tracking_reports("2030-04-09", output_dir=output, data_dir=data)
    before = Path(latest["site_path"]).read_bytes()
    old = report.export_cb_tracking_reports("2030-04-08", output_dir=output, data_dir=data)
    assert old["latest_updated"] is False
    assert Path(latest["site_path"]).read_bytes() == before
    assert 'data-report-date="2030-04-08"' in Path(old["date_path"]).read_text(encoding="utf-8")
    assert 'href="../reports/2030-04-08.html"' in Path(old["date_path"]).read_text(encoding="utf-8")
