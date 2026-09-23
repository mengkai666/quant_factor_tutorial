# pyright: reportMissingTypeStubs=false, reportGeneralTypeIssues=false, reportOperatorIssue=false, reportArgumentType=false
"""连板高度 × 压力高度 —— 系统战法研究 (独立模块)

回答四个问题, 全部用 209 个交易日的真数据算, 不做盘面推测:
  1. 市场最高板 (连板高度) 与压力高度 (前 N 日高点) 之间是什么结构关系
  2. 突破压力高度之后, 后续怎么走 —— 按突破落点分层
  3. 断板之后, 次高板能不能接棒 —— 与"不断板"基准对照
  4. 高度周期的几何形状, 以及可执行的战法 + 成功率

数据源 (全部本地缓存, 零联网):
  data/涨停历史缓存.csv        每日涨停/跌停名单 + 连板数
  data/price_history_cache.csv 日线收盘 (三口径)
  data/sentiment_history_cache.csv 每日涨跌家数

用法:
    python src/height_pressure_study.py            # 全样本 + 近半年子样本
    python src/height_pressure_study.py --half     # 只跑近半年

输出:
    output/height_pressure_system.md

⚠️ 踩过的坑, 判据都写死在常量里, 改之前先读注释:
  - 残缺快照日: 涨停数极少的日子 (20260710 ZT=10/maxh=1, 20260721 ZT=6) 不是真的
    没有高度, 是当天名单没抓全。参与高度序列会凭空造出"崩塌"与"突破"。
  - 价格口径: close_raw / close_qfq / close_legacy 按日成片, 2026-03-26 是分界。
    跨口径相减是错的 (见 ad-price-basis-pairing), 本模块逐股票挑两天共有的口径。
  - 长假: 压力高度是"市场记忆"概念, 回看窗口跨春节 (11 自然日) 没有意义, 这类事件剔除。
  - 首板日: 涨停缓存有 158 条连板链血统断裂, 任何涉及首板日期的推算都用 lb 反推,
    本模块只用当日 lb 快照, 不推首板日, 因此不受该缺陷影响。
"""
from __future__ import annotations

import os
import sys
import warnings
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import ZT_CACHE_FILE, SENTIMENT_CACHE, PRICE_CACHE, OUTPUT_DIR  # noqa: E402
from time_utils import filter_completed_rows  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 判据常量 (单一真源, 改这里不要散着改)
# ─────────────────────────────────────────────────────────────
MIN_ZT_FOR_USABLE = 15    # 当日涨停数低于此值 = 名单没抓全, 该日高度不可信
PRESSURE_WINDOW = 5       # 短压力 = 前 5 个交易日最高板 (不含当日)
CYCLE_FLOOR = 3           # maxh <= 3 视为周期外 (冰点/空窗)
MAX_CALENDAR_GAP = 4      # 回看窗口内相邻交易日自然日跨度上限, 超过 = 跨长假
FWD_WINDOWS = (1, 3, 5, 10)
HALF_YEAR_START = '20260312'  # 近半年子样本起点 (约 121 个交易日)
MIN_CYCLE_LEN = 2         # len==1 的段是单日脉冲不是周期, 不进几何统计
MIN_N_FOR_RULE = 10       # 低于此样本量只写"线索", 不写进战法纪律
# 相邻两天收益恰好 0.00% 的正常占比上限。真正原地不动的收盘价在 A 股是稀有事件,
# 高于这个数就说明价格表里混了"冻结价"。用它当**判据**而不是当散文里的一句话:
# 报告里"修好了/没修好"的结论由残余占比与这个阈值比出来, 不由"掩码跑没跑过"决定。
FROZEN_NORM_PCT = 0.5

# 突破落点分档 —— 单一真源。正文分层表、战法、子样本对照必须共用同一套边界,
# 否则会出现"战法引用的档位在正文里根本没有对应行"这种自相矛盾。
# ⚠️ 实测 38 个突破日的落点最低是 5 板, ≤4 板这一档恒空 —— 因为个股一天最多加
#    一板, maxh(t) <= maxh(t-1)+1, 而压力高度取前 5 日最大值, 想在 4 板突破就
#    要求前 5 日最高板 ≤3 板 (周期外), 这种冷启动日在样本里没出现过。
BREAK_BANDS = ((None, 6, '中低位突破 落点≤6板'), (7, None, '高位突破 落点≥7板'))

# 断板高度分档 —— 同样是单一真源, 正文第四节分层表和战法 D 必须共用。
# ⚠️ 别把 5-6 板和 ≥7 板并成一个"≥5板"。可成交口径下两档的接棒都是负的
#    (5-6板 -3.31% / ≥7板 -14.31%), 但**幅度差 11 个点, 且周期状态相反**:
#    5-6 板断板高度 5 日内修复 85% (周期还活着, 纪律只是"不接棒"),
#    ≥7 板只有 38% (周期真的结束, 手里高位股要走)。并档会把这两个相反的
#    操作压成一句"≥5板小亏别接", 把清仓信号弄丢。
#    (早前版本这里写的是"5-6板是正的" —— 那是 enter3 全篮口径, 含当日涨停
#     买不到的那半个篮子; 换 enterb3 可成交口径后符号就翻了, 见 build_break_events。)
BOARD_BREAK_BANDS = ((3, 4, '3-4板'), (5, 6, '5-6板'), (7, 99, '≥7板'))

# 高度"还活着"的修复率门槛。用来区分两种都亏钱的断板:
#   修复率高 = 接棒亏钱但周期没结束 → 纪律是"不接棒", 不是"清仓";
#   修复率低 = 真的周期结束 → 手里的高位股要走。
# 两者的操作相反, 并成一句"按周期结束处理"会让 5-6 板档自相矛盾
# (那一档修复率 85%, 一边说周期结束一边说高度五天内回来)。
REPAIR_ALIVE = 60.0

_BASIS_PRIORITY = ('close_raw', 'close_qfq', 'close_legacy')


# ─────────────────────────────────────────────────────────────
# 数据加载
# ─────────────────────────────────────────────────────────────
def load_zt() -> pd.DataFrame:
    df = pd.read_csv(ZT_CACHE_FILE, encoding='utf-8-sig', dtype={'日期': str})
    df.columns = [c.strip().lstrip('﻿') for c in df.columns]
    df = filter_completed_rows(df, '日期')
    df['日期'] = df['日期'].astype(str).str.strip()
    df = df[df['日期'].str.len() == 8].copy()
    df['连板数'] = pd.to_numeric(df['连板数'], errors='coerce').fillna(1).astype(int)
    df['代码'] = df['代码'].astype(str).str.strip()
    df['名称'] = df['名称'].astype(str).str.strip()
    return df


def load_sentiment() -> pd.DataFrame:
    df = pd.read_csv(SENTIMENT_CACHE, encoding='utf-8-sig', dtype={'日期': str})
    df.columns = [c.strip().lstrip('﻿') for c in df.columns]
    df = filter_completed_rows(df, '日期')
    for col in ('up', 'down'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['up', 'down'])
    # 宽度门槛与 ad_breadth.MIN_MARKET_BREADTH 同源: 残缺快照方向都可能是反的
    df = df[(df['up'] + df['down']) >= 4000].copy()
    df['日期'] = df['日期'].astype(str).str.strip()
    df['ad_ratio'] = df['up'] / (df['up'] + df['down'])
    return df.set_index('日期')['ad_ratio'].to_dict()


class PriceBook:
    """三口径收盘价的对齐容器 —— 取收益时逐股票挑两天共有的口径。

    为什么不能只取一列: 缓存按日成片 (2026-03-26 由 legacy 切到 raw+qfq),
    "今天的 raw ÷ 昨天的 legacy" 是两个复权 epoch 相减, 实测同一天 raw 与 legacy
    只有 2.65% 的股票一致、中位差 1.22% —— 已是日均波动量级。
    """

    def __init__(self, frame: pd.DataFrame, suspended=None):
        dates = sorted(frame['date'].unique())
        codes = sorted(frame['code'].unique())

        # 停牌日的收盘价是"冻结价": 那天根本没有交易, 既买不进也卖不出, 而缓存里
        # 照抄了停牌前的收盘。把这些格子打成 NaN 之后, 凡是跨停牌日的收益会被
        # returns() 的 (a>0)&(b>0) 直接判掉, 不再以假"平盘"混进任何队列指标。
        # 在 pivot 上一次性掩掉, 而不是在 cohort() 里逐次过滤 —— returns() 是全部
        # 指标的唯一漏斗, 掩在这里等于一次修好下游所有格子 (含 frozen_share 探针)。
        by_date: dict[str, list[str]] = defaultdict(list)
        for c, d in (suspended or ()):
            by_date[d].append(c)

        self.pivots = []
        self.n_masked = 0
        for col in _BASIS_PRIORITY:
            if col not in frame.columns:
                continue
            piv = frame.pivot_table(index='date', columns='code', values=col, aggfunc='last')
            piv = piv.reindex(index=dates, columns=codes)
            if by_date:
                have = set(piv.columns)
                masked = 0
                for d, cs in by_date.items():
                    if d not in piv.index:
                        continue
                    hit = [c for c in cs if c in have]
                    if not hit:
                        continue
                    masked += int(piv.loc[d, hit].notna().sum())
                    piv.loc[d, hit] = np.nan
                self.n_masked = max(self.n_masked, masked)
            self.pivots.append(piv)
        self.dates = set(dates)
        self._cache: dict[tuple[str, str], pd.Series] = {}

    def returns(self, d0: str, d1: str) -> pd.Series:
        """全市场 d0→d1 的百分比收益 (逐股票口径配对), 无覆盖的股票为 NaN。"""
        key = (d0, d1)
        if key in self._cache:
            return self._cache[key]
        if d0 not in self.dates or d1 not in self.dates or d0 == d1:
            out = pd.Series(dtype=float)
        else:
            out = None
            for piv in self.pivots:
                a, b = piv.loc[d0], piv.loc[d1]
                r = ((b / a - 1.0) * 100.0).where((a > 0) & (b > 0))
                out = r if out is None else out.fillna(r)  # 首个命中口径优先
            out = out if out is not None else pd.Series(dtype=float)
        if len(self._cache) > 4000:
            self._cache.clear()
        self._cache[key] = out
        return out

    def frozen_share(self, dates: list[str]) -> tuple[float, int, list[tuple[str, int]]]:
        """相邻交易日收益恰好为 0.00% 的占比 —— 停牌重复收盘价的探针。

        真正原地不动的收盘价在 A 股是稀有事件 (万分之几量级)。占比明显偏高说明
        缓存里塞了停牌日的重复收盘价, 这些假 0% 会被当成"平盘"混进每一个队列指标:
        它既不是涨也不是跌, 却会把中位数往 0 拽、把红盘率往下压。
        返回 (占比%, 参与统计的股票日数, [(代码, 冻结天数)] 前几名)。
        """
        pairs = [(a, b) for a, b in zip(dates, dates[1:])]
        total = 0
        frozen = 0
        per_code: dict[str, int] = defaultdict(int)
        for d0, d1 in pairs:
            r = self.returns(d0, d1).dropna()
            if r.empty:
                continue
            total += len(r)
            z = r[r == 0.0]
            frozen += len(z)
            for code in z.index:
                per_code[code] += 1
        share = frozen / total * 100 if total else float('nan')
        top = sorted(per_code.items(), key=lambda kv: -kv[1])[:3]
        return share, total, top

    def cohort(self, codes, d0: str, d1: str) -> np.ndarray:
        """一篮子股票 d0 收盘买入 / d1 收盘卖出的逐股收益 (%), 丢掉无覆盖的。"""
        if not codes:
            return np.array([])
        r = self.returns(d0, d1)
        if r.empty:
            return np.array([])
        vals = r.reindex(list(codes)).to_numpy(dtype=float)
        return vals[~np.isnan(vals)]


