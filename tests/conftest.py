# -*- coding: utf-8 -*-
"""测试套件的统一数据隔离装置 (2026-09-17)。

**为什么需要**

单测会跑真实流水线, 于是会顺手写掉**生产缓存**、并在缓存不够新时**真的联网重拉**。
实测踩过三次, 而且每次都是间歇性的:

| 被写的生产文件 | 写入者 |
|---|---|
| `data/fetch_status.csv` | `主线强度追踪._record_price_gap_fetch_status` |
| `data/ths_sector_hist.json` | `phase_resonance.fetch_sectors` |
| `data/cninfo_announcement_cache.csv` / `…negative…` | `dragon_succession._save_ann_cache` |

**为什么会漏**: 这些缓存路径大多是**模块级常量**(`from paths import XXX_CACHE` 在 import 时绑定),
所以 `monkeypatch.setattr(paths, 'XXX_CACHE', …)` 是**无效**的, 必须打在消费模块上 ——
反直觉, 于是每个碰流水线的用例都得自己记得重定向它可能碰到的每一个缓存, 漏一个就漏一个。
触发条件又只在"缓存覆盖不住请求日期"时成立, 所以刚跑完日常批就复现不了。

**做法**

在**任何被测模块被 import 之前**(pytest 先导入 conftest, 再导入测试模块), 把 `data/` 的工作集
拷到一个临时目录的 `data` 子目录下, 然后用 `QF_DATA_DIR` 把它指过去。

**能保证什么, 不能保证什么** (别把话说满):

- ✅ **保证不再改写生产数据** —— 所有写入都落在临时副本里。这是本装置的核心目的,
  实测全量回归前后对 `data/` 下 70 个文件做 mtime 比对: **零改动**(改造前会动 3 个文件)。
- ⚠️ **不保证完全不发网络请求** —— 副本跟生产缓存一样有"右端", 若某个用例请求的日期越过它,
  仍然会联网重拉 —— 只是**落在副本里**。要彻底断网得另加桩替换 fetcher, 那是另一件事。
- 顺带的好处是**结果确定**: 缓存状态不再取决于"今天跑到哪一天", 也就不会出现
  "跑完日常批就复现不了"的间歇性。

三个细节都是有原因的, 改动前请先读:

1. **副本必须叫 `data`** —— `tests/test_smoke.py::test_paths_module_single_source` 断言
   "缓存路径以 `data` 结尾", 换个名字就会打红它。
2. **拷副本, 不是指到空目录** —— 空目录会让"缓存覆盖不足"成立, 反而**强制联网重拉**, 比原来更糟。
3. **排除 `*.bak.*` 与 `*.candidate.csv`** —— 它们占了 `data/` 体积的绝大部分
   (779MB → ~100MB), 且测试用不到。

临时目录默认在退出时清理; 调试时设 `QF_KEEP_TEST_DATA=1` 保留。
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_REAL_DATA = _ROOT / "data"
_KEEP_TEMP = os.environ.get("QF_KEEP_TEST_DATA") == "1"


def _isolate_data_dir():
    if os.environ.get("QF_DATA_DIR"):
        return                      # 调用方已指定 (例如外部 harness)
    if "paths" in sys.modules:
        # 已经有人把 paths 拉进来了 —— 这时改环境变量也来不及, 常量早绑定了。
        print("[conftest] 警告: paths 已被 import, 跳过 data 目录隔离")
        return
    if not _REAL_DATA.is_dir():
        return

    root = Path(tempfile.mkdtemp(prefix="qf-test-"))
    target = root / "data"
    shutil.copytree(
        _REAL_DATA,
        target,
        ignore=shutil.ignore_patterns("*.bak.*", "*.candidate.csv", "*.tmp"),
        dirs_exist_ok=True,
    )
    os.environ["QF_DATA_DIR"] = str(target)
    print(f"[conftest] data 已隔离到 {target}")

    if not _KEEP_TEMP:
        def _cleanup():
            try:
                shutil.rmtree(root, ignore_errors=True)
            except BaseException:
                pass            # 清理失败不该影响测试结论 (沙箱守卫会拦批量删除)
        atexit.register(_cleanup)


_isolate_data_dir()


# ---------------------------------------------------------------------------
# 可选: 联网守卫 (QF_BLOCK_NETWORK=1)
# ---------------------------------------------------------------------------
# 上面的隔离装置保证"不写生产数据", 但**不保证不发网络请求** —— 副本也有"右端",
# 用例请求的日期越过它仍会联网重拉(只是落在副本里)。而"测试偷偷联网"这件事本身有害:
# 慢、不确定、在没网的 CI 上会挂, 而且**静默** —— 实测就是它导致三个缓存被改写,
# 我却两次都没定位到具体是哪个用例。
#
# 所以留一个开关: 打开后任何真实连接都直接报错, traceback 直接指出是哪个用例在联网。
# 默认关闭, 不影响日常跑批。
if os.environ.get("QF_BLOCK_NETWORK") == "1":
    import socket as _socket

    _LOCAL = ("127.0.0.1", "::1", "localhost", "0.0.0.0")
    _real_connect = _socket.socket.connect
    _real_connect_ex = _socket.socket.connect_ex

    def _host_of(address):
        try:
            return str(address[0])
        except Exception:
            return str(address)

    def _blocked_connect(self, address):
        if _host_of(address) in _LOCAL:
            return _real_connect(self, address)
        raise RuntimeError(
            f"[QF_BLOCK_NETWORK] 这个用例试图联网: {address}\n"
            "测试不该依赖网络 —— 要么注入假 fetcher, 要么显式标注需要网络。"
        )

    def _blocked_connect_ex(self, address):
        if _host_of(address) in _LOCAL:
            return _real_connect_ex(self, address)
        raise RuntimeError(f"[QF_BLOCK_NETWORK] 这个用例试图联网: {address}")

    _socket.socket.connect = _blocked_connect
    _socket.socket.connect_ex = _blocked_connect_ex
    print("[conftest] 联网守卫已开启 (QF_BLOCK_NETWORK=1)")


# ---------------------------------------------------------------------------
# 可选: 生产数据写入监视 (QF_WATCH_DATA=1) —— 诊断用
# ---------------------------------------------------------------------------
# 隔离装置把症状藏起来了(写入被重定向到副本), 但**没有修掉"某个用例在做真实 I/O"这件事本身**。
# 打开这个开关后, 每个用例结束后检查真实 data/ 下被跟踪文件的 mtime, 谁改了就点名。
# 用法: QF_DATA_DIR=<真实 data 目录> QF_WATCH_DATA=1 pytest ...
if os.environ.get("QF_WATCH_DATA") == "1":
    import glob as _glob

    _WATCH_DIR = os.environ.get("QF_DATA_DIR") or str(_REAL_DATA)
    _WATCHED = sorted(
        _glob.glob(os.path.join(_WATCH_DIR, "*"))
    )
    _before = {p: os.path.getmtime(p) for p in _WATCHED if os.path.isfile(p)}

    def pytest_runtest_teardown(item, nextitem):
        changed = []
        for path, stamp in _before.items():
            if not os.path.isfile(path):
                changed.append(f"{os.path.basename(path)}(消失)")
                continue
            now = os.path.getmtime(path)
            if now != stamp:
                _before[path] = now
                changed.append(os.path.basename(path))
        if changed:
            print(f"\n[QF_WATCH_DATA] {item.nodeid} 改写了生产数据: {', '.join(changed)}")
