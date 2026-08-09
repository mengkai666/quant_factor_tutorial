# Index Phase Turning Leaders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reusable report summary that shows the current index stage and the leading sectors and A-share stocks since the mechanically detected major turning low.

**Architecture:** Extract the report's per-stock legacy-to-qfq stitching into the shared price provider, then use it in a focused turning-leader builder without changing the existing board attribution model. Extend `phase_resonance` with a structured summary and a compact responsive renderer placed before the existing timeline; missing leader inputs degrade independently and never block the report.

**Tech Stack:** Python 3, pandas, server-rendered HTML/CSS, pytest, Playwright CLI.

## Global Constraints

- The turning date is always `det["bottom"]["date"]`; never hard-code a calendar date.
- The endpoint is `det["latest"]["date"]` under the report-date cutoff.
- Sector leaders use THS industry index `底部至今` returns and show Top 3.
- Stock leaders use Shanghai, Shenzhen, and Beijing full-A-share stitched qfq returns and show Top 5.
- Sector and stock rankings remain independent; do not fabricate sector-to-stock attribution.
- Current names and ST status come from the report-date security master through the shared name resolver.
- Hide the stock ranking below 80% endpoint coverage; never substitute zero or one-day returns.
- Do not add a new external data source or let this feature block daily report generation.
- The new summary is a flat three-part layout, not nested cards, and must not overflow at 390px.

---

### Task 1: Extract The Shared Stitched Price Matrix

**Files:**
- Modify: `src/data_sources/price_provider.py:530-540`
- Modify: `src/主线强度追踪.py:41-56,2141-2192`
- Test: `tests/test_data_source_models.py`
- Test: `tests/test_report_logic.py:1365-1381`

**Interfaces:**
- Consumes: canonical price rows with `date`, `code`, `close_raw`, `close_qfq`, and `close_legacy`.
- Produces: `build_price_matrix(frame: pd.DataFrame, basis: str = "qfq", *, allow_legacy: bool = True) -> pd.DataFrame` indexed by date with normalized stock-code columns.
- Preserves: `主线强度追踪._price_matrix(...)` as a compatibility wrapper around the shared function.

- [ ] **Step 1: Write the failing shared-matrix test**

Add to `tests/test_data_source_models.py`:

```python
def test_build_price_matrix_stitches_legacy_history_per_stock_without_backfill():
    import pandas as pd
    from data_sources.price_provider import build_price_matrix

    prices = pd.DataFrame([
        {"date": "2026-07-17", "code": "sh600001", "close_legacy": 10.0, "close_qfq": None},
        {"date": "2026-08-06", "code": "sh600001", "close_legacy": 12.0, "close_qfq": 18.0},
        {"date": "2026-08-07", "code": "sh600001", "close_legacy": 13.0, "close_qfq": 19.5},
        {"date": "2026-07-17", "code": "sz000002", "close_legacy": 20.0, "close_qfq": None},
        {"date": "2026-08-07", "code": "sz000002", "close_legacy": 22.0, "close_qfq": None},
        {"date": "2026-08-07", "code": "bj920001", "close_legacy": None, "close_qfq": 8.0},
    ])

    matrix = build_price_matrix(prices, "qfq", allow_legacy=True)

    assert matrix.loc["2026-07-17", "sh600001"] == 15.0
    assert matrix.loc["2026-08-07", "sh600001"] == 19.5
    assert matrix.loc["2026-07-17", "sz000002"] == 20.0
    assert pd.isna(matrix.loc["2026-07-17", "bj920001"])
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest -q tests/test_data_source_models.py::test_build_price_matrix_stitches_legacy_history_per_stock_without_backfill
```

Expected: collection/import failure because `build_price_matrix` does not exist.

- [ ] **Step 3: Implement the shared matrix**

Add after the final `price_value_column()` in `src/data_sources/price_provider.py`:

