# Limit Pool Fallback Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add truthful, source-attributed fallback chains for Shanghai/Shenzhen/Beijing limit-up and limit-down pools without changing report calculations or the existing publication quality gate.

**Architecture:** Keep `LimitPoolProvider` as the orchestration boundary. Add a focused HTTP adapter module for Eastmoney push2ex and THS, then let the provider select the first structurally valid non-empty source per pool while preserving valid-zero and failure state separately.

**Tech Stack:** Python 3.10+, pandas, requests, pytest, existing `FetchResult` / `FetchStatus` contracts.

## Global Constraints

- Primary source remains AkShare.
- Limit-up chain is `akshare_em -> eastmoney_push2ex -> ths_limit_up`.
- Limit-down chain is `akshare_em -> eastmoney_push2ex`.
- No `mootdx` dependency and no changes to the raw/qfq price contract.
- Every output row and aggregate fetch result records the actual selected source.
- A missing limit-up or limit-down pool remains `partial`/`failed` and is blocked by the existing quality gate.
- All tests are offline; real network calls are optional smoke checks only.
- External endpoint contracts are adapted minimally from `simonlin1212/a-stock-data` V3.6.0 (Apache-2.0).

---

### Task 1: Direct HTTP Source Adapters

**Files:**
- Create: `src/data_sources/limit_pool_sources.py`
- Create: `tests/test_limit_pool_sources.py`

**Interfaces:**
- Produces: `EastmoneyLimitPoolSource.fetch_zt(date: str) -> pd.DataFrame`
- Produces: `EastmoneyLimitPoolSource.fetch_dt(date: str) -> pd.DataFrame`
- Produces: `ThsLimitUpSource.fetch_zt(date: str) -> pd.DataFrame`
- Returned frames expose `code`, `name`, and `limit_count` columns.

The test module defines this offline transport fixture before the endpoint tests:

```python
class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers, timeout))
        return FakeResponse(self.payload)
```

- [ ] **Step 1: Write failing Eastmoney parser tests**

```python
def test_eastmoney_limit_pool_parses_beijing_code_and_count():
    session = FakeSession({"data": {"pool": [
        {"c": "920117", "n": "国航远洋", "lbc": 2},
    ]}})
    source = EastmoneyLimitPoolSource(session=session, min_interval=0)

    frame = source.fetch_zt("2026-08-05")

    assert frame.to_dict("records") == [
        {"code": "920117", "name": "国航远洋", "limit_count": 2}
    ]
    assert session.calls[0][0].endswith("/getTopicZTPool")
    assert session.calls[0][1]["date"] == "20260805"


def test_eastmoney_missing_data_node_raises_schema_error():
    source = EastmoneyLimitPoolSource(
        session=FakeSession({"result": "ok"}), min_interval=0
    )
    with pytest.raises(ValueError, match="data.pool"):
        source.fetch_dt("2026-08-05")
```

- [ ] **Step 2: Run Eastmoney tests and verify RED**

Run: `python -m pytest tests/test_limit_pool_sources.py -q`

Expected: collection/import failure because `limit_pool_sources` does not exist.

- [ ] **Step 3: Implement the minimal Eastmoney adapter**

```python
class EastmoneyLimitPoolSource:
    BASE_URL = "https://push2ex.eastmoney.com"
    UT = "7eea3edcaed734bea9cbfc24409ed989"

    def __init__(self, session=None, min_interval=1.0, clock=None, sleep=None):
        self.session = session or _retry_session()
        self.min_interval = max(0.0, float(min_interval))
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self._last_call = None

    def fetch_zt(self, date):
        return self._fetch("getTopicZTPool", "fbt:asc", date, "lbc")

    def fetch_dt(self, date):
        return self._fetch("getTopicDTPool", "fund:asc", date, None)
```

`_fetch()` must call `raise_for_status()`, require a dictionary `data` node and list `data.pool`, normalize the date to `YYYYMMDD`, drop rows without a six-digit code, and raise when a non-empty payload produces no valid rows.

- [ ] **Step 4: Run Eastmoney tests and verify GREEN**

Run: `python -m pytest tests/test_limit_pool_sources.py -q`

Expected: Eastmoney tests pass.

- [ ] **Step 5: Write failing THS fixture tests**

```python
def test_ths_limit_pool_extracts_high_days_count():
    source = ThsLimitUpSource(session=FakeSession({"data": {"info": [
        {"code": "920117", "name": "国航远洋", "high_days": "3天2板"},
    ]}}))

    frame = source.fetch_zt("2026-08-05")

    assert frame.to_dict("records") == [
        {"code": "920117", "name": "国航远洋", "limit_count": 2}
    ]


def test_ths_missing_info_node_raises_schema_error():
    source = ThsLimitUpSource(session=FakeSession({"data": {}}))
    with pytest.raises(ValueError, match="data.info"):
        source.fetch_zt("2026-08-05")
```

- [ ] **Step 6: Run THS tests and verify RED**

