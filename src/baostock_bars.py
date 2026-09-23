"""baostock 长历史日线: 抓取 + 涨停/连板重建 + 停牌标记。

存在的理由 (三件事一次解决)
--------------------------------
1. **更长样本**。涨停池接口对任意历史日期返回 0 行 (memory zt-pool-api-no-history),
   所以 10 个月的样本无法用现有管道加长 —— 只能从 K 线**重建**涨停。
2. **停牌标记**。价格缓存里 2.17% 的股票日是"恰好 0.00%"的假平盘, 会把所有中位数
   往 0 拉、把红盘率压低。baostock 的 `tradestatus='0'` 就是真标记。
3. **涨停判据的分母**。本地缓存只有收盘价, 用"昨收盘"当分母在除权日会错。
   baostock 每根 K 线自带 `preclose`, 那才是交易所算涨停价用的分母。

判据是怎么定下来的 (别改成百分比阈值)
--------------------------------
先用百分比阈值试过, 3~10% 的误判。把争议样本逐个拉出来看, 结论是:

* **涨跌幅百分比根本不能当判据**。真值池里的涨停日 `pctChg` 分布在 4.82~5.12 和
  19.40~20.45 —— 因为涨停价是 `round(preclose × (1+幅度), 2)`, 低价股一分钱的
  四舍五入就是 0.3pp 的涨幅。任何阈值都会同时切错两头。
  唯一干净的判据是**收盘价精确等于涨停价**。
* **限制幅度由板块决定, 不由 ST 决定**。抽样验证: 主板 ST 封死在 5% (sh603557、
  sz000609 等 8 只全部 4.8~5.1%), 但创业板/科创板 ST 依然是 20% (sz300391 达
  20.45%, sh688184 带 ST 标记时摸到 19.98%)。所以 ST 只对主板降档。
* **真值池本身不是干净的标准答案**。它漏掉过真实涨停 (sh688185 64.43→77.32 精确
  +20%、三只主板 ST 精确 +5%), 所以重建结果与它的差异不能一律算"重建错了"。

已知边界
--------------------------------
* **北交所 (bj) 没有覆盖**。baostock 明确报错"股票代码未标识sh或sz"。实测代价:
  bj 占涨停池 0.75%、占 ≥7 板 0%、209 天里只有 1 天独占市场最高板。这份数据
  用于连板高度研究时损失可忽略, 但必须在报告里写明。
* **上市首日**不适用涨停判据 (首日另有规则), 重建时跳过每只票的第一根 K 线。
"""

from __future__ import annotations

import contextlib
import gzip
import io
import os
import sys
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd

from paths import (
    BAOSTOCK_BAR_DIR,
    BAOSTOCK_CLOSE_LONG,
    BAOSTOCK_FETCH_STATUS,
    BAOSTOCK_LIMIT_HISTORY,
    BAOSTOCK_SUSPENSION,
    BAOSTOCK_UNIVERSE_PIT,
)

# baostock 要拉的字段。preclose 是涨停判据的分母, tradestatus 是停牌标记,
# isST 决定主板要不要降到 5%。三个都不能省。
BAR_FIELDS = 'date,code,close,preclose,volume,tradestatus,isST,pctChg'

# 涨停幅度。键是板块, 不是 ST —— 见模块头部的验证结论。
LIMIT_MAIN = Decimal('0.10')
LIMIT_MAIN_ST = Decimal('0.05')
LIMIT_GROWTH = Decimal('0.20')  # 创业板 sz30x / 科创板 sh688, ST 也是 20%

os.makedirs(BAOSTOCK_BAR_DIR, exist_ok=True)


# ---------------------------------------------------------------- 代码格式

def to_dotted(code: str) -> str:
    """`sz300750` -> `sz.300750`。baostock 只认带点的 9 位格式。"""
    c = code.strip().lower()
    if '.' in c:
        return c
    return f'{c[:2]}.{c[2:]}'


def to_bare(code: str) -> str:
    """`sz.300750` -> `sz300750`。项目内部一律用裸码。"""
    return code.strip().lower().replace('.', '')


def is_supported(code: str) -> bool:
    """baostock 只有沪深。北交所整段没有覆盖。

    只答"交易所有没有数据"这一个问题。**是不是股票**是另一个问题, 见 is_stock ——
    这两件事混在一个函数里过一次, 代价是 1400 只基金进了股票池。
    """
    return to_bare(code)[:2] in ('sh', 'sz')