```python
def build_price_matrix(
    frame: pd.DataFrame,
    basis: str = "qfq",
    *,
    allow_legacy: bool = True,
) -> pd.DataFrame:
    if frame is None or frame.empty or not {"date", "code"}.issubset(frame.columns):
        return pd.DataFrame()
    preferred = "close_raw" if str(basis).lower() == "raw" else "close_qfq"
    fallback = "close_legacy" if "close_legacy" in frame.columns else (
        "close" if "close" in frame.columns else None
    )
    columns = [column for column in (preferred, fallback) if column and column in frame.columns]
    if not columns:
        return pd.DataFrame()

    work = frame[["date", "code", *dict.fromkeys(columns)]].copy()
    work["date"] = work["date"].astype(str).str.strip()
    work["code"] = work["code"].map(normalize_code)
    for column in columns:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work["_value"] = work[preferred] if preferred in work else pd.NA

    if allow_legacy and fallback in work:
        stitched = []
        for _, group in work.groupby("code", sort=False):
            target = pd.to_numeric(group["_value"], errors="coerce")
            legacy = pd.to_numeric(group[fallback], errors="coerce")
            overlap = target.notna() & legacy.notna() & legacy.ne(0)
            ratio = 1.0
            if overlap.any():
                ratios = (target[overlap] / legacy[overlap]).replace(
                    [float("inf"), float("-inf")], pd.NA
                ).dropna()
                if not ratios.empty:
                    ratio = float(ratios.median())
            stitched.append(target.fillna(legacy * ratio))
        work["_value"] = pd.concat(stitched).sort_index()

    work["_value"] = pd.to_numeric(work["_value"], errors="coerce").round(8)
    work = work.dropna(subset=["_value"])
    if work.empty:
        return pd.DataFrame()
    return work.pivot_table(
        index="date", columns="code", values="_value", aggfunc="last"
    ).sort_index()
```

Import `build_price_matrix` in `src/主线强度追踪.py` and replace `_price_matrix()` body with:

```python
def _price_matrix(price_df, basis="qfq", allow_legacy=True):
    return build_price_matrix(price_df, basis, allow_legacy=allow_legacy)
```

- [ ] **Step 4: Run shared and compatibility tests**

Run:

```powershell
python -m pytest -q tests/test_data_source_models.py::test_build_price_matrix_stitches_legacy_history_per_stock_without_backfill tests/test_report_logic.py::test_price_matrix_stitches_legacy_history_to_partial_qfq_series
```

Expected: both tests pass.

- [ ] **Step 5: Commit the shared price contract**

```powershell
git add src/data_sources/price_provider.py src/主线强度追踪.py tests/test_data_source_models.py tests/test_report_logic.py
git diff --cached --check
git commit -m "refactor: share stitched report price matrix"
```

---

### Task 2: Build The Turning-Point Leader Summary

**Files:**
- Modify: `src/stock_representatives.py:20-125`
- Modify: `src/phase_resonance.py:305-385`
- Test: `tests/test_report_logic.py`

**Interfaces:**
- Consumes: `phases`, the report-date price cache, `NameResolution`, the phase detector result, and the existing sector table.
- Produces: `build_turning_stock_leaders(phases: dict, *, name_resolution: NameResolution | None = None, expected_universe_size: int | None = None, top_n: int = 5, min_coverage: float = 0.80) -> dict`.
- Produces: `build_turning_summary(det: dict, table: pd.DataFrame, stock_leaders: dict, *, sector_top_n: int = 3) -> dict`.

- [ ] **Step 1: Write the failing stock-leader test**

Add to `tests/test_report_logic.py`:

```python
def test_turning_stock_leaders_rank_stitched_qfq_and_use_current_names(tmp_path, monkeypatch):
    import pandas as pd
    import stock_representatives
    from data_sources.name_resolver import NameResolution

    prices = pd.DataFrame([
        {"date": "2026-07-17", "code": "sh600001", "close_legacy": 10.0, "close_qfq": None},
        {"date": "2026-08-07", "code": "sh600001", "close_legacy": 15.0, "close_qfq": 30.0},
        {"date": "2026-07-17", "code": "sz000002", "close_legacy": 20.0, "close_qfq": None},
        {"date": "2026-08-07", "code": "sz000002", "close_legacy": 24.0, "close_qfq": 24.0},
    ])
    path = tmp_path / "prices.csv"
    prices.to_csv(path, index=False)
    monkeypatch.setattr(stock_representatives, "PRICE_CACHE", str(path))
    names = NameResolution(
        names={"sh600001": "领涨股份", "sz000002": "传智教育"},
        sources={"sh600001": "universe", "sz000002": "universe"},
        conflicts=[],
    )

    result = stock_representatives.build_turning_stock_leaders(
        {"底部至今": ("2026-07-17", "2026-08-07")},
        name_resolution=names,
        expected_universe_size=2,
    )

    assert result["usable"] is True
    assert result["coverage"] == 1.0
    assert [row["name"] for row in result["rows"]] == ["领涨股份", "传智教育"]
    assert result["rows"][0]["return"] == 50.0
    assert result["rows"][1]["st"] is False
```

- [ ] **Step 2: Write the failing phase-summary test**

Add to the same file:

```python
def test_turning_summary_uses_major_bottom_and_top_three_sector_returns():
    import pandas as pd
    from phase_resonance import build_turning_summary

    det = {
        "shape": "箱体突破 (箱体 3768~3941, 振幅 4.6%, 收在上沿)",
        "bottom": {"date": "2026-07-17", "close": 3764.0},
        "latest": {"date": "2026-08-07", "close": 3940.0},
        "index_series": [
            {"date": "2026-07-17", "close": 3764.0},
            {"date": "2026-07-20", "close": 3800.0},
            {"date": "2026-08-07", "close": 3940.0},
        ],
    }
    table = pd.DataFrame([
        {"板块": "教育", "底部至今": 25.56},
        {"板块": "贵金属", "底部至今": 35.09},
        {"板块": "能源金属", "底部至今": 17.38},
        {"板块": "软件开发", "底部至今": 16.10},
    ])
    stocks = {"usable": True, "coverage": 0.93, "rows": [{"code": "sh600001", "name": "领涨股份", "return": 50.0, "st": False}]}

    summary = build_turning_summary(det, table, stocks)

    assert summary["current_phase"]["label"] == "箱体突破"
    assert summary["current_phase"]["turning_date"] == "2026-07-17"
    assert summary["current_phase"]["trading_days"] == 3
    assert summary["current_phase"]["index_return"] == 4.68
    assert [row["name"] for row in summary["turning_leaders"]["sectors"]] == ["贵金属", "教育", "能源金属"]
```

- [ ] **Step 3: Run both tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_report_logic.py::test_turning_stock_leaders_rank_stitched_qfq_and_use_current_names tests/test_report_logic.py::test_turning_summary_uses_major_bottom_and_top_three_sector_returns
```

Expected: import/attribute failures because both builders are missing.

- [ ] **Step 4: Implement stock ranking and name loading**

In `src/stock_representatives.py`:

- Import `UNIVERSE_CACHE`, `build_price_matrix`, `NameResolution`, and `resolve_names`.
- Add `_load_name_resolution()` that reads `UNIVERSE_CACHE` and `INDUSTRY_CACHE` with `dtype=str`, then calls `resolve_names(universe=..., industry=...)`.
- Add `build_turning_stock_leaders()` with this exact behavior:
  - Read and report-date-filter `PRICE_CACHE`.
  - Build a stitched qfq matrix with `build_price_matrix()`.
  - Resolve the `底部至今` endpoints using the last matrix date `<=` each boundary.
  - Restrict candidates to normalized codes present in `NameResolution.names`.
  - Compute endpoint coverage against `expected_universe_size` or `len(names)`.
  - Return `{"usable": False, "coverage": value, "rows": []}` below `min_coverage`.
  - Otherwise sort returns descending, then code ascending, and emit Top N rows containing `code`, `name`, rounded `return`, and `st`.

- [ ] **Step 5: Implement the structured phase summary**

In `src/phase_resonance.py`, add `build_turning_summary()` that:

- Splits `det["shape"]` at the first `" ("` into `label` and `detail`.
- Uses `det["bottom"]` and `det["latest"]` for dates and index return.
- Counts inclusive index-series trading dates between the two endpoints.
- Drops sector rows with missing `底部至今`, sorts descending, and emits Top 3 as `name` and rounded `return`.
- Copies usable stock rows or emits an empty list plus `stock_hint="区间个股覆盖不足"` when coverage is below 80%.

In `_build()`, call `build_turning_stock_leaders(det["phases"])`, call `build_turning_summary(det, t, stock_leaders)`, and merge its `current_phase` and `turning_leaders` keys into the returned result. Wrap only the stock-leader call in `try/except`; a stock ranking failure must produce an unusable empty result without suppressing sector output.

- [ ] **Step 6: Run the new and adjacent phase tests**

Run:

```powershell
python -m pytest -q tests/test_report_logic.py -k "turning or phase_index or phase_price_cache or price_matrix"
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit the structured summary**

