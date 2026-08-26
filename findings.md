# 关键发现

- 旧价格缓存混用前复权与不复权价格，不能作为新模型迁移源。
- 工作树不含被忽略的大缓存，将按真实冷启动路径验证。
- LongHu 历史日期不可信，只允许盘中展示回退。
- Universe 必须由沪深北端点自行生成，不能依赖旧 industry_cache。

## 报表层复核结论

- 连板晋级率已改为以前后交易日证券代码匹配为前提；展示同时保留分子、分母、匹配样本数和转移覆盖率。
- 当前梯队、昨日梯队和前后日匹配样本均为空或不足时，连板结论分别降级为“数据未就位”“样本不足”或“条件性可用”，不再把小样本外推为全市场结论。
- 数据可信度摘要现在统一记录主源、备用源、来源链、fallback/stale、报告日期、市场代码前缀缺失和可发布模块，便于运行后追溯。
- 主流程的 universe 覆盖率不再用部分 security master 记录冒充完整市场；无证券池时 raw/qfq 价格覆盖率按 0 处理，确保质量闸门能正确阻断。
- 首屏质量卡已提前到股票池之前，用户先看到数据状态和发布范围，再看到观察名单或策略内容。
- 当前工作树没有完整 `data/price_history_cache.csv`，因此只能完成代码级和契约级验证；真实历史缓存审计需在包含完整缓存的运行环境重新执行。


## 2026-08-17 参考项目确认

- 用户此前提供的两个参考项目已从 Codex 历史会话确认：`shy3130/tickflow-stock-panel` 与 `charliedream1/ai_quant_trade`。
- TickFlow 值得迁移的是 Provider capability/schema/freshness 契约、统一 repository 查询层与显式任务状态，不照搬其完整前后端平台。
- ai_quant_trade 值得迁移的是多源 fallback、统一代码/字段标准化、单源失败继续尝试且保留错误链路，不照搬其示例集合结构。
- 当前阶段目标不是继续增加数据源数量，而是清除旧报告模块绕过 Provider、名称解析、价格矩阵和 QualityGate 的旁路。

## 2026-08-17 端到端报告完整性根因与结论

- GitHub Actions 的真实入口是 `python src/主线强度追踪.py`；旧审计只修 `legacy_tracker.py` 不能覆盖线上路径。
- `主线强度追踪.py::_load_name_resolution()` 原先没有读取权威 `security_master`，导致 GitHub 冷环境缺少行业缓存时中文名称退化；现统一优先级为 `industry < classified < universe < security_master < limit_pool`。
- `phase_resonance._build()` 原先只向渲染层传递 `reps_html`，丢失 `build_representatives()` 的结构化代表股，导致最终发布层无法检查数量、中文名称覆盖率和代码兜底；现同时保留 `representatives`。
- 主流程原先捕获并吞掉 `publish()` 异常，CI 会在站点发布失败后继续部署 Pages 和发送邮件；现 GitHub Actions 环境直接抛出，邮件仅在发布成功后发送，workflow 部署前再校验主报告与归档报告。
- 新增 `report-integrity/v1` 元数据与发布前门禁，统一检查报告/行情日期、四象限板块和代表股非空、中文名称、价格覆盖率、核心阻断项以及降级来源披露。
- `publish_site.publish()` 在创建站点目录或写入归档、latest、index、dashboard 前校验最终 HTML；无元数据或无效元数据时不产生任何站点文件。
- 个股代表模块此前虽然优先读取 `security_master`，却把来源标签记成 `universe`；现显式分别传入两套主数据，保证名称值和血缘标签同时正确。
- 降级来源门禁从“存在任意一条披露”收紧为“每个 degraded/fallback/partial/stale 模块都必须有以模块名标识的来源披露”，避免一个模块的说明掩盖另一个模块的静默降级。
- 旧单测曾被本机真实 `security_master.csv` 污染：测试伪造名称被权威主表覆盖。修复方式是显式注入测试自己的 `NameResolution`，避免环境依赖而不削弱生产优先级。
- 全量测试暴露 `legacy_tracker.py` 使用 `SECURITY_MASTER_CACHE` 却遗漏导入，已补齐并用目标测试及全量测试验证。
- 真实缓存冷环境验证已补做：刻意让 `industry_cache.csv` 不存在，只读使用主工作区真实价格、证券主表、证券池与涨停历史；四个象限各生成 6 条代表股，共 24 条，中文名称覆盖率 100%，代码兜底 0，价格覆盖率 100%，最终生成的主报告 HTML 通过门禁。