# A 股股票的代码前缀白名单。
# ⚠️ 必须是白名单, 不能是"排除指数/排除基金"的黑名单。实测教训: 原先只排除
# sh000/sz399 两个指数前缀, 而 baostock 的 query_all_stock 从 2026-01-05 起开始
# 连场内基金一起返回 —— 1400 只 ETF/LOF 直接进了股票池 (sz15 585 只、sh51 418 只、
# sh56 204 只、sh58 119 只...), 而且排序上 sh51 在 sh60 前面, 抓取的头 8 块 958 只
# 几乎全是基金, 11 分钟全花在没有连板概念的品种上。
# 黑名单永远落后于数据源加品种 (基金之后还有债、REITs、优先股), 白名单则是数据源
# 加什么都不受影响 —— 新品种默认落在外面, 而不是默认混进来。
_STOCK_PREFIXES = (
    'sh60',    # 沪主板 600/601/603/605
    'sh68',    # 科创板 688
    'sz00',    # 深主板+中小板 000/001/002/003
    'sz30',    # 创业板 300/301
)


def is_stock(code: str) -> bool:
    """是不是 A 股股票 (排除指数/基金/债/B 股)。

    和 is_supported 是两个不同的问题: is_supported 问交易所有没有覆盖 (北交所没有),
    is_stock 问这个代码是什么品种。B 股 (sh900/sz200) 也在外面 —— 它有涨跌幅限制但
    不参与连板生态, 放进来只会给中位数注水。
    """
    return to_bare(code).lower().startswith(_STOCK_PREFIXES)


# ---------------------------------------------------------------- 涨停判据

def board_limit(bare_code: str, is_st: bool) -> Decimal | None:
    """这只票当日的涨跌幅限制。返回 None = 判不了 (北交所)。"""
    c = bare_code.lower()
    if c.startswith('sz30') or c.startswith('sh688'):
        # 创业板/科创板: 20%, ST 不降档 (已抽样验证)
        return LIMIT_GROWTH
    if c.startswith('sh6') or c.startswith('sz00'):
        return LIMIT_MAIN_ST if is_st else LIMIT_MAIN
    return None


