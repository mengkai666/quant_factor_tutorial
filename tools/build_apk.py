"""Android APK 自动化构建工具 (build_apk.py)

利用本地 Android SDK 与 JDK 11 工具链，快速编译、打包并签名量化投研终端 Android 原生 APK。
包含：
  1. aapt2 编译与链接资源 (R.java + assets/index.html)
  2. javac 编译原生 Java 代码
  3. d8 生成 classes.dex
  4. zipalign 内存对齐 (4-byte alignment)
  5. apksigner 对齐签名 (debug.keystore)
输出产物：output/apk/量化投研终端.apk
"""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SDK_ROOT = Path(r"C:\Users\mengk\AppData\Local\Android\Sdk")
BUILD_TOOLS = SDK_ROOT / "build-tools" / "30.0.3"
PLATFORM_JAR = SDK_ROOT / "platforms" / "android-34" / "android.jar"
JAVA_HOME = Path(r"C:\Program Files\Java\jdk-11")
JAVAC = JAVA_HOME / "bin" / "javac.exe"
JAVA = JAVA_HOME / "bin" / "java.exe"
AAPT2 = BUILD_TOOLS / "aapt2.exe"
ZIPALIGN = BUILD_TOOLS / "zipalign.exe"
KEYSTORE = Path(r"C:\Users\mengk\.android\debug.keystore")

# d8 / apksigner 的 .bat 包装脚本只是为了用 find_java.bat 去找 JDK，
# 而 find_java.bat 会调 reg.exe —— 本机 reg.exe 被安全策略拉黑，
# 于是 .bat 一跑就 exit 255。既然 JAVA_HOME 已知，直接驱动 jar 即可，
# 完全绕开 reg.exe（这不是绕过黑名单，而是根本不需要它）。
D8_JAR = BUILD_TOOLS / "lib" / "d8.jar"
APKSIGNER_JAR = BUILD_TOOLS / "lib" / "apksigner.jar"
D8_MAIN = "com.android.tools.r8.D8"
APKSIGNER_MAIN = "com.android.apksigner.ApkSignerTool"


def run_cmd(cmd, env=None, check=True):
    print(f" -> 执行: {' '.join(str(c) for c in cmd)}")
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    if check and res.returncode != 0:
        print(f"❌ 命令执行失败 (code {res.returncode}):")
        if res.stdout:
            print(res.stdout)
        if res.stderr:
            print(res.stderr)
        raise RuntimeError(f"Command failed: {cmd[0]}")
    return res


def run_jar(main_class, jar, args, env=None, check=True):
    """用 java 直接驱动 build-tools 里的 jar，绕开会调用 reg.exe 的 .bat 包装。"""
    return run_cmd([str(JAVA), "-cp", str(jar), main_class, *args],
                   env=env, check=check)

