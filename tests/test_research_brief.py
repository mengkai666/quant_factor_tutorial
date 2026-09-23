from copy import deepcopy
import pytest

DAY = "2026-09-07"
DAYS = ["2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", DAY]

def stock(code, sector="算力", height=1, name=None, day=DAY, **extra):
    return {"code": code, "name": name or ("股票" + code[-3:]), "大主线": sector,
            "细分板块": sector + "设备", "height": height, "trade_date": day,
            "pool_type": "ZT", "amount": 100_000_000, "first_limit_time": "093000",
            "source": "fixture", "source_timestamp": day+"T16:00:00+08:00", **extra}

def context(rows):
    return {"report_date": DAY, "target_trade_date": "2026-09-08",
            "quality": {"publication_mode": "observation", "strategy_qualification": {
                "eligible_strategy_ids": [], "strategies": {"watch": {"plan_permitted": False}}}},
            "facts": {"market_snapshot": {"report_date": DAY, "date_verified": True,
                "limit_up": len(rows), "limit_down": 2, "max_height": max([r["height"] for r in rows] or [0]),
                "breadth_ratio": .6, "limit_pool_rows": rows}}}

def prices(rows, day=DAY):
    return [{"code": r["code"], "date": day, "close_raw": 11.0, "close_qfq": 11.0,
             "source": "fixture_price", "source_timestamp": day+"T16:00:00+08:00"} for r in rows]

def build(ctx, **kwargs):
    from research_brief import build_research_brief
    return build_research_brief(ctx, **kwargs)

def test_multiple_sectors_each_have_three_despite_unverified_trading_strategy():
    rows = [stock(f"sz00000{i}", "算力" if i <= 4 else "机器人", height=(i-1)%4+1) for i in range(1,9)]
    ctx = context(rows); before = deepcopy(ctx)
    brief = build(ctx, price_rows=prices(rows), trading_days=DAYS)
    assert brief["purpose"] == "research_observation"
    assert len(brief["sectors"]) == 2
    assert [len(s["stocks"]) for s in brief["sectors"]] == [3, 3]
    assert {r["code"] for s in brief["sectors"] for r in s["stocks"]} == {"sz000002","sz000003","sz000004","sz000006","sz000007","sz000008"}
    assert ctx == before
    assert "plan_permitted" not in brief