def limit_price(preclose: Decimal, limit: Decimal) -> Decimal:
    """交易所算涨停价: 四舍五入到分。

    必须用 Decimal + ROUND_HALF_UP。Python 内置 round() 是银行家舍入,
    round(2.615, 2) 给 2.61 而交易所给 2.62 —— 一分钱之差就是一次误判。
    """
    return (preclose * (Decimal(1) + limit)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def is_limit_up(bare_code: str, close: float, preclose: float, is_st: bool) -> bool | None:
    """收盘是否精确封在涨停价。返回 None = 判不了 (缺分母 / 不支持的板块)。"""
    if not preclose or preclose <= 0 or not close or close <= 0:
        return None
    limit = board_limit(bare_code, is_st)
    if limit is None:
        return None
    want = limit_price(Decimal(str(preclose)), limit)
    # 价格都是两位小数, 半分钱的容差等价于精确相等, 只是躲开浮点表示误差。
    return abs(Decimal(str(close)) - want) < Decimal('0.005')


# ---------------------------------------------------------------- 抓取

@dataclass
class FetchResult:
    code: str
    rows: int
    status: str          # ok / empty / mislabel / error / skip
    detail: str = ''


def _bar_path(bare_code: str) -> str:
    return os.path.join(BAOSTOCK_BAR_DIR, f'{bare_code}.csv.gz')


def has_bars(bare_code: str) -> bool:
    """逐股一个文件 = 天然可续跑, 文件存在即已抓过。"""
    return os.path.exists(_bar_path(bare_code))


def _write_bars(bare_code: str, df: pd.DataFrame) -> None:
    """先写临时文件再原子替换。**不能直写目标路径。**

    因为超时块是 `pool.terminate()` 强杀的, 直写的话 worker 可能正好停在写一半:
    留下一个残缺的 gz。之后 `has_bars()` 只看文件存在就判"已抓", 这只票永远不会
    被续跑补上; 而 `read_bars()` 的 except 会把解析失败悄悄吞成 None ——
    结果是这只票从重建里凭空消失, 且没有任何日志。
    os.replace 在同盘是原子的: 要么是完整的旧文件, 要么是完整的新文件。
    """
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    final = _bar_path(bare_code)
    tmp = f'{final}.tmp{os.getpid()}'
    try:
        with gzip.open(tmp, 'wt', encoding='utf-8') as fh:
            fh.write(buf.getvalue())
        os.replace(tmp, final)
    except BaseException:
        # BaseException: 强杀走的是 SystemExit/KeyboardInterrupt, 也要清掉临时文件
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def read_bars(bare_code: str) -> pd.DataFrame | None:
    p = _bar_path(bare_code)
    if not os.path.exists(p):
        return None
    try:
        return pd.read_csv(p, dtype={'code': str, 'date': str})
    except Exception:
        return None


def fetch_one(bs, bare_code: str, start: str, end: str) -> FetchResult:
    """抓一只票的日线并落库。

    ⚠️ 必须用返回行的 row[1] 裸码校验。baostock 批量抓取偶发返回相邻代码的 K 线
    (memory baostock-batch-code-mislabel), 不校验的话股票 A 的真值会被写成股票 B 的
    收盘, 而且事后没有任何自动判据能查出来。串号一律整只丢弃, 不做部分保留。
    """
    if not is_supported(bare_code):
        return FetchResult(bare_code, 0, 'skip', 'baostock 无北交所覆盖')

    dotted = to_dotted(bare_code)
    try:
        rs = bs.query_history_k_data_plus(
            dotted, BAR_FIELDS,
            start_date=start, end_date=end, frequency='d', adjustflag='3',
        )
    except Exception as exc:                                  # pragma: no cover
        return FetchResult(bare_code, 0, 'error', str(exc)[:120])

    if rs is None or rs.error_code != '0':
        return FetchResult(bare_code, 0, 'error', getattr(rs, 'error_msg', 'unknown')[:120])

    rows, bad = [], 0
    while rs.next():
        row = rs.get_row_data()
        if len(row) < 8:
            continue
        if to_bare(row[1]) != bare_code:      # 串号校验, 不可省
            bad += 1
            continue
        rows.append(row)

    if bad:
        return FetchResult(bare_code, 0, 'mislabel', f'{bad} 行返回了别的代码, 整只丢弃')
    if not rows:
        return FetchResult(bare_code, 0, 'empty')

    df = pd.DataFrame(rows, columns=BAR_FIELDS.split(','))
    df['code'] = bare_code
    for col in ('close', 'preclose', 'pctChg'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0).astype('int64')
    _write_bars(bare_code, df)
    return FetchResult(bare_code, len(df), 'ok')


def fetch_many(codes, start: str, end: str, *, skip_existing: bool = True,
               progress_every: int = 200, log=print) -> pd.DataFrame:
    """按顺序抓一批票。返回逐股状态表 (同时落 BAOSTOCK_FETCH_STATUS)。

    串行是故意的: baostock 单连接, 并发只会被服务端掐断, 而逐股续跑本来就便宜。
    """
    import baostock as bs

    lg = bs.login()
    if lg.error_code != '0':                                  # pragma: no cover
        raise RuntimeError(f'baostock 登录失败: {lg.error_msg}')

    results: list[FetchResult] = []
    t0 = time.time()
    try:
        for i, raw in enumerate(codes, start=1):
            code = to_bare(raw)
            if skip_existing and has_bars(code):
                results.append(FetchResult(code, 0, 'cached'))
            else:
                results.append(fetch_one(bs, code, start, end))
            if progress_every and i % progress_every == 0:
                done = time.time() - t0
                ok = sum(1 for r in results if r.status == 'ok')
                log(f'  [{i}/{len(codes)}] 新抓 {ok} 只, 已用 {done/60:.1f} 分, '
                    f'预计还需 {done/i*(len(codes)-i)/60:.1f} 分')
    finally:
        bs.logout()

    status = pd.DataFrame([r.__dict__ for r in results])
    status.to_csv(BAOSTOCK_FETCH_STATUS, index=False, encoding='utf-8-sig')
    return status


# 每只票的超时预算, 块超时 = 块内票数 × 这个数 (有下限)。
# ⚠️ 别改回"一块一个固定秒数"。实测教训: 6 只票一块、固定 300s, 整块超时被丢 ——
# 因为出错重试把单只票的最坏情况翻倍了 (socket 超时 + 重登 + 再一次),
# 而固定预算不随块大小变。按 chunk=120 算, 固定 300s 会让**每一块**都超时,
# 全量跑下来一只都抓不到。
#
# 预算怎么定的 (实测: 健康响应 0.1~1.2s, 失败一只 = socket 超时 ×2 + 重登):
# 关键是**续跑块天生全是失败票** —— 它本来就是上一轮没抓到的那批, 所以"每只都
# 走最坏路径"是它的常态而不是极端情况。照最坏值放大外层预算会变成 chunk=120 要
# 等 90 分钟, 那超时就废了 (它的本职是抓住空转的 worker)。
# 所以改成两头收: socket 超时压到 10s (健康响应的 8 倍还多, 不会误杀),
# worker 自己带软截止时间提前交还部分结果 —— 外层预算就不必为最坏情况买单。
SOCKET_TIMEOUT = 10
PER_CODE_TIMEOUT = 25
MIN_CHUNK_TIMEOUT = 180

# worker 软截止 = 外层预算 × 这个系数。留出的余量用来把已抓到的状态回传, 而不是
# 被 terminate() 连状态一起吞掉 (K 线文件早就落盘了, 丢的是"哪只没抓到"的记录)。
WORKER_DEADLINE_RATIO = 0.8


@contextlib.contextmanager
def _silenced():
    """baostock 的 login/logout 直接往 stdout 打字, 多进程下会把进度日志搅烂。"""
    old = sys.stdout
    sys.stdout = open(os.devnull, 'w')
    try:
        yield
    finally:
        sys.stdout.close()
        sys.stdout = old


def _relogin(bs) -> None:
    """重建连接。**这是串号的解药, 不只是重试。**

    实测证据 (40 只测试, 前 4 只报"网络接收错误", 紧接着 2 只报串号且各 492 行,
    之后 34 只全对): baostock 的串号不是随机返回相邻代码, 而是**连接错位** ——
    一次请求失败后, 上一条响应的字节还留在 socket 里没读完, 下一次 query 读到的
    就是前一只票的 K 线。所以错位一旦发生会顺着同一条连接一直传下去。
    出错就把连接扔掉重登, 错位无法蔓延; row[1] 校验仍然留着当兜底。
    """
    with _silenced():
        try:
            bs.logout()
        except Exception:                                     # pragma: no cover
            pass
        bs.login()


def _fetch_chunk(args):
    """进程池 worker: 抓一批票并各自落库, 只把状态回传主进程。

    必须是模块级函数 (要能被 pickle)。每只票自己写自己的 gz 文件, 所以 worker
    之间没有共享状态, 强杀某个 worker 也只丢它手里那一块。
    """
    codes, start, end = args
    import socket
    socket.setdefaulttimeout(SOCKET_TIMEOUT)
    import baostock as bs

    with _silenced():
        bs.login()

    # 软截止: 自己算, 和主进程 budget 用同一个公式同一批常量, 不靠参数传 (传了就会
    # 有一天两边算出不同的数)。到点主动收工把已有状态交回去 —— 外层 terminate()
    # 是连返回值一起丢的, 那样"哪只没抓到"就只能靠下轮 has_bars() 反推。
    deadline = time.time() + max(MIN_CHUNK_TIMEOUT, len(codes) * PER_CODE_TIMEOUT) \
        * WORKER_DEADLINE_RATIO

    out = []
    for n, code in enumerate(codes):
        if time.time() > deadline:
            out.extend({'code': c, 'rows': 0, 'status': 'deadline', 'detail': ''}
                       for c in codes[n:])
            break
        try:
            r = fetch_one(bs, code, start, end)
        except Exception as exc:                              # pragma: no cover
            r = FetchResult(code, 0, 'error', str(exc)[:120])
        # 出错/串号说明连接已经错位, 重登再补一次这只票。补不回来也不硬扛, 交给下轮续跑。
        if r.status in ('error', 'mislabel'):
            _relogin(bs)
            try:
                r2 = fetch_one(bs, code, start, end)
                if r2.status == 'ok':
                    r = r2
            except Exception:                                 # pragma: no cover
                _relogin(bs)
        out.append(r.__dict__)

    with _silenced():
        bs.logout()
    return out


def fetch_many_parallel(codes, start: str, end: str, *, skip_existing: bool = True,
                        cores: int = 4, chunk: int = 40, log=print) -> pd.DataFrame:
    """多进程抓取。5900 只票串行要 ~2 小时, 4 进程 ~30 分钟。

    ⚠️ 用 apply_async + 逐块 `.get(timeout)`, 不用 imap_unordered。原因在
    tools/backfill_price_gap.py 记着: 某些畸形响应会让 `rs.next()` 在 C 层读循环里
    100% CPU 空转, socket 超时管不住它, 池关闭会永久等下去 (曾卡死 9 小时)。
    超时即放弃该块并 terminate 强杀, 漏掉的票下一轮续跑补上 —— 逐股文件让这件事
    是免费的。

    chunk 为什么是 40 而不是 120: **块大小是空转代价的唯一旋钮**。空转卡在 C 层
    读循环里, worker 自己的软截止只在每只票之间检查, 管不了卡在一只票中间的情况
    (Windows 也没有 SIGALRM 能从里面打断它), 所以只能靠外层超时兜 —— 而外层预算
    = 块内票数 × PER_CODE_TIMEOUT, 于是 chunk=120 就是"每次空转白等 50 分钟"。
    实测: 41 块的全量跑第 13 块空转, 一块吃掉 50 分钟, 占全程一半。
    chunk=40 把这个数压到 17 分钟, 代价只是多几次 login (~1s 一次, 可忽略)。
    健康块 40 只 × 1.2s = 48s 就跑完, 预算 1000s 是它的 20 倍, 不会误杀。
    """
    import multiprocessing

    todo = [to_bare(c) for c in codes]
    # 两道都要过: 交易所有覆盖 (is_supported) + 品种是股票 (is_stock)。少了后一道,
    # 名单里混进来的场内基金会照抓 —— 而且排序上 sh51 在 sh60 前面, 头几块全是基金。
    todo = [c for c in todo if is_supported(c) and is_stock(c)]
    cached = [c for c in todo if skip_existing and has_bars(c)]
    todo = [c for c in todo if not (skip_existing and has_bars(c))]
    log(f'  待抓 {len(todo)} 只 (已缓存 {len(cached)} 只跳过)')
    if not todo:
        return pd.DataFrame([{'code': c, 'rows': 0, 'status': 'cached', 'detail': ''}
                             for c in cached])

    blocks = [todo[i:i + chunk] for i in range(0, len(todo), chunk)]
    results = [{'code': c, 'rows': 0, 'status': 'cached', 'detail': ''} for c in cached]
    t0 = time.time()
    total = len(blocks)
    pending: list[tuple[int, list[str]]] = list(enumerate(blocks, start=1))
    collected = 0

    # 超时就**重建整个池子**, 而不是接着用。
    # 实测教训 (41 块的全量跑, 第 13 块): 空转的 worker 卡在 C 层读循环里, 谁也叫不回
    # 来 —— 它不会因为主进程放弃这一块就退出。于是 4 进程的池子从此变 3 进程, 再卡一次
    # 变 2 进程, 越往后越慢, 而日志上看不出来 (每块照常报进度)。
    # 重建的代价只是"其余在飞的块要重投", 而重投几乎免费: 重投前按 has_bars 再筛一遍,
    # 已经落盘的票直接不进新块 (逐股一个文件让这件事免费, 和续跑同一个机制)。
    while pending:
        pool = multiprocessing.Pool(cores)
        stalled = False
        try:
            ars = [(i, blk, pool.apply_async(_fetch_chunk, ((blk, start, end),)))
                   for i, blk in pending]
            for k, (i, blk, ar) in enumerate(ars):
                budget = max(MIN_CHUNK_TIMEOUT, len(blk) * PER_CODE_TIMEOUT)
                try:
                    results.extend(ar.get(timeout=budget))
                except Exception as exc:
                    log(f'  ⚠️ 第 {i} 块超时/失败 ({str(exc)[:60]}), 放弃 {len(blk)} 只, '
                        f'下轮续跑; 重建进程池 (空转的 worker 不会自己回来)')
                    results.extend([{'code': c, 'rows': 0, 'status': 'timeout', 'detail': ''}
                                    for c in blk])
                    # 未收的块重投, 已落盘的票就地剔掉 (别人的 worker 可能已经抓完了)。
                    pending = [(j, [c for c in b if not has_bars(c)]) for j, b, _ in ars[k + 1:]]
                    pending = [(j, b) for j, b in pending if b]
                    stalled = True
                    break
                collected += 1
                done = time.time() - t0
                ok = sum(1 for r in results if r['status'] == 'ok')
                log(f'  [{collected}/{total}] 成功 {ok} 只, 已用 {done/60:.1f} 分, '
                    f'预计还需 {done/collected*(total-collected)/60:.1f} 分')
                # 状态逐块落盘: 整块跑完才写的话, 中途被 Ctrl-C / 杀进程就一行不留
                # (实测丢过一次 13 块的记录, 只能靠 has_bars 反推抓到哪儿了)。
                pd.DataFrame(results).to_csv(BAOSTOCK_FETCH_STATUS, index=False,
                                             encoding='utf-8-sig')
            if not stalled:
                pending = []
        finally:
            pool.terminate()   # 强杀 (含空转的 worker), 不 join

    status = pd.DataFrame(results)
    status.to_csv(BAOSTOCK_FETCH_STATUS, index=False, encoding='utf-8-sig')
    return status


def fetch_point_in_time_universe(dates, log=print, *, use_cache: bool = True) -> pd.DataFrame:
    """按月取一次全市场名单, 并集起来当"曾经存在过"的股票池。

    存在的理由: stock_universe.csv 只有当前在册的 5543 只、退市行数为 0。直接拿它
    重建长样本就是生存偏差 —— 那半年里退市/被并的票被静默剔掉, 而它们恰好常是
    炒到高位的那批。逐月抓一次 all_stock 能把这些票捞回来。

    落盘缓存不是可选项: 单次 query_all_stock 实测 30~70s, 逐月 24 次就是十几分钟,
    而这份名单一旦取到就不再变 (历史某天的在册名单是定值)。
    """
    want = sorted(str(d) for d in dates)
    if use_cache and os.path.exists(BAOSTOCK_UNIVERSE_PIT) and want:
        try:
            cached = pd.read_csv(BAOSTOCK_UNIVERSE_PIT, dtype=str)
            # 命中判据只看采样区间和采样次数 (逐日名单没有展开存)。这足够挡住重复
            # 抓取, 但**不保证**采样日逐个相同 —— 想换一套采样日请显式 use_cache=False。
            got_from = str(cached['sampled_from'].iloc[0])
            got_to = str(cached['sampled_to'].iloc[0])
            got_n = int(cached['sampled_n'].iloc[0])
            if got_from <= want[0] and got_to >= want[-1] and got_n >= len(want):
                log(f'  命中缓存: {cached["code"].nunique()} 只 '
                    f'({got_from}~{got_to}, {got_n} 个采样日)')
                return cached
            log(f'  缓存区间 {got_from}~{got_to} ({got_n} 个采样日) 不覆盖请求, 重抓')
        except Exception as exc:
            log(f'  缓存读取失败, 重抓: {exc}')

    import baostock as bs

    lg = bs.login()
    if lg.error_code != '0':                                  # pragma: no cover
        raise RuntimeError(f'baostock 登录失败: {lg.error_msg}')

    seen: dict[str, str] = {}
    done_dates: list[str] = []
    try:
        dates = _snap_to_trading_days(bs, dates)
        log(f'  采样日对齐到交易日: {len(dates)} 个')
        for d in dates:
            rs = bs.query_all_stock(day=d)
            if rs is None or rs.error_code != '0':
                log(f'  {d}: 失败 {getattr(rs, "error_msg", "?")}')
                continue
            n = 0
            dropped = 0
            while rs.next():
                row = rs.get_row_data()
                code = to_bare(row[0])
                # query_all_stock 返回的是"全部品种": 股票 + 指数 + 场内基金。
                # 走白名单 is_stock, 别改回"排除指数"那种黑名单 (理由在 _STOCK_PREFIXES)。
                if not is_supported(code) or not is_stock(code):
                    dropped += 1
                    continue
                seen.setdefault(code, d)
                n += 1
            done_dates.append(d)
            # 剔掉的个数要打出来: 它是白名单的自检。哪天数据源改了代码段, 这个数会
            # 突然跳一截 (2026-01-05 那天从 ~400 跳到 ~1800), 比事后翻并集容易发现。
            log(f'  {d}: {n} 只 (剔非股票 {dropped} 个), 累计 {len(seen)}')
    finally:
        bs.logout()

    out = (pd.DataFrame({'code': list(seen), 'first_seen': list(seen.values())})
           .sort_values('code').reset_index(drop=True))
    # 覆盖范围只存三个短字段 (起/止/个数), 不逐日展开 —— 展开是 5900 只 × 24 个
    # 采样日 = 14 万行, 存的还是同一份名单。
    if done_dates:
        # 存的是**请求区间**而不是实际采样到的区间。因为对齐只会把日期往后挪
        # (9/01 周日 → 9/02), 存实际值的话下次拿同一个区间来问, got_from(9/02)
        # <= want[0](9/01) 不成立 → 判缓存不命中 → 每次跑都白抓 20 分钟。
        out['sampled_from'] = want[0] if want else min(done_dates)
        out['sampled_to'] = want[-1] if want else max(done_dates)
        # 个数存的是"**是否把请求的采样密度covered满**", 不是实际请求次数。
        # 同理于上面: 对齐后去重会让采样日变少 (长假里两个月初撞到同一个交易日),
        # 26 个请求日可能只剩 24 个实际采样日。存 24 的话, 下次拿同一批日期来问
        # got_n(24) >= len(want)(26) 不成立 → 又是每次白抓 20 分钟。
        # 全部采样成功就记成请求数 (=已覆盖); 有失败才记实际数, 此时它必然小于
        # 请求数 (去重只会变少), 下次自动重抓 —— 这是想要的: 名单缺月等于生存
        # 偏差没补上, 宁可重抓也不能把带洞的并集缓存成"已完成"。
        out['sampled_n'] = len(want) if len(done_dates) == len(dates) else len(done_dates)
        if len(done_dates) < len(dates):
            log(f'  ⚠️ {len(dates) - len(done_dates)} 个采样日失败, '
                f'名单可能缺月, 下次运行会重抓')
        out.to_csv(BAOSTOCK_UNIVERSE_PIT, index=False, encoding='utf-8-sig')
    return out


def _snap_to_trading_days(bs, dates) -> list[str]:
    """把采样日对齐到真实交易日 (就近向后, 越界则向前)。

    ⚠️ 这不是"省几次白跑的请求", 而是修生存偏差本身。query_all_stock 对非交易日
    返回 0 行, 而逐月采样取的是**日历月初** —— 9/1 是周日, 加上元旦/十一/春节/五一,
    26 个采样日里有三分之一落在休市日。那些月份对并集的贡献是 0 只, 于是"在这个月
    里上市又退市"的票照样漏掉, 正是 pit 模式要捞回来的那批。
    整个窗口的交易日历一次查完 (一次请求), 比逐日试探便宜。
    """
    want = [str(d) for d in dates]
    rs = bs.query_trade_dates(start_date=min(want), end_date=max(want))
    if rs is None or rs.error_code != '0':                        # pragma: no cover
        return want
    cal: list[tuple[str, bool]] = []
    while rs.next():
        row = rs.get_row_data()
        cal.append((row[0], row[1] == '1'))
    open_days = [d for d, is_open in cal if is_open]
    if not open_days:                                             # pragma: no cover
        return want

    out: list[str] = []
    for d in want:
        fwd = [x for x in open_days if x >= d]
        out.append(fwd[0] if fwd else open_days[-1])
    # 去重保序: 两个采样日可能撞到同一个交易日 (长假), 撞了就只抓一次。
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- 重建

def reconstruct(codes=None, log=print) -> dict[str, pd.DataFrame]:
    """把已落库的 K 线重建成三张表: 涨停/连板历史、停牌表、长收盘价表。

    连板数的算法: 只在该股**实际交易**的行上连锁 (tradestatus=1)。停牌那几天不
    算断板也不算续板 —— 停牌期间没有交易, 复牌后接着数, 这和交易所口径一致。
    """
    if codes is None:
        codes = [f[:-7] for f in os.listdir(BAOSTOCK_BAR_DIR) if f.endswith('.csv.gz')]
    codes = sorted(to_bare(c) for c in codes)

    # 落库目录里有历史遗留的非股票文件 (白名单修好之前抓下来的 ETF), 重建这一步必须
    # 再过一次筛 —— 不能指望"目录里的就是该重建的"。基金没有涨停概念, 混进去会给
    # 涨停表注水, 也会把长收盘价表撑大。
    n_all = len(codes)
    codes = [c for c in codes if is_stock(c)]
    if n_all > len(codes):
        log(f'  剔掉 {n_all - len(codes)} 个非股票文件 (基金/指数), 重建 {len(codes)} 只')

    limit_rows, susp_rows, close_frames = [], [], []
    skipped_first = 0

    for i, code in enumerate(codes, start=1):
        df = read_bars(code)
        if df is None or df.empty:
            continue
        df = df.sort_values('date').reset_index(drop=True)
        df['tradestatus'] = df['tradestatus'].astype(str)
        df['isST'] = df['isST'].astype(str)

        # 停牌: tradestatus='0'。这就是那 2.17% 假平盘的真标记。
        susp = df[df['tradestatus'] == '0']
        for d in susp['date']:
            susp_rows.append({'code': code, 'date': d})

        close_frames.append(df[['code', 'date', 'close', 'preclose', 'pctChg',
                                'tradestatus', 'isST']])

        lb = 0
        for j, row in enumerate(df.itertuples(index=False)):
            if str(row.tradestatus) != '1':
                continue                     # 停牌日: 连板数原样挂着, 不断不续
            if j == 0:
                skipped_first += 1           # 上市首日不适用涨停判据
                continue
            zt = is_limit_up(code, row.close, row.preclose, str(row.isST) == '1')
            if zt:
                lb += 1
                limit_rows.append({'date': row.date, 'code': code, 'lb': lb,
                                   'close': row.close, 'preclose': row.preclose,
                                   'pctChg': row.pctChg, 'isST': int(str(row.isST) == '1')})
            else:
                lb = 0

        if i % 500 == 0:
            log(f'  重建 {i}/{len(codes)} 只')

    limit_df = (pd.DataFrame(limit_rows).sort_values(['date', 'code'])
                .reset_index(drop=True) if limit_rows else
                pd.DataFrame(columns=['date', 'code', 'lb', 'close', 'preclose', 'pctChg', 'isST']))
    susp_df = (pd.DataFrame(susp_rows).sort_values(['date', 'code'])
               .reset_index(drop=True) if susp_rows else
               pd.DataFrame(columns=['code', 'date']))
    close_df = (pd.concat(close_frames, ignore_index=True) if close_frames else
                pd.DataFrame(columns=['code', 'date', 'close', 'preclose', 'pctChg',
                                      'tradestatus', 'isST']))

    limit_df.to_csv(BAOSTOCK_LIMIT_HISTORY, index=False, encoding='utf-8-sig')
    susp_df.to_csv(BAOSTOCK_SUSPENSION, index=False, encoding='utf-8-sig')
    close_df.to_csv(BAOSTOCK_CLOSE_LONG, index=False, encoding='utf-8-sig')
    log(f'  涨停 {len(limit_df)} 行 / 停牌 {len(susp_df)} 行 / 收盘 {len(close_df)} 行 '
        f'(跳过 {skipped_first} 个上市首日)')
    return {'limit': limit_df, 'suspension': susp_df, 'close': close_df}


# ---------------------------------------------------------------- 对账

def validate_against_truth(limit_df: pd.DataFrame, zt_cache: pd.DataFrame,
                           log=print) -> pd.DataFrame:
    """在重叠窗口上把重建结果和涨停池真值逐日对账。

    真值池自己也漏票 (见模块头部), 所以这张表的用法是**看差异的成分**, 不是看
    准确率数字。判断标准: 重建多出来的票, 抽查后应当都是真涨停 (真值漏), 重建
    漏掉的票应当趋近于 0。
    """
    zt = zt_cache.copy()
    zt.columns = [str(c).strip() for c in zt.columns]
    date_col, type_col, code_col, lb_col = zt.columns[0], zt.columns[1], zt.columns[2], zt.columns[4]
    zt = zt[zt[type_col].astype(str).str.upper() == 'ZT']
    zt['d'] = zt[date_col].astype(str).str.replace('-', '', regex=False)
    zt['c'] = zt[code_col].astype(str).str.lower()

    rec = limit_df.copy()
    rec['d'] = rec['date'].astype(str).str.replace('-', '', regex=False)

    rows = []
    for d, g in zt.groupby('d'):
        # 北交所整段没覆盖, 对账时先剔掉, 否则每天都记一笔假的漏抓。
        truth = {c for c in g['c'] if is_supported(c)}
        mine = set(rec[rec['d'] == d]['code'])
        if not mine and not truth:
            continue
        rows.append({'date': d, 'truth': len(truth), 'rebuilt': len(mine),
                     'both': len(truth & mine), 'only_rebuilt': len(mine - truth),
                     'only_truth': len(truth - mine),
                     'sample_only_truth': ','.join(sorted(truth - mine)[:5]),
                     'sample_only_rebuilt': ','.join(sorted(mine - truth)[:5])})

    out = pd.DataFrame(rows).sort_values('date').reset_index(drop=True)
    if not out.empty:
        log(f'  对账 {len(out)} 天: 真值 {out["truth"].sum()} / 重建 {out["rebuilt"].sum()} / '
            f'交集 {out["both"].sum()} / 仅重建 {out["only_rebuilt"].sum()} / '
            f'仅真值 {out["only_truth"].sum()}')
    return out


# ---------------------------------------------------------------- 停牌集合

def load_suspension_set() -> set[tuple[str, str]]:
    """停牌股票日集合 `{(裸码, YYYYMMDD)}`, 给下游剔假平盘用。

    日期在这里就归一化成 8 位, 不留给调用方。停牌表落盘是 `2026-04-29` 而价格
    缓存读进来是 `20260429` —— 两边不统一的话掩码一条都匹配不上, 而且失败方式是
    静默的 (剔除 0 行, 看起来像"本来就没有停牌"), 不会报错。
    """
    if not os.path.exists(BAOSTOCK_SUSPENSION):
        return set()
    df = pd.read_csv(BAOSTOCK_SUSPENSION, dtype=str)
    if df.empty or 'code' not in df.columns or 'date' not in df.columns:
        return set()
    code = df['code'].astype(str).str.strip().str.lower()
    date = df['date'].astype(str).str.replace('-', '', regex=False).str.strip()
    return set(zip(code, date))
