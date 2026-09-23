from copy import deepcopy
import pytest
from test_research_brief import stock, context, prices, DAYS, build


def sample():
    rows=[stock("sz000001","算力",height=2),stock("sz000002","算力")]
    return build(context(rows),price_rows=prices(rows),trading_days=DAYS)


def test_research_report_uses_observed_rows_not_legacy_quadrant_requirements():
    from report_integrity import build_research_report_integrity, validate_report_integrity
    metadata=build_research_report_integrity(sample())
    assert metadata["schema"]=="report-integrity/v2"
    assert metadata["purpose"]=="research_observation"
    assert metadata["metrics"]["watchlist_rows"]==2
    assert validate_report_integrity(metadata)["ok"] is True
    assert "quadrant_rows" not in metadata["metrics"]


@pytest.mark.parametrize("bad",["price","date","name","duplicate"])
def test_invalid_research_output_cannot_be_published(bad):
    from report_integrity import build_research_report_integrity, validate_report_integrity, ReportIntegrityError
    brief=sample()
    row=brief["sectors"][0]["stocks"][0]
    if bad=="price":row["close"]=float("nan")
    if bad=="date":row["source_date"]="2026-09-04"
    if bad=="name":row["name"]=row["code"]
    if bad=="duplicate":brief["sectors"][0]["stocks"].append(deepcopy(row))
    with pytest.raises(ReportIntegrityError):
        validate_report_integrity(build_research_report_integrity(brief))


def test_research_integrity_keeps_explicit_core_data_failure_but_not_ai_or_validation_status():
    from report_integrity import build_research_report_integrity, validate_report_integrity, ReportIntegrityError
    quality={"critical_blocked":[],"modules":{"ai":{"status":"unavailable"}},"strategy_qualification":{"eligible_strategy_ids":[]}}
    assert validate_report_integrity(build_research_report_integrity(sample(),quality=quality))["ok"]
    quality["critical_blocked"]=["price_raw"]
    with pytest.raises(ReportIntegrityError):
        validate_report_integrity(build_research_report_integrity(sample(),quality=quality))


def test_verified_zero_limit_up_day_is_not_fabricated_as_missing_price_coverage():
    from report_integrity import build_research_report_integrity,validate_report_integrity
    brief=build(context([]),price_rows=[],trading_days=DAYS)
    metadata=build_research_report_integrity(brief)
    assert metadata["metrics"]["watchlist_rows"]==0
    assert "price_coverage_pct" not in metadata["metrics"]
    assert validate_report_integrity(metadata)["ok"]
