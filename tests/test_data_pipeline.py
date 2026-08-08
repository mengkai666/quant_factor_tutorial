# -*- coding: utf-8 -*-
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def test_report_policy_capability_matrix_and_semantic_gate():
    from report_logic import ReportPolicy, scan_forbidden_semantics

    facts = ReportPolicy.from_mode("facts_only")
    assert facts.allow_facts is True
    assert facts.allow_observations is False
    assert facts.allow_probabilities is False
    assert facts.allow_positions is False
    assert facts.allow_actions is False
    assert facts.allow_ai is False
    assert scan_forbidden_semantics("今日上涨 3000 家", facts) == []
    assert "空仓观望" in scan_forbidden_semantics("冰点，空仓观望", facts)

    observation = ReportPolicy.from_mode("observation")
    assert observation.allow_observations is True
    assert observation.allow_scenarios is True
    assert observation.allow_probabilities is False
    assert observation.allow_actions is False

    decision = ReportPolicy.from_mode("decision")
    assert all([
        decision.allow_facts, decision.allow_observations,
        decision.allow_scenarios, decision.allow_probabilities,
        decision.allow_positions, decision.allow_actions, decision.allow_ai,
    ])


def test_module_quality_aggregation_drives_publication_mode():
    from data_sources.quality_gate import build_module_quality, aggregate_report_quality

    modules = {
        "universe": build_module_quality("universe", total=5200, covered=5200, source="eastmoney"),
        "price_raw": build_module_quality("price_raw", total=5200, covered=5150, source="tencent"),
        "breadth": build_module_quality("breadth", total=5200, covered=5150, source="tencent"),
        "history": build_module_quality("history", total=60, covered=8, source="local"),
    }
    quality = aggregate_report_quality(modules)
    assert quality["status"] == "degraded"
    assert quality["publication_mode"] == "observation"
    assert quality["modules"]["history"]["coverage_pct"] == 13.33

    modules["breadth"] = build_module_quality("breadth", total=5200, covered=0, source="tencent", errors=["请求失败"])
    blocked = aggregate_report_quality(modules)
    assert blocked["status"] == "blocked"
    assert blocked["publication_mode"] == "facts_only"


def test_universe_refresh_requires_all_three_markets_and_preserves_old_cache(tmp_path):
    from data_sources.universe_provider import UniverseProvider

    cache = tmp_path / "security_master.csv"
    cache.write_text("code,name,market,industry,status,updated_at\nsh600000,浦发银行,sh,银行,active,2026-08-05T15:00:00\nsz000001,平安银行,sz,银行,active,2026-08-05T15:00:00\nbj920001,北交样本,bj,制造,active,2026-08-05T15:00:00\n", encoding="utf-8")
    old = cache.read_text(encoding="utf-8")

    def incomplete_fetcher(page, page_size):
        if page > 1:
            return []
        return [
            {"f12": "600000", "f13": 1, "f14": "浦发银行", "f100": "银行"},
            {"f12": "000001", "f13": 0, "f14": "平安银行", "f100": "银行"},
        ]

    provider = UniverseProvider(cache, fetcher=incomplete_fetcher, min_total=3)
    result = provider.refresh()
    assert result["updated"] is False
    assert "bj" in result["missing_market_prefixes"]
    assert cache.read_text(encoding="utf-8") == old

    def complete_fetcher(page, page_size):
        if page > 1:
            return []
        return [
            {"f12": "600000", "f13": 1, "f14": "浦发银行", "f100": "银行"},
            {"f12": "000001", "f13": 0, "f14": "平安银行", "f100": "银行"},
            {"f12": "920001", "f13": 0, "f14": "北交样本", "f100": "制造"},
        ]

    result = UniverseProvider(cache, fetcher=complete_fetcher, min_total=3).refresh()
    assert result["updated"] is True
    assert result["market_prefixes"] == ["bj", "sh", "sz"]
    assert {row["code"] for row in result["records"]} == {"sh600000", "sz000001", "bj920001"}


def test_price_contract_separates_raw_and_qfq_and_rejects_mixed_rows():
    from data_sources.price_provider import normalize_price_rows, validate_price_contract

    rows = normalize_price_rows([
        {"code": "600000.SH", "date": "2026-08-06", "close": 10.5, "adjustment": "raw", "source": "tencent"},
        {"code": "600000.SH", "date": "2026-08-05", "close": 10.2, "adjustment": "qfq", "source": "baostock"},
    ])
    assert rows[0]["close_raw"] == 10.5 and rows[0]["close_qfq"] is None
    assert rows[1]["close_qfq"] == 10.2 and rows[1]["close_raw"] is None
    assert validate_price_contract(rows)["valid"] is True

    dual = [{"code": "sh600000", "date": "2026-08-06", "close_raw": 10.5, "close_qfq": 10.4, "price_basis": "raw+qfq"}]
    assert validate_price_contract(dual)["valid"] is True


