# 数据获取链路复盘 — 速度 / 准确性 / 可靠性

> 日期: 2026-09-12 | 范围: 从数据源到落库的抓取链路（不含报告渲染与策略逻辑）
> 方法: 代码链路核查 + 本机真实接口实测 + 历史跑批日志比对 + 本地缓存体检

## 0. 口径与边界（先说清楚什么是实测、什么是推断）

- 本次复盘**只读**：没有修改任何代码，没有运行主入口 `src/主线强度追踪.py`（它会写缓存、重建报告，且默认带发布与邮件），没有发布、没有推送、没有发邮件。
- 下文每条结论都标了来源类型：
  - **实测**：本轮在本机真实发起请求 / 真实读缓存测出来的数字。
  - **日志**：来自仓库里已有的跑批日志，日期早于本轮。
  - **代码**：读代码得到的事实（含行号）。
  - **未定位**：明确没查清的，不猜。
- 实测环境：本机 Windows，直连（`src/data_sources/http_session.py` 里 `trust_env=False`，绕过系统代理），2026-09-12。CI 出口环境不同，速率会变。

---

## 1. 速度基线：本轮实测

| 环节 | 实测 | 换算 | 判定 |
|---|---|---|---|
| 腾讯批量快照 `qt.gtimg.cn`，800 只/批 | **0.32s** | 5538 只 ≈ **2.2s**（7 批串行） | 不是瓶颈 |
| 东财逐股板块归因，16 并发 | **35.8 只/s**（120 只 3.35s，0 失败） | 1400 只 ≈ **39s** | 主要网络开销 |
| `build_phase_resonance`（含 90 个板块指数拉取） | **17.64s** | — | 主要网络开销 |
| `build_trend_regime`（全市场 MA120） | **2.97s** | — | 可接受 |
| `build_prediction_review`（6.34MB jsonl） | **0.12s** | — | 不是瓶颈 |
| `build_scenario_calibration` | **0.12s** | — | 不是瓶颈 |
| `load_phase_snapshots`（1.40MB） | **0.09s** | — | 不是瓶颈 |
| 全量读 75MB 价格缓存 | **1.02s**（全列）/ 0.74s（取 3 列） | 一轮读 7 次 ≈ 7s | 需消除重复读 |

**缓存体检（实测）**

| 文件/目录 | 体量 | 状态 |
|---|---|---|
| `data/price_history_cache.csv` | 75.76MB，1,056,135 行，210 个交易日（2025-11-04 → 2026-09-10） | 最新日 5538 只**全覆盖**；raw 列同样 5538，仅 09-08 少 9 只 |
| `data/price_slices/` | 150 个 gzip | 已顶到 `KEEP_SLICES=150` 上限 |
| `data/report_prediction_history.jsonl` | 6.34MB | — |
| `data/cninfo_announcement_cache.csv` | 2.16MB | 窗口 15 天 |
| `data/raw_ohlc_slices/` | 3 个 json | 刚起步 |
| `data/report_daily_snapshots/` | 23 个 json | 窗口 250 天 |

**完整跑批**：最后一次带计时日志是 **107.8s**（`output/_timed_run_after.log`，2026-08-27，日志）。

---

## 2. 速度：还没解决的问题

### P0-1 · 73 秒空窗 = **AI 研判调用**（已定位、已修复）

- **日志证据**：`output/_timed_run_after.log` 从 `[27.4s] 📦 板块指数缓存已覆盖 … 复用` 直接跳到 `[100.4s] [今日决策] 已写出统一股票池 14 只`，中间 **73 秒没有任何输出**。
- **隔离副本 + cProfile 实跑复现**（2026-09-12，副本位于系统临时目录，不触碰正式报告与归档）：
  该空窗区间里、紧接着"板块指数缓存复用"之后的**第一个**动作就是 AI 研判 ——
  ```
  📦 板块指数缓存已覆盖 2026-06-22~2026-09-10, 复用 (90 个板块)
  [警告] AI 研判 API 返回 503 (模型 claude-opus-4-8 第1/3次) …
  ```
  profile 里 `ai_rebound.run_guarded_ai` 累计 **29.1s**（本次中转不可用，3 次 503 + 1 次 403）。
- **为什么 8/27 那次是"静默"的**：成功路径原先**不打印任何日志**。一次成功的远端推理于是
  在日志里变成一团黑洞。本次跑批中转故障、打印了警告，反而把这段暴露了出来。
