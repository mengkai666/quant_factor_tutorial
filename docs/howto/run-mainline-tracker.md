# 运行主线追踪

本文说明如何在本地运行主线强度追踪脚本并检查输出结果。

## 准备环境

```bash
pip install -r requirements.txt
```

项目依赖 `pandas`、`numpy`、`requests`、`baostock`、`wordcloud` 和 `akshare`。网络接口不可用时，脚本会尽量使用已有缓存继续生成报告。

## 生成报告

```bash
python 主线强度追踪.py
```

核心输出：

- `主线强度追踪111111.html`：可视化投研报告。
- `data_quality_report.json`：缓存覆盖、重复数据、分类覆盖、情绪异常等审计信息。
- `*.csv`：涨停、概念分类、价格和情绪缓存。

## 配置邮件

默认不发送邮件。需要发送时设置环境变量：

```powershell
$env:EMAIL_ENABLE="1"
$env:EMAIL_SENDER="your@qq.com"
$env:EMAIL_PASSWORD="your_auth_code"
$env:EMAIL_RECEIVERS="receiver1@qq.com,receiver2@qq.com"
python 主线强度追踪.py
```

## 检查数据质量

运行后打开 `data_quality_report.json`。重点关注：

- `warnings`：需要优先处理的风险提示。
- `cls_plate_cache.duplicate_rows`：概念分类缓存是否存在重复。
- `classified.other_mainline_pct`：未归入核心主线的比例。
- `sentiment_runtime.top_up_down_pair_repeat`：涨跌家数组合是否异常重复。
