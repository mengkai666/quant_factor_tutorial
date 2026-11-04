"""测试研报原篇阅读器及其日期下拉浮层 (tools/test_actual_report_modal.py)"""

import sys
import time
import shutil
import tempfile
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = Path(r"C:\Users\mengk\.gemini\antigravity\brain\ba6fa374-7f9e-426c-8a83-846251ecd150")
ADB = Path(r"D:\Program Files (x86)\leidian\LDPlayer9\adb.exe")
SERIAL = "emulator-5554"

def adb_run(*args, timeout=15):
    cmd = [str(ADB), "-s", SERIAL] + list(args)
    with tempfile.TemporaryFile(mode="w+b") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=timeout)
        fh.seek(0)
        return fh.read().decode("utf-8", errors="replace")

def screenshot(name):
    remote = "/sdcard/_shot_tmp.png"
    adb_run("shell", "screencap", "-p", remote)
    local = PROJECT_ROOT / "output" / "apk" / name
    local.parent.mkdir(parents=True, exist_ok=True)
    adb_run("pull", remote, str(local).replace("\\", "/"))
    adb_run("shell", "rm", "-f", remote)
    shutil.copy2(local, ARTIFACTS_DIR / name)
    print(f"✅ 截图: {ARTIFACTS_DIR / name}")

def tap(x, y, wait=1.5):
    print(f"👉 点击: ({x}, {y})")
    adb_run("shell", "input", "tap", str(x), str(y))
    time.sleep(wait)

def main():
    sys.stdout.reconfigure(line_buffering=True)
    
    # 1. 关掉之前可能开着的【历史】弹窗
    print("1. 关闭旧弹窗...")
    tap(1868, 222, wait=1.0) # 点击关闭按钮 x=1868, y=222
    screenshot("10_after_close.png")

    # 2. 点击【研报原篇】按钮 (x=1535, y=105)
    print("2. 点击【研报原篇】(x=1535, y=105)...")
    tap(1535, 105, wait=2.5)
    screenshot("11_report_modal_real.png")

    # 3. 研报阅读器打开后，点击顶部的【切换日期】按钮
    # 在 1920x1080 下，研报模态框的头部高度约在 y=50..120
    # 右侧有三个按钮：关闭(约 x=1880), 刷新(约 x=1830), 日期选择(约 x=1710, y=95)
    print("3. 点击研报原篇内的日期下拉按钮 (x=1710, y=95)...")
    tap(1710, 95, wait=1.5)
    screenshot("12_report_date_popup_real.png")

if __name__ == "__main__":
    main()