def load_suspension() -> set[tuple[str, str]]:
    """停牌 (代码, 日期) 集合。缺文件就返回空集 —— 本模块退回原来的口径, 不报错。

    真源是 baostock 的 `tradestatus='0'`, 由 tools/backfill_baostock_bars.py 落盘。
    本地缓存里没有停牌标记, 所以这是唯一能剔掉假"平盘"的依据。
    """
    try:
        import baostock_bars as bb
        return bb.load_suspension_set()
    except Exception:
        return set()


def load_price_frame() -> pd.DataFrame:
    """价格长表。单独拆出来是为了**一次读盘喂两个 PriceBook** ——

    报告要现算"剔停牌前 vs 剔停牌后"的假平盘占比, 就得同时有掩码版和未掩码版。
    读这份 76MB CSV 是 2.9s 里的大头, 建 pivot 只是零头, 所以拆开后建两份几乎免费。
    """
    usecols = ['code', 'date'] + list(_BASIS_PRIORITY)
    df = pd.read_csv(PRICE_CACHE, usecols=lambda c: c in usecols)
    df = filter_completed_rows(df, 'date')
    df['date'] = df['date'].astype(str).str.replace('-', '', regex=False)
    df['code'] = df['code'].astype(str).str.strip()
    for col in _BASIS_PRIORITY:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


def load_price() -> PriceBook:
    return PriceBook(load_price_frame(), suspended=load_suspension())


# ─────────────────────────────────────────────────────────────
# 高度序列构建
# ─────────────────────────────────────────────────────────────
def build_height_series(zt: pd.DataFrame, ad_map: dict) -> pd.DataFrame:
    """逐日高度台账: 最高板 / 次高板 / 压力高度 / 涨停跌停结构 / 可用性标记。"""
    zt_only = zt[zt['类型'] == 'ZT']
    dt_only = zt[zt['类型'] == 'DT']
    days = sorted(zt['日期'].unique())

    tiers: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for d, h, c in zip(zt_only['日期'], zt_only['连板数'], zt_only['代码']):
        tiers[d][int(h)].append(str(c))

    zt_cnt = zt_only.groupby('日期').size().to_dict()
    dt_cnt = dt_only.groupby('日期').size().to_dict()

    rows = []
    for d in days:
        heights = sorted(tiers[d].keys(), reverse=True)
        n_zt = int(zt_cnt.get(d, 0))
        rows.append({
            '日期': d,
            'maxh': heights[0] if heights else 0,
            'h2': heights[1] if len(heights) > 1 else None,
            'zt': n_zt,
            'dt': int(dt_cnt.get(d, 0)),
            'b1': len(tiers[d].get(1, [])),
            'b2': len(tiers[d].get(2, [])),
            'lb3plus': sum(len(v) for k, v in tiers[d].items() if k >= 3),
            'ad': ad_map.get(d),
            # 残缺快照判据: 名单没抓全的日子高度是假的, 会凭空造出崩塌/突破
            'usable': n_zt >= MIN_ZT_FOR_USABLE and bool(heights),
        })
    df = pd.DataFrame(rows)
    df['dt_str'] = pd.to_datetime(df['日期'], format='%Y%m%d')
    df['gap_days'] = df['dt_str'].diff().dt.days.fillna(1).astype(int)
    df['孤峰差'] = df['maxh'] - df['h2']
    return df, tiers


def annotate_pressure(hs: pd.DataFrame) -> pd.DataFrame:
    """压力高度 = 前 PRESSURE_WINDOW 个交易日的最高板 (不含当日)。

    ⚠️ 必须"不含当日": 含当日会让"突破"变成恒真的同义反复 (当日就是窗口最大值),
       这是 lianban_analysis 里那份 5 日压力的口径, 只能用来画图不能用来做事件研究。
    """
    hs = hs.copy()
    maxh = hs['maxh'].to_numpy()
    usable = hs['usable'].to_numpy()
    gap = hs['gap_days'].to_numpy()
    n = len(hs)
    pressure = np.full(n, np.nan)
    win_ok = np.zeros(n, dtype=bool)
    for i in range(n):
        lo = i - PRESSURE_WINDOW
        if lo < 0:
            continue
        idx = range(lo, i)
        if not all(usable[j] for j in idx) or not usable[i]:
            continue
        # 窗口内任一相邻交易日跨长假 → 压力(市场记忆)不连续, 剔除
        if any(gap[j] > MAX_CALENDAR_GAP for j in range(lo + 1, i + 1)):
            continue
        pressure[i] = max(maxh[j] for j in idx)
        win_ok[i] = True
    hs['pressure'] = pressure
    hs['pressure_ok'] = win_ok
    hs['压力差'] = hs['maxh'] - hs['pressure']
    return hs


def label_state(row) -> str | None:
    """当日高度相对压力高度的四种状态。"""
    if not row['pressure_ok'] or pd.isna(row['压力差']):
        return None
    d = int(row['压力差'])
    if d > 0:
        return '突破'
    if d == 0:
        return '平压'
    if d == -1:
        return '回落1档'
    return '深度回落'


# ─────────────────────────────────────────────────────────────
# 前向指标
# ─────────────────────────────────────────────────────────────
def forward_maxh_delta(hs: pd.DataFrame, i: int, k: int):
    """未来 k 日内最高板相对当日的最大增量 (窗口内必须全部可用)。"""
    n = len(hs)
    if i + k >= n:
        return None
    seg = hs.iloc[i + 1:i + 1 + k]
    if not seg['usable'].all():
        return None
    return int(seg['maxh'].max()) - int(hs['maxh'].iloc[i])


def summarize(vals) -> dict | None:
    arr = np.asarray([v for v in vals if v is not None and not (isinstance(v, float) and np.isnan(v))],
                     dtype=float)
    if arr.size == 0:
        return None
    return {
        'n': int(arr.size),
        'win': float((arr > 0).mean() * 100),
        'med': float(np.median(arr)),
        'mean': float(arr.mean()),
        'p25': float(np.percentile(arr, 25)),
        'p75': float(np.percentile(arr, 75)),
    }


def _s(stat: dict | None, key: str, fmt: str = '{:+.1f}') -> str:
    if not stat:
        return '—'
    return fmt.format(stat[key])


def band_masks(df: pd.DataFrame, col: str = 'maxh'):
    """按 BREAK_BANDS 切档, 返回 (标签, 掩码) —— 正文/战法/子样本共用同一套边界。"""
    out = []
    for lo, hi, label in BREAK_BANDS:
        m = pd.Series(True, index=df.index)
        if lo is not None:
            m &= df[col] >= lo
        if hi is not None:
            m &= df[col] <= hi
        out.append((label, m))
    return out


def _n_caveat(n: int) -> str:
    """样本量不够就把话说清楚, 不要让读者以为 n=6 的格子是概率。"""
    return '' if n >= MIN_N_FOR_RULE else f' ⚠️ n={n} 不足 {MIN_N_FOR_RULE}, 只是线索'


# ─────────────────────────────────────────────────────────────
# 事件构建
# ─────────────────────────────────────────────────────────────
def build_state_events(hs: pd.DataFrame, tiers, book: PriceBook) -> pd.DataFrame:
    """每个可用交易日一条记录: 状态 + 前向高度增量 + 前向篮子收益。"""
    recs = []
    for i, row in hs.iterrows():
        state = label_state(row)
        if state is None:
            continue
        d = row['日期']
        maxh = int(row['maxh'])
        top_codes = tiers[d].get(maxh, [])
        zt_codes = [c for lst in tiers[d].values() for c in lst]
        # 首板池: 战法 C / A 的操作建议是"买新首板池", 那就必须真的把它测出来。
        # 上一版这两条战法的动作是一句没有数字支撑的话 —— 结论指向一个从未度量的篮子。
        b1_codes = tiers[d].get(1, [])
        rec = {
            '日期': d, 'i': i, 'state': state, 'maxh': maxh,
            'pressure': int(row['pressure']), '压力差': int(row['压力差']),
            'zt': int(row['zt']), 'dt': int(row['dt']), 'b1': int(row['b1']),
            'ad': row['ad'], '孤峰差': row['孤峰差'],
            # 最高板那一档有几只 —— 高位时几乎恒为 1, 单股中位数不能当概率讲
            'n_top': len(top_codes),
            # 存下代码本身: 用来算"这些事件其实只来自几只票"。同一只妖股连续 6 天
            # 都是最高板, 就贡献 6 个事件 —— n 看着够, 独立标的只有一个。
            'top_codes': tuple(top_codes),
        }
        for k in FWD_WINDOWS:
            rec[f'dmax{k}'] = forward_maxh_delta(hs, i, k)
            # ① 事件日收盘口径 = "已经在手" 的持仓收益。⚠️ 不是买点收益:
            #    这些股票当日全在涨停板上, 收盘价根本买不到 (封住的板挂单排不上)。
            j = i + k
            if j < len(hs):
                d1 = hs['日期'].iloc[j]
                top_r = book.cohort(top_codes, d, d1)
                zt_r = book.cohort(zt_codes, d, d1)
                mkt_r = book.returns(d, d1).dropna().to_numpy()
                rec[f'top{k}'] = float(np.median(top_r)) if top_r.size else None
                rec[f'zt{k}'] = float(np.median(zt_r)) if zt_r.size else None
                rec[f'mkt{k}'] = float(np.median(mkt_r)) if mkt_r.size else None
            else:
                rec[f'top{k}'] = rec[f'zt{k}'] = rec[f'mkt{k}'] = None
            # ② 次日收盘口径 = 可成交的建仓收益 (信号当晚看到, 第二天收盘买)
            j2 = i + 1 + k
            if i + 1 < len(hs) and j2 < len(hs):
                d_in, d_out = hs['日期'].iloc[i + 1], hs['日期'].iloc[j2]
                topn = book.cohort(top_codes, d_in, d_out)
                ztn = book.cohort(zt_codes, d_in, d_out)
                b1n = book.cohort(b1_codes, d_in, d_out)
                mktn = book.returns(d_in, d_out).dropna().to_numpy()
                rec[f'topn{k}'] = float(np.median(topn)) if topn.size else None
                rec[f'ztn{k}'] = float(np.median(ztn)) if ztn.size else None
                rec[f'ztn{k}_win'] = float((ztn > 0).mean() * 100) if ztn.size else None
                rec[f'b1n{k}'] = float(np.median(b1n)) if b1n.size else None
                rec[f'b1n{k}_win'] = float((b1n > 0).mean() * 100) if b1n.size else None
                rec[f'mktn{k}'] = float(np.median(mktn)) if mktn.size else None
            else:
                rec[f'topn{k}'] = rec[f'ztn{k}'] = None
                rec[f'ztn{k}_win'] = rec[f'mktn{k}'] = None
                rec[f'b1n{k}'] = rec[f'b1n{k}_win'] = None
        recs.append(rec)
    return pd.DataFrame(recs)


