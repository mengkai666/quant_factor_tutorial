import pandas as pd
import pytest
import threading
import time

from data_sources.limit_pool_provider import LIMIT_POOL_COLUMNS, LimitPoolProvider
from data_sources.models import FetchResult, FetchStatus
from data_sources.plate_provider import PLATE_COLUMNS, PlateProvider


def test_limit_pool_successful_empty_response_is_zero():
    provider = LimitPoolProvider(fetch_zt=lambda _: pd.DataFrame(),
                                 fetch_dt=lambda _: pd.DataFrame())
    result = provider.fetch_day("2026-08-05")
    assert result.status is FetchStatus.ZERO
    assert result.data.columns.tolist() == LIMIT_POOL_COLUMNS
    assert result.actual_count == 0


def test_limit_pool_history_uses_same_provider_and_records_each_day():
    seen = []

    def fetch_zt(date):
        seen.append(("ZT", date))
        return pd.DataFrame({"代码": ["600000"], "名称": ["浦发银行"], "连板数": [1]})

    def fetch_dt(date):
        seen.append(("DT", date))
        return pd.DataFrame()

    provider = LimitPoolProvider(fetch_zt=fetch_zt, fetch_dt=fetch_dt)
    history = provider.fetch_history(["2026-08-04", "2026-08-05"])

    assert list(history) == ["2026-08-04", "2026-08-05"]
    assert all(item.status is FetchStatus.SUCCESS for item in history.values())
    assert seen == [("ZT", "2026-08-04"), ("DT", "2026-08-04"),
                    ("ZT", "2026-08-05"), ("DT", "2026-08-05")]


def test_limit_pool_timeout_with_other_side_zero_is_partial_not_zero():
    provider = LimitPoolProvider(
        fetch_zt=lambda _: (_ for _ in ()).throw(TimeoutError("timeout")),
        fetch_dt=lambda _: pd.DataFrame(),
    )
    result = provider.fetch_day("2026-08-05")
    assert result.status is FetchStatus.PARTIAL
    assert "timeout" in result.message


def test_limit_pool_partial_when_one_side_fails_and_codes_include_bj():
    zt = pd.DataFrame({"代码": ["600000", "920117.BJ"], "名称": ["浦发银行", "国航远洋"],
                       "连板数": [1, 2]})
    provider = LimitPoolProvider(
        fetch_zt=lambda _: zt,
        fetch_dt=lambda _: (_ for _ in ()).throw(ValueError("DT schema drift")),
    )
    result = provider.fetch_day("2026-08-05")
    assert result.status is FetchStatus.PARTIAL
    assert set(result.data["code"]) == {"sh600000", "bj920117"}
    assert set(result.data["pool_type"]) == {"ZT"}


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
    fallback = pd.DataFrame({
        "code": ["920117"], "name": ["国航远洋"], "limit_count": [2],
    })
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


def test_limit_pool_uses_fallback_after_primary_empty_response():
    fallback = pd.DataFrame({
        "code": ["000001"], "name": ["平安银行"], "limit_count": [1],
    })
    provider = LimitPoolProvider(
        fetch_zt=lambda _date: pd.DataFrame(),
        fetch_dt=lambda _date: pd.DataFrame(),
        zt_fallbacks=[("eastmoney_push2ex", lambda _date: fallback)],
    )

    result = provider.fetch_day("2026-08-05")

    assert result.status is FetchStatus.SUCCESS
    assert result.source == "ZT:eastmoney_push2ex|DT:akshare_em"
    assert result.data.iloc[0].to_dict()["code"] == "sz000001"


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


def test_limit_pool_nonempty_crosscheck_marks_source_drift_partial():
    primary = pd.DataFrame({
        "code": [f"sz{index:06d}" for index in range(1, 101)],
        "limit_count": [1] * 100,
    })
    secondary = pd.DataFrame({
        "code": [f"sz{index:06d}" for index in range(1, 111)],
        "limit_count": [1] * 110,
    })
    provider = LimitPoolProvider(
        fetch_zt=lambda _date: primary,
        fetch_dt=lambda _date: pd.DataFrame(),
        zt_crosscheck=("eastmoney_push2ex", lambda _date: secondary),
    )

    result = provider.fetch_day("2026-08-05")

    assert result.status is FetchStatus.PARTIAL
    assert "count drift" in result.message
    assert result.data["code"].nunique() == 100


def test_limit_pool_default_chain_contains_direct_and_independent_fallbacks():
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