def test_default_sector_count_is_five_and_miscellaneous_is_not_a_sector_pick():
    rows = [stock(f"sz{i:06d}", "板块"+str(i//4), height=i%4+1) for i in range(1,25)]
    rows += [stock("sz000099", "其它", height=9)]
    brief = build(context(rows), price_rows=prices(rows), trading_days=DAYS)
    assert len(brief["sectors"]) == 5
    assert all(s["name"] != "其它" and len(s["stocks"]) == 3 for s in brief["sectors"])
    codes = [r["code"] for s in brief["sectors"] for r in s["stocks"]]
    assert len(codes) == len(set(codes))


def test_st_and_missing_or_wrong_day_prices_are_not_used_to_fill_three():
    rows = [stock("sz000001",height=5,name="ST风险"),stock("sz000002",height=4),stock("sz000003",height=3),stock("sz000004",height=2)]
    p = prices(rows)
    p[1]["date"] = "2026-09-04"
    p[2]["close_raw"] = float("nan")
    brief = build(context(rows), price_rows=p, trading_days=DAYS)
    assert [r["code"] for s in brief["sectors"] for r in s["stocks"]] == ["sz000004"]
    assert brief["sectors"][0]["limit_up_count"] == 4


def test_multi_board_table_distinguishes_streak_repeated_limits_and_old_leader():
    rows = [stock("sz000001",height=3),stock("sz000002",height=1),stock("sz000003",height=1)]
    old = stock("sz000099",height=4,day="2026-09-02",name="近期高标")
    history = [stock("sz000001",height=1,day="2026-09-03"),stock("sz000001",height=2,day="2026-09-04"),
               stock("sz000002",height=1,day="2026-09-02"),old]
    brief = build(context(rows), price_rows=prices(rows+[old]), history_rows=history,
                  trading_days=DAYS, history_days=DAYS[1:])
    recent = {r["code"]:r for r in brief["recent"]["stocks"]}
    assert brief["recent"]["window_days"] == DAYS[1:]
    assert brief["recent"]["complete"] is True
    assert recent["sz000001"]["current_height"] == 3
    assert recent["sz000001"]["limit_up_count"] == 3
    assert recent["sz000002"]["current_height"] == 1
    assert recent["sz000002"]["limit_up_count"] == 2
    assert recent["sz000099"]["state"] == "今日未涨停"
    assert recent["sz000099"]["peak_height"] == 4
    assert recent["sz000099"]["last_limit_date"] == "2026-09-02"
    assert "sz000003" not in recent


def test_recent_window_ignores_future_and_old_rows_and_deduplicates_dates():
    rows = [stock("sz000001",height=1)]
    observed = stock("sz000001",height=1,day="2026-09-02")
    history = [observed,deepcopy(observed),stock("sz000001",height=99,day="2026-09-08"),stock("sz000001",height=20,day="2026-08-31")]
    brief = build(context(rows), price_rows=prices(rows), history_rows=history,trading_days=DAYS,history_days=DAYS[1:])
    r = brief["recent"]["stocks"][0]
    assert r["peak_height"] == 1 and r["limit_up_count"] == 2
    assert r["last_limit_date"] == DAY
    assert len(r["trajectory"]) == 5


def test_five_day_return_uses_exact_qfq_calendar_endpoints_not_raw_or_nearby_day():
    rows=[stock("sz000001"),stock("sz000002")]
    p=prices(rows)
    p[0].update(close_raw=5.5,close_qfq=11.0)
    p += [{"code":"sz000001","date":DAYS[0],"close_raw":100.0,"close_qfq":10.0},
          {"code":"sz000002","date":DAYS[1],"close_raw":1.0,"close_qfq":1.0}]
    brief=build(context(rows),price_rows=p,trading_days=DAYS)
    stocks={r["code"]:r for s in brief["sectors"] for r in s["stocks"]}
    assert stocks["sz000001"]["pct_5d"] == pytest.approx(10.0)
    assert stocks["sz000001"]["close"] == 5.5
    assert stocks["sz000002"]["pct_5d"] is None


def test_raw_ohlc_reference_conflict_cannot_become_a_candidate_price():
    rows=[stock("sz000001",height=3),stock("sz000002")]
    bars=[{"code":"sz000001","date":DAY,"open_raw":8.,"high_raw":8.,"low_raw":8.,"close_raw":8.,
           "price_basis":"raw","source":"fixture_bar","source_timestamp":DAY+"T16:00:00+08:00"}]
    brief=build(context(rows),price_rows=prices(rows),raw_bars=bars,trading_days=DAYS)
    assert [r["code"] for s in brief["sectors"] for r in s["stocks"]] == ["sz000002"]


@pytest.mark.parametrize("bad",["snapshot_date","row_date","duplicate_code","count"])
def test_invalid_core_identity_or_date_refuses_a_misleading_new_report(bad):
    rows=[stock("sz000001")];ctx=context(rows)
    if bad=="snapshot_date":ctx["facts"]["market_snapshot"]["report_date"]="2026-09-04"
    if bad=="row_date":ctx["facts"]["market_snapshot"]["limit_pool_rows"][0]["trade_date"]="2026-09-04"
    if bad=="duplicate_code":ctx["facts"]["market_snapshot"]["limit_pool_rows"].append(deepcopy(rows[0]))
    if bad=="count":ctx["facts"]["market_snapshot"]["limit_up"]=2
    with pytest.raises(ValueError):build(ctx,price_rows=prices(rows),trading_days=DAYS)


def test_missing_historical_day_is_not_silently_counted_as_no_limit_up():
    rows=[stock("sz000001",height=2)]
    brief=build(context(rows),price_rows=prices(rows),trading_days=DAYS,history_days=[DAY])
    recent=brief["recent"]
    assert recent["complete"] is False
    assert recent["coverage_days"] == [DAY]
    assert recent["stocks"][0]["trajectory"][0]["limit_up"] is None


def test_unclassified_previous_snapshot_does_not_imply_zero_sector_activity():
    rows=[stock("sz000001",height=2)]
    old={"code":"sz000001","name":"股票001","trade_date":"2026-09-04","height":1}
    brief=build(context(rows),price_rows=prices(rows),history_rows=[old],trading_days=DAYS,history_days=DAYS[1:])
    assert brief["sectors"][0]["previous_limit_up_count"] is None
    assert brief["sectors"][0]["change"] is None


def test_recent_display_limit_does_not_discard_the_remaining_observed_stocks():
    rows=[stock(f"sz{i:06d}",height=2) for i in range(1,8)]
    brief=build(context(rows),price_rows=prices(rows),trading_days=DAYS,recent_limit=3)
    assert brief["recent"]["total_count"]==7
    assert len(brief["recent"]["stocks"])==3
    assert len(brief["recent"]["more_stocks"])==4
    assert {r["code"] for r in brief["recent"]["stocks"]+brief["recent"]["more_stocks"]}=={r["code"] for r in rows}


def test_market_change_uses_the_exact_previous_trading_day_snapshot():
    rows=[stock("sz000001",height=2)]
    ctx=context(rows)
    previous=[stock("sz000002",day="2026-09-04"),stock("sz000003",day="2026-09-04")]
    ctx["facts"]["previous_market_snapshot"]={"report_date":"2026-09-04","date_verified":True,
        "limit_up":2,"limit_down":3,"limit_pool_rows":previous}
    brief=build(ctx,price_rows=prices(rows),trading_days=DAYS)
    assert brief["market"]["comparison"]["report_date"]=="2026-09-04"
    assert brief["market"]["comparison"]["limit_up_change"]==-1
    assert any("减少1只" in s for s in brief["market"]["narrative"])
    ctx["facts"]["previous_market_snapshot"]["report_date"]="2026-09-08"
    assert build(ctx,price_rows=prices(rows),trading_days=DAYS)["market"]["comparison"] is None


def test_fractional_decline_count_is_not_truncated_into_a_stock_count():
    rows=[stock("sz000001")];ctx=context(rows)
    ctx["facts"]["market_snapshot"]["limit_down"]=1.5
    brief=build(ctx,price_rows=prices(rows),trading_days=DAYS)
    assert brief["market"]["limit_down"] is None
    assert not any("跌停1只" in s for s in brief["market"]["narrative"])


def test_conflicting_price_dates_cannot_admit_a_future_labeled_price():
    rows=[stock('sz000001',height=3),stock('sz000002')]
    p=prices(rows)
    p[0].update(date='2026-09-08',trade_date=DAY,close_raw=99.,close_qfq=99.)
    brief=build(context(rows),price_rows=p,trading_days=DAYS)
    assert [r['code'] for s in brief['sectors'] for r in s['stocks']]==['sz000002']


def test_qfq_endpoint_with_conflicting_date_alias_is_not_used():
    rows=[stock('sz000001')]
    p=prices(rows)+[{'code':'sz000001','date':DAYS[0],'report_date':DAYS[1],'close_qfq':10.,'close_raw':10.}]
    brief=build(context(rows),price_rows=p,trading_days=DAYS)
    assert brief['sectors'][0]['stocks'][0]['pct_5d'] is None
    p=prices(rows);p[0]['trade_date']=DAY.replace('-','')
    assert build(context(rows),price_rows=p,trading_days=DAYS)['sectors'][0]['stocks'][0]['close']==11.


def test_conflicting_raw_bar_business_dates_cannot_supply_board_shape():
    rows=[stock('sz000001')]
    bar={'code':'sz000001','date':DAY,'trade_date':'2026-09-08','open_raw':11.,'high_raw':11.,
         'low_raw':11.,'close_raw':11.,'price_basis':'raw','source':'fixture','source_timestamp':DAY+'T16:00:00+08:00'}
    brief=build(context(rows),price_rows=prices(rows),raw_bars=[bar],trading_days=DAYS)
    assert brief['sectors'][0]['stocks'][0]['board_type'] is None


@pytest.mark.parametrize('kind',['none','blank','nan','pdna','nat'])
def test_missing_optional_date_alias_does_not_discard_a_known_same_day_price(kind):
    import pandas as pd
    absent={'none':None,'blank':'','nan':float('nan'),'pdna':pd.NA,'nat':pd.NaT}[kind]
    rows=[stock('sz000001')]
    p=prices(rows);p[0]['report_date']=absent
    assert build(context(rows),price_rows=p,trading_days=DAYS)['sectors'][0]['stocks'][0]['close']==11.
