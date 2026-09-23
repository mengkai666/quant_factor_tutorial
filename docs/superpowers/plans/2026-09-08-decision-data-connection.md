# 数据接通与根因告警 Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development / test-driven-development. User approved these three directions; keep changes focused on actual inputs, not more authorization gates.

**Goal:** 接通已有事件事实、真实盘中数据与研究验证链路，收敛同源告警。
**Architecture:** 原始观测不变 + 带证据事实解释 + 现有采集/回放入口 + 纯展示根因分组。
**Tech Stack:** Python3.10+、pytest、现有requests/行情Provider、JSONL/HTML。
**Spec:** docs/superpowers/specs/2026-09-08-decision-data-connection.md

## Global Constraints
不伪造事件/时间/收益或样本；不新增易通过策略；不改密钥/代理/模型/自动任务；保护原JSONL。

### Task 1: 解释已有事件并修复归档复用（主线程关键路径）
Files: src/event_facts.py (new), src/strategy_qualification.py, src/主线强度追踪.py, src/limit_events.py (only explicit archive refresh support if needed), tests/test_event_facts.py (new).
Interface: resolve_limit_event_facts(snapshot: dict, *, price_rows=None) -> dict, returns a copy with resolved canonical fields, raw evidence and derivation metadata; raw snapshot is not mutated.
- [x] 先复现93个原始值已知而规范状态全空；写ZT零/正炸板次数、DT、未知/非法/矛盾计数、错日、原始输入保留回归。
- [x] 实现源语义转换及同日可信OHLC板型；不从单个时间/换手率猜板型。
- [x] 主入口/策略输入统一消费解释结果；刷新不完整同日归档但不换权威成员。
- [x] 真实93条重放量化前后覆盖率，目标是实际字段变为可用，而非只改告警字样。

### Task 2: 盘中实际输入与真实验证（主线程；分开验收）
- [x] 盘点并复用现有行情客户端/时间窗/预测绑定，用真实时刻采集输入，新增可运行采集入口；失效、缺来源、过期时不能回填成早盘观测。
- [ ] 清点真实价格、历史阶段及预测发布日期；明确定义样本外能验证的现有策略和执行协议。旧宽度proxy禁止用于真实收益声明。
- [ ] 运行可复现研究；必要执行假设在实施前明确，不把没有样本的检查报告说成已完成真实策略验证。

### Task 3: 同源告警归并（独立worker，和Task1不交叉写文件）
Files: src/data_diagnostics.py (new), src/recap_panels.py, tests/test_data_diagnostics.py (new).
Interface: build_data_diagnostics(quality: dict, *, phase_confirmation=None) -> list[dict]; each has code/status/title/impact/recovery/affected_strategies/details; renderer preserves raw machine issues.
- [x] 写缺一份验证报告不扩成十几条、同一事件缺口跨策略合并、研究分支不当作缺行情、AI异常非行情缺失的失败回归。
- [x] 实现纯数据分组及复用既有样式的展示；所有文字安全处理，不打印私有样本。
- [x] 保留底层资格/权限不变，单条根因说明影响及恢复途径。

### Task 4: 验收与同步
- [x] 按任务执行TDD，真实数据/网络核查与禁网回归分开；不跑未经要求的全量日报发布。
- [x] 全量pytest、3.10语法、真实数据覆盖对比、限定独立审查。
- [ ] 如真实交易级样本外验证缺少定义或历史，明确停在该边界请用户确认，不以代理实验替代完成。
- [ ] 按既有授权提交、正常合并同步，保留双方运行记录，不强推。


## 2026-09-09 续接验收状态
- Task1事件到报告/三种预览的接线已修复。两项新回归均先红后绿，真实9/7四字段93/93且核心输入/正式日志不变。
- Task2采集入口已实际请求5543条公共报价；源仍是9/8收盘，时窗外且预测目标不符，未回写。真实分钟级OOS尚未完成，不能把库存盘点当验证。
- Task3独立验收224项通过（含110项诊断测试），没有新增门禁或更改授权。
- 显式禁网全量1416 passed/3既有warnings；178 Python文件通过3.10语法。最终Task1/2复核待回。
- 只做git fetch；远端有9/8的自动数据更新，尚未合并/推送，不声称已同步。

### 最终工程验收（续接本轮）
- F1冲突归档覆盖问题已关闭：独立复验使用当前调用确认原始状态、16:00来源时间、嵌入OHLC证据和成员都保留；兼容缺字段仍可补齐，默认生产归档刷新路径不变。Task1/2限定范围spec/quality PASS。
- F1修复后显式禁网全量：1418 passed，3条既有warnings，136.30s；178个Python文件3.10语法与src/tools编译通过，测试期间源码哈希不变。
- 真实9/7三种HTML、事件输入与22行CSV一致；核心模块及受保护原始文件哈希不变。
- Task2b未完成：没有把日线库存、90条预测/134条市场结果或旧广度proxy说成交易级样本外验证，没有生成passed。
- 当前保留本地开发分支和所有未提交改动；仅fetch，未合并/提交/推送或发布。
