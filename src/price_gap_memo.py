# -*- coding: utf-8 -*-
"""价格缺口负缓存: 记住"这只票这一天怎么抓都没有", 避免每次跑批重抓同一批死代码。

背景 (实测 2026-08-22, 报告日 2026-08-20):
  价格更新的放行门禁要求报告日 raw/qfq 覆盖 **全部** 5538 只代码 (`>= len(all_codes)`),
  而 7 只票 (sh600984/sz002084/sz002155/sz002445/sz002906/sz300176/sz300862) 在该交易日
  就是没有前复权价 —— 覆盖永远到不了 100%, 于是每次跑批都判"数据落后", 触发全市场重抓:
    baostock 登录超时 2.3s + 统一备用源对这 7 只 40s ≈ 53s, 最终 "未获取到新价格数据"。
  (实测门禁四项里 raw 100% / breadth_pair_ready=True 都过, 唯一卡在 qfq 5531/5538。)

策略 (与 em_stock_plates 的负缓存同构):
  按 (code, date) 记录失败次数与最后尝试时间, 达到阈值后跳过:
    - 历史日 (date < 今天): 阈值 1 次 —— 过去某天没有的成交价, 以后也不会长出来
      (且 PriceProvider 自带 retry=2, 一轮跑批本身已是多次尝试);
    - 当天/未来 (date >= 今天): 阈值 2 次且只跳过 TTL_TODAY 秒 —— 盘中数据可能晚些才出。
  任一次成功取到该 (code, date) 立即删记录, 偶发网络故障不会被钉死。

系统性故障护栏:
  代理挂掉 / 接口全线 502 会让整轮全失败。若失败数超过
  `max(MAX_RECORD_ABS, 本轮请求数 * MAX_RECORD_RATIO)`, 判为系统性故障, **整轮不记账**,
  避免把全市场误钉成"抓不到"。

⚠️ 本模块只影响"抓不抓", 不影响覆盖率统计与质量门禁: 缓存内容与 meta 口径完全不变,
   跳过后的 coverage 数字与重抓一轮失败后的数字相同 (实测重抓恒为 0 行)。
   需要强制重试时设 PRICE_GAP_RETRY_ALL=1。
"""
import os
import time

import pandas as pd

from paths import PRICE_GAP_MEMO

FAIL_THRESHOLD_PAST = 1     # 历史日: 一轮跑批 (内含 retry) 全失败即判定抓不到
FAIL_THRESHOLD_TODAY = 2    # 当天: 需连续两轮失败
TTL_TODAY = 3 * 3600        # 当天的 (code,date) 只跳过 3 小时, 留盘中重试窗口
MAX_RECORD_ABS = 50         # 系统性故障护栏: 失败数上限 (绝对)
MAX_RECORD_RATIO = 0.05     # 系统性故障护栏: 失败数上限 (占本轮请求数比例)
CHRONIC_MIN_DATES = 3       # 长期缺价: 至少 3 个不同历史日都判定抓不到
CHRONIC_PROBE_DAYS = 7      # 长期缺价: 每 7 天放行一次探针 (复牌/接口修复能自愈)
CHRONIC_MAX_CODES = 30      # 长期缺价: 只在缺口很小时启用 (与占比取较大者)
_COLUMNS = ['code', 'date', 'attempts', 'last_attempt']


def _force_retry():
    return os.environ.get('PRICE_GAP_RETRY_ALL', '').strip().lower() in {'1', 'true', 'yes'}


def _today():
    return time.strftime('%Y-%m-%d')


def _read_rows():
    """→ {(code, date): (attempts, last_attempt_ts)}; 读失败返回空表 (纯优化件, 绝不阻断)。"""
    if not os.path.exists(PRICE_GAP_MEMO):
        return {}
    try:
        df = pd.read_csv(PRICE_GAP_MEMO, dtype={'code': str, 'date': str})
    except Exception:
        return {}
    memo = {}
    for row in df.itertuples(index=False):
        code = str(getattr(row, 'code', '') or '').strip()
        date = str(getattr(row, 'date', '') or '').strip()
        if not code or not date:
            continue
        try:
            attempts = int(getattr(row, 'attempts', 0) or 0)
        except (TypeError, ValueError):
            attempts = 0
        try:
            last = float(getattr(row, 'last_attempt', 0) or 0)
        except (TypeError, ValueError):
            last = 0.0
        memo[(code, date)] = (attempts, last)
    return memo


def load_memo():
    """查询侧读取; PRICE_GAP_RETRY_ALL=1 时返回空表 (等价于全部重试)。"""
    return {} if _force_retry() else _read_rows()


