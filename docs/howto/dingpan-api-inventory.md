# 盯盘数据接口清单 (财联社 / 开盘啦 / 东方财富备用源)

> 本文档记录「盯盘」页所依赖的**全部已实测可用接口**，供后续维护与二次开发。
> 实测日期：2026-10-17 · 全部接口均以真实请求验证过（含返回字段与语义）。
>
> 相关实现：
> - `src/data_sources/cls_client.py` —— 财联社（第一源）
> - `src/data_sources/kaipanla_client.py` —— 开盘啦（第二源）
> - `src/data_sources/em_dingpan_client.py` —— 东方财富 + 同花顺（第三源 / 备用源）
> - `src/dingpan_service.py` —— 三源降级聚合，产出 `tab_dingpan`
> - `mobile_app/index.html` —— 盯盘页前端（1:1 复刻）

---

## 一、数据源拓扑

| 优先级 | 数据源 | 定位 | 覆盖的盯盘数据块 |
|:---:|:---|:---|:---|
| 1 | **财联社 CLS 8.5.4** | 盯盘页原生口径，字段最全 | 市场概况、市场热度、涨停表现、连板梯队、资金热力图、风口、主线、直播 |
| 2 | **开盘啦 KaiPanLa 6.1.6** | 独立交叉校验源 | 涨跌停家数、炸板率、昨涨停表现、涨停天梯、盘面异动 |
| 3 | **东方财富 + 同花顺** | 备用源（两源同时失效时接管） | 涨跌停家数、封板率、连板梯队、涨停池明细 |

降级规则：**逐块独立降级**。每个数据块单独走 1→2→3 链，
实际生效的数据源记录在 `tab_dingpan.sources`，任一降级到第三源时 `degraded=true`。
第三源无法提供的块（如全市场涨跌分布、资金热力图）显式列入 `unavailable`，
前端显示占位提示，**绝不用假数据填充**。

---

## 二、财联社 CLS

### 2.1 公共协议

| 项 | 值 |
|:---|:---|
| 行情域名 | `https://x-quote.cls.cn` |
| 内容域名 | `https://api3.cls.cn` |
| 方法 | `GET`（全部参数走 query string） |
| User-Agent | `okhttp/4.9.3` |
| 公共参数 | `app=cailianpress` `sv=8.5.4` `os=android` `mb=nubia-NX709J` `ov=28` `channel=8` `sign=5b4c4c6a3b9db1dd279a0d713111c4d0` `motif=0` `net=` `province_code=4107` `uid=` `token=` |

> ⚠️ **签名陷阱**：`sign` 是对「参与签名的参数集合」算出的固定值。
> 只要额外附加未参与签名的参数，服务端立刻返回 `10012 签名错误`。
> 典型受害者：`/v1/roll/get_roll_list` 传 `rn` / `last_time` 必然失败。

### 2.2 接口清单

#### ① `GET /v2/quote/a/stock/emotion` — 市场概况 + 涨停表现 + 连板梯队
**盯盘页最核心接口**，一次拿到「市场概况」「涨停表现」两大区块。

返回 `data` 字段：

| 字段 | 含义 | 示例 |
|:---|:---|:---|
| `market_degree` | 市场热度 0–100 | `"54"` |
| `shsz_balance` | 两市成交额 | `"1.56万亿"` |
| `shsz_balance_change_px` | 较上一日 | `"-776亿"` |
| `preview_balance` | 今日预测量能 | `"1.56万亿"` |
| `preview_balance_change_px` | 预测量能增减 | `"-776亿"` |
| `up_ratio` | **封板率** | `"55.00%"` |
| `up_ratio_num` | **封板家数** | `"61"` |
| `up_open_num` | **触及（炸板）家数** | `"49"` |
| `performance` | **昨涨停今表现** | `"-1.74%"` |
| `up_open_ratio` | 高开率 | `"56%"` |
| `profit_ratio` | 获利率 | `"33%"` |
| `up_down_dis.rise_num` / `fall_num` / `flat_num` | 上涨 / 下跌 / 持平家数 | `3301 / 2128 / 138` |
| `up_down_dis.up_num` / `down_num` | 涨停 / 跌停家数 | `61 / 11` |
| `up_down_dis.suspend_num` | 停牌家数 | `10` |
| `up_down_dis.up_2/up_4/up_6/up_8/up_10` | 涨幅分桶 | `2201 / 749 / 176 / 69 / 106` |
| `up_down_dis.down_2/down_4/down_6/down_8/down_10` | 跌幅分桶 | `1447 / 433 / 150 / 46 / 52` |
| `limit_up_board.row1` | 梯队表头 | `["一板","二板","三板","高度板"]` |
| `limit_up_board.row2` | 各高度家数 | `["54","4","1","2"]` |
| `limit_up_board.row3` | 连板率（首位为行标签） | `["连板率","13%","50%","50%"]` |

**分桶语义**（`src/data_sources/cls_client.py::DISTRIBUTION_BUCKETS`，即图表从左到右 13 档）：

```
涨停  大于+8%  +6%    +4%    +2%    0%    平    -2%    -4%    -6%    -8%   小于-8%  跌停
up_num up_10  up_8   up_6   up_4   up_2  flat  down_2 down_4 down_6 down_8 down_10 down_num
```

#### ② `GET /v2/quote/a/stock/limit_up_performance` — 涨停表现同比（「查看更多」）

