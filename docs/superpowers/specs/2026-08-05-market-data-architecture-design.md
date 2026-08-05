# 沪深北全 A 股数据架构修复设计

## 1. 目标与成功条件

本次修复把“可生成报告”提升为“数据来源、口径和质量均可证明”。完成后系统必须满足：

1. 沪深北全部 A 股使用同一 universe，代码格式唯一且可逆。
2. A/D 使用不复权收盘价；收益、排行和回测使用前复权价格，两个口径不写入同一列。
3. 首次部署或 CI 缓存丢失时，系统能自行生成 universe 并重建价格缓存。
4. 每个交易日、每类数据都有抓取状态，可区分真实零值、缺失、部分成功、失败和陈旧。
5. 异常涨跌、复权切换、覆盖不足、来源缺失、上市状态和停牌状态均可被自动检测。
6. 严重数据问题在报告生成、邮件和 Pages 发布之前阻断。
7. 外部数据调用通过 Provider 边界替换，测试无需访问真实网络。
8. 原命令 `python src/主线强度追踪.py` 继续可用。

## 2. Universe 与代码模型

标准代码使用小写交易所前缀加六位数字：

- 上海：`sh600000`
- 深圳：`sz000001`
- 北京：`bj920117`

外部格式如 `600000.SH`、`920117.BJ`、`sh.600000` 必须先经统一转换函数标准化，业务模块禁止自行拼接代码。

`stock_universe.csv` 至少包含：

| 字段 | 含义 |
|---|---|
| `code` | 标准内部代码，唯一键 |
| `raw_code` | 六位证券代码 |
| `exchange` | `SH` / `SZ` / `BJ` |
| `name` | 证券简称 |
| `list_date` | 上市日期，可空但必须有字段 |
| `delist_date` | 退市日期，未退市为空 |
| `list_status` | `listed` / `suspended_listing` / `delisted` / `unknown` |
| `industry` | 行业，可空 |
| `source` | 主数据来源 |
| `updated_at` | 刷新时间 |

UniverseProvider 优先从 AkShare 的沪深北股票名录组合生成；若单一端点失败，可使用交易所或其他已实现端点回退。刷新时保留退市记录，不用“今天没返回”直接删除历史股票。停牌是按日行情状态，不等同于退市状态。

## 3. 价格数据模型与口径

正式价格长表使用明确字段：

| 字段 | 含义 |
|---|---|
| `date` | `YYYY-MM-DD` 交易日 |
| `code` | 标准内部代码 |
| `close_raw` | 不复权收盘价，A/D 唯一价格真源 |
| `close_qfq` | 前复权收盘价，收益与回测价格真源 |
| `trade_status` | `traded` / `suspended` / `not_listed` / `missing` |
| `source_raw` | 不复权价格来源 |
| `source_qfq` | 前复权价格来源 |
| `fetched_at` | 获取时间 |

禁止继续使用含义不明的单列 `close`。Provider 可以从一个端点同时取得两种口径，也可以用不同来源，但写入前必须经过日期、代码、来源和口径验证。

A/D 定义为当日 `trade_status=traded` 且今日与上一有效交易日都有 `close_raw` 的股票：

- `close_raw_today > close_raw_previous` 为上涨；
- `<` 为下跌；
- `==` 为平盘；
- 停牌、未上市和缺失行情不进入上涨或下跌分母。

收益、排行榜和回测只使用 `close_qfq`。不再对全表无条件 `ffill`；停牌期间可以按具体研究规则持有上一估值，但必须携带停牌状态，退市或未上市区间不可填充。

## 4. Provider 边界

新增 `src/data_sources/` 包：

- `models.py`：标准代码、抓取状态、质量结果等数据契约。
- `calendar_provider.py`：交易日列表与最近已收盘交易日。
- `universe_provider.py`：沪深北股票主数据生成和刷新。
- `price_provider.py`：不复权/前复权日线获取、增量更新和全量重建。
- `limit_pool_provider.py`：涨停、跌停及真实零值状态。
- `plate_provider.py`：个股概念板块归因。
- `fetch_status.py`：每日抓取状态存储。
- `quality_gate.py`：统一质量检查和阻断决策。

