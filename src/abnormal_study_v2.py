"""Point-in-time features and descriptive event studies for abnormal-move research.

No label is used to select a consolidation observation. Official regulatory status
is supplied separately from this price-only screen.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from abnormal_case_math import add_deviation_windows, fixed_forward_return


def board_of(code):
    if str(code).startswith(("sz300", "sz301")):
        return "创业板"
    if str(code).startswith(("sh600", "sh601", "sh603", "sh605", "sz000", "sz001", "sz002", "sz003")):
        return "主板"
    return None


def normalize(code):
    s = str(code).strip().lower()
    if s.startswith(("sh", "sz", "bj")):
        return s
    s = s.zfill(6)
    return ("sh" if s.startswith("6") else "sz") + s


def index_for(code):
    return "sz399102" if board_of(code) == "创业板" else ("sh000002" if str(code).startswith("sh") else "sz399107")


def clean_json(value):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return str(value)[:10] if pd.notna(value) else None
    if isinstance(value, np.generic):
        value = value.item()
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def prepare_prices(prices, master, indices, pool, calendar):
    p = prices.copy()
    p["date"] = pd.to_datetime(p["date"])
    p["board"] = p["code"].map(board_of)
    p = p[p.board.notna() & p.close_raw.gt(0)].copy()
    names = master.drop_duplicates("code").set_index("code")
    p["name"] = p.code.map(names["name"]).fillna(p.code)
    p = p[~p.name.str.contains("ST|退", case=False, regex=True)].copy()
    p = p.sort_values(["code", "date"]).drop_duplicates(["code", "date"])
    # Fixed basis: future missing qfq rows must never switch earlier observations
    # back to raw prices. Missing adjusted prices stay unknown.
    p["close_used"] = p.close_qfq.where(p.close_qfq.gt(0))
    p["price_basis"] = "qfq_only"
    p["index_code"] = p.code.map(index_for)
    p = p.merge(indices[["date", "index_code", "index_close"]], on=["date", "index_code"], how="left")
    calmap = {pd.Timestamp(d): i for i, d in enumerate(calendar)}
    p["date_idx"] = p.date.map(calmap)
    p = p.dropna(subset=["date_idx"]).copy()
    p["date_idx"] = p.date_idx.astype(int)
    p = add_deviation_windows(p)
    g = p.groupby("code", sort=False)
    previous = g.close_raw.shift(1)
    contiguous = p.date_idx.sub(g.date_idx.shift(1)).eq(1)
    rate = np.where(p.board.eq("创业板"), .20, .10)
    # Positive prices; integer-fen half-up rounding, with a tiny binary tolerance.
    p["limit_price"] = np.floor(previous * (1 + rate) * 100 + .50000001) / 100
    regular = contiguous & previous.gt(0) & (p.close_raw <= p.limit_price + .005)
    p["is_zt"] = np.where(regular, (p.close_raw - p.limit_price).abs().lt(.005).astype(float), np.nan)
    p["limit_evidence"] = np.where(regular, "原始价格重建", "未知/特殊行情")
    pool_keys = set(zip(pool.code, pd.to_datetime(pool.date)))
    in_pool = pd.Series([(c, d) in pool_keys for c, d in zip(p.code, p.date)], index=p.index)
    p.loc[in_pool, "is_zt"] = 1.0
    p.loc[in_pool, "limit_evidence"] = "历史涨停池"
    frozen = p.close_raw.eq(previous) & p.close_raw.eq(g.close_raw.shift(2)) & ~in_pool
    p.loc[frozen, "is_zt"] = np.nan
    p.loc[frozen, "limit_evidence"] = "连续平价/停牌待核"
    p["signal"] = p.dev10_pct.ge(80) | p.dev30_pct.ge(180)
    p["crossed"] = p.dev10_pct.ge(100) | p.dev30_pct.ge(200)
    p["kind"] = np.where(p.crossed, "数值越线", "接近阈值20个百分点")
    p["window"] = np.where(p.dev10_pct.ge(80), 10, 30)
    # A missing previous observation does not create a new threshold entry.
    previous_signal = p.groupby("code").signal.shift(1)
    previous_known = p.groupby("code").dev10_pct.shift(1).notna()
    p["entry"] = p.signal & previous_signal.eq(False) & previous_known & contiguous
    return p


def feature_at(path, t):
    """Only rows <=t determine all features, including the pause counter."""
    h = path[path.date_idx.le(t)].sort_values("date_idx")
    if h.empty or int(h.iloc[-1].date_idx) != t:
        return None
    r = h.iloc[-1]
    if pd.isna(r.close_used):
        return None
    z = h.set_index("date_idx").is_zt
    streak = 0
    for j in range(t, -1, -1):
        v = z.get(j, np.nan)
        if pd.isna(v) or v != 1:
            break
        streak += 1
    gap = 0
    for j in range(t, -1, -1):
        v = z.get(j, np.nan)
        if pd.isna(v) or v != 0:
            break
        gap += 1
    last5 = h.set_index("date_idx").close_used.reindex(range(t - 4, t + 1))
    ma5 = float(last5.mean()) if len(last5) == 5 and last5.notna().all() else np.nan
    count10 = z.reindex(range(t - 9, t + 1))
    boards10 = int(count10.sum()) if count10.notna().all() else None
    segments = []
    old, n = None, 0
    for v in z.reindex(range(max(0, t - 14), t + 1)):
        key = "未知" if pd.isna(v) else ("板" if v else "断")
        if key != old and n:
            segments.append((old, n))
            n = 0
        old, n = key, n + 1
    if n:
        segments.append((old, n))
    shown = segments[-6:]
    sequence = " → ".join(f"{n}连板" if k == "板" else (f"非板{n}日" if i == 0 and k == '断' else f"{k}{n}日") for i, (k, n) in enumerate(shown))
    return {"streak": streak, "gap": gap, "boards10": boards10, "sequence": sequence,
            "ma5": ma5, "close": float(r.close_used), "raw_close": float(r.close_raw),
            "dev10": float(r.dev10_pct), "dev30": float(r.dev30_pct),
            "date": r.date, "date_idx": t, "window": int(r.window), "is_zt": r.is_zt}


def box_features(path, start, days):
    """A box exists only after exactly k observed calendar sessions; no outcomes."""
    t = start + days - 1
    h = path[path.date_idx.le(t)].set_index("date_idx")
    sub = h.reindex(range(start, t + 1))
    columns = ["close_used", "is_zt", "index_close"]
    if days < 2 or sub[columns].isna().any().any():
        return {"eligible": False, "reason": "区间不足或有缺失"}
    if not sub.is_zt.eq(0).all():
        return {"eligible": False, "reason": "区间含涨停，属于加速/再板"}
    ma5s = h.close_used.reindex(range(t - 4, t + 1))
    if ma5s.isna().any():
        return {"eligible": False, "reason": "MA5不足"}
    c = sub.close_used
    spread = float((c.max() / c.min() - 1) * 100)
    dd = float((c / c.cummax() - 1).min() * 100)
    ma5 = float(ma5s.mean())
    return {"eligible": spread <= 15 and dd > -10 and c.iloc[-1] >= ma5,
            "reason": "按已知收盘区间判定", "days": days, "landmark_idx": t,
            "range_pct": spread, "drawdown_pct": dd, "ma5": ma5,
            "box_low": float(c.min()), "box_high": float(c.max()), "close": float(c.iloc[-1])}


def outcome_at(path, t, horizon):
    h = path.set_index("date_idx")
    c = h.close_used.reindex(range(t, t + horizon + 1))
    if c.isna().any() or not c.gt(0).all():
        return {"return": None, "end_date": None, "newhigh": None, "breakdown": None, "reboard": None}
    end_date = h.loc[t + horizon, "date"]
    reference = h.close_used.reindex(range(t - 20, t + 1))
    ref = reference.max()
    z = h.is_zt.reindex(range(t + 1, t + horizon + 1))
    reboard = None if z.isna().any() else bool((z.eq(1) & z.shift(1).eq(1)).any())
    return {"return": float((c.iloc[-1] / c.iloc[0] - 1) * 100), "end_date": end_date,
            "newhigh": None if reference.isna().any() else bool(c.iloc[1:].max() > ref),
            "breakdown": bool((c.iloc[1:] / c.iloc[0] - 1).min() <= -.15),
            "max_return": float((c.iloc[1:] / c.iloc[0] - 1).max() * 100),
            "drawdown": float((c / c.cummax() - 1).min() * 100), "reboard": reboard}


def build_events_and_landmarks(p):
    events, landmarks = [], []
    paths = {c: x.sort_values("date_idx").copy() for c, x in p.groupby("code", sort=False)}
    for code, h in paths.items():
        for _, e in h[h.entry].iterrows():
            t = int(e.date_idx)
            f = feature_at(h, t)
            o5, o10 = outcome_at(h, t, 5), outcome_at(h, t, 10)
            observed = h[h.date_idx.gt(t) & h.date_idx.le(t + 10)]
            events.append({"episode_id": f"{code}:{e.date:%Y-%m-%d}", "code": code, "name": e['name'], "board": e.board,
                           "event_date": e.date, "event_idx": t, "kind": e.kind, "window": int(e.window),
                           "dev10": e.dev10_pct, "dev30": e.dev30_pct, "streak": f['streak'], "boards10": f['boards10'],
                           "sequence": f['sequence'], "observed_days": len(observed),
                           "observed_return": float((observed.iloc[-1].close_used / e.close_used - 1) * 100) if len(observed) else None,
                           "return5": o5['return'], "return10": o10['return'], "end5": o5['end_date'], "end10": o10['end_date'],
                           "newhigh10": o10['newhigh'], "reboard10": o10['reboard'], "breakdown10": o10['breakdown']})
            # First observable non-limit day in the next ten sessions anchors this
            # episode's pause. No later successful pause is cherry-picked.
            start = first_pause_start(h, t)
            if start is None:
                continue
            for k in (2, 3, 5):
                b = box_features(h, start, k)
                if not b['eligible']:
                    continue
                lt = start + k - 1
                lr = h.loc[h.date_idx.eq(lt)].iloc[0]
                a5, a10 = outcome_at(h, lt, 5), outcome_at(h, lt, 10)
                landmarks.append({"episode_id": f"{code}:{e.date:%Y-%m-%d}", "code": code, "name": e['name'], "board": e.board,
                                  "window": int(e.window), "days": k, "pause_start_idx": start, "pause_start": h.loc[h.date_idx.eq(start), 'date'].iloc[0],
                                  "landmark_date": lr.date, "landmark_idx": lt,
                                  "dev10": lr.dev10_pct, "dev30": lr.dev30_pct, **b,
                                  "return5": a5['return'], "return10": a10['return'], "end5": a5['end_date'], "end10": a10['end_date'],
                                  "newhigh10": a10['newhigh'], "reboard10": a10['reboard'], "breakdown10": a10['breakdown']})
    return pd.DataFrame(events), pd.DataFrame(landmarks), paths


def first_pause_start(path, event_idx, asof_idx=None):
    cap = min(event_idx + 10, asof_idx) if asof_idx is not None else event_idx + 10
    h = path[path.date_idx.between(event_idx, cap)].sort_values('date_idx')
    expected = event_idx
    for _, row in h.iterrows():
        if row.date_idx != expected or pd.isna(row.is_zt):
            return None
        if row.is_zt == 0:
            return int(row.date_idx)
        expected += 1
    return None


def build_crossing_events(paths):
    """A separate clock at first numeric crossing in each warning episode.

    This is an overlapping secondary cohort, not extra independent warning events.
    No announcement confirmation or regulatory reset is inferred from it.
    """
    result = []
    for code, h in paths.items():
        for _, e in h[h.entry].iterrows():
            after = h[h.date_idx.ge(e.date_idx)].sort_values('date_idx')
            expected, cross = int(e.date_idx), None
            for _, r in after.iterrows():
                if int(r.date_idx) != expected or pd.isna(r.dev10_pct) or not r.signal:
                    break
                if bool(r.crossed):
                    cross = r
                    break
                expected += 1
            if cross is None:
                continue
            t = int(cross.date_idx)
            f = feature_at(h, t)
            a5, a10 = outcome_at(h, t, 5), outcome_at(h, t, 10)
            result.append({'episode_id': f'{code}:{e.date:%Y-%m-%d}', 'code': code, 'name': cross['name'], 'board': cross.board,
                           'event_date': cross.date, 'kind': '首次越线后（数值重建）', 'dev10': cross.dev10_pct, 'dev30': cross.dev30_pct,
                           'streak': f['streak'], 'sequence': f['sequence'], 'return5': a5['return'], 'return10': a10['return'],
                           'end5': a5['end_date'], 'end10': a10['end_date'], 'newhigh10': a10['newhigh'],
                           'reboard10': a10['reboard'], 'breakdown10': a10['breakdown']})
    return pd.DataFrame(result)


def group_stats(frame, keys):
    result = []
    if frame.empty:
        return result
    for key, g in frame.groupby(keys, dropna=False, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        r = dict(zip(keys, key))
        r.update(n=len(g), stocks=g.code.nunique(), n5=int(g.return5.notna().sum()), n10=int(g.return10.notna().sum()),
                 median5=g.return5.median(), median10=g.return10.median(),
                 p25_10=g.return10.quantile(.25), p75_10=g.return10.quantile(.75),
                 win10_success=int(g.return10.gt(0).sum()), win10_n=int(g.return10.notna().sum()))
        for field in ("reboard", "newhigh", "breakdown"):
            s = g[f'{field}10'].dropna()
            r[f'{field}_success'], r[f'{field}_n'] = int(s.eq(True).sum()), len(s)
        result.append(clean_json(r))
    return result


def choose_analogs(landmarks, current, as_of):
    """Eligibility and distance depend only on information visible at the landmark."""
    if landmarks.empty or current.get('box_days') not in (2, 3, 5) or not current.get('box_eligible') or not current.get('analog_comparable', True):
        return pd.DataFrame()
    a = landmarks[(landmarks.board == current['board']) & (landmarks.days == current['box_days']) &
                  (landmarks.window == current['window']) & (landmarks.code != current['code']) &
                  landmarks.end10.notna() & (landmarks.end10 <= pd.Timestamp(as_of))].copy()
    if a.empty:
        return a
    d = 'dev10' if current['window'] == 10 else 'dev30'
    a['distance'] = (a[d] - current[d]).abs()
    a = a[a.distance.le(25) & a.range_pct.sub(current['range_pct']).abs().le(7.5)]
    # At most one observation per historical stock: do not count its repeated
    # episodes as independent votes for a current security.
    return a.sort_values(['distance', 'landmark_date']).drop_duplicates('code').head(12)


def make_current(paths, events, landmarks, alerts, cutoff, targets):
    alert_rows = {}
    for a in alerts.get('alerts', []):
        code = normalize(a.get('code', ''))
        if board_of(code) is None:
            continue
        if code not in alert_rows or a.get('state_mark') == 0:
            alert_rows[code] = a
    target_map = {c['code']: c for c in targets}
    latest = {c: h for c, h in paths.items() if str(h.iloc[-1].date)[:10] == cutoff}
    screen = [c for c, h in latest.items() if bool(h.iloc[-1].signal)]
    codes = list(dict.fromkeys(list(target_map) + list(alert_rows) + screen))
    current, matches = [], []
    for code in codes:
        if code not in latest:
            current.append({"code": code, "name": alert_rows.get(code, {}).get('name', code), "board": board_of(code),
                            "action": "资料不足", "missing": "缺少同截止日收盘价；未生成观察条件"})
            continue
        h = latest[code]
        e = h.iloc[-1]
        f = feature_at(h, int(e.date_idx))
        if f is None:
            current.append({'code': code, 'name': e['name'], 'board': e.board, 'action': '资料不足',
                            'missing': '当日调整后收盘价缺失；不混入原始价生成信号'})
            continue
        a = alert_rows.get(code, {})
        latest_entry = events[(events.code == code) & (events.event_idx <= e.date_idx)].tail(1)
        known_after_signal = not latest_entry.empty and e.date_idx - latest_entry.iloc[0].event_idx <= 20
        raw_start = int(e.date_idx) - f['gap'] + 1
        start = max(raw_start, int(latest_entry.iloc[0].event_idx)) if known_after_signal else raw_start
        k = int(e.date_idx) - start + 1
        first_pause = first_pause_start(h, int(latest_entry.iloc[0].event_idx), int(e.date_idx)) if known_after_signal else None
        comparable = start == first_pause
        box = box_features(h, start, k) if known_after_signal and 2 <= k <= 5 else {'eligible': False}
        # Count only real consolidation days. A non-limit trend is not a box.
        state = "横盘已形成，等待突破" if box['eligible'] else ("连续涨停/加速中" if f['streak'] else "分歧整理，尚未满足横盘条件")
        r = {"code": code, "name": e['name'], "board": e.board, "price_as_of": cutoff,
             "alert_as_of": alerts.get('date'), "alert_deviation": a.get('deviation_pct'),
             "regulation_status": target_map.get(code, {}).get('regulation_status', '公告状态未核验'),
             **f, "window": int(latest_entry.iloc[0].window) if known_after_signal else int(e.window),
             "observation_state": state, "box_days": k if box['eligible'] else 0, "nonlimit_days": k,
             "box_eligible": box['eligible'], "range_pct": box.get('range_pct'),
             "analog_comparable": comparable,
             "box_low": box.get('box_low'), "box_high": box.get('box_high'),
             "trigger_text": a.get('trigger_text', '无当前接口预警'), "trigger_price": a.get('trigger_price'),
             "analog_n": 0, "median5": None, "median10": None, "p25_10": None, "p75_10": None,
             "missing": "同题材同步强度未核验；盘中提醒与收盘价时点不同"}
        if not comparable:
            r['missing'] += '；当前不是预警后首轮整理，不套用首轮整理样本'
        analogs = choose_analogs(landmarks, r, cutoff)
        r['analog_n'] = len(analogs)
        if len(analogs) >= 5:
            r.update(median5=analogs.return5.median(), median10=analogs.return10.median(),
                     p25_10=analogs.return10.quantile(.25), p75_10=analogs.return10.quantile(.75))
        for _, match in analogs.iterrows():
            matches.append({'current_code': code, **match.to_dict()})
        if box['eligible']:
            r['action'] = "观察突破；历史区间不足5股时不显示预测"
            r['buy_condition'] = f"收盘突破整理上沿{box['box_high']:.2f}，次日回踩不失守；成交量和同题材强度另核"
            r['sell_condition'] = f"收盘跌破整理下沿{box['box_low']:.2f}，横盘假设失效；持仓转退出评估"
        else:
            r['action'] = "暂不据异动值发买入信号"
            r['buy_condition'] = "先确认整理区间或连续再板；见案例卡的转强条件"
            r['sell_condition'] = "跌破最近已确认支撑且收盘未收复，转退出评估"
        current.append(clean_json(r))
    return current, matches
