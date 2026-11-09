import os
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BIN_DIR = BASE_DIR / "tools" / "bin"
BIN_DIR.mkdir(parents=True, exist_ok=True)
EXE_PATH = BIN_DIR / "cloudflared.exe"

URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"

def download():
    if EXE_PATH.exists() and EXE_PATH.stat().st_size > 10000000:
        print(f"cloudflared.exe 已存在: {EXE_PATH} (大小: {EXE_PATH.stat().st_size // 1024 // 1024} MB)")
        return True

    print(f"开始自动下载 Cloudflare Tunnel 穿透引擎到: {EXE_PATH} ...")
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(URL, headers=headers)

    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        chunk_size = 1024 * 1024
        with open(EXE_PATH, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    percent = downloaded / total * 100
                    print(f"\r下载进度: {downloaded // 1024 // 1024} MB / {total // 1024 // 1024} MB ({percent:.1f}%)", end="", flush=True)
                else:
                    print(f"\r已下载: {downloaded // 1024 // 1024} MB", end="", flush=True)
    print("\n✔ 下载完成！")
    return True

if __name__ == "__main__":
    download()
