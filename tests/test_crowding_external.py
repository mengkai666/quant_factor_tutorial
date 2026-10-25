from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from crowding_external import (CACHE_NAME, build_external_evidence, normalize_snapshot,
                               refresh_external_evidence, source_date, stock_code)
from crowding_external_view import external_markdown, render_external_section


def endpoint(key, raw, provider="财联社"):
    return dict(key=key, provider=provider, route=key, url="https://api3.cls.cn/test", params={}, raw=raw)


def snapshot():
    stock = dict(stock_code="sh603278", stock_name="甲", reason="6天连板")
    return dict(schema="crowding-external/v1", requested_as_of="2026-10-24", fetched_at="2026-10-24T10:00:00+08:00", endpoints=[
        endpoint("cls_mainline", dict(errno=0, data=dict(chances=[dict(plates=[dict(name="机器人", time="2026-10-23", faucet_1=stock, article=dict(id=123))])],
                 winds=[dict(title="机器人", plate_code="cls1", faucet_stocks=[dict(stock_code="sz301313", stock_name="乙")])],
                 styles=dict(title="市场风格", index_1=dict(stacks=[dict(stock_code="sh600825", stock_name="丙", rise_range="+20%")]))))),
        endpoint("cls_members:cls1", dict(errno=0, data=[dict(secu_code="sz301313", secu_name="乙"), dict(secu_code="sz300781", secu_name="丁")])),
        endpoint("cls_roll", dict(errno=0, data=dict(roll_data=[dict(id=777, ctime="2026-10-25", title="未来电报", stock_list=[dict(code="sh603278", name="甲")]),
                  dict(id=778, ctime="2026-10-24", title="宏观消息只有名称甲", stock_list=[])]))),
        endpoint("kpl_tianti", dict(errcode=0, date="2026-10-23", StockList=[["603278", "甲  ", 6, 0, "8011", "机器人"]]), "开盘啦"),
        endpoint("kpl_timeline", dict(errcode="0", date="2026-10-23", List=[dict(TimeMin="2026-10-23", TagShuXing=0, ZSName="机器人", Detail="下跌异动", StockList=[["301313", "乙"]])]), "开盘啦"),
    ])


def report():
    return dict(requested_date="2026-10-24", market_as_of="2026-10-23", tracking=dict(
        stocks=[dict(code="sh603278", name="甲", status="老团"), dict(code="sz301313", name="乙", status="退出")],
        daily=[dict(date="2026-10-23", rows=[dict(code="sh603278", name="甲", status="老团"), dict(code="sz301313", name="乙", status="退出")])]),
        screening_replay=dict(names={"sh603278": "甲", "sz301313": "乙", "sh600825": "丙"}, windows={"2":dict(days=[
            dict(date="2026-10-22", selected={"sh600825": {}}), dict(date="2026-10-23", selected={"sh603278": {}})])}))


@pytest.mark.parametrize("raw,expected", [("603278", "sh603278"), ("SZ301313", "sz301313"), ("301313.SZ", "sz301313"),
    ("835001", "bj835001"), ("000001", "sz000001"), ("cls82064", None), ("sh000001", None), ("801159板块", None),
    ("机器人", None), ("股票603278", None), (None, None), ("sz603278", None)])
def test_only_explicit_equity_codes(raw, expected):
    assert stock_code(raw) == expected


@pytest.mark.parametrize("raw,expected", [("20261023", "2026-10-23"), (1792722326, "2026-10-23"),
    (1792722326000, "2026-10-23"), ("2026-10-24T10:00:00+08:00", "2026-10-24"), (0, None), ("10-23", None), ("2026-13-40", None)])
def test_dates_are_provider_dates_not_guesses(raw, expected):
    assert source_date(raw) == expected


