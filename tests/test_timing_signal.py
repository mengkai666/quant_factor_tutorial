import pandas as pd

from timing_signal import generate_timing_signal


def test_timing_signal_keeps_interface_but_does_not_claim_static_win_rate():
    result = generate_timing_signal(
        pd.DataFrame({"连板高度": [4, 5, 6]}),
        {
            "up": 3000,
            "down": 500,
            "zt": 100,
            "dt": 1,
            "zt_max_height": 6,
            "zt_max_height_prev": 5,
            "zt_prev": 80,
        },
    )

    assert "win_rate" in result
    assert result["win_rate"] is None
    assert "历史" not in result["desc"]
