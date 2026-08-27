"""锚定日前复权补齐 (零网络)。

前复权序列锚在**最新一根 K 线**上: 该根的复权因子恒为 1, 所以
``close_qfq(最新日) == close_raw(最新日)`` 是恒等式, 不是近似。
报告日的 qfq 因此不需要任何网络请求 —— 逐股去抓等于花十几分钟
把 raw 抄一遍。

本机缓存实测 (抓取当日即当时的最新日, 全部由腾讯一次抓回):

| 日期 | 双列都有 | 逐股完全相等 |
|---|---|---|
| 2026-08-26 | 5535 | 5535 (100.00%) |
| 2026-08-20 | 5531 | 5531 (100.00%) |
| 2026-08-10 | 5538 | 5538 (100.00%) |

**只对锚定日成立**。历史日的 qfq 是当年抓的、锚点早已右移, 加上其后的除权,
2026-08-24 实测最大偏 10.23%, 故本函数只碰 ``anchor_date`` 这一天,
其余日期一行不动 (历史缺口仍走网络补)。

与 A/D 真源无关: 涨跌家数用 raw (见备忘 sentiment-ad-reconcile 点 9),
本函数只填 qfq 空格, 不改任何已有数值。
"""
from __future__ import annotations

import pandas as pd

QFQ_ANCHOR_TAG = 'qfq=raw@anchor'


def normalize_price_date(value) -> str:
    """'20260827' / '2026-08-27' → '2026-08-27'; 认不出的原样返回。"""
    text = str(value or '').strip()
    if not text:
        return ''
    digits = text.replace('-', '').replace('/', '')
    if len(digits) == 8 and digits.isdigit():
        return f'{digits[:4]}-{digits[4:6]}-{digits[6:]}'
    return text


def fill_anchor_day_qfq(frame: pd.DataFrame, anchor_date, *, codes=None):
    """把锚定日 close_qfq 的空格用同一行的 close_raw 填上。

    返回 ``(frame, 补齐行数)``。有可填的行才复制, 否则原样返回。
    只填空格: 已有 qfq 一律不覆盖; 没有正 raw 的行不动。

    **前置条件**: ``anchor_date`` 必须是刚抓回来的那一天、也就是 frame 里最新的一天。
    数据源给的 qfq 锚在**市场最新一根 K 线**上, 只有那一根 factor=1。传一个更早的
    日期(而 frame 里还有更晚的行)说明它已经不是锚点了, 此时 qfq≠raw, 函数直接拒填。
    """
    anchor = normalize_price_date(anchor_date)
    if frame is None or frame.empty or not anchor:
        return frame, 0
    if 'close_raw' not in frame.columns or 'close_qfq' not in frame.columns:
        return frame, 0
    if 'date' not in frame.columns:
        return frame, 0
    if str(frame['date'].astype(str).max()) > anchor:
        return frame, 0     # 不是最新一根 K 线, 锚点已右移, 恒等式不成立

    mask = (
        (frame['date'].astype(str) == anchor)
        & frame['close_qfq'].isna()
        & frame['close_raw'].notna()
        & (pd.to_numeric(frame['close_raw'], errors='coerce') > 0)
    )
    if codes is not None:
        wanted = {str(code).strip() for code in codes if str(code).strip()}
        if wanted:
            mask &= frame['code'].astype(str).isin(wanted)
    filled = int(mask.sum())
    if not filled:
        return frame, 0

    result = frame.copy()
    result.loc[mask, 'close_qfq'] = pd.to_numeric(
        result.loc[mask, 'close_raw'], errors='coerce')
    if 'price_basis' in result.columns:
        result.loc[mask, 'price_basis'] = 'raw+qfq'
    if 'source' in result.columns:
        origin = result.loc[mask, 'source'].fillna('').astype(str)
        result.loc[mask, 'source'] = [
            text if QFQ_ANCHOR_TAG in text else (
                f'{text}+{QFQ_ANCHOR_TAG}' if text else QFQ_ANCHOR_TAG)
            for text in origin
        ]
    return result, filled
