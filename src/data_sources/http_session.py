# -*- coding: utf-8 -*-
"""线程本地 HTTP 会话池 (性能基础设施)。

为什么需要:
    腾讯/东财的逐股接口每天要打上万次 (板块归因 ~2500 只 + 价格补缺 ~5500 只)。
    原先每个 fetcher 内部 `requests.Session()` 现开现用, 每次请求都要重做
    DNS + TCP + TLS 握手 —— 实测东财板块接口单请求 p50 从 0.05s 抬到 0.49s,
    吞吐 25/s 掉到 6.9/s (整整 3.6 倍)。握手时间占了绝大部分墙上时间。

做法:
    每个工作线程复用一条带连接池的 Session (keep-alive), 线程结束自然回收。
    trust_env=False + proxies=None 保持与既有腾讯快速路径一致 —— 本机 Clash
    白名单不含这些域名, 走代理会被限流/拒 (见 price-cache-tencent-fastpath)。

用法:
    from .http_session import get_session
    resp = get_session().get(url, params=..., timeout=8)
    # 不要 close(): 会话按线程复用, 关掉就退化成原来的每请求握手。
"""
from __future__ import annotations

import threading

import requests
from requests.adapters import HTTPAdapter

_local = threading.local()

# 单线程串行发请求, 连接池给 4 条足够 (同域名 keep-alive 复用同一条)。
_POOL_SIZE = 4


def get_session() -> requests.Session:
    """返回当前线程复用的 Session (首次调用时创建)。"""
    session = getattr(_local, 'session', None)
    if session is not None:
        return session
    session = requests.Session()
    session.trust_env = False
    session.proxies = {'http': None, 'https': None}  # type: ignore[assignment]
    adapter = HTTPAdapter(
        pool_connections=_POOL_SIZE, pool_maxsize=_POOL_SIZE, max_retries=0,
    )
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    _local.session = session
    return session


def reset_session() -> None:
    """丢弃当前线程的会话 (仅测试/异常恢复用)。"""
    session = getattr(_local, 'session', None)
    if session is not None:
        try:
            session.close()
        except Exception:
            pass
    _local.session = None