| 字段 | 含义 |
|:---|:---|
| `continuous_limit_rate` / `_yd` | 连板率 今日 / 昨日 |
| `limit_up_num` / `_yd` | 涨停家数 今日 / 昨日 |
| `up_ratio` / `_yd` | 封板率 今日 / 昨日 |

#### ③ `GET /v2/quote/a/plate/fund_heat` — 资金热力图

| 参数 | 取值 | 说明 |
|:---|:---|:---|
| `way` | `money_inflow` \| `change` | `money_inflow`=按主力资金，`change`=按涨跌幅 |
| `rever` | `0` \| `1` | `0`=正向榜（流入/涨幅），`1`=反向榜（流出/跌幅） |
| `days` | `1` \| `5` \| `10` | 今日 / 5日 / 10日 |
| `num` | int | 返回条数 |

> 实测：`way` 只有 `money_inflow` 与 `change` 两档真实生效，其余取值静默回落到 `money_inflow`。
> 真正的排序方向由 `rever` 控制。

**「排序规则」胶囊 → 参数映射**：

| 排序规则 | way | rever |
|:---|:---|:---|
| 主力净流入 | `money_inflow` | `0` |
| 主力净流出 | `money_inflow` | `1` |
| 按涨幅 | `change` | `0` |
| 按跌幅 | `change` | `1` |

返回 `data[]`：`secu_code` `secu_name` `change`（如 `-0.06%`）`change_num`
`money_inflow`（如 `+41.3亿元`）`money_inflow_num`

前端一次性拉取 **4 排序 × 3 时间 = 12 组**完整矩阵，胶囊切换为纯前端切换、零请求。

#### ④ `GET /v2/quote/a/plate/up_down_analysis` — 涨停池 / 连板梯队明细
参数：`date`(YYYYMMDD) `up_limit`(1=涨停)。返回 `data.continuous_limit_up[]`：
`{height, stock_list:[{secu_code, secu_name}]}`

#### ⑤ `GET /v2/quote/a/index/list` — 指数列表
返回 `data[]`：`{secu_code, secu_name, market}`

#### ⑥ `GET /v2/dingPan/mainline` — 主线机会
返回 `data.chances[]`：`{hot, continued, title, mainLine_desc, plates[]}`
每个 `plate` 含 `faucet_1/2/3`（龙头股：`stock_code` `stock_name` `reason` `rise_range`）。

#### ⑦ `GET /v2/todayTuyere` — 今日风口
返回 `data.today_tuyere[]`：`{id, title, plate_code, subject_id, interpret}`

#### ⑧ `GET /v1/roll/get_roll_list` — 电报 / 直播流
> ⚠️ **必须不附加任何查询参数**，否则 `10012 签名错误`。实测返回最近 16 条。

返回 `data.roll_data[]`：`{id, title, brief, content, ctime, level, reading_num, is_top,
plate_list, stock_list, images, subjects}`

> 注意：`plate_list` / `stock_list` 键存在但值可能为 `null`，
> 解析时须用 `x.get(k) or []`，不能用 `x.get(k, [])`。

### 2.3 已确认失效的路径（勿再尝试）

`/v2/quote/a/stock/{up_down_analysis,limit_up,up_down,rank,up_limit,down_limit,zdfb,
distribution,market_summary,overview,list,big_rise,dazhanggu,ztbao,bse,hot}`、
`/v2/quote/a/plate/{fund_flow,money_flow,rank,list,up_limit,emotion}`、
`/v2/dingPan/{plate,limitUp,index}`、`/v1/{telegraph,live/list,plate/list}`
—— 一律返回 `9004 not found` 或 `50101 小财正在加载中...`。

另：**热榜（C20）** 在 `/v2/dingPan/config` 里是 `hide=0` 的可见模块，但
`/v2/quote/a/stock/hot_rank`、`/quote/hot/rank`、`/web_quote/stock/hot_list`
等候选路径全部 `9004`。**找不到就标为不可用，不要编一个。**

### 2.4 大盘页其余模块与下钻接口（2026-10 补齐）

财联社大盘页的真实模块顺序由 `/v2/dingPan/config` 决定（19 个模块）。
之前只接了 8 个接口，页面因此「缺一大半」。以下是补齐的部分：

