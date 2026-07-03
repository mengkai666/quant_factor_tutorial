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
python 主线强度追踪.py
```

运行后查看 `主线强度追踪.html` 报告。

### 启用邮件推送

设置环境变量后运行：

```bash
# Linux / macOS / GitHub Actions
export EMAIL_ENABLE=1
export EMAIL_SENDER="your@qq.com"
export EMAIL_PASSWORD="your_auth_code"
export EMAIL_RECEIVERS="receiver1@qq.com,receiver2@qq.com"
python 主线强度追踪.py
```

```powershell
# Windows PowerShell
$env:EMAIL_ENABLE="1"
$env:EMAIL_SENDER="your@qq.com"
$env:EMAIL_PASSWORD="your_auth_code"
$env:EMAIL_RECEIVERS="receiver1@qq.com,receiver2@qq.com"
python 主线强度追踪.py
```

## 📁 项目结构

```
quant_factor_tutorial/
├── .github/workflows/
│   └── daily_run.yml              # GitHub Actions 每日跑批
├── 主线强度追踪.py                # 🎯 核心主程序
├── lianban_analysis.py            # 连板高度分析模块
├── fupan_report.py                # 复盘报告 API
├── FuPan_ZhangTingYuanYin.py      # 复盘涨停原因 (独立版)
├── limit_ratio_factor.py          # 市场情绪因子
├── timing_signal.py               # 量化择时信号
├── screener.py                    # 股票池筛选
├── time_utils.py                  # 时间工具
├── tradingview_generator.py       # TradingView 图表生成
├── requirements.txt               # Python 依赖
├── .gitignore                     # Git 忽略规则
│
├── 涨停历史缓存.csv               # [种子缓存] 涨停数据 (<1MB)
├── cls_plate_cache.csv            # [种子缓存] 板块分类 (<1MB)
├── sentiment_history_cache.csv    # [种子缓存] 情绪历史 (<1MB)
│
└── docs/                          # 文档
    ├── index.md
    └── howto/
        └── run-mainline-tracker.md
```

> **大文件说明**: `price_history_cache.csv` (~18MB) 和 `industry_cache.csv` 不在 Git 仓库中，
> 在 GitHub Actions 中通过 `actions/cache` 管理，本地运行时自动生成。

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