Run: `python -m pytest tests/test_limit_pool_sources.py -q`

Expected: failure because `ThsLimitUpSource` is missing.

- [ ] **Step 7: Implement the minimal THS adapter**

```python
class ThsLimitUpSource:
    URL = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"

    def __init__(self, session=None):
        self.session = session or requests.Session()

    def fetch_zt(self, date):
        response = self.session.get(
            self.URL,
            params={
                "page": 1,
                "limit": 200,
                "field": THS_FIELDS,
                "filter": "HS,GEM2STAR",
                "order_field": "330324",
                "order_type": "0",
                "date": date.replace("-", ""),
            },
            headers=DEFAULT_HEADERS,
            timeout=10,
        )
        response.raise_for_status()
        # Require data.info and convert high_days' final board count to int.
```

- [ ] **Step 8: Run adapter tests and commit**

Run: `python -m pytest tests/test_limit_pool_sources.py -q`

Expected: all adapter tests pass.

```powershell
git add src/data_sources/limit_pool_sources.py tests/test_limit_pool_sources.py
git commit -m "feat: add direct limit pool source adapters"
```

---

### Task 2: Truthful Provider Fallback State Machine

**Files:**
- Modify: `src/data_sources/limit_pool_provider.py`
- Modify: `tests/test_market_providers.py`

**Interfaces:**
- Consumes: ordered `(source_name, fetcher)` pairs.
- Produces: `LimitPoolProvider(..., zt_fallbacks=None, dt_fallbacks=None)`.
- Produces: aggregate source strings such as `ZT:eastmoney_push2ex|DT:akshare_em`.

- [ ] **Step 1: Write failing short-circuit and fallback tests**

```python
def test_limit_pool_primary_success_does_not_call_fallback():
    calls = []
    zt = pd.DataFrame({"代码": ["600000"], "名称": ["浦发银行"], "连板数": [1]})
    provider = LimitPoolProvider(
        fetch_zt=lambda _date: zt,
        fetch_dt=lambda _date: pd.DataFrame(),
        zt_fallbacks=[("fallback", lambda _date: calls.append("fallback"))],
    )

    result = provider.fetch_day("2026-08-05")

    assert result.status is FetchStatus.SUCCESS
    assert calls == []
    assert result.source == "ZT:akshare_em|DT:akshare_em"


def test_limit_pool_uses_fallback_after_primary_error():
    fallback = pd.DataFrame({"code": ["920117"], "name": ["国航远洋"],
                             "limit_count": [2]})
    provider = LimitPoolProvider(
        fetch_zt=lambda _date: (_ for _ in ()).throw(TimeoutError("primary timeout")),
        fetch_dt=lambda _date: pd.DataFrame(),
        zt_fallbacks=[("eastmoney_push2ex", lambda _date: fallback)],
    )

    result = provider.fetch_day("2026-08-05")

    assert result.status is FetchStatus.SUCCESS
    assert result.source == "ZT:eastmoney_push2ex|DT:akshare_em"
    assert result.data.iloc[0].to_dict()["code"] == "bj920117"
    assert "primary timeout" in result.message
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_market_providers.py -k "limit_pool_primary_success or limit_pool_uses_fallback" -q`

Expected: `TypeError` because fallback constructor arguments are not supported.

- [ ] **Step 3: Implement ordered source attempts and source-aware normalization**

```python
def __init__(self, fetch_zt=None, fetch_dt=None, status_store=None, now=None,
             zt_fallbacks=None, dt_fallbacks=None):
    self.zt_sources = [("akshare_em", fetch_zt or self._default_zt)]
    self.dt_sources = [("akshare_em", fetch_dt or self._default_dt)]
    self.zt_sources.extend(zt_fallbacks or [])
    self.dt_sources.extend(dt_fallbacks or [])


def _fetch_pool(self, sources, date, pool_type):
    errors = []
    valid_empty = None
    for source_name, fetcher in sources:
        try:
            frame = self._normalize(fetcher(date), date, pool_type, source_name)
            if not frame.empty:
                return frame, source_name, True, errors
            valid_empty = (frame, source_name)
        except Exception as exc:
            errors.append(f"{pool_type}/{source_name}: {exc}")
    if valid_empty is not None:
        frame, source_name = valid_empty
        return frame, source_name, True, errors
    return pd.DataFrame(columns=LIMIT_POOL_COLUMNS), "unavailable", False, errors
```

Update `_normalize(..., source_name)` to accept DataFrame or list-of-dict input, require a code column for non-empty data, normalize every code, drop invalid rows, and set the selected source on every row.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_market_providers.py -k "limit_pool" -q`

Expected: new tests pass; update the existing timeout test from `FAILED` to `PARTIAL` because the empty DT side is still a valid available pool.

- [ ] **Step 5: Write failing zero/partial/failed tests**

```python
def test_limit_pool_all_valid_empty_sources_return_zero():
    provider = LimitPoolProvider(
        fetch_zt=lambda _date: pd.DataFrame(),
        fetch_dt=lambda _date: pd.DataFrame(),
        zt_fallbacks=[("fallback", lambda _date: pd.DataFrame())],
    )
    assert provider.fetch_day("2026-08-05").status is FetchStatus.ZERO


