from catalyst_attribution import _pick_top_catalyst


def test_announcement_without_type_uses_plain_announcement_tag():
    catalyst = _pick_top_catalyst({
        "announcements": [{
            "date": "2026-08-05",
            "title": "股票交易异常波动公告",
            "type": None,
            "url": "https://example.test/announcement",
        }],
    })

    assert catalyst["tag"] == "公告"
    assert "None" not in catalyst["tag"]
