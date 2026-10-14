"""缠论信号层 (Signal) + 事件层 (Event): 把笔/中枢翻译成可组合的离散信号。

体系来源: waditu/czsc 的「信号-事件-交易」三层
--------------------------------------------------
czsc 最值得学的不是某个买点公式, 而是**把判断拆成三层**:

* **信号 Signal** —— 只描述一个客观事实, 不带操作含义。格式 `k1_k2_k3_v1_v2_v3`:
  k1=级别, k2=参数, k3=信号名; v1/v2/v3=取值 (`任意` 是通配)。
  例: `日线_D1_三买V1_三买_强_任意` = 日线最近一笔完成了一个"强三买"。
* **事件 Event** —— 信号的布尔组合: signals_all (全满足) / signals_any (至少一个) /
  signals_not (都不满足)。一个事件 = 一种可交易的形态。
* **交易 Position** —— 事件 + 止损/持有期 (本项目在 chan_tactics 里只做到"战法+回测", 不下单)。

这样拆的好处在本项目尤其明显: 信号可以单测 (给定笔序列, 输出确定), 战法只是信号的组合,
回测就是在历史每一天上重放"信号→事件"。改战法不用碰信号, 改信号也不会悄悄改掉战法的口径。

信号清单 (每个函数头注明对齐的 czsc 源函数)
--------------------------------------------------
| 信号名        | 含义                                           | 来源                          |
|---------------|------------------------------------------------|-------------------------------|
| 表里关系      | 外部方向(笔) × 内部状态(未完成笔的分型)        | cxt_bi_status_V230101         |
| 三笔形态      | 奔走/收敛/扩张/盘背/不重合                     | cxt_three_bi_V230618          |
| 五笔形态      | aAb底背驰/类趋势背驰/颈线突破/类三买卖         | cxt_five_bi_V230619           |
| 七笔形态      | aAbcd/abcAd 背驰/中枢完成/类三买卖             | cxt_seven_bi_V230620          |
| 一买 / 一卖   | 5~21 笔下跌结构 + 价/量/长度力度衰竭           | cxt_first_buy/sell_V221126    |
| 三买辅助      | 关键高点序列被回抽低点站上 + 1.618 空间约束    | cxt_third_buy_V230228         |
| 中枢三买卖    | 离开笔 + 回抽笔不回中枢 (标准定义, 已确认)     | 缠论原文第 20 课, 本项目实现  |
| 三买候选      | 离开中枢后**未完成笔**回抽不破 zg (盘中/次日用)| 本项目实现                    |
| 二买 / 二卖   | 一买之后的回抽低点不破一买低点                 | 缠论原文, 本项目实现          |
| MACD背驰      | 同向笔新高/新低 + MACD 柱面积缩小              | tas_macd_bc 思想, 按笔面积实现|
| 中枢位置      | 现价在最近真中枢 上方/内部/下方 + 距离         | 本项目实现                    |
| 中枢走向      | 最近两个真中枢 上移/下移/扩张/收敛             | 缠论原文第 17 课, 本项目实现  |
| 分型强弱      | 最后一个分型 强/中/弱                          | cxt_fx_power_V221107          |

⚠️ 关于"背驰"必须说清的一点
--------------------------------------------------
czsc 的 cxt 形态信号用的是**价差力度** (power_price), 不是 MACD。缠论原文的背驰比较的是
MACD 柱子面积。两者都实现了: 形态信号里叫"盘背/底背驰"(价差), `MACD背驰` 信号单独给面积比。
同时满足的背驰最可靠; 只有价差背驰而 MACD 面积放大的, 在强势股里常常是加速而非见顶。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from chan_engine import BI, DOWN, MARK_D, MARK_G, UP, ZS, ChanAnalyzer, RawBar

ANY = '任意'
OTHER = '其他'

# 未完成笔延伸超过这个根数, 就认为上一笔之后已经走出了反向笔 (对齐 czsc bars_ubi.len() > 7)
UBI_EXTEND = 7
# MACD 面积比低于这个值叫"显著背驰" (经验值, 不是 czsc 参数; 改它不影响结构信号)
BC_STRONG_RATIO = 0.6


# ============================================================ 信号对象

@dataclass(frozen=True)
class Signal:
    """单个信号。key = k1_k2_k3, value = v1_v2_v3。对齐 czsc objects/signal.rs 的字符串格式。"""
    k1: str
    k2: str
    k3: str
    v1: str = OTHER
    v2: str = ANY
    v3: str = ANY

    @property
    def key(self) -> str:
        return f'{self.k1}_{self.k2}_{self.k3}'

    @property
    def value(self) -> str:
        return f'{self.v1}_{self.v2}_{self.v3}'

    def __str__(self) -> str:
        return f'{self.key}_{self.value}'

    @classmethod
    def parse(cls, s: str) -> 'Signal':
        parts = s.split('_')
        if len(parts) != 6:
            raise ValueError(f'信号必须是 6 段 k1_k2_k3_v1_v2_v3: {s!r}')
        return cls(*parts)

    def is_match(self, pattern: 'Signal') -> bool:
        """pattern 的 v 段写 `任意` 视为通配。key 必须完全相等。"""
        if self.key != pattern.key:
            return False
        for mine, want in ((self.v1, pattern.v1), (self.v2, pattern.v2), (self.v3, pattern.v3)):
            if want != ANY and mine != want:
                return False
        return True


# ============================================================ 指标: MACD 与笔面积

def macd(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
         ) -> tuple[list[float], list[float], list[float]]:
    """国内口径 MACD: DIF=EMA12-EMA26, DEA=EMA9(DIF), 柱=2*(DIF-DEA)。纯因果 (只看过去)。"""
    def ema(xs: Sequence[float], n: int) -> list[float]:
        out: list[float] = []
        alpha = 2 / (n + 1)
        for i, x in enumerate(xs):
            out.append(x if i == 0 else alpha * x + (1 - alpha) * out[-1])
        return out

    if not closes:
        return [], [], []
    dif = [a - b for a, b in zip(ema(closes, fast), ema(closes, slow))]
    dea = ema(dif, signal)
    hist = [2 * (a - b) for a, b in zip(dif, dea)]
    return dif, dea, hist


class ChanContext:
    """一个标的在某一级别上的完整结构 + 指标, 是所有信号函数的输入。

    MACD 用**全量**原始K线算 (ChanAnalyzer.bars_raw 会被裁剪到第一笔起点, 直接用会让 EMA
    起点漂移), 所以构造时必须把完整的 bars 传进来。
    """

    def __init__(self, bars: Sequence[RawBar], symbol: str = '', freq: str = '日线',
                 analyzer: ChanAnalyzer | None = None):
        self.symbol = symbol
        self.freq = freq
        self.bars = list(bars)
        self.c = analyzer if analyzer is not None else ChanAnalyzer(self.bars, symbol=symbol, freq=freq)
        _, _, hist = macd([b.close for b in self.bars])
        self.hist_by_dt = {b.dt: h for b, h in zip(self.bars, hist)}
        self._dts = [b.dt for b in self.bars]

    # ---- 快捷访问 ----
    @property
    def bis(self) -> list[BI]:
        return self.c.bi_list

    @property
    def real_zs(self) -> list[ZS]:
        return self.c.real_zs_list

    @property
    def price(self) -> float | None:
        return self.bars[-1].close if self.bars else None

    @property
    def dt(self):
        return self.bars[-1].dt if self.bars else None

    def macd_area(self, sdt, edt, direction: str) -> float:
        """[sdt, edt] 区间内与 direction 同号的 MACD 柱面积 (取绝对值)。

        只累加同号柱子: 向上笔里夹着的绿柱是笔内回调, 算进去会把力度抵消掉。
        """
        total = 0.0
        for d in self._dts:
            if d < sdt or d > edt:
                continue
            h = self.hist_by_dt.get(d, 0.0)
            if direction == UP and h > 0:
                total += h
            elif direction == DOWN and h < 0:
                total -= h
        return total

    def bi_area(self, bi: BI) -> float:
        return self.macd_area(bi.fx_a.dt, bi.fx_b.dt, bi.direction)

    # ---- 未完成笔 (UBI) ----
    def ubi_extreme(self) -> tuple[float, float] | None:
        """最后一笔结束分型之后至今的 (最高, 最低)。用于判断回抽是否回到中枢。"""
        if not self.bis:
            return None
        start = self.bis[-1].fx_b.dt
        tail = [b for b in self.bars if b.dt > start]
        if not tail:
            return None
        return max(b.high for b in tail), min(b.low for b in tail)


def _sig(ctx: ChanContext, k2: str, k3: str, v1: str = OTHER, v2: str = ANY, v3: str = ANY) -> Signal:
    return Signal(ctx.freq, k2, k3, v1, v2, v3)


# ============================================================ 笔状态

def bi_status(ctx: ChanContext) -> Signal:
    """表里关系: 外部方向 × 内部状态。对齐 cxt_bi_status_V230101。

    外部方向: 最后一笔向下且未完成笔不长 => 还在"向下"; 未完成笔已延伸超过 7 根 => 已经"向上"。
    内部状态: 未完成笔里最后一个分型, 与外部方向相反的叫顶分/底分, 同向叫延伸。
    """
    if len(ctx.bis) < 2:
        return _sig(ctx, 'D1', '表里关系')
    last = ctx.bis[-1]
    extended = ctx.c.ubi_len > UBI_EXTEND
    if last.direction == DOWN:
        v1 = UP if extended else DOWN
    else:
        v1 = DOWN if extended else UP
    fxs = ctx.c.ubi_fxs
    if not fxs:
        v2 = '延伸'
    elif fxs[-1].mark == MARK_D:
        v2 = '底分' if v1 == DOWN else '延伸'
    else:
        v2 = '顶分' if v1 == UP else '延伸'
    return _sig(ctx, 'D1', '表里关系', v1, v2)


def fx_power(ctx: ChanContext) -> Signal:
    """最后一个分型的强弱 (强/中/弱 + 顶/底)。对齐 cxt_fx_power_V221107。"""
    fxs = ctx.c.fx_list
    if not fxs:
        return _sig(ctx, 'D1F', '分型强弱')
    fx = fxs[-1]
    kind = '底' if fx.mark == MARK_D else '顶'
    return _sig(ctx, 'D1F', '分型强弱', f'{fx.power_str}{kind}', '有中枢' if fx.has_zs else '无中枢')


# ============================================================ N 笔形态 (逐行对齐 czsc cxt.rs)

def three_bi(ctx: ChanContext) -> Signal:
    """三笔形态。对齐 cxt_three_bi_V230618。"""
    if len(ctx.bis) < 7 or ctx.c.ubi_len > UBI_EXTEND:
        return _sig(ctx, 'D1三笔', '形态')
    bi1, bi2, bi3 = ctx.bis[-3:]
    if bi3.direction == DOWN:
        if bi3.low > bi1.high:
            v = '向下不重合'
        elif bi2.low < bi3.low < bi1.high < bi2.high:
            v = '向下奔走型'
        elif bi1.high > bi3.high and bi1.low < bi3.low:
            v = '向下收敛'
        elif bi1.high < bi3.high and bi1.low > bi3.low:
            v = '向下扩张'
        elif bi3.low < bi1.low and bi3.high < bi1.high:
            v = '向下盘背' if bi3.power < bi1.power else '向下无背'
        else:
            v = OTHER
    else:
        if bi3.high < bi1.low:
            v = '向上不重合'
        elif bi2.low < bi1.low < bi3.high < bi2.high:
            v = '向上奔走型'
        elif bi1.high > bi3.high and bi1.low < bi3.low:
            v = '向上收敛'
        elif bi1.high < bi3.high and bi1.low > bi3.low:
            v = '向上扩张'
        elif bi3.low > bi1.low and bi3.high > bi1.high:
            v = '向上盘背' if bi3.power < bi1.power else '向上无背'
        else:
            v = OTHER
    return _sig(ctx, 'D1三笔', '形态', v)


def five_bi(ctx: ChanContext) -> Signal:
    """五笔形态。对齐 cxt_five_bi_V230619 (条件逐条照搬, 包括 min/max 的嵌套顺序)。"""
    if len(ctx.bis) < 7 or ctx.c.ubi_len > UBI_EXTEND:
        return _sig(ctx, 'D1五笔', '形态')
    bi1, bi2, bi3, bi4, bi5 = ctx.bis[-5:]
    bis = ctx.bis[-5:]
    max_high = max(x.high for x in bis)
    min_low = min(x.low for x in bis)
    v = OTHER
    if bi1.direction == DOWN:
        if (min(bi2.high, bi4.high) > max(bi2.low, bi4.low) and max_high == bi1.high
                and bi5.power < bi1.power
                and ((min_low == bi3.low and bi5.low < bi1.low) or min_low == bi5.low)):
            v = 'aAb式底背驰'
        elif (max_high == bi1.high and min_low == bi5.low and bi4.high < bi2.low
              and bi5.power < max(bi3.power, bi1.power)):
            v = '类趋势底背驰'
        elif ((min_low == bi1.low and bi5.high > min(bi1.high, bi2.high) > bi5.low > bi1.low)
              or (min_low == bi3.low and bi5.high > bi3.high > bi5.low > bi3.low)):
            v = '上颈线突破'
        elif (max_high == bi5.high and bi5.high > bi5.low > max(bi1.high, bi3.high)
              and min(bi1.high, bi3.high) > max(bi1.low, bi3.low) > min_low):
            v = '类三买'
    else:
        if (min(bi2.high, bi4.high) > max(bi2.low, bi4.low) and min_low == bi1.low
                and bi5.power < bi1.power
                and ((max_high == bi3.high and bi5.high > bi1.high) or max_high == bi5.high)):
            v = 'aAb式顶背驰'
        elif (min_low == bi1.low and max_high == bi5.high
              and bi5.power < max(bi1.power, bi3.power) and bi4.low > bi2.high):
            v = '类趋势顶背驰'
        elif ((max_high == bi1.high and bi5.low < max(bi1.low, bi2.low) < bi5.high < max_high)
              or (max_high == bi3.high and bi5.low < bi3.low < bi5.high < max_high)):
            v = '下颈线突破'
        elif (min_low == bi5.low and bi5.low < bi5.high < min(bi1.low, bi3.low)
              < max(bi1.low, bi3.low) < min(bi1.high, bi3.high) < max_high):
            v = '类三卖'
    return _sig(ctx, 'D1五笔', '形态', v)


def seven_bi(ctx: ChanContext) -> Signal:
    """七笔形态。对齐 cxt_seven_bi_V230620。"""
    if len(ctx.bis) < 10 or ctx.c.ubi_len > UBI_EXTEND:
        return _sig(ctx, 'D1七笔', '形态')
    bi1, bi2, bi3, bi4, bi5, bi6, bi7 = ctx.bis[-7:]
    bis = ctx.bis[-7:]
    max_high = max(x.high for x in bis)
    min_low = min(x.low for x in bis)
    v = OTHER
    if bi7.direction == DOWN:
        if bi1.high == max_high and bi7.low == min_low:
            if (min(bi2.high, bi4.high) > max(bi2.low, bi4.low) > bi6.high
                    and bi7.power < bi5.power):
                v = 'aAbcd式底背驰'
            elif (bi2.low > min(bi4.high, bi6.high) and max(bi4.low, bi6.low) < min(bi4.high, bi6.high)
                  and bi7.power < bi1.high - bi3.low):
                v = 'abcAd式底背驰'
            elif (min(bi2.high, bi4.high, bi6.high) > max(bi2.low, bi4.low, bi6.low)
                  and bi7.power < bi1.power):
                v = 'aAb式底背驰'
            elif (bi2.low > bi4.high and bi4.low > bi6.high
                  and bi7.power < max(bi5.power, bi3.power, bi1.power)):
                v = '类趋势底背驰'
        elif (bi4.low == min_low and min(bi1.high, bi3.high) > max(bi1.low, bi3.low)
              and min(bi5.high, bi7.high) > max(bi5.low, bi7.low)
              and max(bi4.high, bi6.high) > min(bi3.high, bi4.high)
              and max(bi1.low, bi3.low) < max(bi5.high, bi7.high)):
            v = '向上中枢完成'
        elif (min(bi1.low, bi3.low) == min_low and max(bi5.high, bi7.high) == max_high
              and min(bi5.low, bi7.low) > max(bi1.high, bi3.high)
              and min(bi1.high, bi3.high) > max(bi1.low, bi3.low)):
            v = '类三买'
    else:
        if bi1.low == min_low and bi7.high == max_high:
            if (bi6.low > min(bi2.high, bi4.high) > max(bi2.low, bi4.low)
                    and bi7.power < bi5.power):
                v = 'aAbcd式顶背驰'
            elif (min(bi4.high, bi6.high) > max(bi4.low, bi6.low) > bi2.high
                  and bi7.power < bi3.high - bi1.low):
                v = 'abcAd式顶背驰'
            elif (min(bi2.high, bi4.high, bi6.high) > max(bi2.low, bi4.low, bi6.low)
                  and bi7.power < bi1.power):
                v = 'aAb式顶背驰'
            elif (bi2.high < bi4.low and bi4.high < bi6.low
                  and bi7.power < max(bi5.power, bi3.power, bi1.power)):
                v = '类趋势顶背驰'
        elif (bi4.high == max_high and min(bi1.high, bi3.high) > max(bi1.low, bi3.low)
              and min(bi5.high, bi7.high) > max(bi5.low, bi7.low)
              and min(bi4.low, bi6.low) < max(bi3.low, bi4.low)
              and min(bi1.high, bi3.high) > min(bi5.low, bi7.low)):
            v = '向下中枢完成'
        elif (min(bi5.low, bi7.low) == min_low and max(bi1.high, bi3.high) == max_high
              and max(bi7.high, bi5.high) < min(bi1.low, bi3.low)
              and min(bi1.high, bi3.high) > max(bi1.low, bi3.low)):
            v = '类三卖'
    return _sig(ctx, 'D1七笔', '形态', v)


# ============================================================ 第一类买卖点

def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def check_first_buy(bis: Sequence[BI]) -> bool:
    """一买结构。对齐 czsc-signals utils/cxt.rs check_first_buy (逐行)。

    1. 奇数笔、首末笔都向下、首笔 high 是全局最高、末笔 low 是全局最低 (= 一段下跌);
    2. key_bis = 首笔 + 每个"比两笔前创了新低"的向下笔 (下跌过程中的主力杀跌笔);
    3. 背驰 = 末笔价差力度 < max(倒数第三笔, key_bis 平均) 且 (量能 或 长度) 也衰竭。
    """
    if len(bis) % 2 != 1 or bis[-1].direction == UP or bis[0].direction != bis[-1].direction:
        return False
    if max(x.high for x in bis) != bis[0].high or min(x.low for x in bis) != bis[-1].low:
        return False
    key_bis = [bis[0]] + [bis[i] for i in range(2, len(bis) - 2, 2) if bis[i].low < bis[i - 2].low]
    last, prev = bis[-1], bis[-3]
    bc_price = last.power_price < max(prev.power_price, _mean([x.power_price for x in key_bis]))
    bc_volume = last.power_volume < max(prev.power_volume, _mean([x.power_volume for x in key_bis]))
    bc_length = last.length < max(prev.length, _mean([x.length for x in key_bis]))
    return bc_price and (bc_volume or bc_length)


def check_first_sell(bis: Sequence[BI]) -> bool:
    """一卖结构。对齐 check_first_sell (check_first_buy 的镜像)。"""
    if len(bis) % 2 != 1 or bis[-1].direction == DOWN or bis[0].direction != bis[-1].direction:
        return False
    if max(x.high for x in bis) != bis[-1].high or min(x.low for x in bis) != bis[0].low:
        return False
    key_bis = [bis[0]] + [bis[i] for i in range(2, len(bis) - 2, 2) if bis[i].high > bis[i - 2].high]
    last, prev = bis[-1], bis[-3]
    bc_price = last.power_price < max(prev.power_price, _mean([x.power_price for x in key_bis]))
    bc_volume = last.power_volume < max(prev.power_volume, _mean([x.power_volume for x in key_bis]))
    bc_length = last.length < max(prev.length, _mean([x.length for x in key_bis]))
    return bc_price and (bc_volume or bc_length)


def first_buy(ctx: ChanContext, di: int = 1) -> Signal:
    """一买: 依次试最近 21/19/.../5 笔。对齐 cxt_first_buy_V221126。di=1 看最后一笔, di=3 看三笔前。"""
    end = len(ctx.bis) - di + 1
    for n in (21, 19, 17, 15, 13, 11, 9, 7, 5):
        if end - n < 0:
            continue
        if check_first_buy(ctx.bis[end - n:end]):
            return _sig(ctx, f'D{di}B', 'BUY1', '一买', f'{n}笔')
    return _sig(ctx, f'D{di}B', 'BUY1')


def first_sell(ctx: ChanContext, di: int = 1) -> Signal:
    end = len(ctx.bis) - di + 1
    for n in (21, 19, 17, 15, 13, 11, 9, 7, 5):
        if end - n < 0:
            continue
        if check_first_sell(ctx.bis[end - n:end]):
            return _sig(ctx, f'D{di}B', 'SELL1', '一卖', f'{n}笔')
    return _sig(ctx, f'D{di}B', 'SELL1')


# ============================================================ 第二类买卖点

def second_bs(ctx: ChanContext) -> Signal:
    """二买/二卖: 一买之后, 第一次回抽的低点不破一买低点。

    笔序列 ... [一买笔(向下)] [反弹笔(向上)] [回抽笔(向下) = 最后一笔]。
    一买的判定复用 check_first_buy, 窗口末端放在倒数第三笔 (di=3), 保证不偷看。
    """
    if len(ctx.bis) < 8:
        return _sig(ctx, 'D1', '二买卖')
    last = ctx.bis[-1]
    if last.direction == DOWN:
        fb = first_buy(ctx, di=3)
        if fb.v1 == '一买' and last.low > ctx.bis[-3].low:
            # 回抽还站上了一买反弹笔的中点 => 强二买 (回撤不到一半)
            rebound = ctx.bis[-2]
            mid = rebound.low + (rebound.high - rebound.low) * 0.5
            return _sig(ctx, 'D1', '二买卖', '二买', '强' if last.low > mid else '标准', fb.v2)
    else:
        fs = first_sell(ctx, di=3)
        if fs.v1 == '一卖' and last.high < ctx.bis[-3].high:
            drop = ctx.bis[-2]
            mid = drop.low + (drop.high - drop.low) * 0.5
            return _sig(ctx, 'D1', '二买卖', '二卖', '强' if last.high < mid else '标准', fs.v2)
    return _sig(ctx, 'D1', '二买卖')


# ============================================================ 第三类买卖点

def third_buy_assist(ctx: ChanContext) -> Signal:
    """三买辅助。对齐 cxt_third_buy_V230228 (逐行)。

    取最近 n+1 笔 (首笔与末笔反向, 末笔向下), key_bis = 每个创了新高的向下笔起点笔;
    三买 = 末笔低点 > key_bis 高点的最小值 (站上关键高点) 且 关键高点区间有重叠 (有中枢),
    同时末笔低点 < 整体最低 + 1.618 × key_bis 平均力度 (离中枢不能太远, 太远就不是三买而是加速)。
    """
    if len(ctx.bis) < 12:
        return _sig(ctx, 'D1', '三买辅助')
    for n in (13, 11, 9, 7, 5):
        bis = ctx.bis[-(n + 1):]
        if len(bis) != n + 1:
            continue
        if bis[-1].direction == UP or bis[0].direction == bis[-1].direction:
            continue
        key_bis = [bis[0]] + [bis[i] for i in range(2, len(bis) - 2, 2) if bis[i].high > bis[i - 2].high]
        if len(key_bis) < 2:
            continue
        min_kh = min(x.high for x in key_bis)
        max_kl = max(x.low for x in key_bis)
        tb_break = bis[-1].low > min_kh > max_kl
        tb_price = bis[-1].low < min(x.low for x in bis) + 1.618 * _mean([x.power_price for x in key_bis])
        if tb_break and tb_price:
            return _sig(ctx, 'D1', '三买辅助', '三买', f'{len(bis)}笔')
    return _sig(ctx, 'D1', '三买辅助')


def zs_third_bs(ctx: ChanContext) -> Signal:
    """中枢三买/三卖 (缠论标准定义, 已确认的那一类)。

    最后一笔是回抽笔, 倒数第二笔是离开笔, 再往前 >=3 笔构成真中枢:
    * 三买: 离开笔向上且 high > ZG, 回抽笔(向下)低点 > ZG (不回中枢);
    * 三卖: 镜像。
    v2: 强 = 回抽连中枢最高点 GG 都没碰到 (强势股的典型); 标准 = 回到 GG 以下但守住 ZG。
    v3: 离开笔 vs 进入笔 的价差力度 (离开更强 = 真突破, 离开更弱 = 小心假突破)。
    """
    bis = ctx.bis
    if len(bis) < 5:
        return _sig(ctx, 'D1', '中枢三买卖')
    back, leave = bis[-1], bis[-2]
    core = bis[:-2]
    # 从离开笔往回找: 末尾连续落在同一中枢里的笔 (至少 3 笔)
    zs_seq = [z for z in _zs_of(core) if z.is_real]
    if not zs_seq:
        return _sig(ctx, 'D1', '中枢三买卖')
    zs = zs_seq[-1]
    if zs.bis[-1] is not core[-1]:
        return _sig(ctx, 'D1', '中枢三买卖')   # 最近的真中枢不紧挨离开笔, 不是三类点
    enter = zs.bis[0]
    force = '离开强于进入' if leave.power_price > enter.power_price else '离开弱于进入'
    if leave.direction == UP and leave.high > zs.zg and back.low > zs.zg:
        return _sig(ctx, 'D1', '中枢三买卖', '三买', '强' if back.low > zs.gg else '标准', force)
    if leave.direction == DOWN and leave.low < zs.zd and back.high < zs.zd:
        return _sig(ctx, 'D1', '中枢三买卖', '三卖', '强' if back.high < zs.dd else '标准', force)
    return _sig(ctx, 'D1', '中枢三买卖')


def zs_third_buy_pending(ctx: ChanContext) -> Signal:
    """三买候选 (未确认): 最后一笔是向上离开笔, 之后的未完成笔回抽至今没回到 ZG 以下。

    已确认的三买要等回抽笔走完 (日线常常要 6~10 天), 短线等不起; 这个信号给的是
    "离开 → 回抽中 → 尚未破 ZG"的盘中状态, 次日计划用。破 ZG 立即作废。
    """
    bis = ctx.bis
    if len(bis) < 4:
        return _sig(ctx, 'D1', '三买候选')
    leave = bis[-1]
    if leave.direction != UP:
        return _sig(ctx, 'D1', '三买候选')
    zs_seq = [z for z in _zs_of(bis[:-1]) if z.is_real]
    if not zs_seq or zs_seq[-1].bis[-1] is not bis[-2]:
        return _sig(ctx, 'D1', '三买候选')
    zs = zs_seq[-1]
    ext = ctx.ubi_extreme()
    if leave.high <= zs.zg or ext is None:
        return _sig(ctx, 'D1', '三买候选')
    _, ubi_low = ext
    if ubi_low <= zs.zg:
        return _sig(ctx, 'D1', '三买候选', '已破ZG')
    gap = ubi_low / zs.zg - 1
    return _sig(ctx, 'D1', '三买候选', '回抽守ZG', '贴近' if gap < 0.03 else '悬空')


def _zs_of(bis: Sequence[BI]) -> list[ZS]:
    from chan_engine import get_zs_seq
    return get_zs_seq(bis)


# ============================================================ 背驰 (MACD 面积)

def macd_bc(ctx: ChanContext) -> Signal:
    """已完成笔的 MACD 面积背驰: 最后一笔 vs 前一同向笔。

    顶背驰: 向上笔创新高, 但 MACD 红柱面积 < 前一向上笔; 底背驰镜像。
    v2 = 显著 (面积比 < 0.6) / 一般; v3 = 面积比 (保留 2 位, 便于回测分层)。
    """
    bis = ctx.bis
    if len(bis) < 3:
        return _sig(ctx, 'D1', 'MACD背驰')
    last, prev = bis[-1], bis[-3]
    a_last, a_prev = ctx.bi_area(last), ctx.bi_area(prev)
    if a_prev <= 0:
        return _sig(ctx, 'D1', 'MACD背驰')
    ratio = a_last / a_prev
    level = '显著' if ratio < BC_STRONG_RATIO else '一般'
    if last.direction == UP and last.high > prev.high and ratio < 1:
        return _sig(ctx, 'D1', 'MACD背驰', '顶背驰', level, f'{ratio:.2f}')
    if last.direction == DOWN and last.low < prev.low and ratio < 1:
        return _sig(ctx, 'D1', 'MACD背驰', '底背驰', level, f'{ratio:.2f}')
    return _sig(ctx, 'D1', 'MACD背驰', '无背驰', ANY, f'{ratio:.2f}')


def ubi_macd_bc(ctx: ChanContext) -> Signal:
    """进行中的笔的背驰 (盘中/次日预警用): 未完成笔与前一同向已完成笔比较。

    强势股的顶部常常出在"新高的那一笔还没走完"的时候, 等它成笔再报警已经回撤 10%+。
    未完成笔方向 = 最后一笔的反方向, 前一同向笔 = bis[-2]。
    """
    bis = ctx.bis
    if len(bis) < 2 or not ctx.bars:
        return _sig(ctx, 'D0', '进行中背驰')
    ref = bis[-2]
    direction = UP if bis[-1].direction == DOWN else DOWN
    start = bis[-1].fx_b.dt
    tail = [b for b in ctx.bars if b.dt >= start]
    if len(tail) < 2:
        return _sig(ctx, 'D0', '进行中背驰')
    a_now = ctx.macd_area(start, ctx.dt, direction)
    a_ref = ctx.bi_area(ref)
    if a_ref <= 0:
        return _sig(ctx, 'D0', '进行中背驰')
    ratio = a_now / a_ref
    if direction == UP and max(b.high for b in tail) > ref.high and ratio < 1:
        return _sig(ctx, 'D0', '进行中背驰', '顶背驰预警', '显著' if ratio < BC_STRONG_RATIO else '一般', f'{ratio:.2f}')
    if direction == DOWN and min(b.low for b in tail) < ref.low and ratio < 1:
        return _sig(ctx, 'D0', '进行中背驰', '底背驰预警', '显著' if ratio < BC_STRONG_RATIO else '一般', f'{ratio:.2f}')
    return _sig(ctx, 'D0', '进行中背驰', '无', ANY, f'{ratio:.2f}')


# ============================================================ 中枢位置 / 走向

def zs_position(ctx: ChanContext) -> Signal:
    """现价相对最近一个真中枢: 上方/内部/下方 × 远近。远近按中枢振幅归一 (1 倍振幅以内叫"近")。"""
    zs_list = ctx.real_zs
    if not zs_list or ctx.price is None:
        return _sig(ctx, 'D1', '中枢位置', '无中枢')
    zs = zs_list[-1]
    p = ctx.price
    width = max(zs.zg - zs.zd, 1e-9)
    if p > zs.zg:
        return _sig(ctx, 'D1', '中枢位置', '中枢上方', '近' if (p - zs.zg) <= width else '远')
    if p < zs.zd:
        return _sig(ctx, 'D1', '中枢位置', '中枢下方', '近' if (zs.zd - p) <= width else '远')
    return _sig(ctx, 'D1', '中枢位置', '中枢内部', '上半' if p >= zs.zz else '下半')


def zs_trend(ctx: ChanContext) -> Signal:
    """最近两个真中枢的相对位置。对齐缠论第 17 课: 中枢上移 = 上涨趋势, 中枢下移 = 下跌趋势。"""
    zs_list = ctx.real_zs
    if len(zs_list) < 2:
        return _sig(ctx, 'D1', '中枢走向', '单中枢' if zs_list else '无中枢')
    z1, z2 = zs_list[-2], zs_list[-1]
    if z2.zd > z1.zg:
        v = '上移'
    elif z2.zg < z1.zd:
        v = '下移'
    elif z2.zg > z1.zg and z2.zd < z1.zd:
        v = '扩张'
    elif z2.zg < z1.zg and z2.zd > z1.zd:
        v = '收敛'
    else:
        v = '重叠上移' if z2.zz > z1.zz else '重叠下移'
    return _sig(ctx, 'D1', '中枢走向', v)


# ============================================================ 汇总

SIGNAL_FUNCS = (
    bi_status, fx_power, three_bi, five_bi, seven_bi,
    first_buy, first_sell, second_bs, third_buy_assist,
    zs_third_bs, zs_third_buy_pending, macd_bc, ubi_macd_bc,
    zs_position, zs_trend,
)


def compute_signals(ctx: ChanContext) -> dict[str, Signal]:
    """一次算出 ctx 上的全部信号, 以 key 为索引 (同一 key 只会有一个值)。"""
    out: dict[str, Signal] = {}
    for fn in SIGNAL_FUNCS:
        try:
            s = fn(ctx)
        except Exception:      # 单个信号算挂不能拖垮整份报告; 缺失即视为"其他"
            continue
        out[s.key] = s
    return out


def compute_multi_level(day_ctx: ChanContext, week_ctx: ChanContext | None) -> dict[str, Signal]:
    """多级别联立: 日线全部信号 + 周线的 表里关系/中枢位置/MACD背驰。

    缠论的区间套: 大级别定方向, 小级别找买点。周线不需要全部信号 (周线笔数少, 形态信号多半是"其他")。
    """
    sigs = compute_signals(day_ctx)
    if week_ctx is not None and len(week_ctx.bis) >= 2:
        for fn in (bi_status, zs_position, macd_bc):
            try:
                s = fn(week_ctx)
                sigs[s.key] = s
            except Exception:
                continue
    return sigs


# ============================================================ 事件 (Event)

@dataclass(frozen=True)
class Event:
    """信号的布尔组合 = 一种可交易形态。对齐 czsc objects/event.rs 的三元组合语义。

    命中条件: signals_all 全部命中 且 (signals_any 为空 或 至少命中一个) 且 signals_not 一个都不命中。
    信号写成字符串 `k1_k2_k3_v1_v2_v3`, v 段可用 `任意` 通配。
    """
    name: str
    operate: str                         # 开多 / 平多 / 回避 / 观察
    signals_all: tuple[str, ...] = ()
    signals_any: tuple[str, ...] = ()
    signals_not: tuple[str, ...] = ()

    def _parsed(self, items: Iterable[str]) -> list[Signal]:
        return [Signal.parse(s) for s in items]

    def is_match(self, sigs: dict[str, Signal]) -> bool:
        def hit(p: Signal) -> bool:
            s = sigs.get(p.key)
            return s is not None and s.is_match(p)

        if not all(hit(p) for p in self._parsed(self.signals_all)):
            return False
        any_ps = self._parsed(self.signals_any)
        if any_ps and not any(hit(p) for p in any_ps):
            return False
        if any(hit(p) for p in self._parsed(self.signals_not)):
            return False
        return True

    def matched_signals(self, sigs: dict[str, Signal]) -> list[str]:
        """命中的具体信号 (用于在报告里解释"为什么触发")。"""
        out = []
        for p in self._parsed(self.signals_all + self.signals_any):
            s = sigs.get(p.key)
            if s is not None and s.is_match(p):
                out.append(str(s))
        return out
