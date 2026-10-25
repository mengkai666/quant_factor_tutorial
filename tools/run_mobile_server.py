"""移动端本地服务运行脚本 (run_mobile_server.py)

功能：
  1. 实时/盘后抓取开盘啦与财联社真实数据，生成移动端 API 数据包；
  2. 启动轻量 HTTP 服务，监听 0.0.0.0:8088；
  3. 自动识别局域网 WiFi IP (如 192.168.1.x)，打印手机直达链接；
  4. 支持跨域 (CORS) 与一键数据刷新接口 (/api/refresh)。
"""

import http.server
import json
import logging
import os
import socket
import sys
import threading
from pathlib import Path

# 将项目 src 加入 path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mobile_service import MobileDataAggregator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("MobileServer")

PORT = 8088

class CustomHTTPHandler(http.server.SimpleHTTPRequestHandler):
    directory = str(PROJECT_ROOT)

    def end_headers(self):
        # 允许跨域请求 (CORS) 与禁用协商缓存
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_GET(self):
        # 根路径自动重定向到手机版 App 页面
        if self.path in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/mobile_app/index.html")
            self.end_headers()
            return

        # 动态刷新数据接口
        if self.path == "/api/refresh":
            try:
                aggregator = MobileDataAggregator(output_dir=str(PROJECT_ROOT / "output" / "mobile_api"))
                bundle = aggregator.build_full_bundle()
                
                # 同步复制到 mobile_app/bundle.json
                bundle_path = PROJECT_ROOT / "mobile_app" / "bundle.json"
                with open(bundle_path, "w", encoding="utf-8") as f:
                    json.dump(bundle, f, ensure_ascii=False)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "刷新成功", "updated_at": bundle["meta"]["updated_at"]}).encode("utf-8"))
            except Exception as e:
                logger.error(f"Refresh failed: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode("utf-8"))
            return

        return super().do_GET()

def get_lan_ips():
    """获取本机所有可用局域网 IP"""
    ip_list = []
    try:
        host_name = socket.gethostname()
        for ip in socket.gethostbyname_ex(host_name)[2]:
            if not ip.startswith("127."):
                ip_list.append(ip)
    except Exception:
        pass
    return ip_list or ["127.0.0.1"]

def main():
    print("=" * 68)
    print(" 🚀 正在初始化量化投研移动端数据包...")
    aggregator = MobileDataAggregator(output_dir=str(PROJECT_ROOT / "output" / "mobile_api"))
    bundle = aggregator.build_full_bundle()
    
    # 写入 mobile_app/ 目录
    with open(PROJECT_ROOT / "mobile_app" / "bundle.json", "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False)
    print(f" ✅ 数据聚合完成: 情绪温度 {bundle['tab_mood']['degree']}°, 连板龙头 {bundle['tab_mood']['tianti_stocks'][0]['name'] if bundle['tab_mood']['tianti_stocks'] else '无'}")
    print("=" * 68)

    server_address = ("0.0.0.0", PORT)
    httpd = http.server.ThreadingHTTPServer(server_address, CustomHTTPHandler)

    lan_ips = get_lan_ips()
    preferred_ip = next((ip for ip in lan_ips if ip.startswith("192.168.") or ip.startswith("10.")), lan_ips[0])

    print("\n" + "★" * 68)
    print(" 📱 手机端直接打开体验（请确保手机与电脑连接同一 WiFi）：")
    print(f"    👉 http://{preferred_ip}:{PORT}/mobile_app/")
    print("\n 💻 电脑浏览器预览：")
    print(f"    👉 http://localhost:{PORT}/mobile_app/")
    print("\n 💡 极简操作小贴士：")
    print("    • 手机 Safari 打开后点击【分享 ➔ 添加到主屏幕】")
    print("    • 手机 Chrome 打开后点击【菜单 ➔ 安装应用 / 添加到桌面】")
    print("    • 即可作为独立 App 全屏沉浸式看盘，完全没有浏览器网址栏！")
    print("★" * 68 + "\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止服务...")
        httpd.server_close()

if __name__ == "__main__":
    main()
