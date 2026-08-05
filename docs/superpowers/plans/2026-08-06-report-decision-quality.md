# 报告决策质量改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将主线报告改造成名称、来源、市场状态和策略池均可审计的次日决策简报。

**Architecture:** 用独立的名称解析器统一跨缓存名称；在 LimitPoolProvider 上层增加非空结果交叉校验；用统一 regime 结果供择时、反弹和决策看板引用；将股票池和历史场景统计拆成可测试的纯函数，最后由 legacy_tracker 组装报告。

**Tech Stack:** Python 3.10+, pandas, requests, pytest, 现有 ECharts 单文件 HTML 生成器。

## Global Constraints

- 不改变 raw/qfq 价格字段和沪深北 universe 口径。
- 不拼接不同涨跌停来源的股票集合；来源差异必须可追踪。
- AI 失败可以规则降级，但不能伪装为 AI 成功。
- 没有足够历史样本时不显示概率。
- 所有新增行为先写离线测试并观察 RED。
- 不修改主工作树已有未提交文件。

---

### Task 1: Name Resolver and Source Audit

**Files:**
- Create: `src/data_sources/name_resolver.py`
- Modify: `src/legacy_tracker.py`
- Test: `tests/test_name_resolver.py`

- [ ] Write tests for current limit name overriding stale industry name, latest universe fallback, and conflict records.
- [ ] Run the focused tests and verify the missing resolver/behavior fails.
- [ ] Implement a pure resolver returning names, sources, and conflicts.
- [ ] Route ladder, Top30, sector leaders, and focus-pool input names through the resolver.
- [ ] Run the focused tests and existing provider tests.

### Task 2: Non-empty Limit Pool Reconciliation

**Files:**
- Modify: `src/data_sources/limit_pool_provider.py`
- Create: `src/data_sources/limit_pool_reconciliation.py`
- Modify: `src/legacy_tracker.py`
- Test: `tests/test_limit_pool_reconciliation.py`

- [ ] Write tests for matching sources, count drift over 5%, high-board code drift, and insufficient secondary data.
- [ ] Run focused tests and verify RED.
- [ ] Implement count/code reconciliation without merging source sets.
- [ ] Propagate `partial` status and source difference message into the quality gate and report metadata.
- [ ] Run focused provider/reconciliation tests.

### Task 3: Unified Regime and AI Provenance

**Files:**
- Create: `src/data_sources/market_regime.py`
- Modify: `src/timing_signal.py`
- Modify: `src/market_stance.py`
- Modify: `src/ai_rebound.py`
- Modify: `src/decision_dashboard.py`
- Modify: `src/legacy_tracker.py`
- Test: `tests/test_market_regime.py`, `tests/test_smoke.py`

- [ ] Write tests proving strong breadth plus broken ladder resolves to one regime code and AI failure gets a rule-fallback label.
- [ ] Run focused tests and verify RED.
- [ ] Implement the regime result and pass it to all renderers.
- [ ] Replace fixed scenario probabilities with sample metadata or `样本不足`.
- [ ] Run focused tests and HTML string assertions.

### Task 4: Role-aware Focus Pool and Historical Samples

**Files:**
- Modify: `src/screener.py`
- Create: `src/data_sources/focus_pool_stats.py`
- Modify: `src/decision_dashboard.py`
- Modify: `src/legacy_tracker.py`
- Test: `tests/test_focus_pool.py`, `tests/test_focus_pool_stats.py`

- [ ] Write tests for space/trend/low-level role exclusivity, current leader not entering trend pool, ST filtering, and insufficient samples.
- [ ] Run focused tests and verify RED.
- [ ] Implement role filters and historical T+1/T+3 sample summaries.
- [ ] Render sample count/result instead of hardcoded probabilities.
- [ ] Run focused tests and report generation smoke test.

### Task 5: Report Hierarchy and Evidence Metadata

**Files:**
- Modify: `src/legacy_tracker.py`
- Modify: `src/decision_dashboard.py`
- Modify: `docs/howto/run-mainline-tracker.md`
- Test: `tests/test_report_rendering.py`

- [ ] Write tests for ordered regime/quality/focus sections and visible AI fallback metadata.
- [ ] Run focused tests and verify RED.
- [ ] Add compact evidence header, collapse detailed research sections, and include ladder promotion/negative-feedback KPIs.
- [ ] Run focused rendering tests and inspect the generated HTML.

### Task 6: Full Verification and Integration

**Files:**
- Modify only files covered by Tasks 1-5.

- [ ] Run `python -m pytest -q`.
- [ ] Run `python -m compileall -q src tools tests`.
- [ ] Run `python tools/audit_data_integrity.py --quiet`.
- [ ] Generate a local report and inspect source/quality/regime/fallback text.
- [ ] Commit, push `codex/market-data-rebuild`, merge into remote `master` using an isolated integration worktree.
