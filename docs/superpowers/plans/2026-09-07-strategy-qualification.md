# 逐策略资格与条件预案 Implementation Plan

> For agentic workers: follow superpowers:subagent-driven-development / test-driven-development.

**Goal:** 修复统计接线、事件零样本语义与全局捆绑，接通可验证的逐策略条件预案。
**Architecture:** 纯函数验证契约 + 事件资格 + 逐策略依赖表；主线只装配，报表与phase重放共用结果。旧未知payload仍保守。
**Tech Stack:** Python3.10+、pytest、既有HTML与JSONL。
**Spec:** docs/superpowers/specs/2026-09-07-strategy-qualification.md

## Global Constraints
核心行情不放宽；无独立验证不授权；AI非交易依据；不改变模型/密钥/代理或开启自动任务。

### Task 1: 统计验证契约（本地关键路径）
- [x] 新建src/strategy_qualification.py与tests/test_strategy_qualification.py：先写不同策略/规则/结果定义/日期/样本重复/数值缺失反例，再实现read-only load + validation + registered specs。
- [x] 保留描述性win_rate_sample_size，加入明确strategy_validation输入，禁止同型市场命中率冒充策略验证。
### Task 2: 事件语义（独立并行）
- [x] 新建src/event_qualification.py和tests/test_event_qualification.py；完整字段零炸板时回封指标not_applicable，缺字段不适用不可混淆；保留population。
- [x] 主线程集成到compute_ladder_metrics/apply_review_readiness_gates，保持收盘样本范围未知不授全市场资格。
### Task 3: 逐策略依赖与装配
- [x] 生成每个scenario的规则指纹和资格；quality/market_state/resolve/readiness仅对通过且适用策略放行条件预案。
- [x] 主报告读取显式验证文件，报表/CSV/审计/日志一致；AI失败呈确定性文字而不影响独立验证资格。
### Task 4: 盘后与盘中链路
- [x] 盘后选择有资格候选预案但不作已触发；盘中仅使用同预测版本同目标日的允许策略，失效不回退排名。
- [x] phase重放核验有效期与源输入，不提升原始策略权限。
### Task 5: 验证与交付
- [x] 正/负端到端回归（AI失败、单策略缺数据、零事件、样本错版本、核心过期、盘后待确认、盘中可确认）。
- [x] 全量pytest、3.10语法、离线真实审计前后对照、独立审查；文档明确当前证据缺口。
- [x] 提交、正常合并/同步、核验工作区及origin/master一致。

## Rulings
- 用户已在对话批准上述长期规则，沿用该设计直接实施，不重复请求方案确认。
- 本地原工作区清洁，创建codex/strategy-qualification-20260907隔离分支；不移动现有未跟踪运行数据。

## 2026-09-08 最终验收
- 沿用最初行动单总纲，全部本轮实施任务与独立审查已完成；F1–F5及同源残留已关闭。
- 全量1171 passed（3条既有pandas FutureWarning）；165文件通过Python3.10语法/compileall。
- 真实旧审计缺字段时facts_only且无交易候选；合成明确证据能盘后等待、盘中确认。未制造生产验证样本或真实收益。
- 已正常合并并推送master；随后自动缓存更新已快进拉回。本地/远端一致已实证；最终验收记录提交后再次核验SHA。
- 本地浏览器URL策略不允许视觉检查；未绕过、未冒充浏览器验收，HTML/CSV静态一致性已验证。
