# 运行主线追踪

本文说明如何在本地运行主线强度追踪脚本并检查输出结果。

## 准备环境

```bash
pip install -r requirements.txt
```

项目依赖 `pandas`、`numpy`、`requests`、`baostock`、`wordcloud` 和 `akshare`。市场数据首次运行需要网络访问 AkShare、腾讯或新浪接口。

## 重建沪深北市场数据

首次运行、缓存损坏或需要扩大历史窗口时，先执行：

```bash
python tools/rebuild_market_data.py --start 2025-11-04
python tools/audit_data_integrity.py --quiet
```

该流程覆盖上海、深圳、北京证券交易所全部 A 股。价格缓存同时保留未复权和前复权口径：

| 字段 | 用途 |
|---|---|
| `close_raw` | A/D、涨跌方向和停牌判断 |
| `close_qfq` | 周期收益、排行和回测 |
| `trade_status` | `traded`、`suspended`、`not_listed` |
| `source_raw` / `source_qfq` | 逐字段来源追踪 |

候选缓存只有在质量闸门通过后才替换 `data/price_history_cache.csv`。失败时可保留候选文件，修复网络后重复执行命令，检查点会跳过已可信代码。

`data/fetch_status.csv` 记录每次 universe/price 抓取的最终聚合状态；`success`、`zero`、`partial`、`failed`、`stale` 和 `not_available` 分别表示成功、可信空结果、部分覆盖、失败、过期和当前源不可用。

### 涨跌停池备用链

最新闭市日的涨跌停池由 `LimitPoolProvider` 独立取数，并按以下顺序回退：

- 涨停：`akshare_em` -> `eastmoney_push2ex` -> `ths_limit_up`。
- 跌停：`akshare_em` -> `eastmoney_push2ex`。

主源返回可信非空数据后不会调用后续来源；空结果会继续尝试备用源。每行的 `source` 字段和 `data/fetch_status.csv` 中的聚合 `source`（例如 `ZT:eastmoney_push2ex|DT:akshare_em`）记录实际来源，失败原因摘要保存在状态消息中。东财请求带有限速和重试，同花顺仅用于涨停池。

若涨停或跌停任一核心池不可用，结果会标记为 `partial` 或 `failed`，不会用空值伪装成完整成功；质量闸门会阻断报告生成、站点发布和邮件发送。沪深北代码统一保存为 `shxxxxxx`、`szxxxxxx` 或 `bjxxxxxx`。

## 生成报告

```bash
python src/主线强度追踪.py
```

核心输出：

- `output/主线强度追踪.html`：可视化投研报告。
- `data/market_data_quality.json`：统一价格缓存的质量闸门结果。
- `*.csv`：涨停、概念分类、价格和情绪缓存。

## 配置邮件

默认不发送邮件。需要发送时设置环境变量：

```powershell
$env:EMAIL_ENABLE="1"
$env:EMAIL_SENDER="your@qq.com"
$env:EMAIL_PASSWORD="your_auth_code"
$env:EMAIL_RECEIVERS="receiver1@qq.com,receiver2@qq.com"
python src/主线强度追踪.py
```

## 检查数据质量

运行后检查 `data/market_data_quality.json` 和 `python tools/audit_data_integrity.py --quiet` 的退出码。质量报告存在 `critical` 项时，报告生成、站点发布和邮件发送都会被阻断。

旧版业务缓存仍可单独检查：

- `warnings`：需要优先处理的风险提示。
- `cls_plate_cache.duplicate_rows`：概念分类缓存是否存在重复。
- `classified.other_mainline_pct`：未归入核心主线的比例。
- `sentiment_runtime.top_up_down_pair_repeat`：涨跌家数组合是否异常重复。
