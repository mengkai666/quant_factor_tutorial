"""东财板块行情直连 (行业/概念 板块列表 + 板块指数日线)。

为什么不用 akshare: akshare 的 stock_board_industry_* 不带 User-Agent,
东财会直接 RemoteDisconnected 掉连接 (与 Clash 代理无关, 带 UA 后代理内外都通)。
本模块自带 UA + 重试, 走同一套 secid 规则:
  行业板块 fs=m:90 t:2, 概念板块 fs=m:90 t:3, K线 secid=90.<BKcode>
"""
import time

import requests

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
HEADERS = {'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/'}
UT = 'bd1d9ddb04089700cf9c27f6f7426281'

# 东财对本机的可达路径会变: 有时只有系统代理(Clash)通, 有时只有直连通。
# 两条路都建好 session, 谁通用谁, 并记住上次成功的那条优先试。
_S_PROXY = requests.Session()
_S_PROXY.headers.update(HEADERS)
_S_PROXY.trust_env = True

_S_DIRECT = requests.Session()
_S_DIRECT.headers.update(HEADERS)
_S_DIRECT.trust_env = False
_S_DIRECT.proxies = {'http': None, 'https': None}  # type: ignore

_ORDER = [_S_PROXY, _S_DIRECT]


def _get(url, params, retry=3, timeout=12):
    """按上次成功的路径优先, 两条路轮着试; 全失败才抛。"""
    global _ORDER
    last: Exception = RuntimeError('unreachable')
    for i in range(retry):
        for s in list(_ORDER):
            try:
                r = s.get(url, params=params, timeout=timeout)
                r.raise_for_status()
                j = r.json()
                if s is not _ORDER[0]:      # 记住这条通路, 下次先用
                    _ORDER = [s] + [x for x in _ORDER if x is not s]
                return j
            except Exception as e:
                last = e
        time.sleep(0.6 * (i + 1))
    raise last


def board_list(kind='industry'):
    """返回 [{'code': 'BK0447', 'name': '半导体', 'pct': 1.23}, ...]"""
    fs = 'm:90 t:2' if kind == 'industry' else 'm:90 t:3'
    out, pn = [], 1
    while True:
        j = _get('https://push2.eastmoney.com/api/qt/clist/get', {
            'pn': pn, 'pz': 100, 'po': 1, 'np': 1, 'ut': UT,
            'fltt': 2, 'invt': 2, 'fid': 'f3', 'fs': fs,
            'fields': 'f2,f3,f12,f14,f20,f62',
        })
        d = (j or {}).get('data') or {}
        diff = d.get('diff') or []
        if not diff:
            break
        for it in diff:
            out.append({'code': it.get('f12'), 'name': it.get('f14'),
                        'pct': it.get('f3'), 'mktcap': it.get('f20'),
                        'main_inflow': it.get('f62')})
        if len(out) >= (d.get('total') or 0):
            break
        pn += 1
        if pn > 30:
            break
    return out


def board_kline(bk_code, start='20260601', end='20260810', klt=101):
    """板块指数日线 -> [{'date','open','close','high','low','amount','pct'}]"""
    j = _get('https://push2his.eastmoney.com/api/qt/stock/kline/get', {
        'secid': f'90.{bk_code}', 'ut': UT, 'klt': klt, 'fqt': 1,
        'beg': start, 'end': end,
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
    })
    kl = ((j or {}).get('data') or {}).get('klines') or []
    rows = []
    for line in kl:
        p = line.split(',')
        rows.append({
            'date': p[0], 'open': float(p[1]), 'close': float(p[2]),
            'high': float(p[3]), 'low': float(p[4]),
            'volume': float(p[5]), 'amount': float(p[6]),
            'pct': float(p[8]) if len(p) > 8 else None,
        })
    return rows


def board_members(bk_code, page_size=200):
    """板块成分股 -> [{'code','name','pct','close','mktcap'}]"""
    out, pn = [], 1
    while True:
        j = _get('https://push2.eastmoney.com/api/qt/clist/get', {
            'pn': pn, 'pz': page_size, 'po': 1, 'np': 1, 'ut': UT,
            'fltt': 2, 'invt': 2, 'fid': 'f3', 'fs': f'b:{bk_code}',
            'fields': 'f2,f3,f12,f13,f14,f20,f62',
        })
        d = (j or {}).get('data') or {}
        diff = d.get('diff') or []
        if not diff:
            break
        for it in diff:
            mkt = it.get('f13')
            pre = 'sh' if mkt == 1 else 'sz'
            out.append({'code': f"{pre}{it.get('f12')}", 'name': it.get('f14'),
                        'pct': it.get('f3'), 'close': it.get('f2'),
                        'mktcap': it.get('f20')})
        if len(out) >= (d.get('total') or 0):
            break
        pn += 1
        if pn > 20:
            break
    return out