| # | 功能 | 模块 | 域名 | 路径 | 业务参数 |
|:--|:---|:---|:---|:---|:---|
| ⑨ | **指数报价** | C1 | quote | `/quote/index/home` | — |
| ⑩ | 指数前三大成分股 | C1 下钻 | quote | `/v2/quote/a/index/top3_component` | `secu_code` |
| ⑪ | **个股排行** | C14 | quote | `/v2/quote/a/stock/ranking` | `topic`★ `style`★ `index` `way` `rever` `deviation_num` |
| ⑫ | 排行主题字典 | C14 | quote | `/v2/quote/a/stock/ranking/topics` | — |
| ⑬ | 板块列表 | 板块 | quote | `/web_quote/plate/plate_list` | `type`(concept/industry) `rever` |
| ⑭ | 板块详情 | 下钻 | quote | `/v2/quote/a/plate/info` | `plate_codes`（逗号分隔批量） |
| ⑮ | 板块涨停家数 | 板块 | quote | `/v2/quote/a/plate/plate_up_num` | — |
| ⑯ | 板块涨停个股 | 下钻 | quote | `/v2/quote/a/plate/plate_up_stocks` | `plate_code` |
| ⑰ | 风口板块个股 | 下钻 | quote | `/v2/quote/a/plate/tuyere/stocks` | `plate_code` |
| ⑱ | **北交所个股排行** | 北交所 | quote | `/quote/xsb/bj_stock_info` | `market=bj` `way` `rever` `index` `deviation_num` |
| ⑲ | 北交所横幅 | 北交所 | **api3** | `/v3/depth/bjs/topbanner` | — |
| ⑳ | 大盘模块配置 | 骨架 | **api3** | `/v2/dingPan/config` | — |
| ㉑ | 市场解读 | 顶部 | api3 | `/api/subject/dapan/new/article` | — |
| ㉒ | **异动与炸板** | 涨停宝 | api3 | `/v3/transaction_and_blasting` | **无（零参数）** |
| ㉓ | 月度涨跌分布 | 市场概况 | quote | `/quote/golden/distribution_compose` | `date=YYYYMM` |
| ㉔ | 近五个月 | 市场概况 | quote | `/quote/golden/five_month` | — |
| ㉕ | 快讯详情 | 直播下钻 | api3 | `/v1/roll/info` | `id` |

#### 踩过的四个坑

**① 两个接口的域名，旧接口地图写错了。** `/v2/dingPan/config` 和
`/v3/depth/bjs/topbanner` 都在 **`api3.cls.cn`** 上，不在 `x-quote.cls.cn`。
走 x-quote 会得到 `503 Service Temporarily Unavailable`（`<html>...alb`），
不是 JSON —— 很容易被误判成「接口挂了」。

**② `transaction_and_blasting` 必须零业务参数。**
加 `vip=1` 或 `CTime=...` 任一参数都返回 `10012 签名错误`。
签名覆盖的参数集是固定的，**多传一个就校验失败**（少传没事）。

**③ 个股排行的 `topic` + `style` 是必填。**
缺任一个返回 `9001`。两者都要从 `/v2/quote/a/stock/ranking/topics` 取：

| name | topic | style |
|:---|:---|:---|
| 全部A股 | `all` | `TOPIC-STYLE-A-240223` |
| 连板池 | `continuous_up` | `TOPIC-STYLE-B-240223` |
| 炸板池 | `up_open` | `TOPIC-STYLE-B-240223` |
| 跌停池 | `down` | `TOPIC-STYLE-B-240223` |
| 北交所 | `bj` | `TOPIC-STYLE-C-240223` |

> 这张字典是「点开梯队看明细」的钥匙：连板梯队点开 → `continuous_up`，
> 炸板点开 → `up_open`，跌停点开 → `down`。**不需要额外接口。**

**④ `distribution_compose` 的 `date` 必须是 `YYYYMM`。**
传 `YYYYMMDD` 或留空都返回 `9000 服务器错误`。

**⑤ 北交所分页参数反直觉。** `deviation_num` 是**每页条数**、`index` 是**页码**。
默认 8 条/页太少（全市场 351 只），`deviation_num=50` 才够看。

#### 北交所 Tab 曾经整页空白

旧实现把「北交所 Tab」做成了 `[涨停池里的北交所股]` —— 当天没有北交所涨停
就是空页面。真实 App 这里是**全量 300+ 只个股的涨跌幅排行**
（`/quote/xsb/bj_stock_info`）。现已改为全量排行 + 标注哪些是涨停股。

---

## 三、开盘啦 KaiPanLa

### 3.1 公共协议

| 项 | 值 |
|:---|:---|
| 端点 | `POST https://apphwshhq.kaipanhong.com/w1/api/index.php` |
| Content-Type | `application/x-www-form-urlencoded` |
| User-Agent | `okhttp/3.12.13` |
| 路由 | `c=<模块>` `a=<动作>` |
| 公共参数 | `Red=1` `apiv=w46` `PhoneOSNew=1` `VerSion=6.1.6` `DeviceID=<uuid4>` |

> ⚠️ 未知模块/动作会被服务端拒绝并返回**非 JSON** 响应，客户端已做异常捕获。

### 3.2 接口清单

#### ① `c=FuPanLa & a=GetZDTCount` — 全市场涨跌停家数
返回：`{"ZT":"61","DT":"10","ttag":...,"errcode":"0"}`

#### ② `c=FuPanLa & a=GetZhangTingTianTi` — 涨停连板天梯
返回 `StockList[][]`，位置数组语义：

```
[0]代码 [1]名称 [2]连板数 [3]首次涨停时间戳 [4]板块代码 [5]板块名
[6]是否首板 [7]状态标记 [8]涨停天数 [9]流通市值 [10]总市值
```

#### ③ `c=DailyLimitResumption & a=GetPlateInfo_w38` — 板块涨停原因 + 市场统计
参数：`st`(条数) `Index`(分页)

`nums` 段 —— **开盘啦侧最完整的市场统计口径**：

| 字段 | 含义 | 示例 |
|:---|:---|:---|
| `SZJS` | 上涨家数 | `3301` |
| `XDJS` | 下跌家数 | `2127` |
| `ZT` / `DT` | 涨停 / 跌停家数 | `61 / 10` |
| `ZBL` | **炸板率 %** | `44.5455` |
| `yestRase` | **昨日涨停表现 %** | `-1.638` |

