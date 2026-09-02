# -*- coding: utf-8 -*-
"""源码必须能被 CI 的 Python 版本解析 (钉 2026-08-06 起线上决策看板静默失效)。

事故: src/decision_dashboard.py 的 `rows = [...]` 用了 PEP 701 (Python 3.12+) 才允许的
"f-string 内层复用外层引号" 写法。本机 3.13 跑得通, CI 是 3.10 → **import 期**
SyntaxError。三个调用点全包在 `except Exception` 里只打一行警告, 于是:
  - 主报告内嵌决策看板 section 消失 (报告体积少 ~250KB)
  - site/dashboards/ 归档停在 2026-08-07, 首页看板入口卡也不再渲染
  - 统一股票池 (write_today_focus_pool) 写不出来
报告照常发布、CI 全绿, 线上看板冻了近一个月。

判据不能只靠"本机能跑": CI 用哪个版本从 workflow 反查 (单一真源), 不在测试里写死。
"""
import ast  # noqa: F401  (保留: 供后续按需做更细的节点级判定)
import io
import os
import re
import sys
import token as token_mod
import tokenize

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WORKFLOW = os.path.join(_ROOT, '.github', 'workflows', 'daily_run.yml')
_SKIP_DIRS = {'.git', 'output', 'ai_quant_trade', '.venv', 'node_modules', '__pycache__'}
_TRIPLES = ("'''", '"""')
_PREFIX_CHARS = 'fFrRbBuU'


def ci_python_version():
    """CI 实际用的 Python 版本; 单一真源是 workflow 本身。"""
    with open(_WORKFLOW, encoding='utf-8') as handle:
        m = re.search(r"python-version:\s*['\"]?(\d+)\.(\d+)", handle.read())
    assert m, 'daily_run.yml 里读不到 python-version'
    return int(m.group(1)), int(m.group(2))


def _delimiter(tok_text):
    """从字符串 token 原文里取出定界符 (''' / \"\"\" / ' / \")。"""
    body = tok_text.lstrip(_PREFIX_CHARS)
    for q in _TRIPLES:
        if body.startswith(q):
            return q
    return body[:1]


def _scan_quote_reuse(src):
    """3.12+ 的 tokenizer 会把 f-string 拆成 FSTRING_START/…/FSTRING_END,
    替换字段里的表达式照常出普通 token —— 于是"内层字符串的定界符
    是否等于外层 f-string 的定界符"可以精确判定, 不会被隐式拼接误伤。"""
    f_start = getattr(token_mod, 'FSTRING_START', None)
    f_end = getattr(token_mod, 'FSTRING_END', None)
    if f_start is None:
        return []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    hits, stack = [], []
    for tok in toks:
        if tok.type == f_start:
            q = _delimiter(tok.string)
            if stack and q == stack[-1]:
                hits.append((tok.start[0], f'嵌套 f-string 复用定界符 {q}'))
            stack.append(q)
        elif tok.type == f_end:
            if stack:
                stack.pop()
        elif stack and tok.type == token_mod.STRING:
            if _delimiter(tok.string) == stack[-1]:
                hits.append((tok.start[0], f'内层复用外层定界符 {stack[-1]}: {tok.string[:60]}'))
    return hits


def find_offenders(path):
    """<3.12 的解释器上直接 compile (SyntaxError 即命中); 3.12+ 用 tokenize 精判。"""
    with open(path, encoding='utf-8') as handle:
        src = handle.read()
    if sys.version_info < (3, 12):
        try:
            compile(src, path, 'exec')
        except SyntaxError as exc:
            return [(exc.lineno or 0, f'SyntaxError: {exc.msg}')]
        return []
    return _scan_quote_reuse(src)


def iter_repo_sources():
    for base, dirs, files in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if name.endswith('.py'):
                yield os.path.join(base, name)


def test_sources_parse_under_ci_python():
    """CI < 3.12 时, 全仓不许出现 PEP 701-only 的 f-string。"""
    if ci_python_version() >= (3, 12):
        pytest.skip('CI 已升到 3.12+, PEP 701 写法可用')
    offenders = []
    for path in iter_repo_sources():
        for lineno, why in find_offenders(path):
            offenders.append(f'{os.path.relpath(path, _ROOT)}:{lineno}  {why}')
    assert not offenders, (
        'CI 的 Python 会在 import 期 SyntaxError (本机版本更高所以看不出来):\n  '
        + '\n  '.join(offenders)
    )


def test_detector_catches_the_original_regression(tmp_path):
    """探测器要能抓到事故原样写法, 否则上一条测试是哑的。"""
    if sys.version_info < (3, 12):
        pytest.skip('当前解释器 <3.12, 事故写法在这里就是纯 SyntaxError, 已由上一条覆盖')
    bad = tmp_path / 'bad.py'
    bad.write_text("x = f'<span>{d.get('k') or '兜底'}</span>'\n", encoding='utf-8')
    assert find_offenders(str(bad)), '探测器漏掉了事故原样写法'
    ok = tmp_path / 'ok.py'
    ok.write_text("y = f'<span>{d.get(\"k\") or \"兜底\"}</span>'\n", encoding='utf-8')
    assert not find_offenders(str(ok)), '修好的写法被误判为命中'
