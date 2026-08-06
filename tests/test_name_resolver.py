import pandas as pd
import pytest

from data_sources.name_resolver import NameResolution, resolve_names


def test_legacy_report_loader_prefers_current_limit_name_over_industry_cache(monkeypatch, tmp_path):
    import legacy_tracker

    universe_path = tmp_path / "universe.csv"
    industry_path = tmp_path / "industry.csv"
    pd.DataFrame({"code": ["sz003032"], "name": ["传智教育"]}).to_csv(
        universe_path, index=False
    )
    pd.DataFrame({"code": ["sz003032"], "name": ["*ST传智"], "industry": ["教育"]}).to_csv(
        industry_path, index=False
    )
    monkeypatch.setattr(legacy_tracker, "UNIVERSE_CACHE", str(universe_path))
    monkeypatch.setattr(legacy_tracker, "INDUSTRY_CACHE", str(industry_path))

    result = legacy_tracker._load_name_resolution(
        classified=pd.DataFrame({
            "日期": ["20260805"], "代码": ["sz003032"], "名称": ["传智教育"],
        }),
        latest_limit=pd.DataFrame({"代码": ["sz003032"], "名称": ["传智教育"]}),
    )

    assert result.names["sz003032"] == "传智教育"
    assert result.sources["sz003032"] == "limit_pool"


def test_current_limit_name_overrides_stale_industry_name():
    result = resolve_names(
        universe=pd.DataFrame({"code": ["sz003032"], "name": ["传智教育"]}),
        classified=pd.DataFrame({
            "日期": ["20260804"], "代码": ["sz003032"], "名称": ["传智教育"],
        }),
        latest_limit=pd.DataFrame({
            "代码": ["sz003032"], "名称": ["传智教育"],
        }),
        industry=pd.DataFrame({"code": ["sz003032"], "name": ["*ST传智"]}),
    )

    assert result.names["sz003032"] == "传智教育"
    assert result.sources["sz003032"] == "limit_pool"
    assert result.conflicts == [
        {
            "code": "sz003032",
            "names": ["*ST传智", "传智教育"],
            "sources": ["industry", "universe", "classified", "limit_pool"],
        }
    ]


def test_universe_name_is_used_when_no_current_limit_name_exists():
    result = resolve_names(
        universe=pd.DataFrame({"code": ["sh600000"], "name": ["浦发银行"]}),
        industry=pd.DataFrame({"code": ["sh600000"], "name": ["旧名称"]}),
    )

    assert result.names == {"sh600000": "浦发银行"}
    assert result.sources == {"sh600000": "universe"}


def test_classified_latest_name_beats_industry_when_universe_missing():
    result = resolve_names(
        classified=pd.DataFrame({
            "日期": ["20260803", "20260805"],
            "代码": ["bj920117", "bj920117"],
            "名称": ["旧简称", "国航远洋"],
        }),
        industry=pd.DataFrame({"code": ["bj920117"], "name": ["行业缓存名"]}),
    )

    assert result.names["bj920117"] == "国航远洋"
    assert result.sources["bj920117"] == "classified"


def test_empty_and_invalid_name_rows_are_ignored():
    result = resolve_names(
        universe=pd.DataFrame({"code": ["bad", "sz000001"], "name": ["", "平安银行"]}),
    )

    assert result.names == {"sz000001": "平安银行"}
    assert isinstance(result, NameResolution)


def test_legacy_leader_labels_use_the_shared_name_resolution():
    import legacy_tracker

    classified = pd.DataFrame({
        "日期": ["20260805"],
        "代码": ["sz003032"],
        "名称": ["*ST传智"],
        "细分板块": ["AI应用"],
        "连板数": [2],
    })
    resolution = NameResolution(
        names={"sz003032": "传智教育"},
        sources={"sz003032": "limit_pool"},
        conflicts=[],
    )

    leaders = legacy_tracker.get_leaders(classified, name_resolution=resolution)

    assert leaders["20260805"]["AI应用"]["name"] == "传智教育"
