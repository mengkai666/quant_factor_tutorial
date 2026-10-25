"""一键把打包好的 APK 装进桌面模拟器并启动验证 (install_apk.py)

用途：`build_apk.py` 出包之后，不想手点模拟器，直接跑这个脚本完成
  1. 自动定位雷电模拟器目录（含 adb.exe）
  2. 确认设备在线（必要时 adb connect）
  3. adb install -r 覆盖安装
  4. am start 启动 MainActivity
  5. 截图落到 output/apk/，并校验前台 Activity 真的是目标包

用法：
    python tools/install_apk.py                # 装默认产物 + 启动 + 截图
    python tools/install_apk.py --no-shot      # 只装不截图
    python tools/install_apk.py --tab dingpan  # 启动后自动切到盯盘 Tab 并截图

⚠️ 两个实测坑：
  1. **adb 守护进程会随 shell 一起被 SIGTERM**。每次 `adb` 调用都可能重新
     "daemon not running. starting it now"，因此本脚本用 subprocess 一次性
     串完所有命令，不依赖跨调用的守护进程状态。
  2. **`adb pull` 的目标路径必须是 Windows 风格**（`C:/...` 或 `C:\\...`），
     传 `/c/...` 会失败。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PACKAGE = "com.quant.terminal"
ACTIVITY = f"{PACKAGE}/.MainActivity"

DEFAULT_APK = PROJECT_ROOT / "output" / "apk" / "quant_factor_terminal.apk"
SHOT_DIR = PROJECT_ROOT / "output" / "apk"

# 常见模拟器安装位置，按顺序探测
EMULATOR_CANDIDATES = [
    Path(r"D:\Program Files (x86)\leidian\LDPlayer9"),
    Path(r"C:\Program Files (x86)\leidian\LDPlayer9"),
    Path(r"D:\Program Files\leidian\LDPlayer9"),
    Path(r"C:\LDPlayer\LDPlayer9"),
    Path(r"D:\Program Files (x86)\Netease\MuMuPlayer-12.0\shell"),
    Path(r"C:\Program Files\BlueStacks_nxt"),
]

# 底部 Tab 栏在 1920x1080 下的点击坐标（x, y）。截图是 1920 宽，
# 若改成别的分辨率需按比例换算。
TAB_COORDS = {
    "mood": (220, 1005),
    "mainline": (570, 1005),
    "dingpan": (956, 1005),
    "watch": (1345, 1005),
    "strategy": (1730, 1005),
}


def find_adb(explicit: str | None = None) -> Path:
    """定位可用的 adb.exe。"""
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit(f"指定的 adb 不存在: {p}")
        return p

    for root in EMULATOR_CANDIDATES:
        adb = root / "adb.exe"
        if adb.exists():
            return adb

    # 兜底：Android SDK 自带的
    sdk_adb = Path(r"C:\Users\mengk\AppData\Local\Android\Sdk\platform-tools\adb.exe")
    if sdk_adb.exists():
        return sdk_adb

    raise SystemExit("没找到 adb.exe，请用 --adb 指定路径")


def run(adb: Path, *args: str, check: bool = True, timeout: int = 180) -> str:
    """跑一条 adb 命令，返回 stdout+stderr。

    ⚠️ **绝对不能用 `capture_output=True`**。`adb` 首次调用会 fork 一个 adb server
    守护进程，该守护进程**继承父进程的 stdout/stderr 管道句柄**并且不退出，于是
    `subprocess.run` 永远等不到管道 EOF —— 表现为脚本无限挂起（实测 2 分钟无输出）。
    改用临时文件承接输出，父进程只看文件内容，与守护进程是否存活解耦。
    """
    cmd = [str(adb), *args]
    with tempfile.TemporaryFile(mode="w+b") as fh:
        proc = subprocess.run(
            cmd,
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
        fh.seek(0)
        out = fh.read().decode("utf-8", errors="replace")

    if check and proc.returncode != 0:
        raise SystemExit(f"命令失败: {' '.join(cmd)}\n{out}")
    return out


def ensure_device(adb: Path) -> str:
    """确保有一台在线设备，返回设备序列号。"""
    out = run(adb, "devices", check=False)
    serials = [
        line.split("\t")[0]
        for line in out.splitlines()
        if "\tdevice" in line and not line.startswith("List of")
    ]

    if not serials:
        print("[!] 没有在线设备，尝试连接 127.0.0.1:5555 …")
        run(adb, "connect", "127.0.0.1:5555", check=False)
        out = run(adb, "devices", check=False)
        serials = [
            line.split("\t")[0]
            for line in out.splitlines()
            if "\tdevice" in line and not line.startswith("List of")
        ]

    if not serials:
        raise SystemExit(
            "没有可用设备。请先启动模拟器（雷电：dnplayer.exe），再重跑本脚本。"
        )

    serial = serials[0]
    print(f"[OK] 设备在线: {serial}")
    return serial


def install(adb: Path, apk: Path, serial: str) -> None:
    if not apk.exists():
        raise SystemExit(f"APK 不存在: {apk}（先跑 tools/build_apk.py）")
    size_kb = apk.stat().st_size / 1024
    print(f"[..] 安装 {apk.name} ({size_kb:.0f} KB)")
    out = run(adb, "-s", serial, "install", "-r", str(apk))
    if "Success" not in out:
        raise SystemExit(f"安装失败:\n{out}")
    print("[OK] 安装成功")


def launch(adb: Path, serial: str) -> None:
    out = run(adb, "-s", serial, "shell", "am", "start", "-n", ACTIVITY, check=False)
    if "Error" in out:
        raise SystemExit(f"启动失败:\n{out}")
    print("[OK] 已启动", ACTIVITY)
    time.sleep(3)

    # 核对前台 Activity，确认真的起来了（有些机型会静默被系统杀掉）
    dump = run(adb, "-s", serial, "shell", "dumpsys", "activity", "activities", check=False)
    if ACTIVITY.split("/")[0] not in dump:
        print("[!] 前台 Activity 里没看到目标包，可能已被系统回收")
    else:
        print("[OK] 前台确认: 应用正在运行")


def screenshot(adb: Path, serial: str, name: str) -> Path:
    remote = "/sdcard/_shot.png"
    run(adb, "-s", serial, "shell", "screencap", "-p", remote)
    # ⚠️ pull 的目标必须用 Windows 路径，不能是 /c/...
    local = SHOT_DIR / name
    local.parent.mkdir(parents=True, exist_ok=True)
    run(adb, "-s", serial, "pull", remote, str(local).replace("\\", "/"))
    run(adb, "-s", serial, "shell", "rm", "-f", remote, check=False)
    print(f"[OK] 截图: {local}")
    return local


def tap(adb: Path, serial: str, x: int, y: int) -> None:
    run(adb, "-s", serial, "shell", "input", "tap", str(x), str(y))
    time.sleep(2)


def main() -> int:
    # 输出重定向到文件时默认全缓冲，进度看不到；强制行缓冲
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="安装 APK 到模拟器并启动验证")
    ap.add_argument("--apk", default=str(DEFAULT_APK), help="APK 路径")
    ap.add_argument("--adb", default=None, help="adb.exe 路径（默认自动探测）")
    ap.add_argument("--tab", default=None, choices=sorted(TAB_COORDS),
                    help="启动后切换到指定 Tab 再截图")
    ap.add_argument("--no-shot", action="store_true", help="不截图")
    ap.add_argument("--name", default="emulator_shot.png", help="截图文件名")
    args = ap.parse_args()

    adb = find_adb(args.adb)
    print(f"[..] adb: {adb}")

    serial = ensure_device(adb)
    install(adb, Path(args.apk), serial)
    launch(adb, serial)

    if args.tab:
        x, y = TAB_COORDS[args.tab]
        print(f"[..] 切到 Tab: {args.tab} ({x},{y})")
        tap(adb, serial, x, y)

    if not args.no_shot:
        screenshot(adb, serial, args.name)

    return 0


if __name__ == "__main__":
    sys.exit(main())
