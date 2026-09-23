# -*- coding: utf-8 -*-
"""证券代码的交易所归属判定 —— 单一真源。

为什么单独成模块:
    同一个 6 位裸码的归属, 项目里先后写过两份**判据不一致**的实现:
      · `data_sources/models.py`   "4"/"8"/"92"→bj, "5"/"6"/"9"→sh, "0"/"1"/"2"/"3"→sz
      · `report_logic.py`          只有 920/430/830/870/400→bj, 600/601/603/605/688/689→sh, 其余→sz
    于是 `832xxx`/`871xxx`/`88xxxx` 归属于不同交易所, `5xxxxx`(沪市基金) 和
    `900xxx`(沪市B股) 更是被 report_logic 判成深市。当前全库证券池只用 0/3/6/9
    开头的代码, 所以分歧暂时没有实际后果 —— 但扩容到北交所存量段或基金就会静默错位。
    与 `src/ad_breadth.py` 同一个理由: 判据只能有一份, 新路径一律 import 这里,
    不要再复制代码段常量。

判据 (交易所代码段):
    bj  4xxxxx / 8xxxxx / 92xxxx      北交所: 430/830/870 存量段 + 920 新段
    sh  5xxxxx / 6xxxxx / 9xxxxx      沪市: 6xx 主板, 688/689 科创板, 5xx 基金, 900 B股
    sz  0xxxxx / 1xxxxx / 2xxxxx / 3xxxxx
    其余 (如 7xxxxx 配股/申购段) 无法确定 → None, 由调用方决定报错还是留空。
"""
from __future__ import annotations

_BJ_STARTS = ("4", "8")
_SH_STARTS = ("5", "6", "9")
_SZ_STARTS = ("0", "1", "2", "3")


def infer_exchange(digits: str) -> str | None:
    """6 位数字代码 → 'sh' / 'sz' / 'bj'; 无法确定返回 None。"""
    if not isinstance(digits, str) or len(digits) != 6 or not digits.isdigit():
        return None
    # "92" 属于 bj 的 920 新段, 必须先判 (否则会被下面的 "9"→sh 抢走)。
    if digits.startswith("92") or digits.startswith(_BJ_STARTS):
        return "bj"
    if digits.startswith(_SH_STARTS):
        return "sh"
    if digits.startswith(_SZ_STARTS):
        return "sz"
    return None