def build_break_events(hs: pd.DataFrame, tiers, book: PriceBook) -> pd.DataFrame:
    """断板事件 + 次高板接力观测。

    断板定义: 前一日最高板 H 的所有个股, 当日无人走到 H+1。
    次高板 (S): 前一日第二高的连板档位上的全部个股。
    两种介入方式分开算 —— 持有型 (断板前一日收盘就在手) / 介入型 (断板日收盘才买)。
    """
    recs = []
    stock_h: dict[str, dict[str, int]] = defaultdict(dict)
    for d, hmap in tiers.items():
        for h, codes in hmap.items():
            for c in codes:
                stock_h[c][d] = h

    for i in range(1, len(hs)):
        prev, cur = hs.iloc[i - 1], hs.iloc[i]
        if not (prev['usable'] and cur['usable']):
            continue
        H = int(prev['maxh'])
        if H < 3:                      # 高度太低, "断板"没有龙头含义
            continue
        d_prev, d = prev['日期'], cur['日期']
        leaders = tiers[d_prev].get(H, [])
        if not leaders:
            continue
        advanced = [c for c in leaders if stock_h.get(c, {}).get(d, 0) > H]
        h2 = prev['h2']
        if h2 is None or pd.isna(h2):
            continue
        h2 = int(h2)
        s_codes = tiers[d_prev].get(h2, [])
        relay = [c for c in s_codes if stock_h.get(c, {}).get(d, 0) > h2]
        # ⚠️ 介入型 (断板日收盘买) 必须把当日**自己也涨停**的次高板剔掉。
        #    连板加一档的前提就是当日封住 —— relay 那批票的当日收盘价买不到。
        #    5-6 板档接力率中位 50%, 也就是说不剔的话半个篮子是不可成交价, 而且
        #    恰好是涨得最好的那半个。这与第二节事件日口径的坑是同一个坑。
        _relay_set = set(relay)
        s_buyable = [c for c in s_codes if c not in _relay_set]

        rec = {
            '日期': d, 'i': i, 'broke': not advanced, 'H': H, 'h2': h2,
            '档差': H - h2, 'n_leader': len(leaders), 'n_s': len(s_codes),
            'n_s_buy': len(s_buyable),
            '接力率': (len(relay) / len(s_codes) * 100) if s_codes else None,
            'maxh_new': int(cur['maxh']), 'ad': cur['ad'],
            'zt': int(cur['zt']), 'dt': int(cur['dt']),
        }
        # 高度修复: 断板日**之后** 5 个交易日内最高板是否重回 H
        # ⚠️ 必须从 i+1 起。含断板日会让判据部分自证: 3-4 板断板里有 18/26 天
        #    当日 maxh 本身就 ≥H (下面还有别的股在同档), "修复率 100%" 是这么来的。
        seg = hs.iloc[i + 1:i + 6]
        rec['修复'] = (bool((seg['usable'] & (seg['maxh'] >= H)).any())
                       if len(seg) == 5 else None)   # 右截断的事件不算, 否则窗口不等长

        for k in FWD_WINDOWS:
            # 持有型: 断板前一日收盘 → 断板后第 k 日收盘
            j = i - 1 + k
            if j < len(hs):
                hold = book.cohort(s_codes, d_prev, hs['日期'].iloc[j])
                rec[f'hold{k}'] = float(np.median(hold)) if hold.size else None
                rec[f'hold{k}_win'] = float((hold > 0).mean() * 100) if hold.size else None
            else:
                rec[f'hold{k}'] = rec[f'hold{k}_win'] = None
            # 介入型: 断板日收盘 → 之后第 k 日收盘
            j2 = i + k
            if j2 < len(hs):
                enter = book.cohort(s_codes, d, hs['日期'].iloc[j2])
                lead_r = book.cohort(leaders, d, hs['日期'].iloc[j2])
                rec[f'enter{k}'] = float(np.median(enter)) if enter.size else None
                rec[f'enter{k}_win'] = float((enter > 0).mean() * 100) if enter.size else None
                rec[f'lead{k}'] = float(np.median(lead_r)) if lead_r.size else None
                # 可成交口径: 剔掉当日涨停 (接力成功) 的那部分次高板
                entb = book.cohort(s_buyable, d, hs['日期'].iloc[j2])
                rec[f'enterb{k}'] = float(np.median(entb)) if entb.size else None
                rec[f'enterb{k}_win'] = float((entb > 0).mean() * 100) if entb.size else None
            else:
                rec[f'enter{k}'] = rec[f'enter{k}_win'] = rec[f'lead{k}'] = None
                rec[f'enterb{k}'] = rec[f'enterb{k}_win'] = None
        recs.append(rec)
    return pd.DataFrame(recs)


def build_cycles(hs: pd.DataFrame) -> pd.DataFrame:
    """周期 = maxh > CYCLE_FLOOR 的连续段。

    ⚠️ 两处必须切断, 不切会造出假周期:
      - **长假**: 相邻交易日自然日跨度 >MAX_CALENDAR_GAP 就切。不切会把
        20251106→20260130 粘成一个 60 天段, 峰值落在第 55 天 —— 一个段就足以
        把整份周期几何拖歪 (原版就是这么算出"峰值在第 2 天"的)。
      - **残缺快照日**: 名单没抓全的日子高度是假的, 直接跨过去等于假装那天连续。

    len==1 的段不是周期, 是单日脉冲 (高度冒一天头就掉回 ≤3 板)。两者混在一起算
    中位数, 会得到"见峰后还剩 0 天"这种由脉冲主导的结论, 所以这里标出 len,
    几何统计只在 len>=MIN_CYCLE_LEN 的段上做。
    """
    segs: list[dict] = []
    cur: dict | None = None

    def close(reason: str):
        nonlocal cur
        if cur is not None:
            cur['end_reason'] = reason
            segs.append(cur)
            cur = None

    for i, row in hs.iterrows():
        if not row['usable']:
            close('数据缺口')            # 快照残缺, 不假装连续
            continue
        if cur is not None and int(row['gap_days']) > MAX_CALENDAR_GAP:
            close('跨长假')              # 情绪已重置, 长假两侧不是同一个周期
        if row['maxh'] > CYCLE_FLOOR:
            if cur is None:
                cur = {'start': row['日期'], 'heights': [], 'dates': []}
            cur['heights'].append(int(row['maxh']))
            cur['dates'].append(row['日期'])
        else:
            close('高度回到周期外')
    close('样本右截断')                   # 最后一段还没走完, 长度是下限不是真值

    out = []
    for c in segs:
        hh = c['heights']
        peak = max(hh)
        pk_idx = hh.index(peak)
        out.append({
            'start': c['start'], 'end': c['dates'][-1], 'len': len(hh),
            'peak': peak, 'peak_day': pk_idx + 1, 'peak_date': c['dates'][pk_idx],
            'after_peak': len(hh) - pk_idx - 1,
            'end_reason': c['end_reason'],
            # 自然结束 = 高度自己掉下去。切在长假/缺口/样本末尾的段, 长度和
            # after_peak 都只是下限, 不能进几何中位数
            'natural_end': c['end_reason'] == '高度回到周期外',
        })
    return pd.DataFrame(out)


# ─────────────────────────────────────────────────────────────
# 报告章节
# ─────────────────────────────────────────────────────────────
def sec_data(hs: pd.DataFrame, dropped: pd.DataFrame, cy: pd.DataFrame,
             frozen: tuple[float, int, list[tuple[str, int]]] | None = None,
             n_masked: int = 0,
             frozen_raw: tuple[float, int, list[tuple[str, int]]] | None = None) -> list[str]:
    L = ['## 〇、这份数据能说什么、不能说什么', '']
    ok = hs[hs['usable']]
    L.append(f'- 样本: **{len(ok)} 个可用交易日** ({ok["日期"].iloc[0]} → {ok["日期"].iloc[-1]}), '
             f'涨停记录 {int(ok["zt"].sum())} 条')
    if not dropped.empty:
        detail = ', '.join(f'{r["日期"]}(涨停{int(r["zt"])}只/最高{int(r["maxh"])}板)'
                           for _, r in dropped.iterrows())
        L.append(f'- 剔除 **{len(dropped)} 个残缺快照日**: {detail} —— 这些日子不是真没高度, '
                 f'是当天名单没抓全, 留着会凭空造出"崩塌"和"突破"')
    npress = int(hs['pressure_ok'].sum())
    L.append(f'- 压力高度可算的日子 **{npress} 天** (需要前 5 个交易日全部可用且不跨长假; '
             f'跨春节/元旦/五一的窗口一律剔除, 因为"压力"是市场记忆, 隔 11 天记忆已经断了)')
    L.append('- 收益一律**逐股票挑两天共有的价格口径**算。缓存里 raw/qfq/legacy 三种复权口径按日成片, '
             '2026-03-26 是分界, 跨口径相减会得到 1% 量级的假涨跌')
    if frozen is not None:
        share, total_sd, top = frozen
        who = ', '.join(f'{c} {n}天' for c, n in top) if top else '—'
        if n_masked:
            # 掩码生效时, 这段讲的是"修了多少、还剩多少", 三个数全部现算:
            # 剔掉的股票日数、剔之前的假平盘占比、剔之后的残余占比。
            # ⚠️ 别把任何一个写成常量。上一版把 2.17% 写进散文里, 而这个数是随样本
            # 区间变的 —— 换了窗口散文就开始说谎, 且没有任何判据能发现。
            #
            # ⚠️ 结论 (✅/⚠️) 也不能由"掩码跑没跑过"决定, 必须由**残余占比**决定。
            # 上一版写成 if n_masked 就报 ✅ 已剔除, 结果只掩掉 1 个股票日时报告
            # 一边印"从 2.17% 降到 2.17%"、一边说"量级已回到正常区间" —— 自己的
            # 数字打自己的脸, 正是这套报告要防的散文漂移。
            was = f'{frozen_raw[0]:.2f}%' if frozen_raw else '—'
            drop = (frozen_raw[0] - share) if frozen_raw else float('nan')
            fixed = share <= FROZEN_NORM_PCT
            head = ('✅ **假平盘已剔除**' if fixed else
                    '⚠️ **假平盘只剔掉一部分**')
            L.append(f'- {head}: 用 baostock 的 `tradestatus=0` 标出停牌日, '
                     f'把这些格子在价格表上打成 NaN, 共剔掉 **{n_masked} 个股票日**。'
                     f'相邻两天收益恰好 0.00% 的占比从 **{was}** 降到 **{share:.2f}%** '
                     f'({total_sd} 个股票日, 正常应在 {FROZEN_NORM_PCT}% 以下)。'
                     '停牌日的收盘价是"冻结价": 那天没有交易, 既买不进也卖不出, '
                     '缓存却照抄了停牌前的收盘, 于是混成一批假"平盘" —— '
                     '它既不算涨也不算跌, 会把中位数往 0 拽、把红盘率往下压。'
                     '现在跨停牌日的收益一律判无覆盖, 不再进任何队列指标。')
            if fixed:
                if top:
                    L.append(f'  - 残余最集中的几只: {who}。这部分是**真**平盘 '
                             '(收盘价确实没动) 加上 baostock 也没标出的停牌')
            else:
                # 没修干净就得说清"还剩多少、偏向哪边", 别让读者以为已经干净了。
                L.append(f'  - **残余 {share:.2f}% 仍高于 {FROZEN_NORM_PCT}%**, '
                         f'这轮只降了 {drop:.2f}pp。最集中的几只: {who} —— '
                         '说明停牌标记覆盖不全 (停牌表只盖到已抓到 K 线的那批股票, '
                         '未抓全的部分没有标记可用)。'
                         f'下面凡是"小赚"的格子真实幅度可能比表里更大, "小亏"的更深; '
                         '把 tools/backfill_baostock_bars.py --apply 跑全再看这个数')
        else:
            L.append(f'- ⚠️ **已知未修的脏数据**: 相邻两天收益恰好 0.00% 的股票日占 **{share:.2f}%** '
                     f'({total_sd} 个股票日), 正常应在 0.5% 以下。最集中的几只: {who} —— '
                     '几乎可以肯定是停牌期间把前一天收盘价重复写进了缓存, 造出一批假"平盘"。'
                     '影响方向是**把所有队列指标往 0 拽**: 中位数被稀释、红盘率被压低。'
                     '本轮没有剔除这些日子, 因为拿不到停牌标记 '
                     '(跑 tools/backfill_baostock_bars.py --apply 生成) —— 记在这里, 别当它不存在。')
    L.append('')
    ncy = int((cy['len'] >= MIN_CYCLE_LEN).sum()) if not cy.empty else 0
    nnat = int((cy['natural_end'] & (cy['len'] >= MIN_CYCLE_LEN)).sum()) if not cy.empty else 0
    months = len(ok) / 21.0
    L.append(f'**不能说的**: 只有一个完整样本区间 (约 {months:.0f} 个月), '
             f'其中够长的高度周期 **{ncy} 个**, 自然走完 (不是被长假或样本末尾切断) 的只有 '
             f'**{nnat} 个**。周期级结论的有效样本就是这个数, '
             f'下面凡是 n<{MIN_N_FOR_RULE} 的格子都只当线索, 不当概率。')
    L.append('')
    return L


