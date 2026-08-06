# 执行日志

## 2026-08-05

- 创建 `codex/market-data-rebuild` 隔离工作树。
- 基线验证：13 tests passed；pip check 无冲突。
- 设计已批准，实施计划已建立，开始 Task 1。
- Task 1 完成：代码标准化、抓取状态契约、原子状态存储；28 tests passed。
- 开始 Task 2：Calendar/Universe Provider。
- Task 2 完成：沪深北 universe 冷启动、交易日历、退市记录保留；32 tests passed。
- 开始 Task 3：显式 raw/qfq 价格模型及 A/D、收益隔离。
- Task 3 核心模型完成：raw/qfq 隔离、停牌排除、无陈旧 ffill；36 tests passed。
- 开始 Task 4：LimitPool/Plate Provider 与真实零状态。
- Task 4 完成：涨跌停/板块 Provider 与零值、失败、部分成功状态；40 tests passed。
- 开始 Task 5：全量质量闸门和完整性 CLI。
- QualityGate 与新 schema CLI 完成：50 tests passed，compileall 通过；无缓存时 CLI 正确失败。
- 开始 Task 6：候选缓存验证、原子晋升、冷启动重建 CLI。
- DataPipeline 候选验证与原子晋升完成；53 tests passed。
- 开始 Task 7：报告/发布前闸门和 CI 顺序。
- 主流程报告前闸门、raw A/D、qfq 内存兼容视图和 CI 阻断顺序完成；56 tests passed。
- 开始 Task 8：应用 stage 编排与薄兼容入口。
- QualityGate 修复：放行北交所合法 30% 涨跌幅的 raw/qfq 舍入误差，且仅豁免上市后前五个实际交易日的新股。
- 全量重建完成：5537 个沪深北 A 股、184 个交易日、1,018,808 行；质量报告无 critical 问题。
- 修复分批抓取状态覆盖：`fetch_status.csv` 现在记录最终聚合状态 `5537/5537 success`。
- 入口完成真实阶段推进：legacy workflow 暴露 data/analysis/report/delivery 四个暂停边界，旧 `main()` 继续完整消费。
- 文档与运行时缓存忽略规则已更新。
- 阶段验证：73 tests passed；`python -m compileall -q src tools tests` 通过；中文入口导入验证通过。
- 完成审计补强：校验交易状态与上市/退市日期一致性，并将 `not_available` 纳入质量闸门阻断。
- 新增 `PreflightDataStage` 离线集成验证：质量通过后才执行分析、报告和交付；坏缓存不会生成报告。
- 默认应用的 analysis/report/delivery 已接入对应 Pipeline stage，未重复抓取或复制 Provider 逻辑。
- Universe 刷新增按交易所 90% 收缩护栏，静默部分响应不会覆盖既有缓存，状态会改写为 failed。
- 完成真实重建入口复跑：沪深北 universe/price 均为 `5537/5537 success`，候选通过质量闸门后晋升。
- 完成真实主命令运行：`python src/主线强度追踪.py` 退出码 0，生成报告、当日归档、决策看板和站点首页。
- 最终测试集增至 89 项，包含日期/代码/状态完整性、上市状态、离线报告阻断和 universe 收缩回归。
- 五个 Provider 均进入真实主流程：LimitPoolProvider 刷新最新闭市日并参与 preflight，PlateProvider 执行并发概念归因。
- 修复北交所旧代码 `920xxx.BJ` 被处理成 `920xxxBJ` 的问题；涨停、CLS、价格现统一 canonical 代码。
- 真实主命令复跑退出码 0；LimitPoolProvider 为 `104/104 success`，PlateProvider 为 `12/26 partial` 并按可选增强项记 warning。
- 质量规则区分核心与可选来源：价格/universe/limit_pool 失败阻断，plates 部分覆盖保留明细但不误阻断发布。

## 2026-08-06（报表层优化）

- 连板指标增加当前/昨日有效样本、前后日匹配样本、转移覆盖率、最高板数量、龙头集中度及样本状态。
- 连板复盘状态分为 `ok`、`conditional`、`insufficient`、`not_ready`，晋级率继续保留真实分子/分母。
- 数据可信度摘要补充主源、备用源、来源链、fallback/stale、新鲜度、市场前缀和可发布模块。
- 修正主流程在 security master 不完整或证券池为空时的覆盖率计算，避免虚高覆盖率绕过质量闸门。
- 独立看板和内嵌看板首屏提前展示数据质量；连板卡片展示样本可信度和状态原因。
- 验证：`pytest -q` 通过，230 passed。
