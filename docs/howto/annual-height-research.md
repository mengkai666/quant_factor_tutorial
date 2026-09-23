# 一年市场高度与龙头领涨研究

## 当前交付

- 主报告 `output/主线强度追踪.html` 的市场高度图独立显示最近一个自然年，不再随板块图的65日窗口裁剪。
- 已核验历史基底 `data/annual_height_history.csv` 含压力/连板预热；主图按报告日期截取一年。
- 截至2026-09-15的专题与原始证据位于 `output/height_pressure_year_20260916/`。
- 本地通用入口 `output/annual_height_research.html`，逐股浏览器 `output/annual_height_paths.html`。

## 定义

事前P5/P10/P20均不含当日；展示前高单独命名、默认隐藏。缺失交易日不压缩、不当零板。特殊停牌暂停计数只接受已有状态或公司公告证据，严格市场日口径作敏感性。

专题区分：突破后市场延伸、原龙头继续领涨、次高接替、新首板同主题补涨、断板后的再涨停/高点收复/实体反包、已知先例之后的形态模仿。交易代理按确认后下一交易日入场、T+1后退出；涨跌停开盘不成交、跌停退出延期、未结及除权风险不隐去。

## 更新专题

先建新的隔离输出目录，明确已完成日线的研究截止日期。`tools/collect_annual_height_bars.py` 支持断点继续；预检失败停止，不反复冲击失败端点。默认值是本次研究快照，不是动态“今天”。

```text
python tools/collect_annual_height_bars.py --out output/height_pressure_year_20260916 --start 2025-07-01 --end 2026-09-15
python tools/build_annual_height_study.py --out output/height_pressure_year_20260916 --end 2026-09-15
python tools/render_annual_height_study.py --out output/height_pressure_year_20260916
python -m pytest tests/test_annual_height_research.py -q
python tools/verify_annual_height_study.py --out output/height_pressure_year_20260916
```

已有原始数据可离线从第二步复算。新窗口需要重新核对停复牌、历史风险状态、概念标签及数据截止；独立新浪核验和公告证据不能用旧窗口的成功状态冒充新窗口校验。

## 主图与发布

未来报告可以在已核验基底之后从当前已完成日报涨停池追加主板高度，明确新段来源；不会用新池改写历史。追加缺日按正式交易日历补空，重算事前压力。新段的旧最高梯队断板未逐股重建时留未知，而不是伪造0。

`annual_height_view.render_annual_height_section` 被两个日报入口共用。`replace_height_section` 可只替换本地既有报告图段，不触碰其它内容或完整性元数据。`publish_site` 在用户实际发布时复制自包含专题/浏览器并重写归档相对链接；未带专题文件时移除不可用链接，不会生成断链。

本次只生成本地交付，未调用发布、邮件、交易或git推送。