- **最坏阻塞比原先估计的更严重**：本机 `.env` 实际是 `AI_PRIMARY_MAX_ATTEMPTS=3` +
  `AI_REQUEST_TIMEOUT=240`，加备用模型 1 次 = **最坏 960s（16 分钟）**堵在报告主链路上。
  （代码默认值是 2 次 / 120s，我先前按默认值估成 360s，低估了。）
- **本轮已排除**（实测）：`build_phase_resonance` 17.64s、`build_trend_regime` 2.97s、
  `build_prediction_review` / `build_scenario_calibration` / `load_phase_snapshots` 均 < 0.15s、
  `src/decision_dashboard.py` 内无任何网络调用。
- **✅ 已修复**：见第 7 节。


### P1-2 · 东财板块归因一天跑两轮 — ⚠️ **本条已证伪，不需要修**

- **代码**：`src/主线强度追踪.py:896`（源头补救）与 `:1004`（主线梯队）对**同一天**各调一次 `attribute_codes`。
- **❌ 我最初的判断是错的**。我把日志里的"**待抓** 1386 只"当成了"实际发出 1386 次请求"，
  于是得出"两轮重复抓取合计 ~62s"。用带 cProfile 的真实跑批复核后：
  - 第二轮日志原文是 `1386 只待抓（缓存命中 847 只，负缓存跳过 382 只）`；847+382 = 1229，
    **恰好等于第一轮抓到的数量**，实际新抓只有约 **157 只**；
  - `data/em_stock_plate_cache.csv` 每个交易日稳定存 **1700~1930 条**（2026-09-10 有 1737 条），
    第一轮做的是真实工作，第二轮基本全命中；
  - cProfile 实测热缓存下 `attribute_codes` 两轮合计 **0.026s**。
- **结论**：缓存已经在两轮之间生效。合并调用最多省下约 157 次请求（个位数秒），
  与"30~60s"差一个数量级 —— **不值得为此改动主流程结构，不做。**
- **教训**：日志里的"待抓"是**候选数**，不是请求数。凡要据此判断请求量，必须先看同一行的缓存命中数。


### P1-3 · 催化归因是纯串行的逐股循环，无并发、无负缓存

- **代码**：`src/catalyst_attribution.py:462` `for _, row in focus_df.iterrows():` → 串行逐只调用 `attribute_stock`（同文件 `:45` `timeout=15`）。
- **自述成本**：同文件 `:452` 注释 "单只 3-4 秒（三路串行 + 1s 节流），6 只 focus_pool ≈ 20-24 秒"。当前 `focus_pool` 是 **14 只** → 约 **42-56s**。
- **当前为什么没炸**：调用点在 `主线强度追踪.py:6612` 的 `if _policy.allow_focus_pool:` 里，`observation` 模式下被挡住（对应日志里 `if False and _policy.allow_focus_pool` 的 6393 行是更早的死分支）。**一旦进入 decision 模式，这笔钱每天都要付。**
- **对照**：`src/em_stock_plates.py` 已经做对了 —— 并发 + 当日本地正/负缓存（`:46`、`:260-294`）。催化归因没有对齐。
- **建议**：加并发池 + 当日负缓存，或把催化归因从主链路挪到异步后置。

### P2-4 · 价格缓存的"重复全量消费" —— cProfile 找到了比 I/O 严重得多的一处 ✅ 已修

- **原判（偏轻）**：`phase_resonance.py:373/564`、`data_selfcheck.py:88`、`主线强度追踪.py:2186`、
  `price_slices.py`、`tools/audit_data_integrity.py:557` 各自全量读一次，实测 1.02s/次，一轮 7~10 次 ≈ 7-10s。
- **cProfile 揭示的真问题**：真正的浪费不是"读"，而是**把整份 100 万行价格表 materialize 成 dict**：
  - `decision_dashboard.build_today_decision` 被**渲染层调用 5 次**（`812` / `843` / `3155` / `3815` + 主流程 `6837`）；
  - 每次都会走到 `execution_contract.build_execution_contract` → `_exact_raw_closes` → `_as_rows`
    → `price_df.to_dict("records")`，**只为取当天那十几只股票的一列收盘价**；
  - profile：`build_execution_contract` 累计 **84.2s**，其中 `_exact_raw_closes` 83.5s、
    pandas `to_dict` **29.8s 自身耗时**（44 次调用）。
