"""自动阶段只用当日及之前证据；不调用网络或人工案例名单。"""
from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest

from crowding_phases import build_automatic_phases, RULE_VERSION
from crowding_cycle import build_crowding_report


def fixture(proxies, scores=None):
    dates = pd.bdate_range("2026-09-01", periods=len(proxies) + 4).strftime("%Y-%m-%d").tolist()
    codes = list("ABCDEF")
    mats = {key: pd.DataFrame(value, index=dates, columns=codes, dtype=float)
            for key, value in dict(price=12, ma5=11, r1=1, r5=10, r20=20, dd20=-3, score=90).items()}
    rows = []
    for i, proxy in enumerate(proxies):
        candidates = scores[i] if scores else {"A": 99, "B": 97, "C": 95}
        rows.append(dict(date=dates[i + 4], coverage_ok=True, confirmed_proxy=proxy,
                         proxy_state="有效" if proxy else "失效", candidate_codes=list(candidates),
                         candidate_scores=[dict(code=c, score=s) for c, s in candidates.items()], pending_proxy=None))
    return rows, mats


def run(rows, mats, **kwargs):
    return build_automatic_phases(rows, mats, {}, window_start=kwargs.pop("window_start", rows[0]["date"]),
                                  requested_date=kwargs.pop("requested_date", rows[-1]["date"]), **kwargs)


def weaken(mats, date, codes="ABC", broken=True):
    for key, value in dict(price=8, ma5=11, r1=-4 if broken else 0, r5=-12, r20=-8, dd20=-30).items():
        mats[key].loc[date, list(codes)] = value


def test_confirmation_not_backdated_and_frozen_members_retained():
    rows, mats = fixture([None, "A", "A", "A"])
    result = run(rows, mats)
    assert [r["stage_id"] for r in result["daily"]] == ["formation", "formation", "focus", "focus"]
    focused = result["segments"][-1]
    assert focused["start"] == focused["confirmed_on"] == rows[2]["date"]
    assert focused["observed_since"] == rows[1]["date"]
    assert result["cohorts"][0]["codes"] == ["A", "B", "C"]
    rows[-1]["candidate_scores"] = [dict(code="D", score=100)]
    rows[-1]["candidate_codes"] = ["D"]
    assert run(rows, mats)["cohorts"][0]["codes"] == ["A", "B", "C"]


@pytest.mark.parametrize("missing", ["member", "cross_section", "proxy_expired"])
def test_missing_breaks_confirmation_and_does_not_claim_failure(missing):
    rows, mats = fixture(["A"] * 5)
    date = rows[1]["date"]
    if missing == "member":
        mats["r5"].at[date, "B"] = np.nan
    elif missing == "cross_section":
        rows[1]["coverage_ok"] = False
    else:
        rows[1].update(proxy_state="过期", confirmed_proxy=None)
    result = run(rows, mats)
    assert result["daily"][1]["stage_id"] == "insufficient"
    assert result["daily"][1]["evidence_as_of"] is None
    assert result["daily"][2]["stage_id"] == "formation"
    assert result["daily"][3]["stage_id"] == "focus"
    assert result["cohorts"][0]["core_failures"] == 0
    assert result["cohorts"][0]["codes"] == ["A", "B", "C"]


def test_break_clearance_repair_and_repeat_break_do_not_erase_history():
    rows, mats = fixture(["A"] * 11)
    weaken(mats, rows[2]["date"])
    for i in range(3, 6):
        weaken(mats, rows[i]["date"], broken=False)
    weaken(mats, rows[9]["date"])
    stages = [d["stage_id"] for d in run(rows, mats)["daily"]]
    assert stages[:6] == ["formation", "focus", "broken", "stabilizing", "clearance", "clearance"]
    assert stages[6:10] == ["repair_wait", "repair_wait", "repair", "broken"]
    result = run(rows, mats)
    assert len(result["cohorts"]) == 1
    assert result["cohorts"][0]["was_broken"]
    assert all(d["cohorts"][0]["was_broken"] for d in result["daily"][2:])


def test_internal_rotation_is_not_new_group_and_single_failure_not_breakdown():
    rows, mats = fixture(["A", "A", "B", "B", "B"])
    result = run(rows, mats)
    assert len(result["cohorts"]) == 1
    assert result["daily"][2]["stage_id"] == "rotation"
    assert result["daily"][4]["stage_id"] == "focus"
    assert any(e["kind"] == "within_basket_rotation" for e in result["events"])
    rows[2].update(confirmed_proxy=None, candidate_codes=["B", "C"])
    result = run(rows[:3], mats)
    assert result["latest"]["stage_id"] == "divergence"
    assert not result["cohorts"][0]["was_broken"]


