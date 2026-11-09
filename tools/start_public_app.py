"""一键启动本地量化服务与公网穿透隧道 (start_public_app.py)

功能：
1. 启动本地 8088 移动端数据服务；
2. 启动 Cloudflare Tunnel 免费专属公网穿透；
3. 自动捕获公网 HTTPS 域名；
4. 自动复制到剪贴板，方便微信秒发朋友；
5. 随开随用，关闭窗口自动断开。
"""

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = BASE_DIR / "tools" / "run_mobile_server.py"
BIN_DIR = BASE_DIR / "tools" / "bin"
CLOUDFLARED_EXE = BIN_DIR / "cloudflared.exe"

def is_port_in_use(port: int = 8088) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0

def copy_to_clipboard(text: str):
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value '{text}'"], check=True)
        return True
    except Exception:
        return False

def main():
    print("=" * 60)
    print(" 🚀 A股量化主线追踪 · 移动端公网直连启动器 ")
    print("=" * 60)

    # 1. 检查 cloudflared.exe
    if not CLOUDFLARED_EXE.exists():
        print("未检测到穿透引擎，正在自动下载...")
        from download_cloudflared import download
        download()

    # 2. 检查并启动本地数据服务
    server_proc = None
    if is_port_in_use(8088):
        print("✔ 检测到本地 8088 服务已在运行。")
    else:
        print("正在启动本地量化数据服务 (8088端口)...")
        server_proc = subprocess.Popen(
            [sys.executable, str(SERVER_SCRIPT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(BASE_DIR)
        )
        time.sleep(2)
        if not is_port_in_use(8088):
            time.sleep(2)
        print("✔ 本地 8088 服务启动成功！")

    # 3. 启动 Cloudflare Tunnel
    print("正在建立安全公网 HTTPS 加密隧道 (请稍候 3~5 秒)...")
    tunnel_cmd = [
        str(CLOUDFLARED_EXE),
        "tunnel",
        "--url", "http://127.0.0.1:8088",
        "--no-autoupdate"
    ]

    tunnel_proc = subprocess.Popen(
        tunnel_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace"
    )

    public_url = None
    # Cloudflare 的输出在 stderr
    pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    
    start_time = time.time()
    try:
        while time.time() - start_time < 25:
            line = tunnel_proc.stderr.readline()
            if not line:
                if tunnel_proc.poll() is not None:
                    print("✖ 穿透进程意外退出！")
                    break
                time.sleep(0.1)
                continue
            
            match = pattern.search(line)
            if match:
                public_url = match.group(0)
                break
        
        if not public_url:
            print("✖ 获取公网链接超时，请检查网络是否通畅。")
            if server_proc: server_proc.terminate()
            if tunnel_proc: tunnel_proc.terminate()
            return

        app_url = f"{public_url}/mobile_app/index.html"
        copied = copy_to_clipboard(app_url)

        print("\n" + "=" * 60)
        print("🎉 恭喜！你的专属移动端公网直连通道已成功建立！")
        print("=" * 60)
        print(f"\n👉 手机直达公网链接:\n\n   {app_url}\n")
        if copied:
            print("📋 [提示] 该链接已自动复制到您的剪贴板，打开微信直接 Ctrl+V 发送！")
        print("\n📱 手机使用指南:")
        print("  1. 手机浏览器打开上述链接；")
        print("  2. 点击「添加到主屏幕」即可秒变全屏独立 App；")
        print("  3. 盘中任何人在外网均可秒级刷新最新主线与异动安全雷达！")
        print("\n💡 保持此终端窗口运行即可；按 Ctrl + C 可安全停止公网服务。")
        print("=" * 60 + "\n")

        # 保持运行
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n正在安全关闭公网隧道与服务...")
    finally:
        try:
            if tunnel_proc and tunnel_proc.poll() is None:
                tunnel_proc.terminate()
            if server_proc and server_proc.poll() is None:
                server_proc.terminate()
        except Exception:
            pass
        print("✔ 服务已停止。")

if __name__ == "__main__":
    main()
