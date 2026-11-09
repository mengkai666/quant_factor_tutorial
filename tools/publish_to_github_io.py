"""一键同步并发布移动端 Web App 到 GitHub Pages (publish_to_github_io.py)

功能：
1. 将当前 mobile_app 目录（HTML、最新 bundle.json、bundle.js、history 数据等）
   完整同步到根目录下的 mengkai666.github.io/app/ 目录；
2. 自动运行 git add、git commit 与 git push；
3. 输出公网访问链接: https://mengkai666.github.io/app/
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MOBILE_APP_DIR = BASE_DIR / "mobile_app"
GITHUB_IO_DIR = BASE_DIR / "mengkai666.github.io"
TARGET_APP_DIR = GITHUB_IO_DIR / "app"

def sync_and_publish():
    if not MOBILE_APP_DIR.exists():
        print(f"Error: {MOBILE_APP_DIR} does not exist.")
        return False
    if not GITHUB_IO_DIR.exists():
        print(f"Error: {GITHUB_IO_DIR} does not exist.")
        return False

    print("0. 正在同步生成最新 version.json 与 bundle.js 离线注入资产 ...")
    _copy_fn = shutil.copy2
    try:
        from sync_mobile_assets import sync as sync_assets, _safe_copy2
        _copy_fn = _safe_copy2
        sync_assets()
    except Exception as e:
        print(f"   ⚠️ 同步资产提示: {e}")

    print(f"1. 正在同步 mobile_app 到 {TARGET_APP_DIR} ...")
    TARGET_APP_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(MOBILE_APP_DIR, TARGET_APP_DIR, dirs_exist_ok=True, copy_function=_copy_fn)
    print("   ✔ 移动端文件同步完成。")

    print("2. 正在提交并推送到 GitHub Pages ...")
    try:
        # git add
        subprocess.run(["git", "-C", str(GITHUB_IO_DIR), "add", "app"], check=True)
        # git commit
        res = subprocess.run(
            ["git", "-C", str(GITHUB_IO_DIR), "commit", "-m", "同步移动端明日预案板块个股锚点、转债快照与异动安全雷达"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdout_txt = res.stdout or ""
        if "nothing to commit" in stdout_txt:
            print("   ✔ 没有检测到新变动，线上已是最新版本。")
        else:
            print("   ✔ 已生成版本提交:", stdout_txt.strip().split("\n")[0])

        # git push
        print("3. 正在推送至 GitHub 远程仓库 (origin/main) ...")
        push_res = subprocess.run(
            ["git", "-C", str(GITHUB_IO_DIR), "push", "origin", "main"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True
        )
        print("   ✔ 推送成功！")
        print("\n=======================================================")
        print("🚀 专属公网访问链接已就绪:")
        print("👉 https://mengkai666.github.io/app/")
        print("=======================================================\n")
        print("手机打开后，点击浏览器右上角分享 ->「添加到主屏幕」即可秒变原生 App！")
        return True
    except subprocess.CalledProcessError as e:
        print(f"   ✖ Git 推送失败: {e.stderr if hasattr(e, 'stderr') else e}")
        return False

if __name__ == "__main__":
    sync_and_publish()