def test_legacy_price_cache_is_preserved_but_never_claimed_as_raw_or_qfq():
    import pandas as pd
    from data_sources.price_provider import normalize_price_frame, price_coverage

    frame = normalize_price_frame(pd.DataFrame([
        {"date": "2026-08-05", "code": "sh600000", "close": 10.5},
        {"date": "2026-08-05", "code": "sz000001", "close": 12.5},
    ]))

    assert set(["close_raw", "close_qfq", "close_legacy", "price_basis"]).issubset(frame.columns)
    assert frame["price_basis"].tolist() == ["legacy_mixed", "legacy_mixed"]
    assert frame["close_raw"].isna().all()
    assert frame["close_qfq"].isna().all()
    assert frame["close_legacy"].notna().all()
    assert price_coverage(frame.to_dict("records"), ["sh600000", "sz000001"], "raw")["market_covered"] == 0


def test_merge_price_frames_keeps_raw_and_qfq_on_same_security_day():
    import pandas as pd
    from data_sources.price_provider import merge_price_frames

    merged = merge_price_frames(
        pd.DataFrame([{"date": "2026-08-06", "code": "sh600000", "close_qfq": 10.4, "price_basis": "qfq", "source": "baostock"}]),
        pd.DataFrame([{"date": "2026-08-06", "code": "sh600000", "close_raw": 10.5, "price_basis": "raw", "source": "tencent"}]),
    )

    assert len(merged) == 1
    row = merged.iloc[0]
    assert row["close_raw"] == 10.5
    assert row["close_qfq"] == 10.4
    assert row["price_basis"] == "raw+qfq"


def test_security_master_overrides_stale_st_name():
    from report_logic import filter_tradeable_pool

    rows = [{"code": "003032", "name": "*ST传智", "turnover": 1000000}]
    master = {"sz003032": {"name": "传智教育", "is_st": False, "status": "active", "tradable": True}}
    got = filter_tradeable_pool(rows, security_master=master)
    assert len(got) == 1
    assert got[0]["name"] == "传智教育"


def test_universe_refresh_uses_independent_fallback_when_primary_fails(tmp_path):
    from data_sources.universe_provider import UniverseProvider

    def primary_fetcher(page, page_size):
        raise RuntimeError("primary unavailable")

    def fallback_fetcher():
        return [
            {"code": "600000", "name": "浦发银行", "source": "akshare"},
            {"code": "000001", "name": "平安银行", "source": "akshare"},
            {"code": "920001", "name": "纬达光电", "source": "akshare"},
        ]

    cache = tmp_path / "security_master.csv"
    result = UniverseProvider(
        cache,
        fetcher=primary_fetcher,
        fallback_fetcher=fallback_fetcher,
        min_total=3,
    ).refresh()

    assert result["updated"] is True
    assert result["used_fallback"] is True
    assert result["used_stale"] is False
    assert result["source"] == "akshare"
    assert result["data_date"]
    assert result["source_timestamp"]
    assert result["cache_updated_at"]
    assert result["market_prefixes"] == ["bj", "sh", "sz"]
    assert cache.exists()

def test_universe_cache_parses_boolean_strings(tmp_path):
    from data_sources.universe_provider import UniverseProvider

    cache = tmp_path / "security_master.csv"
    cache.write_text(
        "code,name,market,industry,status,is_st,tradable,updated_at,source\n"
        "sz003032,传智教育,sz,教育,active,False,True,2026-08-06T15:30:00,akshare\n",
        encoding="utf-8",
    )
    row = UniverseProvider(cache, min_total=1).load_cache()[0]
    assert row["is_st"] is False
    assert row["tradable"] is True