`list[]` 段：`{ZSCode, ZSName, StockList[][]}`，个股位置数组含
封单金额、`4天3板` 高度描述、题材标签、核心概念、涨停原因长文本。

#### ④ `c=FuPanLa & a=GetPMSL_PMLD` — 盘面梳理异动时间线
返回 `List[]`：`{TimeMin, TagID, TagName, TagShuXing(2=涨停类/0=跌停类/1=其他),
ZSName, Detail, StockList[][]}`

---

## 四、东方财富 + 同花顺（第三源 / 备用源）

### 4.1 东方财富 push2ex

| 接口 | 说明 |
|:---|:---|
| `GET https://push2ex.eastmoney.com/getTopicZTPool` | 涨停池，`data.tc` = 涨停家数 |
| `GET https://push2ex.eastmoney.com/getTopicDTPool` | 跌停池，`data.tc` = 跌停家数 |
| `GET https://push2ex.eastmoney.com/getTopicZBPool` | **炸板池**，`data.tc` = 炸板家数 |

公共参数：`ut=7eea3edcaed734bea9cbfc24409ed989` `dpt=wz.ztzt`
`Pageindex=0` `pagesize=10000` `sort=<fbt:asc|fund:asc>` `date=YYYYMMDD`

`pool[]` 单只字段：

| 字段 | 含义 |
|:---|:---|
| `c` / `n` | 代码 / 名称 |
| `lbc` | **连板数** |
| `fbt` / `lbt` | 首次 / 最后封板时间（`HHMMSS` 整数，如 `92500` = 09:25:00） |
| `fund` | 封单额 |
| `zbc` | 炸板次数 |
| `hs` | 换手率 |
| `ltsz` | 流通市值 |
| `hybk` | 行业板块 |
| `zttj` | `{days, ct}` 涨停统计 |

**封板率推导**：`封板率 = ZT.tc / (ZT.tc + ZB.tc)`
实测 `61 / (61 + 49) = 55.45%`，与财联社 `55.00%` 一致。

**连板率口径**（与财联社对齐）：`该高度今日家数 / 上一高度昨日家数`，
需回溯到上一个交易日取基数；取不到时显示 `—`，不用近似值冒充。

### 4.2 同花顺 dataapi

| 接口 | 说明 |
|:---|:---|
| `GET https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool` | 涨停池 + 封板率同比 |
| `GET https://data.10jqka.com.cn/dataapi/limit_up/continuous_limit_up` | 连板梯队 |

参数：`page` `limit` `field` `filter=HS,GEM2STAR` `order_field=330324` `order_type=0` `date=YYYYMMDD`

`limit_up_pool` 的 `data.limit_up_count`：

```json
{"today":     {"num":59, "history_num":108, "rate":0.5463, "open_num":49},
 "yesterday": {"num":36, "history_num":43,  "rate":0.8372, "open_num":7}}
```

- `num` = **封板家数**，`history_num` = **触及家数**，`open_num` = **炸板家数**
- `rate` = **封板率**（`num / history_num`）
- `trade_status.name` 为 `非交易日` 时 `today` 全为 0，须回落到最近交易日

`continuous_limit_up` 的 `data[]`：`{height, number, code_list[{code,name,continue_num}]}`

> ⚠ `continuous_limit_up` **只收录连板股（2 板起），本身不含首板**。
> 因此它的「一板」家数结构性缺失，展示为 `—` 而不是 `0`
> （写 0 会被读成「今日没有首板」，属误导）。

### 4.3 交易日口径陷阱（重要）

**非交易日不会报错，而是静默回落**，且东财与同花顺的回落方式**不一致**：

| 请求日类型 | 东财 `push2ex` | 同花顺 `dataapi` |
|:---|:---|:---|
| 未来/当日非交易日（如周六） | 回落到最近交易日数据 | **返回空** |
| 过去的非交易日 | 返回空池（`tc=0`） | 返回空 |

东财响应里的 `qdate` 字段**恒为「全市场最近交易日」**，并不是请求日对应的
交易日，所以**不能直接拿 `qdate` 当答案**。正确的回落判定是：

```python
# 请求日晚于 qdate <=> 本次响应是回落来的
effective = latest_trade_date if requested > latest_trade_date else requested
```

**为什么必须做这一步**：凡是「需要拿另一个交易日做基数」的口径（连板率、
昨涨停今表现等），都要先校正请求日再回溯。否则周六请求时，从周六往回走，
第一个有数据的日子正好是**同一个交易日**，基数=自身，连板率退化成自比值：

| | 一板 | 二板 | 三板 | 高度板 |
|:---|:---|:---|:---|:---|
| 错误（基数取到自身） | 54 | 4 / 54 = **7%** | 1 / 4 = **25%** | 2 / 1 = **100%** |
| 正确（基数=上一交易日 10-15：30/2/2） | 54 | 4 / 30 = **13%** | 1 / 2 = **50%** | 2 / (2+2) = **50%** |

自比值「看着像那么回事」，极难察觉，因此 `tests/test_dingpan_service.py`
里加了守卫断言 `test_ladder_rate_is_not_self_ratio`。

**高度板是聚合档**：分子分母都要聚合（今日 `k>=4` / 昨日 `k>=3`）。
若分子误取 `k==4` 的精确家数，会出现「高度板显示 2 家、连板率却按 1 家算」
的自相矛盾。

### 4.4 已知能力边界

