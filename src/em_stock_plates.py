# -*- coding: utf-8 -*-
"""东财个股所属概念板块归因 (独立模块)。

主线天梯覆盖全市场强势股, 其中绝大多数没有涨停记录, 拿不到 CLS 题材数据,
原先只能靠 industry_cache 的证监会注册行业硬套 (~97% 走行业回退, 系统性错配)。

本模块用东财个股所属板块接口 (push2.eastmoney.com/api/qt/slist/get) 为任意个股
拉取其全部概念板块名, 再喂给主程序的 classify_by_tags / classify_by_plate_name
做投票归因 (取众数, 不受返回顺序影响)。实测归因率从 ~2.5% 提升到 ~94%,
行业回退退回真正的兜底角色。

关键坑 (已处理):
  1. 接口返回的 data.diff 字段可能是 dict (含 f14) 也可能是 list, 两种都要解析;
     只当 dict 处理会 AttributeError, 被 except 吞掉后全部归因失败。
  2. 并发请求必须 trust_env=False 绕过系统代理 (本机 Clash 白名单不含东财域名,
     走代理并发会被限流/拒), 与 price 缓存腾讯快速路径同源。

用法:
    from em_stock_plates import attribute_codes
    code_to_sub_ml = attribute_codes(codes, classify_by_tags, classify_by_plate_name,
                                     mainline_names, trade_date='YYYYMMDD')
    # 返回 {code: (sub, ml)}, 仅含成功归入主线的股票。
"""
import os
import time
import requests
import pandas as pd
from datetime import datetime
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from data_sources.models import normalize_code
from paths import EM_PLATE_CACHE

# 东财个股所属板块接口
_EM_URL = "https://push2.eastmoney.com/api/qt/slist/get"
_EM_UT = "f057cbcbce2a86e2866ab8877db1d059"
_EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

# 缓存最大保留天数 (只保留最近 N 个交易日的归因, 天梯只看当天)
_EM_CACHE_KEEP_DAYS = 10

# "接口答复了但一个板块都没有"的负缓存护栏 (见 _answered_empty_codes):
# 空答复数超过 max(绝对值, 本轮占比) 时判为接口异常, 整轮不记账。
_EMPTY_NEG_MIN = 30
_EMPTY_NEG_RATIO = 0.05


def _em_secid(code):
    """内部代码 sh600000 / sz300750 → 东财 secid 1.600000 / 0.300750。"""
    mkt = '1' if code.startswith('sh') else '0'
    return f"{mkt}.{code[2:]}"


def _parse_diff(diff):
    """接口 data.diff 可能是 dict 或 list, 统一提取板块名 (f14)。"""
    if isinstance(diff, dict):
        items = diff.values()
    elif isinstance(diff, list):
        items = diff
    else:
        return []
    names = []
    for v in items:
        if isinstance(v, dict):
            n = v.get('f14')
            if n:
                names.append(n)
    return names


def _fetch_plates(code, session, retry=3):
    """抓取单只个股所属的全部概念板块名。失败返回 (code, [])。"""
    params = {
        "spt": 3, "secid": _em_secid(code), "fields": "f12,f13,f14",
        "po": 1, "pz": 300, "pi": 0, "np": 1, "fltt": 2, "invt": 2, "ut": _EM_UT,
    }
    for attempt in range(retry):
        try:
            r = session.get(_EM_URL, params=params, headers=_EM_HEADERS, timeout=8)
            if r.status_code == 200:
                data = r.json().get('data') or {}
                names = _parse_diff(data.get('diff'))
                if names:
                    return code, names
        except Exception:
            pass
        time.sleep(0.3 * (attempt + 1))
    return code, []


# 归因缓存读取记忆 (按文件指纹): 一轮报告 load_all_attributions 被调 4 次、
# _load_cache 2 次, 每次都重读同一份 CSV 再聚合 (实测合计 1.8s)。缓存一写指纹就变,
# 记忆整体失效, 所以不会读到过期数据 (同 time_utils.get_latest_date 的教训)。
# 返回浅拷贝, 免得调用方就地改动污染记忆。
_ALL_ATTR_MEMO: dict = {}
_DAY_CACHE_MEMO: dict = {}


def _cache_stamp():
    """归因缓存文件指纹; 取不到返回 None 表示不可记忆。"""
    from time_utils import _cache_file_stamp
    return _cache_file_stamp(EM_PLATE_CACHE)