def test_one_sided_custom_primary_keeps_other_default_fallback_chain():
    provider = LimitPoolProvider(fetch_zt=lambda _date: pd.DataFrame())

    assert [name for name, _ in provider.zt_sources] == ["akshare_em"]
    assert [name for name, _ in provider.dt_sources] == [
        "akshare_em", "eastmoney_push2ex"
    ]


def test_limit_pool_reports_discarded_invalid_rows():
    zt = pd.DataFrame({
        "code": ["600000", "invalid"],
        "name": ["浦发银行", "坏行"],
    })
    provider = LimitPoolProvider(
        fetch_zt=lambda _date: zt,
        fetch_dt=lambda _date: pd.DataFrame(),
    )

    result = provider.fetch_day("2026-08-05")

    assert result.status is FetchStatus.SUCCESS
    assert result.data["code"].tolist() == ["sh600000"]
    assert "discarded 1 invalid stock row" in result.message


def test_legacy_entrypoint_blocks_unexpected_limit_pool_stage_error(monkeypatch):
    import legacy_tracker
    import lianban_analysis
    from data_sources.calendar_provider import CalendarProvider

    monkeypatch.setattr(CalendarProvider, "latest_closed_day", lambda _self: "2026-08-05")
    monkeypatch.setattr(legacy_tracker, "trim_cache_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        lianban_analysis,
        "fetch_zt_pool_data",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("pool stage offline")),
    )
    monkeypatch.setattr(
        legacy_tracker,
        "load_and_classify_zt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("continued after failure")),
    )

    with pytest.raises(RuntimeError, match="pool stage offline"):
        next(legacy_tracker.iter_main(limit_pool_provider=object(), plate_provider=object()))


def test_plate_provider_distinguishes_partial_coverage_and_normalizes_codes():
    def fetcher(code, _date):
        if code == "sz000001":
            return None
        return ["银行", "沪股通"]

    result = PlateProvider(fetcher=fetcher).fetch_codes(
        ["600000.SH", "000001.SZ", "920117.BJ"], "2026-08-05"
    )
    assert result.status is FetchStatus.PARTIAL
    assert result.data.columns.tolist() == PLATE_COLUMNS
    assert set(result.data["code"]) == {"sh600000", "bj920117"}
    assert result.expected_count == 3
    assert result.actual_count == 2


def test_latest_limit_pool_refresh_routes_provider_data_into_legacy_maps(monkeypatch):
    import lianban_analysis

    refresh_latest_limit_pool = lianban_analysis.refresh_latest_limit_pool
    saved = []
    monkeypatch.setattr(
        lianban_analysis, "_save_cache",
        lambda zt_data, dt_data: saved.append((set(zt_data), set(dt_data))),
    )

    data = pd.DataFrame([
        ["2026-08-05", "ZT", "sh600000", "浦发银行", 2, "fixture"],
        ["2026-08-05", "DT", "bj920117", "国航远洋", 1, "fixture"],
    ], columns=LIMIT_POOL_COLUMNS)

    class Provider:
        def fetch_day(self, date):
            assert date == "2026-08-05"
            return FetchResult.success(
                dataset="limit_pool", date=date, source="fixture",
                expected_count=2, actual_count=2, scope="SH,SZ,BJ", data=data,
            )

    zt_data = {"20260804": pd.DataFrame({"代码": ["000001"], "名称": ["平安银行"], "连板数": [1]})}
    dt_data = {"20260804": pd.DataFrame()}

    result = refresh_latest_limit_pool(
        zt_data, dt_data, "2026-08-05", Provider(), persist=True
    )

    assert result.status is FetchStatus.SUCCESS
    assert zt_data["20260805"].to_dict("records") == [
        {"代码": "600000", "名称": "浦发银行", "连板数": 2}
    ]
    assert dt_data["20260805"].to_dict("records") == [
        {"代码": "920117", "名称": "国航远洋"}
    ]
    assert saved == [({"20260804", "20260805"}, {"20260804", "20260805"})]


