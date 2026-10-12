<div align="center">

# 📊 quant_factor_tutorial

**A 股短线主线追踪与多周期量化投研系统 · 情绪周期 · 抱团战法 · 算法择时**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python-Version](https://img.shields.io/badge/Python-3.10+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Author: 孟锴 (Kai Meng)](https://img.shields.io/badge/Author-孟锴%20(Kai%20Meng)-2563eb.svg)](https://mengkai666.github.io/)
[![GitHub Actions](https://github.com/mengkai666/quant_factor_tutorial/actions/workflows/daily_run.yml/badge.svg)](https://github.com/mengkai666/quant_factor_tutorial/actions)
[![Stars](https://img.shields.io/github/stars/mengkai666/quant_factor_tutorial?style=social)](https://github.com/mengkai666/quant_factor_tutorial)
[![Forks](https://img.shields.io/github/forks/mengkai666/quant_factor_tutorial?style=social)](https://github.com/mengkai666/quant_factor_tutorial)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/mengkai666/quant_factor_tutorial/pulls)

<p>
  <sub>🚀 发起与维护：<a href="https://mengkai666.github.io/">孟锴 (Kai Meng)</a> · 每日全自动盘后计算 · 统一沪深北全 A 股 Universe · 数据质量闸门机制 · 自动化邮件与站点发布</sub>
</p>

</div>

---

<p align="center">
  <a href="#-核心亮点">✨ 核心亮点</a> •
  <a href="#-最近演进">🔥 最近演进</a> •
  <a href="#-适用对象与场景">📖 适用场景</a> •
  <a href="#-核心投研功能">📊 核心功能</a> •
  <a href="#-抱团周期研究模块">🔬 抱团研究</a> •
  <a href="#-快速开始">🚀 快速开始</a> •
  <a href="#-数据契约与质量保障">🏗️ 数据契约</a> •
  <a href="#-项目结构">📁 项目结构</a> •
  <a href="#-github-actions-自动化">⚙️ 自动化</a> •
  <a href="#-相关文档">📖 文档</a> •
  <a href="#-参与贡献">🤝 贡献</a> •
  <a href="#-开源许可证">📄 许可证</a> •
  <a href="#-免责声明">⚖️ 免责声明</a>
</p>

---

## ✨ 核心亮点

| 🎯 模块/定位 | 📌 核心说明 | 状态 |
|:---:|:---|:---:|
| 📊 **连板梯队与主线强度** | 连板高度梯队梳理、主次属性拆解、大主线资金分布堆叠图与细分龙头标注 | ✅ |
| 🌊 **情绪周期与择时引擎** | 沪深北统一 A/D 涨跌统计、极值情绪因子提取、量化择时与自动仓位建议 | ✅ |
| 🔬 **抱团周期与条件战法** | 两个月分周期复盘、固定旧核心篮子、个股价格路径复核与试错升级检查 | ✅ |
| 🛡️ **数据契约与质量闸门** | 全 A 股标准代码对齐、候选文件原子替换、断点续跑与不可降级错误拦截 | ✅ |
| 🤖 **自动化跑批与多端发布** | GitHub Actions 每日 16:30 自动跑批、轻重数据分级缓存、HTML 看板与邮件推送 | ✅ |

---

## 🔥 最近演进

| **时间** | **特性 / 更新内容** |
|:---|:---|
| 2026.10 | 🆕 [**独立抱团周期研究模块**](docs/howto/crowding-cycle.md)：两个月分周期复盘、固定核心篮子路径核对、滚动强势代理与试错升级检查 |
| 2026.09 | 🆕 [**多板块观察子页体系**](docs/howto/multi-sector-brief.md)：每板块3只重点股、近期多板表下钻与主报告双向导航 |
| 2026.09 | [**今日复盘与明日预案闭环**](docs/howto/recap-scenario-closure.md)：单一事实结论、逐策略资格独立验证与真实盘中事件采集 |
| 2026.08 | [**全市场统一缓存与数据质量闸门**](docs/howto/run-mainline-tracker.md)：覆盖沪深北全 A 股、严格区分未复权与前复权、原子级缓存替换 |

---

## 📖 适用对象与场景

- 🎯 **短线游资与打板量化研究员**：监控每日高度板断板、连板晋级率、板块资金吸筹与情绪周期极值点。
- 📈 **趋势波段与主线策略开发者**：识别大主线与分支主线的轮动节奏，量化择时信号提供客观的仓位风控基准。
- 🔬 **机构量化与学术研究**：依托全市场统一数据契约、A/D 情绪因子、严格防未来函数与断点续跑机制。

---

## 📊 核心投研功能

### 1. 连板高度与主线追踪
- **涨停梯队属性梳理表**：连板高度梯队分布，自动解析个股主属性与次属性，标识板块核心成分股。
- **大主线强度折线图**：多日百分比强度趋势对比，直观捕捉主线资金进退节奏。
- **板块资金分布堆叠图**：大主线与细分板块成交量能结构可视化。
- **细分板块强度折线图**：包含行业龙头个股动态标注与涨幅领先指标。

### 2. 盘面情绪与量化择时
- **全市场涨跌统计**：沪深北全 A 股真实涨跌停分布、上涨/下跌家数比（A/D 真源）。
- **极值情绪指标**：连板晋级率、炸板率、高标溢价与情绪冰点/沸点探测。
- **量化择时信号**：基于多因子情绪模型，自动生成仓位参考建议（空仓/防守/积极参与）。

### 3. 实战选股与多板块观察
- **明日核心股票池**：自动化输出明日关注标的（接力池 + 低吸池）。
- **N日涨幅Top30排行榜**：滚动统计 5 / 10 / 20 / 60 日强势股表现。
- **多板块观察子页**：独立子页面展示每板块 3 只重点股与近期多板明细，支持与主报告无缝互跳。

---

## 🔬 抱团周期研究模块

新增独立“抱团”研究页：两个月分周期复盘、固定旧核心篮子、个股价格路径、截图节点核对、独立滚动强势代理、试错升级检查和条件战法。

```powershell
# 本地生成抱团周期交互报告与战法 Markdown
python tools/build_crowding_report.py --as-of 2026-10-08 --link-local

# 可选联网：只刷新18只参考个股的独立日线，不覆盖全市场缓存
python tools/build_crowding_report.py --as-of 2026-10-08 --refresh-reference-quotes --link-local
```

- **生成产物**：
  - `output/抱团周期分析_最新.html`
  - `output/抱团周期分析_YYYY-MM-DD.html` (历史归档)
  - `output/抱团周期战法_YYYY-MM-DD.md`
  - `output/site/crowding/` 站点静态资源
- **口径说明**：未接入分时承接与可成交性，不输出自动交易信号、固定仓位或未经验证的胜率。详细口径见 [抱团模块说明](docs/howto/crowding-cycle.md)。

---

## 🚀 快速开始

### 1. 克隆仓库与安装依赖

```bash
# 克隆仓库
git clone https://github.com/mengkai666/quant_factor_tutorial.git
cd quant_factor_tutorial

# 安装 Python 依赖 (推荐 Python >= 3.10)
pip install -r requirements.txt
```

### 2. 本地运行与报告生成

```bash
# 首次运行或需要重建历史时，生成沪深北全 A 股统一缓存
python tools/rebuild_market_data.py --start 2025-11-04

# 校验数据完整性与质量闸门
python tools/audit_data_integrity.py --quiet

# 质量闸门通过后，运行主流水线生成报告
python src/主线强度追踪.py
```

运行完成后，在浏览器中打开 `output/主线强度追踪.html` 或 `output/index.html` 即可查看完整交互式复盘看板。

### 3. 启用邮件自动推送（可选）

设置环境变量后运行脚本即可自动推送：

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

---

## 🏗️ 数据契约与质量保障

统一 universe 覆盖上海、深圳、北京证券交易所全部 A 股，标准代码分别为 `sh600000`、`sz000001`、`bj920117`。

### 价格缓存字段规范
- `close_raw`：未复权收盘价，仅用于 A/D 涨跌统计和停牌状态判断。
- `close_qfq`：前复权收盘价，仅用于收益率、排行和量化回测。
- `trade_status`：交易状态（`traded`、`suspended`、`not_listed`）。
- `source_raw`、`source_qfq`：逐字段记录数据源。

### 质量闸门机制与失败恢复
- **原子替换**：候选文件通过质量闸门检查后才原子替换正式缓存，避免脏数据写入。
- **阻断策略**：价格、universe、涨跌停池的 `partial/failed/stale/not_available` 会阻断报告生成；板块归因为可降级增强项。
- **断点续跑**：支持按代码分批和检查点续跑。网络波动失败时保留候选文件与状态记录，修复网络后重新执行即可。

---

## 📁 项目结构

```
quant_factor_tutorial/
├── .github/workflows/
│   └── daily_run.yml              # GitHub Actions 每日自动化跑批
├── src/                           # 📦 核心算法与应用源码
│   ├── 主线强度追踪.py            # 🎯 主程序运行入口
│   ├── app.py                     # 四阶段应用编排
│   ├── data_sources/              # 日历、universe、行情价格及质量 Provider
│   ├── pipeline/                  # 数据管道与报告前质量闸门
│   ├── lianban_analysis.py        # 连板高度与梯队分析模块
│   ├── fupan_report.py            # 复盘报告生成 API
│   ├── limit_ratio_factor.py      # 市场情绪因子模型 (A/D 真源)
│   ├── timing_signal.py           # 量化择时与风控信号
│   ├── screener.py                # 核心股票池筛选引擎
│   ├── baotuan_cycle.py           # 抱团周期算法引擎
│   ├── crowding_cycle.py          # 抱团研究与强势代理
│   ├── time_utils.py              # 交易日历与时间工具
│   └── tradingview_generator.py   # TradingView 图表生成器
├── data/                          # 💾 缓存数据
│   ├── 涨停历史缓存.csv           # [种子缓存] 涨停数据 (<1MB)
│   ├── cls_plate_cache.csv        # [种子缓存] 板块分类 (<1MB)
│   ├── sentiment_history_cache.csv# [种子缓存] 情绪历史 (<1MB)
│   ├── price_history_cache.csv    # [运行时生成] 全市场 raw/qfq 价格 (不入库)
│   ├── stock_universe.csv         # [运行时生成] 沪深北全 A 股 universe (不入库)
│   ├── fetch_status.csv           # [运行时生成] 分批抓取状态 (不入库)
│   └── market_data_quality.json   # [运行时生成] 质量报告 (不入库)
├── output/                        # 📤 生成产物目录 (不入库)
│   ├── 主线强度追踪.html          # 主报告看板
│   ├── 抱团周期分析_最新.html     # 抱团周期专题报告
│   ├── 今日复盘与明日预案_最新.html# 实战推演闭环报告
│   └── focus_pool.csv             # 核心关注股票池
├── tools/                         # 🛠️ 离线运维与重跑工具
│   ├── rebuild_market_data.py     # 全市场数据缓存重建工具
│   ├── audit_data_integrity.py    # 数据完整性审计脚本
│   └── build_crowding_report.py   # 抱团专题报告构建工具
├── tests/                         # 🧪 自动化测试套件
│   ├── test_smoke.py              # 核心冒烟测试
│   ├── test_baotuan_cycle.py      # 抱团算法测试
│   └── test_all_pages_e2e.py      # 全页面端到端测试
├── docs/                          # 📖 投研与实战指南
│   ├── index.md                   # 文档导航总览
│   └── howto/                     # 专题指引 (抱团、多板块、预案等)
├── pyproject.toml                 # 项目工程元数据
├── requirements.txt               # Python 依赖项
├── LICENSE                        # MIT 开源许可证
└── .gitignore
```

---

## ⚙️ GitHub Actions 自动化

项目配置了完整的自动化工作流 (`.github/workflows/daily_run.yml`)：

- **定时触发**：每个交易日北京时间 **16:30** 自动执行盘后跑批。
- **手动触发**：支持在 GitHub Actions 控制台点击 "Run workflow" 手动执行。
- **缓存策略**：
  - 核心轻量文件（涨停历史、板块、情绪）自动提交持久化。
  - 大文件（全市场价格历史、行业映射）通过 `actions/cache` 跨运行持久化，设置 10MB/文件上限防膨胀。
- **邮件通知**：配置 Secrets 即可在跑批完成后自动发送投研日报。

### 配置 GitHub Secrets

在仓库 **Settings** → **Secrets and variables** → **Actions** 中添加：

| Secret 名称 | 必填 | 说明 |
|---|:---:|---|
| `EMAIL_SENDER` | 是 | 发件人邮箱地址 (如 `xxx@qq.com`) |
| `EMAIL_PASSWORD` | 是 | 邮箱 SMTP 服务授权码 (非邮箱登录密码) |
| `EMAIL_RECEIVERS` | 是 | 收件人列表 (支持英文逗号分隔多个邮箱) |

---

## 📖 相关文档

- 🧭 [文档总览索引](docs/index.md)
- 🔬 [抱团周期与新周期试错模块说明](docs/howto/crowding-cycle.md)
- 📊 [多板块观察子页使用说明](docs/howto/multi-sector-brief.md)
- 📝 [今日复盘与明日推演：候选、验证和结果闭环](docs/howto/recap-scenario-closure.md)
- 🎯 [逐策略资格、独立验证与条件预案](docs/howto/strategy-qualification.md)
- 🛠️ [运行主线追踪系统操作指南](docs/howto/run-mainline-tracker.md)
- 📡 [事件数据接通、离线验收与真实盘中采集](docs/howto/decision-data-connection.md)

---

## 🤝 参与贡献 (Contributing)

欢迎提交 Issue 与 Pull Request 共同完善本项目！

1. **Fork** 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交修改 (`git commit -m 'feat: Add some AmazingFeature'`)
4. 推送至分支 (`git push origin feature/AmazingFeature`)
5. 开启一个 **Pull Request**

---

## 📄 开源许可证 (License)

本项目基于 [MIT 许可证](LICENSE) 开源，允许免费商用、修改与分发。

---

## ⚖️ 免责声明 (Disclaimer)

- 本项目所有数据、图表与策略推演仅供量化投研技术交流与学术参考，不构成任何投资建议或买卖依据。
- 市场有风险，决策需独立。据此入市，盈亏自负。