def test_universe_cache_fallback_marks_stale_lineage(tmp_path):
    from data_sources.universe_provider import UniverseProvider

    cache = tmp_path / "security_master.csv"
    cache.write_text(
        "code,name,market,industry,status,is_st,tradable,updated_at,source\n"
        "sh600000,浦发银行,sh,银行,active,False,True,2026-08-06T15:00:00,eastmoney\n"
        "sz000001,平安银行,sz,银行,active,False,True,2026-08-06T15:00:00,eastmoney\n"
        "bj920001,纬达光电,bj,电子,active,False,True,2026-08-06T15:00:00,eastmoney\n",
        encoding="utf-8",
    )
    provider = UniverseProvider(cache, fetcher=lambda *_: (_ for _ in ()).throw(RuntimeError("down")), min_total=3)
    result = provider.load_or_refresh(refresh=True)

    assert result["source"] == "cache"
    assert result["used_fallback"] is False
    assert result["used_stale"] is True
    assert result["data_date"] == "2026-08-06"
    assert result["cache_updated_at"]
    assert result["errors"]

def test_security_master_as_of_does_not_use_current_name_for_past_report(tmp_path):
    from data_sources.universe_provider import UniverseProvider

    cache = tmp_path / "security_master.csv"
    cache.write_text(
        "code,name,market,industry,status,is_st,tradable,updated_at,source\n"
        "sz003032,*ST传智,sz,,active,True,True,2026-08-07T09:00:00+08:00,eastmoney\n",
        encoding="utf-8",
    )
    provider = UniverseProvider(cache, min_total=1)
    current = provider.load_cache()

    historical = provider.records_as_of(current, "2026-08-06")
    assert historical[0]["status_as_of"] == "unknown"
    assert historical[0]["is_st_as_of"] is None
    assert historical[0]["tradable_as_of"] is None

    same_day = provider.records_as_of(current, "2026-08-07")
    assert same_day[0]["status_as_of"] == "active"
    assert same_day[0]["is_st_as_of"] is True


def test_module_quality_clamps_covered_and_coverage_to_same_scope():
    from data_sources.quality_gate import build_module_quality

    got = build_module_quality("price_raw", total=2467, covered=5190, source="price_cache")
    assert got["total"] == 2467
    assert got["covered"] == 2467
    assert got["coverage_pct"] == 100.0
    assert got["raw_covered"] == 5190
    assert got["raw_coverage_pct"] == round(5190 / 2467 * 100, 2)
    assert any("COVERAGE_OVERFLOW" in error for error in got["errors"])
    assert got["status"] == "blocked"


def test_market_sentiment_reads_canonical_price_columns_and_reports_basis(tmp_path, monkeypatch):
    import pandas as pd
    import limit_ratio_factor as factor

    cache = tmp_path / "price_history_cache.csv"
    pd.DataFrame([
        {"date": "2026-08-05", "code": "sh600000", "close_raw": 10.0, "close_qfq": 10.0,
         "close_legacy": None, "price_basis": "raw+qfq", "source": "test", "source_timestamp": ""},
        {"date": "2026-08-06", "code": "sh600000", "close_raw": 10.5, "close_qfq": 10.5,
         "close_legacy": None, "price_basis": "raw+qfq", "source": "test", "source_timestamp": ""},
        {"date": "2026-08-05", "code": "sz000001", "close_raw": 20.0, "close_qfq": 20.0,
         "close_legacy": None, "price_basis": "raw+qfq", "source": "test", "source_timestamp": ""},
        {"date": "2026-08-06", "code": "sz000001", "close_raw": 19.5, "close_qfq": 19.5,
         "close_legacy": None, "price_basis": "raw+qfq", "source": "test", "source_timestamp": ""},
    ]).to_csv(cache, index=False)
    monkeypatch.setattr(factor, "PRICE_CACHE_FILE", str(cache))

    result = factor.MarketSentimentFactor()._load_ad_cache()

    assert result["20260806"]["up"] == 1
    assert result["20260806"]["down"] == 1
    assert result["20260806"]["market_covered"] == 2
    assert result["20260806"]["price_basis"] == "raw"
    assert result["20260806"]["coverage_pct"] is None
    assert result["20260806"]["scope_inferred"] is True


