# Micro Cycle Resonance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an automatically detected post-bottom micro cycle, identify sector/mainline resonance after its signal date, and show the corresponding leading stocks in the permanent report template.

**Architecture:** Keep the existing major phase detector unchanged and add pure calculations in a focused `micro_cycle.py` module. `phase_resonance.py` remains the orchestration and HTML boundary: it loads existing caches, calls the pure builders independently, and degrades the new section without blocking the report.

**Tech Stack:** Python 3, pandas, server-rendered HTML/CSS, pytest, Playwright CLI.

## Global Constraints

- Never hard-code the 2026 example dates; derive every event from the ordered index series.
- Keep `phase_resonance.detect_phases()` and the existing major turning-point summary behavior unchanged.
- Treat 2026-08-04 as a signal and 2026-08-05 as a close confirmation in the fixture; preserve both concepts in future reports.
- Compute the signal-date cohort from per-day limit-up fact membership; never use upstream `连板数` to infer the chain.
- Use CLS attribution before Eastmoney attribution and never fabricate attribution for missing codes.
- Rank leader returns with the shared stitched qfq matrix and resolve current names from the security master.
- A micro-cycle, limit-chain, sector, attribution, or price failure must not block the existing report.
- Render a flat layout without cards inside cards and prevent horizontal overflow at 390px.
- Do not add a new external data source, generate dates with AI, or write historical immutable snapshots.

---

### Task 1: Detect The Post-Bottom Micro Cycle

**Files:**
- Create: `src/micro_cycle.py`
- Create: `tests/test_micro_cycle.py`

**Interfaces:**
- Consumes: `det` containing `bottom` and an ascending `index_series`; optional `{YYYY-MM-DD: limit_up_count}`.
- Produces: `detect_micro_cycle(det: dict, *, daily_limit_counts: dict[str, int] | None = None) -> dict`.
- Returns: `status`, `events`, `signal_date`, `confirmation_date`, `full_confirmation_date`, `signal_return`, `rising_days`, and `signal_basis`.

- [ ] **Step 1: Write the failing event-sequence test**

Add a fixture and test to `tests/test_micro_cycle.py`:

```python
import pytest


def _index_fixture():
    values = [
        ("2026-07-17", 3745.174, 3869.215, 3764.155),
        ("2026-07-20", 3741.110, 3831.659, 3796.281),
        ("2026-07-21", 3743.360, 3864.600, 3864.367),
        ("2026-07-22", 3839.665, 3884.435, 3867.034),
        ("2026-07-23", 3851.706, 3878.832, 3876.777),
        ("2026-07-24", 3808.636, 3861.040, 3814.198),
        ("2026-07-27", 3793.449, 3858.310, 3858.245),
        ("2026-07-28", 3797.373, 3844.012, 3813.315),
        ("2026-07-29", 3782.481, 3845.766, 3828.469),
        ("2026-07-30", 3767.503, 3839.341, 3804.693),
        ("2026-07-31", 3822.374, 3847.093, 3832.262),
        ("2026-08-03", 3797.643, 3827.636, 3809.663),
        ("2026-08-04", 3799.524, 3831.940, 3822.285),
        ("2026-08-05", 3815.122, 3884.397, 3878.430),
        ("2026-08-06", 3864.273, 3902.054, 3900.352),
        ("2026-08-07", 3885.625, 3940.935, 3940.037),
    ]
    return [
        {"date": date, "low": low, "high": high, "close": close}
        for date, low, high, close in values
    ]


def test_detect_micro_cycle_separates_signal_close_confirmation_and_full_breakout():
    from micro_cycle import detect_micro_cycle

    det = {
        "bottom": {"date": "2026-07-17", "close": 3764.155},
        "index_series": _index_fixture(),
    }
    result = detect_micro_cycle(
        det,
        daily_limit_counts={"2026-08-03": 101, "2026-08-04": 140},
    )

    assert result["events"]["final_stop"]["date"] == "2026-07-20"
    assert result["events"]["rebound_high"]["high_date"] == "2026-07-22"
    assert result["events"]["rebound_high"]["close_date"] == "2026-07-23"
    assert result["events"]["secondary_bottom"]["date"] == "2026-07-30"
    assert result["events"]["secondary_bottom"]["higher_low"] is True
    assert result["events"]["retest"]["date"] == "2026-08-03"
    assert result["signal_date"] == "2026-08-04"
    assert result["confirmation_date"] == "2026-08-05"
    assert result["full_confirmation_date"] == "2026-08-06"
    assert result["status"] == "小周期主升"
    assert result["rising_days"] == 4
    assert result["signal_return"] == pytest.approx(3.08, abs=0.01)
    assert result["signal_basis"] == "price+limit_pool"
```

- [ ] **Step 2: Write the failing no-breakout test**

```python
def test_detect_micro_cycle_does_not_claim_turn_up_before_rebound_high_breaks():
    from micro_cycle import detect_micro_cycle

    rows = _index_fixture()[:-3]
    rows[-1] = {**rows[-1], "close": 3822.285, "high": 3831.940}
    result = detect_micro_cycle({
        "bottom": {"date": "2026-07-17", "close": 3764.155},
        "index_series": rows,
    })

    assert result["confirmation_date"] == ""
    assert result["status"] == "震荡筑底"
    assert result["signal_basis"] == "price_only"
```

- [ ] **Step 3: Run both tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_micro_cycle.py -k "detect_micro_cycle"
```

Expected: import failure because `micro_cycle` does not exist.

- [ ] **Step 4: Implement the pure detector**

Create `src/micro_cycle.py` with these helpers and contract:

```python
from __future__ import annotations

from typing import Any


def _after(rows, date):
    return [row for row in rows if str(row.get("date", "")) > date]


def _event(date="", **values):
    return {"date": date, **values}