def sec_terrain(hs: pd.DataFrame) -> list[str]:
    ok = hs[hs['usable']]
    L = ['## 一、市场高度长什么样 (先认地形)', '']
    dist = ok['maxh'].value_counts().sort_index()
    total = len(ok)
    L.append('### 1. 最高板的高度分布')
    L.append('')
    L.append('| 当日最高板 | 天数 | 占比 | 累计占比(≥该高度) |')
    L.append('|---|---|---|---|')
    cum = 0
    for h in sorted(dist.index, reverse=True):
        cum += int(dist[h])
        L.append(f'| {int(h)}板 | {int(dist[h])} | {dist[h]/total*100:.0f}% | {cum/total*100:.0f}% |')
    L.append('')
    med = int(ok['maxh'].median())
    p_ge5 = (ok['maxh'] >= 5).mean() * 100
    p_ge7 = (ok['maxh'] >= 7).mean() * 100
    p_ge10 = (ok['maxh'] >= 10).mean() * 100
    L.append(f'**中位 {med} 板 · {p_ge5:.0f}% 的日子有 5 板 · {p_ge7:.0f}% 有 7 板 · {p_ge10:.0f}% 有 10 板以上**')
    L.append('')
    L.append('**怎么操作**: 把 5 板当"正常年景的天花板", 7 板以上是少数日子才有的行情。'
             '手里股票走到 5 板以上, 就不再是"还能涨多少"的问题, 是"什么时候走"的问题。')
    L.append('')

    L.append('### 2. 高度分区: 三个区各是什么状态')
    L.append('')
    L.append('| 高度区 | 天数 | 占比 | 当日涨停中位 | 跌停中位 | 首板中位 | 红盘比中位 |')
    L.append('|---|---|---|---|---|---|---|')
    zones = [('低位区 ≤4板', ok['maxh'] <= 4),
             ('中位区 5-6板', (ok['maxh'] >= 5) & (ok['maxh'] <= 6)),
             ('高位区 ≥7板', ok['maxh'] >= 7)]
    for name, mask in zones:
        sub = ok[mask]
        if sub.empty:
            continue
        ad = sub['ad'].dropna()
        L.append(f'| {name} | {len(sub)} | {len(sub)/total*100:.0f}% | {int(sub["zt"].median())} | '
                 f'{int(sub["dt"].median())} | {int(sub["b1"].median())} | '
                 f'{ad.median():.0%} |' if not ad.empty else
                 f'| {name} | {len(sub)} | {len(sub)/total*100:.0f}% | {int(sub["zt"].median())} | '
                 f'{int(sub["dt"].median())} | {int(sub["b1"].median())} | — |')
    L.append('')
    return L


def sec_relation(hs: pd.DataFrame, ev: pd.DataFrame) -> list[str]:
    L = ['## 二、高度和压力高度的关系 (四种状态, 后面走势完全不同)', '']
    L.append(f'压力高度 = 前 {PRESSURE_WINDOW} 个交易日的最高板 (**不含当日**)。'
             '不含当日这一点是判据要害: 含当日的话"突破"就是恒真的同义反复, '
             '既有看板里那条 5 日压力线只能画图, 不能拿来做事件研究。')
    L.append('')
    order = ['突破', '平压', '回落1档', '深度回落']
    L.append('### 1. 四种状态的出现频率与后续')
    L.append('')
    L.append('| 状态 | 天数 | 占比 | 次日最高板变化(中位) | 5日内最高板增量(中位) | 5日内创新高% | 次日全市场中位 |')
    L.append('|---|---|---|---|---|---|---|')
    n_all = len(ev)
    for st in order:
        sub = ev[ev['state'] == st]
        if sub.empty:
            continue
        nxt = summarize([next_delta for next_delta in sub['dmax1']])
        d5 = summarize(sub['dmax5'])
        newhigh = sub['dmax5'].dropna()
        nh = (newhigh > 0).mean() * 100 if len(newhigh) else float('nan')
        mkt1 = summarize(sub['mkt1'])
        L.append(f'| {st} | {len(sub)} | {len(sub)/n_all*100:.0f}% | {_s(nxt, "med")} | '
                 f'{_s(d5, "med")} | {nh:.0f}% | {_s(mkt1, "med", "{:+.2f}%")} |')
    L.append('')
    L.append('**读法**: `dmax5` 是"未来 5 天最高板能比今天高多少", 它衡量的是**行情高度还有没有增量**, '
             '不是个股赚不赚钱 —— 两件事在下面第三、四节会分开算。')
    L.append('')

    # 首板池是战法 A/C 的落点, 必须真的量出来。否则"买新首板池"只是一句顺口的话:
    # 结论指向一个报告里从未出现过的篮子, 读者无从判断它到底比涨停池好在哪。
    L.append('### 1b. 四种状态下的首板池 (战法真正的下单落点)')
    L.append('')
    L.append('全部**次日收盘建仓**口径 —— 首板池是唯一次日还能大量买到的篮子 '
             '(高位连板股次日往往一字或高开锁死, 首板股次日普遍有量有价)。')
    L.append('')
    # ⚠️ 必须带全市场那一列。四个状态的首板池全是负的, 但如果同期全市场也是负的,
    #    那说的是"这半年 3 日持有本来就亏钱"(beta), 不是"这个信号没用"(alpha)。
    #    只报绝对值会把市场环境错当成策略失效。
    L.append('| 状态 | n | 首板池只数(中位) | 首板池 +3日中位 | 个股赚钱率 | 涨停池 +3日中位 | 全市场 +3日中位 | 首板−全市场 |')
    L.append('|---|---|---|---|---|---|---|---|')
    for st in order:
        sub = ev[ev['state'] == st]
        if sub.empty:
            continue
        b3 = sub['b1n3'].dropna()
        z3 = sub['ztn3'].dropna()
        m3 = sub['mktn3'].dropna()
        w3 = sub['b1n3_win'].dropna()
        if b3.empty:
            continue
        exc = (b3.median() - m3.median()) if len(m3) else float('nan')
        L.append(f'| {st} | {len(b3)} | {int(sub["b1"].median())} | {b3.median():+.2f}% | '
                 f'{f"{w3.mean():.0f}%" if len(w3) else "—"} | '
                 f'{f"{z3.median():+.2f}%" if len(z3) else "—"} | '
                 f'{f"{m3.median():+.2f}%" if len(m3) else "—"} | '
                 f'{f"{exc:+.2f}pp" if pd.notna(exc) else "—"} |')
    L.append('')
    _dp = ev[ev['state'] == '深度回落']
    _dp_b3 = _dp['b1n3'].dropna()
    _dp_z3 = _dp['ztn3'].dropna()
    _dp_m3 = _dp['mktn3'].dropna()
    if len(_dp_b3) and len(_dp_z3) and len(_dp_m3):
        _vs_zt = _dp_b3.median() - _dp_z3.median()
        _vs_mkt = _dp_b3.median() - _dp_m3.median()
        L.append(f'**怎么操作**: 深度回落那一行是战法 C 的落点。首板池 +3日 '
                 f'{_dp_b3.median():+.2f}%, 涨停池 {_dp_z3.median():+.2f}%, '
                 f'同期全市场 {_dp_m3.median():+.2f}%。')
        # 两个比较分开讲: 跟涨停池比是"选哪个篮子", 跟全市场比是"这事值不值得做"
        L.append('')
        L.append(f'- **首板 vs 涨停池 {_vs_zt:+.2f}pp** —— '
                 + ('首板池更优, "买新首板不买残余高位股"这句话有数字支撑。'
                    if _vs_zt > 0 else
                    '**首板池并没有更优**, 换篮子解决不了问题。'))
        L.append(f'- **首板 vs 全市场 {_vs_mkt:+.2f}pp** —— '
                 + (f'扣掉市场环境后仍有超额, 亏的那 {abs(_dp_b3.median()):.2f}% 主要是 beta '
                    '(这半年 3 日持有本来就是负的), 信号本身没坏。'
                    if _vs_mkt > 0 else
                    '扣掉市场环境后**依然没有超额** —— 不是市场的锅, 这个位置买入本身就没优势。'))
        L.append('')
        L.append('把这两行连起来读: 深度回落这个状态**对高度的预测是真的** (5日内抬升 91%), '
                 '但它**不构成一个买点**。高度回来靠的是新的资金和新的题材, 而不是'
                 '"在低点买一篮子涨停股等它抬升"。战法 C 的正确用法是**别在这里减仓/别追空**, '
                 '不是"在这里加仓"。')
        L.append('')

    L.append('### 2. 同一状态下, 买最高板股 vs 买全部涨停股')
    L.append('')
    L.append('⚠️ **这张表是事件日收盘口径, 不能当策略读。** 事件日那批票当天都在涨停板上, '
             '收盘价你买不到。它只用来做**相对比较** (最高板 vs 涨停池 vs 全市场 谁强), '
             '绝对数字一律偏高。能下单的口径在第三节第 2 表和第六节战法里, 全部是"次日收盘建仓"。')
    L.append('')
    L.append('| 状态 | n | 买最高板股 +3日中位 | 买涨停股 +3日中位 | 全市场 +3日中位 | 涨停股跑赢市场% |')
    L.append('|---|---|---|---|---|---|')
    for st in order:
        sub = ev[ev['state'] == st]
        if sub.empty:
            continue
        top3 = summarize(sub['top3'])
        zt3 = summarize(sub['zt3'])
        mkt3 = summarize(sub['mkt3'])
        pair = sub[['zt3', 'mkt3']].dropna()
        beat = ((pair['zt3'] > pair['mkt3']).mean() * 100) if len(pair) else float('nan')
        L.append(f'| {st} | {len(sub)} | {_s(top3, "med", "{:+.2f}%")} | {_s(zt3, "med", "{:+.2f}%")} | '
                 f'{_s(mkt3, "med", "{:+.2f}%")} | {beat:.0f}% |')
    L.append('')
    return L