东方财富行情列表接口 `push2*.eastmoney.com/api/qt/clist/get` 在本机网络环境下
**全部不可达**（proxy 拒绝连接），因此备用源**无法提供**：

- 全市场涨跌分布（13 档柱状图）
- 板块资金热力图（含 5日 / 10日窗口）

这两块在降级时会显式标记为 `unavailable`，前端显示占位提示。

---

## 五、前端渲染契约

`tab_dingpan` 结构（`src/dingpan_service.py` 产出）：

```jsonc
{
  "tab_bar":   [{"key":"watchlist","label":"自选"}, ..., {"key":"zt_bao","label":"涨停宝"}],
  "active_tab": "market",
  "market": {
    "overview":          { "available", "series":[{key,label,value}], "up_count", "flat_count",
                           "down_count", "limit_up_count", "suspend_count", "limit_down_count",
                           "crosscheck" },
    "heat":              { "available", "degree", "turnover", "turnover_change",
                           "preview_energy", "preview_change" },
    "limit_performance": { "available", "seal_rate", "sealed_count", "touched_count",
                           "yesterday_perf", "open_high_rate", "profit_rate",
                           "ladder":{tiers,counts,rates}, "crosscheck" },
    "fund_heat":         { "available", "sort_modes", "time_windows",
                           "active_sort", "active_window", "matrix"{排序:{时间:[卡片]}} },
    "index_quotes":      { "available", "quotes":[{code,name,last_px,change_str,up_num,down_num}] },
    "commentary":        { "title", "jump" }
  },
  "watchlist": [], "live": [], "tuyere": [], "mainline": {},
  "big_rise": [], "zt_bao": [],
  "bse": [ {code,name,last_px,change_str,is_limit_up,limit_count,plate_name} ],
  "bse_total": 351,

  "ranking": { "available", "topics":[{name,topic,style}], "active_topic",
               "boards": { "all":[...], "continuous_up":[...],
                           "up_open":[...], "down":[...] } },
  "plate_ranking": { "available", "plates":[...], "up_num":[...] },
  "blasting": { "available", "blasting_fuses":[...], "articles":[...] },

  // 点开才需要的数据，构建时一次性预取（手机端不能直连财联社）
  "drill": {
    "index":  { "sh000001": { "name", "top3":[...] } },
    "plate":  { "cls80457": { "info":{name,change,main_money_inflow_str},
                              "up_stocks":[...] } },
    "tuyere": { "cls80222": [ ...个股... ] }
  },

  "sources":     { "market_overview": "财联社(8.5.4)", ... },
  "unavailable": [ ... ],
  "degraded":    false,
  "generated_at": "2026-10-17 08:34:49"
}
```

前端字段命名与财联社 App 显示一一对应，详见 `mobile_app/index.html`
中的 `renderDingpan` / `dpMarketHtml` / `dpChartHtml` / `dpPerfHtml` / `dpFundHeatHtml`
/ `dpIndexHtml` / `dpRankingHtml` / `dpBlastingHtml` / `dpBseHtml`。

### 5.1 「点不开」是怎么解决的

手机端**不能**直连财联社（跨域 + `sign` 签名），所以点击展开的数据必须在
构建 bundle 时**全部预取**。做法是 `_build_drill()` 把下钻数据塞进 `drill`：

| 点哪里 | 展开什么 | 数据来源 |
|:---|:---|:---|
| 顶部指数卡 | 前三大成分股 | `drill.index[code].top3` |
| 资金热力图卡片 | 板块详情 + 板块内涨停个股 | `drill.plate[code]` |
| 连板梯队任一档 | 连板池明细 | `ranking.boards.continuous_up` |
| 「查看更多」 | 当前榜单全量 | `ranking.boards[topic]` |
| 风口条目 | 风口板块内个股 | `drill.tuyere[code]` |

交互由 `dpOpen(kind, key)` / `dpClose()` 驱动，底部弹层是
`#dp-sheet` + `#dp-mask`（`.dp-sheet.on` 时滑出）。

> ⚠️ 下钻只预取**默认视图**的前 6 个板块卡，不是全部 12 个组合 × 6 张卡 ——
> 否则请求数会爆炸。切了排序/时间窗后，卡片仍可点，但明细对应的是默认视图。

### 5.2 两个「看着像数据缺失」的坑

**① 指数成分股返回的是 `change_30`，不是当日涨幅。**
`top3_component` 给 `{last_px, change_px, change_30}`。`change_30` 是区间涨幅
（实测 `+199.60%`），**直接当涨跌幅展示会严重误导**（会被读成今天涨停两次）。
前端必须显式标注「区间涨幅」。

**② 板块涨停个股接口不返回 `last_px`。**
`plate_up_stocks` 只有 `{secu_code,secu_name,change,change_px}`。
前端要**按需渲染价格列**，否则整列都是 `—`，看起来像数据没取到。

### 5.3 后端陷阱：`_safe(client.get_x)` 兜不住缺方法

```python
# ❌ 错：属性查找发生在 _safe 调用之前，缺方法直接 AttributeError
self._safe(self.cls.get_index_home)

# ✅ 对：方法名当字符串传进去，getattr 拿不到也走降级
self._call(self.cls, "get_index_home")
```

`_safe(fn, ...)` 收到的是**已经解析好的可调用对象**，所以 `self.cls.get_index_home`
这个属性查找发生在进 `try` 之前 —— 客户端没有这个方法时，异常根本不会被
`_safe` 捕获。用一个只实现部分接口的 mock 一测就暴露。

