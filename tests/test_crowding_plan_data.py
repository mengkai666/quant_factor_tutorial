"""抱团预案数据层：日期隔离、缺失语义与动态候选选择。"""
from copy import deepcopy
from datetime import date, timedelta
import json

import pytest

from crowding_plan_data import build_crowding_plan_data


DAY = "2026-11-05"


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _dates(day=DAY, count=25):
    cursor, result = date.fromisoformat(day), []
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return sorted(result)


def _member(code, *, status="新团跟踪", identity="新候选/新团", active=True, qualified=True, streak=2):
    return {"code": code, "name": f"样本{code}", "identity": identity, "status": status,
            "active": active, "qualified_today": qualified, "support_streak": streak,
            "failed_streak": 0 if qualified else 4, "metrics_as_of": DAY, "evidence_ok": True,
            "metrics": {}, "reason": "来源原文"}


def _stock_payload(codes=None):
    codes = codes or [f"sz{i:06d}" for i in range(1, 12)]
    dates = _dates()
    rows = [_member(c) for c in codes]
    rows[-1].update(identity="旧团", status="已退出", active=False, qualified_today=False, support_streak=0, failed_streak=4)
    daily = {"date": DAY, "coverage_ok": True, "confirmed_proxy": codes[0], "proxy_state": "有效",
             "candidate_scores": [{"code": c, "score": 99 - i} for i, c in enumerate(codes[:-1])]}
    return {"requested_date": DAY, "market_as_of": DAY, "sources_as_of": {"prices": DAY},
            "tracking": {"requested_date": DAY, "daily": [{"date": DAY, "coverage_ok": True, "rows": rows}]},
            "daily": [{"date": dates[-2], "confirmed_proxy": codes[-1], "proxy_state": "有效"}, daily],
            "automatic_phases": {"latest": {"date": DAY, "stage": "聚焦强化", "confirmed_proxy": codes[0]}},
            "screening_replay": {"requested_date": DAY, "dates": dates,
                                 "prices": {c: [10 + j * .1 + i for j in range(len(dates))] for i, c in enumerate(codes)}}}


def _sector(name="题材甲", *, count=10, streak=3, day=DAY, code="sz100001"):
    return {"name": name, "limit_up_count": count, "streak_count": streak, "max_height": 4,
            "change": 2, "rank": 1, "stocks": [{"code": code, "name": "代表甲", "sub_sector": "子题材甲",
                                                 "source_date": day, "current_height": 3}]}


@pytest.fixture
def workspace(tmp_path):
    output, data = tmp_path / "output", tmp_path / "data"
    output.mkdir()
    data.mkdir()
    dates = _dates()[-3:]
    (data / "trading_calendar_cache.csv").write_text("trade_date\n" + "\n".join(dates), encoding="utf-8")
    for index, day in enumerate(dates):
        _write(output / "research_briefs" / f"research_brief_{day}.json",
               {"report_date": day, "sectors": [_sector(count=8 + index, day=day)]})
    payload = _stock_payload()
    _write(output / f"抱团周期分析_{DAY}.json", payload)
    return output, data, payload


def _build(workspace, **kwargs):
    output, data, _ = workspace
    return build_crowding_plan_data(DAY, output_dir=output, data_dir=data, **kwargs)


def test_metrics_are_date_aligned_and_old_core_survives_top_eight(workspace):
    result = _build(workspace)
    section = result["stocks"]
    assert section["status"] == "ok"
    assert len(section["rows"]) == 8
    core = section["rows"][0]
    assert core["code"] == "sz000001" and core["role"] == "core"
    assert core["close"] == 12.4
    assert core["ma5"] == pytest.approx(12.2)
    assert core["r10"] == pytest.approx((12.4 / 11.4 - 1) * 100, abs=.0001)
    assert core["r20"] == pytest.approx((12.4 / 10.4 - 1) * 100, abs=.0001)
    assert core["ranking"] == 1 and core["support_streak"] == 2
    assert core["price_basis"] == "qfq" and core["price_as_of"] == DAY
    risk = next(r for r in section["rows"] if r["code"] == "sz000011")
    assert risk["role"] == "risk" and risk["status"] == "已退出"
    assert risk["support_streak"] == 0 and risk["failed_streak"] == 4
    assert "前复权" in core["watch"] and "机构资金" in "".join(section["warnings"])


@pytest.mark.parametrize("field,value,expected", [
    ("requested_date", "2026-11-06", "invalid"),
    ("market_as_of", "2026-11-04", "stale"),
    ("market_as_of", "2026-11-06", "invalid"),
])
def test_mismatched_report_or_price_clock_never_creates_current_rows(workspace, field, value, expected):
    output, _, payload = workspace
    payload[field] = value
    _write(output / f"抱团周期分析_{DAY}.json", payload)
    result = _build(workspace)
    assert result["stocks"]["status"] == expected
    assert result["stocks"]["rows"] == []
    assert result["sources"][0]["status"] == expected