def sec_breakout(ev: pd.DataFrame) -> list[str]:
    L = ['## 三、突破压力高度之后 (按落点分层, 这是全篇最重要的一张表)', '']
    br = ev[ev['state'] == '突破'].copy()
    L.append(f'突破日共 **{len(br)} 个**。突破本身不是买卖信号, **落在几板才是** —— '
             '同一个"突破"动作, 在 4 板和 8 板是两件相反的事。')
    L.append('')

    bands = band_masks(br)
    L.append('### 1. 突破落点决定后续')
    L.append('')
    L.append('| 突破落点 | n | 次日最高板变化 | 5日内再创新高% | 10日内最高板增量 | 次日守住高度% |')
    L.append('|---|---|---|---|---|---|')
    for name, mask in bands:
        sub = br[mask]
        if sub.empty:
            continue
        d1 = summarize(sub['dmax1'])
        d5 = sub['dmax5'].dropna()
        d10 = summarize(sub['dmax10'])
        hold = sub['dmax1'].dropna()
        L.append(f'| {name} | {len(sub)} | {_s(d1, "med")} | '
                 f'{(d5 > 0).mean()*100:.0f}% | {_s(d10, "med")} | '
                 f'{(hold >= 0).mean()*100:.0f}% |')
    L.append('')

    L.append('### 2. 突破日买入的实际盈亏 (两种口径分开看)')
    L.append('')
    L.append('**口径一 · 涨停池等权** —— 这是唯一能当策略读的一列: 篮子里有几十只票, '
             '次日收盘建仓是真能成交的价格。')
    L.append('')
    L.append('| 突破落点 | n | 次日建仓 +1日 | +3日 | +5日 | +3日个股赚钱率 | 同期全市场 +3日 |')
    L.append('|---|---|---|---|---|---|---|')
    for name, mask in bands:
        sub = br[mask]
        if sub.empty:
            continue
        n1, n3, n5 = (summarize(sub[f'ztn{k}']) for k in (1, 3, 5))
        w3 = sub['ztn3_win'].dropna()
        m3 = summarize(sub['mktn3'])
        L.append(f'| {name} | {len(sub)} | {_s(n1, "med", "{:+.2f}%")} | {_s(n3, "med", "{:+.2f}%")} | '
                 f'{_s(n5, "med", "{:+.2f}%")} | '
                 f'{f"{w3.mean():.0f}%" if len(w3) else "—"} | {_s(m3, "med", "{:+.2f}%")} |')
    L.append('')
    n_top_med = br['n_top'].median()
    L.append(f'**口径二 · 最高板那一档** —— 只能当"手里已经拿着"的持仓路径读, '
             f'不能当策略: 最高板档位每天只有 **{n_top_med:.0f} 只**股票 (中位), '
             '而且当天全在涨停板上, 收盘价买不到。'
             '下面的数字是同一只票在重叠窗口里被反复计数的结果, n 不是独立样本数。')
    L.append('')
    L.append('> 读表提示: "独立标的"可以**大于** n(事件) —— 低位那一档最高板常有好几只票'
             '并列, 一个事件日就贡献多只; 而高位那一档几乎恒为 1 只, 独立标的必然远小于 n。'
             '两列的比值才是要看的东西: 越接近 1 越像独立样本, 越小越是同一只票的连板路径。')
    L.append('')
    L.append('| 突破落点 | n(事件) | 独立标的 | 前3只占比 | 事件日收盘持有 +1日 | +3日 | 次日收盘才买 +3日 |')
    L.append('|---|---|---|---|---|---|---|')
    conc: dict[str, tuple[int, int, float]] = {}
    for name, mask in bands:
        sub = br[mask]
        if sub.empty:
            continue
        t1, t3 = (summarize(sub[f'top{k}']) for k in (1, 3))
        tn3 = summarize(sub['topn3'])
        flat = [c for tup in sub['top_codes'] for c in tup]
        vc = pd.Series(flat).value_counts()
        n_uni = len(vc)
        top3_share = vc.head(3).sum() / len(flat) * 100 if flat else float('nan')
        conc[name] = (len(sub), n_uni, top3_share)
        L.append(f'| {name} | {len(sub)} | {n_uni} 只 | {top3_share:.0f}% | '
                 f'{_s(t1, "med", "{:+.2f}%")} | {_s(t3, "med", "{:+.2f}%")} | '
                 f'{_s(tn3, "med", "{:+.2f}%")} |')
    L.append('')
    # 集中度这段话必须由数字生成 —— 写死"26 个事件 10 只票"下个月重跑就会对不上
    hi_name = next((lbl for lo, hi, lbl in BREAK_BANDS if lo is not None and lo >= 7), None)
    if hi_name in conc:
        n_ev, n_uni, share = conc[hi_name]
        L.append(f'**"独立标的"这一列是这张表真正的信息量**: 高位突破那 {n_ev} 个事件'
                 f'只来自 **{n_uni} 只票**, 其中 3 只连续霸榜最高板的票就贡献了 **{share:.0f}%**。'
                 f'所谓"高位突破后龙头继续 +10%", 说的其实是**几只票的几段连板路径**, '
                 f'不是 {n_ev} 次独立验证 —— 换掉这几只, 整档结论就没了。')
        L.append('')
    L.append('**怎么操作**: 看盘只看第一张表。第二张表存在的意义是提醒你 —— '
             '"龙头突破后大涨"这种印象来自极少数个股的路径, 不是一个你能重复执行的动作。'
             '连板路径的 +10% 是**已经在车上**的人的收益, 且它锁死在涨停板上 —— '
             '你看到突破时, 那个价格已经不接受新的买单了。')
    L.append('')

    L.append('### 3. 突破幅度没有信息 (结构决定的, 不是样本不够)')
    L.append('')
    diff_dist = br['压力差'].value_counts().sort_index()
    dist_txt = ', '.join(f'+{int(k)}档 {int(v)} 次' for k, v in diff_dist.items())
    L.append(f'{len(br)} 个突破日的幅度分布: **{dist_txt}**。')
    L.append('')
    L.append('这不是巧合, 是**机械恒等式**: 个股一天最多加一板, 所以 '
             '`maxh(t) ≤ maxh(t-1)+1`; 压力高度取前 5 日最高板, 至少等于 `maxh(t-1)`。'
             '两式相减, 突破幅度只可能是 +1 档。')
    L.append('')
    L.append('**怎么操作**: 别去找"强突破/弱突破"这种分类, 它在连板高度上不存在。'
             '突破的强弱信息全部落在**落点几板**和**市场配合**上 —— 就是上面第 1 张表和下面第 4 张表。')
    L.append('')

    L.append('### 4. 突破日的市场配合 (同样的突破, 配合不同结果不同)')
    L.append('')
    L.append('收益列统一用**次日收盘建仓**口径 (能成交的那个), 与第 2 表和第六节战法对齐; '
             '"次日全市场"是从突破日收盘算到次日收盘的全市场中位, 指数口径不受涨停板限制。')
    L.append('')
    L.append('| 突破日条件 | n | 5日内再创新高% | 次日建仓涨停池 +3日中位 | 次日全市场中位 |')
    L.append('|---|---|---|---|---|')
    conds = [
        ('全部突破日 (基准)', pd.Series(True, index=br.index)),
        ('红盘比 ≥55% (市场配合)', br['ad'] >= 0.55),
        ('红盘比 <45% (只有高度没有面)', br['ad'] < 0.45),
        ('跌停 ≤10 只', br['dt'] <= 10),
        ('跌停 >20 只 (分化型突破)', br['dt'] > 20),
        ('孤峰 (比次高多≥2档)', br['孤峰差'] >= 2),
        ('阶梯连续 (比次高多1档)', br['孤峰差'] <= 1),
    ]
    for label, mask in conds:
        sub = br[mask.fillna(False)]
        if len(sub) < 3:
            continue
        d5 = sub['dmax5'].dropna()
        # ⚠️ 必须用 ztn3/mktn1 (次日收盘建仓) 而不是 zt3/mkt1 (事件日收盘)。
        #    事件日那批票全在板上, 收盘价买不到, 用它算出来的优势是不可成交的。
        L.append(f'| {label} | {len(sub)} | {(d5 > 0).mean()*100:.0f}% | '
                 f'{_s(summarize(sub["ztn3"]), "med", "{:+.2f}%")} | '
                 f'{_s(summarize(sub["mktn1"]), "med", "{:+.2f}%")} |')
    L.append('')
    return L