def detect_micro_cycle(det: dict, *, daily_limit_counts=None) -> dict:
    rows = sorted((det or {}).get("index_series") or [], key=lambda row: row["date"])
    bottom_date = str(((det or {}).get("bottom") or {}).get("date") or "")
    start = next((i for i, row in enumerate(rows) if row["date"] == bottom_date), -1)
    empty = {
        "status": "探底未完成", "events": {}, "signal_date": "",
        "confirmation_date": "", "full_confirmation_date": "",
        "signal_return": None, "rising_days": 0, "signal_basis": "unavailable",
    }
    if start < 0 or len(rows) - start < 8:
        return empty

    stop_slice = rows[start:start + 3]
    stop = min(stop_slice, key=lambda row: float(row["low"]))
    stop_i = rows.index(stop)
    rebound_slice = rows[stop_i + 1:stop_i + 6]
    if len(rebound_slice) < 3:
        return empty
    high_peak = max(rebound_slice, key=lambda row: float(row["high"]))
    close_peak = max(rebound_slice, key=lambda row: float(row["close"]))
    close_peak_i = rows.index(close_peak)

    after_rebound = rows[close_peak_i + 1:]
    confirmation = next(
        (row for row in after_rebound if float(row["close"]) > float(close_peak["close"])),
        None,
    )
    search_end = rows.index(confirmation) if confirmation else len(rows)
    pullback_rows = rows[close_peak_i + 1:search_end]
    if not pullback_rows:
        return empty
    secondary = min(pullback_rows, key=lambda row: float(row["low"]))
    higher_low = float(secondary["low"]) > float(stop["low"])

    secondary_i = rows.index(secondary)
    retest_rows = rows[secondary_i + 1:search_end]
    retest = min(retest_rows, key=lambda row: float(row["close"])) if retest_rows else secondary
    retest_i = rows.index(retest)
    candidates = rows[retest_i + 1:search_end + (1 if confirmation else 0)]
    price_signal = next((
        row for row in candidates
        if float(row["close"]) > float(rows[rows.index(row) - 1]["close"])
        and float(row["low"]) >= float(retest["low"])
    ), None)

    signal = price_signal
    basis = "price_only" if signal else "unavailable"
    counts = daily_limit_counts or {}
    if signal and counts:
        previous = rows[rows.index(signal) - 1]["date"]
        if counts.get(signal["date"], 0) > counts.get(previous, 0):
            basis = "price+limit_pool"
        else:
            signal = None
            basis = "unavailable"

    full = next((
        row for row in after_rebound
        if float(row["high"]) > float(high_peak["high"])
        and float(row["close"]) > float(close_peak["close"])
    ), None)
    signal_i = rows.index(signal) if signal else -1
    signal_rows = rows[signal_i:] if signal_i >= 0 else []
    rising_days = 1
    for previous, current in zip(signal_rows, signal_rows[1:]):
        if float(current["close"]) > float(previous["close"]):
            rising_days += 1
        else:
            rising_days = 1
    status = "探底未完成" if not higher_low else "震荡筑底"
    if confirmation:
        status = "小周期主升" if signal and rising_days >= 4 else "震荡转升"
    signal_return = (
        round((float(rows[-1]["close"]) / float(signal["close"]) - 1) * 100, 2)
        if signal else None
    )
    return {
        "status": status,
        "events": {
            "final_stop": _event(stop["date"], low=float(stop["low"])),
            "rebound_high": {
                "high_date": high_peak["date"], "high": float(high_peak["high"]),
                "close_date": close_peak["date"], "close": float(close_peak["close"]),
            },
            "secondary_bottom": _event(
                secondary["date"], low=float(secondary["low"]), higher_low=higher_low,
            ),
            "retest": _event(retest["date"], close=float(retest["close"])),
        },
        "signal_date": signal["date"] if signal else "",
        "confirmation_date": confirmation["date"] if confirmation else "",
        "full_confirmation_date": full["date"] if full else "",
        "signal_return": signal_return,
        "rising_days": rising_days if signal else 0,
        "signal_basis": basis,
    }
```

Keep the implementation small; extract helpers only when the test makes a branch unclear. Do not add indicators such as MACD or moving averages.

- [ ] **Step 5: Run the detector tests and verify GREEN**

```powershell
python -m pytest -q tests/test_micro_cycle.py -k "detect_micro_cycle"
```

Expected: both tests pass.

- [ ] **Step 6: Commit the detector**

```powershell
git add src/micro_cycle.py tests/test_micro_cycle.py
git diff --cached --check
git commit -m "feat: detect post-bottom micro cycle"
```

---

### Task 2: Build The Signal-Date Limit-Up Cohort

**Files:**
- Modify: `src/micro_cycle.py`
- Modify: `tests/test_micro_cycle.py`

**Interfaces:**
- Consumes: historical ZT fact rows, ordered report trading dates, the signal and latest dates, a `NameResolution`, and immutable snapshot dates.
- Produces: `build_signal_limit_chain(...) -> dict` with `usable`, `status`, `dates`, `consecutive_days`, `rows`, and `hint`.
- Ignores: every upstream height or `连板数` value.

- [ ] **Step 1: Write the failing code-intersection test**

```python
import pandas as pd
from data_sources.name_resolver import NameResolution


def test_signal_limit_chain_uses_fact_membership_and_excludes_prior_limit_ups():
    from micro_cycle import build_signal_limit_chain

    starters = ["sh600721", "sh600892", "sh603773", "sz002425", "sz002428", "sz002552", "sz002975"]
    rows = []
    for date in ("20260804", "20260805", "20260806", "20260807"):
        for code in starters + ["sz002963"]:
            rows.append({"日期": date, "类型": "ZT", "代码": code, "连板数": 3})
    rows.extend([
        {"日期": "20260803", "类型": "ZT", "代码": "sz002963", "连板数": 1},
        {"日期": "20260804", "类型": "DT", "代码": "sh600001", "连板数": 0},
    ])
    names = NameResolution(
        names={code: f"股票{index}" for index, code in enumerate(starters, 1)} | {"sz002963": "豪尔赛"},
        sources={}, conflicts=[],
    )

    result = build_signal_limit_chain(
        pd.DataFrame(rows),
        ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"],
        "2026-08-04", "2026-08-07",
        names=names,
        immutable_dates={"2026-08-07"},
    )

    assert result["usable"] is True
    assert result["status"] == "provisional"
    assert result["consecutive_days"] == 4
    assert [row["code"] for row in result["rows"]] == starters
    assert "上游连板数" not in str(result["rows"])
    assert "历史事实交集" in result["hint"]
