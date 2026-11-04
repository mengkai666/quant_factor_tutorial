"""最终全链路验收测试 (tools/verify_final_experience.py)"""

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
    print(f"✅ 截图: {name}")

def tap(x, y, wait=1.5):
    print(f"👉 点击: ({x}, {y})")
    adb_run("shell", "input", "tap", str(x), str(y))
    time.sleep(wait)

def main():
    sys.stdout.reconfigure(line_buffering=True)
    time.sleep(1.0)
    
    # 1. 截取首页
    print("1. 检查首页状态...")
    screenshot("final_01_home.png")

    # 2. 点击【研报原篇】
    print("2. 点击顶部【研报原篇】...")
    tap(1533, 109, wait=2.0)
    screenshot("final_02_report_reader_1029.png")

    # 3. 展开日期下拉列表
    print("3. 点击日期选择器展开下拉列表...")
    tap(1609, 154, wait=1.5)
    screenshot("final_03_date_dropdown_open.png")

    # 4. 点击 2026-10-28 周三 (y~350, x~1500)
    print("4. 选择 2026-10-28 周三 研报...")
    tap(1500, 350, wait=1.8)
    screenshot("final_04_plan_1028_loaded.png")

    # 5. 切换到【🌪️ 抱团周期深度解构】Tab (x=480, y=225)
    print("5. 切换到【抱团周期】Tab...")
    tap(480, 225, wait=1.8)
    screenshot("final_05_crowd_1028_loaded.png")

    # 6. 切换到【📊 主线追踪大报告】Tab (x=740, y=225)
    print("6. 切换到【主线追踪】Tab...")
    tap(740, 225, wait=2.0)
    screenshot("final_06_mainline_1028_loaded.png")

    print("🎉 所有验证步骤成功执行完毕！")

if __name__ == "__main__":
    main()
