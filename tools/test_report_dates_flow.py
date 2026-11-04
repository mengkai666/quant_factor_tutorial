"""测试研报日期下拉流 (tools/test_report_dates_flow.py)"""

import subprocess
import time
import shutil
from pathlib import Path

ADB = r"D:\Program Files (x86)\leidian\LDPlayer9\adb.exe"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = Path(r"C:\Users\mengk\.gemini\antigravity\brain\ba6fa374-7f9e-426c-8a83-846251ecd150")

def run_adb(args):
    cmd = [ADB, "-s", "emulator-5554"] + args
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout

def capture(name):
    run_adb(["shell", "screencap", "-p", f"/sdcard/{name}"])
    local_p = PROJECT_ROOT / "output" / "apk" / name
    subprocess.run([ADB, "-s", "emulator-5554", "pull", f"/sdcard/{name}", str(local_p)])
    shutil.copy2(local_p, ARTIFACTS_DIR / name)
    print(f"Captured {name}")

def main():
    print("1. 重新启动 App...")
    run_adb(["shell", "am", "force-stop", "com.quant.terminal"])
    time.sleep(1)
    run_adb(["shell", "am", "start", "-n", "com.quant.terminal/.MainActivity"])
    time.sleep(2.5)

    capture("app_home_clean.png")

    print("2. 点击【研报原篇】按钮 (x=1470, y=55)...")
    run_adb(["shell", "input", "tap", "1470", "55"])
    time.sleep(2.0)
    capture("report_modal_opened.png")

    print("3. 点击研报弹窗内的【切换日期】按钮 (在弹窗右上角)...")
    # 研报弹窗内右上角的切换日期按钮：右侧有关闭按钮(x~1870)，同步按钮(x~1800)，日期选择(x~1670)
    run_adb(["shell", "input", "tap", "1670", "110"])
    time.sleep(1.5)
    capture("report_dropdown_opened.png")

    print("Done all capture!")

if __name__ == "__main__":
    main()