```

- [ ] **Step 2: Write the failing verified-state test**

```python
def test_signal_limit_chain_is_verified_only_when_every_cycle_date_is_immutable():
    from micro_cycle import build_signal_limit_chain

    history = pd.DataFrame([
        {"date": date, "type": "ZT", "code": "sh600001"}
        for date in ("20260804", "20260805", "20260806", "20260807")
    ])
    names = NameResolution(names={"sh600001": "验证股份"}, sources={}, conflicts=[])
    result = build_signal_limit_chain(
        history,
        ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"],
        "2026-08-04", "2026-08-07", names=names,
        immutable_dates={"2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"},
    )
    assert result["status"] == "verified"
    assert result["hint"] == ""
```

- [ ] **Step 3: Run both tests and verify RED**

```powershell
python -m pytest -q tests/test_micro_cycle.py -k "signal_limit_chain"
```

Expected: `build_signal_limit_chain` import failure.

- [ ] **Step 4: Implement normalized fact-set intersection**

Add to `src/micro_cycle.py`:

```python
import pandas as pd

from data_sources.models import normalize_code
from data_sources.name_resolver import NameResolution


def _column(frame, *candidates):
    return next((column for column in candidates if column in frame.columns), None)


def build_signal_limit_chain(
    limit_history: pd.DataFrame,
    trading_dates: list[str],
    signal_date: str,
    latest_date: str,
    *,
    names: NameResolution,
    immutable_dates: set[str] | None = None,
) -> dict:
    empty = {"usable": False, "status": "unavailable", "dates": [],
             "consecutive_days": 0, "rows": [], "hint": ""}
    if limit_history is None or limit_history.empty:
        return empty
    date_col = _column(limit_history, "date", "日期")
    code_col = _column(limit_history, "code", "代码")
    type_col = _column(limit_history, "type", "类型")
    if not date_col or not code_col:
        return empty
    dates = [date for date in trading_dates if signal_date <= date <= latest_date]
    if len(dates) < 3:
        return empty

    frame = limit_history.copy()
    frame["_date"] = frame[date_col].astype(str).str.replace("-", "", regex=False)
    if type_col:
        frame = frame[frame[type_col].astype(str).str.upper().eq("ZT")]
    frame["_code"] = frame[code_col].map(normalize_code)
    sets = {
        date: set(frame.loc[frame["_date"].eq(date.replace("-", "")), "_code"])
        for date in dates
    }
    if any(not sets[date] for date in dates):
        return empty
    chain = set.intersection(*(sets[date] for date in dates))
    before = [date for date in trading_dates if date < signal_date]
    previous = before[-1] if before else ""
    previous_set = set(
        frame.loc[frame["_date"].eq(previous.replace("-", "")), "_code"]
    ) if previous else set()
    starters = sorted(chain - previous_set)
    immutable = immutable_dates or set()
    verified = all(date in immutable for date in dates)
    return {
        "usable": bool(starters),
        "status": "verified" if verified else "provisional",
        "dates": dates,
        "consecutive_days": len(dates),
        "rows": [
            {"code": code, "name": names.names.get(code, code)}
            for code in starters
        ],
        "hint": "" if verified else "按历史事实交集计算，逐日不可变快照待补强",
    }
```

- [ ] **Step 5: Run chain tests and the existing ladder tests**

```powershell
python -m pytest -q tests/test_micro_cycle.py -k "signal_limit_chain" tests/test_report_logic.py -k "progression or ladder"
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the cohort builder**

```powershell
git add src/micro_cycle.py tests/test_micro_cycle.py
git diff --cached --check
git commit -m "fix: derive signal cohort from limit facts"
```

---

### Task 3: Calculate Strong Industries, Resonance, And Leaders

**Files:**
- Modify: `src/micro_cycle.py`
- Modify: `tests/test_micro_cycle.py`

**Interfaces:**
- Produces: `build_sector_return_table(cache: dict, signal_date: str, latest_date: str, index_return: float | None) -> pd.DataFrame` with `name`, `return`, and `excess_return`.
- Produces: `build_cycle_resonance(...) -> dict` with `strong_industries`, `mainlines`, `attribution_coverage`, and `leader_coverage`.
- Consumes attribution frames with `date`, `code`, `sub`, and `mainline`; latest valid CLS wins, then latest valid Eastmoney.

- [ ] **Step 1: Write the failing resonance and leader test**

```python
def test_cycle_resonance_separates_strong_industries_from_confirmed_mainlines():
    from micro_cycle import build_cycle_resonance

    codes = ["sz002552", "sz002428", "sh603773", "sz002975", "sh600721", "sz002425", "sh600892"]
    chain = {
        "usable": True,
        "rows": [{"code": code, "name": name} for code, name in zip(codes, [
            "宝鼎科技", "云南锗业", "沃格光电", "博杰股份", "百花医药", "凯撒文化", "大晟文化",
        ])],
    }
    sector_returns = pd.DataFrame([
        {"name": "电子化学品", "return": 17.14, "excess_return": 14.06},
        {"name": "元件", "return": 15.22, "excess_return": 12.14},
        {"name": "贵金属", "return": 15.13, "excess_return": 12.05},
        {"name": "半导体", "return": 11.53, "excess_return": 8.45},
        {"name": "医疗服务", "return": 9.08, "excess_return": 6.00},
    ])
    price_matrix = pd.DataFrame(
        [[10, 10, 10, 10, 10, 10, 10], [12.6767, 12.6763, 12.6760, 12.4081, 12.6731, 11.5622, 11.4758]],
        index=["2026-08-04", "2026-08-07"], columns=codes,
    )
    cls = pd.DataFrame([
        {"date": "20260807", "code": code, "sub": sub, "mainline": mainline}
        for code, sub, mainline in [
            ("sz002552", "PCB", "AI算力"), ("sz002428", "光通信", "AI算力"),
            ("sh603773", "PCB", "AI算力"), ("sz002975", "液冷", "AI算力"),
            ("sh600721", "医药", "医药"), ("sz002425", "AI应用", "AI应用"),
            ("sh600892", "传媒", "AI应用"),
        ]
    )

    result = build_cycle_resonance(
        sector_returns, chain, price_matrix, "2026-08-04", "2026-08-07",
        cls_attribution=cls, em_attribution=None,
    )

    assert [row["name"] for row in result["strong_industries"][:3]] == ["电子化学品", "元件", "贵金属"]
    levels = {row["name"]: row["level"] for row in result["mainlines"]}
    assert levels == {"AI算力": "核心共振", "医药": "次级共振", "AI应用": "连板跟随"}
    leaders = {row["name"]: [stock["name"] for stock in row["leaders"]] for row in result["mainlines"]}
    assert leaders["AI算力"] == ["宝鼎科技", "云南锗业", "沃格光电", "博杰股份"]
    assert "贵金属" not in levels
```

