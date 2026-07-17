"""集中定义项目所有路径 (单一真源)。

目录结构:
  仓库根/
    ├── src/       (本文件所在, 业务模块)
    ├── data/      (缓存 CSV)
    └── output/    (报告产物)

打包(frozen)时数据/输出与 exe 同级; 开发时从 src/ 回退到仓库根, 再进 data/ 与 output/。

⚠️ 动目录结构时只改这一个文件即可, 其他模块都从这里 import。
"""
import os
import sys

# === 基准目录 ===
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # src/ 的上一级是仓库根
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(BASE_DIR, 'data')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# === 数据缓存 (data/) ===
ZT_CACHE_FILE = os.path.join(DATA_DIR, '涨停历史缓存.csv')
PRICE_CACHE = os.path.join(DATA_DIR, 'price_history_cache.csv')
INDUSTRY_CACHE = os.path.join(DATA_DIR, 'industry_cache.csv')
SENTIMENT_CACHE = os.path.join(DATA_DIR, 'sentiment_history_cache.csv')
CLS_PLATE_CACHE = os.path.join(DATA_DIR, 'cls_plate_cache.csv')
EM_PLATE_CACHE = os.path.join(DATA_DIR, 'em_stock_plate_cache.csv')  # 东财个股所属概念板块归因缓存

# === 输出产物 (output/) ===
OUTPUT_HTML = os.path.join(OUTPUT_DIR, '主线强度追踪.html')

# === 站点发布 (归档历史报告 + 首页; 本地累积在 output/site/, CI 部署到 gh-pages) ===
SITE_DIR = os.path.join(OUTPUT_DIR, 'site')
