# -*- coding: utf-8 -*-
"""A/D (涨跌家数) 的市场宽度判据 —— 单一真源。

为什么单独成模块: 判据原先散在 `主线强度追踪.py` / `legacy_tracker.py` /
`limit_ratio_factor.py` / `tools/*.py` 里各写一份, 而**判据漂移的代价是静默改写历史**
(见 2026-09-01 的事故: 合并边界没走判据, 12 个历史日被窄口径覆盖, 20260319 由
4096/1347 的普涨日翻成 335/3095 的下跌日)。任何新的写入路径都必须 import 这里,
不要再复制常量。

术语:
  完整 (complete)  : up+down >= MIN_MARKET_BREADTH, 可作权威值发布
  残缺 (incomplete): 低于门槛 = 只覆盖了部分股票的快照, 方向都可能是反的
  变窄 (narrower)  : 过了绝对门槛, 但仍比已有的完整值窄一截 (口径退化)
"""
from __future__ import annotations

# 全市场宽度下限: up+down 低于此值视为残缺快照 (全市场 ~5200~5500 只)
MIN_MARKET_BREADTH = 4000

# 已有值本身完整时, 新值比它窄这么多就不采用
AD_NARROWER_TOLERANCE = 0.98


def _as_float(value):
    """NaN / None / 空串 / 非数字 一律归一成 None。

    必须做这一步: `float(nan or 0)` 得到的是 nan (nan 是真值!), 而 `nan < 4000`
    为 False —— 直接把 NaN 喂给 is_ad_incomplete 会被判成"完整", 判据反向失效。
    """
    if value is None:
        return None
    try:
        num = float(value)
    except (ValueError, TypeError):
        return None
    if num != num:  # NaN
        return None
    return num


def is_ad_incomplete(up, down) -> bool:
    """市场宽度体检: up+down 低于全市场规模阈值 = 残缺快照, 不可作权威值发布。
    三道来源 (FuPan/腾讯重算/价格缓存) 与显示层共用此判据, 单一真源避免口径漂移。"""
    u, d = _as_float(up), _as_float(down)
    return (u or 0.0) + (d or 0.0) < MIN_MARKET_BREADTH


def should_adopt_reconciled_ad(new_up, new_dn, cur_up=None, cur_dn=None) -> bool:
    """新算出的一天 A/D 是否可以覆盖已有值。

    价格缓存是 up/down 的唯一真源, 但"真源存在"不等于"真源可信":
    CI 用 actions/cache 恢复的价格缓存对历史日往往只覆盖 ~850 只 (全市场 1/6),
    `_load_ad_cache()` 照样返回 ad_available=True 的残缺 A/D (实测 20260707 得 71/779,
    真值 615/4495)。若无条件覆盖, 每天的 CI 都会把已对齐的历史家数重新写坏一遍。
    因此真源本身也要过市场宽度体检 —— 与 tools/reconcile_sentiment_ad.py 判据同源。
    """
    if _as_float(new_up) is None or _as_float(new_dn) is None:
        return False
    if is_ad_incomplete(new_up, new_dn):
        return False
    # 第二道 (2026-08-27 加): 真源过了 4000 门槛, 仍可能比已有值窄一截。
    # 4000 只是"绝对残缺"的下限, 拦不住"相对变窄": 本机回补的价格缓存只抓到
    # 4588 只 (北交所 333 只 baostock 不收录 + 限流漏抓 617 只), 对账出的 A/D
    # 合计 ~4450, 而这些日子原本存着当日线上跑的全市场值 (合计 ~4870, universe
    # ~4900)。两者方向一致, 只是口径窄了 ~400 只 —— 无条件覆盖等于用窄口径
    # 替换宽口径, 一路把历史磨薄。故已有值本身完整时, 真源明显更窄就不采用。
    # 阈值 0.98: 实测这批变窄日落在 90.7%~97.4%, 全部拦下; 而同口径的真纠错
    # (20260824 盘中价 → 收盘价) 合计只动 2 只 (99.96%), 20260825 也有 99.3%, 照旧放行。
    cur_u, cur_d = _as_float(cur_up), _as_float(cur_dn)
    if cur_u is not None and cur_d is not None and not is_ad_incomplete(cur_u, cur_d):
        new_total = float(_as_float(new_up) or 0.0) + float(_as_float(new_dn) or 0.0)
        cur_total = cur_u + cur_d
        if cur_total > 0 and new_total < cur_total * AD_NARROWER_TOLERANCE:
            return False
    return True