```powershell
git add src/stock_representatives.py src/phase_resonance.py tests/test_report_logic.py
git diff --cached --check
git commit -m "feat: calculate leaders since index turning point"
```

---

### Task 3: Render The Permanent Product Template

**Files:**
- Modify: `src/phase_resonance.py:389-565`
- Test: `tests/test_report_rendering.py`

**Interfaces:**
- Consumes: `res["current_phase"]` and `res["turning_leaders"]` from Task 2.
- Produces: `_turning_summary_html(res: dict) -> str`, included by `render_phase_resonance_html()` before `_phase_timeline(res)`.

- [ ] **Step 1: Write the failing rendering test**

Add to `tests/test_report_rendering.py`:

```python
def test_phase_turning_summary_renders_current_stage_and_independent_leader_lists():
    from phase_resonance import _turning_summary_html

    html = _turning_summary_html({
        "current_phase": {
            "label": "箱体突破",
            "detail": "箱体 3768~3941，振幅 4.6%，收在上沿",
            "turning_date": "2026-07-17",
            "latest_date": "2026-08-07",
            "index_return": 4.67,
            "trading_days": 16,
        },
        "turning_leaders": {
            "sectors": [
                {"name": "贵金属", "return": 35.09},
                {"name": "教育", "return": 25.56},
                {"name": "能源金属", "return": 17.38},
            ],
            "stocks": [
                {"code": "sh603221", "name": "爱丽家居", "return": 140.59, "st": False},
                {"code": "sz003032", "name": "传智教育", "return": 102.63, "st": False},
            ],
            "stock_hint": "",
        },
    })

    for token in ("当前阶段", "箱体突破", "主要转折", "2026-07-17", "转折以来领涨板块", "贵金属", "+35.1%", "转折以来领涨个股", "爱丽家居", "传智教育"):
        assert token in html
    assert "强制归因" not in html
    assert "*ST传智" not in html
    assert "phase-turning-grid" in html
```

- [ ] **Step 2: Write the failing compact-degradation test**

```python
def test_phase_turning_summary_hides_empty_rankings_and_keeps_small_coverage_hint():
    from phase_resonance import _turning_summary_html

    html = _turning_summary_html({
        "current_phase": {
            "label": "二次探底", "detail": "", "turning_date": "2026-07-17",
            "latest_date": "2026-08-07", "index_return": -1.2, "trading_days": 16,
        },
        "turning_leaders": {"sectors": [], "stocks": [], "stock_hint": "区间个股覆盖不足"},
    })

    assert "当前阶段" in html
    assert "转折以来领涨板块" not in html
    assert "转折以来领涨个股" not in html
    assert "区间个股覆盖不足" in html
    assert "None" not in html
    assert "nan" not in html.lower()
```

