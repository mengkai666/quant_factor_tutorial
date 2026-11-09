"""同步移动端前端资产到各 App 壳 (sync_mobile_assets.py)

只做「资产同步」这一件事，不触发 aapt2 / d8 / apksigner 全链路构建，
方便改完 mobile_app/index.html 后快速验证 Android / Flutter 壳。

与 tools/build_apk.py 第 1 步保持完全一致的行为：
  1. mobile_app/{index.html,bundle.json,version.json,manifest.json} -> android_app/assets/
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

ASSET_FILES = ["index.html", "bundle.json", "version.json", "manifest.json", "history_index.json", "history_index.js"]


def _safe_copy2(src: str | Path, dst: str | Path) -> None:
    """兼容 Windows 内存映射锁 (WinError 1224) 的安全文件复制。"""
    src_p, dst_p = Path(src), Path(dst)
    try:
        shutil.copy2(src_p, dst_p)
        return
    except OSError:
        pass
    try:
        if dst_p.exists():
            dst_p.unlink()
        shutil.copy2(src_p, dst_p)
    except OSError:
        pass


def _safe_write_text(path: Path, content: str) -> None:
    try:
        path.write_text(content, encoding="utf-8")
        return
    except OSError:
        pass
    try:
        if path.exists():
            path.unlink()
        path.write_text(content, encoding="utf-8")
    except OSError:
        pass


def update_version_manifest() -> dict:
    """根据当前 index.html 与 bundle.json 自动生成 version.json 并回写 APP_UI_BUILD 哈希。"""
    import hashlib
    import json
    import re
    index_path = MOBILE_APP / "index.html"
    bundle_path = MOBILE_APP / "bundle.json"
    html_text = index_path.read_text(encoding="utf-8")
    normalized = re.sub(r"const APP_UI_BUILD = '[^']*';", "const APP_UI_BUILD = '__HASH__';", html_text)
    ui_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    ui_build = f"1.2.{ui_hash}"
    new_html = re.sub(
        r"const APP_UI_BUILD = '[^']*';",
        f"const APP_UI_BUILD = '{ui_build}';",
        html_text,
        count=1,
    )
    if new_html != html_text:
        _safe_write_text(index_path, new_html)

    b_meta = {}
    if bundle_path.exists():
        try:
            b_meta = json.loads(bundle_path.read_text(encoding="utf-8")).get("meta", {})
        except Exception:
            pass

    ver_data = {
        "ui_build": ui_build,
        "bundle_version": b_meta.get("version", "1.2.0"),
        "bundle_date": b_meta.get("date", ""),
        "bundle_updated_at": b_meta.get("updated_at", ""),
        "report_date": b_meta.get("report_date", ""),
        "target_date": b_meta.get("target_date", ""),
    }
    _safe_write_text(
        MOBILE_APP / "version.json",
        json.dumps(ver_data, ensure_ascii=False, indent=2),
    )
    print(f"✅ 生成 version.json (ui_build={ui_build}, bundle_updated_at={ver_data['bundle_updated_at']})")
    return ver_data


def sync() -> int:
    if not MOBILE_APP.exists():
        print(f"❌ 未找到 {MOBILE_APP}")
        return 1

    update_version_manifest()
    ANDROID_ASSETS.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ASSET_FILES:
        src = MOBILE_APP / name
        if not src.exists():
            print(f"  ⚠️ 跳过缺失文件 {name}")
            continue
        _safe_copy2(src, ANDROID_ASSETS / name)
        copied.append(name)
    print(f"✅ 同步到 android_app/assets: {', '.join(copied)}")

    # 同步历史与研报目录
    for sub in ["history", "reports"]:
        sub_dir = MOBILE_APP / sub
        if sub_dir.exists():
            shutil.copytree(sub_dir, ANDROID_ASSETS / sub, dirs_exist_ok=True, copy_function=_safe_copy2)
            print(f"✅ 同步到 android_app/assets/{sub}")

    # 离线首帧注入：与 build_apk.py 一致
    bundle_json = MOBILE_APP / "bundle.json"
    if bundle_json.exists():
        content = bundle_json.read_text(encoding="utf-8")
        payload = f"window.__INITIAL_BUNDLE__ = {content};\n"
        _safe_write_text(ANDROID_ASSETS / "bundle.js", payload)
        _safe_write_text(MOBILE_APP / "bundle.js", payload)
        print(f"✅ 生成 bundle.js 离线注入 ({len(payload) / 1024:.0f} KB)")
    else:
        print("  ⚠️ 无 bundle.json，跳过 bundle.js 生成")

    if FLUTTER_ASSETS.parent.exists():
        FLUTTER_ASSETS.mkdir(parents=True, exist_ok=True)
        for name in ASSET_FILES:
            src = MOBILE_APP / name
            if src.exists():
                _safe_copy2(src, FLUTTER_ASSETS / name)
        print("✅ 同步到 flutter_app/assets")
    else:
        print("  ℹ️ 无 flutter_app/assets，跳过")

    # 同步静态站点副本（GitHub Pages 的 mobile 入口）
    site_mobile = PROJECT_ROOT / "output" / "site" / "mobile"
    if site_mobile.parent.exists():
        shutil.copytree(MOBILE_APP, site_mobile, dirs_exist_ok=True, copy_function=_safe_copy2)
        print("✅ 同步到 output/site/mobile")

    return 0


if __name__ == "__main__":
    sys.exit(sync())
