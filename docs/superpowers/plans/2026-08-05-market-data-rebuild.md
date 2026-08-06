# Unified Market Data Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a cold-startable Shanghai/Shenzhen/Beijing A-share data layer with explicit raw/qfq prices, fetch status, pre-publication quality gates, and a compatible thin application entrypoint.

**Architecture:** External APIs are isolated behind five injectable Providers under `src/data_sources`. Providers emit canonical DataFrames and persisted fetch-status records; a data pipeline validates candidate caches before atomic replacement, and analysis consumes explicit `close_raw` or `close_qfq` columns. The legacy entry delegates orchestration only after the new data model and gates are active.

**Tech Stack:** Python 3.10+, pandas, requests, akshare, baostock, pytest, CSV/JSON atomic file storage.

## Global Constraints

- Universe includes all Shanghai, Shenzhen, and Beijing A shares.
- A/D uses unadjusted close; returns/rankings/backtests use qfq close; never alias either to ambiguous `close` in production data.
- Suspended stocks remain in universe and are excluded from the daily A/D denominator.
- Critical quality failures must stop report generation, email, and Pages publication.
- Preserve the command `python src/主线强度追踪.py`.
- Candidate caches replace official caches only after full validation via same-directory `os.replace`.

---

### Task 1: Canonical contracts and fetch-status store

**Files:**
- Create: `src/data_sources/__init__.py`
- Create: `src/data_sources/models.py`
- Create: `src/data_sources/fetch_status.py`
- Test: `tests/test_data_source_models.py`

**Interfaces:**
- Produces: `normalize_code(value) -> str`, `FetchStatus`, `FetchResult`, `FetchStatusStore.record(...)`, `FetchStatusStore.latest(...)`.

- [x] Write tests for SH/SZ/BJ code normalization and rejection of unsupported identifiers.
- [x] Run focused tests and verify RED due to missing modules.
- [x] Implement enums/dataclasses and atomic status CSV upsert.
- [x] Run focused tests and full suite; verify GREEN.

### Task 2: Calendar and沪深北 Universe Providers

**Files:**
- Create: `src/data_sources/calendar_provider.py`
- Create: `src/data_sources/universe_provider.py`
- Modify: `src/paths.py`
- Test: `tests/test_universe_provider.py`

**Interfaces:**
- Produces: `CalendarProvider.trading_days(start, end)`, `CalendarProvider.latest_closed_day(now)`, `UniverseProvider.fetch()`, `UniverseProvider.refresh(path)`.
- Universe columns: `code,raw_code,exchange,name,list_date,delist_date,list_status,industry,source,updated_at`.

- [x] Write contract tests using injected SH/SZ/BJ frames, including merge with prior delisted rows.
- [x] Verify RED.
- [x] Implement providers and atomic universe refresh.
- [x] Add AkShare adapters with fallback endpoint selection and explicit failure status.
- [x] Verify focused and full tests GREEN.

### Task 3: Explicit raw/qfq Price Provider and analysis helpers

**Files:**
- Create: `src/data_sources/price_provider.py`
- Create: `src/market_data.py`
- Test: `tests/test_price_provider.py`

**Interfaces:**
- Produces: `PriceProvider.fetch_range(universe, dates)`, `PriceProvider.rebuild(...)`, `compute_advance_decline(prices)`, `compute_period_returns(prices, periods)`.
- Price columns: `date,code,close_raw,close_qfq,trade_status,source_raw,source_qfq,fetched_at`.

- [x] Write tests proving A/D uses only raw prices and excludes suspended/missing/not-listed rows.
- [x] Write tests proving returns use only qfq and do not include stale/retired codes through global ffill.
- [x] Verify RED.
- [x] Implement injected-source provider and Beijing-capable AkShare raw/qfq adapter.
- [x] Implement candidate write and atomic replacement.
- [x] Verify focused/full tests GREEN.

### Task 4: LimitPoolProvider, PlateProvider, and truthful zero states

**Files:**
- Create: `src/data_sources/limit_pool_provider.py`
- Create: `src/data_sources/plate_provider.py`
- Test: `tests/test_market_providers.py`