凡是新增接口都必须走 `_call`，否则「客户端版本旧一点」就是整个盯盘包构建失败，
而不是优雅降级。`tests/test_dingpan_service.py::test_missing_client_methods_degrade_instead_of_crashing`
就是守这条的。

---

## 六、复现与验证

```bash
# 1. 生成盯盘数据包（写入 output/mobile_api/bundle.json + mobile_app/bundle.json）
PYTHONPATH=src python -c "from mobile_service import MobileDataAggregator; MobileDataAggregator().build_full_bundle()"

# 2. 单独调试盯盘聚合器（打印各区块取值与数据源）
cd src && python dingpan_service.py

# 3. 单独调试第三源（东方财富 + 同花顺）
cd src && python data_sources/em_dingpan_client.py

# 4. 同步前端资产到 Android / Flutter 壳
python tools/sync_mobile_assets.py

# 5. 重新打包 Android APK（只同步资产是不够的，产物不会自己更新）
python tools/build_apk.py

# 6. 本地预览（手机访问 http://<本机局域网IP>:8088/mobile_app/index.html）
python tools/run_mobile_server.py

# 6. 盯盘单测（含降级路径与连板率口径守卫，零网络）
QF_BLOCK_NETWORK=1 pytest tests/test_dingpan_service.py -q

# 7. 降级场景端到端验证：主源全挂、只有备用源活着
#    构建 -> 渲染 -> 断言（真实联网，产物落在 output/dingpan_preview/）
PYTHONPATH=src python scratch/build_fallback_bundle.py
python scratch/verify_fallback.py
```

降级路径平时跑不到（两个主源都健康时永远不执行），所以第 6、7 步不是可选项。

第 7 步会断言：页脚出现「⚠ 已降级至备用源」且只列备用源、缺失板块有明确
占位提示、正文无 `undefined`/`NaN` 泄漏、无横向溢出、无 console 报错，
以及**连板率与财联社口径一致（13%/50%/50%）而非自比值**。

### 打包后必须解包核对

**只同步资产 ≠ 用户能用上。** 改完前端一定要重跑第 5 步并解包确认，
否则会以为做完了、其实手机上的包还是旧的：

```python
import zipfile
z = zipfile.ZipFile("output/apk/quant_factor_terminal.apk")
html = z.read("assets/index.html").decode("utf-8")
assert "盯盘" in html and "dp-root" in html
assert z.getinfo("assets/index.html").file_size == <磁盘字节数>
```

⚠️ 用 `getinfo().file_size` 比字节数，**不要用 `len(html)`** ——
中文是多字节，58,460 字节解码后只有 55,937 字符，会被误判成「内容不一致」。

### 环境坑：`reg.exe` 被拉黑导致 d8 失败

`d8.bat` / `apksigner.bat` 会调 `find_java.bat` → `reg.exe` 去定位 JDK。
本机 `reg.exe` 在安全策略黑名单里，包装脚本会直接 exit 255。
`build_apk.py` 已改为**直接驱动 jar**（JAVA_HOME 已知，`.bat` 唯一作用就是找它）：

```
java -cp <build-tools>/lib/d8.jar com.android.tools.r8.D8 <classes...> --output <dir>
java -cp <build-tools>/lib/apksigner.jar com.android.apksigner.ApkSignerTool sign ...
```

`aapt2.exe` / `zipalign.exe` 是原生 exe，不受影响。

### Flutter 壳未覆盖（重要）

`flutter_app` 是**原生 Dart 实现**，不是 WebView 套壳 ——
只在 `tradingview_kline_widget.dart` 里用 WebView 画 K 线，
**不加载 `mobile_app/index.html`**。因此：

- `flutter_app/assets/index.html` 实际是**无用文件**（同步脚本仍会写，但没人读）
- Flutter 底部只有 情绪 / 主线 / 自选K线 / 预案 四个 tab，**没有「盯盘」**

要让 Flutter 版也有盯盘，需另写 Dart 屏幕复用 `assets/bundle.json`
的 `tab_dingpan` 数据。**目前只有 Android 版覆盖。**

---

# 2026-10-17 补充：模拟器实机抓包（REA + mitmproxy）

第二次抓包在雷电模拟器里真装了财联社 App，逐个 Tab、逐个排序按钮点过去，
拿到 82 个端点的完整记录。这一轮修掉的都是「看着能用其实是错的」问题。

## 抓包环境怎么搭起来的（四个坑）

1. **财联社没装**：先 `adb install`，但中文名 APK 会被 adb 的编码处理破坏
   （`Unable to open file: /data/local/tmp/²Ɓªɧ.apk`）。先 `cp` 成 ASCII 路径再装。
2. **App 报"网络异常"**：mitmproxy 的 CA 哈希是 `c8750f0d`，旧的 mitm CA 和新生成
   的**文件名相同但内容不同**（主题名固定导致哈希必然撞）。必须把当前
   `~/.mitmproxy/mitmproxy-ca-cert.pem` 推到 `/system/etc/security/cacerts/c8750f0d.0`
   再重启模拟器，否则 TLS 全部握手失败。
3. **模拟器连不上宿主机 8080**：`adb reverse` 在旧版 adb 上不可用，
   且 `192.168.1.7` / `172.16.1.2` 都不通。最终可行方案是**直连宿主机网卡 IP**
   并把系统代理指过去，`adb shell settings put global http_proxy 192.168.1.7:8080`。
