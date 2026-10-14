"""缠论内核 (Chan Theory Engine): 去包含 → 分型 → 笔 → 中枢, 纯 Python 零依赖。

为什么自己写一遍, 而不是 pip install czsc
--------------------------------------------------
参考实现是 waditu/czsc (1.0 起核心算法已全部搬到 Rust, 通过 PyO3 暴露)。本项目**不能**直接依赖它:

1. **装不上**。czsc 1.x 的缠论内核是 Rust 扩展 (`czsc._native`), 预编译轮子之外要 Rust
   工具链 + maturin。本机现在 `import czsc` 直接 ModuleNotFoundError, 而
   `src/zhao_tracker.py` 里那段 `sys.path.insert(.../github缠论项目分析/.venv/...)` 指向的
   目录并不存在 —— 也就是说那边的"缠论笔/中枢"从来没真算过, 一直走的是
   "连接近 25 日最高/最低点" 三点折线兜底 (zs_boxes 恒为空)。这正是本模块要根治的。
2. **CI 跑不起**。日报在 GitHub Actions 每天跑, 不能引入需要编译的重依赖。
3. **口径要能自己核验**。本项目一贯要求判据单一真源、可单测 (见 ad_breadth / limit_events),
   第三方黑盒出来的"三买"没法对账。

所以这里按 czsc 的 Rust 源码**逐行对齐**重写成纯 Python (只用标准库 + dataclasses),
算法与 `crates/czsc-core/src/analyze/utils.rs`、`objects/{bi,zs,fx,bar}.rs` 等价。
对齐点在每个函数的 docstring 里标了出处, 改动前请先看那边。

数据口径 (本项目三个真源)
--------------------------------------------------
* **个股**: 腾讯 qfq 日线 `web.ifzq.gtimg.cn/appstock/app/fqkline/get` —— 有完整 OHLCV,
  且绕得过本机 Clash 代理 (见 memory price-cache-tencent-fastpath)。
* **指数**: akshare `stock_zh_index_daily` (phase_resonance.fetch_index 已在用) 或腾讯同一接口。
* **板块指数**: `data/ths_sector_hist.json` (90 个同花顺行业板块, 零网络)。
  ⚠️ 这份缓存**没有 open 字段**, 只有 date/close/high/low/amount。分型与笔只用 high/low,
  不受影响; 但 `BI.snr` / `BI.slope` 要用 open/close, 故 `bars_from_records` 会把缺失的
  open 合成为"前一根的 close"并置 `open_synthetic=True`, 依赖 SNR 的判据必须自己跳过。

必读的两个坑
--------------------------------------------------
* **笔会被回退**。`_update_bi` 末尾那段"当前笔被破坏"会把刚成的笔 pop 掉并把 K 线退回
  bars_ubi —— 没有它, 走势新高/新低时笔列表会错, 这是与 czsc 一致的关键一步。
* **dt 必须可比较且升序**。日线一律用 'YYYY-MM-DD' 字符串 (ISO 字符串的字典序 == 时序),
  不要混入 datetime 与 str。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# 笔的最小长度: 构成一笔的无包含 K 线根数下限 (对齐 czsc 默认 6, 见 resolve_min_bi_len)。
# bars_a 的跨度 = fx_b 顶点 - fx_a 顶点 + 3, 所以 6 等价于"两个分型顶点至少隔 3 根无包含K线"。
MIN_BI_LEN = 6
# 笔列表上限 (对齐 czsc 默认 50)。日线 250 根 K 线大约 15~30 笔, 这个上限基本不会触发。
MAX_BI_NUM = 50

UP = '向上'
DOWN = '向下'
MARK_G = '顶分型'
MARK_D = '底分型'


# ============================================================ 数据对象

@dataclass
class RawBar:
    """原始 K 线。对齐 czsc objects/bar.rs RawBar。"""
    symbol: str
    dt: Any           # 'YYYY-MM-DD' 字符串 (日线) 或任何可比较对象
    open: float
    high: float
    low: float
    close: float
    vol: float = 0.0
    amount: float = 0.0
    id: int = 0
    freq: str = '日线'
    open_synthetic: bool = False   # True = open 是"前一根 close"合成的 (板块指数缓存无 open)


@dataclass
class NewBar:
    """去掉包含关系之后的 K 线。elements 是它吞掉的原始 K 线。对齐 NewBar。"""
    symbol: str
    dt: Any
    open: float
    high: float
    low: float
    close: float
    vol: float = 0.0
    amount: float = 0.0
    freq: str = '日线'
    elements: list[RawBar] = field(default_factory=list)

    @classmethod
    def from_raw(cls, bar: RawBar) -> 'NewBar':
        return cls(
            symbol=bar.symbol, dt=bar.dt, open=bar.open, high=bar.high,
            low=bar.low, close=bar.close, vol=bar.vol, amount=bar.amount,
            freq=bar.freq, elements=[bar],
        )


@dataclass
class FX:
    """分型。mark=顶分型时 fx=中间K线的 high, 底分型时 fx=low。对齐 objects/fx.rs。"""
    symbol: str
    dt: Any
    mark: str              # MARK_G / MARK_D
    high: float
    low: float
    fx: float
    elements: list[NewBar] = field(default_factory=list)

    @property
    def power_str(self) -> str:
        """分型强弱 (强/中/弱)。对齐 czsc-signals utils/cxt.rs fx_power_str。

        底分型: 第三根收盘 > 第一根 high 为"强", > 第二根 high 为"中", 否则"弱"。顶分型镜像。
        """
        if len(self.elements) != 3:
            return '弱'
        k1, k2, k3 = self.elements
        if self.mark == MARK_D:
            if k3.close > k1.high:
                return '强'
            return '中' if k3.close > k2.high else '弱'
        if k3.close < k1.low:
            return '强'
        return '中' if k3.close < k2.low else '弱'

    @property
    def has_zs(self) -> bool:
        """分型内部三根K线是否有重合区间 (min(high) >= max(low))。对齐 fx_has_zs。"""
        if len(self.elements) != 3:
            return False
        zd = max(x.low for x in self.elements)
        zg = min(x.high for x in self.elements)
        return zg >= zd


@dataclass
class BI:
    """笔。对齐 objects/bi.rs —— 所有派生量都按那边的定义算, 别自己改口径。"""
    symbol: str
    fx_a: FX                      # 笔开始的分型
    fx_b: FX                      # 笔结束的分型
    fxs: list[FX]                 # 笔内部的分型序列
    direction: str                # UP / DOWN
    bars: list[NewBar]            # 构成这一笔的无包含K线 (含两端分型的K线)

    # --- 位置 ---
    @property
    def sdt(self) -> Any:
        return self.bars[0].dt

    @property
    def edt(self) -> Any:
        return self.bars[-1].dt

    @property
    def high(self) -> float:
        """笔的最高点 = max(两端分型的 high)。注意不是笔内K线最高价。"""
        return max(self.fx_a.high, self.fx_b.high)

    @property
    def low(self) -> float:
        return min(self.fx_a.low, self.fx_b.low)

    # --- 力度 ---
    @property
    def power_price(self) -> float:
        """价差力度 = |fx_b.fx - fx_a.fx|, 保留 2 位。背驰判据的主力度。"""
        return round(abs(self.fx_b.fx - self.fx_a.fx), 2)

    @property
    def power(self) -> float:
        return self.power_price

    @property
    def power_volume(self) -> float:
        """成交量力度 = 去掉首尾两根之后的 vol 之和 (首尾与相邻笔共享, 故剔除)。"""
        if len(self.bars) <= 2:
            return 0.0
        return float(sum(x.vol for x in self.bars[1:-1]))

    @property
    def length(self) -> int:
        """笔的长度 = 无包含K线根数。"""
        return len(self.bars)

    @property
    def change(self) -> float:
        """笔的涨跌幅 = (fx_b.fx - fx_a.fx) / fx_a.fx, 保留 4 位。"""
        if self.fx_a.fx == 0:
            return 0.0
        return round((self.fx_b.fx - self.fx_a.fx) / self.fx_a.fx, 4)

    @property
    def raw_bars(self) -> list[RawBar]:
        """笔内部的原始K线 (同样剔除首尾两根无包含K线)。"""
        if len(self.bars) <= 2:
            return []
        out: list[RawBar] = []
        for bar in self.bars[1:-1]:
            out.extend(bar.elements)
        return out

    @property
    def snr(self) -> float:
        """信噪比 = |末收 - 首开| / Σ|收-开|。越大越顺畅, 越小越拉锯。

        ⚠️ open 合成的序列 (板块指数) 这个值没有意义, 调用方看 open_synthetic 自行跳过。
        """
        bars = self.raw_bars
        if not bars:
            return 0.0
        if len(bars) == 1:
            return abs(bars[0].close - bars[0].open)
        total = abs(bars[-1].close - bars[0].open)
        diff = sum(abs(b.close - b.open) for b in bars)
        return round(total / diff, 4) if diff else 0.0

    @property
    def open_synthetic(self) -> bool:
        return any(b.open_synthetic for b in self.raw_bars)

    @property
    def slope(self) -> float:
        """笔内部收盘价的线性回归斜率。对齐 bi.rs get_slope。"""
        closes = [b.close for b in self.raw_bars]
        n = len(closes)
        if n < 2:
            return 0.0
        x_mean = (n - 1) / 2
        y_mean = sum(closes) / n
        num = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(closes))
        den = sum((i - x_mean) ** 2 for i in range(n))
        return round(num / den, 4) if den else 0.0

    def __repr__(self) -> str:  # 便于调试与日志
        return (f'BI({self.direction} {self.sdt}~{self.edt} '
                f'{self.fx_a.fx:.2f}->{self.fx_b.fx:.2f} len={self.length})')


@dataclass
class ZS:
    """中枢。对齐 objects/zs.rs: zg/zd 只取**前三笔**的高低点, gg/dd 取全部笔。

    注意 czsc 的 get_zs_seq 会把每一笔都塞进某个 ZS, 所以 len(bis)==1 的 ZS 不是真中枢;
    真中枢请用 `is_real` (至少三笔且 zg>=zd 且 is_valid)。
    """
    bis: list[BI]

    @property
    def sdt(self) -> Any:
        return self.bis[0].sdt

    @property
    def edt(self) -> Any:
        return self.bis[-1].edt

    @property
    def sdir(self) -> str:
        return self.bis[0].direction

    @property
    def edir(self) -> str:
        return self.bis[-1].direction

    @property
    def zg(self) -> float:
        """中枢上沿 = 前三笔 high 的最小值。"""
        return min(x.high for x in self.bis[:3])

    @property
    def zd(self) -> float:
        """中枢下沿 = 前三笔 low 的最大值。"""
        return max(x.low for x in self.bis[:3])

    @property
    def zz(self) -> float:
        """中枢中轴。"""
        return self.zd + (self.zg - self.zd) * 0.5

    @property
    def gg(self) -> float:
        """中枢最高点 (全部笔)。"""
        return max(x.high for x in self.bis)

    @property
    def dd(self) -> float:
        """中枢最低点 (全部笔)。"""
        return min(x.low for x in self.bis)

    @property
    def is_valid(self) -> bool:
        """中枢有效性: zg>=zd 且每一笔都与中枢区间有交集。对齐 zs.rs is_valid。"""
        zg, zd = self.zg, self.zd
        if zg < zd:
            return False
        for bi in self.bis:
            high_in = zd <= bi.high <= zg
            low_in = zd <= bi.low <= zg
            covers = bi.high >= zg and bi.low <= zd
            if not (high_in or low_in or covers):
                return False
        return True

    @property
    def is_real(self) -> bool:
        """是不是一个真中枢 (三笔及以上 + 有效)。单笔/双笔的 ZS 只是分组产物。"""
        return len(self.bis) >= 3 and self.is_valid

    @property
    def amp(self) -> float:
        """中枢振幅 (zg/zd - 1), 衡量中枢的"厚度"。"""
        return round(self.zg / self.zd - 1, 4) if self.zd else 0.0

    def __repr__(self) -> str:
        return (f'ZS({len(self.bis)}笔 {self.sdt}~{self.edt} '
                f'[{self.zd:.2f}, {self.zg:.2f}])')


# ============================================================ 核心算法

def remove_include(k1: NewBar, k2: NewBar, k3: RawBar) -> tuple[bool, NewBar]:
    """去除包含关系。k1/k2 已无包含, k3 是新来的原始K线。

    对齐 analyze/utils.rs remove_include:
    * 方向由 k1.high 与 k2.high 决定; 相等时**不合并**, 直接把 k3 当新K线。
    * 向上合并取 (max(high), max(low)); 向下合并取 (min(high), min(low))。
      ⚠️ 向上时 low 取 **max** 而不是 min —— 这是缠论去包含的定义, 不是笔误。
    * dt 跟着被选中的那根走: 向上取 high 更高者的 dt, 向下取 low 更低者的 dt。
    """
    if k1.high < k2.high:
        direction = UP
    elif k1.high > k2.high:
        direction = DOWN
    else:
        return False, NewBar.from_raw(k3)

    has_include = (k2.high <= k3.high and k2.low >= k3.low) or (k2.high >= k3.high and k2.low <= k3.low)
    if not has_include:
        return False, NewBar.from_raw(k3)

    if direction == UP:
        high = max(k2.high, k3.high)
        low = max(k2.low, k3.low)
        dt = k2.dt if k2.high > k3.high else k3.dt
    else:
        high = min(k2.high, k3.high)
        low = min(k2.low, k3.low)
        dt = k2.dt if k2.low < k3.low else k3.dt

    open_, close = (high, low) if k3.open > k3.close else (low, high)
    elements = [x for x in k2.elements[:100] if x.dt != k3.dt] + [k3]
    k4 = NewBar(
        symbol=k2.symbol, dt=dt, open=open_, close=close, high=high, low=low,
        vol=k2.vol + k3.vol, amount=k2.amount + k3.amount, freq=k2.freq, elements=elements,
    )
    return True, k4


def check_fx(k1: NewBar, k2: NewBar, k3: NewBar) -> FX | None:
    """三根无包含K线找分型。对齐 analyze/utils.rs check_fx。

    顶分型: k2 的 high 与 low 都高于 k1、k3; 底分型镜像。
    """
    if k1.high < k2.high > k3.high and k1.low < k2.low > k3.low:
        return FX(symbol=k1.symbol, dt=k2.dt, mark=MARK_G, high=k2.high,
                  low=k2.low, fx=k2.high, elements=[k1, k2, k3])
    if k1.low > k2.low < k3.low and k1.high > k2.high < k3.high:
        return FX(symbol=k1.symbol, dt=k2.dt, mark=MARK_D, high=k2.high,
                  low=k2.low, fx=k2.low, elements=[k1, k2, k3])
    return None


def check_fxs(bars: Sequence[NewBar]) -> list[FX]:
    """找出序列里所有分型, 并强制顶底交替 (同向重复的后者丢弃)。对齐 check_fxs。"""
    fxs: list[FX] = []
    for i in range(len(bars) - 2):
        fx = check_fx(bars[i], bars[i + 1], bars[i + 2])
        if fx is None:
            continue
        if fxs and fx.mark == fxs[-1].mark:
            # czsc 这里只打日志不中断: 正常情况下分型本就交替, 少数特例强制交替。
            continue
        fxs.append(fx)
    return fxs


def check_bi(bars: Sequence[NewBar], min_bi_len: int = MIN_BI_LEN) -> tuple[BI | None, Sequence[NewBar]]:
    """从无包含K线序列里找出一笔, 返回 (笔 or None, 剩余K线)。对齐 check_bi。

    判据:
    1. fx_a 取序列首个分型; fx_b 取其后**同向最极端**的反向分型 (底→找最高顶, 并列保留先出现的);
    2. 两个分型不能互相包含 (ab_include);
    3. 跨度 bars_a 根数 >= min_bi_len。
    不成笔时原样退回 bars, 等后续K线补足。
    """
    fxs = check_fxs(bars)
    if len(fxs) < 2:
        return None, bars

    fx_a = fxs[0]
    fx_b: FX | None = None
    if fx_a.mark == MARK_D:
        direction = UP
        for x in fxs:
            if x.mark == MARK_G and x.dt > fx_a.dt and x.fx > fx_a.fx:
                if fx_b is None or x.high > fx_b.high:   # 严格大于 => 并列时保留先出现的
                    fx_b = x
    else:
        direction = DOWN
        for x in fxs:
            if x.mark == MARK_D and x.dt > fx_a.dt and x.fx < fx_a.fx:
                if fx_b is None or x.low < fx_b.low:
                    fx_b = x
    if fx_b is None:
        return None, bars

    start_dt = fx_a.elements[0].dt
    end_dt = fx_b.elements[2].dt
    bars_a = [b for b in bars if start_dt <= b.dt <= end_dt]
    if not bars_a:
        return None, bars
    new_start_dt = fx_b.elements[0].dt
    bars_b = [b for b in bars if b.dt >= new_start_dt]

    ab_include = ((fx_a.high > fx_b.high and fx_a.low < fx_b.low)
                  or (fx_a.high < fx_b.high and fx_a.low > fx_b.low))
    if not ab_include and len(bars_a) >= min_bi_len:
        bi = BI(
            symbol=fx_a.symbol, fx_a=fx_a, fx_b=fx_b,
            fxs=[x for x in fxs if start_dt <= x.dt <= end_dt],
            direction=direction, bars=bars_a,
        )
        return bi, bars_b
    return None, bars


def get_zs_seq(bis: Sequence[BI]) -> list[ZS]:
    """把连续笔划分成中枢序列。对齐 analyze/utils.rs get_zs_seq。

    规则: 向上笔的 high 低于当前中枢下沿, 或向下笔的 low 高于上沿 => 另起一个中枢; 否则并入。
    """
    zs_list: list[ZS] = []
    for bi in bis:
        if not zs_list:
            zs_list.append(ZS(bis=[bi]))
            continue
        last = zs_list[-1]
        if ((bi.direction == UP and bi.high < last.zd)
                or (bi.direction == DOWN and bi.low > last.zg)):
            zs_list.append(ZS(bis=[bi]))
        else:
            zs_list[-1] = ZS(bis=last.bis + [bi])
    return zs_list


def is_bis_up(bis: Sequence[BI]) -> bool:
    """连续奇数笔是否构成向上结构 (末笔向上且创全局新高, 首笔低点是全局最低)。"""
    if len(bis) < 3 or len(bis) % 2 == 0:
        return False
    if any(bis[i].edt >= bis[i + 1].edt for i in range(len(bis) - 1)):
        return False
    first, last = bis[0], bis[-1]
    return (last.direction == UP
            and last.high == max(x.high for x in bis)
            and first.low == min(x.low for x in bis))


def is_bis_down(bis: Sequence[BI]) -> bool:
    """连续奇数笔是否构成向下结构。"""
    if len(bis) < 3 or len(bis) % 2 == 0:
        return False
    if any(bis[i].edt >= bis[i + 1].edt for i in range(len(bis) - 1)):
        return False
    first, last = bis[0], bis[-1]
    return (last.direction == DOWN
            and first.high == max(x.high for x in bis)
            and last.low == min(x.low for x in bis))


def is_symmetry_zs(bis: Sequence[BI], threshold: float = 0.3) -> bool:
    """是否对称中枢: 奇数笔(>=3)、有重合区间、各笔力度的离散系数 <= threshold。"""
    if len(bis) < 3 or len(bis) % 2 == 0 or threshold < 0:
        return False
    zs = ZS(bis=list(bis))
    max_low = max(x.low for x in bis)
    min_high = min(x.high for x in bis)
    if zs.zd > zs.zg or max_low > min_high:
        return False
    powers = [x.power_price for x in bis]
    mean = sum(powers) / len(powers)
    if not mean:
        return False
    var = sum((p - mean) ** 2 for p in powers) / len(powers)
    return (var ** 0.5) / abs(mean) <= threshold


def check_gap_info(bars: Sequence[RawBar]) -> list[dict]:
    """K线缺口及回补状态。对齐 check_gap_info。A股一字板/跳空缺口在战法里是硬支撑/阻力。"""
    gaps: list[dict] = []
    for i in range(1, len(bars)):
        b1, b2 = bars[i - 1], bars[i]
        right = bars[i:]
        if b1.high < b2.low:
            gaps.append({
                'kind': '向上缺口',
                'cover': '已补' if any(x.low < b1.high for x in right) else '未补',
                'sdt': b1.dt, 'edt': b2.dt, 'high': b2.low, 'low': b1.high,
                'delta': round(b2.low / b1.high - 1, 4) if b1.high else 0.0,
            })
        elif b1.low > b2.high:
            gaps.append({
                'kind': '向下缺口',
                'cover': '已补' if any(x.high > b1.low for x in right) else '未补',
                'sdt': b1.dt, 'edt': b2.dt, 'high': b1.low, 'low': b2.high,
                'delta': round(b1.low / b2.high - 1, 4) if b2.high else 0.0,
            })
    return gaps


# ============================================================ 分析器

class ChanAnalyzer:
    """缠论分析器 (对齐 czsc 的 CZSC 类)。逐根喂K线, 增量维护 分型/笔/中枢。

    增量这件事不是性能优化, 而是**回测不穿越**的保证: 走到第 i 根时, 笔与中枢只由前 i 根
    决定, 所以直接拿它做历史验证不会用到未来数据。

    用法:
        c = ChanAnalyzer(bars, symbol='sh000001')        # 一次性灌入
        c.bi_list[-1].direction, c.zs_list[-1].zg        # 读结构
        c.update(new_bar)                                # 增量追加
    """

    def __init__(self, bars: Iterable[RawBar] | None = None, symbol: str = '',
                 freq: str = '日线', min_bi_len: int = MIN_BI_LEN,
                 max_bi_num: int = MAX_BI_NUM):
        self.symbol = symbol
        self.freq = freq
        self.min_bi_len = min_bi_len
        self.max_bi_num = max_bi_num
        self.bars_raw: list[RawBar] = []
        self.bars_ubi: list[NewBar] = []    # 未成笔的无包含K线 (UBI = un-finished bi)
        self.bi_list: list[BI] = []
        for bar in (bars or []):
            self.update(bar)

    # ---------------------------------------------------------- 更新

    def update(self, bar: RawBar) -> None:
        """喂一根原始K线。同 dt 重复喂 = 当日行情刷新 (替换最后一根)。"""
        if not self.bars_raw or bar.dt != self.bars_raw[-1].dt:
            self.bars_raw.append(bar)
            last_bars = [bar]
        else:
            # 同一根K线的延伸 (盘中刷新): 退回最后一根无包含K线再重算
            self.bars_raw[-1] = bar
            last_ubi = self.bars_ubi.pop()
            last_bars = list(last_ubi.elements)
            last_bars[-1] = bar

        for b in last_bars:
            if len(self.bars_ubi) < 2:
                self.bars_ubi.append(NewBar.from_raw(b))
            else:
                has_include, k4 = remove_include(self.bars_ubi[-2], self.bars_ubi[-1], b)
                if has_include:
                    self.bars_ubi[-1] = k4
                else:
                    self.bars_ubi.append(k4)

        self._update_bi()

        if len(self.bi_list) > self.max_bi_num:
            self.bi_list = self.bi_list[-self.max_bi_num:]
        if self.bi_list:
            sdt = self.bi_list[0].fx_a.elements[0].dt
            self.bars_raw = [x for x in self.bars_raw if x.dt >= sdt]

    def _update_bi(self) -> None:
        """维护笔列表。对齐 analyze/mod.rs __update_bi, 包括末尾的"笔被破坏"回退。"""
        if len(self.bars_ubi) < 3:
            return

        if not self.bi_list:
            # 第一笔: 先把起点定在"同向最极端"的那个分型上, 再找笔
            fxs = check_fxs(self.bars_ubi)
            if not fxs:
                return
            first = fxs[0]
            same = [x for x in fxs if x.mark == first.mark]
            if first.mark == MARK_D:
                fx_a = min(same, key=lambda x: x.low)          # 最低的底 (并列取后者, 对齐 <=)
                for x in same:
                    if x.low <= fx_a.low:
                        fx_a = x
            else:
                fx_a = max(same, key=lambda x: x.high)
                for x in same:
                    if x.high >= fx_a.high:
                        fx_a = x
            bars_f = [x for x in self.bars_ubi if x.dt >= fx_a.elements[0].dt]
            bi, rest = check_bi(bars_f, self.min_bi_len)
            if bi is not None:
                self.bi_list.append(bi)
            self.bars_ubi = list(rest)
            return

        bi, rest = check_bi(self.bars_ubi, self.min_bi_len)
        if bi is not None:
            self.bi_list.append(bi)
        self.bars_ubi = list(rest)

        # 当前笔被破坏: 最新K线突破了笔的端点 => 这一笔不成立, 退回去等重算。
        # 少了这一步, 单边走势里的笔会定错, 后面所有中枢/买卖点都跟着错。
        last_bi = self.bi_list[-1]
        if self.bars_ubi and (
            (last_bi.direction == UP and self.bars_ubi[-1].high > last_bi.high)
            or (last_bi.direction == DOWN and self.bars_ubi[-1].low < last_bi.low)
        ):
            merge_point = last_bi.bars[-2].dt
            self.bars_ubi = (last_bi.bars[:-2]
                             + [x for x in self.bars_ubi if x.dt >= merge_point])
            self.bi_list.pop()

    # ---------------------------------------------------------- 读取

    @property
    def zs_list(self) -> list[ZS]:
        """中枢序列 (含单笔分组产物, 真中枢请过滤 is_real)。"""
        return get_zs_seq(self.bi_list)

    @property
    def real_zs_list(self) -> list[ZS]:
        return [z for z in self.zs_list if z.is_real]

    @property
    def fx_list(self) -> list[FX]:
        """已成笔部分的分型 + 未完成笔里的分型。"""
        out: list[FX] = []
        for bi in self.bi_list:
            out.extend(bi.fxs[:-1] if bi is not self.bi_list[-1] else bi.fxs)
        out.extend(self.ubi_fxs)
        # 去重 (相邻笔共享端点分型)
        seen, uniq = set(), []
        for fx in out:
            key = (fx.dt, fx.mark)
            if key not in seen:
                seen.add(key)
                uniq.append(fx)
        return uniq

    @property
    def ubi_fxs(self) -> list[FX]:
        return check_fxs(self.bars_ubi) if self.bars_ubi else []

    @property
    def last_bi_direction(self) -> str | None:
        return self.bi_list[-1].direction if self.bi_list else None

    @property
    def ubi_len(self) -> int:
        """未完成笔的无包含K线根数 —— czsc 很多信号用它判"笔是否刚结束"。"""
        return len(self.bars_ubi)

    @property
    def latest_price(self) -> float | None:
        return self.bars_raw[-1].close if self.bars_raw else None

    @property
    def latest_dt(self) -> Any:
        return self.bars_raw[-1].dt if self.bars_raw else None


# ============================================================ 数据适配

def bars_from_records(records: Sequence[dict], symbol: str = '', freq: str = '日线',
                      date_key: str = 'date') -> list[RawBar]:
    """把 [{'date','open','high','low','close','vol'|'amount'}] 转成 RawBar 列表。

    两处宽容 (都是本项目真实缓存的形状, 见模块头部"数据口径"):
    * **没有 open**: 用前一根的 close 合成, 并打 open_synthetic 标记 (板块指数缓存)。
      第一根没有前收时退化为 close 自身。
    * **没有 vol**: 用 amount 顶上 (板块指数只有成交额)。power_volume 因此是"成交额力度",
      同一标的内部可比, 跨标的不可比。
    """
    out: list[RawBar] = []
    prev_close: float | None = None
    for i, r in enumerate(records):
        try:
            close = float(r['close'])
            high = float(r['high'])
            low = float(r['low'])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(x == x for x in (close, high, low)):   # NaN 跳过
            continue
        raw_open = r.get('open')
        synth = False
        if raw_open in (None, '') or (isinstance(raw_open, float) and raw_open != raw_open):
            open_ = prev_close if prev_close is not None else close
            synth = True
        else:
            open_ = float(raw_open)
        vol = r.get('vol', r.get('volume'))
        amount = r.get('amount', 0.0)
        try:
            vol = float(vol) if vol not in (None, '') else float(amount or 0.0)
        except (TypeError, ValueError):
            vol = 0.0
        try:
            amount = float(amount or 0.0)
        except (TypeError, ValueError):
            amount = 0.0
        out.append(RawBar(
            symbol=symbol, dt=r[date_key], open=open_, high=high, low=low, close=close,
            vol=vol, amount=amount, id=i, freq=freq, open_synthetic=synth,
        ))
        prev_close = close
    return out


def resample_weekly(bars: Sequence[RawBar], symbol: str = '') -> list[RawBar]:
    """日线合成周线 (缠论多级别联立要用)。按 ISO 周分组, 不补缺。

    口径: open 取周首、close 取周末、high/low 取区间极值、vol/amount 累加,
    dt 用**该周最后一个交易日**(与日线对齐, 便于按报告日截断)。
    """
    import datetime as _dt

    groups: dict[tuple[int, int], list[RawBar]] = {}
    order: list[tuple[int, int]] = []
    for b in bars:
        d = b.dt
        if isinstance(d, str):
            d = _dt.date.fromisoformat(d[:10])
        key = d.isocalendar()[:2]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(b)

    out: list[RawBar] = []
    for i, key in enumerate(order):
        g = groups[key]
        out.append(RawBar(
            symbol=symbol or g[0].symbol, dt=g[-1].dt, open=g[0].open,
            high=max(x.high for x in g), low=min(x.low for x in g), close=g[-1].close,
            vol=sum(x.vol for x in g), amount=sum(x.amount for x in g), id=i, freq='周线',
            open_synthetic=any(x.open_synthetic for x in g),
        ))
    return out
