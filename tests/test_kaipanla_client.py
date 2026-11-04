from data_sources.kaipanla_client import KaiPanLaClient


def test_abnormal_alerts_parses_kaipanla_rows(monkeypatch):
    raw = {
        "Day": "2026-10-28",
        "Many_Num": 2,
        "Time": 1793199315,
        "List": [
            ["002242", "九阳股份", 1, "连续30个交易日内涨幅偏离值累计达到 200%", 0, 30, 177.24,
             "涨幅达到8.67%将触发严重异动", 8.67, 177.24, "", 24.32, "未达到严重异动条件"],
            ["603278", "大业股份", 1, "连续10个交易日内涨幅偏离值累计达到 100%", 0, 10, 99.58,
             "涨幅达到0.22%将触发严重异动", 0.22, 99.58, "0000-00-00", 17.96, "未达到严重异动条件"],
        ],
        "ZDJKList": [],
        "WXHJList": [],
        "errcode": "0",
    }
    client = KaiPanLaClient()
    monkeypatch.setattr(client, "_call", lambda *args, **kwargs: raw)

    result = client.get_abnormal_alerts()

    assert result["date"] == "2026-10-28"
    assert result["count"] == 2
    assert result["alerts"][0]["code"] == "002242"
    assert result["alerts"][0]["window_days"] == 30
    assert result["alerts"][0]["deviation_pct"] == 177.24
    assert result["alerts"][0]["required_change_pct"] == 8.67
    assert result["alerts"][1]["window_days"] == 10
    assert result["zdjk_list"] == []
    assert result["alerts"][0]["trigger_price"] == 24.32
    assert "price" not in result["alerts"][0]


def test_alert_failure_and_empty_success_are_distinct(monkeypatch):
    client = KaiPanLaClient()
    monkeypatch.setattr(client, '_call', lambda *a, **k: {'errcode': 1020})
    assert client.get_abnormal_alerts()['available'] is False
    monkeypatch.setattr(client, '_call', lambda *a, **k: {'errcode': 0, 'List': [], 'Many_Num': 22})
    r = client.get_abnormal_alerts()
    assert r['available'] is True and r['count'] == 0
    assert r['many_abnormal_count'] == 22


def test_alert_call_does_not_use_published_third_party_credentials(monkeypatch):
    calls = []
    def capture(*args, **kwargs):
        calls.append(kwargs)
        return {'errcode': 0, 'List': []}
    c = KaiPanLaClient()
    monkeypatch.setattr(c, '_call', capture)
    c.get_abnormal_alerts()
    assert 'Token' not in calls[0] and 'UserID' not in calls[0]
