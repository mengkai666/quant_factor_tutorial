"""
Unit tests for src/ai_tactics_recap.py
"""

import json
from pathlib import Path
import pytest

from ai_tactics_recap import (
    is_gemini_available,
    update_tactics_recap_file,
    get_default_market_facts_0924,
)


def test_is_gemini_available_env(monkeypatch):
    import ai_tactics_recap

    monkeypatch.setattr(ai_tactics_recap, "GEMINI_API_KEY", "test_key")
    monkeypatch.setattr(ai_tactics_recap, "GEMINI_ENABLE", True)
    assert is_gemini_available() is True

    monkeypatch.setattr(ai_tactics_recap, "GEMINI_API_KEY", "")
    assert is_gemini_available() is False

    monkeypatch.setattr(ai_tactics_recap, "GEMINI_API_KEY", "test_key")
    monkeypatch.setattr(ai_tactics_recap, "GEMINI_ENABLE", False)
    assert is_gemini_available() is False


def test_update_tactics_recap_mocked_gemini(tmp_path, monkeypatch):
    import ai_tactics_recap

    # 1. Setup temporary file
    fake_recap = {
        "report_date": "2026-09-23",
        "target_date": "2026-09-24",
        "status_summary": {"tactical_stance": "old"},
        "tactics_system": [{"name": "战法1"}],
        "today_recap": {"market_qualitative": "old", "zhongjun_analysis": "old"},
        "yesterday_comparison": [],
        "tomorrow_plan": {"auction_beacons": []},
    }
    recap_file = tmp_path / "tactics_recap.json"
    recap_file.write_text(json.dumps(fake_recap, ensure_ascii=False), encoding="utf-8")
    history_file = tmp_path / "tactics_recap_history.jsonl"

    monkeypatch.setattr(ai_tactics_recap, "TACTICS_RECAP_PATH", recap_file)
    monkeypatch.setattr(ai_tactics_recap, "TACTICS_HISTORY_PATH", history_file)

    # 2. Mock call_gemini_json
    mock_ai_output = {
        "market_qualitative": "今日市场普跌，资金防御。",
        "zhongjun_analysis": "中军回踩均线承接良好。",
        "yesterday_comparison": [
            {
                "point": "【P0 龙头】",
                "yesterday_plan": "冲高止盈",
                "today_reality": "早盘冲高20%",
                "result_badge": "✅ 完全兑现",
                "badge_color": "#3fb950",
                "eval": "执行完美",
            }
        ],
        "auction_beacons": [
            {
                "beacon": "雷达 1",
                "target": "总高标",
                "metric": "竞价金额>5000万",
                "judgment": "高开可看高一线",
            }
        ],
    }

    monkeypatch.setattr(ai_tactics_recap, "call_gemini_json", lambda *a, **kw: mock_ai_output)

    # 3. Execute
    res = update_tactics_recap_file(report_date="2026-09-24", target_date="2026-09-25")

    # 4. Verify in-memory result
    assert res["report_date"] == "2026-09-24"
    assert res["target_date"] == "2026-09-25"
    assert res["today_recap"]["market_qualitative"] == "今日市场普跌，资金防御。"
    assert res["today_recap"]["zhongjun_analysis"] == "中军回踩均线承接良好。"
    assert res["yesterday_comparison"][0]["result_badge"] == "✅ 完全兑现"
    assert res["tomorrow_plan"]["auction_beacons"][0]["beacon"] == "雷达 1"

    # 5. Verify file written atomically
    saved = json.loads(recap_file.read_text(encoding="utf-8"))
    assert saved["report_date"] == "2026-09-24"

    # 6. Verify history appended
    history_lines = history_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(history_lines) == 1
    h_rec = json.loads(history_lines[0])
    assert h_rec["report_date"] == "2026-09-24"
    assert h_rec["yesterday_comparison"][0]["point"] == "【P0 龙头】"


def test_update_tactics_recap_fallback_when_gemini_unavailable(tmp_path, monkeypatch):
    import ai_tactics_recap

    fake_recap = {
        "report_date": "2026-09-23",
        "today_recap": {"zhongjun_analysis": "保持不变"},
    }
    recap_file = tmp_path / "tactics_recap.json"
    recap_file.write_text(json.dumps(fake_recap, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ai_tactics_recap, "TACTICS_RECAP_PATH", recap_file)
    monkeypatch.setattr(ai_tactics_recap, "call_gemini_json", lambda *a, **kw: None)

    res = update_tactics_recap_file(report_date="2026-09-24", target_date="2026-09-25")
    # Graceful fallback: file is untouched
    assert res["report_date"] == "2026-09-23"
    assert res["today_recap"]["zhongjun_analysis"] == "保持不变"