- [ ] **Step 2: Write the failing attribution-priority test**

```python
def test_cycle_resonance_prefers_latest_valid_cls_and_keeps_unattributed_codes():
    from micro_cycle import build_cycle_resonance

    chain = {"usable": True, "rows": [
        {"code": "sh600001", "name": "甲"}, {"code": "sh600002", "name": "乙"},
    ]}
    prices = pd.DataFrame(
        [[10.0, 10.0], [12.0, 11.0]],
        index=["2026-08-04", "2026-08-07"], columns=["sh600001", "sh600002"],
    )
    cls = pd.DataFrame([
        {"date": "20260806", "code": "sh600001", "sub": "PCB", "mainline": "AI算力"},
        {"date": "20260807", "code": "sh600001", "sub": "其它", "mainline": "其它"},
    ])
    em = pd.DataFrame([
        {"date": "20260807", "code": "sh600001", "sub": "传媒", "mainline": "AI应用"},
    ])
    result = build_cycle_resonance(
        pd.DataFrame(), chain, prices, "2026-08-04", "2026-08-07",
        cls_attribution=cls, em_attribution=em,
    )
    assert result["mainlines"][0]["name"] == "AI算力"
    assert result["unattributed_count"] == 1
    assert result["attribution_coverage"] == 0.5
```

- [ ] **Step 3: Write the failing low-qfq-coverage test**

```python
def test_cycle_resonance_hides_numeric_returns_below_eighty_percent_qfq_coverage():
    from micro_cycle import build_cycle_resonance

    chain = {"usable": True, "rows": [
        {"code": "sh600001", "name": "甲"}, {"code": "sh600002", "name": "乙"},
    ]}
    prices = pd.DataFrame(
        [[10.0], [12.0]],
        index=["2026-08-04", "2026-08-07"], columns=["sh600001"],
    )
    cls = pd.DataFrame([
        {"date": "20260807", "code": code, "sub": "传媒", "mainline": "AI应用"}
        for code in ("sh600001", "sh600002")
    ])
    result = build_cycle_resonance(
        pd.DataFrame(), chain, prices, "2026-08-04", "2026-08-07",
        cls_attribution=cls,
    )

    assert result["leader_coverage"] == 0.5
    assert [row["return"] for row in result["mainlines"][0]["leaders"]] == [None, None]
```

- [ ] **Step 4: Run the tests and verify RED**

```powershell
python -m pytest -q tests/test_micro_cycle.py -k "cycle_resonance"
```

Expected: `build_cycle_resonance` import failure.

- [ ] **Step 5: Implement sector returns and stable mainline aliases**

Add to `src/micro_cycle.py`:

```python
MAINLINE_INDUSTRIES = {
    "AI算力": {"电子化学品", "元件", "半导体", "其他电子", "通信设备", "消费电子", "光学光电子"},
    "AI应用": {"软件开发", "IT服务", "互联网服务", "游戏", "出版", "广告营销", "影视院线"},
    "医药": {"医疗服务", "生物制品", "化学制药", "中药", "医药商业", "医疗器械"},
    "周期资源": {"贵金属", "小金属", "能源金属", "金属新材料", "工业金属", "化学原料", "化学制品", "煤炭开采"},
    "机器人": {"自动化设备", "通用设备", "专用设备", "电机"},
    "新能源电网": {"电网设备", "电池", "光伏设备", "风电设备", "电力"},
    "军工航天": {"航空装备", "航天装备", "军工电子", "船舶制造"},
}


def _segment_return(records, start, end):
    first = [row for row in records if str(row.get("date", "")) <= start]
    last = [row for row in records if str(row.get("date", "")) <= end]
    if not first or not last or not first[-1].get("close"):
        return None
    return round((float(last[-1]["close"]) / float(first[-1]["close"]) - 1) * 100, 2)


def build_sector_return_table(cache, signal_date, latest_date, index_return):
    rows = []
    for name, records in (cache or {}).items():
        value = _segment_return(records, signal_date, latest_date)
        if value is not None:
            rows.append({
                "name": str(name), "return": value,
                "excess_return": round(value - float(index_return or 0), 2),
            })
    result = pd.DataFrame(rows, columns=["name", "return", "excess_return"])
    if result.empty:
        return result
    return result.sort_values(["return", "name"], ascending=[False, True]).reset_index(drop=True)
```

- [ ] **Step 6: Implement attribution, levels, and qfq leader ranking**

Add the complete attribution and ranking implementation to `src/micro_cycle.py`:

