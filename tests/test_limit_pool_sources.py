import requests
import pytest

from data_sources.limit_pool_sources import EastmoneyLimitPoolSource, ThsLimitUpSource


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
