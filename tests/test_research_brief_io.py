from copy import deepcopy
import csv
import gzip
import json
from pathlib import Path

import pytest
from test_research_brief import stock, context, prices, DAY, DAYS


def inputs(tmp_path):
    rows = [stock(f"sz00000{i}", "算力" if i <= 4 else "机器人", height=(i-1)%4+1) for i in range(1,9)]
    ctx = context(rows)
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"context":ctx}), encoding="utf-8")
    snapshots, slices, bars = [tmp_path / name for name in ("snapshots", "slices", "bars")]
    for path in (snapshots, slices, bars): path.mkdir()
    for day in DAYS[1:-1]:
        (snapshots / f"{day}.json").write_text(json.dumps({"report_date":day, "date_verified":True,
            "limit_up":0,"limit_pool_rows":[]}), encoding="utf-8")
    current = snapshots / f"{DAY}.json"
    current.write_text(json.dumps(ctx["facts"]["market_snapshot"]), encoding="utf-8")
    for day in (DAYS[0], DAY):
        with gzip.open(slices / f"{day}.csv.gz", "wt", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f,fieldnames=list(prices(rows)[0]))
            writer.writeheader()
            writer.writerows(prices(rows,day=day))
    calendar = tmp_path / "calendar.csv"
    calendar.write_text("trade_date\n"+"\n".join(DAYS+["2026-09-08"])+"\n",encoding="utf-8")
    return rows, ctx, audit, {"snapshot_dir":snapshots,"price_slices_dir":slices,
        "calendar_cache":calendar,"raw_bar_cache_dir":bars}