```python
LEVEL_ORDER = {"核心共振": 0, "次级共振": 1, "连板跟随": 2}
INVALID_MAINLINES = {"", "其它", "其他", "nan", "None"}


def _safe_code(value):
    try:
        return normalize_code(value)
    except ValueError:
        return ""


def _attribution_map(frame, latest_date):
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    required = {"date", "code", "mainline"}
    if not required.issubset(frame.columns):
        return {}
    work = frame.copy()
    work["_date"] = work["date"].astype(str).str.replace("-", "", regex=False)
    work["_code"] = work["code"].map(_safe_code)
    work["_mainline"] = work["mainline"].astype(str).str.strip()
    work = work[
        work["_date"].le(latest_date.replace("-", ""))
        & work["_code"].ne("")
        & ~work["_mainline"].isin(INVALID_MAINLINES)
    ]
    if work.empty:
        return {}
    latest = work.sort_values("_date").drop_duplicates("_code", keep="last")
    return dict(zip(latest["_code"], latest["_mainline"]))


def _endpoint_returns(price_matrix, codes, signal_date, latest_date):
    if (
        price_matrix is None
        or not isinstance(price_matrix, pd.DataFrame)
        or signal_date not in price_matrix.index
        or latest_date not in price_matrix.index
    ):
        return {}
    result = {}
    for code in codes:
        if code not in price_matrix.columns:
            continue
        start = pd.to_numeric(price_matrix.at[signal_date, code], errors="coerce")
        end = pd.to_numeric(price_matrix.at[latest_date, code], errors="coerce")
        if pd.isna(start) or pd.isna(end) or float(start) == 0:
            continue
        result[code] = round((float(end) / float(start) - 1) * 100, 2)
    return result


def build_cycle_resonance(
    sector_returns,
    chain,
    price_matrix,
    signal_date,
    latest_date,
    *,
    cls_attribution=None,
    em_attribution=None,
):
    chain_rows = list((chain or {}).get("rows") or []) if (chain or {}).get("usable") else []
    chain_total = len(chain_rows)
    empty = {
        "strong_industries": [], "mainlines": [],
        "attribution_coverage": 0.0, "leader_coverage": 0.0,
        "unattributed_count": chain_total,
    }

    strong = pd.DataFrame()
    if isinstance(sector_returns, pd.DataFrame) and not sector_returns.empty:
        strong = sector_returns.copy()
        strong["return"] = pd.to_numeric(strong["return"], errors="coerce")
        strong["excess_return"] = pd.to_numeric(strong["excess_return"], errors="coerce")
        strong = strong[strong["excess_return"].ge(2.0)].dropna(subset=["name", "return"])
        strong = strong.sort_values(["return", "name"], ascending=[False, True])
    empty["strong_industries"] = [
        {"name": str(row["name"]), "return": round(float(row["return"]), 2)}
        for _, row in strong.head(5).iterrows()
    ]
    if not chain_rows:
        return empty

    cls_map = _attribution_map(cls_attribution, latest_date)
    em_map = _attribution_map(em_attribution, latest_date)
    attributed = []
    for row in chain_rows:
        code = _safe_code(row.get("code"))
        mainline = cls_map.get(code) or em_map.get(code)
        if code and mainline:
            attributed.append({**row, "code": code, "mainline": mainline})

    returns = _endpoint_returns(
        price_matrix, [row["code"] for row in chain_rows], signal_date, latest_date,
    )
    leader_coverage = len(returns) / chain_total
    evidence_names = set(strong["name"].astype(str)) if not strong.empty else set()
    evidence_order = list(strong["name"].astype(str)) if not strong.empty else []
    mainlines = []
    groups = pd.DataFrame(attributed).groupby("mainline", sort=False) if attributed else []
    for mainline, rows in groups:
        row_list = rows.to_dict("records")
        industries = MAINLINE_INDUSTRIES.get(str(mainline), set()) & evidence_names
        industry_evidence = [name for name in evidence_order if name in industries]
        count = len(row_list)
        if count >= 2 and len(industry_evidence) >= 2:
            level = "核心共振"
        elif count >= 1 and industry_evidence:
            level = "次级共振"
        elif count >= 2:
            level = "连板跟随"
        else:
            continue
        leaders = sorted(
            row_list,
            key=lambda row: (-returns.get(row["code"], float("-inf")), row["code"]),
        )[:4]
        mainlines.append({
            "name": str(mainline), "level": level, "chain_count": count,
            "chain_total": chain_total, "industry_evidence": industry_evidence,
            "leaders": [
                {
                    "code": row["code"], "name": str(row.get("name") or row["code"]),
                    "return": returns.get(row["code"]) if leader_coverage >= 0.80 else None,
                }
                for row in leaders
            ],
        })
    mainlines.sort(key=lambda row: (
        LEVEL_ORDER[row["level"]], -row["chain_count"], row["name"],
    ))
    return {
        "strong_industries": empty["strong_industries"],
        "mainlines": mainlines,
        "attribution_coverage": round(len(attributed) / chain_total, 4),
        "leader_coverage": round(leader_coverage, 4),
        "unattributed_count": chain_total - len(attributed),
    }
```

- [ ] **Step 7: Run all micro-cycle tests**

```powershell
python -m pytest -q tests/test_micro_cycle.py
```

Expected: all tests pass.

- [ ] **Step 8: Commit resonance calculations**

```powershell
git add src/micro_cycle.py tests/test_micro_cycle.py
git diff --cached --check
git commit -m "feat: calculate micro cycle resonance"
```

---

### Task 4: Integrate And Render The Permanent Template

**Files:**
- Modify: `src/phase_resonance.py:30-35,371-444,452-680,681-730`
- Modify: `tests/test_report_logic.py`
- Modify: `tests/test_report_rendering.py`

**Interfaces:**
- Consumes: `detect_micro_cycle`, `build_signal_limit_chain`, `build_sector_return_table`, and `build_cycle_resonance`.
- Produces: `res["micro_cycle"]`, `res["micro_chain"]`, `res["micro_resonance"]`, and `_micro_cycle_html(res: dict) -> str`.
- Preserves: existing `current_phase`, `turning_leaders`, `_turning_summary_html()`, and `_phase_timeline()` output.

- [ ] **Step 1: Write the failing integration-degradation test**

Add to `tests/test_report_logic.py`:

```python
def test_phase_micro_cycle_failure_does_not_remove_major_phase(monkeypatch):
    import phase_resonance

    monkeypatch.setattr(
        phase_resonance,
        "detect_micro_cycle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("fixture")),
        raising=False,
    )
    major = {
        "current_phase": {"label": "箱体突破"},
        "turning_leaders": {"sectors": [], "stocks": []},
    }
    result = phase_resonance._attach_micro_cycle(
        major,
        {
            "bottom": {"date": "2026-07-17", "close": 3764.0},
            "latest": {"date": "2026-08-07", "close": 3940.0},
            "index_series": [],
        },
        {},
    )

    assert result["current_phase"]["label"] == "箱体突破"
    assert result["micro_cycle"] == {}
    assert result["micro_chain"] == {}
    assert result["micro_resonance"] == {}
```

This test must fail before implementation because `_attach_micro_cycle` does not exist, then pass only when the production failure boundary preserves the major result.

- [ ] **Step 2: Write the failing renderer test**

Add to `tests/test_report_rendering.py`:

```python
def test_micro_cycle_template_renders_events_strong_industries_and_mainline_leaders():
    from phase_resonance import _micro_cycle_html

    html = _micro_cycle_html({
        "micro_cycle": {
            "status": "小周期主升", "signal_date": "2026-08-04",
            "confirmation_date": "2026-08-05", "full_confirmation_date": "2026-08-06",
            "signal_return": 3.08, "rising_days": 4, "signal_basis": "price+limit_pool",
            "events": {
                "final_stop": {"date": "2026-07-20", "low": 3741.11},
                "rebound_high": {"high_date": "2026-07-22", "close_date": "2026-07-23"},
                "secondary_bottom": {"date": "2026-07-30", "low": 3767.50, "higher_low": True},
                "retest": {"date": "2026-08-03", "close": 3809.66},
            },
        },
        "micro_chain": {"usable": True, "consecutive_days": 4, "rows": [{"code": "sz002552", "name": "宝鼎科技"}], "hint": "历史事实交集"},
        "micro_resonance": {
            "strong_industries": [{"name": "电子化学品", "return": 17.14}, {"name": "贵金属", "return": 15.13}],
            "mainlines": [{
                "name": "AI算力", "level": "核心共振", "chain_count": 4, "chain_total": 7,
                "industry_evidence": ["电子化学品", "元件", "半导体"],
                "leaders": [{"code": "sz002552", "name": "宝鼎科技", "return": 26.77}],
            }],
            "attribution_coverage": 1.0, "leader_coverage": 1.0, "unattributed_count": 0,
        },
    })

    for token in (
        "短周期结构", "小周期主升", "7/20", "7/22-23", "7/30", "8/4",
        "转强信号", "8/5", "突破确认", "强行业", "电子化学品", "共振主线",
        "核心共振", "AI算力", "板块领涨个股", "宝鼎科技", "+26.8%",
    ):
        assert token in html
    assert "贵金属" in html
    assert "贵金属</strong>" not in html
    assert "micro-cycle-timeline" in html
```

- [ ] **Step 3: Write the failing empty-evidence renderer test**

```python
def test_micro_cycle_template_hides_empty_evidence_headings_and_keeps_small_hint():
    from phase_resonance import _micro_cycle_html

    html = _micro_cycle_html({
        "micro_cycle": {
            "status": "震荡筑底", "signal_date": "", "confirmation_date": "",
            "full_confirmation_date": "", "signal_return": None, "rising_days": 0,
            "events": {"final_stop": {"date": "2026-07-20", "low": 3741.11}},
        },
        "micro_chain": {"usable": False, "hint": "历史事实不足"},
        "micro_resonance": {},
    })

    assert "短周期结构" in html
    assert "历史事实不足" in html
    assert "强行业" not in html
    assert "共振主线" not in html
    assert "None" not in html
    assert "nan" not in html.lower()
```

- [ ] **Step 4: Run the tests and verify RED**

```powershell
python -m pytest -q tests/test_report_logic.py::test_phase_micro_cycle_failure_does_not_remove_major_phase tests/test_report_rendering.py -k "micro_cycle_template"
```

Expected: `_attach_micro_cycle` and `_micro_cycle_html` are missing.

- [ ] **Step 5: Add cache loading and independent orchestration**

In `src/phase_resonance.py`:

```python
from paths import (
    CLS_PLATE_CACHE, DAILY_SNAPSHOT_DIR, DATA_DIR, EM_PLATE_CACHE,
    INDUSTRY_CACHE, PRICE_CACHE, SECURITY_MASTER_CACHE, ZT_CACHE_FILE,
)
from data_sources.name_resolver import resolve_names
from data_sources.price_provider import build_price_matrix
from micro_cycle import (
    build_cycle_resonance, build_sector_return_table,
    build_signal_limit_chain, detect_micro_cycle,
)
```

Add these readers and the isolated enrichment boundary before `_build()`:

```python
def _read_csv(path, **kwargs):
    if not path or not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path, **kwargs)
    except (OSError, ValueError, pd.errors.ParserError):
        return pd.DataFrame()


def _pick_column(frame, *candidates):
    return next((name for name in candidates if name in frame.columns), None)


def _daily_limit_counts(history):
    if history is None or history.empty:
        return {}
    date_col = _pick_column(history, "date", "日期")
    code_col = _pick_column(history, "code", "代码")
    type_col = _pick_column(history, "type", "类型")
    if not date_col or not code_col:
        return {}
    frame = history.copy()
    if type_col:
        frame = frame[frame[type_col].astype(str).str.upper().eq("ZT")]
    frame["_date"] = frame[date_col].astype(str).str.replace("-", "", regex=False)
    frame["_code"] = frame[code_col].astype(str).str.strip()
    grouped = frame[frame["_code"].ne("")].groupby("_date")["_code"].nunique()
    return {
        f"{date[:4]}-{date[4:6]}-{date[6:8]}": int(count)
        for date, count in grouped.items()
        if len(str(date)) == 8
    }


def _immutable_snapshot_dates():
    result = set()
    if not os.path.isdir(DAILY_SNAPSHOT_DIR):
        return result
    for name in os.listdir(DAILY_SNAPSHOT_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(DAILY_SNAPSHOT_DIR, name), encoding="utf-8") as handle:
                snapshot = json.load(handle)
        except (OSError, ValueError, TypeError):
            continue
        date = name[:-5]
        if (
            snapshot.get("snapshot_schema") == "daily-fact-snapshot/v1"
            and snapshot.get("date_verified") is True
            and snapshot.get("immutable") is True
            and str(snapshot.get("report_date") or date) == date
        ):
            result.add(date)
    return result


def _build_micro_cycle_payload(det, cache):
    history = _read_csv(ZT_CACHE_FILE)
    counts = _daily_limit_counts(history)
    micro_cycle = detect_micro_cycle(det, daily_limit_counts=counts)
    micro_chain = {}
    micro_resonance = {}
    if micro_cycle.get("signal_date"):
        master = _read_csv(SECURITY_MASTER_CACHE, dtype=str)
        industry = _read_csv(INDUSTRY_CACHE, dtype=str)
        names = resolve_names(universe=master, industry=industry)
        trading_dates = [row["date"] for row in det["index_series"]]
        micro_chain = build_signal_limit_chain(
            history, trading_dates, micro_cycle["signal_date"], det["latest"]["date"],
            names=names, immutable_dates=_immutable_snapshot_dates(),
        )
        sector_returns = build_sector_return_table(
            cache, micro_cycle["signal_date"], det["latest"]["date"],
            micro_cycle.get("signal_return"),
        )
        prices = build_price_matrix(_read_csv(PRICE_CACHE), "qfq", allow_legacy=True)
        micro_resonance = build_cycle_resonance(
            sector_returns, micro_chain, prices,
            micro_cycle["signal_date"], det["latest"]["date"],
            cls_attribution=_read_csv(CLS_PLATE_CACHE, dtype=str),
            em_attribution=_read_csv(EM_PLATE_CACHE, dtype=str),
        )
    return {
        "micro_cycle": micro_cycle,
        "micro_chain": micro_chain,
        "micro_resonance": micro_resonance,
    }


def _attach_micro_cycle(result, det, cache):
    try:
        payload = _build_micro_cycle_payload(det, cache)
    except Exception as exc:
        print(f"  ⚠️ 短周期共振计算跳过 (不影响主要阶段): {exc}")
        payload = {"micro_cycle": {}, "micro_chain": {}, "micro_resonance": {}}
    return {**result, **payload}
```