- **实测与修复**：见第 7 节（单次 4.36s → 0.23s，**19×**）。
- **仍需单独设计、本轮未动**（已量化，理由见第 7 节的"未修项"）：
  - `limit_ratio_factor._compute_ad_cache` 实测 **16.3s/次 × 2 次**（其中 CSV 读取只占 0.95s，
    其余是百万行的逐元素运算）。两次计算都是**合法**的：第一次算在价格缓存被写入**之前**，
    写入后 `(mtime_ns, size)` 指纹失效，第二次必须重算。
  - `research_brief._price_index` 实测 **6.37s/次 × 2 次**；第二次 `build_research_brief`
    是设计使然（补完 OHLC 后必须重建，`research_brief_io.py:154`），不能简单去掉。


### P2-5 · 超时与重试最坏叠加，且全局兜底会留半份数据

- **代码**：
  - AI：`ai_rebound.py:87` 代码默认 `timeout=120`、`:84` `MAX_ATTEMPTS=2`；但**本机 `.env` 覆盖成
    `AI_PRIMARY_MAX_ATTEMPTS=3` + `AI_REQUEST_TIMEOUT=240`**，加备用模型 1 次 = 最坏 **960s（16 分钟）**。
    我先前的"360s"是按代码默认值估的，低估了。✅ 已修（见第 7 节）。
  - 腾讯批量：`主线强度追踪.py:2390/2477` `timeout=10` + retry，全市场 7 批。
  - `GLOBAL_TIMEOUT = 300`（`主线强度追踪.py:3135`）触发时 `:3290-3292` 仍把已抓到的 `new_rows` 落库。
- **已知后果**：`src/price_slices.py:46-53` 记录了 2026-09-01 事故 —— CI 的 baostock 整段抓取撞上 `GLOBAL_TIMEOUT` 被 break，已抓的 ~840 只 × 60 个交易日**照样落库**，每天只有全市场的 15%，把 58 天的 sentiment 覆盖污染成 ~840。（后续用 `SLICE_MIN_COVERAGE` 挡住了切片导出，但落库本身仍在写。）
- **建议**：超时中断时，要么整批不落库，要么落库时显式打上"部分覆盖"标记并让下游按残缺处理。

### P2-6 · 收尾固定开销是"每天做全量"

- **代码**：`data_selfcheck.py:140` subprocess `timeout=900`（并且自己先全量读缓存，再由审计脚本重读）；`cache_budget.py:192/206` 对每一份缓存**整读整写**（CSV 走 `pd.read_csv` + `to_csv`）。
- 这些都是"每天一次的全量重算"，在只增量更新一天的场景下属于纯浪费。

---

## 3. 准确性：还没解决的问题

### A1 · 两套代码标准化实现不一致 — ⚠️ **影响是潜在的，不是正在发生的** ✅ 已修

- **代码**：
  - `src/data_sources/models.py:38-45`：`4` / `8` 开头或 `92` 开头 → `bj`；`5/6/9` → `sh`；`0/1/2/3` → `sz`。
  - `src/report_logic.py:2090-2096`：只有 `920 / 430 / 830 / 870 / 400` → `bj`，**其余一律落 `sz`**。
- **⚠️ 我把影响说重了**。实测核对了全库证券池（`price_history_cache.csv` 5556 个代码、
  `涨停历史缓存.csv`、`security_master.csv`、`industry_cache.csv`、`stock_universe.csv`）：
  首位分布**只有 `0 / 3 / 6 / 9`**（9 开头那 333 个是北交所 920 段），
  **两套规则在现有数据上分歧条数 = 0**。所以 `832xxx`/`871xxx`/`88xxxx` 的错位
  今天不会发生，它是"哪天扩容到北交所存量段或基金就静默错位"的定时炸弹，不是正在出血的伤口。
- **仍然修了**（便宜、且值得防）：收敛到 `src/stock_code.py` 一份判据，两侧都 import 它。
  `5xxxxx`（沪市基金）与 `900xxx`（沪市B股）这两段旧实现确实判错（判成深市），一并纠正。
  顺带：无法确定的段（如 `7xxxxx` 配股/申购）不再被猜成深市，改为留空。
- **预期收益要如实说**：**今天的收益是 0**，收益在"不再有人写第三份判据"。