def test_market_sentiment_reports_flat_and_does_not_infer_full_market_scope(tmp_path, monkeypatch):
    import pandas as pd
    import limit_ratio_factor as factor

    cache = tmp_path / "price_history_cache.csv"
    pd.DataFrame([
        {"date": "2026-08-05", "code": "sh600000", "close_raw": 10.0},
        {"date": "2026-08-06", "code": "sh600000", "close_raw": 10.5},
        {"date": "2026-08-05", "code": "sz000001", "close_raw": 20.0},
        {"date": "2026-08-06", "code": "sz000001", "close_raw": 20.0},
    ]).to_csv(cache, index=False)
    monkeypatch.setattr(factor, "PRICE_CACHE_FILE", str(cache))
    monkeypatch.setattr(factor, "SECURITY_MASTER_CACHE", str(tmp_path / "missing_master.csv"))

    row = factor.MarketSentimentFactor()._load_ad_cache()["20260806"]

    assert row["up"] == 1
    assert row["down"] == 0
    assert row["flat"] == 1
    assert row["up"] + row["down"] + row["flat"] == row["market_covered"]
    assert row["market_covered"] == 2
    assert row["market_total"] is None
    assert row["coverage_pct"] is None
    assert row["scope_inferred"] is True
    assert row["market_scope_source"] == "price_cache"


def test_market_sentiment_uses_security_master_as_explicit_market_scope(tmp_path, monkeypatch):
    import pandas as pd
    import limit_ratio_factor as factor

    cache = tmp_path / "price_history_cache.csv"
    master = tmp_path / "security_master.csv"
    pd.DataFrame([
        {"date": "2026-08-05", "code": "sh600000", "close_raw": 10.0},
        {"date": "2026-08-06", "code": "sh600000", "close_raw": 10.5},
        {"date": "2026-08-05", "code": "sz000001", "close_raw": 20.0},
        {"date": "2026-08-06", "code": "sz000001", "close_raw": 19.5},
    ]).to_csv(cache, index=False)
    pd.DataFrame([
        {"code": "sh600000"},
        {"code": "sz000001"},
        {"code": "bj920001"},
    ]).to_csv(master, index=False)
    monkeypatch.setattr(factor, "PRICE_CACHE_FILE", str(cache))
    monkeypatch.setattr(factor, "SECURITY_MASTER_CACHE", str(master))

    row = factor.MarketSentimentFactor()._load_ad_cache()["20260806"]

    assert row["market_covered"] == 2
    assert row["market_total"] == 3
    assert row["coverage_pct"] == round(2 / 3 * 100, 2)
    assert row["scope_inferred"] is False
    assert row["market_scope_source"] == "security_master"


def test_tencent_ad_quality_requires_partition_and_universe_bounds():
    import 主线强度追踪 as report

    accepted = report._validate_tencent_ad_meta(
        {"up": 2800, "down": 1600, "flat": 900, "covered": 5300, "requested": 5500},
        universe_total=5538,
    )
    assert accepted["accepted"] is True
    assert accepted["coverage_pct"] == round(5300 / 5500 * 100, 2)
    assert accepted["partition_ok"] is True

    rejected = report._validate_tencent_ad_meta(
        {"up": 3000, "down": 1000, "flat": 0, "covered": 5000, "requested": 5500},
        universe_total=5500,
    )
    assert rejected["accepted"] is False
    assert rejected["partition_ok"] is False
    assert any("涨跌平" in reason for reason in rejected["reasons"])

    out_of_scope = report._validate_tencent_ad_meta(
        {"up": 3000, "down": 1500, "flat": 500, "covered": 5000, "requested": 5600},
        universe_total=5538,
    )
    assert out_of_scope["accepted"] is False
    assert any("requested" in reason for reason in out_of_scope["reasons"])


def test_market_sentiment_uses_legacy_close_as_explicit_degraded_ad(tmp_path, monkeypatch):
    import pandas as pd
    import limit_ratio_factor as factor

    cache = tmp_path / "legacy_price_history_cache.csv"
    pd.DataFrame([
        {"date": "2026-08-05", "code": "sh600000", "close": 10.0},
        {"date": "2026-08-06", "code": "sh600000", "close": 10.5},
    ]).to_csv(cache, index=False)
    monkeypatch.setattr(factor, "PRICE_CACHE_FILE", str(cache))

    result = factor.MarketSentimentFactor()._load_ad_cache()

    row = result["20260806"]
    assert row["up"] == 1
    assert row["down"] == 0
    assert row["market_covered"] == 1
    assert row["coverage_pct"] is None
    assert row["scope_inferred"] is True
    assert row["price_basis"] == "legacy_mixed"
    assert row["legacy_mixed"] is True
    assert row["used_fallback"] is True