def test_file_loader_builds_multi_sector_brief_without_rewriting_sources(tmp_path):
    from research_brief_io import load_brief_context, prepare_research_brief
    rows,ctx,audit,paths = inputs(tmp_path)
    before = {p:p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    got_ctx = load_brief_context(audit_path=audit, snapshot_dir=paths["snapshot_dir"])
    brief = prepare_research_brief(got_ctx, **paths)
    assert [len(s["stocks"]) for s in brief["sectors"]] == [3,3]
    assert brief["recent"]["complete"] is True
    assert brief["target_trade_date"] == "2026-09-08"
    assert all(p.read_bytes()==value for p,value in before.items())
    assert got_ctx == ctx


def test_latest_loader_uses_verified_body_date_not_only_filename(tmp_path):
    from research_brief_io import load_brief_context
    rows,ctx,audit,paths = inputs(tmp_path)
    got = load_brief_context(snapshot_dir=paths["snapshot_dir"])
    assert got["report_date"] == DAY
    wrong = paths["snapshot_dir"] / "2026-09-08.json"
    wrong.write_text(json.dumps(ctx["facts"]["market_snapshot"]),encoding="utf-8")
    with pytest.raises(ValueError):load_brief_context(snapshot_dir=paths["snapshot_dir"])


def test_bad_history_day_is_unknown_not_a_no_limit_day(tmp_path):
    from research_brief_io import prepare_research_brief
    rows,ctx,audit,paths = inputs(tmp_path)
    (paths["snapshot_dir"] / "2026-09-03.json").write_text(json.dumps({"report_date":"2026-09-03",
        "limit_up":10,"limit_pool_rows":[]}),encoding="utf-8")
    brief = prepare_research_brief(ctx, **paths)
    assert brief["recent"]["complete"] is False
    assert "2026-09-03" not in brief["recent"]["coverage_days"]
    point = next(x for x in brief["recent"]["stocks"][0]["trajectory"] if x["date"]=="2026-09-03")
    assert point["limit_up"] is None


def test_missing_current_price_slice_does_not_borrow_previous_prices(tmp_path):
    from research_brief_io import prepare_research_brief
    rows,ctx,audit,paths = inputs(tmp_path)
    (paths["price_slices_dir"] / f"{DAY}.csv.gz").unlink()
    with pytest.raises(ValueError, match="同日"):
        prepare_research_brief(ctx, **paths)


def test_optional_bar_collection_only_requests_selected_or_recent_stocks(tmp_path):
    from research_brief_io import prepare_research_brief
    rows,ctx,audit,paths = inputs(tmp_path)
    class Provider:
        def fetch_day(self,codes,day,*,cache_dir):
            assert set(codes)=={"sz000002","sz000003","sz000004","sz000006","sz000007","sz000008"}
            assert day==DAY and cache_dir==paths["raw_bar_cache_dir"]
            return {"status":"complete","requested":6,"covered":6,"errors":[],"records":[
                {"code":c,"date":DAY,"open_raw":10.,"high_raw":11.,"low_raw":9.,"close_raw":11.,
                 "price_basis":"raw","source":"fixture_raw","source_timestamp":DAY+"T16:00:00+08:00"} for c in codes]}
    brief=prepare_research_brief(ctx,fetch_missing=True,provider=Provider(),**paths)
    assert all(r["low"]==9. for s in brief["sectors"] for r in s["stocks"])
    assert brief["provenance"]["ohlc_collection"]["covered"] == 6


def test_writer_exports_research_lists_without_changing_legacy_execution_pool(tmp_path):
    from research_brief_io import prepare_research_brief, write_research_brief
    rows,ctx,audit,paths=inputs(tmp_path)
    brief=prepare_research_brief(ctx,**paths)
    output=tmp_path/"output";output.mkdir()
    legacy=output/"focus_pool.csv";legacy.write_text("legacy trade data",encoding="utf-8")
    result=write_research_brief(brief,output,html="<!doctype html><html><body>fixture</body></html>",source_paths=[audit])
    assert legacy.read_text(encoding="utf-8")=="legacy trade data"
    saved=json.loads(Path(result["json"]).read_text(encoding="utf-8"))
    assert saved["purpose"]=="research_observation"
    with Path(result["watchlist_csv"]).open(encoding="utf-8-sig",newline="") as f:
        exported=list(csv.DictReader(f))
    assert len(exported)==6
    assert {x["code"] for x in exported}=={r["code"] for s in brief["sectors"] for r in s["stocks"]}
    assert all(x["purpose"]=="research_observation" and x["report_date"]==DAY for x in exported)
    assert not any("permission" in key or "可执行" in key for key in exported[0])
    assert Path(result["recent_csv"]).exists()
    assert 'report-integrity' in Path(result["html"]).read_text(encoding="utf-8")


def test_writer_refuses_to_overwrite_an_input_before_any_output(tmp_path):
    from research_brief_io import prepare_research_brief, write_research_brief
    rows,ctx,audit,paths=inputs(tmp_path)
    brief=prepare_research_brief(ctx,**paths)
    output=tmp_path/"output";output.mkdir()
    source=output/f"research_brief_{DAY}.json";source.write_text("keep",encoding="utf-8")
    with pytest.raises(ValueError):
        write_research_brief(brief,output,html="<body></body>",source_paths=[source])
    assert source.read_text(encoding="utf-8")=="keep"
    assert len(list(output.iterdir()))==1


def test_verified_legacy_snapshot_envelope_supplies_its_rows_date(tmp_path):
    from research_brief_io import prepare_research_brief
    rows,ctx,audit,paths=inputs(tmp_path)
    (paths["snapshot_dir"] / "2026-09-03.json").write_text(json.dumps({"report_date":"2026-09-03",
        "date_verified":True,"snapshot_schema":"daily-market-facts/v1","limit_up":1,
        "limit_pool_rows":[{"code":"sz000003","name":"股票003","height":2}]}),encoding="utf-8")
    brief=prepare_research_brief(ctx,**paths)
    assert brief["recent"]["complete"] is True
    item=next(r for r in brief["recent"]["stocks"] if r["code"]=="sz000003")
    assert item["limit_up_count"]==2
    assert next(x for x in item["trajectory"] if x["date"]=="2026-09-03")["height"]==2


def test_csv_retains_recent_rows_beyond_the_default_open_table(tmp_path):
    from research_brief_io import prepare_research_brief,write_research_brief
    rows,ctx,audit,paths=inputs(tmp_path)
    brief=prepare_research_brief(ctx,recent_limit=2,**paths)
    files=write_research_brief(brief,tmp_path/"out",html="<html><body>fixture</body></html>")
    with Path(files["recent_csv"]).open(encoding="utf-8-sig",newline="") as f: exported=list(csv.DictReader(f))
    assert len(exported)==brief["recent"]["total_count"]==6


def test_conflicting_fetched_bars_cannot_restore_the_pre_fetch_watchlist(tmp_path):
    from research_brief_io import prepare_research_brief
    rows,ctx,audit,paths=inputs(tmp_path)
    ctx=context([rows[0]])
    class Provider:
        def fetch_day(self,codes,day,*,cache_dir):
            return {"status":"complete","records":[{"code":codes[0],"date":DAY,"open_raw":9.,
                "high_raw":9.,"low_raw":9.,"close_raw":9.,"price_basis":"raw","source":"fixture",
                "source_timestamp":DAY+"T16:00:00+08:00"}],"covered":1,"requested":1,"errors":[]}
    with pytest.raises(ValueError,match="同日"):
        prepare_research_brief(ctx,fetch_missing=True,provider=Provider(),**paths)


@pytest.mark.parametrize("day",["2026-09-09","2026-09-10"])
def test_loader_rejects_unclosed_or_future_daily_snapshots(tmp_path,monkeypatch,day):
    import research_brief_io as module
    from datetime import datetime,timezone,timedelta
    class Clock:
        @staticmethod
        def now(tz=None):return datetime(2026,9,9,10,0,tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(module,"datetime",Clock,raising=False)
    row=stock("sz000001",day=day)
    (tmp_path/(day+".json")).write_text(json.dumps({"report_date":day,"date_verified":True,
        "limit_up":1,"limit_pool_rows":[row]}),encoding="utf-8")
    with pytest.raises(ValueError,match="收盘|未来"):
        module.load_brief_context(snapshot_dir=tmp_path)