### A2 · 涨停池源日期 — ⚠️ **我提的修法错了，已回滚；项目原有的处置更完整**

- **我最初看到的事实**（没错）：`src/data_sources/limit_pool_sources.py:162-175` 只校验 `rc == 0` 和
  `data.pool` 存在；provider 把**请求日**当兜底传给 `limit_event_provenance`；
  **没有**"源报的日期 ≠ 请求日就拒绝"的分支。
- **我据此做了**：在 `LimitPoolProvider._fetch_pool` 里加 `_source_day_mismatch`，不一致就按源失败处理。
- **❌ 这是错的，全量回归当场打红 5 条既有测试**。查明后：项目**早就有**一套更完整的处置 ——
  1. 东财 `qdate` 由 `limit_event_provenance` 抽成 `trade_date` **证据**（`trade_date_evidence` 保留字段名与原值）；
  2. `build_limit_event_snapshot` 把它统计成 `quality.trade_date_mismatches` /
     `record_trade_date_mismatches` / `provenance_trade_date_mismatches`（`src/limit_events.py:235-237`）；
  3. `src/recap_panels.py:181` 把该计数**渲染成报告里的披露**。
- **后果**：我的"提前拒绝"会让那个计数永远是 0 —— 把一套现成的诊断机制变成**死代码**，
  而且真遇到陈旧源时只剩一句"源失败"，**丢掉了"它答的是哪一天"这个最关键的信息**。
  项目的既定哲学是**标注**（并逐条披露），不是**丢弃** —— 与"降级模块必须逐条披露"同源。
- **✅ 处置**：回滚 provider 改动。
  留下 `tests/test_limit_pool_source_day.py` 作为**契约锁定**：断言"错日源照常入库、保留原始证据、
  且必须在归档质量里数得出来"，并把上面这段理由写进文件头和断言消息 —— 下次有人（包括我）
  再想"提前拒绝"会先看到红灯和原因。
- **教训**：动手前应先跑一次全量回归。这 5 条测试**一直都在**，它们就是这道问题的规格说明。



### A3 · 相邻日 ffill 顶替当日

- **代码**：`主线强度追踪.py:3361` `_price_matrix(price_df, 'qfq', allow_legacy=True).ffill()`（N 日涨幅）；`:3452` 同类调用；`legacy_tracker.py:2033` `pivot(values='close').ffill()`。
- **问题**：停牌/缺口用**前一交易日**报价顶替当日。这与 `progress.md` 2026-08-05 Task3 确立的"停牌排除、无陈旧 ffill"原则相冲突。需要确认这是收益计算的有意为之，还是残留；无论哪种，都应该显式标注，而不是默认打开。

### A4 · 冲突数据静默择一

- **代码**：`price_provider.py:677-700` `merge_price_frames` 用 `_first_number` / `_first_text` 取**首个非空**，两侧冲突不记录、不告警。

> 附注（**不是**当前日报路径的问题）：`legacy_tracker.py:1800-1819` 的 baostock 抓取用 `adjustflag="2"`（前复权），字段名却是 `close`，且 `:2011-2019` 未过 `normalize_price_frame` 直接 concat 写库。**但主入口 `src/主线强度追踪.py` 已经不 import `legacy_tracker`**，所以这是独立入口的潜在污染，不在每日链路里。要不要处理取决于这个旧入口是否还会被人手动运行。

---

## 4. 可靠性：还没解决的问题

### R1 · 非目标日的核心抓取失败只算 warning

- **代码**：`src/data_sources/quality_gate.py:316-328`
  ```python
  severity = "warning" if result.dataset == "plates" or not is_target else "critical"
  ```
- **问题**：历史日的 `universe` / `limit_pool` / `prices` 的 `partial` / `failed` / `stale` **不阻断发布**——但历史连板窗口直接吃这些数据。设计意图是"历史空洞不该拦今天的报告"，可问题是没有任何地方把"历史数据残缺"转成当天结论的置信度降级。

### R2 · "空池被当成功"的真实机制 — ⚠️ **我原来的修法建议是错的**

- **事实**：`quality_gate.py:128-131` 的 `CRITICAL_FETCH_STATUS` 确实不含 `ZERO`；
  `limit_pool_provider.py` 的 `usable = {"success", "zero"}` 也确实把空池算作可用。