def test_limit_pool_one_available_side_is_partial():
    provider = LimitPoolProvider(
        fetch_zt=lambda _date: pd.DataFrame({"code": ["600000"]}),
        fetch_dt=lambda _date: (_ for _ in ()).throw(TimeoutError("dt timeout")),
    )
    result = provider.fetch_day("2026-08-05")
    assert result.status is FetchStatus.PARTIAL
    assert set(result.data["pool_type"]) == {"ZT"}


def test_limit_pool_all_sources_failed_is_failed():
    fail = lambda _date: (_ for _ in ()).throw(TimeoutError("offline"))
    result = LimitPoolProvider(fetch_zt=fail, fetch_dt=fail).fetch_day("2026-08-05")
    assert result.status is FetchStatus.FAILED
```

- [ ] **Step 6: Run tests and verify RED, then complete status aggregation**

Run: `python -m pytest tests/test_market_providers.py -k "limit_pool" -q`

Expected before implementation: at least one status assertion fails.

Aggregation rules:

```python
if zt_available and dt_available:
    status = FetchStatus.ZERO if data.empty else FetchStatus.SUCCESS
elif zt_available or dt_available:
    status = FetchStatus.PARTIAL
else:
    status = FetchStatus.FAILED
```

- [ ] **Step 7: Run provider tests and commit**

Run: `python -m pytest tests/test_market_providers.py -q`

Expected: all provider tests pass.

```powershell
git add src/data_sources/limit_pool_provider.py tests/test_market_providers.py
git commit -m "feat: add truthful limit pool fallback chain"
```

---

### Task 3: Default Wiring, Documentation, and Full Verification

**Files:**
- Modify: `src/data_sources/limit_pool_provider.py`
- Modify: `tests/test_market_providers.py`
- Modify: `docs/howto/run-mainline-tracker.md`

**Interfaces:**
- Consumes: `EastmoneyLimitPoolSource` and `ThsLimitUpSource`.
- Produces: default production chains while preserving explicit test/custom fetcher isolation.

- [ ] **Step 1: Write failing default-chain construction test**

```python
def test_limit_pool_default_chain_contains_direct_and_independent_fallbacks(monkeypatch):
    provider = LimitPoolProvider()
    assert [name for name, _ in provider.zt_sources] == [
        "akshare_em", "eastmoney_push2ex", "ths_limit_up"
    ]
    assert [name for name, _ in provider.dt_sources] == [
        "akshare_em", "eastmoney_push2ex"
    ]


def test_custom_primary_does_not_enable_network_fallbacks_implicitly():
    provider = LimitPoolProvider(
        fetch_zt=lambda _date: pd.DataFrame(),
        fetch_dt=lambda _date: pd.DataFrame(),
    )
    assert [name for name, _ in provider.zt_sources] == ["akshare_em"]
    assert [name for name, _ in provider.dt_sources] == ["akshare_em"]
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_market_providers.py -k "default_chain or custom_primary" -q`

Expected: default chain lacks fallback names.

- [ ] **Step 3: Wire default adapters without surprising injected callers**

When `fetch_zt is None`, instantiate one shared `EastmoneyLimitPoolSource` and one
`ThsLimitUpSource`, then append their bound methods. When a custom primary fetcher is supplied,
append only explicitly supplied fallback tuples. Apply the same rule independently to DT.

- [ ] **Step 4: Document runtime behavior**

Add a short section to `docs/howto/run-mainline-tracker.md` describing:

- the two fallback chains;
- `fetch_status.csv` source strings;
- `partial/failed` publication blocking;
- Eastmoney throttling and THS being ZT-only.

- [ ] **Step 5: Run focused and full verification**

Run:

```powershell
python -m pytest tests/test_limit_pool_sources.py tests/test_market_providers.py -q
python -m pytest -q
python -m compileall -q src tools tests
python tools/audit_data_integrity.py --quiet
```

Expected:

- all tests pass;
- compileall exits 0;
- data audit confirms the current SH/SZ/BJ universe and raw/qfq cache are healthy.

- [ ] **Step 6: Optional live smoke test**

Run one current trading-day fetch through each direct adapter. Treat network/proxy unavailability as
an observed environment limitation, not a unit-test failure. Do not overwrite production caches.

- [ ] **Step 7: Commit and push**

```powershell
git add src/data_sources/limit_pool_provider.py docs/howto/run-mainline-tracker.md tests/test_market_providers.py
git commit -m "docs: document limit pool fallback behavior"
git push origin codex/market-data-rebuild
```

After push, merge the feature branch into `master` using a non-force merge that preserves any new
GitHub Actions cache commit on the remote branch.