4. **adb server 被进程组带走**：每次调用都重启 server，第一条命令会拿到
   `device offline`。解决办法是用新版 adb + 分离进程方式常驻 server。

## 参数层面的三个真 bug

### ① `ranking` 用的是 `reverse`，不是 `rever`
```python
# 之前（错）：传 rever，接口静默忽略，永远返回涨幅榜
self._get(..., topic=topic, index=index, way=way, rever=rever, ...)

# 现在（对）：真 App 抓包确认是 reverse
self._get(..., topic=topic, index=index, way=way, reverse=reverse, ...)
```
不报错、不告警，只是「跌幅榜/量比/换手率/成交额」四个排序点不动 ——
典型的假可用。相邻的 `fund_heat` 用的却是 `rever`，同一次抓包里两种命名都有。

### ② `fields` 必须与 `way` 配套
`way=business_balance` 但 `fields=change,last_px` → 返回的 `business_balance` 全是 `None`。
正确组合：

| 排序 Tab | way | fields |
|---|---|---|
| 涨幅榜 | `change` + reverse=1 | `change,last_px` |
| 跌幅榜 | `change` + reverse=0 | `change,last_px` |
| 量比 | `qrr` + reverse=1 | `qrr,last_px` |
| 换手率 | `tr` + reverse=1 | `tr,last_px` |
| 成交额 | `business_balance` + reverse=1 | `business_balance,last_px` |
| 池类 | `time` + reverse=1 | `change,change_px,last_px,time,reason,is_st,limit_up_days,plate,change_color,last_px` |

### ③ `style` 必须与 `topic` 配套
池 topic（`continuous_up`/`up_open`/`down`）配 `TOPIC-STYLE-A-*` **不报错但返回 0 条**，
看着像"今天没连板"。必须配 `TOPIC-STYLE-B-240223`；北交所 topic=bj 配 C。

### ④ `up_reason` 只认前缀式代码
榜单给的是 `002085` / `920196.BJ`，接口要的是 `sz002085` / `bj920196`。
直接传榜代码会静默返回空 dict。返回值的键又是请求格式，需再映射回榜代码。

## 新接入的接口（13 个，全部实测有数据）

| 端点 | 用途 | 备注 |
|---|---|---|
| `/v3/transaction/anchor` | 直播页板块异动锚点 | 需 `cdate`，且必须传**交易日**；周末返回空 |
| `/v2/quote/a/plate/stocks` | 板块成分股 | 返回是 `data.stocks`，字段 `stock_code`/`stock_name`/`assoc_desc` |
| `/v2/quote/a/plate/popular_stocks` | 板块人气股 | `plate_code` |
| `/web_quote/plate/info` | 板块详情头 | `secu_code` |
| `/v2/quote/a/stock/tline` `/tline_5d` | 板块分时 | **只对板块有效**，指数返回 `line: []` |
| `/quote/stock/up_reason` | 大涨股业务说明 | 批量，代码需转前缀式 |
| `/v2/quote/a/stock/range_trading_days` | 区间交易日 | `start_date`/`end_date` |
| `/quote/stock/closest_trading_day` | 最近交易日 | **返回列表不是单值**，取第一个 |
| `/api/limit_up_tool` | 涨停宝工具位 | 零参数 |
| `/quote/golden/qs_info_all` | 券商月度观点 | `date=YYYYMM` |
| `/quote/index/basic` | 指数轻量行情 | `secu_code`+`fields` |
| `/api/optional/stock` | 自选股 | ❌ 未登录时 `10012`，已降级 |
| `/api/subject/{id}/article` | 风口追踪文章 | ❌ 签名覆盖路径参数，我们的固定 sign 一律 `10012` |

## 新增的 6 个数据块

| payload 键 | 内容 |
|---|---|
| `zt_panorama` | 涨停全景图：50 个板块涨停家数 + 前 6 个板块的涨停股明细（可点开切换） |
| `plate_detail` | 板块下钻：成分股 777/434 只、人气股、分时 241 点 |
| `up_reason` | 大涨股 20 只的业务说明（`板块\|说明`，前端拆开展示） |
| `anchor` | 板块异动锚点 24 条（时间+方向+关联文章） |
| `trading_days` | 最近交易日 + 30 日交易日列表 |
| `extra` | 券商月度观点 28 条、指数基础行情、涨停宝工具位 |

## 抓包时机要点

- `transaction_anchor` 的 `cdate` 传周末/未来 → **空数组不报错**，要回落到最近交易日。
- `plate_up_num` / `plate_up_stocks` 需要 `date=YYYY-MM-DD`，否则拿到的是非交易日数据。
- 接口地图里 `/api/subject/1485/article` 这一类带路径参数的，签名算法与固定 sign 不同，
  用默认 sign 一律 `10012` —— 这类只能标记 unavailable，不能填假数据。

---

# 2026-10-18 补充：风口 / 主线 深挖

## 核心发现：`/v2/dingPan/mainline` 一个接口喂三个 Tab

之前只解析了 `chances[].plates[].faucet_1`，把 `styles` 和 `winds` 两大块整个丢了，
所以主线页只剩一个标题、风口页的涨跌幅和龙头全是空的。完整结构：

