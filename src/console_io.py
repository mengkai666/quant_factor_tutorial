# -*- coding: utf-8 -*-
"""控制台输出编码统一 (Windows GBK 地雷的唯一解药)。

为什么需要:
    本项目的 print 里到处是 emoji (✅ ❌ ⚠️ 📂 ...)。Windows 中文环境下,
    stdout **接到控制台**时 Python 用 UTF-8 没问题, 一旦被**重定向到文件或管道**
    就回落到 locale 编码 (GBK), 于是第一个 emoji 就炸:

        UnicodeEncodeError: 'gbk' codec can't encode character '\\U0001f4c2'

    这不是"输出乱码"而是**整个进程崩在第一行 print**, 后面的活一件没干。
    踩点: 写日志 (`> run.log`)、被别的脚本 subprocess 调用、进 CI 采集输出。
    2026-08-31 回补 08-28 价格缺口时就是这样一启动就挂 (backfill_price_history.py:459)。

用法:
    每个**入口脚本**(有 __main__ 的) 在 import 后调一次即可, 模块不用管:

        from console_io import enable_utf8_console
        enable_utf8_console()

    src/paths.py 在 import 时已经代调了一次 —— 凡是 `from paths import ...` 的
    脚本 (tools/ 下几乎全部) 都自动免疫, 不需要再显式调用。
"""
from __future__ import annotations

import sys

_APPLIED = False


def enable_utf8_console(errors: str = 'replace') -> bool:
    """把 stdout/stderr 切成 UTF-8。幂等, 失败静默 (绝不能因为它本身抛异常)。

    errors='replace' 而非 'strict': 极老的终端仍可能吞不下某些字形,
    宁可显示成 '?' 也不要让整个跑批因为一个装饰字符挂掉。

    返回是否真正生效 (已生效过再调返回 True, 环境不支持返回 False)。
    """
    global _APPLIED
    if _APPLIED:
        return True
    ok = False
    for stream_name in ('stdout', 'stderr'):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding='utf-8', errors=errors)
            ok = True
        except Exception:
            pass
    _APPLIED = ok
    return ok
