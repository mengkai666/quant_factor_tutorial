import json

import pandas as pd
import pytest

import next_day_plan as ndp


@pytest.mark.parametrize("prev,close,code,name,expected", [
    (24.06, 26.47, "sz001216", "华瓷股份", "ZT"),   # 主板 10%，四舍五入到分
    (100.0, 120.0, "sh688056", "莱伯泰科", "ZT"),   # 科创 20%
    (100.0, 110.05, "sh688056", "莱伯泰科", ""),    # 科创涨 10% 不是涨停
    (10.0, 10.5, "sz000001", "*ST 某某", "ZT"),     # ST 5%
    (20.91, 18.82, "sz000993", "闽东电力", "DT"),
    (None, 10.0, "sz000001", "", ""),
])
def test_price_limit_rebuilds_exact_limit_price(prev, close, code, name, expected):
    assert ndp._price_limit(prev, close, code, name) == expected


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    zt = tmp_path / "zt.csv"
    zt.write_text(
        "日期,类型,代码,名称,连板数\n"
        "20260923,ZT,sh601811,新华文轩,4.0\n"
        "20260923,ZT,sz000910,大亚圣象,4.0\n"
        "20260924,ZT,sh601811,新华文轩,5.0\n"
        "20260924,DT,sz000823,超声电子,\n",
        encoding="utf-8-sig")
    history = tmp_path / "history.jsonl"
    prev = {
        "event_type": "prediction", "report_date": "2026-09-23", "primary_scenario_id": "retreat",
        "market_thesis": {"breadth_relay_state": {"breadth_ratio": 0.34, "limit_up": 51, "limit_down": 13, "promotion_rate": 0.19}},
        "scenario_plans": [{
            "scenario_id": "retreat", "title": "高位退潮优先", "invalidation_conditions": ["跌停扩张"],
            "observation_pool": [
                {"code": "sh601811", "name": "新华文轩", "role": "confirm", "height": 4},
                {"code": "sz001317", "name": "三羊马", "role": "risk", "height": 3},
                {"code": "sz000910", "name": "大亚圣象", "role": "observation", "height": 4},
            ]}],
    }
    history.write_text(json.dumps(prev, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(ndp, "ZT_CACHE_FILE", str(zt))
    monkeypatch.setattr(ndp, "PREDICTION_HISTORY", str(history))
    monkeypatch.setattr(ndp, "PRICE_SLICE_DIR", str(tmp_path / "no_slices"))
    monkeypatch.setattr(ndp, "TACTICS_RECAP", str(tmp_path / "recap.json"))
    prices = pd.DataFrame([
        ("2026-09-23", "sh601811", 18.50), ("2026-09-24", "sh601811", 20.35),
        ("2026-09-23", "sz001317", 14.84), ("2026-09-24", "sz001317", 16.32),   # 涨停池漏了它
        ("2026-09-23", "sz000910", 10.00), ("2026-09-24", "sz000910", 9.50),
    ], columns=["date", "code", "close_raw"])
    return tmp_path, prices


def _context():
    return {
        "report_date": "2026-09-24", "target_trade_date": "2026-09-28",
        "market_thesis": {"breadth_relay_state": {"breadth_ratio": 0.2013, "limit_up": 52, "limit_down": 13, "promotion_rate": 0.255}},
        "scenario_plans": [{"scenario_id": "a", "title": "<b>退潮</b>", "position_ceiling": 0.2,
                            "auction_triggers": ["高位竞价低于预期不追"], "invalidation_conditions": ["跌停扩张"]},
                           {"scenario_id": "b", "title": "风险观察", "position_ceiling": 0.1}],
        "today_decision": {"position": "1 成", "default_action": "不开新仓",
                           "watch_items": [{"title": "市场开关", "headline": "当前不开新仓", "check": "9:35 前看宽度"}]},
    }


def test_scores_previous_plan_with_price_rebuilt_limits(sandbox):
    _, prices = sandbox
    plan = ndp.build_next_day_plan(_context(), [{"height": "5连板", "stocks": ["新华文轩"]}], prices)
    sc = plan["scorecard"]
    rows = {r["name"]: r for r in sc["rows"]}
    assert sc["prev_day"] == "2026-09-23"
    assert rows["新华文轩"]["status"] == "涨停 · 5 板"
    assert rows["三羊马"]["status"] == "涨停"            # 缓存漏票，价格重建补上
    assert rows["大亚圣象"]["status"] == "断板（昨 4 板）"
    assert rows["大亚圣象"]["key"] is False and rows["三羊马"]["key"] is True
    assert "3 只" in sc["summary"] and "2 只收涨" in sc["summary"]
    assert [m["label"] for m in sc["metrics"]] == ["上涨占比", "涨停家数", "跌停家数", "晋级率"]
    assert [a["name"] for a in plan["avoid"]] == ["大亚圣象"]
    assert plan["scenarios"][0]["primary"] and not plan["scenarios"][1]["primary"]
    assert plan["target_date"] == "2026-09-28"


def test_ai_beacons_need_same_day_real_facts(sandbox):
    tmp, prices = sandbox
    recap = {"report_date": "20260924", "tomorrow_plan": {"auction_beacons": [{"beacon": "竞价看新华文轩"}]}}
    (tmp / "recap.json").write_text(json.dumps(recap, ensure_ascii=False), encoding="utf-8")
    assert ndp.build_next_day_plan(_context(), [], prices)["ai_beacons"] == []   # 没有来源标记
    recap["facts_source"] = "report_context"
    (tmp / "recap.json").write_text(json.dumps(recap, ensure_ascii=False), encoding="utf-8")
    assert ndp.build_next_day_plan(_context(), [], prices)["ai_beacons"][0]["beacon"] == "竞价看新华文轩"


def test_plan_html_escapes_and_links_back_to_same_day_report(sandbox):
    _, prices = sandbox
    html = ndp.generate_plan_html(ndp.build_next_day_plan(_context(), [], prices))
    assert "<b>退潮</b>" not in html and "&lt;b&gt;退潮&lt;/b&gt;" in html
    assert 'href="../reports/2026-09-24.html"' in html
    for section in ("今日盘面事实", "明日三道开关", "情景推演", "不追名单", "昨日预案对账"):
        assert section in html


def test_collect_market_facts_uses_report_date(sandbox):
    _, prices = sandbox
    facts = ndp.collect_market_facts(_context(), [{"height": "5连板", "stocks": ["新华文轩"]}], prices)
    assert facts["trade_date"] == "2026-09-24" and facts["previous_trade_date"] == "2026-09-23"
    assert facts["breadth"]["dt_count"] == 13
    assert facts["breadth"]["zt_highest"] == {"names": ["新华文轩"], "height": 5}


def test_site_index_links_plan_only_when_published():
    from publish_site import _render_plan_entry
    assert _render_plan_entry(None) == ""
    assert 'href="plan/latest.html"' in _render_plan_entry("2026-09-24")
    assert ndp.render_plan_teaser_html("20260924").count("/plan/latest.html") == 1


def test_export_plan_standalone_reports(tmp_path):
    out_dir = str(tmp_path / "output")
    res = ndp.export_plan_standalone_reports("2026-09-28", output_dir=out_dir)
    assert "local_latest" in res and "site_latest" in res
    import os
    assert os.path.exists(res["local_latest"])
    assert os.path.exists(res["site_latest"])
    with open(res["local_latest"], encoding="utf-8") as f:
        local_content = f.read()
    assert "今日深度复盘 · 明日实战预案" in local_content
    assert "本地导航入口.html" in local_content
    with open(res["site_latest"], encoding="utf-8") as f:
        site_content = f.read()
    assert "../index.html" in site_content