Replace the direct dictionary return at the end of `_build()` with `result = {...}` followed by `return _attach_micro_cycle(result, det, cache)`. Keep all existing keys in `result` unchanged; the `try/except` is confined to `_attach_micro_cycle`.

- [ ] **Step 6: Implement the flat responsive renderer**

Add these helpers to `src/phase_resonance.py`:

```python
def _micro_date(value):
    text = str(value or "")
    parts = text.split("-")
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        return f"{int(parts[1])}/{int(parts[2])}"
    return escape(text, quote=True)


def _micro_return(value):
    number = pd.to_numeric(value, errors="coerce")
    return "—" if pd.isna(number) else f"{float(number):+.1f}%"


def _micro_cycle_html(res):
    micro = (res or {}).get("micro_cycle") or {}
    if not micro:
        return ""
    events = micro.get("events") or {}
    final_stop = events.get("final_stop") or {}
    rebound = events.get("rebound_high") or {}
    secondary = events.get("secondary_bottom") or {}
    retest = events.get("retest") or {}
    rebound_date = ""
    if rebound.get("high_date"):
        rebound_date = _micro_date(rebound.get("high_date"))
        if rebound.get("close_date"):
            rebound_date += f"-{_micro_date(rebound.get('close_date')).split('/')[-1]}"
    timeline = [
        (_micro_date(final_stop.get("date")), "止跌确认", f"盘中低点 {final_stop.get('low', 0):.0f}"),
        (rebound_date, "第一反弹高点", "盘中与收盘高点分开确认"),
        (
            _micro_date(secondary.get("date")), "二次探底",
            "低点抬升" if secondary.get("higher_low") else "再次破底",
        ),
        (_micro_date(retest.get("date")), "局部回测", f"收盘 {retest.get('close', 0):.0f}"),
        (_micro_date(micro.get("signal_date")), "转强信号", "收涨且低点不再下移"),
        (_micro_date(micro.get("confirmation_date")), "收盘突破确认", "突破第一反弹收盘高点"),
    ]
    event_html = "".join(
        '<div class="micro-cycle-event">'
        f'<b>{escape(date, quote=True)}</b><span>{escape(label, quote=True)}</span>'
        f'<small>{escape(evidence, quote=True)}</small></div>'
        for date, label, evidence in timeline if date
    )

    resonance = (res or {}).get("micro_resonance") or {}
    industries = resonance.get("strong_industries") or []
    industry_html = "".join(
        f'<span>{escape(str(row.get("name") or ""), quote=True)} '
        f'<i style="color:{_clr(row.get("return"))};">{_micro_return(row.get("return"))}</i></span>'
        for row in industries
    )
    industry_section = (
        f'<div class="micro-subhead">强行业</div><div class="micro-strong-industries">{industry_html}</div>'
        if industry_html else ""
    )

    mainline_html = ""
    for row in resonance.get("mainlines") or []:
        evidence = " · ".join(str(item) for item in row.get("industry_evidence") or [])
        leaders = "".join(
            '<span class="micro-leader">'
            f'{escape(str(stock.get("name") or stock.get("code") or ""), quote=True)}'
            f'<small>{escape(str(stock.get("code") or ""), quote=True)}</small>'
            f'<i style="color:{_clr(stock.get("return"))};">{_micro_return(stock.get("return"))}</i>'
            '</span>'
            for stock in row.get("leaders") or []
        )
        mainline_html += (
            '<div class="micro-resonance-row">'
            f'<div><b>{escape(str(row.get("level") or ""), quote=True)}</b>'
            f'<strong>{escape(str(row.get("name") or ""), quote=True)}</strong>'
            f'<small>启动 {int(row.get("chain_count") or 0)}/{int(row.get("chain_total") or 0)}'
            f'{" · 行业证据 " + escape(evidence, quote=True) if evidence else ""}</small></div>'
            f'<div class="micro-leaders"><em>板块领涨个股</em>{leaders}</div></div>'
        )
    mainline_section = (
        f'<div class="micro-subhead">共振主线</div>{mainline_html}' if mainline_html else ""
    )

    chain = (res or {}).get("micro_chain") or {}
    hints = [str(chain.get("hint") or "")]
    if resonance.get("attribution_coverage", 1.0) < 1.0:
        hints.append(f"主线归因覆盖 {float(resonance.get('attribution_coverage') or 0):.0%}")
    if resonance.get("leader_coverage", 1.0) < 0.8:
        hints.append("个股前复权端点覆盖不足，收益暂不展示")
    hint_html = " · ".join(escape(item, quote=True) for item in hints if item)
    hint_block = f'<div class="micro-cycle-hint">{hint_html}</div>' if hint_html else ""
    full_date = _micro_date(micro.get("full_confirmation_date"))
    full_text = f" · {full_date} 全面突破" if full_date else ""
    return f'''
    <style>
      .micro-cycle-section{{margin:0 0 16px;border-top:1px solid rgba(48,54,61,.8);border-bottom:1px solid rgba(48,54,61,.8);padding:16px 0;overflow-wrap:anywhere;}}
      .micro-cycle-head{{display:flex;justify-content:space-between;gap:16px;align-items:flex-end;margin:0 16px 14px;}}
      .micro-cycle-head h3{{margin:0;color:#e6edf3;font-size:16px;letter-spacing:0;}}
      .micro-cycle-head b{{color:#58a6ff;font-size:20px;}}
      .micro-cycle-head small,.micro-cycle-hint{{color:#8b949e;font-size:11px;}}
      .micro-cycle-timeline{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));border-top:1px solid rgba(48,54,61,.7);border-bottom:1px solid rgba(48,54,61,.7);}}
      .micro-cycle-event{{min-width:0;padding:12px 10px;display:flex;flex-direction:column;gap:4px;}}
      .micro-cycle-event+.micro-cycle-event{{border-left:1px solid rgba(48,54,61,.7);}}
      .micro-cycle-event b{{color:#58a6ff;font-size:13px;}}.micro-cycle-event span{{color:#e6edf3;font-size:12px;font-weight:700;}}
      .micro-cycle-event small{{color:#8b949e;font-size:11px;line-height:1.45;}}
      .micro-subhead{{margin:14px 16px 8px;color:#8b949e;font-size:11px;font-weight:700;}}
      .micro-strong-industries{{display:flex;flex-wrap:wrap;gap:8px 16px;margin:0 16px;}}
      .micro-strong-industries span{{color:#e6edf3;font-size:12px;}}.micro-strong-industries i{{font-style:normal;font-weight:700;}}
      .micro-resonance-row{{display:grid;grid-template-columns:minmax(220px,.8fr) minmax(0,1.5fr);gap:16px;padding:11px 16px;border-top:1px solid rgba(48,54,61,.55);}}
      .micro-resonance-row>div:first-child{{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;}}
      .micro-resonance-row b{{color:#d29922;font-size:11px;}}.micro-resonance-row strong{{color:#e6edf3;font-size:14px;}}
      .micro-resonance-row small{{color:#8b949e;font-size:10px;}}
      .micro-leaders{{display:flex;gap:8px 12px;align-items:center;flex-wrap:wrap;}}.micro-leaders em{{color:#8b949e;font-size:10px;font-style:normal;}}
      .micro-leader{{display:inline-flex;gap:5px;align-items:baseline;color:#e6edf3;font-size:12px;}}
      .micro-leader small{{font-size:9px;}}.micro-leader i{{font-size:11px;font-style:normal;font-weight:700;}}
      .micro-cycle-hint{{margin:10px 16px 0;}}
      @media(max-width:760px){{.micro-cycle-head{{align-items:flex-start;flex-direction:column;}}.micro-cycle-timeline{{grid-template-columns:1fr;}}.micro-cycle-event+.micro-cycle-event{{border-left:0;border-top:1px solid rgba(48,54,61,.55);}}.micro-resonance-row{{grid-template-columns:1fr;gap:8px;}}}}
    </style>
    <section class="micro-cycle-section">
      <div class="micro-cycle-head"><div><h3>短周期结构</h3><b>{escape(str(micro.get("status") or ""), quote=True)}</b></div>
      <small>转强后 {_micro_return(micro.get("signal_return"))} · 连续 {int(micro.get("rising_days") or 0)} 日收涨{full_text}</small></div>
      <div class="micro-cycle-timeline">{event_html}</div>
      {industry_section}{mainline_section}
      {hint_block}
    </section>'''
```

