"""集中定义项目所有路径 (单一真源)。

目录结构:
  仓库根/
    ├── src/       (本文件所在, 业务模块)
    ├── data/      (缓存 CSV)
    └── output/    (报告产物)

打包(frozen)时数据/输出与 exe 同级; 开发时从 src/ 回退到仓库根, 再进 data/ 与 output/。

⚠️ 动目录结构时只改这一个文件即可, 其他模块都从这里 import。

⚠️ CSV 缓存自愈: 涨停历史缓存.csv 等文件参与 git 版本控制, rebase/stash pop
   可能引入 `<<<<<<< / ======= / >>>>>>>` 冲突标记, 导致 pandas.read_csv 读到脏数据
   (行数暴增, awk/grep 也会被截断)。本模块 import 时自动扫描 DATA_DIR 下所有 CSV,
   发现冲突标记就地清理并告警 (保留冲突两侧内容中较长的一侧, 通常更完整)。
"""
import os
import re
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
SECURITY_MASTER_CACHE = os.path.join(DATA_DIR, 'security_master.csv')
PREDICTION_HISTORY = os.path.join(DATA_DIR, 'report_prediction_history.jsonl')
DAILY_SNAPSHOT_DIR = os.path.join(DATA_DIR, 'report_daily_snapshots')

# === 输出产物 (output/) ===
OUTPUT_HTML = os.path.join(OUTPUT_DIR, '主线强度追踪.html')
AUDIT_DIR = os.path.join(OUTPUT_DIR, 'audit')
os.makedirs(DAILY_SNAPSHOT_DIR, exist_ok=True)
os.makedirs(AUDIT_DIR, exist_ok=True)

# === 站点发布 (归档历史报告 + 首页; 本地累积在 output/site/, CI 部署到 gh-pages) ===
SITE_DIR = os.path.join(OUTPUT_DIR, 'site')
# GitHub Pages 首页 (可用环境变量 SITE_URL 覆盖; 本地跑完后自动打开)
SITE_URL = os.environ.get(
    'SITE_URL',
    'https://mengkai666.github.io/quant_factor_tutorial/',
)


# ─────────────────────────────────────────────────────────────
# CSV 缓存冲突标记自愈
# ─────────────────────────────────────────────────────────────
# git merge/stash pop 冲突标记正则 (同时匹配三种标记, 多行模式)
_CONFLICT_BLOCK_RE = re.compile(
    r'^<{7} .*?\r?\n(.*?)^={7}\r?\n(.*?)^>{7} .*?\r?\n',
    re.DOTALL | re.MULTILINE,
)


def _heal_conflict_markers(path):
    """就地清理单个文件的 git 冲突标记。有冲突返回 True, 无冲突/失败返回 False。

    策略: 保留两侧内容中较长的一侧 (行数多的一侧通常更完整, 因为冲突多是新数据 vs 旧数据)。
    原文件备份到 .conflict.bak, 万一策略选错还能人工恢复。
    """
    try:
        with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
            content = f.read()
        if '<<<<<<<' not in content:
            return False

        # 备份
        bak = path + '.conflict.bak'
        if not os.path.exists(bak):
            with open(bak, 'w', encoding='utf-8-sig') as f:
                f.write(content)

        def _pick_longer(m):
            side_a, side_b = m.group(1), m.group(2)
            return side_a if side_a.count('\n') >= side_b.count('\n') else side_b

        cleaned = _CONFLICT_BLOCK_RE.sub(_pick_longer, content)
        # 兜底: 若还残留任何标记 (格式变体), 整行删除
        cleaned = re.sub(r'^(?:<{7}|={7}|>{7}).*$\r?\n?', '', cleaned, flags=re.MULTILINE)

        with open(path, 'w', encoding='utf-8-sig') as f:
            f.write(cleaned)
        return True
    except Exception as e:
        print(f'  ⚠️ CSV 冲突自愈失败 {os.path.basename(path)}: {e}')
        return False


def _auto_heal_data_csvs():
    """import 时自动扫描 DATA_DIR 下所有 CSV, 修复冲突标记。"""
    if not os.path.isdir(DATA_DIR):
        return
    healed = []
    for fn in os.listdir(DATA_DIR):
        if not fn.lower().endswith('.csv'):
            continue
        fp = os.path.join(DATA_DIR, fn)
        if _heal_conflict_markers(fp):
            healed.append(fn)
    if healed:
        print(f'  🔧 已自动清理 git 冲突标记: {", ".join(healed)} '
              f'(原文件备份为 .conflict.bak)')


# 模块首次 import 时执行 (无冲突则秒过, 有冲突则清理+告警)
_auto_heal_data_csvs()
