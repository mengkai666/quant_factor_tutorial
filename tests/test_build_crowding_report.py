"""Report CLI refresh status must agree with the external evidence outcome."""
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import crowding_external as external


@pytest.fixture
def cli(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "tools" / "build_crowding_report.py"
    spec = importlib.util.spec_from_file_location("crowding_report_cli_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Exercise actual external refresh/cache handling, without market I/O or network.
    refresh = external.refresh_external_evidence
    def fixed_refresh(as_of, data_dir):
        return refresh(as_of, data_dir, now=datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Asia/Shanghai")))
    monkeypatch.setattr(external, "refresh_external_evidence", fixed_refresh)

    def build(as_of, *, data_dir, tracking_rules):
        result = dict(requested_date=as_of, market_as_of="2026-10-23", tracking={},
                      screening_replay={}, sources_as_of={}, quality={})
        result["external_evidence"] = external.build_external_evidence(result, data_dir=data_dir)
        return result

    def export(result, output_dir, *, site_dir):
        target = Path(output_dir) / "report.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return {"json": str(target)}

    monkeypatch.setattr(module, "build_crowding_report", build)
    monkeypatch.setattr(module, "export_crowding_reports", export)
    return module


def arguments(tmp_path):
    return ["--as-of", "2026-10-25", "--data-dir", str(tmp_path / "data"),
            "--output-dir", str(tmp_path / "output")]


def write_old_cache(tmp_path):
    path = tmp_path / "data" / external.CACHE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(schema="crowding-external/v1", requested_as_of="2026-10-24",
                                  fetched_at="2026-10-24T12:00:00+08:00", endpoints=[])), encoding="utf-8")
    return path


@pytest.mark.parametrize("has_old_cache", [False, True])
def test_total_refresh_failure_returns_nonzero_but_exports_degraded_report(cli, monkeypatch, tmp_path, capsys, has_old_cache):
    cache = write_old_cache(tmp_path) if has_old_cache else tmp_path / "data" / external.CACHE_NAME
    old = cache.read_bytes() if has_old_cache else None
    def fail(route, timeout):
        raise TimeoutError("simulated failure")
    monkeypatch.setattr(external, "_request", fail)
    status = cli.main(arguments(tmp_path) + ["--refresh-external-evidence"])
    captured = capsys.readouterr()
    value = json.loads(captured.out)
    report = json.loads(Path(value["files"]["json"]).read_text(encoding="utf-8"))
    assert "原成功缓存保留" in captured.err
    assert any("全部失败" in warning for warning in report["external_evidence"]["warnings"])
    assert value["external_mode"] == ("stale" if has_old_cache else "missing")
    assert (tmp_path / "data" / "crowding_external_last_failure.json").exists()
    assert len(list((tmp_path / "data" / "crowding_external_snapshots").glob("*.json"))) == 1
    if has_old_cache:
        assert cache.read_bytes() == old
    else:
        assert not cache.exists()
    assert status == 1
    assert value["external_refresh_status"] == "failed"


def test_offline_report_does_not_refresh_or_change_cache(cli, monkeypatch, tmp_path, capsys):
    cache = write_old_cache(tmp_path)
    old = cache.read_bytes()
    def forbidden(route, timeout):
        pytest.fail("offline report must not fetch external evidence")
    monkeypatch.setattr(external, "_request", forbidden)
    assert cli.main(arguments(tmp_path)) == 0
    captured = capsys.readouterr()
    value = json.loads(captured.out)
    assert not captured.err
    assert value["external_refresh_status"] == "not_requested"
    assert value["external_mode"] == "supplement"
    assert cache.read_bytes() == old
    assert not (tmp_path / "data" / "crowding_external_snapshots").exists()


@pytest.mark.parametrize("partial_failure", [False, True])
def test_successful_or_partial_refresh_returns_zero(cli, monkeypatch, tmp_path, capsys, partial_failure):
    def fetch(route, timeout):
        key, provider, host, path, params = route
        if partial_failure and provider == "开盘啦":
            raise TimeoutError("simulated partial failure")
        raw = dict(errno=0, data={}) if provider == "财联社" else dict(errcode=0)
        return dict(key=key, provider=provider, route=path, url=None, params=params, raw=raw)
    monkeypatch.setattr(external, "_request", fetch)
    assert cli.main(arguments(tmp_path) + ["--refresh-external-evidence"]) == 0
    captured = capsys.readouterr()
    value = json.loads(captured.out)
    assert not captured.err
    assert value["external_refresh_status"] == "succeeded"
    assert value["external_mode"] == "supplement"
    cache = tmp_path / "data" / external.CACHE_NAME
    assert cache.exists()
    assert not (tmp_path / "data" / "crowding_external_last_failure.json").exists()
    report = json.loads(Path(value["files"]["json"]).read_text(encoding="utf-8"))
    assert bool(report["external_evidence"]["warnings"]) == partial_failure