def build_apk():
    print("=" * 68)
    print(" 🚀 开始编译并打包【量化投研终端】Android 原生 APK...")
    print("=" * 68)

    # 1. 检查必备工具链
    for p, name in [
        (AAPT2, "aapt2"), (ZIPALIGN, "zipalign"),
        (D8_JAR, "d8.jar"), (APKSIGNER_JAR, "apksigner.jar"),
        (JAVAC, "javac"), (JAVA, "java"), (PLATFORM_JAR, "android.jar")
    ]:
        if not p.exists():
            raise FileNotFoundError(f"缺少必要组件: {name} (路径: {p})")

    # 2. 准备临时构建目录与产物目录
    work_dir = PROJECT_ROOT / "build" / "apk_temp"
    out_dir = PROJECT_ROOT / "output" / "apk"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    gen_dir = work_dir / "gen"
    classes_dir = work_dir / "classes"
    gen_dir.mkdir(parents=True, exist_ok=True)
    classes_dir.mkdir(parents=True, exist_ok=True)

    # 环境准备
    env = os.environ.copy()
    env["JAVA_HOME"] = str(JAVA_HOME)
    env["PATH"] = f"{JAVA_HOME / 'bin'};{env.get('PATH', '')}"

    # 3. 同步最新移动端资产到 android_app/assets
    print(" 1/7 同步最新前端资产与真实行情数据...")
    assets_dir = PROJECT_ROOT / "android_app" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    for f in ["index.html", "bundle.json", "manifest.json", "history_index.json", "history_index.js"]:
        src_f = PROJECT_ROOT / "mobile_app" / f
        if src_f.exists():
            shutil.copy2(src_f, assets_dir / f)

    # 同步历史复盘 bundles 目录与深度研报原篇
    src_hist = PROJECT_ROOT / "mobile_app" / "history"
    if src_hist.exists():
        shutil.copytree(src_hist, assets_dir / "history", dirs_exist_ok=True)
    src_reports = PROJECT_ROOT / "mobile_app" / "reports"
    if src_reports.exists():
        shutil.copytree(src_reports, assets_dir / "reports", dirs_exist_ok=True)

    # 自动生成离线启动注入文件 bundle.js
    bundle_json_file = PROJECT_ROOT / "mobile_app" / "bundle.json"
    if bundle_json_file.exists():
        with open(bundle_json_file, "r", encoding="utf-8") as f:
            content = f.read()
        with open(assets_dir / "bundle.js", "w", encoding="utf-8") as f:
            f.write(f"window.__INITIAL_BUNDLE__ = {content};\n")
        with open(PROJECT_ROOT / "mobile_app" / "bundle.js", "w", encoding="utf-8") as f:
            f.write(f"window.__INITIAL_BUNDLE__ = {content};\n")

    # 4. aapt2 编译资源
    print(" 2/7 aapt2 编译 Android 资源...")
    compiled_res = work_dir / "compiled_res.zip"
    run_cmd([
        str(AAPT2), "compile",
        "--dir", str(PROJECT_ROOT / "android_app" / "res"),
        "-o", str(compiled_res)
    ], env=env)

    # 5. aapt2 链接生成 base.apk 与 R.java
    print(" 3/7 aapt2 链接生成 base.apk 与 R.java...")
    base_apk = work_dir / "base.apk"
    manifest_xml = PROJECT_ROOT / "android_app" / "AndroidManifest.xml"
    run_cmd([
        str(AAPT2), "link",
        "-I", str(PLATFORM_JAR),
        str(compiled_res),
        "--manifest", str(manifest_xml),
        "--min-sdk-version", "21",
        "--target-sdk-version", "34",
        "-o", str(base_apk),
        "--java", str(gen_dir),
        "-A", str(assets_dir),
        "--auto-add-overlay"
    ], env=env)

    # 6. javac 编译 Java 代码
    print(" 4/7 javac 编译原生 Java 代码...")
    r_java = list(gen_dir.rglob("R.java"))[0]
    main_activity = PROJECT_ROOT / "android_app" / "src" / "com" / "quant" / "terminal" / "MainActivity.java"
    run_cmd([
        str(JAVAC),
        "-encoding", "UTF-8",
        "-cp", str(PLATFORM_JAR),
        "-d", str(classes_dir),
        str(r_java),
        str(main_activity)
    ], env=env)

    # 7. d8 生成 classes.dex
    print(" 5/7 d8 生成 classes.dex 字节码...")
    class_files = [str(f) for f in classes_dir.rglob("*.class")]
    run_jar(D8_MAIN, D8_JAR, [*class_files, "--output", str(work_dir)], env=env)

    # 8. 将 classes.dex 注入 base.apk 形成未对齐包
    print(" 6/7 打包生成完整 APK 容器...")
    unaligned_apk = work_dir / "unaligned.apk"
    shutil.copy2(base_apk, unaligned_apk)
    with zipfile.ZipFile(unaligned_apk, "a") as z:
        z.write(work_dir / "classes.dex", "classes.dex")

    # 9. zipalign 内存对齐 (4-byte alignment)
    print(" 7/7 zipalign 4字节内存对齐与签名...")
    aligned_apk = work_dir / "aligned.apk"
    run_cmd([
        str(ZIPALIGN),
        "-v", "-p", "-f", "4",
        str(unaligned_apk),
        str(aligned_apk)
    ], env=env)

    # 10. apksigner 签名
    final_apk = out_dir / "quant_factor_terminal.apk"
    run_jar(APKSIGNER_MAIN, APKSIGNER_JAR, [
        "sign",
        "--ks", str(KEYSTORE),
        "--ks-pass", "pass:android",
        "--key-pass", "pass:android",
        "--out", str(final_apk),
        str(aligned_apk)
    ], env=env)

    # 生成中文别名文件
    cn_apk = out_dir / "量化投研终端.apk"
    shutil.copy2(final_apk, cn_apk)

    # 11. 验证签名
    run_jar(APKSIGNER_MAIN, APKSIGNER_JAR, ["verify", str(final_apk)], env=env)

    size_mb = os.path.getsize(final_apk) / (1024 * 1024)
    print("\n" + "★" * 68)
    print(f" 🎉 Android APK 编译成功！")
    print(f" 📦 英文安装包: {final_apk}")
    print(f" 📦 中文安装包: {cn_apk}")
    print(f" 📊 安装包体积: {size_mb:.2f} MB")
    print(f"\n 📲 一键安装到手机/模拟器：")
    print(f'    C:\\adb-tools\\adb.exe install -r "{final_apk}"')
    print("★" * 68 + "\n")

    return final_apk

if __name__ == "__main__":
    build_apk()