- [ ] **Step 3: Run both tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_report_rendering.py -k "phase_turning_summary"
```

Expected: import failure because `_turning_summary_html` does not exist.

- [ ] **Step 4: Implement the flat responsive renderer**

In `src/phase_resonance.py`:

- Import `escape` from `html` and escape every dynamic name, code, label, detail, and date.
- Add `_turning_summary_html(res)`.
- Render only non-empty leader columns.
- Use classes `phase-turning-summary`, `phase-turning-grid`, `phase-turning-col`, and `phase-turning-row`.
- Put separators on columns/rows; do not give each list or item a card background/border.
- Render ST as a small `ST` label only when `row["st"]` is true.
- Format sector and stock returns with one decimal and A-share red/green colors through `_clr()`.
- Append `stock_hint` once as an 11px muted line.
- Add a local media rule that changes `.phase-turning-grid` from three columns to one column at `max-width:760px`; every child uses `min-width:0` and `overflow-wrap:anywhere`.

Insert `{_turning_summary_html(res)}` immediately before `{_phase_timeline(res)}` in `render_phase_resonance_html()`.

- [ ] **Step 5: Run rendering and mobile regression tests**

Run:

```powershell
python -m pytest -q tests/test_report_rendering.py -k "phase_turning_summary or phase_timeline"
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the permanent template**

```powershell
git add src/phase_resonance.py tests/test_report_rendering.py
git diff --cached --check
git commit -m "feat: show index stage and turning leaders"
```

---

### Task 4: Regenerate And Verify The 2026-08-07 Product Report

**Files:**
- Regenerate: `output/site/reports/2026-08-07.html`
- Regenerate: `output/site/dashboards/2026-08-07.html`
- Regenerate: `output/site/index.html`
- Create: `output/playwright/index-phase-leaders-desktop.png`
- Create: `output/playwright/index-phase-leaders-mobile.png`

**Interfaces:**
- Consumes: the production report entry point and the existing 2026-08-07 immutable snapshot.
- Produces: a report that uses the new template for this and all future report dates.

- [ ] **Step 1: Run focused feature suites**

```powershell
python -m pytest -q tests/test_data_source_models.py tests/test_report_logic.py tests/test_report_rendering.py
```

Expected: all tests pass.

- [ ] **Step 2: Regenerate the historical report**

```powershell
$env:REPORT_DATE='2026-08-07'
$env:EMAIL_ENABLE='0'
python 'src/主线强度追踪.py'
```

Expected: the report, dashboard, and index publish successfully; the immutable limit-up fact pool remains 83 stocks.

- [ ] **Step 3: Verify required product copy and values**

```powershell
rg -n "当前阶段|箱体突破|主要转折|2026-07-17|转折以来领涨板块|贵金属|转折以来领涨个股" output/site/reports/2026-08-07.html
rg -n "\*ST传智|None|nan" output/site/reports/2026-08-07.html
```

Expected: the first command finds the new summary; the second command has no output for the new summary region. Inspect any unrelated lowercase `nan` in serialized charts before treating it as a failure.

- [ ] **Step 4: Verify desktop and mobile with Playwright CLI**

Open `http://127.0.0.1:8765/reports/2026-08-07.html`, then verify at `1440x1000` and `390x844`:

```javascript
({
  scrollWidth: document.documentElement.scrollWidth,
  clientWidth: document.documentElement.clientWidth,
  currentStage: document.body.innerText.includes('当前阶段'),
  turningDate: document.body.innerText.includes('2026-07-17'),
  sectorLeader: document.body.innerText.includes('贵金属'),
  stockLeader: document.body.innerText.includes('转折以来领涨个股'),
})
```

Expected: `scrollWidth === clientWidth` and every boolean is `true`. Save full-page screenshots to the paths listed above and visually inspect text wrapping and column order.

- [ ] **Step 5: Run final verification gates**

```powershell
python -m pytest -q
python -m compileall -q src tests
git diff --check
git status --short --branch
```

Expected: the full suite passes, compile and diff checks exit zero, and unrelated pre-existing workspace files remain untouched.
