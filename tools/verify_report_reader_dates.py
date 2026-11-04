"""验证研报日期选择器 (tools/verify_report_reader_dates.py)"""

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
    print(f"✅ 截图已保存至: {ARTIFACTS_DIR / name}")

def tap(x, y, wait=1.5):
    print(f"👉 点击坐标: ({x}, {y})")
    adb_run("shell", "input", "tap", str(x), str(y))
    time.sleep(wait)

def main():
    sys.stdout.reconfigure(line_buffering=True)
    print("1. 启动量化投研终端...")
    adb_run("shell", "am", "start", "-n", "com.quant.terminal/.MainActivity")
    time.sleep(2)
    screenshot("01_app_main.png")

    # 1920x1080 分辨率下：
    # 顶部 header 右侧按钮：[研报原篇] 约在 x=1470, y=55
    print("2. 点击【研报原篇】按钮...")
    tap(1470, 55, wait=2.0)
    screenshot("02_report_opened.png")

    # 研报模态框已开启。
    # 切换日期按钮位于研报模态框的右上角偏右位置 (x~1670, y~110)
    print("3. 点击研报弹窗内的【切换日期】按钮...")
    tap(1670, 110, wait=1.5)
    screenshot("03_report_dates_dropdown.png")

    # 如果需要下滑看更多日期：
    # 可以执行 swipe 1670 400 1670 200 300
    print("4. 滑动日期列表查看更多日期...")
    adb_run("shell", "input", "swipe", "1670", "400", "1670", "220", "300")
    time.sleep(1.0)
    screenshot("04_report_dates_scrolled.png")

    print("🎉 验证测试流程全部完成！")

if __name__ == "__main__":
    main()