def test_new_outside_group_can_coexist_with_healthy_old_group_and_return():
    scores = [{"A": 99, "B": 97, "C": 95}] * 2 + [{"D": 99, "E": 97, "F": 95}] * 2 + [{"A": 99, "B": 97, "C": 95}]
    rows, mats = fixture(["A", "A", "D", "D", "A"], scores)
    result = run(rows, mats)
    assert len(result["cohorts"]) == 2
    assert result["daily"][2]["stage_id"] == "trial"
    assert result["daily"][2]["cohorts"][0]["stage_id"] == "focus"
    assert result["daily"][2]["parallel_cohort_ids"] == [result["cohorts"][0]["id"]]
    assert result["daily"][4]["active_cohort_id"] == result["cohorts"][0]["id"]
    assert any(e["kind"] == "old_basket_return" for e in result["events"])


def test_parallel_old_risk_stays_visible_during_new_trial():
    scores = [{"A": 99, "B": 97, "C": 95}] * 2 + [{"D": 99, "E": 97, "F": 95}] * 2
    rows, mats = fixture(["A", "A", "D", "D"], scores)
    weaken(mats, rows[2]["date"])
    result = run(rows, mats)
    assert result["daily"][2]["stage_id"] == "trial"
    assert result["daily"][2]["parallel_risk_count"] == 1
    assert result["daily"][2]["cohorts"][0]["stage_id"] == "broken"


def test_future_inputs_cannot_change_any_past_daily_state():
    rows, mats = fixture(["A"] * 5)
    short = run(rows, mats, requested_date=rows[2]["date"])
    future = deepcopy(mats)
    for frame in future.values():
        frame.loc[frame.index > rows[2]["date"]] = 99999
    later = run(rows, future)
    assert short["daily"] == later["daily"][:3]
    assert short["cohorts"][0]["codes"] == later["cohorts"][0]["codes"]
    assert all(e["date"] <= rows[2]["date"] for e in short["events"])
    json.dumps(short, allow_nan=False)


def test_clipping_does_not_reset_phase_and_segments_cover_every_day_once():
    rows, mats = fixture(["A"] * 8)
    rows[4]["coverage_ok"] = False
    result = run(rows, mats, window_start=rows[3]["date"])
    assert result["daily"] == run(rows, mats)["daily"][3:]
    assert result["segments"][0]["origin_start"] == rows[1]["date"]
    assert result["segments"][0]["left_censored"]
    dates = [r["date"] for r in result["daily"]]
    assert [d for s in result["segments"] for d in dates if s["start"] <= d <= s["end"]] == dates
    assert [s["stage_id"] for s in result["segments"]] == ["focus", "insufficient", "formation", "focus"]


def test_single_member_unknown_calendar_and_empty_input_cannot_upgrade():
    rows, mats = fixture(["A"] * 3, [{"A": 99}] * 3)
    assert all(r["stage_id"] == "insufficient" for r in run(rows, mats)["daily"])
    assert all(r["stage_id"] == "insufficient" for r in run(rows, mats, calendar_known=False)["daily"])
    empty = build_automatic_phases([], None, {}, window_start="2026-09-01", requested_date="2026-10-25")
    assert empty["latest"] is None and empty["segments"] == []


def test_integrated_layer_is_independent_of_manual_dates_and_does_not_change_execution(monkeypatch):
    dates = pd.bdate_range("2026-07-01", "2026-10-23").strftime("%Y-%m-%d").tolist()
    prices = pd.DataFrame([dict(date=d, code=c, close_qfq=10 * (1 + growth) ** i)
                           for i, d in enumerate(dates) for c, growth in [("sh600127", .02), ("sh601086", .015), ("sh600865", .01)]])
    args = dict(prices=prices, limits=pd.DataFrame(), sentiment=pd.DataFrame(), calendar=dates, quotes={})
    first = build_crowding_report("2026-10-25", **args)
    monkeypatch.setattr("crowding_cycle.PERIODS", [])
    monkeypatch.setattr("crowding_cycle.GROUPS", [])
    # 历史GROUPS渲染有独立固定案例契约；仅屏蔽reference层，测试自动输入未依赖人工名单。
    monkeypatch.setattr("crowding_cycle.REFERENCE_DATE", "2099-01-01")
    second = build_crowding_report("2026-10-25", **args)
    assert first["automatic_phases"] == second["automatic_phases"]
    assert first["execution"] == second["execution"]
    assert first["automatic_phases"]["rule_version"] == RULE_VERSION
    assert first["market_as_of"] == "2026-10-23"
    assert [d["date"] for d in first["automatic_phases"]["daily"]] == [d["date"] for d in first["daily"]]