def test_stale_report_is_attributed_without_prices(tmp_path):
    output = tmp_path / "output"
    _write(output / "抱团周期分析_2026-11-04.json", {"requested_date": "2026-11-04", "market_as_of": "2026-11-04"})
    result = build_crowding_plan_data(DAY, output_dir=output, data_dir=tmp_path)
    assert result["stocks"]["status"] == "stale"
    assert result["stocks"]["as_of"] == "2026-11-04"
    assert not result["stocks"]["rows"]
    assert "2026-11-04" in result["sources"][0]["path"]


def test_future_file_is_not_read_or_used(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    # 故意放损坏 JSON：未来文件不得为了补当前报告而读取内容。
    (output / "抱团周期分析_2026-11-06.json").write_text("not-json", encoding="utf-8")
    result = build_crowding_plan_data(DAY, output_dir=output, data_dir=tmp_path)
    assert result["stocks"]["status"] == "invalid"
    assert "未来" in "".join(result["stocks"]["warnings"])
    assert not result["stocks"]["rows"]


def test_future_tracking_member_and_latest_phase_do_not_leak(workspace):
    output, _, payload = workspace
    payload["tracking"]["daily"][0]["rows"][1]["metrics_as_of"] = "2026-11-06"
    payload["automatic_phases"]["latest"] = {"date": "2026-11-06", "stage": "未来错误标签", "confirmed_proxy": "future"}
    payload["daily"].append({"date": "2026-11-06", "confirmed_proxy": "future", "proxy_state": "有效"})
    _write(output / f"抱团周期分析_{DAY}.json", payload)
    result = _build(workspace)
    assert all(row["code"] != "sz000002" for row in result["stocks"]["rows"])
    assert result["stocks"]["rows"][0]["code"] == "sz000001"
    assert "未来错误标签" not in result["stocks"]["summary"]


def test_future_price_tail_is_rejected_instead_of_using_last_price(workspace):
    output, _, payload = workspace
    payload["screening_replay"]["dates"].append("2026-11-06")
    for values in payload["screening_replay"]["prices"].values():
        values.append(99999)
    _write(output / f"抱团周期分析_{DAY}.json", payload)
    result = _build(workspace)
    assert result["stocks"]["status"] == "partial"
    assert all(r["close"] is None and r["ma5"] is None for r in result["stocks"]["rows"])
    assert all("99999" not in r["watch"] for r in result["stocks"]["rows"])


def test_incomplete_cross_section_has_no_active_list(workspace):
    output, _, payload = workspace
    payload["tracking"]["daily"][0]["coverage_ok"] = False
    _write(output / f"抱团周期分析_{DAY}.json", payload)
    result = _build(workspace)
    assert result["stocks"]["status"] == "partial" and not result["stocks"]["rows"]


def test_missing_metrics_stay_none_and_gaps_do_not_shorten_windows(workspace):
    output, _, payload = workspace
    row = payload["tracking"]["daily"][0]["rows"][0]
    row.pop("support_streak")
    payload["daily"][-1].pop("candidate_scores")
    payload["screening_replay"]["prices"][row["code"]][-3] = None
    _write(output / f"抱团周期分析_{DAY}.json", payload)
    core = _build(workspace)["stocks"]["rows"][0]
    assert core["close"] == 12.4
    assert core["ma5"] is None and core["r5"] is None and core["r10"] is None
    assert core["support_streak"] is None and core["ranking"] is None
    assert "0 日" not in core["reason"]


def test_price_metric_conflict_is_flagged_and_observation_price_suppressed(workspace):
    output, _, payload = workspace
    payload["tracking"]["daily"][0]["rows"][0]["metrics"] = {"r5": 500}
    _write(output / f"抱团周期分析_{DAY}.json", payload)
    core = _build(workspace)["stocks"]["rows"][0]
    assert core["price_status"] == "conflict" and core["close"] is None
    assert "冲突" in "".join(core["warnings"]) and "不提供观察价" in core["watch"]


def test_candidates_change_with_same_day_source_not_a_fixed_watchlist(workspace):
    output, _, payload = workspace
    first = _build(workspace)["stocks"]["rows"]
    replacement = _stock_payload([f"sh{i:06d}" for i in range(100, 111)])
    _write(output / f"抱团周期分析_{DAY}.json", replacement)
    second = _build(workspace)["stocks"]["rows"]
    assert not ({r["code"] for r in first} & {r["code"] for r in second})
    assert second[0]["code"] == "sh000100"
    assert any(r["code"] == "sh000110" and r["role"] == "risk" for r in second)


def test_active_old_core_risk_only_uses_one_of_eight_slots(workspace):
    output, _, payload = workspace
    payload["tracking"]["daily"][0]["rows"][-1].update(active=True, qualified_today=False, status="分歧观察")
    # 当前候选不足7个时，旧核心也不能重复出现。
    payload["tracking"]["daily"][0]["rows"] = payload["tracking"]["daily"][0]["rows"][:5] + payload["tracking"]["daily"][0]["rows"][-1:]
    _write(output / f"抱团周期分析_{DAY}.json", payload)
    rows = _build(workspace)["stocks"]["rows"]
    assert len(rows) == len({r["code"] for r in rows}) == 6
    assert rows[-1]["role"] == "risk"


def test_sector_history_preserves_missing_and_streak_count_means_members(workspace):
    output, _, _ = workspace
    yesterday = _dates()[-2]
    _write(output / "research_briefs" / f"research_brief_{yesterday}.json", {"report_date": yesterday, "sectors": []})
    section = _build(workspace)["sectors"]
    row = section["rows"][0]
    assert len(row["history"]) == 3
    assert row["history"][1]["limit_up_count"] is None
    assert row["history"][1]["status"] == "not_listed"
    assert row["streak_count"] == 3 and row["streak_count_label"] == "连板家数"
    assert "连板 3 家" in row["reason"] and "3 天" not in row["reason"]
    assert row["stocks"][0]["sub_sector"] == "子题材甲"
    assert "非全板块资金" in "".join(section["warnings"])


def test_sector_context_is_read_only_and_same_day_context_takes_priority(workspace):
    context = {"report_date": DAY, "research_brief": {"report_date": DAY, "sectors": [_sector("内存新题材", count=50)]}}
    original = deepcopy(context)
    result = _build(workspace, report_context=context)
    assert result["sectors"]["rows"][0]["name"] == "内存新题材"
    assert context == original
    assert any(s["path"] == "report_context.research_brief" and s["status"] == "ok" for s in result["sources"])


def test_future_context_and_historical_json_are_rejected(workspace):
    output, _, _ = workspace
    context = {"report_date": "2026-11-06", "research_brief": {"report_date": "2026-11-06", "sectors": [_sector("未来题材", count=999)]}}
    previous = _dates()[-2]
    _write(output / "research_briefs" / f"research_brief_{previous}.json", {"report_date": "2026-11-06", "sectors": [_sector(count=999)]})
    result = _build(workspace, report_context=context)
    assert result["sectors"]["rows"][0]["name"] == "题材甲"
    assert result["sectors"]["rows"][0]["history"][1]["limit_up_count"] is None
    assert any(s["status"] == "invalid" for s in result["sources"])
    assert "未来题材" not in json.dumps(result, ensure_ascii=False)


def test_future_nested_sector_history_does_not_leak_count(workspace):
    output, _, _ = workspace
    previous = _dates()[-2]
    row = _sector(count=999, day=previous)
    row["as_of"] = "2026-11-06"
    _write(output / "research_briefs" / f"research_brief_{previous}.json", {"report_date": previous, "sectors": [row]})
    result = _build(workspace)
    assert result["sectors"]["rows"][0]["history"][1]["limit_up_count"] is None


def test_sector_list_is_limited_and_changes_with_current_brief(workspace):
    sectors = [_sector(f"题材{i}", count=i, code=f"sz{i:06d}") for i in range(1, 8)]
    result = _build(workspace, report_context={"research_brief": {"report_date": DAY, "sectors": sectors}})
    rows = result["sectors"]["rows"]
    assert len(rows) == 5 and rows[0]["name"] == "题材7"
    assert {r["name"] for r in rows} == {"题材3", "题材4", "题材5", "题材6", "题材7"}


def test_future_sector_representatives_are_excluded_and_missing_count_not_zero(workspace):
    sector = _sector()
    sector.pop("streak_count")
    sector["stocks"][0]["source_date"] = "2026-11-06"
    result = _build(workspace, report_context={"research_brief": {"report_date": DAY, "sectors": [sector]}})
    row = result["sectors"]["rows"][0]
    assert row["streak_count"] is None and row["stocks"] == []
    assert result["sectors"]["status"] == "partial"


def test_missing_sources_are_explained_without_fabricated_rows(tmp_path):
    result = build_crowding_plan_data(DAY, output_dir=tmp_path, data_dir=tmp_path)
    assert result["stocks"]["status"] == result["sectors"]["status"] == "missing"
    assert result["stocks"]["rows"] == result["sectors"]["rows"] == []
    assert result["stocks"]["as_of"] is None and result["warnings"]


@pytest.mark.parametrize("day", [None, "../2026-11-05", "2026-02-30", "tomorrow"])
def test_invalid_date_is_not_used_for_path_selection(tmp_path, day):
    with pytest.raises(ValueError):
        build_crowding_plan_data(day, output_dir=tmp_path, data_dir=tmp_path)