**Interfaces:**
- Produces: `LimitPoolProvider.fetch_day(date) -> FetchResult`, `PlateProvider.fetch_codes(codes, date) -> FetchResult`.

- [x] Write tests distinguishing successful empty datasets (`zero`) from malformed/timeout responses (`failed`) and partial coverage (`partial`).
- [x] Verify RED.
- [x] Implement providers by moving/encapsulating existing CLS/EM parsing and canonical code conversion.
- [x] Verify focused/full tests GREEN.

### Task 5: Quality gate and expanded integrity CLI

**Files:**
- Create: `src/data_sources/quality_gate.py`
- Modify: `tools/audit_data_integrity.py`
- Test: `tests/test_quality_gate.py`

**Interfaces:**
- Produces: `QualityIssue`, `QualityReport`, `DataQualityError`, `MarketDataQualityGate.validate(...)`, CLI exit 0/1.

- [x] Write tests for missing source/adjustment columns, duplicate keys, invalid prices, coverage, abnormal jumps, systematic adjustment switches, universe exchange coverage, listing/trading status, and critical fetch statuses.
- [x] Verify RED.
- [x] Implement deterministic checks and JSON report output.
- [x] Update CLI to validate the new schema and fail critical defects.
- [x] Verify focused/full tests GREEN.

### Task 6: Data pipeline, migration CLI, and cold-start rebuild

**Files:**
- Create: `src/pipeline/__init__.py`
- Create: `src/pipeline/data_pipeline.py`
- Create: `tools/rebuild_market_data.py`
- Test: `tests/test_data_pipeline.py`

**Interfaces:**
- Produces: `DataPipeline.prepare(target_date)`, `DataPipeline.validate_and_promote(...)`, rebuild CLI with `--start`, `--end`, `--candidate-only`.

- [x] Write integration tests proving invalid candidates do not replace official caches and valid candidates replace atomically.
- [x] Verify RED.
- [x] Implement cold-start universe refresh, candidate rebuild, status persistence, quality report, and promotion.
- [x] Run a small live沪深北 cold-start sample, then full requested history rebuild.
- [x] Validate rebuilt candidate and promote only on zero critical issues.

### Task 7: Pre-report gate and CI publication ordering

**Files:**
- Modify: `src/主线强度追踪.py`
- Modify: `.github/workflows/daily_run.yml`
- Test: `tests/test_publication_gate.py`

**Interfaces:**
- Produces: `run_preflight_gate(...)`; process exits nonzero before HTML/publish/email on critical data failure.

- [x] Write tests proving critical defects prevent report and delivery callbacks.
- [x] Verify RED.
- [x] Integrate preflight before analysis/reporting and make homepage use the same quality result.
- [x] Move CI gate before script execution and remove `continue-on-error` for the core gate.
- [x] Verify focused/full tests GREEN.

### Task 8: Split orchestration and preserve compatibility entry

**Files:**
- Create: `src/pipeline/analysis_pipeline.py`
- Create: `src/pipeline/report_pipeline.py`
- Create: `src/pipeline/delivery_pipeline.py`
- Create: `src/app.py`
- Modify: `src/主线强度追踪.py`
- Test: `tests/test_app_pipeline.py`

**Interfaces:**
- Produces: stage classes/functions with injected collaborators and `app.main() -> int`.

- [x] Write orchestration tests proving stage order data→analysis→report→delivery and short-circuit on quality failure.
- [x] Verify RED.
- [x] Extract orchestration without duplicating provider logic; leave compatibility entry as a thin delegator.
- [x] Verify original command imports and offline fixture run.

### Task 9: Documentation and completion audit

**Files:**
- Modify: `README.md`
- Modify: `docs/howto/run-mainline-tracker.md`
- Modify: `.gitignore`

- [x] Document schemas, cold start, rebuild, BSE inclusion, status meanings, and failure recovery.
- [x] Run `python -m pytest -q` and `python -m compileall -q src tools tests`.
- [x] Run new integrity CLI against rebuilt official caches.
- [x] Run a target-date offline main pipeline and verify report generation only after gate success.
- [x] Audit each original objective against files and command output; resolve every gap before completion.
