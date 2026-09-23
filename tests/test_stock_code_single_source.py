# -*- coding: utf-8 -*-
"""证券代码的交易所归属只能有一份判据 (2026-09-12)。

背景: 同一个 6 位裸码的归属, 项目里先后写过两份不一致的实现 ——
    `data_sources.models._canonical_code`:  "4"/"8"/"92"→bj, "5"/"6"/"9"→sh, "0"/"1"/"2"/"3"→sz
    `report_logic._normalize_stock_code_text`: 只有 920/430/830/870/400→bj, 600/601/603/605/688/689→sh, 其余→sz

后果: `832xxx`/`871xxx`/`88xxxx`/`900xxx`/`5xxxxx` 在两套规则下属于**不同交易所**
(其中 `5xxxxx` 是沪市基金、`900xxx` 是沪市B股, report_logic 会把它们判成深市)。

当前全库证券池只用到 0/3/6/9 开头的代码 (2026-09-12 核实: 价格为 0/3/6/9, 涨停历史
同为 0/3/6/9), 所以这个分歧暂时没有实际后果 —— 但它是一旦扩容北交所存量段或基金
就会静默错位的定时炸弹。本测试把两侧钉在同一份判据上。
"""
import pytest


# (裸 6 位码, 期望交易所) —— 覆盖全部代码段, 含两套旧实现分歧的那几段
CASES = [
    ("600000", "sh"), ("601988", "sh"), ("603259", "sh"), ("605179", "sh"),
    ("688981", "sh"), ("689009", "sh"),
    ("000001", "sz"), ("001979", "sz"), ("002415", "sz"), ("300750", "sz"),
    ("920117", "bj"), ("430047", "bj"), ("830799", "bj"), ("870204", "bj"),
    # 旧实现分歧段: models 判 bj/sh, report_logic 曾一律判 sz
    ("832000", "bj"), ("871981", "bj"), ("889900", "bj"),
    ("900901", "sh"), ("510300", "sh"),
]


@pytest.mark.parametrize("digits,expected", CASES)
def test_infer_exchange_covers_every_code_segment(digits, expected):
    from stock_code import infer_exchange

    assert infer_exchange(digits) == expected


def test_unknown_segment_is_not_guessed():
    """7xxxxx (配股/申购段) 无法确定归属 —— 返回 None, 不猜。"""
    from stock_code import infer_exchange

    assert infer_exchange("730001") is None
    assert infer_exchange("00001") is None
    assert infer_exchange("abcdef") is None


@pytest.mark.parametrize("digits,expected", CASES)
def test_report_logic_and_models_agree_on_every_segment(digits, expected):
    """两份实现必须同源 —— 这正是本测试要防的回归。"""
    from data_sources.models import normalize_code
    from report_logic import normalize_stock_code

    assert normalize_stock_code(digits) == expected + digits
    assert normalize_code(digits) == expected + digits


def test_report_logic_still_normalizes_the_prefixed_and_suffixed_forms():
    """统一判据不能破坏既有的前缀/后缀/混合写法。"""
    from report_logic import normalize_stock_code

    assert normalize_stock_code("600000.SH") == "sh600000"
    assert normalize_stock_code("SZ.000001") == "sz000001"
    assert normalize_stock_code("bj920001") == "bj920001"
    assert normalize_stock_code("920001.BJ") == "bj920001"
    assert normalize_stock_code("430047") == "bj430047"
    assert normalize_stock_code("") == ""


def test_report_logic_does_not_silently_default_unknown_to_shenzhen():
    """旧实现把无法确定的段一律当深市 —— 那是编出来的归属, 现在留空。"""
    from report_logic import normalize_stock_code

    assert normalize_stock_code("730001") == ""


def test_models_keeps_raising_on_unknown_and_invalid_codes():
    """严格侧的既有契约不能因为共用判据而放松。"""
    from data_sources.models import normalize_code

    with pytest.raises(ValueError):
        normalize_code("730001")
    with pytest.raises(ValueError):
        normalize_code(None)