Provider 输出标准 DataFrame 和 `FetchResult`，业务代码不直接依赖 AkShare、BaoStock、腾讯、CLS 或 LongHu 的原始字段。每个 Provider 接受可注入的客户端或 fetch 函数，测试可用内存假数据替代网络。

## 5. 每日抓取状态

`data/fetch_status.csv` 使用 `(date, dataset, scope)` 作为逻辑键，字段包括：

- `status`: `success`、`zero`、`partial`、`failed`、`stale`、`not_available`；
- `source`；
- `expected_count`；
- `actual_count`；
- `message`；
- `started_at`、`finished_at`；
- `run_id`。

`zero` 只能由请求成功、响应结构合法且明确无记录产生。异常、超时、空响应或字段漂移必须记录为 `failed` 或 `partial`，不能转成零值。LongHu 只可作为盘中展示回退，不得成为历史 `success` 记录。

## 6. 质量检查与阻断等级

严重缺陷会抛出 `DataQualityError` 并阻断报告生成：

- 缺少 universe 或 universe 不含任一目标交易所；
- 正式价格缓存缺少口径、来源或交易状态字段；
- `(date, code)` 重复；
- 收盘价非正或日期/代码非法；
- 单日覆盖低于当日应交易 universe 的 90%；
- 普通股票出现不符合板块涨跌幅规则的异常跳变，且无法由公司行动/来源校验解释；
- 同一代码相邻批次出现系统性复权切换；
- 最近目标交易日价格或关键涨跌停数据为 `failed`、`partial` 或 `stale`；
- 报告目标日与交易日历、价格和涨跌停日期不一致。

警告但不阻断的情况包括非核心板块归因缺失、历史较早日期的非关键字段缺失等。质量结果写入 JSON，并由报告和站点首页显示同一状态。

质量闸门执行顺序固定为：

`准备候选数据 → 质量检查 → 原子替换正式缓存 → 因子计算 → 报告生成 → 发布/邮件`。

CI 不再对核心质量闸门使用 `continue-on-error`。诊断性、非核心检查可以保留告警模式。

## 7. 全量迁移和原子替换

迁移工具先备份旧价格缓存，再生成候选缓存：

1. 刷新沪深北 universe。
2. 根据交易日历确定重建区间，默认保留当前历史起点至最近已收盘交易日。
3. 分批获取 `close_raw` 与 `close_qfq`，每批记录抓取状态。
4. 对候选文件运行全量质量检查。
5. 只有零严重缺陷时才使用同目录临时文件和 `os.replace` 原子替换正式缓存。
6. 失败时保留旧缓存、候选文件和诊断报告，不产生半写正式文件。

迁移后旧 `price_history_cache.csv` 的单列 `close` 格式不再被生产主流程接受。兼容读取只用于一次性迁移诊断，不能静默进入计算。

## 8. 主流程拆分

数据模型稳定后，将原 4,350 行入口逐步拆为：

- `pipeline/data_pipeline.py`：日历、universe、价格、涨跌停、板块及质量闸门；
- `pipeline/analysis_pipeline.py`：强度、A/D、排行、择时和股票池；
- `pipeline/report_pipeline.py`：HTML、决策看板和站点产物；
- `pipeline/delivery_pipeline.py`：Pages、本地打开和邮件；
- `app.py`：组装与退出码。

`主线强度追踪.py` 最终只负责导入并调用 `app.main()`。拆分过程中优先迁移已被契约测试覆盖的逻辑，不复制现有数据获取实现。

## 9. 测试与验收

测试分为四层：

1. 单元测试：代码标准化、状态机、A/D、复权隔离、异常跳变、停牌和上市状态。
2. Provider 契约测试：用固定响应验证沪深北、字段漂移、零值与失败分类。
3. 迁移集成测试：从小型旧缓存重建候选文件，验证失败不替换、成功原子替换。
4. 主流程测试：严重质量缺陷时不生成/不发布；健康数据下兼容入口可完成最小离线运行。

最终验收必须同时通过：完整 pytest、compileall、全量候选缓存质量检查、冷启动演练、目标日离线主流程演练，以及对七项原始目标逐项核对。

## 10. 非目标

- 本次不引入数据库服务；继续使用可审计的本地文件，但所有正式写入必须原子化。
- 不以 LongHu 历史接口修复旧数据。
- 不在数据模型稳定前大规模重写 HTML 和决策规则。
