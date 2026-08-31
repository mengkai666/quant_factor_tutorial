# -*- coding: utf-8 -*-
"""日报收尾的数据完整性自检 (让缓存空洞当场发声, 而不是过夜)。

为什么要有:
    `tools/audit_data_integrity.py` 判据齐全 (陈旧副本/覆盖缺口/休市日污染/
    A/D 残缺), 但它只在人想起来时手动跑。2026-08-28 本地漏跑一天, 价格缓存
    沪深整段是空的 (5538 只 → 333 只), 08-31 晚上人肉发现数据不对才查出来
    —— 中间隔了一个周末, 期间报告一直降级成 facts_only。

    所以日报每次跑完都过一遍体检: 有缺陷就打横幅 + 进程非零退出。

放在收尾而不是开头:
    体检失败不能拦住报告产出 (报告本身有 report-integrity 门禁负责降级披露),
    否则一个历史日的空洞会让当天直接断更。顺序是"先出报告, 再红灯"。

用法:
    from data_selfcheck import run_selfcheck
    defects = run_selfcheck()               # 返回缺陷类数, 0 = 干净

    python src/data_selfcheck.py            # CI / 手动: 缺陷即非零退出
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_ROOT = os.path.dirname(_HERE)
AUDIT_SCRIPT = os.path.join(_ROOT, 'tools', 'audit_data_integrity.py')

BANNER = '=' * 72

# 窗口兜底值 (交易日)。真正用的窗口由 _regime_window() 现算, 见那里的注释。
DEFAULT_WINDOW = 15
MIN_WINDOW, MAX_WINDOW = 10, 60
# 与 audit_data_integrity.MIN_COVERAGE_RATIO 对齐: 低于基准这个比例算覆盖缺口
_COVERAGE_RATIO = 0.9


def _window_from_levels(dates: list[str], levels: dict[str, int]) -> int:
    """窗口计算的纯逻辑部分 (给测试用): dates 升序, levels 是每天的有效覆盖只数。

    从最后一天往回走, 遇到低于 `最新一天 × _COVERAGE_RATIO` 的天先记着继续走,
    **连续两天**低才认定是口径台阶并收在台阶之上; 单日塌陷 (真空洞) 仍留在窗口里。
    """
    if len(dates) < MIN_WINDOW:
        return max(1, len(dates))
    floor = levels.get(dates[-1], 0) * _COVERAGE_RATIO
    window, below_streak = 1, 0
    for day in reversed(dates[:-1]):
        if levels.get(day, 0) < floor:
            below_streak += 1
            if below_streak >= 2:      # 连续两天低 → 是台阶, 收在台阶之上
                window -= 1            # 把先记下的那一天也吐回去
                break
        else:
            below_streak = 0
        window += 1
    return max(MIN_WINDOW, min(MAX_WINDOW, window))


def _regime_window(default: int = DEFAULT_WINDOW) -> tuple[int, str]:
    """现算自检窗口: 从最新一天往回走, 停在**证券口径台阶**处。返回 (天数, 说明)。

    为什么不能写死一个天数:
        体检的覆盖判据拿窗口内**行数中位数**当基准, 而本项目的证券口径是阶梯式扩过来的
        (~4604 只 → 07-06 起 5199 → 08-06 起 raw 补齐到 5538, 每次换/加数据源涨一档)。
        窗口一旦跨过某道台阶, 老口径那半段就被新口径的中位数判成"覆盖不足" ——
        于是自检天天红。一个永远红的闸门等于没有闸门, 下次真出事没人会看。

    怎么区分"台阶"和"空洞" (两者都是行数变少, 但处理方式相反):
        台阶会**持续**, 空洞只是**一天塌下去** —— 判别规则见 _window_from_levels。
        这样 2026-08-28 那种单日空洞仍然留在窗口里被体检抓到, 而 08-06 之前的老口径
        整段被自动切掉。

        每天取 all 覆盖与 raw 覆盖的**较小值**: 体检对两者各判一次, 谁先掉档都会红。
    """
    try:
        import pandas as pd
        from paths import PRICE_CACHE
        if not os.path.exists(PRICE_CACHE):
            return default, '价格缓存不存在, 用兜底窗口'
        frame = pd.read_csv(
            PRICE_CACHE, dtype={'code': str},
            usecols=['code', 'date', 'close_raw'], low_memory=False,
        )
        all_cnt = frame.groupby('date')['code'].nunique()
        raw_cnt = frame.loc[frame['close_raw'].notna()].groupby('date')['code'].nunique()
        dates = sorted(all_cnt.index)
        levels = {day: min(int(all_cnt.get(day, 0)), int(raw_cnt.get(day, 0)))
                  for day in dates}
        window = _window_from_levels(dates, levels)
        if not dates:
            return default, '价格缓存为空, 用兜底窗口'
        return window, f'口径自适应, 最新一天 {levels[dates[-1]]} 只'
    except Exception as exc:               # 算窗口失败绝不能拖垮自检本身
        return default, f'窗口现算失败 ({type(exc).__name__}), 用兜底窗口'


def run_selfcheck(recent: int | None = None, as_of: str | None = None,
                  quiet: bool = True) -> int:
    """跑一次缓存体检, 原样透出输出, 返回缺陷类数 (0 = 通过, -1 = 体检自身没跑起来)。

    recent=None 时窗口由 _regime_window() 现算; 传数字则照传 (调试用)。
    只看近段而非全量: 收尾自检要快, 且窗口不能跨证券口径台阶。
    全量体检 (含历史积压) 仍然靠手动 `python tools/audit_data_integrity.py`。
    """
    print()
    print(BANNER)
    print('  收尾自检: 数据缓存完整性')
    print(BANNER)
    if not os.path.exists(AUDIT_SCRIPT):
        print(f'  ⚠️ 体检脚本不存在, 跳过: {AUDIT_SCRIPT}')
        return -1

    if recent is None:
        recent, why = _regime_window()
        print(f'  窗口: 近 {recent} 交易日 ({why})')
    else:
        print(f'  窗口: 近 {recent} 交易日 (显式指定)')

    cmd = [sys.executable, AUDIT_SCRIPT]
    if recent and recent > 0:
        cmd += ['--recent', str(recent)]
    if as_of:
        cmd += ['--as-of', str(as_of)]
    if quiet:
        cmd.append('--quiet')

    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    try:
        proc = subprocess.run(
            cmd, cwd=_ROOT, env=env, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=900,
        )
    except Exception as exc:
        print(f'  ⚠️ 体检没跑起来 ({type(exc).__name__}: {exc}), 不阻断日报')
        return -1

    for line in (proc.stdout or '').splitlines():
        print(f'  {line}' if line.strip() else '')
    if proc.stderr and proc.stderr.strip():
        # pandas 的 DtypeWarning 占两行 (告警行 + 回显的源码行), 两行一起丢掉,
        # 否则光滤告警行会剩下一句裸 `price_df = pd.read_csv(...)` 挂在日志里。
        noise = ('DtypeWarning', 'low_memory', 'Warning:')
        tail, skip_next = [], False
        for line in proc.stderr.strip().splitlines():
            if any(key in line for key in noise):
                skip_next = True
                continue
            if skip_next:
                skip_next = False
                continue
            tail.append(line)
        if tail:
            print(f'  [体检 stderr] {" / ".join(tail)[:500]}')

    if proc.returncode == 0:
        print('  ✅ 缓存体检通过, 无空洞')
        return 0
    defects = _count_defects(proc.stdout or '')
    print(BANNER)
    print(f'  ❌ 缓存体检未通过 ({defects or "若干"} 类缺陷) —— 本次跑批以非零码退出')
    print('     修复入口: python tools/audit_data_integrity.py     (看完整判据与修复提示)')
    print('               python tools/backfill_price_history.py --repair-days <缺的天> --apply')
    print('               python tools/reconcile_sentiment_ad.py --apply')
    print(BANNER)
    return defects or 1


def _count_defects(stdout: str) -> int:
    """从体检输出里取"发现 N 类缺陷"的 N; 取不到返回 0 让调用方回落到 1。"""
    import re
    hit = re.search(r'发现\s*(\d+)\s*类缺陷', stdout)
    return int(hit.group(1)) if hit else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='数据缓存完整性收尾自检 (缺陷即非零退出)')
    ap.add_argument('--recent', type=int, default=None,
                    help='窗口交易日数; 默认按证券口径台阶自适应')
    ap.add_argument('--as-of', default=None, help='以该日期为截止日体检')
    ap.add_argument('--verbose', action='store_true', help='透出体检明细 (默认折叠)')
    args = ap.parse_args(argv)
    defects = run_selfcheck(recent=args.recent, as_of=args.as_of, quiet=not args.verbose)
    if defects > 0 and os.environ.get('GITHUB_ACTIONS') == 'true':
        print('::error title=数据缓存体检未通过::'
              f'{defects} 类缺陷, 详见上方明细; '
              '修复: python tools/backfill_price_history.py --repair-days <缺的天> --apply')
    return 1 if defects > 0 else 0


if __name__ == '__main__':
    sys.exit(main())
