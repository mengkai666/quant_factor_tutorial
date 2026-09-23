# 多板块盘后简报 Implementation Plan
> For agentic workers: use superpowers:subagent-driven-development or executing-plans. Use TDD. Main owns critical data/integration; bounded renderer work may run independently.
**Goal:** 用真实数据给出多板块各3只和近5日多板股，替换诊断式报告正文。
**Architecture:** 纯模型 → 纯视图 → 输入/输出适配器，研究观察与交易许可隔离。
**Tech Stack:** Python>=3.10、现有pandas/raw-bar/calendar、静态HTML/CSS、pytest。
**Spec:** docs/superpowers/specs/2026-09-09-multi-sector-brief.md

## Global Constraints
不伪造行情/时间/收益/验证；不更改交易许可；保护现有未提交代码和正式记录。不新增外部依赖、AI、自动任务或实际发布。

### Task1 数据模型（主线程关键路径）
Files: src/research_brief.py; tests/test_research_brief.py
Interface: build_research_brief(context, *, price_rows=None, history_rows=None, trading_days=None, history_days=None, raw_bars=None, max_sectors=5, stocks_per_sector=3, recent_window=5, recent_limit=20) -> dict.
- [x] 写失败用例：两个板块各4只必须各取3，quality未验证也能观察；断板与N天M板分离，错日/未来无污染，缺价格不借用上日。
- [x] 实现权威池标准化、日期约束、稳定排名、真实参考价、近5日多板轨迹、事实性市场结论。
- [x] 跑目标测试，保持输入不变及旧权限独立。

### Task2 视图（独立文件范围）
Files: src/research_brief_view.py; tests/test_research_brief_view.py
Interface: render_research_brief(brief, *, embedded=False) -> str，完整消费冻结的JSON契约。
- [x] 先写真实HTML断言：5个板块各3行、近期多板轨迹、转义、不展示审批/缺数据清单、响应式。
- [x] 实现批准的冷白多板块视图；缺可选项直接不绘制，不堆未知。
- [x] 完成目标测试和简短报告，不修改模型/主入口。

### Task3 输入、输出与主入口（主线程）
Files: src/research_brief_io.py; tools/render_research_brief.py; src/主线强度追踪.py; tests/test_research_brief_io.py
- [x] 写tmp目录回归：读取审计/每日事实、同日raw与qfq切片、日历和近期权威快照；不重写正式数据。
- [x] 写独立watchlist/recent_boards CSV与brief JSON/HTML，原子替换输出，不覆盖输入或旧执行CSV。
- [x] 接入主报告/发布看板使用同一brief，保留真实性发布校验；不会执行实际发布来验收。

### Task4 真实验收与交付
- [ ] 只读使用9/7和最新可用权威收盘快照，按需补选中股票同日OHLC，不补造盘中历史。
- [ ] 核对多板块3股、近期多板表、价格日期和HTML/CSV一致；查看页面而不是只数测试。
- [ ] 限域独立审查、禁网全量测试、3.10语法与输入哈希核验。
- [ ] 更新说明、打开新版报告，保留旧开发页归档；无用户要求不合并推送。

## 最终验收记录（进行中）
- 9/7：5×3重点股，57只完整近期多板。9/9：5×3，49只（20展开+29折叠）；30/30所需同日OHLC取得成功。
- HTML/JSON/两类CSV与原始每日涨停池及同日价格切片逐项相符；受保护的原始审计、日快照和两份正式日志哈希不变。
- 独立审查发现历史不全空表、报价日期别名冲突、CLI日历覆盖3个P2；均已先红后绿修复，补充缓存/审计碰撞和空日期别名正控，95项特性测试通过。
- 浏览器选取现有file URL被安全策略拒绝；没有改用其他浏览器或转发服务绕过。已检查实际HTML正文和DOM，但未声称浏览器视觉验收完成。

## 收尾结果
模型/视图/输入与主入口完成；原3个P2和R1限定复验通过。1517测试通过、3既有warnings，187文件3.10语法/编译通过。两份真实HTML、JSON、完整CSV与源数据一致；主报告和原9/7页已安全备份后替换。浏览器视觉自动检查因file URL策略未完成，未绕过限制；这不冒充浏览器实测。未发布或推送远端。详见output/multi_sector_brief_20260909/completion.json及acceptance.json。