def resolve_ad_pair(primary_up, primary_dn, cache_up, cache_dn):
    """合并边界: 主数据 (本次重算) 与缓存 (历史已存) 二选一, 返回 (up, down)。

    为什么必须**成对**决定: up 和 down 出自同一份价格切片, 一列取重算值另一列取
    缓存值会拼出一个现实中不存在的比值 —— 而 ad_ratio 是情绪指数/择时信号/回测
    结论的共同输入。老实现按列 fill (`fill_mask = df[col].isna() | (df[col] == 0)`)
    正好犯这个错, 且"缺失才回填"只拦 NaN/0, 拦不住**窄而非零** ——
    残缺快照 (3369/561) 长得像正常数据, 于是静默胜过缓存里的全市场真值 (4049/834)。

    规则:
      - 缓存没这天 (任一侧缺) → 用主数据 (新的一天只能靠它, 哪怕偏窄; 体检会报)
      - should_adopt_reconciled_ad 判主数据可采用 → 用主数据 (真纠错要能落地)
      - 否则 (主数据残缺 / 或明显窄于完整的缓存值) → 保留缓存
    """
    c_up, c_dn = _as_float(cache_up), _as_float(cache_dn)
    if c_up is None or c_dn is None or c_up + c_dn <= 0:
        # 0/0 与 NaN 同义 = 缓存这天没数据 (两处调用点的 valid_cache 都按 up>0|down>0 过滤,
        # 但 legacy_tracker 会把缺失的 _cache 列 fillna(0), 必须在判据里当"无数据"处理,
        # 否则窄主数据会被清成 0)。
        return primary_up, primary_dn
    if should_adopt_reconciled_ad(primary_up, primary_dn, cache_up, cache_dn):
        return primary_up, primary_dn
    return cache_up, cache_dn


def protect_ad_with_cache(df, date_col: str = '日期') -> list:
    """就地保护合并后的 sentiment 表: up/down 与 up_cache/down_cache 成对择优。

    调用方 (`src/主线强度追踪.py` 与 `src/legacy_tracker.py` 的情绪缓存合并边界) 把
    缓存按日期 left-merge 成 `_cache` 后缀列, 然后交给本函数决定每一天取哪一侧 ——
    **写入路径必须走判据**, 而不是各自写一遍列级 fill_mask (那正是 2026-09-01
    12 个历史日被窄口径覆盖的原因: 判据存在, 但这条路绕开了它)。

    返回被保留缓存值的日期列表 (供调用方打印, 空列表 = 本次重算全部采用)。
    缺列时静默返回空列表, 让老调用方/裁剪过的表不至于炸。
    """
    needed = {'up', 'down', 'up_cache', 'down_cache'}
    if df is None or getattr(df, 'empty', True) or not needed <= set(df.columns):
        return []
    kept = []
    for idx in df.index:
        p_up, p_dn = df.at[idx, 'up'], df.at[idx, 'down']
        r_up, r_dn = resolve_ad_pair(p_up, p_dn, df.at[idx, 'up_cache'], df.at[idx, 'down_cache'])
        if (r_up, r_dn) != (p_up, p_dn):
            df.at[idx, 'up'] = r_up
            df.at[idx, 'down'] = r_dn
            kept.append(str(df.at[idx, date_col]) if date_col in df.columns else str(idx))
    return kept