def test_view_escapes_names_and_exposes_confirmation_and_independent_history():
    from crowding_phases_view import render_automatic_phases, automatic_phases_markdown, PHASE_SCRIPT
    from crowding_view import generate_crowding_html, generate_crowding_markdown
    rows, mats = fixture(["A"] * 3)
    result = run(rows, mats)
    result['cohorts'][0]['anchor_name'] = '<script>alert(1)</script>'
    page = render_automatic_phases(result)
    assert '<script>alert(1)</script>' not in page
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page
    for text in ('自动时间阶段划分', '首次观察到', '规则确认', '并行旧篮子', '不剔除失败者', '出清观察≠出清完成'):
        assert text in page
    # 与其他独立view一致，脚本片段插入主页面已有script，不可嵌套标签。
    assert '<script' not in PHASE_SCRIPT and '</script>' not in PHASE_SCRIPT
    assert 'row.dataset.start>date' in PHASE_SCRIPT
    assert 'row.dataset.next<=date' in PHASE_SCRIPT
    assert 'textContent' in PHASE_SCRIPT and 'innerHTML' not in PHASE_SCRIPT
    assert any('自动时间阶段划分' in line for line in automatic_phases_markdown(result))
    dates = pd.bdate_range('2026-07-01', '2026-09-30').strftime('%Y-%m-%d').tolist()
    prices = pd.DataFrame([dict(date=d, code=c, close_qfq=10 * 1.02**i) for i, d in enumerate(dates)
                           for c in ('sh600127', 'sh601086', 'sh600865')])
    report = build_crowding_report('2026-09-30', prices=prices, limits=pd.DataFrame(), sentiment=pd.DataFrame(), calendar=dates, quotes={})
    full = generate_crowding_html(report)
    assert 'href="#automatic-phases"' in full and 'id="automatic-phases"' in full
    assert 'auto-phase-date' in full
    from html.parser import HTMLParser

    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_script = False
            self.blocks = []

        def handle_starttag(self, tag, attrs):
            if tag == 'script':
                self.in_script = True
                self.blocks.append('')

        def handle_endtag(self, tag):
            if tag == 'script':
                self.in_script = False

        def handle_data(self, text):
            if self.in_script:
                self.blocks[-1] += text

    parser = Scripts()
    parser.feed(full)
    assert any(PHASE_SCRIPT in block for block in parser.blocks)
    assert all('<script' not in block for block in parser.blocks)
    assert '自动时间阶段划分' in generate_crowding_markdown(report)
    report.pop('automatic_phases')
    assert 'id="automatic-phases"' not in generate_crowding_html(report)


def test_absent_candidates_wait_and_pressure_needs_group_weakness():
    rows, mats = fixture([None, None], [{}, {}])
    assert all(r['stage_id'] == 'waiting' for r in run(rows, mats)['daily'])
    rows, mats = fixture(['A'] * 3)
    for date in (rows[1]['date'], rows[2]['date']):
        weaken(mats, date, codes='AB', broken=False)
    result = run(rows, mats)
    assert result['daily'][1]['stage_id'] == 'pressure'
    assert not result['cohorts'][0]['was_broken']


def test_missing_after_break_resets_stop_and_repair_streaks():
    rows, mats = fixture(['A'] * 9)
    weaken(mats, rows[1]['date'])
    for i in (2, 3, 4):
        weaken(mats, rows[i]['date'], broken=False)
    rows[3]['coverage_ok'] = False
    rows[6]['coverage_ok'] = False
    result = run(rows, mats)
    assert result['daily'][4]['stage_id'] == 'stabilizing'
    assert result['daily'][5]['stage_id'] == 'repair_wait'
    assert result['daily'][7]['stage_id'] == 'repair_wait'
    assert result['daily'][8]['stage_id'] == 'repair_wait'
    assert result['cohorts'][0]['was_broken']


def test_old_missing_proxy_does_not_gain_failure_on_new_basket_confirmation():
    scores = [{'A': 99, 'B': 97, 'C': 95}] + [{'D': 99, 'E': 97, 'F': 95}] * 3
    rows, mats = fixture(['A', None, 'D', 'D'], scores)
    rows[1]['proxy_state'] = '过期'
    for date in (rows[1]['date'], rows[2]['date']):
        mats['r5'].at[date, 'A'] = np.nan
    result = run(rows, mats)
    assert result['cohorts'][0]['core_failures'] == 0
    assert result['daily'][2]['stage_id'] == 'trial'
    assert result['daily'][2]['cohorts'][0]['stage_id'] == 'insufficient'


def test_break_during_rotation_does_not_mask_old_basket_recovery():
    rows, mats = fixture(['A', 'A', 'B', 'B', 'B', 'B', 'B'])
    weaken(mats, rows[3]['date'])
    result = run(rows, mats)
    assert [d['stage_id'] for d in result['daily'][2:]] == [
        'rotation', 'broken', 'repair_wait', 'repair_wait', 'repair']
    assert result['cohorts'][0]['was_broken']
    assert result['cohorts'][0]['rotation_days'] == 0