def test_match_by_code_does_not_change_price_or_tracking_pools():
    r = report(); before = deepcopy(r)
    value = build_external_evidence(r, snapshot=snapshot())
    assert r == before
    rows = {x["code"]: x for x in value["matches"]}
    assert set(rows) == {"sh603278", "sz301313", "sh600825"}
    assert rows["sh603278"]["price_selected"]
    assert rows["sh603278"]["sources"] == ["开盘啦", "财联社"]
    assert not rows["sz301313"]["price_selected"]
    assert rows["sz301313"]["tracking_status"] == "退出"
    assert rows["sz301313"]["risk_count"] == 1
    assert rows["sh600825"]["pool_context"] == "仅两月研究历史入选"
    assert value["external_only"][0]["code"] == "sz300781"
    assert value["excluded_count"] == 1
    assert not any(x["category"] == "关联电报" for row in value["matches"] for x in row["records"])


def test_undated_winds_are_not_inferred_from_fetch_or_other_provider():
    value = build_external_evidence(report(), snapshot=snapshot())
    row = next(x for x in value["matches"] if x["code"] == "sz301313")
    wind = next(x for x in row["records"] if x["category"] == "风口龙头")
    assert wind["source_date"] is None
    assert wind["date_status"] == "未标注日期"
    assert not wind["current_dated_context"]
    assert wind["observed_at"] == "2026-10-24T10:00:00+08:00"
    member = next(x for x in row["records"] if x["category"] == "风口板块成员")
    assert member["match_basis"] == "接口板块成员代码"
    assert member["theme"] == "机器人"


def test_later_snapshot_is_completely_hidden_from_historical_report():
    r = report();r["requested_date"] = "2026-10-23"
    value = build_external_evidence(r, snapshot=snapshot())
    assert value["mode"] == "future_snapshot"
    assert not value["matches"] and not value["external_only"] and not value["topics"]
    assert value["excluded_count"] > 0


def test_missing_or_invalid_cache_stays_offline(tmp_path):
    assert build_external_evidence(report(), data_dir=tmp_path)["mode"] == "missing"
    (tmp_path / CACHE_NAME).write_text("not json", encoding="utf-8")
    value = build_external_evidence(report(), data_dir=tmp_path)
    assert value["mode"] == "missing" and value["warnings"]
    invalid = snapshot();invalid["fetched_at"] = None
    assert build_external_evidence(report(), snapshot=invalid)["mode"] == "invalid_time"


def test_stale_cache_is_not_current_dated_support():
    r = report();r["requested_date"] = "2026-10-30"
    value = build_external_evidence(r, snapshot=snapshot())
    assert value["mode"] == "stale"
    assert not any(x["current_dated_context"] for row in value["matches"] for x in row["records"])


def test_partial_api_failure_is_visible_not_fake_success():
    s = snapshot();s["endpoints"][0]["raw"] = dict(errno=10012, msg="签名失效", data={})
    value = build_external_evidence(report(), snapshot=s)
    assert "签名失效" in " ".join(value["warnings"])
    assert not value["endpoints"][0]["success"]
    assert value["endpoints"][0]["record_count"] == 0
    assert all(x["category"] != "主线龙头" for row in value["matches"] for x in row["records"])


def test_refresh_preserves_good_cache_on_total_failure_and_archives_attempt(tmp_path):
    old = snapshot();(tmp_path / CACHE_NAME).write_text(json.dumps(old), encoding="utf-8")
    def failed(route, timeout):
        raise TimeoutError("test timeout")
    with pytest.raises(RuntimeError):
        refresh_external_evidence("2026-10-24", tmp_path, now=datetime(2026,10,24,11,tzinfo=ZoneInfo("Asia/Shanghai")), fetcher=failed)
    assert json.loads((tmp_path / CACHE_NAME).read_text()) == old
    assert len(list((tmp_path / "crowding_external_snapshots").glob("*.json"))) == 1
    value = build_external_evidence(report(), data_dir=tmp_path)
    assert value["mode"] == "stale" and "全部失败" in " ".join(value["warnings"])


def test_refresh_follows_only_explicit_wind_membership(tmp_path):
    called=[]
    def fetch(route, timeout):
        key,provider,host,path,params = route;called.append((key,params))
        raw = dict(errno=0, data={}) if provider == "财联社" else dict(errcode="0")
        if key == "cls_tuyere":raw["data"] = dict(today_tuyere=[dict(title="机器人",plate_code="cls1")])
        if key.startswith("cls_members:"):raw["data"] = [dict(secu_code="sh603278",secu_name="甲")]
        result=endpoint(key,raw,provider);result["route"]=path;return result
    snap=refresh_external_evidence("2026-10-24",tmp_path,now=datetime(2026,10,24,10,tzinfo=ZoneInfo("Asia/Shanghai")),fetcher=fetch)
    assert ("cls_members:cls1",{"plate_code":"cls1"}) in called
    assert (tmp_path/CACHE_NAME).exists()
    parsed=normalize_snapshot(snap)
    assert parsed["records"][0]["theme"] == "机器人"
    assert next(x for x in parsed["endpoints"] if x["key"]=="cls_members:cls1")["record_count"] == 1