- **❌ 但我建议的"把 ZERO 纳入阻断"是错的**。核实后：`ZERO` **只由 `limit_pool` / `plates` 两处产生**
  （`grep FetchResult.zero` 全文只有这两个调用点），而它们返回空的语义是
  **"源答复了，这天确实没有这只池子"** —— 跌停池在平静交易日天然就是空的，
  `lianban_analysis` 也据此把 ZERO 视为 complete。把 ZERO 当阻断，会让**每个平静交易日都发不出报告**，
  而一个永远红的闸门等于没有闸门。
- **真正的缺口**是"空/陈旧"与"真的没有"无法区分 —— 这个应该用**源自己申报的日期**判，
  而不是用"结果为空"判。✅ 已按这个方向修（见第 7 节，R2 条）。
- **本轮留下的是回归保护**：`tests/test_quality_gate.py` 新增两条用例，把"ZERO 不得进入阻断集合"
  和"平静日空池必须可发布"钉住；`quality_gate.py` 里也写明了理由，防止后人再顺手加上。


### R3 · 价格补缺 Provider 不写 FetchStatus ✅ 已修

- **代码**：`主线强度追踪.py` 里构造 `PriceProvider(...)` 时**没有传 `status_store`**；结果只写进本地 `meta` 字典，不 `record`。
- **后果**：价格补缺的成功 / 部分 / 失败 / 覆盖数**不进抓取状态契约**（`data/fetch_status.csv`），也就进不了质量闸门和审计。这是唯一一条每天真跑、却完全不在状态契约内的抓取路径。
- **✅ 已修**：见第 7 节。

### R4 · LongHu API 陈旧检测缺失 ✅ 已修（且拿到了实测证据）

- **代码**：`src/limit_ratio_factor.py` 的 `_fetch_longhu_sentiment` 直接把请求日写进 `"date"`，**从不读取接口返回的日期**；异常静默 `return None`。
- **为什么防护失效**：调用方用 `api_date == d` 判陈旧，而 `api_date` 正是自己写进去的请求日 —— **自证循环，永远成立**。
- **✅ 2026-09-12 实探坐实**（直接对接口发两次请求）：
  - 请求 `Day=2026-09-10` → 返回 `date = 2026-09-11`，`nums = {SZJS:643, XDJS:4870, ZT:40, DT:21}`
  - 请求 `Day=2026-07-15` → 返回 `date = 2026-09-11`，`nums` **逐字段完全相同**
  - 即接口**确实忽略 `Day` 参数**、永远返回最近一个快照；而它自己报了 `date` 字段。
- **✅ 已修**：见第 7 节。
- **主入口另有的一套**：`主线强度追踪.py:494-519` 用进程内 key 比对（"API 连续 3 天返回同一快照就停止"，见 `run_perf_after.log:14`）。它现在与源日期判据互为补充。


### R5 · CI 里有两道 `continue-on-error` 的门

- **代码**：`.github/workflows/daily_run.yml:145` universe 刷新失败不阻断；`:161` 质量预检不阻断。
- 叠加 `actions/cache` 的 LRU 行为（key 用 `github.run_number`，靠 `restore-keys: market-data-cache-` 前缀匹配，`daily_run.yml:103-125`）→ 缓存没恢复时 CI 走**冷启动全量重建**：慢，而且证券口径会变。
- 另外 `data/price_history_cache.csv` 在 `.gitignore` 里、只靠 actions/cache；`price_slices`（150 天 gzip）是它唯一可回溯的副本——`price_slices.py` 模块头那个 2026-08-28 "CI 有数据、本地整天是空的" 事故就是这条链的产物。

### R6 · 腾讯快照失败静默

- **代码**：`主线强度追踪.py:2416-2423`、`:2438` 失败返回 `[]`；`:3203-3207` 只打印"覆盖不足 / 快照异常"再回落 baostock。回落本身是对的，但失败这件事没有进状态契约。

---

## 5. 建议的处理顺序（同日实施后的实际状态）

**先做**
1. ✅ `cProfile` 定位那 73 秒 → **定位为 AI 研判调用**，已修。
2. ❌ 合并东财归因两轮 → **证伪，不做**（见 P1-2）。
3. ✅ 代码标准化收敛到单一真源 → 已修（但把影响从"正在发生"修正为"潜在"）。

**再做**

4. ⚠️ ~~涨停池加"源申报日期 ≠ 请求日即拒绝"~~ → **实施后回滚**：与项目已有的
   "记录 + 归档计数 + 报告披露"机制冲突，且打红 5 条既有测试。见 A2。