def sec_break_board(bk: pd.DataFrame) -> list[str]:
    L = ['## 四、断板之后, 次高板能不能接棒', '']
    if bk.empty:
        return L + ['样本不足。', '']
    broke = bk[bk['broke']]
    kept = bk[~bk['broke']]
    L.append(f'样本: 前一日最高板 ≥3 板的日子共 **{len(bk)} 个**, 其中 **{len(broke)} 个断板** '
             f'(占 {len(broke)/len(bk)*100:.0f}%), {len(kept)} 个龙头顺利晋级。')
    L.append('断板 = 前一日最高板那一档的**全部个股**当日无人走到更高一板。'
             '次高板 = 前一日第二高的那一档上的全部个股。')
    L.append('')
    L.append('两个口径先说清楚, 不然会读反:')
    L.append('')
    L.append('- **晋级率** = 次高板那一档里, 当日走到"更高一板"的比例。'
             '这不是封板率 —— 封板率算的是当日涨停占比, 一只 4 板股今天涨停就算封住; '
             '晋级率要求它真的从 4 板走到 5 板。同一批票晋级率总是低于封板率。')
    L.append(f'- **高度修复** = 断板日**之后** 5 个交易日内, 最高板重新回到 H。'
             '窗口从次日起算 —— 含断板日的话判据会部分自证 (断板日同档往往还有别的票, '
             'maxh 当天就 ≥H), 3-4 板那一档的修复率会虚高到 100%。窗口不满 5 天的事件不计入。')
    L.append('')

    L.append('### 1. 接棒的基础概率')
    L.append('')
    L.append('| 情形 | n | 次高板当日晋级率(中位) | 高度5日内修复% | 当日最高板中位 |')
    L.append('|---|---|---|---|---|')
    for label, sub in [('断板日', broke), ('龙头晋级日 (基准)', kept)]:
        if sub.empty:
            continue
        relay = sub['接力率'].dropna()
        rep = sub['修复'].dropna()
        L.append(f'| {label} | {len(sub)} | {relay.median():.0f}% | '
                 f'{rep.mean()*100:.0f}% | {sub["maxh_new"].median():.0f}板 |')
    L.append('')

    L.append('### 2. 次高板篮子的实际盈亏 —— 两种介入方式')
    L.append('')
    L.append('| 介入方式 | 情形 | n | +1日中位 | +3日中位 | +5日中位 | +3日个股赚钱率 |')
    L.append('|---|---|---|---|---|---|---|')
    for kind, pre in [('持有型 (断板前一日收盘就在手)', 'hold'), ('介入型 (断板日收盘才买)', 'enter')]:
        for label, sub in [('断板', broke), ('未断板(基准)', kept)]:
            if sub.empty:
                continue
            r1, r3, r5 = (summarize(sub[f'{pre}{k}']) for k in (1, 3, 5))
            w3 = sub[f'{pre}3_win'].dropna()
            L.append(f'| {kind} | {label} | {len(sub)} | {_s(r1, "med", "{:+.2f}%")} | '
                     f'{_s(r3, "med", "{:+.2f}%")} | {_s(r5, "med", "{:+.2f}%")} | '
                     f'{w3.mean():.0f}% |' if len(w3) else
                     f'| {kind} | {label} | {len(sub)} | {_s(r1, "med", "{:+.2f}%")} | '
                     f'{_s(r3, "med", "{:+.2f}%")} | {_s(r5, "med", "{:+.2f}%")} | — |')
    L.append('')

    L.append('### 3. 按断板高度分层 (从几板掉下来, 决定次高板值不值得接)')
    L.append('')
    # ⚠️ "全篮"那一列含当日接力成功 (= 当日涨停) 的次高板, 它们的当日收盘价买不到。
    #    5-6 板档接力率中位 50%, 不剔的话半个篮子是不可成交价, 且恰好是涨最好的半个。
    #    战法只许引用 **可成交篮** 那一列。两列并排放, 是为了让这个差额自己现形。
    L.append('| 断板高度 H | n | 次高板晋级率 | 全篮介入+3日 (含涨停·买不到) | '
             '**可成交篮**介入+3日 | 可成交只数(中位) | 原龙头介入+3日 | 高度修复% |')
    L.append('|---|---|---|---|---|---|---|---|')
    for lo, hi, label in BOARD_BREAK_BANDS:
        sub = broke[(broke['H'] >= lo) & (broke['H'] <= hi)]
        if sub.empty:
            continue
        relay = sub['接力率'].dropna()
        rep = sub['修复'].dropna()
        L.append(f'| {label}断 | {len(sub)} | {relay.median():.0f}% | '
                 f'{_s(summarize(sub["enter3"]), "med", "{:+.2f}%")} | '
                 f'**{_s(summarize(sub["enterb3"]), "med", "{:+.2f}%")}** | '
                 f'{sub["n_s_buy"].median():.0f} 只 | '
                 f'{_s(summarize(sub["lead3"]), "med", "{:+.2f}%")} | {rep.mean()*100:.0f}% |')
    L.append('')
    # 差额和符号翻转都从数据里读出来, 不手写
    _flip = []
    for lo, hi, label in BOARD_BREAK_BANDS:
        sub = broke[(broke['H'] >= lo) & (broke['H'] <= hi)]
        a, b = sub['enter3'].dropna(), sub['enterb3'].dropna()
        if len(a) and len(b):
            _flip.append((label, a.median(), b.median()))
    if _flip:
        L.append('**怎么操作**: 只看加粗那一列。两列的差就是"把买不到的涨停价算进去"能虚增多少 —— '
                 + '; '.join(f'{t} {a:+.2f}% → {b:+.2f}% ({b-a:+.2f}pp)' for t, a, b in _flip)
                 + '。')
        _sign_flip = [t for t, a, b in _flip if (a > 0) != (b > 0)]
        if _sign_flip:
            L.append('')
            L.append(f'⚠️ **{"、".join(_sign_flip)}** 这一档在两个口径下**符号是反的**: '
                     '全篮看着能赚, 剔掉买不到的涨停股就不赚了。这一档不能当买入纪律。')
    L.append('')

    L.append('### 4. 断板时的档差 (次高板离龙头几档)')
    L.append('')
    L.append('| 档差 H−次高 | n | 次高板晋级率 | 次高板介入+3日中位 | 高度修复% |')
    L.append('|---|---|---|---|---|')
    for label, mask in [('1档 (阶梯完整)', broke['档差'] == 1),
                        ('2档', broke['档差'] == 2),
                        ('≥3档 (孤峰断板)', broke['档差'] >= 3)]:
        sub = broke[mask]
        if len(sub) < 3:
            continue
        relay = sub['接力率'].dropna()
        rep = sub['修复'].dropna()
        L.append(f'| {label} | {len(sub)} | {relay.median():.0f}% | '
                 f'{_s(summarize(sub["enter3"]), "med", "{:+.2f}%")} | {rep.mean()*100:.0f}% |')
    L.append('')
    return L


def sec_cycle(cy: pd.DataFrame) -> list[str]:
    L = ['## 五、高度周期的形状', '']
    if cy.empty:
        return L + ['样本不足。', '']

    pulse = cy[cy['len'] < MIN_CYCLE_LEN]
    real = cy[cy['len'] >= MIN_CYCLE_LEN]
    geo = real[real['natural_end']]          # 几何统计只用自然结束的段

    L.append(f'周期定义: 最高板 >{CYCLE_FLOOR} 板的连续交易日段, 遇长假 / 残缺快照日切断。'
             f'共切出 **{len(cy)} 段**, 其中:')
    L.append('')
    L.append(f'- **单日脉冲 {len(pulse)} 段** (高度冒一天头就掉回 ≤{CYCLE_FLOOR} 板) —— '
             '不算周期, 下面的几何统计不含它们。混进去会把"周期长度"和"见峰后剩几天"'
             '的中位数直接压到 1 天和 0 天。')
    L.append(f'- **真周期 {len(real)} 段**, 其中自然结束 (高度自己掉下去) **{len(geo)} 段**。'
             '切在长假 / 数据缺口 / 样本末尾的段, 长度只是下限, 不进中位数。')
    L.append('')

    if geo.empty:
        L.append('自然结束的周期不足, 几何形状无法统计。')
        L.append('')
        return L

    L.append(f'### 自然结束周期的形状 (n={len(geo)})')
    L.append('')
    L.append(f'- 周期长度中位 **{geo["len"].median():.0f} 天** (区间 {geo["len"].min()}–{geo["len"].max()})')
    L.append(f'- 峰值高度中位 **{geo["peak"].median():.0f} 板** (区间 {geo["peak"].min()}–{geo["peak"].max()})')
    L.append(f'- 峰值出现在周期第 **{geo["peak_day"].median():.0f} 天** (中位)')
    L.append(f'- 见峰之后还剩 **{geo["after_peak"].median():.0f} 天** (中位) 高度就掉回周期外')
    L.append('')
    L.append('| 周期 | 起 | 止 | 长度 | 峰值 | 峰值在第几天 | 见峰后剩几天 | 结束原因 |')
    L.append('|---|---|---|---|---|---|---|---|')
    for i, (_, r) in enumerate(real.sort_values('start').iterrows(), 1):
        L.append(f'| {i} | {r["start"]} | {r["end"]} | {int(r["len"])}天 | {int(r["peak"])}板 | '
                 f'第{int(r["peak_day"])}天 | {int(r["after_peak"])}天 | {r["end_reason"]} |')
    L.append('')
    after = geo['after_peak']
    L.append(f'**怎么操作**: 见峰之后 **{(after <= 1).mean()*100:.0f}%** 的周期在 1 天内结束, '
             f'中位剩余 {after.median():.0f} 天。所以"最高板刚创新高"不是加仓信号, '
             '是开始减的信号 —— 高度顶不给第二次出货机会。'
             f'但注意峰值日期的中位在第 {geo["peak_day"].median():.0f} 天, '
             '这说明周期前段才是高度的加仓区。')
    L.append('')

    L.append('### 清过某个高度之后还能走多远')
    L.append('')
    L.append(f'口径: 只用真周期 (n={len(real)}), 含被截断的段 —— 这里问的是"峰值能到几板", '
             '截断影响的是长度不是峰值。')
    L.append('')
    L.append('| 清掉这一档 | 周期数 | 最终峰值中位 | 就此止步% |')
    L.append('|---|---|---|---|')
    for lvl in (4, 5, 6, 7, 8):
        sub = real[real['peak'] >= lvl]
        if sub.empty:
            continue
        stop = (sub['peak'] == lvl).mean() * 100
        L.append(f'| {lvl}板 | {len(sub)} | {sub["peak"].median():.0f}板 | {stop:.0f}% |')
    L.append('')
    return L


