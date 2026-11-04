"""精确测试研报原篇阅读器与日期下拉列表 (tools/test_exact_report_flow.py)"""

import sys
import time
import shutil
import tempfile
import subprocess
from pathlib import Path
from PIL import Image

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
    return local

def tap(x, y, wait=1.5):
    print(f"👉 点击: ({x}, {y})")
    adb_run("shell", "input", "tap", str(x), str(y))
    time.sleep(wait)

def main():
    sys.stdout.reconfigure(line_buffering=True)
    
    print("1. 干净重启应用...")
    adb_run("shell", "am", "force-stop", "com.quant.terminal")
    time.sleep(1)
    adb_run("shell", "am", "start", "-n", "com.quant.terminal/.MainActivity")
    time.sleep(3.0)
    screenshot("exact_01_home.png")

    print("2. 点击【研报原篇】按钮 Center: (1533, 109)...")
    tap(1533, 109, wait=2.5)
    shot2 = screenshot("exact_02_report_opened.png")

    # 找到弹窗内的【📅 切换日期】或【📅 2026-10-29】按钮
    # 在 1920x1080 屏幕上，通过查找黄色文字或按钮边缘确定位置
    im = Image.open(shot2)
    # 查找顶部区域 y in [50, 150], x in [1500, 1850] 的按钮
    cand_pts = []
    for x in range(1450, 1850):
        for y in range(60, 140):
            r, g, b, *a = im.getpixel((x, y))
            # 查找切换日期按钮的金黄色文字 #FCD34D
            if r > 220 and g > 180 and b < 100:
                cand_pts.append((x, y))

    if cand_pts:
        xs = [p[0] for p in cand_pts]
        ys = [p[1] for p in cand_pts]
        cx, cy = sum(xs)//len(xs), sum(ys)//len(ys)
        print(f"🎯 自动识别日期切换按钮位置: ({cx}, {cy})")
        tap(cx, cy, wait=2.0)
    else:
        print("未自动识别，使用预估坐标 (1680, 100)")
        tap(1680, 100, wait=2.0)

    screenshot("exact_03_dates_popup.png")

    print("4. 滑动日期下拉列表以查看完整连续交易日...")
    adb_run("shell", "input", "swipe", "1650", "380", "1650", "200", "300")
    time.sleep(1.2)
    screenshot("exact_04_dates_scrolled.png")

    print("🎉 流程全部顺利完成！")

if __name__ == "__main__":
    main()