5. ✅ `PriceProvider` 接上 `FetchStatusStore` → 已修。
6. ⚠️ ~~把 `ZERO` 纳入核心阻断~~ → **判断错误，改为加回归保护**（见 R2）。
7. ✅ LongHu 补陈旧检测（用接口自报的 `date`）→ 已修。
8. ⚠️ "消除重复全量读" → **拆成两半**：价格表的重复 materialize 已修（19×）；
   A/D 与 `_price_index` 的二次计算属设计使然，已量化但未改。

---

## 6. 明确没做的事（不要当已完成）

- **没有**在正式工作区运行 `src/主线强度追踪.py`：它写缓存与报告、默认带归档/邮件。
  定位 73 秒用的是一次**隔离副本**（复制到系统临时目录，排除 `*.bak.*` 与 `*.candidate.csv`），
  并在副本里带 `cProfile` 跑完整主入口；正式 `data/`、`output/`、审计与归档**没有被触碰**。
- **没有**在 CI 环境实测速率；第 1 节的数字是本机直连测的，CI 出口不同会变。
- **没有**做浏览器视觉验收，**没有**实际发布 GitHub Pages，**没有**推送远端，**没有**提交。
- 第 3 节 A3（`ffill` 顶替停牌日）**没有**判定是有意为之还是残留 —— 需要业务口径确认后才好动。

---

## 7. 修复记录（2026-09-12，同一轮内完成）

全部按"先写失败测试、再实现"的顺序落地；每条都标注了**实测**的收益或明确说明收益为 0。

| # | 问题 | 改动 | 测试 | 实测效果 |
|---|---|---|---|---|
| 1 | AI 研判可阻塞 16 分钟、且成功时静默 | `src/ai_rebound.py`：新增墙钟总预算 `AI_TOTAL_BUDGET`（默认 180s，可用环境变量覆盖）；单请求 `timeout` 被**剩余预算**夹住；预算耗尽即停止重试并记 `budget_exhausted`；`run_guarded_ai` 在阶段边界**无论成败都打印一行**含耗时的状态并把 `elapsed_seconds` 写进 lineage | `tests/test_ai_budget.py`（5 条） | 最坏阻塞从 **960s → 180s**；日志黑洞消失 |
| 2 | 整份百万行价格表被 materialize 5 次 | `src/execution_contract.py`：新增 `_target_day_frame`，**先按报告日向量化切片再转 dict**；非 DataFrame / 缺日期列 / 切片异常一律原样退回 | `tests/test_execution_contract.py`（+2 条） | 单次 **4.36s → 0.23s（19×）**；一轮省约 **20s** |
| 3 | 涨停池"源日期不校验" | ❌ **先实施、后回滚**。曾加 `_source_day_mismatch` 在 provider 层拒绝错日源；全量回归打红 5 条既有测试，查明项目已有"记录 + 归档计数 + 报告披露"的完整机制，回滚。改为留下契约锁定测试 | `tests/test_limit_pool_source_day.py`（4 条，锁定既有语义） | **净收益 = 0**；避免了一次把诊断机制改成死代码的破坏 |
| 4 | 价格补缺不进抓取状态契约 | `src/主线强度追踪.py`：新增 `_record_price_gap_fetch_status`，在三个出口（无缺口提前返回 / 异常 / 正常收尾）统一写 `FetchResult(dataset='prices')`；口径是"**缺口补上了吗**"——`failed` 与"有缺口但一只没抓"都落 `failed`，不写 success | `tests/test_price_gap_fetch_status.py`（6 条） | 价格抓取首次进入质量闸门与审计视野 |
| 5 | LongHu 陈旧判据是自证循环 | `src/limit_ratio_factor.py`：读取接口自报的 `date` 与请求日比对，不一致即丢弃；结果里的 `date` 改为来自源申报字段 | `tests/test_longhu_staleness.py`（4 条） | 实探坐实接口忽略 `Day` 参数（两次请求返回同一份 `date=2026-09-11`） |
| 6 | 代码标准化有两份判据 | 新增 `src/stock_code.py`（`infer_exchange`）作为唯一真源；`data_sources/models.py` 与 `report_logic.py` 都改为 import 它；顺带纠正 `5xxxxx`（沪市基金）、`900xxx`（沪市B股）被误判成深市，无法确定的段不再猜 | `tests/test_stock_code_single_source.py`（42 条参数化） | **今天的收益是 0**（现有数据两套规则分歧 = 0）；收益在"不再有人写第三份" |
| 7 | ~~ZERO 纳入阻断~~ | **未按原建议实施** —— 核实后 ZERO 只由 `limit_pool`/`plates` 产生，空池在平静日是合法结果，加阻断会让平静日发不出报告。改为在 `quality_gate.py` 写明理由 + 两条回归用例钉住语义 | `tests/test_quality_gate.py`（+2 条） | 防止后人"顺手加上" |
| 8 | 重复消费价格表 | 同第 2 条 | — | — |
| 9 | **测试套件往生产 AI 缓存里写记录**（实施过程中发现） | `tests/test_report_integration.py`：`test_ai_gate_skips_facts_only_and_sanitizes_observation` 与 `test_guarded_ai_uses_configurable_default_request_timeout` 走的是**成功**路径，`run_guarded_ai` 会把结果写进 `AI_OUTPUT_CACHE_DIR`，而这两条没重定向 → 直接写进生产 `data/ai_output_cache/`（实测：一次全量回归就留下一条 `sanitized` 记录，`cached_at` 与回归开始时间对得上）。给两条都接上 `tmp_path`，并加断言确保缓存确实落在临时目录 | `tests/test_report_integration.py`（+1 条断言） | 测试不再污染生产缓存；已清除那条夹具记录（指纹只来自测试输入 `{"breadth":0.6}`，非生产数据） |

