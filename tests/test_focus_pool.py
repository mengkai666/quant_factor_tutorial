import pandas as pd

from screener import generate_focus_pool


def test_focus_pool_keeps_space_leader_out_of_trend_pool_and_filters_st():
    echelon = [
        {
            "height": "8连板", "stocks": ["传智教育"],
            "primary": "AI应用80%", "secondary": "/",
            "stock_details": [{"name": "传智教育", "code": "sz003032"}],
        },
        {
            "height": "2连板", "stocks": ["低位补涨"],
            "primary": "AI算力50%", "secondary": "/",
            "stock_details": [{"name": "低位补涨", "code": "sz000001"}],
        },
    ]
    top30 = {
        "5日": [
            {"code": "sz003032", "name": "传智教育", "mainline": "AI应用"},
            {"code": "sz000002", "name": "趋势中军", "mainline": "AI算力"},
            {"code": "sz000003", "name": "*ST旧名", "mainline": "AI算力"},
        ],
    }

    result = generate_focus_pool(pd.DataFrame(), echelon, top30, None, output_path=None)

    space = result[result["策略池"].str.contains("空间")]
    trend = result[result["策略池"].str.contains("中军")]
    low = result[result["策略池"].str.contains("补涨")]
    assert "sz003032" in set(space["代码"])
    assert "sz003032" not in set(trend["代码"])
    assert "sz000003" not in set(result["代码"])
    assert not low.empty


def test_focus_pool_deduplicates_by_code_not_stale_name():
    echelon = [{
        "height": "3连板", "stocks": ["传智教育"],
        "primary": "AI应用80%", "secondary": "/",
        "stock_details": [{"name": "传智教育", "code": "sz003032"}],
    }]
    top30 = {"5日": [{"code": "sz003032", "name": "*ST传智", "mainline": "AI应用"}]}

    result = generate_focus_pool(pd.DataFrame(), echelon, top30, None, output_path=None)

    assert result["代码"].tolist().count("sz003032") == 1


def test_dashboard_keeps_low_level_role_in_its_own_bucket():
    from decision_dashboard import _split_focus_pool

    frame = pd.DataFrame([{
        "股票": "低位补涨", "代码": "sz000001", "板块": "AI算力",
        "策略池": "【低位补涨池】", "入场条件": "确认", "防守位": "退出",
    }])

    buckets = _split_focus_pool(frame)

    assert buckets["low_level"][0]["name"] == "低位补涨"
