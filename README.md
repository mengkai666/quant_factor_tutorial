# 主线强度追踪系统

A 股短线主线追踪终端。每日自动更新涨停池、概念板块分类、价格缓存和市场情绪数据，并生成 `主线强度追踪.html` 交互式报告。

## ✨ 功能概览

- 📊 **涨停梯队属性梳理表** — 连板高度→主属性/次属性/核心成分股
- 📈 **大主线强度折线图** — 百分比强度趋势
- 🏗️ **大主线堆叠柱状图** — 板块资金分布
- 🔍 **细分板块强度折线图** — 含龙头标注
- 🏆 **N日涨幅Top30排行榜** — 5/10/20/60日
- 📉 **盘面涨跌统计** — 涨跌家数 + 情绪指标
- 🔥 **各周期领涨板块热力分析**
- 🤖 **量化择时信号** — 自动仓位建议
- 📋 **明日核心股票池** — 接力池 + 低吸池

## 🚀 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 本地运行

```bash
# 首次运行或需要重建历史时，先生成沪深北全 A 股统一缓存
python tools/rebuild_market_data.py --start 2025-11-04
python tools/audit_data_integrity.py --quiet

# 质量闸门通过后再生成报告
python src/主线强度追踪.py
```

运行后查看 `output/主线强度追踪.html` 报告。

### 市场数据契约

统一 universe 覆盖上海、深圳、北京证券交易所全部 A 股，标准代码分别为
`sh600000`、`sz000001`、`bj920117`。价格缓存字段为：

- `close_raw`：未复权收盘价，仅用于 A/D 和停牌状态判断。
- `close_qfq`：前复权收盘价，仅用于收益率、排行和回测。
- `trade_status`：`traded`、`suspended`、`not_listed`。
- `source_raw`、`source_qfq`：逐字段记录数据源。

候选文件通过质量闸门后才原子替换正式缓存。抓取状态写入
`data/fetch_status.csv`，质量报告写入 `data/market_data_quality.json`。
价格、universe、涨跌停池的 `partial/failed/stale/not_available` 会阻断报告；板块归因属于可降级增强项，其部分覆盖记录为 warning 并使用已有缓存或行业映射继续运行。

### 失败恢复

重建支持按代码分批和检查点续跑。网络失败时保留候选文件和状态记录，修复网络后重新执行同一命令即可；质量闸门失败不会覆盖已有正式缓存。Universe 刷新还会按交易所对比上次有效规模，接口静默返回明显不完整的列表时保留旧缓存并记录失败。

### 启用邮件推送

设置环境变量后运行：

```bash
# Linux / macOS / GitHub Actions
export EMAIL_ENABLE=1
export EMAIL_SENDER="your@qq.com"
export EMAIL_PASSWORD="your_auth_code"
export EMAIL_RECEIVERS="receiver1@qq.com,receiver2@qq.com"
python src/主线强度追踪.py
```

```powershell
# Windows PowerShell
$env:EMAIL_ENABLE="1"
$env:EMAIL_SENDER="your@qq.com"
$env:EMAIL_PASSWORD="your_auth_code"
$env:EMAIL_RECEIVERS="receiver1@qq.com,receiver2@qq.com"
python src/主线强度追踪.py
```

## 📁 项目结构

```
quant_factor_tutorial/
├── .github/workflows/
│   └── daily_run.yml              # GitHub Actions 每日跑批
├── src/                           # 📦 源码
│   ├── 主线强度追踪.py            # 🎯 兼容入口
│   ├── app.py                     # 四阶段应用编排
│   ├── data_sources/              # 日历、universe、价格及质量 Provider
│   ├── pipeline/                  # 数据准备与报告前质量闸门
│   ├── lianban_analysis.py        # 连板高度分析模块
│   ├── fupan_report.py            # 复盘报告 API
│   ├── limit_ratio_factor.py      # 市场情绪因子 (A/D 真源)
│   ├── timing_signal.py           # 量化择时信号
│   ├── screener.py                # 股票池筛选
│   ├── time_utils.py              # 时间工具
│   └── tradingview_generator.py   # TradingView 图表生成 (独立工具)
├── data/                          # 💾 缓存数据
│   ├── 涨停历史缓存.csv           # [种子缓存] 涨停数据 (<1MB)
│   ├── cls_plate_cache.csv        # [种子缓存] 板块分类 (<1MB)
│   ├── sentiment_history_cache.csv# [种子缓存] 情绪历史 (<1MB)
│   ├── price_history_cache.csv    # [运行时生成] 全市场 raw/qfq 价格 (不入库)
│   ├── stock_universe.csv         # [运行时生成] 沪深北全 A 股 universe (不入库)
│   ├── fetch_status.csv           # [运行时生成] 分批抓取状态 (不入库)
│   ├── market_data_quality.json   # [运行时生成] 最后一次质量报告 (不入库)
│   └── industry_cache.csv         # [运行时生成] 行业映射 (不入库)
├── output/                        # 📤 生成产物 (不入库)
│   ├── 主线强度追踪.html          # 主报告
│   └── focus_pool.csv             # 核心股票池
├── tests/                         # 🧪 冒烟测试
│   └── test_smoke.py
├── docs/                          # 📖 文档
├── pyproject.toml                 # 项目元数据
├── requirements.txt               # Python 依赖
└── .gitignore
```

> **运行时文件说明**: `data/price_history_cache.csv` 和 `data/industry_cache.csv` 不在 Git 仓库中，
> 在 GitHub Actions 中通过 `actions/cache` 管理，本地运行时自动生成。
> `output/` 下的报告产物也不入库。

## ⚙️ GitHub Actions 自动化

项目配置了 GitHub Actions 工作流 (`.github/workflows/daily_run.yml`)：

- **定时执行**: 每个交易日北京时间 16:30 自动运行
- **手动触发**: 支持在 GitHub 页面点击 "Run workflow"
- **缓存策略**:
  - 小文件 (涨停历史、板块、情绪) → Git 提交到仓库
  - 大文件 (价格历史、行业) → `actions/cache` 跨运行持久化
  - CI 环境下缓存上限 10MB/文件，避免膨胀
- **邮件推送**: 通过 GitHub Secrets 配置邮箱凭据

### 配置 GitHub Secrets

在仓库 Settings → Secrets and variables → Actions 中添加：

| Secret 名称 | 说明 |
|---|---|
| `EMAIL_SENDER` | 发件人邮箱 (如 `xxx@foxmail.com`) |
| `EMAIL_PASSWORD` | 邮箱授权码 (非登录密码) |
| `EMAIL_RECEIVERS` | 收件人列表 (逗号分隔) |

## 📖 文档

- [运行主线追踪](docs/howto/run-mainline-tracker.md)
- [文档索引](docs/index.md)
