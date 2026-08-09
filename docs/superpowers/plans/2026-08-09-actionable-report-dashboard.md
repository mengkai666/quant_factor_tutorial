# Actionable Report Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the report's generic observation blocks with a compact, data-backed execution plan containing position, named stocks, conditional buy/sell actions, invalidation rules, and risk controls.

**Architecture:** Keep canonical market facts and AI output unchanged. Extend publication policy so observation reports may publish bounded conditional actions without publishing unsupported probabilities, then build a deterministic action plan from structured echelon rows and render the same plan in standalone and embedded dashboards.

**Tech Stack:** Python 3, pandas-compatible inputs, server-rendered single-file HTML/CSS, pytest, Playwright CLI.

## Global Constraints

- Stock names must come from structured echelon or progression data; AI text cannot create a stock row.
- Observation mode may publish positions and conditional actions but not probabilities or the legacy focus pool.
- Facts-only mode remains action-free and position-free.
- Missing previous-day snapshots must not render a synthetic `0%` promotion rate.
- Risk rows can only render hold, reduce, avoid, or exit actions.
- Data quality details stay collapsed after the execution plan.
- Desktop and mobile layouts must not overflow or nest cards.

---

### Task 1: Permit Bounded Actions In Observation Reports

**Files:**
- Modify: `src/report_logic.py:37-61`
- Test: `tests/test_report_logic.py`
- Test: `tests/test_report_integration.py`

**Interfaces:**
- Consumes: `ReportPolicy.from_mode(mode: Any) -> ReportPolicy`
- Produces: observation policy with `allow_positions=True`, `allow_actions=True`, `allow_probabilities=False`, and `allow_focus_pool=False`

- [ ] **Step 1: Write the failing policy test**

```python
def test_observation_policy_allows_bounded_actions_without_probabilities_or_legacy_pool():
    policy = ReportPolicy.from_mode("observation")
    assert policy.allow_positions is True
    assert policy.allow_actions is True
    assert policy.allow_probabilities is False
    assert policy.allow_focus_pool is False
    assert scan_forbidden_semantics("建议仓位 2-4 成，回封买入", policy) == []
    assert "概率" in scan_forbidden_semantics("上涨概率 70%", policy)
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest -q tests/test_report_logic.py::test_observation_policy_allows_bounded_actions_without_probabilities_or_legacy_pool`

Expected: FAIL because observation mode currently disables positions and actions.

- [ ] **Step 3: Implement the policy change**

```python
if normalized == "observation":
    return cls(normalized, True, True, True, False, True, True, True, False)
```

Update old observation tests so they continue to forbid unconditional legacy timing copy but permit the new conditional action plan. Keep facts-only assertions unchanged.

- [ ] **Step 4: Run policy and integration tests**

Run: `python -m pytest -q tests/test_report_logic.py tests/test_report_integration.py`

Expected: PASS.

---

### Task 2: Build A Deterministic Action Plan

**Files:**
- Modify: `src/decision_dashboard.py:13-360`
- Test: `tests/test_report_rendering.py`

**Interfaces:**
- Consumes: `ctx["echelon"]`, `ctx["progression_chain"]`, `ctx["mainline_review"]`, `ctx["market_state"]`, `ctx["publication_mode"]`, and `_overall_judgement(ctx, state)`
- Produces: `_build_action_plan(ctx: dict, judgement: dict | None = None) -> dict[str, Any]`

The returned shape is:

```python
{
    "position": "2-4 成",
    "posture": "试错",
    "core_action": "只做低位晋级，高位孤峰不追",
    "groups": [
        {
            "code": "attack",
            "label": "进攻组",
            "position": "单票不超过 1 成",
            "rows": [{
                "name": "样例股份",
                "code": "sh600001",
                "height": 2,
                "sector": "AI算力",
                "action": "分歧回封买入",
                "trigger": "首次回封且同题材至少 2 只保持红盘",
                "invalid": "跌破昨日收盘且题材无跟随",
            }],
        },
    ],
}
```

- [ ] **Step 1: Add failing tests for grouping and position**

```python
def test_action_plan_builds_named_attack_confirm_and_risk_groups_from_echelon():
    ctx = _context()
    ctx["echelon"] = [
        {"height": "2连板", "stock_details": [{"name": "江化微", "code": "sh603078", "ml": "AI算力"}]},
        {"height": "3连板", "stock_details": [{"name": "沃格光电", "code": "sh603773", "ml": "AI算力"}]},
        {"height": "10连板", "stock_details": [{"name": "爱丽家居", "code": "sh603221", "ml": "其它"}]},
    ]
    ctx["scene"] = "高位承压 · 结构换挡"
    plan = _build_action_plan(ctx)
    assert plan["position"] == "2-4 成"
    assert [group["code"] for group in plan["groups"]] == ["attack", "confirm", "risk"]
    assert "江化微" in str(plan)
    assert "沃格光电" in str(plan)
    assert "爱丽家居" in str(plan)
    assert "买入" in str(plan["groups"][0])
    assert "减仓" in str(plan["groups"][2])
```

Add separate tests for facts-only output, invalid/missing stock codes, group de-duplication, and no-candidate fallback.

- [ ] **Step 2: Run the builder tests and verify RED**

Run: `python -m pytest -q tests/test_report_rendering.py -k "action_plan"`

Expected: FAIL because `_build_action_plan` does not exist.

- [ ] **Step 3: Preserve the canonical echelon input**

Add `"echelon": list(echelon or [])` to `build_dashboard_ctx`'s returned dictionary.

- [ ] **Step 4: Implement stock normalization and plan rules**

Add private helpers in `decision_dashboard.py`:

```python
def _height_number(value: Any) -> int: ...
def _echelon_action_rows(ctx: dict) -> list[dict[str, Any]]: ...
def _build_action_plan(ctx: dict, judgement: dict | None = None) -> dict[str, Any]: ...
```

Rules:

- Height 2 becomes the attack group, limited to five rows.
- Height 3-5 becomes the confirm group, limited to five rows.
- Highest height and negative progression feedback become the risk group, limited to four rows.
- Normalize codes with `normalize_stock_code`; skip rows with no code or no name.
- De-duplicate by normalized code, with risk taking precedence over confirm and confirm over attack.
- Facts-only returns `position="空仓"`, `core_action="不开新仓"`, and no stock groups.
- Breadth below `0.45` or at least `15` limit-down stocks returns `0-2 成`.
- A high-level pressure, retreat, height-gap, or structural-shift judgement returns `2-4 成`.
- Breadth at least `0.65`, ladder at least `12`, at most `5` limit-down stocks, and no height gap returns `6-8 成`.
- Remaining publishable states return `2-4 成`.

- [ ] **Step 5: Run builder tests and verify GREEN**

Run: `python -m pytest -q tests/test_report_rendering.py -k "action_plan"`

Expected: PASS.

---

### Task 3: Replace Generic Blocks With The Execution Board

**Files:**
- Modify: `src/decision_dashboard.py:1502-3167`
- Test: `tests/test_report_rendering.py`
- Test: `tests/test_report_integration.py`
- Test: `tests/test_market_regime.py`

**Interfaces:**
- Consumes: `_build_action_plan(ctx, judgement)`
- Produces: `_action_plan_html(plan: dict, prefix: str = "") -> str` and `_compact_market_facts_html(ctx: dict, prefix: str = "") -> str`

- [ ] **Step 1: Write failing rendering tests**

For both `generate_dashboard_html` and `generate_dashboard_section`, assert:

```python
assert "建议仓位" in html
assert "2-4 成" in html
assert "明日执行计划" in html
assert "江化微" in html
assert "分歧回封买入" in html
assert "沃格光电" in html
assert "晋级确认后加仓" in html
assert "爱丽家居" in html
assert "断板减仓" in html
assert "判断依据" not in html
assert "明日验证路径" not in html
assert "等待验证信号" not in html
assert "连板复盘 · 连板质量" not in html
assert "主线复盘 · 主线集中度" not in html
assert "历史预测复盘" not in html
```

Add a scored prediction fixture and assert `历史预测复盘` is rendered only when `scored_count > 0`.

- [ ] **Step 2: Run rendering tests and verify RED**

Run: `python -m pytest -q tests/test_report_rendering.py tests/test_market_regime.py`

Expected: FAIL on the new execution-board assertions.

- [ ] **Step 3: Implement compact render helpers**

`_action_plan_html` renders one unframed section with three semantic columns. Each stock row contains a single flat block, not nested cards. `_compact_market_facts_html` renders only four scan-friendly facts and replaces invalid `0/N` promotion metrics with `样本待补`.

- [ ] **Step 4: Recompose both dashboard templates**

For standalone and embedded renderers:

- Add position and core action directly below the judgement headline.
- Render compact market facts, then the execution board.
- Omit `_stance_detail_html`.
- Omit generic scenario cards and the legacy focus-pool section.
- Omit verbose lianban and mainline review blocks.
- Render prediction review only when `scored_count > 0`.
- Render `_quality_html` after execution content and keep it collapsed.
- Keep facts-only output free of positions, stock actions, and targets.

- [ ] **Step 5: Add responsive CSS**

Use stable three-column tracks on desktop and one column below `760px`. Stock rows use `min-width:0`, wrapping labels, visible focus states for `<details>`, and no viewport-scaled font sizes.

- [ ] **Step 6: Run report rendering suites and verify GREEN**

Run: `python -m pytest -q tests/test_report_rendering.py tests/test_report_integration.py tests/test_market_regime.py tests/test_report_logic.py`

Expected: PASS.

---

### Task 4: Regenerate And Visually Verify The Product Report

**Files:**
- Regenerate: `output/site/reports/2026-08-07.html`
- Regenerate: `output/site/dashboards/2026-08-07.html`
- Regenerate: `output/site/index.html`

**Interfaces:**
- Consumes: production report entry point and the existing AI/cache configuration
- Produces: a report whose first decision viewport contains a position, named targets, actions, and invalidation rules

- [ ] **Step 1: Run the full test and compile gates**

Run: `python -m pytest -q`

Run: `python -m compileall -q src tests`

Expected: all tests pass and compile exits zero.

- [ ] **Step 2: Regenerate the target report**

Run in PowerShell:

```powershell
$env:REPORT_DATE='2026-08-07'
$env:EMAIL_ENABLE='0'
python 'src/主线强度追踪.py'
```

Expected: report, dashboard, and index publish successfully.

- [ ] **Step 3: Check required and forbidden copy**

Run:

```powershell
rg -n "建议仓位|核心动作|明日执行计划|买入|加仓|减仓|失效条件|判断依据|历史预测复盘|明日验证路径|等待验证信号|连板复盘 · 连板质量|主线复盘 · 主线集中度" output/site/reports/2026-08-07.html output/site/dashboards/2026-08-07.html
```

Expected: actionable copy and named stocks appear; removed generic sections do not.

- [ ] **Step 4: Verify desktop and mobile in a real browser**

Use Playwright to capture `1440x1000` and `390x844` screenshots of the local report. Confirm the execution board is visible, text wraps, no horizontal overflow exists, and no large empty card remains.

- [ ] **Step 5: Run final repository checks**

Run: `git diff --check`

Run: `git status --short`

Expected: no whitespace errors; unrelated user changes remain untouched.