Insert `{_micro_cycle_html(res)}` immediately after `{_turning_summary_html(res)}` in `render_phase_resonance_html()`. The component remains flat, escapes every dynamic label, formats returns to one decimal, and changes to one event per row below 760px without horizontal scrolling.

- [ ] **Step 7: Run integration and rendering tests**

```powershell
python -m pytest -q tests/test_micro_cycle.py tests/test_report_logic.py -k "micro_cycle or turning_summary" tests/test_report_rendering.py -k "micro_cycle or phase_turning"
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit integration and template**

```powershell
git add src/phase_resonance.py tests/test_report_logic.py tests/test_report_rendering.py
git diff --cached --check
git commit -m "feat: show micro cycle resonance in report"
```

---

### Task 5: Regenerate And Verify The Product Report

**Files:**
- Regenerate: `output/site/reports/2026-08-07.html`
- Regenerate: `output/site/dashboards/2026-08-07.html`
- Regenerate: `output/site/index.html`
- Create: `output/playwright/micro-cycle-resonance-desktop.png`
- Create: `output/playwright/micro-cycle-resonance-mobile.png`

**Interfaces:**
- Consumes: existing historical caches and the production report entry point.
- Produces: a permanent template used by 2026-08-07 and future reports.

- [ ] **Step 1: Run focused suites before generation**

```powershell
python -m pytest -q tests/test_micro_cycle.py tests/test_report_logic.py tests/test_report_rendering.py
```

Expected: all tests pass.

- [ ] **Step 2: Regenerate the historical report**

```powershell
$env:REPORT_DATE='2026-08-07'
$env:EMAIL_ENABLE='0'
python 'src/主线强度追踪.py'
```

Expected: report, dashboard, and index publish; immutable report-day fact pool remains 83 stocks.

- [ ] **Step 3: Verify exact product content**

```powershell
$html = Get-Content -Raw 'output/site/reports/2026-08-07.html'
@(
  '短周期结构','小周期主升','7/20','7/22-23','7/30','8/4','8/5',
  '核心共振','AI算力','宝鼎科技','云南锗业','沃格光电','博杰股份',
  '次级共振','百花医药','连板跟随','凯撒文化','大晟文化'
) | ForEach-Object { if (-not $html.Contains($_)) { throw "missing: $_" } }
if ($html.Contains('*ST传智')) { throw 'stale name returned' }
```

Expected: command exits zero.

- [ ] **Step 4: Verify desktop and mobile with Playwright CLI**

Open the local report through an HTTP server and check at `1440x1000` and `390x844`:

```javascript
({
  scrollWidth: document.documentElement.scrollWidth,
  clientWidth: document.documentElement.clientWidth,
  microCycle: document.body.innerText.includes('短周期结构'),
  signal: document.body.innerText.includes('8/4'),
  confirmation: document.body.innerText.includes('8/5'),
  resonance: document.body.innerText.includes('核心共振'),
  leader: document.body.innerText.includes('宝鼎科技'),
})
```

Expected: `scrollWidth === clientWidth` and every boolean is true. Capture the two screenshots listed above and inspect timeline order, text wrapping, and leader-row density.

- [ ] **Step 5: Run final verification gates**

```powershell
python -m pytest -q
python -m compileall -q src tests
git diff --check
git status --short --branch
```

Expected: full suite passes, compile and diff checks exit zero, and unrelated local caches/probes remain untracked.

- [ ] **Step 6: Complete the branch**

Use `superpowers:verification-before-completion`, then `superpowers:finishing-a-development-branch`. Do not merge or push until the user selects an integration option.