### 未修项（已量化，**故意不动**）

- **`limit_ratio_factor._compute_ad_cache` 16.3s × 2/轮**。两次都是合法计算：第一次发生在
  价格缓存被写入**之前**，写入后 `(mtime_ns, size)` 指纹失效，第二次必须重算。
  想省掉一次就要改"谁在什么时候算 A/D"的时序，而 A/D 口径有事故史
  （`src/ad_breadth.py` 模块头记录的 2026-09-01 那次 12 个历史日被窄口径覆盖），
  **必须单独设计与验证，不该顺手改**。`usecols` 限列只省 0.2s（实测 0.95s→0.75s），不值得。
- **`research_brief._price_index` 6.37s × 2/轮**。第二次 `build_research_brief` 是**设计使然**
  （补完 OHLC 后必须重建，`research_brief_io.py:154`，注释写明"新观察到的矛盾不得恢复旧名单"），
  不能直接删。可优化方向是把与 `raw_bars` 无关的价格索引结果复用或向量化，需要单独验证冲突检测语义。
- **`catalyst_attribution` 串行逐股**（P1-3）。当前被 `observation` 模式的 `allow_focus_pool`
  挡着，进入 `decision` 模式才会每天付 42-56s。改动属于"加并发 + 当日负缓存"，与
  `em_stock_plates.py` 已有做法对齐即可，但本轮没做 —— 它不影响当下的正确性。
- **CI 两道 `continue-on-error`**（R5）。属于 CI 策略调整（会影响发布节奏），需要单独决定。

### 验证

- 新增/扩展测试文件 7 个，共 **66 条**新用例（`test_ai_budget` 5、`test_execution_contract` +2、
  `test_limit_pool_source_day` 4（契约锁定）、`test_price_gap_fetch_status` 6、`test_longhu_staleness` 4、
  `test_stock_code_single_source` 42、`test_quality_gate` +2、`test_report_integration` +1 断言）。
- **全量回归：1603 passed**，3 条既有 `FutureWarning`，201.15s。
  （中途一次 `5 failed / 1598 passed` 是涨停池 A2 造成的，回滚后复跑通过。）
- 编译校验：`python -m compileall -q src tools tests` 通过；`src/` + `tools/` 共 **119 个文件**
  通过 Python 3.10 语法解析。
- **回归后 `data/ai_output_cache/` 条目数未变化（36）** —— 直接验证了"测试不再污染生产缓存"。
- 正式 `data/`、`output/`、审计与归档**未被本轮修改触碰**。唯一例外是清掉一条测试夹具写进
  `data/ai_output_cache/` 的记录（该目录在 `.gitignore` 内，指纹只来自测试输入 `{"breadth":0.6}`）。
- **未提交、未推送、未发布**：改动全部留在工作区。本项目的既有流程要求先独立复核再提交，
  本轮没做那一步。