```
data
├── chance_desc     「判断主线机会，洞悉趋势强度」  ← 主线 Tab 副标题
├── mainLine_desc   「精准把控主力动向，掌握主线机会」
├── wind_desc       「紧跟每日风口，领悟市场风向」  ← 风口 Tab 副标题
├── style_desc      「预判风格走向，投资无往不利」  ← 市场风格副标题
├── chances[]       ← 主线机会
│   ├── id / hot / continued / title / mainLine_desc
│   └── plates[]
│       ├── code / name / reason / rise_count / rise_range / time
│       ├── faucet_1 / faucet_2 / faucet_3   ← 龙一/龙二/龙三（之前只要了 1）
│       └── article {id, title, img, read_count, time}
├── styles          ← 市场风格
│   ├── title / interpret
│   └── index_1 { code, name, change30, stacks[] }  ← 30 日区间涨幅 top3
└── winds[]         ← 每日风口
    ├── id / title / plate_code / subject_id / hot / plate_change / interpret / img
    └── faucet_stocks[] {stock_code, stock_name, rise_range, status}
```

**`winds` 就是风口 Tab 的数据源**，不必单独调 `todayTuyere` 拿涨跌幅。

## 风口页 3 张卡怎么拼出来

真 App 每张卡显示「板块名 + 涨跌幅 + 主力净流入」，三个字段来自三个地方：

| 字段 | 来源 |
|---|---|
| 板块名 / 投资逻辑 | `/v2/todayTuyere`（或 `mainline.winds[].title/interpret`） |
| 涨跌幅 | `mainline.winds[].plate_change`（小数，×100 转百分比） |
| 主力净流入 | `/v2/quote/a/plate/info`（**参数是 `plate_codes` 复数**，可逗号批量） |

⚠️ `plate/info` 传 `plate_code`（单数）会返回 `9001 请求错误`，且错误信息里
明确提示 `"plate_codes": "板块代码"` —— 这个提示很容易被忽略。

## 风口下钻：龙一龙二龙三 与「更多概念个股」是两个接口

| 内容 | 接口 | 特征 |
|---|---|---|
| 龙一/龙二/龙三 | `/v2/quote/a/plate/tuyere/stocks?plate_code=` | **固定返回 3 只**，传任何分页参数都是 3 只，不是分页 |
| 更多概念个股 | `/v2/quote/a/plate/stocks?plate_code=&way=change&reverse=1&only_core=0` | 全量（军工 434 / 低空经济 273 / 房地产 100） |

## 北交所横幅

`/v3/depth/bjs/topbanner`（**api3**）返回 1 条 banner，字段 `img` + `active_url`。
之前这个接口实现了但没接进 payload，页面上看不到。

## 时间戳陷阱（开盘啦 vs 东财）

| 源 | 字段 | 格式 | 例子 |
|---|---|---|---|
| 开盘啦 | `first_limit_time` | **Unix 时间戳** | `1792114221` |
| 东方财富 | `fbt` | `HHMMSS` 整数 | `92500` |

直接拼进文案会显示成 `首封 1792114221`。前端用 `dpClock()` 统一处理：
`< 1e6` 当 HHMMSS，否则按 Unix 秒转本地时间。

## 大涨股板块胶囊

真 App 顶部有「低空经济(19) 军工(10) 汽车零部件(4) …」，括号是涨停家数。
数据来自个股的 `plate_name` 分组，服务层 `_group_by_plate()` 产出
`big_rise_plates`，前端点击即本地过滤，不发请求。

---

# 2026-10-18 补充：市场风格 / 主线文章 点开详情

## 结论：文章正文没有可用 JSON 接口，只有 H5 这条路

试过的接口全部失败：

| 尝试 | 结果 |
|---|---|
| `/v1/roll/info?id=2499598` | `10012 签名错误` |
| `/v2/article/detail` `/v1/article/detail` `/api/article/detail` | `50101`（路径不存在） |
| `/api/subject/article/{id}` `/v1/article/{id}` | `50101` |

**能用的只有 H5 页面**（真 App 也是用 WebView 打开这个地址）：

```
https://iav.cls.cn/articles/sv854/{article_id}
```

- HTTP 200，返回完整文章（标题、责编、发布时间、配图、正文）
- **没有 `X-Frame-Options` / CSP** → 可以直接 `iframe` 内嵌
- 配套的 `https://i.cls.cn/articles/v1/{id}/read` 是阅读计数，返回空 body

## 哪些 id 是文章 id

| 位置 | 字段 | 示例 |
|---|---|---|
| 市场风格 | `mainline.styles.id` | `2499598` |
| 主线板块关联文章 | `mainline.mainlines[].plates[].article.id` | `2506380` |

两者都指向同一套 H5 模板，拼上 id 即可打开。

## 前端实现要点

1. **应用内浮层 + iframe**，不离开 `index.html`，返回时无需重新加载整个页面
2. **必须 `history.pushState`**：WebView 的返回键走 `canGoBack()`，如果不开历史栈，
   按返回会直接**退出 App**。压一条 `{dpArt: id}` 后，返回键触发 `popstate`，
   我们借此关掉浮层，用户仍停在盯盘页
3. 关闭时把 `iframe.src` 置回 `about:blank`，释放 H5 占用的内存

## 注意

`iav.cls.cn` 是财联社的 H5 站点，**需要联网**才能看文章。
如果设备离线，浮层会显示白屏 —— 这是预期行为，不是 bug。