def _skippable(memo, code, date, now=None):
    attempts, last = memo.get((code, date), (0, 0.0))
    if date >= _today():
        # 当天/未来日: 数据可能稍后才出, 阈值更高且只在 TTL 内跳过。
        return attempts >= FAIL_THRESHOLD_TODAY and (now or time.time()) - last < TTL_TODAY
    return attempts >= FAIL_THRESHOLD_PAST


def unobtainable_codes(codes, dates, now=None):
    """codes 中"在 dates 每一天都已判定抓不到"的子集 (即可安全跳过的)。"""
    if _force_retry() or not codes or not dates:
        return set()
    memo = load_memo()
    if not memo:
        return set()
    now = now or time.time()
    dates = [str(d).strip() for d in dates if str(d).strip()]
    return {
        code for code in codes
        if all(_skippable(memo, code, date, now) for date in dates)
    }


def _save_memo(memo):
    """落盘; 失败静默 (纯优化件)。"""
    try:
        rows = [
            {'code': c, 'date': d, 'attempts': a, 'last_attempt': round(float(t), 3)}
            for (c, d), (a, t) in sorted(memo.items())
        ]
        pd.DataFrame(rows, columns=_COLUMNS).to_csv(
            PRICE_GAP_MEMO, index=False, encoding='utf-8-sig')
        return True
    except Exception:
        return False


def record_outcome(failed_pairs, success_pairs=(), attempted=None, now=None):
    """记录一轮抓取结果。

    failed_pairs / success_pairs: 可迭代的 (code, date)。
      失败 → attempts += 1 并刷新 last_attempt;
      成功 → 直接删记录 (偶发网络故障不会被钉死)。
    attempted: 本轮请求的 (code,date) 对数, 用于系统性故障护栏; None 表示不设护栏。
    返回落盘后的记录条数; 未写盘 / 判为系统性故障返回 None。
    """
    failed = {(str(c).strip(), str(d).strip()) for c, d in (failed_pairs or ())}
    ok = {(str(c).strip(), str(d).strip()) for c, d in (success_pairs or ())}
    ok = {p for p in ok if p[0] and p[1]}
    failed = {p for p in failed if p[0] and p[1]} - ok
    if not failed and not ok:
        return None

    if failed and attempted:
        limit = max(MAX_RECORD_ABS, int(attempted) * MAX_RECORD_RATIO)
        if len(failed) > limit:
            # 全线失败更像代理/接口故障而非"这些票没有价", 整轮不记账。
            return None

    memo = _read_rows()
    ts = now or time.time()
    for pair in ok:
        memo.pop(pair, None)
    for pair in failed:
        memo[pair] = (memo.get(pair, (0, 0.0))[0] + 1, ts)

    return len(memo) if _save_memo(memo) else None


def chronic_codes(codes, now=None):
    """→ codes 中"长期抓不到"的子集 (与具体日期无关, 用于**新日期的首轮**)。

    仅按日期精确匹配的 `unobtainable_codes` 管不了新日期: 每来一个新交易日,
    那几只常年没有前复权价的票又要各自跑一轮备用源 (实测 7 只换 0 行、40s),
    线上 CI 每天都吃这笔。判据: 该代码在 **CHRONIC_MIN_DATES 个不同历史日**
    都已判定抓不到 → 认为它在新日期上也不会有。

    两道自愈护栏, 避免把复牌/接口修复后的票永久钉死:
      ① 探针: 最近一次尝试已超过 CHRONIC_PROBE_DAYS 天时不跳过, 放行一次真实尝试
         (那次尝试会刷新 last_attempt, 成功则记录被直接删除);
      ② 调用方 (`_fill_price_gaps_with_provider`) 只在缺口规模很小时才启用本规则,
         代理故障造成的大面积缺口照旧全量重抓。
    另外本规则只作用于"逐只补缺口"的备用源, 全市场批量源每天照抓所有代码,
    所以票一旦恢复有价, 主路径就会自然覆盖它, 不再进入缺口名单。
    """
    if _force_retry() or not codes:
        return set()
    memo = _read_rows()
    if not memo:
        return set()
    now = now or time.time()
    today = _today()
    dates_by_code = {}
    last_by_code = {}
    for (code, date), (attempts, last) in memo.items():
        if date >= today or attempts < FAIL_THRESHOLD_PAST:
            continue
        dates_by_code.setdefault(code, set()).add(date)
        last_by_code[code] = max(last_by_code.get(code, 0.0), last)
    probe_cutoff = now - CHRONIC_PROBE_DAYS * 86400
    return {
        code for code in codes
        if len(dates_by_code.get(code, ())) >= CHRONIC_MIN_DATES
        and last_by_code.get(code, 0.0) >= probe_cutoff
    }
