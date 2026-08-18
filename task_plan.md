# 数据源架构修复执行计划

## 当前阶段

1. 设计与迁移规范 — `complete`
2. 数据契约与抓取状态 — `complete`
3. Calendar/Universe Provider — `complete`
4. Price Provider 与全量重建 — `complete`
   - 双口径模型、候选晋升、检查点续跑和聚合抓取状态完成
   - 已完成沪深北 5537 代码、184 交易日的真实重建
5. Limit/Plate Provider — `complete`
   - 最新闭市日涨跌停池由 LimitPoolProvider 覆盖旧缓存并在 preflight 前校验
   - 东财概念归因由并发 PlateProvider 执行，部分覆盖记录 warning 并保留降级结果
6. 质量闸门与发布阻断 — `complete`
   - 北交所 30% 价格精度容差、新股上市初期豁免和老股异常检测完成
   - 上市/退市日期与交易状态交叉校验、`not_available` 阻断完成
   - 报告、发布、邮件前质量闸门和 CI 阻断完成
7. Pipeline 与入口拆分 — `complete`
   - legacy workflow 已按 data/analysis/report/delivery 真实暂停推进，并接入对应 Pipeline stage
   - 保持 `python src/主线强度追踪.py` 兼容入口
8. 全量验证与文档 — `complete`
   - 文档、审计、测试、编译、入口导入和离线报告阻断验证完成
9. 报表运行后逻辑优化 — `complete`
   - 连板样本可信度、数据血缘、覆盖率口径和首屏质量提示已完成
   - 中文名称与 legacy/qfq 价格拼接已完成，344 项测试通过
10. 参考项目驱动的数据完整性审计 — `complete`
   - 参考 `tickflow-stock-panel` 的 Provider 能力契约、统一查询入口和显式状态
   - 参考 `ai_quant_trade` 的多源 fallback、标准化输出和可观测失败
   - 已清除证券名称主数据旁路、错误来源标签、四象限结构化数据丢失和发布异常吞掉问题
   - 最终 HTML 嵌入机器可读完整性元数据，发布、Pages 部署和邮件均受同一门禁约束
   - GitHub 冷环境真实缓存验证：24 条代表股、中文名称 100%、代码兜底 0、价格覆盖率 100%
   - 最终验证：358 项测试通过，`compileall` 通过

## 执行规范

- 详细步骤见 `docs/superpowers/plans/2026-08-05-market-data-rebuild.md`。
- 每项生产行为先写失败测试。
- 正式缓存只在候选缓存通过全量质量闸门后原子替换。
- 用户原工作区未提交改动不参与此工作树。

## 错误记录

| 错误 | 处理 |
|---|---|
| `git check-ignore .worktrees` 对尚不存在目录返回 1 | 改用 `.worktrees/probe` 验证，规则有效 |