def _load_cache(trade_date):
    """读取当日归因缓存。

    返回 (positive, attempted):
      positive: {code: (sub, ml)} 已成功归入主线的股;
      attempted: set(code) 当天已抓过的全部股 (含无主线的负缓存),
                 用于跳过重抓 —— 无主线的股 sub/mainline 存空串。
    """
    positive, attempted = {}, set()
    if not (trade_date and os.path.exists(EM_PLATE_CACHE)):
        return positive, attempted
    key = (_cache_stamp(), str(trade_date))
    if key[0] is not None and key in _DAY_CACHE_MEMO:
        cached_positive, cached_attempted = _DAY_CACHE_MEMO[key]
        return dict(cached_positive), set(cached_attempted)
    try:
        df = pd.read_csv(EM_PLATE_CACHE, dtype=str).fillna('')
        if df.empty or 'date' not in df.columns:
            return positive, attempted
        day = df[df['date'] == str(trade_date)]
        # ⚠️ 用列 tolist() 而不是 iterrows(): 后者每行造一个 Series (实测 5232 行 0.42s)。
        blank = [''] * len(day)
        codes = day['code'].tolist()
        subs = day['sub'].tolist() if 'sub' in day.columns else blank
        mls = day['mainline'].tolist() if 'mainline' in day.columns else blank
        for code, sub, ml in zip(codes, subs, mls):
            attempted.add(code)
            if sub and ml:
                positive[code] = (sub, ml)
    except Exception as e:
        print(f'  ⚠️ 东财板块归因缓存加载失败: {e}')
        return positive, attempted
    if key[0] is not None:
        _DAY_CACHE_MEMO.clear()   # 只留最新指纹一份, 缓存一写就整体失效
        _DAY_CACHE_MEMO[key] = (positive, attempted)
    return positive, attempted


def _save_cache(trade_date, new_rows):
    """追加当日新归因结果, 并裁剪掉超过保留天数的旧数据。"""
    if not (trade_date and new_rows):
        return
    try:
        add_df = pd.DataFrame(new_rows)
        if os.path.exists(EM_PLATE_CACHE):
            old = pd.read_csv(EM_PLATE_CACHE, dtype=str)
            df = pd.concat([old, add_df], ignore_index=True)
        else:
            df = add_df
        # 去重 (date, code), 保留最后一条
        df = df.drop_duplicates(subset=['date', 'code'], keep='last')
        # 只保留最近 N 个交易日
        keep_dates = sorted(df['date'].unique())[-_EM_CACHE_KEEP_DAYS:]
        df = df[df['date'].isin(keep_dates)]
        df.to_csv(EM_PLATE_CACHE, index=False, encoding='utf-8-sig')
    except Exception as e:
        print(f'  ⚠️ 东财板块归因缓存写入失败: {e}')


def _answered_empty_codes(to_fetch, fetched, provider_result):
    """本轮"请求成功、但一个板块名都没返回"的代码集合。

    PlateProvider 的 FetchResult 只在 message 里带失败清单 (格式 ``"code: error"``),
    所以用 ``f'{code}: ' in message`` 反查失败者, 剩下的就是已被接口答复的。
    再用 actual_count 交叉校验 (已答复 = 有板块的 + 空答复的), 对不上直接返回空集 ——
    宁可多抓一轮, 也不能把网络失败误记成"这只票没有板块"。
    """
    message = str(getattr(provider_result, 'message', '') or '')
    answered_empty = set()
    for raw in to_fetch:
        try:
            code = normalize_code(raw)
        except Exception:
            continue
        if code in fetched or f'{code}: ' in message:
            continue
        answered_empty.add(code)
    actual = int(getattr(provider_result, 'actual_count', 0) or 0)
    if len(fetched) + len(answered_empty) != actual:
        return set()
    return answered_empty


def _vote(names, classify_by_tags, classify_by_plate_name, mainline_names):
    """所有板块名投票取众数 (sub, ml)。不受东财返回顺序影响, 比取首命中稳。"""
    votes = Counter()
    for n in names:
        sub, ml = classify_by_tags([n])
        if not ml:
            sub, ml = classify_by_plate_name(n)
        if ml and ml in mainline_names:
            votes[(sub, ml)] += 1
    if not votes:
        return None
    return votes.most_common(1)[0][0]


