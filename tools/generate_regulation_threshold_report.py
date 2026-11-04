from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / "src"))


def board_of(code: str) -> str | None:
    code = str(code)
    if code.startswith("sz300"):
        return "创业板"
    if code.startswith(("sz000", "sz001", "sz002", "sz003", "sh600", "sh601", "sh603", "sh605")):
        return "主板"
    return None


def load_limit_events() -> pd.DataFrame:
    rows = []
    for path in sorted((DATA / "limit_events").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows.extend(payload.get("records") or [])
        except Exception:
            continue
    if not rows:
        return pd.DataFrame(columns=["code", "date", "pool_type", "break_count", "limit_count"])
    df = pd.DataFrame(rows)
    for col in ["break_count", "limit_count"]:
        if col not in df:
            df[col] = pd.NA
    df["date"] = pd.to_datetime(df["date"])
    return df[["code", "date", "pool_type", "break_count", "limit_count"]]


def main() -> None:
    prices = pd.read_csv(DATA / "price_history_cache.csv", usecols=["date", "code", "close_raw"])
    master = pd.read_csv(DATA / "security_master.csv", usecols=["code", "name", "market", "is_st"])
    prices["date"] = pd.to_datetime(prices["date"])
    prices["close_raw"] = pd.to_numeric(prices["close_raw"], errors="coerce")
    prices = prices.dropna(subset=["close_raw"])
    prices = prices.merge(master, on="code", how="left")
    prices["board"] = prices["code"].map(board_of)
    prices = prices[prices["board"].notna() & (~prices["is_st"].fillna(False))].copy()
    prices = prices.sort_values(["code", "date"])
    g = prices.groupby("code", group_keys=False)
    prices["ret10"] = g["close_raw"].pct_change(9)
    prices["ret30"] = g["close_raw"].pct_change(29)
    prices["ret10_pct"] = prices["ret10"] * 100
    prices["ret30_pct"] = prices["ret30"] * 100
    prices["dist10_pp"] = 100 - prices["ret10_pct"]
    prices["dist30_pp"] = 200 - prices["ret30_pct"]
    prices["entered"] = (prices["ret10_pct"] >= 100) | (prices["ret30_pct"] >= 200)
    prices["near"] = ((prices["ret10_pct"] >= 80) & (prices["ret10_pct"] < 100)) | ((prices["ret30_pct"] >= 160) & (prices["ret30_pct"] < 200))
    prices["threshold"] = prices.apply(lambda r: "10日100%" if r["ret10_pct"] >= 100 and (r["ret30_pct"] < 200 or pd.isna(r["ret30_pct"])) else ("30日200%" if r["ret30_pct"] >= 200 else ("接近10日100%" if r["ret10_pct"] >= 80 else "接近30日200%")), axis=1)

    latest_date = prices["date"].max()
    latest = prices[prices["date"] == latest_date].copy()
    latest["near_score"] = latest[["dist10_pp", "dist30_pp"]].where(lambda x: x >= 0).min(axis=1)
    latest_scan = latest[(latest["near"] | latest["entered"])].sort_values(["entered", "near_score", "ret10_pct"], ascending=[False, True, False])

    # First threshold entry in the available history, used for event-study style summaries.
    entered_rows = prices[prices["entered"]].copy()
    first_entries = entered_rows.sort_values("date").groupby("code", as_index=False).first()
    first_entries = first_entries.merge(master[["code", "name"]], on="code", how="left", suffixes=("", "_m"))

    limits = load_limit_events()
    if not limits.empty:
        limits["is_limit"] = limits["pool_type"].isin(["ZT", "连板", "涨停"])
        limits["is_broken"] = pd.to_numeric(limits["break_count"], errors="coerce").fillna(0).gt(0)
        limit_day = limits.groupby(["code", "date"], as_index=False).agg(is_limit=("is_limit", "max"), is_broken=("is_broken", "max"), break_count=("break_count", "max"), limit_count=("limit_count", "max"))
        first_entries = first_entries.merge(limit_day, left_on=["code", "date"], right_on=["code", "date"], how="left")
    else:
        for col in ["is_limit", "is_broken", "break_count", "limit_count"]:
            first_entries[col] = pd.NA

    # 开盘啦真实「严重异动提醒」接口：网络不可用时保留历史扫描结果。
    kpl_alerts = []
    kpl_error = ""
    try:
        from data_sources.kaipanla_client import KaiPanLaClient

        kpl_payload = KaiPanLaClient(timeout=20).get_abnormal_alerts()
        kpl_alerts = kpl_payload.get("alerts") or []
    except Exception as exc:
        kpl_error = str(exc)
    kpl_df = pd.DataFrame(kpl_alerts)

    # Look at the next five available sessions after each first entry.
    forward = prices[["code", "date", "close_raw"]].copy()
    forward["entry_date"] = forward["date"]
    first_entries = first_entries.rename(columns={"date": "entry_date", "close_raw": "entry_close"})
    first_entries["fwd5_date"] = first_entries.apply(lambda r: forward[(forward.code == r.code) & (forward.date > r.entry_date)].date.iloc[4] if len(forward[(forward.code == r.code) & (forward.date > r.entry_date)]) >= 5 else pd.NaT, axis=1)
    first_entries = first_entries.merge(forward[["code", "date", "close_raw"]], left_on=["code", "fwd5_date"], right_on=["code", "date"], how="left")
    first_entries["fwd5_ret_pct"] = (first_entries["close_raw"] / first_entries["entry_close"] - 1) * 100
    first_entries = first_entries.drop(columns=["date", "close_raw"], errors="ignore")

    def fmt(v, digits=1):
        return "—" if pd.isna(v) else f"{v:.{digits}f}"

    report = []
    report.append(f"# 创业板/主板监管阈值走势扫描\n\n")
    report.append(f"数据截止：价格 {latest_date.date()}；涨停/炸板事件 {limits['date'].max().date() if not limits.empty else '无'}；开盘啦提醒 {kpl_payload.get('date', '不可用') if 'kpl_payload' in locals() else '不可用'}。\n\n")
    report.append("## 口径与限制\n\n")
    report.append("交易所规则中的 10 日 +100% / 30 日 +200% 是连续窗口内‘日收盘价格涨跌幅偏离值累计’，本报告用原始收盘价的 10/30 个交易日滚动涨幅作预警代理，不把代理值当作正式监管认定。‘断板’仅在缓存有涨停池 break_count 时标记，普通收盘未涨停不等于盘中炸板。\n\n")
    report.append("## 最新横截面（截至 " + str(latest_date.date()) + "）\n\n")
    if latest_scan.empty:
        report.append("暂无达到预警区间或阈值的样本。\n\n")
    else:
        cols = ["board", "code", "name", "ret10_pct", "ret30_pct", "entered", "threshold"]
        report.append("|板块|代码|名称|10日滚动涨幅|30日滚动涨幅|状态|主要触发|\n|---|---|---|---:|---:|---|---|\n")
        for _, r in latest_scan[cols].iterrows():
            status = "已进入代理阈值" if r.entered else "接近阈值"
            report.append(f"|{r['board']}|{r['code']}|{r['name']}|{fmt(r['ret10_pct'])}%|{fmt(r['ret30_pct'])}%|{status}|{r['threshold']}|\n")
        report.append("\n")

    report.append("## 开盘啦实时严重异动提醒\n\n")
    if not kpl_df.empty:
        report.append("|代码|名称|窗口|涨幅偏离值|触发所需涨幅|状态|规则|\n|---|---|---:|---:|---:|---|---|\n")
        for _, r in kpl_df.iterrows():
            report.append(f"|{r['code']}|{r['name']}|{r['window_days']}日|{fmt(r['deviation_pct'])}%|{fmt(r['required_change_pct'])}%|{r['status_text']}|{r['rule']}|\n")
        report.append("\n")
    else:
        report.append(f"开盘啦提醒未取到。{kpl_error}\n\n")

    report.append("## 首次进入阈值后的样本走势\n\n")
    if first_entries.empty:
        report.append("样本不足。\n\n")
    else:
        agg = first_entries.groupby("board").agg(samples=("code", "nunique"), mean_fwd5=("fwd5_ret_pct", "mean"), median_fwd5=("fwd5_ret_pct", "median"), broken_on_entry=("is_broken", "sum"), limit_on_entry=("is_limit", "sum")).reset_index()
        report.append("|板块|首次进入样本数|进入后第5个交易日平均收益|中位数|进入日缓存炸板数|进入日涨停数|\n|---|---:|---:|---:|---:|---:|\n")
        for _, r in agg.iterrows():
            report.append(f"|{r.board}|{int(r.samples)}|{fmt(r.mean_fwd5)}%|{fmt(r.median_fwd5)}%|{fmt(r.broken_on_entry,0)}|{fmt(r.limit_on_entry,0)}|\n")
        report.append("\n")

    report.append("## 解读：断板是否可能是‘绕监管’\n\n")
    report.append("从价格行为上只能识别‘阈值附近先断板/降速’，不能证明存在主力为了规避监管而操纵走势。更稳妥的可检验定义是：进入 80%–100% 预警区间后，涨停连续性下降、炸板率升高、但趋势没有立即破坏；同时把正式异动公告/交易所公开信息作为独立标签。若只是某一天不涨停，不能直接称为‘绕监管’。\n\n")
    report.append("对创业板重点观察：20%涨跌幅使其更容易快速接近 10 日 100%，因此应优先看‘10日阈值距离 + 连板/炸板 + 换手’三者联动；主板则更关注 10%涨跌幅下的连续加速与 30 日累计趋势。\n\n")
    report.append("## 可复现文件\n\n")
    report.append("- `regulation_threshold_latest.csv`：最新横截面候选\n- `regulation_threshold_entries.csv`：历史首次进入样本及第5日收益\n- `regulation_threshold_kaipanla_alerts.csv`：开盘啦实时严重异动提醒（若接口可用）\n\n")
    report.append("## 规则来源\n\n")
    report.append("深交所投教材料明确列出主板与创业板严重异常波动的 +100%/+200% 标准，并说明创业板在‘10日内同向异常波动次数’上为 3 次、主板为 4 次；上交所公开规则/查询页也展示主板连续10/30个交易日累计 +100%/+200% 的口径。\n")
    report.append("\n官方参考：深交所创业板问答（https://www.szse.cn/www/investor/knowledge/t20200805_580244.html）；深交所主板问答（https://investor.szse.cn/knowledge/t20230406_599705.html）；上交所主板严重异常波动查询（https://www.sse.com.cn/disclosure/diclosure/ycjyxx/main/detail.shtml）。\n")

    (OUT / "regulation_threshold_report.md").write_text("".join(report), encoding="utf-8")
    latest_scan.to_csv(OUT / "regulation_threshold_latest.csv", index=False, encoding="utf-8-sig")
    first_entries.to_csv(OUT / "regulation_threshold_entries.csv", index=False, encoding="utf-8-sig")
    if not kpl_df.empty:
        kpl_df.to_csv(OUT / "regulation_threshold_kaipanla_alerts.csv", index=False, encoding="utf-8-sig")
    try:
        import matplotlib.pyplot as plt

        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False

        fig, axes = plt.subplots(1, 2, figsize=(13, 5), dpi=160)
        # Latest candidates: normalize the last 30 observations to 100.
        for _, row in latest_scan.head(6).iterrows():
            hist = prices[(prices.code == row.code) & (prices.date <= latest_date)].tail(30)
            if len(hist) < 5:
                continue
            axes[0].plot(range(len(hist)), hist.close_raw / hist.close_raw.iloc[0] * 100, label=f"{row['name']} {row.code}")
        axes[0].axhline(200, color="#c44e52", lw=0.8, ls="--")
        axes[0].set_title("最新预警候选：近30交易日归一化走势")
        axes[0].set_xlabel("交易日")
        axes[0].set_ylabel("起点=100")
        axes[0].legend(fontsize=7, loc="upper left")
        axes[0].grid(alpha=0.2)

        if not first_entries.empty:
            plot_df = first_entries.groupby("board")["fwd5_ret_pct"].agg(["mean", "median"]).reindex(["创业板", "主板"])
            plot_df.plot.bar(ax=axes[1], color=["#4c72b0", "#dd8452"], width=0.72)
            axes[1].axhline(0, color="black", lw=0.8)
            axes[1].set_title("首次进入阈值后第5交易日收益")
            axes[1].set_ylabel("收益率（%）")
            axes[1].grid(axis="y", alpha=0.2)
            axes[1].legend(["平均", "中位数"], fontsize=8)
        fig.tight_layout()
        fig.savefig(OUT / "regulation_threshold_event_study.png", bbox_inches="tight")
        plt.close(fig)
    except Exception as exc:
        (OUT / "regulation_threshold_plot_error.txt").write_text(str(exc), encoding="utf-8")
    summary = {"price_latest_date": str(latest_date.date()), "limit_event_latest_date": str(limits["date"].max().date()) if not limits.empty else None, "kaipanla_alert_date": kpl_payload.get("date") if "kpl_payload" in locals() else None, "kaipanla_alert_count": int(len(kpl_df)), "latest_candidate_count": int(len(latest_scan)), "first_entry_count": int(len(first_entries))}
    (OUT / "regulation_threshold_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