def sec_playbook(ev: pd.DataFrame, bk: pd.DataFrame) -> list[str]:
    """把上面所有结论压成可执行规则, 每条带 n 和成功率。

    两条硬约束:
      - 收益一律用**次日收盘建仓**口径 (ztn3/enter3), 因为信号当晚才看到, 而且
        事件日那批票全在涨停板上, 事件日收盘价根本买不到。
      - n < MIN_N_FOR_RULE 的规则降级成"线索", 标题上直接写明, 不许当纪律执行。
    """
    L = ['## 六、系统战法 (每条带样本数和成功率; n 不够的明确标成线索)', '']
    L.append(f'**收益口径**: 全部是"信号当晚看到 → 次日收盘建仓 → 持 3 个交易日"的涨停池等权收益。'
             f'事件日收盘买不到 (票都在板上), 所以不用事件日口径。')
    L.append(f'**n 门槛**: n ≥ {MIN_N_FOR_RULE} 才叫纪律, 低于这个数只写成线索。')
    L.append('')
    br = ev[ev['state'] == '突破']
    broke = bk[bk['broke']] if not bk.empty else bk

    rules = []

    # 战法 A/B: 突破按落点分档 —— 边界与正文第三节共用 BREAK_BANDS
    for lo, hi, band_name in BREAK_BANDS:
        sub = br if lo is None and hi is None else br[
            ((br['maxh'] >= lo) if lo is not None else True)
            & ((br['maxh'] <= hi) if hi is not None else True)
        ]
        if sub.empty:
            continue
        z3 = sub['ztn3'].dropna()
        d5 = sub['dmax5'].dropna()
        d1 = sub['dmax1'].dropna()
        if z3.empty:
            continue
        is_high = lo is not None and lo >= 7
        # ⚠️ 措辞由数字符号决定, 不写死方向。上一版把 A 写成"可以做多", 而它自己的
        #    可成交口径是负的 —— 结论和证据打对台。这里让两者不可能再分叉。
        edge = z3.median()
        rules.append({
            'name': (('B · 高位突破 → 只减不加' if is_high else 'A · 中低位突破 → 没有可交易的多头优势')
                     if edge <= 0 else
                     ('B · 高位突破 → 只减不加' if is_high else 'A · 中低位突破 → 弱多头倾向')),
            'trigger': f'最高板突破前 {PRESSURE_WINDOW} 日高点, 且**{band_name.split(" ")[-1]}**',
            'n': len(sub),
            'metric': f'次日建仓涨停池 +3日 中位 {edge:+.2f}% · 赚钱事件率 {(z3 > 0).mean()*100:.0f}% · '
                      f'5日内再创新高 {(d5 > 0).mean()*100:.0f}% · 次日高度下降 {(d1 < 0).mean()*100:.0f}%',
            'action': ('突破本身不是买点。手里高位股在突破当天分批走, 不要等次日确认 —— '
                       '次日高度下降是大概率, 而"确认"这个动作本身就晚了一天。'
                       if is_high else
                       (f'高度还在往上 (5日再创新高 {(d5 > 0).mean()*100:.0f}%), 但可成交口径是亏的: '
                        f'次日收盘建仓 3 天中位 {edge:+.2f}%, 赚钱事件不到一半。'
                        '**别把"突破"当买入信号** —— 它是行情高度的读数, 不是一张能下单的票。'
                        '要参与就等回落档 (战法 C) 的首板池, 不要在突破次日追涨停池。'
                        if edge <= 0 else
                        f'次日收盘建仓持 3 天中位 {edge:+.2f}%, 幅度很薄, 仓位按试仓给。')),
        })

    # 战法 C: 深度回落低吸
    # ⚠️ 上一版这条的动作是"买新首板池", 而首板池收益当时根本没测过 —— 一条战法
    #    把仓位指向一个从未度量的篮子。现在 b1n{k} 测出来了, 措辞由它的符号决定。
    deep = ev[ev['state'] == '深度回落']
    if not deep.empty:
        d5 = deep['dmax5'].dropna()
        z3 = deep['ztn3'].dropna()
        b3 = deep['b1n3'].dropna()
        b3w = deep['b1n3_win'].dropna()
        lift = (d5 > 0).mean() * 100
        parts = [f'5日内最高板增量中位 {d5.median():+.0f} 档 · 抬升概率 {lift:.0f}%']
        if len(z3):
            parts.append(f'次日建仓涨停池 +3日 中位 {z3.median():+.2f}%')
        if len(b3):
            parts.append(f'次日建仓**首板池** +3日 中位 {b3.median():+.2f}%')
        if len(b3w):
            parts.append(f'首板池个股赚钱率 {b3w.mean():.0f}%')
        # 首板池比涨停池好多少 —— 这个差值才是"买首板不买高位"的全部依据
        if len(b3) and len(z3):
            gap = b3.median() - z3.median()
            if gap > 0:
                act = (f'这是高度的补库位置, 高度几乎必然回来 ({lift:.0f}%)。但"高度回来"≠'
                       f'"这批票赚钱" —— 落点必须是**新首板池**: 次日收盘建仓首板池 +3日 中位 '
                       f'{b3.median():+.2f}%, 比同口径涨停池 ({z3.median():+.2f}%) 好 '
                       f'{gap:.2f} 个点。残余高位股不在这条战法里, 它们的高度是上一轮的。'
                       f'仓位可以正常给, 这是全篇 n 最大 (n={len(deep)}) 且方向最干净的一条。')
            else:
                act = (f'这是高度的补库位置, 高度几乎必然回来 ({lift:.0f}%) —— **但没有一个'
                       f'能赚钱的篮子**: 首板池 +3日 中位 {b3.median():+.2f}%, 涨停池 '
                       f'{z3.median():+.2f}%, 首板并不更优。这一条只能当**行情节奏读数**用: '
                       '知道高度会回来, 所以不要在这里割掉底仓、不要追空; '
                       '但"买首板池"这个动作本身在这半年样本里没有正期望, 别照着下单。')
        else:
            act = f'这是高度的补库位置, 高度几乎必然回来 ({lift:.0f}%)。落点篮子样本不足, 不给下单指令。'
        rules.append({
            'name': 'C · 深度回落 → 等高度重启',
            'trigger': f'最高板比压力高度低 ≥2 档',
            'n': len(deep),
            'metric': ' · '.join(parts),
            'action': act,
        })

    # 战法 D: 断板接棒 —— 边界共用 BOARD_BREAK_BANDS, 与正文第四节第 3 表逐档对齐。
    # ⚠️ 上一版这里自己并了个"≥5板", 把 5-6 板 (正) 和 ≥7 板 (负) 平均成一个小负数,
    #    战法里读不出"一个能接一个必须走"。分档不能在战法侧另立。
    if not broke.empty:
        # "最差的一档"这种比较级必须先把三档都算出来再判, 不能写死在某一档里。
        # 上一版把它钉在 3-4 板 (当时 -4.13% vs ≥7板 -3.65% 确实最差), 换成可成交
        # 口径后 ≥7板 是 -14.31%, 那句话就变成假的了。比较级一律由数据给。
        _band_edge: dict[str, float] = {}
        for _lo, _hi, _tag in BOARD_BREAK_BANDS:
            _s3 = broke[(broke['H'] >= _lo) & (broke['H'] <= _hi)]['enterb3'].dropna()
            if len(_s3):
                _band_edge[_tag] = _s3.median()
        _worst_tag = min(_band_edge, key=_band_edge.get) if _band_edge else None

        for idx, (lo, hi, tag) in enumerate(BOARD_BREAK_BANDS, start=1):
            sub = broke[(broke['H'] >= lo) & (broke['H'] <= hi)]
            if sub.empty:
                continue
            # ⚠️ 结论必须走**可成交口径** enterb3 (剔掉断板日自己也涨停的次高板)。
            #    用 enter3 会把当日封板价当成买入价 —— 那批票恰好是涨得最好的一半
            #    (5-6 板档接力率中位 50%), 结论会被一个填不到的价格顶成正的。
            e3 = sub['enterb3'].dropna()
            e3_all = sub['enter3'].dropna()
            relay = sub['接力率'].dropna()
            rep = sub['修复'].dropna()
            if e3.empty:
                continue
            edge = e3.median()
            hit = (e3 > 0).mean() * 100
            edge_all = e3_all.median() if len(e3_all) else float('nan')
            repair = rep.mean() * 100 if len(rep) else float('nan')
            # 措辞随符号走: 正中位=可以接, 负中位=不接。别写死方向。
            verdict = '次高板可以接' if edge > 0 else '次高板别接'
            # 两个口径差多少也要报出来 —— 这是"不可成交价虚增了多少"的直接读数。
            fill_gap = (f' (含当日涨停那半个篮子是 {edge_all:+.2f}%, '
                        f'差 {edge_all - edge:+.2f}pp 全是买不到的价)'
                        if pd.notna(edge_all) else '')
            # ⚠️ 负档也要分两种, 不能共用一段话。低位断板 (H 只比周期地板高一两档)
            #    是**行情根本没起来**, 手里压根没有高位股可走; 高位断板才是周期结束。
            #    上一版两档共用"手里的高位股当天走", 放在 3-4 板那档是说不通的。
            is_low = hi is not None and hi <= CYCLE_FLOOR + 1
            if edge > 0:
                act = (f'断板日收盘介入次高板 (只买当日**没有**涨停的那部分, 涨停的填不到), '
                       f'持 3 天中位 {edge:+.2f}%, 赚钱事件率 {hit:.0f}%{fill_gap}。'
                       f'高度 5 日内修复 {repair:.0f}% —— 这一档的断板是**洗盘不是退潮**, '
                       '龙头掉下来但梯队还在, 次高板顶上去的概率够。'
                       '仓位正常给, 止损放在次高板自己也断板那天。')
            elif is_low:
                # ⚠️ "最差的一档"由 _worst_tag 给, 不能写死。可成交口径下最差的是 ≥7板,
                #    上一版这句钉在 3-4 板, 换口径后就成了假话。
                _worst_txt = ('这是全部断板档里最差的一档。'
                              if _worst_tag == tag else
                              f'比它更差的是 {_worst_tag}断 ({_band_edge[_worst_tag]:+.2f}%)。')
                act = (f'断板日收盘介入次高板 (可成交口径) 持 3 天中位 {edge:+.2f}%, '
                       f'赚钱事件率只有 {hit:.0f}%{fill_gap} —— '
                       f'{_worst_txt}原因不是"周期结束", 而是**行情根本没起来**: '
                       f'最高板才 {tag}, 次高板就是 2-3 板, 接的是一个没有溢价的位置。'
                       f'表里那个"高度 5 日内修复 {repair:.0f}%"别当利好读 —— '
                       f'{tag}离周期地板 ({CYCLE_FLOOR}板) 只差一两档, 随便冒一天头就算修复, '
                       '这个数字接近恒真, 不含信息。'
                       '**这一档不做接棒**: 空仓等高度自己起来, 或者按战法 C 等深度回落后的新首板池。')
            elif repair >= REPAIR_ALIVE:
                # ⚠️ 高修复档不能写"按周期结束处理" —— 它自己的修复率就在打自己的脸。
                #    这一档要说的是两件独立的事: 周期没结束 (高度会回来), 但**接棒这个
                #    动作**依然不赚钱 (可成交口径中位为负)。上一版把这两句并成"周期结束",
                #    5-6 板档就出现了"按周期结束处理"紧跟"修复 85%"的自相矛盾。
                act = (f'断板日收盘介入次高板 (可成交口径) 持 3 天中位 {edge:+.2f}%, '
                       f'赚钱事件率只有 {hit:.0f}%{fill_gap}。'
                       f'但高度 5 日内修复 {repair:.0f}% —— **周期没结束, 是接棒这个动作不赚钱**。'
                       '这两句不冲突: 断板后高度确实会回来, 可回来靠的是新资金打新方向, '
                       '不是昨天的次高板顶上去。'
                       '**怎么做**: 手里的高位股按自己的止损走 (不用清仓, 这不是退潮), '
                       '但别在断板日收盘去接次高板 —— 那个位置买不到涨停的那半个篮子, '
                       f'剩下能买到的这半个是 {edge:+.2f}%。等高度自己重启后再从新首板池进。')
            else:
                act = (f'断板日收盘介入次高板 (可成交口径) 持 3 天中位 {edge:+.2f}%, '
                       f'赚钱事件率只有 {hit:.0f}%{fill_gap} —— 接棒在这一档亏得最狠。'
                       f'高度 5 日内修复只有 {repair:.0f}%, 就算修复了也不是这批高位票修复的。'
                       '**这一档的断板按周期结束处理**: 手里的高位股当天走, 不要用"次高板接棒"这个'
                       '理由留仓, 要参与等回落档 (战法 C) 的新首板池。')
            rules.append({
                'name': f'D{idx} · {tag}断板 → {verdict}',
                'trigger': f'最高板 {tag} 那一档全体没能晋级 (断板)',
                'n': len(sub),
                'metric': f'次高板当日晋级率中位 {relay.median():.0f}% · 断板日收盘介入 +3日 中位 '
                          f'{edge:+.2f}% · 赚钱事件率 {hit:.0f}% · '
                          f'高度5日内修复 {repair:.0f}%',
                'action': act,
            })

    # 战法 E: 孤峰突破
    # ⚠️ 上一版把"孤峰差≥2"当过滤器写成"按退潮前一天处理", 两处站不住:
    #    (1) 它覆盖了绝大多数突破日 —— 突破本来就是靠单只票顶出来的, 孤峰是突破的
    #        **默认形状**, 不是一个能筛掉谁的条件;
    #    (2) 它自己的次日数字并不比阶梯形状差。
    #    所以这里改成: 报覆盖率, 跟补集 (阶梯完整) 对照, 结论由差值符号决定。
    orphan = br[br['孤峰差'] >= 2]
    ladder = br[br['孤峰差'] <= 1]
    if not orphan.empty:
        cover = len(orphan) / len(br) * 100 if len(br) else float('nan')
        m1 = orphan['mktn1'].dropna()
        z3 = orphan['ztn3'].dropna()
        lm1 = ladder['mktn1'].dropna()
        lz3 = ladder['ztn3'].dropna()
        # 只有"孤峰明显比阶梯差"才配叫风险信号。差值两项都要看。
        worse_mkt = len(lm1) > 0 and m1.median() < lm1.median()
        worse_zt = len(lz3) > 0 and len(z3) > 0 and z3.median() < lz3.median()
        is_signal = worse_mkt and worse_zt
        cmp_txt = (f'对照阶梯形状 (孤峰差≤1, n={len(ladder)}): 次日全市场中位 '
                   f'{lm1.median():+.2f}% · 次日建仓涨停池 +3日 {lz3.median():+.2f}%'
                   if len(lm1) and len(lz3) else '补集样本太少, 无法对照')
        rules.append({
            'name': ('E · 孤峰式突破 → 降级处理' if is_signal else
                     'E · 孤峰不是风险信号 (它就是突破的常态形状)'),
            'trigger': f'突破日最高板比次高档多 ≥2 档 (下面缺档) —— 占全部突破日 {cover:.0f}%',
            'n': len(orphan),
            'metric': (f'次日全市场中位 {m1.median():+.2f}% · 次日下跌概率 {(m1 < 0).mean()*100:.0f}% · '
                       f'次日建仓涨停池 +3日 中位 {z3.median():+.2f}% || {cmp_txt}'
                       if len(z3) else
                       f'次日全市场中位 {m1.median():+.2f}% · 次日下跌概率 '
                       f'{(m1 < 0).mean()*100:.0f}% || {cmp_txt}'),
            'action': ('有高度没梯队的突破按"退潮前一天"处理, 与既有择时里的孤峰保护同源。'
                       if is_signal else
                       f'**别拿孤峰当卖出理由。** 它覆盖了 {cover:.0f}% 的突破日 —— 高度本来就是'
                       '单只票顶出来的, "下面缺档"是突破的常态, 不是异常。而且这一组的次日'
                       '数字并不比阶梯形状差。真正管用的孤峰保护是择时层里那条更窄的条件 '
                       '(6+板 且 5 板缺档), 不要用这里的 ≥2 档去替代它, 会把八成突破日'
                       '全判成退潮前夜。离场理由请用战法 B (高位突破) 和 D3 (≥7板断板)。'),
        })

    firm = [r for r in rules if r['n'] >= MIN_N_FOR_RULE]
    hints = [r for r in rules if r['n'] < MIN_N_FOR_RULE]

    def _emit(r, tag):
        L.append(f'### {r["name"]}{tag}')
        L.append('')
        L.append(f'- **触发**: {r["trigger"]}')
        L.append(f'- **样本**: n = {r["n"]}')
        L.append(f'- **成绩**: {r["metric"]}')
        L.append(f'- **怎么操作**: {r["action"]}')
        L.append('')

    for r in firm:
        _emit(r, '')
    if hints:
        L.append(f'### ⚠️ 以下 {len(hints)} 条样本不足 {MIN_N_FOR_RULE}, 只当线索')
        L.append('')
        L.append('样本太少的规则, 中位数换一两个事件就会翻符号。列在这里是为了下个季度继续观察, '
                 '**不是让你现在照着做**。')
        L.append('')
        for r in hints:
            _emit(r, f' *(线索, n={r["n"]})*')
    return L


