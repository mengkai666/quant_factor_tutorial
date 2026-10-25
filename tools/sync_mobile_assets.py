"""同步移动端前端资产到各 App 壳 (sync_mobile_assets.py)

只做「资产同步」这一件事，不触发 aapt2 / d8 / apksigner 全链路构建，
方便改完 mobile_app/index.html 后快速验证 Android / Flutter 壳。

与 tools/build_apk.py 第 1 步保持完全一致的行为：
  1. mobile_app/{index.html,bundle.json,manifest.json} -> android_app/assets/
  2. 由 bundle.json 生成离线首帧注入文件 bundle.js
     (`window.__INITIAL_BUNDLE__ = {...}`)，同时回写 mobile_app/bundle.js
  3. 同步 index.html 到 flutter_app 资源目录（若存在）

用法:
    python tools/sync_mobile_assets.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MOBILE_APP = PROJECT_ROOT / "mobile_app"
ANDROID_ASSETS = PROJECT_ROOT / "android_app" / "assets"
FLUTTER_ASSETS = PROJECT_ROOT / "flutter_app" / "assets"

ASSET_FILES = ["index.html", "bundle.json", "manifest.json", "history_index.json", "history_index.js"]


def sync() -> int:
    if not MOBILE_APP.exists():
        print(f"❌ 未找到 {MOBILE_APP}")
        return 1

    ANDROID_ASSETS.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ASSET_FILES:
        src = MOBILE_APP / name
        if not src.exists():
            print(f"  ⚠️ 跳过缺失文件 {name}")
            continue
        shutil.copy2(src, ANDROID_ASSETS / name)
        copied.append(name)
    print(f"✅ 同步到 android_app/assets: {', '.join(copied)}")

    # 同步历史与研报目录
    for sub in ["history", "reports"]:
        sub_dir = MOBILE_APP / sub
        if sub_dir.exists():
            shutil.copytree(sub_dir, ANDROID_ASSETS / sub, dirs_exist_ok=True)
            print(f"✅ 同步到 android_app/assets/{sub}")

    # 离线首帧注入：与 build_apk.py 一致
    bundle_json = MOBILE_APP / "bundle.json"
    if bundle_json.exists():
        content = bundle_json.read_text(encoding="utf-8")
        payload = f"window.__INITIAL_BUNDLE__ = {content};\n"
        (ANDROID_ASSETS / "bundle.js").write_text(payload, encoding="utf-8")
        (MOBILE_APP / "bundle.js").write_text(payload, encoding="utf-8")
        print(f"✅ 生成 bundle.js 离线注入 ({len(payload) / 1024:.0f} KB)")
    else:
        print("  ⚠️ 无 bundle.json，跳过 bundle.js 生成")

    if FLUTTER_ASSETS.parent.exists():
        FLUTTER_ASSETS.mkdir(parents=True, exist_ok=True)
        for name in ASSET_FILES:
            src = MOBILE_APP / name
            if src.exists():
                shutil.copy2(src, FLUTTER_ASSETS / name)
        print(f"✅ 同步到 flutter_app/assets")
    else:
        print("  ℹ️ 无 flutter_app/assets，跳过")

    # 同步静态站点副本（GitHub Pages 的 mobile 入口）
    site_mobile = PROJECT_ROOT / "output" / "site" / "mobile"
    if site_mobile.parent.exists():
        shutil.copytree(MOBILE_APP, site_mobile, dirs_exist_ok=True)
        print("✅ 同步到 output/site/mobile")

    return 0


if __name__ == "__main__":
    sys.exit(sync())