def attribute_codes(codes, classify_by_tags, classify_by_plate_name,
                    mainline_names, trade_date=None, max_workers=16,
                    plate_provider=None):
    """为一批个股拉取东财概念板块并投票归因到 (细分板块, 大主线)。

    Args:
        codes: 内部格式代码列表 (sh600000 / sz300750)。
        classify_by_tags / classify_by_plate_name: 主程序的分类函数 (复用同一套映射)。
        mainline_names: MAINLINE_NAMES, 用于过滤有效主线。
        trade_date: 'YYYYMMDD', 命中当日缓存则跳过抓取; 传 None 则不走缓存。
        max_workers: 并发数 (16 实测稳定 0 失败, 约 35 只/秒; 会话复用后瓶颈在服务端)。

    Returns:
        {code: (sub, ml)} 仅含成功归入主线的股票。
    """
    result = {}
    if not codes:
        return result

    # 1. 先吃当日缓存 (positive: 已归入主线; attempted: 当天已抓过的全部, 含无主线负缓存)
    positive, attempted = _load_cache(trade_date)
    to_fetch = []
    for c in codes:
        if c in positive:
            result[c] = positive[c]
        elif c not in attempted:
            to_fetch.append(c)  # 既未归入主线, 也没抓过 → 需抓

    if not to_fetch:
        return result

    print(f"  📥 东财个股板块归因: {len(to_fetch)} 只待抓 "
          f"(缓存命中 {len(positive)} 只, 负缓存跳过 {len(attempted) - len(positive)} 只)...")

    # 2. 统一通过 PlateProvider 抓取，Provider 负责来源、状态和代码规范。
    t0 = time.time()
    if plate_provider is None:
        from data_sources.fetch_status import FetchStatusStore
        from data_sources.plate_provider import PlateProvider
        from paths import FETCH_STATUS_CACHE
        plate_provider = PlateProvider(
            status_store=FetchStatusStore(FETCH_STATUS_CACHE), max_workers=max_workers
        )
    compact_date = str(trade_date or datetime.now().strftime("%Y%m%d")).replace("-", "")
    provider_date = f"{compact_date[:4]}-{compact_date[4:6]}-{compact_date[6:8]}"
    provider_result = plate_provider.fetch_codes(to_fetch, provider_date)
    fetched = {}
    if provider_result.data is not None and not provider_result.data.empty:
        fetched = {
            code: group["plate_name"].dropna().astype(str).tolist()
            for code, group in provider_result.data.groupby("code")
        }

    # 3. 投票归因。取到板块的股全部写缓存: 成功归主线写 (sub, ml),
    #    无主线写空串作负缓存 (避免明天/重跑再抓)。
    new_rows = []
    hit = 0
    for code, names in fetched.items():
        sub_ml = _vote(names, classify_by_tags, classify_by_plate_name, mainline_names)
        if sub_ml:
            result[code] = sub_ml
            hit += 1
            if trade_date:
                new_rows.append({'date': str(trade_date), 'code': code,
                                 'sub': sub_ml[0], 'mainline': sub_ml[1]})
        elif trade_date:
            new_rows.append({'date': str(trade_date), 'code': code,
                             'sub': '', 'mainline': ''})  # 负缓存

    # 3.5 "接口答复了但没有板块"的代码同样写负缓存 (同一交易日内不再重抓)。
    #    实测 2026-08-20: 11 只每轮都被重抓, provider 回 status=zero
    #    (11/11 请求成功、0 个板块名), 4.4s 换 0 行 —— 旧逻辑把"成功的空答复"
    #    和"网络失败"混为一谈, 于是当天每跑一次就再问一遍。只记本轮确实被答复的;
    #    新交易日照旧重问一次 (新股/新概念会长出板块), 所以不会永久钉死。
    empty_negatives = set()
    if trade_date and getattr(provider_result, 'status', '') != 'failed':
        candidates = _answered_empty_codes(to_fetch, fetched, provider_result)
        limit = max(_EMPTY_NEG_MIN, int(len(to_fetch) * _EMPTY_NEG_RATIO))
        if candidates and len(candidates) <= limit:
            # 超限时判为接口异常 (维护期整片返回空), 整轮不记账 —— 同 price_gap_memo。
            empty_negatives = candidates
            new_rows.extend({'date': str(trade_date), 'code': code,
                             'sub': '', 'mainline': ''} for code in sorted(candidates))

    tail = ''
    if empty_negatives:
        sample = ', '.join(sorted(empty_negatives)[:6])
        more = '…' if len(empty_negatives) > 6 else ''
        tail = (f", {len(empty_negatives)} 只接口答复无板块已记负缓存"
                f" ({sample}{more})")
    print(f"  ✅ 东财归因完成: {len(fetched)}/{len(to_fetch)} 只取到板块, "
          f"{hit} 只成功归入主线{tail} (耗时 {time.time()-t0:.1f}s)")

    # 4. 写缓存
    _save_cache(trade_date, new_rows)

    return result


def load_all_attributions():
    """读取东财归因缓存的全部正向记录, 聚合成 {code: (sub, ml)}。

    跨全部缓存日期取每只 code 的最新一条正向归因 (按 date 升序, keep=last),
    供 calc_subsector_returns 复用作板块成员池 —— 概念级精准, 优于证监会行业回退。
    负缓存 (sub/ml 为空) 跳过。

    Returns:
        {code: (sub, ml)} 仅含成功归入主线的股票。缓存不存在返回 {}。
    """
    out = {}
    if not os.path.exists(EM_PLATE_CACHE):
        return out
    stamp = _cache_stamp()
    if stamp is not None and stamp in _ALL_ATTR_MEMO:
        return dict(_ALL_ATTR_MEMO[stamp])
    try:
        df = pd.read_csv(EM_PLATE_CACHE, dtype=str).fillna('')
        if df.empty or not {'date', 'code', 'sub', 'mainline'}.issubset(df.columns):
            return out
        df = df[(df['sub'] != '') & (df['mainline'] != '')]
        df = df.sort_values('date').drop_duplicates('code', keep='last')
        # 同上: tolist()+zip 取代 iterrows() (实测 4 次调用 11392 行合计 0.96s)。
        out = dict(zip(df['code'].tolist(),
                       zip(df['sub'].tolist(), df['mainline'].tolist())))
    except Exception as e:
        print(f'  ⚠️ 东财板块归因缓存聚合读取失败: {e}')
        return out
    if stamp is not None:
        _ALL_ATTR_MEMO.clear()
        _ALL_ATTR_MEMO[stamp] = out
    return dict(out)