def test_update_price_cache_fills_missing_report_day_raw_and_keeps_bj_universe(tmp_path, monkeypatch):
    import pandas as pd
    import 主线强度追踪 as report

    price_cache = tmp_path / "price_history_cache.csv"
    security_cache = tmp_path / "security_master.csv"
    pd.DataFrame([
        {"date": "2026-08-06", "code": code, "close_qfq": value,
         "price_basis": "qfq", "source": "baostock"}
        for code, value in [("sh600000", 10.0), ("sz000001", 20.0), ("bj920001", 30.0)]
    ]).to_csv(price_cache, index=False)
    pd.DataFrame([
        {"code": "sh600000", "name": "浦发银行", "market": "sh", "tradable": True},
        {"code": "sz000001", "name": "平安银行", "market": "sz", "tradable": True},
        {"code": "bj920001", "name": "北交样本", "market": "bj", "tradable": True},
    ]).to_csv(security_cache, index=False)

    monkeypatch.setattr(report, "PRICE_CACHE", str(price_cache))
    monkeypatch.setattr(report, "SECURITY_MASTER_CACHE", str(security_cache))
    monkeypatch.setattr(report, "INDUSTRY_CACHE", str(tmp_path / "missing_industry.csv"))
    calls = []

    def fake_tencent(codes, trade_date_str, **kwargs):
        calls.append(list(codes))
        return [
            {"date": trade_date_str, "code": code, "close_raw": value,
             "price_basis": "raw", "source": "tencent"}
            for code, value in [("sh600000", 10.2), ("sz000001", 19.8), ("bj920001", 30.5)]
        ]

    monkeypatch.setattr(report, "_fetch_tencent_close", fake_tencent)

    classified = pd.DataFrame([{"日期": "20260806", "代码": "sh600000"}])
    result = report.update_price_cache(classified)
    latest = result[result["date"] == "2026-08-06"]

    assert calls == [["sh600000", "sz000001", "bj920001"]]
    assert set(latest["code"]) == {"sh600000", "sz000001", "bj920001"}
    assert latest["close_raw"].notna().all()
    assert latest["close_qfq"].notna().all()


def test_fetch_bs_chunk_marks_bj_as_raw_only_without_querying_baostock(monkeypatch):
    import types
    import 主线强度追踪 as report

    calls = []

    class FakeBaostock:
        def login(self):
            calls.append(('login',))

        def logout(self):
            calls.append(('logout',))

        def query_history_k_data_plus(self, *args, **kwargs):
            calls.append(('query', args, kwargs))
            raise AssertionError('北交所代码不应进入 Baostock 查询')

    monkeypatch.setitem(__import__('sys').modules, 'baostock', FakeBaostock())
    rows, diagnostics = report._fetch_bs_chunk((['bj920001'], '2026-08-06', '2026-08-06', True))

    assert rows == []
    assert diagnostics['requested_count'] == 1
    assert diagnostics['unsupported_codes'] == ['bj920001']
    assert diagnostics['missing_raw_codes'] == ['bj920001']
    assert diagnostics['missing_qfq_codes'] == ['bj920001']
    assert diagnostics['qfq_raw_only_markets'] == ['bj']
    assert diagnostics['query_errors'] == []
    assert calls == [('login',), ('logout',)]


def test_fetch_bs_chunk_records_qfq_query_error_without_fabricating_qfq(monkeypatch):
    import 主线强度追踪 as report

    class Result:
        def __init__(self, rows=None, error_code='0', error_msg=''):
            self.error_code = error_code
            self.error_msg = error_msg
            self._rows = iter(rows or [])

        def next(self):
            try:
                self._row = next(self._rows)
                return True
            except StopIteration:
                return False

        def get_row_data(self):
            return self._row

    class FakeBaostock:
        def login(self):
            return None

        def logout(self):
            return None

        def query_history_k_data_plus(self, code, fields, **kwargs):
            if kwargs.get('adjustflag') == '3':
                return Result([['2026-08-06', code, '10.5']])
            return Result(error_code='100010', error_msg='qfq unavailable')

    monkeypatch.setitem(__import__('sys').modules, 'baostock', FakeBaostock())
    rows, diagnostics = report._fetch_bs_chunk((['sh600000'], '2026-08-06', '2026-08-06', True))

    assert rows == [{
        'date': '2026-08-06', 'code': 'sh600000', 'close_raw': 10.5,
        'price_basis': 'raw', 'source': 'baostock',
    }]
    assert diagnostics['raw_covered_codes'] == ['sh600000']
    assert diagnostics['qfq_covered_codes'] == []
    assert diagnostics['missing_qfq_codes'] == ['sh600000']
    assert diagnostics['query_errors'] == [{
        'code': 'sh600000', 'basis': 'qfq', 'adjustflag': '2',
        'error_code': '100010', 'error_msg': 'qfq unavailable',
    }]