def test_short_arrays_ads_and_empty_payload_are_safe():
    s=snapshot()
    s["endpoints"].extend([endpoint("kpl_resumption",dict(errcode="0",list=[None,dict(StockList=[[],["603278"]])]),"开盘啦"),
                           endpoint("cls_roll",dict(errno=0,data=dict(roll_data=[None,dict(id=99,is_ad=1,stock_list=[dict(code="sh603278")])])) )])
    assert normalize_snapshot(s)["records"]


def test_html_escapes_remote_copy_and_rejects_unsafe_links():
    value=build_external_evidence(report(),snapshot=snapshot())
    value["matches"][0]["name"]='<script>alert("x")</script>'
    value["matches"][0]["records"][0]["url"]='javascript:alert(1)'
    document=render_external_section(value)
    assert '<script>alert' not in document and 'javascript:' not in document
    assert '&lt;script&gt;' in document
    assert 'external-scope' in document and 'data-external-code' in document
    assert '来源日期' in document and '未标注' in document
    assert '区间强势' in "\n".join(external_markdown(value))


def test_summary_cards_have_responsive_grid_container():
    from html.parser import HTMLParser

    class CardParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.card_count = 0

        def handle_starttag(self, tag, attrs):
            classes = dict(attrs).get("class", "").split()
            if "external-stat" in classes:
                assert any("external-stats" in parent for parent in self.stack)
                self.card_count += 1
            if tag not in ("br", "input"):
                self.stack.append(classes)

        def handle_endtag(self, tag):
            if self.stack:
                self.stack.pop()

    parser = CardParser()
    parser.feed(render_external_section(build_external_evidence(report(), snapshot=snapshot())))
    assert parser.card_count == 4


def test_half_year_history_matches_without_changing_two_month_qualification():
    r = report()
    r["screening_replay"]["windows"]["6"] = dict(days=[
        dict(date="2026-05-02", selected={"sz300781": {}}),
        dict(date="2026-10-23", selected={"sz300781": {}})])
    before = deepcopy(r)
    value = build_external_evidence(r, snapshot=snapshot())
    assert r == before
    row = next(x for x in value["matches"] if x["code"] == "sz300781")
    assert row["ever_selected_6m"] and row["price_selected_6m"]
    assert not row["price_selected"] and not row["ever_selected_2m"]
    assert row["pool_context"] == "仅半年研究历史入选"
    assert row["first_selected_6m"] == "2026-05-02"
    assert row["last_selected_6m"] == "2026-10-23"
    assert value["summary"]["historical_6m_only"] == 1
    assert "半年研究：首选 2026-05-02" in render_external_section(value)


def test_ranking_is_a_bounded_daily_screen_not_a_historical_return():
    s = snapshot()
    items = [dict(secu_code="603278.SH", secu_name="甲", change="+10.00%")]*60
    items += [dict(secu_code="000001.SZ", secu_name="超出首屏", change="+1.00%")]
    s["endpoints"].append(endpoint("cls_ranking", dict(code=200, data=dict(items=items))))
    parsed = normalize_snapshot(s)
    ranks = [r for r in parsed["records"] if r["category"] == "当日涨幅榜"]
    assert len(ranks) == 60
    assert all(r["code"] == "sh603278" for r in ranks)
    assert all(r["source_date"] is None for r in ranks)
    assert "非两月/半年收益" in ranks[0]["detail"]
    value = build_external_evidence(report(), snapshot=s)
    assert "当日涨幅榜" in value["matches"][0]["categories"]
    assert not any(x["current_dated_context"] for row in value["matches"] for x in row["records"] if x["category"]=="当日涨幅榜")