def test_attribute_codes_routes_plate_fetch_through_provider(tmp_path, monkeypatch):
    import em_stock_plates

    monkeypatch.setattr(em_stock_plates, "EM_PLATE_CACHE", str(tmp_path / "plates.csv"))
    calls = []
    data = pd.DataFrame([
        ["2026-08-05", "sh600000", "银行", "fixture"],
        ["2026-08-05", "bj920117", "航运", "fixture"],
    ], columns=PLATE_COLUMNS)

    class Provider:
        def fetch_codes(self, codes, date):
            calls.append((list(codes), date))
            return FetchResult.success(
                dataset="plates", date=date, source="fixture",
                expected_count=2, actual_count=2, scope="SH,SZ,BJ", data=data,
            )

    def classify(tags):
        return {"银行": ("银行", "金融"), "航运": ("航运", "周期")}.get(tags[0], (None, None))

    result = em_stock_plates.attribute_codes(
        ["sh600000", "bj920117"], classify, lambda _name: (None, None),
        ["金融", "周期"], trade_date="20260805", plate_provider=Provider(),
    )

    assert calls == [(["sh600000", "bj920117"], "2026-08-05")]
    assert result == {"sh600000": ("银行", "金融"), "bj920117": ("航运", "周期")}


def test_plate_provider_fetches_codes_concurrently():
    lock = threading.Lock()
    active = 0
    peak = 0

    def fetcher(_code, _date):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return ["板块"]

    result = PlateProvider(fetcher=fetcher, max_workers=2).fetch_codes(
        ["sh600000", "sz000001"], "2026-08-05"
    )

    assert result.status is FetchStatus.SUCCESS
    assert peak >= 2


def test_plate_provider_retries_transient_empty_response():
    attempts = 0

    def fetcher(_code, _date):
        nonlocal attempts
        attempts += 1
        return None if attempts == 1 else ["板块"]

    result = PlateProvider(
        fetcher=fetcher, max_workers=1, retry=2, retry_delay=0
    ).fetch_codes(["sh600000"], "2026-08-05")

    assert result.status is FetchStatus.SUCCESS
    assert result.actual_count == 1
    assert attempts == 2


def test_plate_provider_truthful_empty_keeps_code_coverage_counts():
    result = PlateProvider(fetcher=lambda _code, _date: []).fetch_codes(
        ["sh600599", "bj920305"], "2026-08-05"
    )

    assert result.status is FetchStatus.ZERO
    assert result.expected_count == 2
    assert result.actual_count == 2


def test_plate_provider_treats_eastmoney_rc102_as_not_listed_empty(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"rc": 102, "data": None}

    class Session:
        trust_env = True

        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("data_sources.plate_provider.requests.Session", Session)

    assert PlateProvider._eastmoney_fetcher("sh600599", "2026-08-05") == []


def test_legacy_limit_cache_normalizes_bj_and_unprefixed_codes(monkeypatch, tmp_path):
    import legacy_tracker

    zt_path = tmp_path / "zt.csv"
    cls_path = tmp_path / "cls.csv"
    pd.DataFrame({
        "日期": ["20260805", "20260805", "20260805"],
        "类型": ["ZT", "ZT", "ZT"],
        "代码": ["600000", "000001", "920230.BJ"],
        "名称": ["浦发银行", "平安银行", "样本北交所"],
        "连板数": [1, 1, 2],
    }).to_csv(zt_path, index=False, encoding="utf-8-sig")
    pd.DataFrame({
        "date": ["20260805"] * 3,
        "code": ["sh600000", "sz000001", "bj920230"],
        "sub": ["银行", "银行", "航运"],
        "mainline": ["金融", "金融", "周期"],
    }).to_csv(cls_path, index=False)
    monkeypatch.setattr(legacy_tracker, "ZT_CACHE_FILE", str(zt_path))
    monkeypatch.setattr(legacy_tracker, "CLS_PLATE_CACHE", str(cls_path))

    result = legacy_tracker.load_and_classify_zt(n_days=1)

    assert set(result["代码"]) == {"sh600000", "sz000001", "bj920230"}


def test_limit_pool_cache_persists_only_canonical_codes(monkeypatch, tmp_path):
    import lianban_analysis

    cache = tmp_path / "limit_pool.csv"
    monkeypatch.setattr(lianban_analysis, "CACHE_FILE", str(cache))
    zt_data = {
        "20260805": pd.DataFrame({
            "代码": ["600000", "920230.BJ"],
            "名称": ["浦发银行", "样本北交所"],
            "连板数": [1, 2],
        })
    }

    lianban_analysis._save_cache(zt_data, {"20260805": pd.DataFrame()})

    saved = pd.read_csv(cache, encoding="utf-8-sig", dtype=str)
    assert set(saved["代码"]) == {"sh600000", "bj920230"}