def sec_stability(ev_full: pd.DataFrame, ev_half: pd.DataFrame) -> list[str]:
    """半年子样本 vs 全样本: 结论稳不稳。"""
    L = ['## 七、近半年子样本对照 (结论稳不稳)', '']
    L.append('把最近半年单独拎出来重算一遍。数字对不上不一定是错, 但对不上的那几条'
             '**只能当线索**, 不要写进纪律。')
    L.append('')
    L.append('| 指标 | 全样本 | 近半年 |')
    L.append('|---|---|---|')

    def _row(label, fn):
        a, b = fn(ev_full), fn(ev_half)
        L.append(f'| {label} | {a} | {b} |')

    _row('可算压力的天数', lambda e: f'{len(e)}')
    _row('突破日占比', lambda e: f'{(e["state"] == "突破").mean()*100:.0f}%')

    # 分档边界与正文/战法共用 BREAK_BANDS, 不在这里另立一套
    for lo, hi, label in BREAK_BANDS:
        def band_win(e, lo=lo, hi=hi):
            br = e[e['state'] == '突破']
            m = pd.Series(True, index=br.index)
            if lo is not None:
                m &= br['maxh'] >= lo
            if hi is not None:
                m &= br['maxh'] <= hi
            s = br[m]['ztn3'].dropna()
            return f'{s.median():+.2f}% (n={len(s)})' if len(s) else '—'
        _row(f'{label} · 涨停池次日建仓+3日中位', band_win)

    def deep_up(e):
        s = e[e['state'] == '深度回落']['dmax5'].dropna()
        return f'{(s>0).mean()*100:.0f}% (n={len(s)})' if len(s) else '—'
    _row('深度回落 5日内高度抬升%', deep_up)

    def orphan_down(e):
        s = e[(e['state'] == '突破') & (e['孤峰差'] >= 2)]['mktn1'].dropna()
        return f'{(s<0).mean()*100:.0f}% (n={len(s)})' if len(s) else '—'
    _row('孤峰突破 次日之后市场下跌%', orphan_down)
    L.append('')
    return L


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────
def main() -> None:
    t0 = datetime.now()
    print(f'[{t0:%H:%M:%S}] 载入缓存...')
    zt = load_zt()
    ad_map = load_sentiment()
    # 一次读盘建两个 book: 掩码版是全部指标的口径, 未掩码版只用来量"剔停牌之前
    # 假平盘有多少"。报告里那句"从 X% 降到 Y%" 必须两个数都是本轮现算的 ——
    # 写死任何一个, 换了样本区间散文就开始说谎。
    _frame = load_price_frame()
    susp = load_suspension()
    book = PriceBook(_frame, suspended=susp)
    book_raw = PriceBook(_frame) if susp else None
    del _frame
    print(f'  涨停记录 {len(zt)} 行 · 情绪 {len(ad_map)} 天 · 价格 {len(book.dates)} 天'
          + (f' · 停牌标记 {len(susp)} 个股票日 (掩掉 {book.n_masked})' if susp else ' · 无停牌标记'))

    hs, tiers = build_height_series(zt, ad_map)
    dropped = hs[~hs['usable']][['日期', 'zt', 'maxh']]
    hs = annotate_pressure(hs)

    print(f'[{datetime.now():%H:%M:%S}] 构建状态事件...')
    ev = build_state_events(hs, tiers, book)
    print(f'[{datetime.now():%H:%M:%S}] 构建断板事件...')
    bk = build_break_events(hs, tiers, book)
    cy = build_cycles(hs)
    ev_half = ev[ev['日期'] >= HALF_YEAR_START]

    ok = hs[hs['usable']]

    # 结论先行的两句话必须由数字算出来, 不能手写。上一版写死了"低位突破是买点",
    # 而可成交口径 (次日收盘建仓) 恰好是负的 —— 摘要和正文自相矛盾。
    _brk = ev[ev['state'] == '突破']
    _brk_z3 = _brk['ztn3'].dropna()
    _deep_d5 = ev[ev['state'] == '深度回落']['dmax5'].dropna()
    _brk_nh = _brk['dmax5'].dropna()
    _brk_edge = f'{_brk_z3.median():+.2f}%' if len(_brk_z3) else '—'
    _brk_win = f'{(_brk_z3 > 0).mean()*100:.0f}%' if len(_brk_z3) else '—'
    _brk_nh_p = f'{(_brk_nh > 0).mean()*100:.0f}%' if len(_brk_nh) else '—'
    _deep_p = f'{(_deep_d5 > 0).mean()*100:.0f}%' if len(_deep_d5) else '—'

    # 离场那句同样不能手写。上一版点名"孤峰突破"是离场信号, 而战法 E 自己的对照
    # (孤峰 vs 阶梯) 得出的是相反结论 —— 孤峰是突破的常态形状, 不是风险条件。
    # 这里改成引用最高那一档断板的实测数字, 它才是真正带负号的离场证据。
    _hi_lo, _hi_hi, _hi_tag = BOARD_BREAK_BANDS[-1]
    _hb = bk[bk['broke'] & (bk['H'] >= _hi_lo) & (bk['H'] <= _hi_hi)] if not bk.empty else bk
    # 与战法 D 同口径: enterb3 (剔掉断板日自己也涨停、买不到的那部分次高板)
    _hb_e3 = _hb['enterb3'].dropna() if len(_hb) else pd.Series(dtype=float)
    _hb_rep = _hb['修复'].dropna() if len(_hb) else pd.Series(dtype=float)
    _exit_txt = (
        f'**{_hi_tag}断板**是周期结束信号 (接棒 +3日 中位 {_hb_e3.median():+.2f}%, '
        f'高度 5 日内修复只有 {_hb_rep.mean()*100:.0f}%, n={len(_hb)})'
        if len(_hb_e3) and len(_hb_rep) else '**高位断板**是周期结束信号'
    )

    lines = [
        '# 连板高度 × 压力高度 —— 系统战法',
        f'*生成时间: {datetime.now():%Y-%m-%d %H:%M} · 样本 {ok["日期"].iloc[0]} → {ok["日期"].iloc[-1]}*',
        '',
        '> 结论先行: **高度是均值回归量, 不是动量量 —— 而且它是一个读数, 不是一个买点。**',
        f'> 突破压力高度确实带高度信息 (突破后 5 日内再创新高 {_brk_nh_p}), 但换不成钱: '
        f'次日收盘建仓涨停池持 3 天中位 {_brk_edge}, 赚钱事件率 {_brk_win}。',
        f'> 真正能用的不对称在另外两头 —— **深度回落**是高度的补库位 (5 日内抬升 {_deep_p}), '
        f'{_exit_txt}。',
        '> 详见第六节, 每条都标了样本数。',
        '',
    ]
    # 脏数据探针只在可用交易日上跑 —— 残缺快照日本来就被剔了, 不该混进分母
    _probe_days = [d for d in ok['日期'] if d in book.dates]
    frozen = book.frozen_share(_probe_days)
    # 未掩码版跑**同一批日子**, 否则"从 X% 降到 Y%"是两个分母的数, 不可比。
    frozen_raw = book_raw.frozen_share(_probe_days) if book_raw is not None else None
    book_raw = None          # 28MB pivot, 量完就放掉, 后面全程用掩码版
    lines += sec_data(hs, dropped, cy, frozen,
                      n_masked=book.n_masked, frozen_raw=frozen_raw)
    lines += sec_terrain(hs)
    lines += sec_relation(hs, ev)
    lines += sec_breakout(ev)
    lines += sec_break_board(bk)
    lines += sec_cycle(cy)
    lines += sec_playbook(ev, bk)
    lines += sec_stability(ev, ev_half)

    text = '\n'.join(lines)
    out_path = os.path.join(OUTPUT_DIR, 'height_pressure_system.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(text)

    print('\n' + '=' * 70)
    print(text)
    print('=' * 70)
    print(f'\n报告已保存: {out_path}')
    print(f'耗时 {(datetime.now() - t0).total_seconds():.1f}s')


if __name__ == '__main__':
    main()
