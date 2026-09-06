"""One provenance-preserving candidate funnel for scenarios, views and exports."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from typing import Any

from report_logic import filter_tradeable_pool, normalize_stock_code


def _height(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else (1 if "首板" in str(value) else 0)


def matches_mainline(sector: Any, mainline: Any) -> bool:
    mainline = str(mainline or "").strip()
    if not mainline or mainline in {"未知", "方向待确认", "题材待确认", "/"}:
        return False
    tokens = re.split(r"[,，、;；|/]+", str(sector or ""))
    return mainline in {re.sub(r"\d+(?:\.\d+)?%$", "", token).strip() for token in tokens}


def _extract_candidates(ctx: dict) -> list[dict[str, Any]]:
    """将结构化梯队与负反馈统一成可追溯的操作候选。"""
    candidates: dict[str, dict[str, Any]] = {}
    malformed: list[dict[str, Any]] = []
    priority = {'observation': 0, 'attack': 1, 'confirm': 2, 'risk': 3}
    groups = [row for row in list(ctx.get('echelon') or []) if isinstance(row, dict)]
    heights = [_height(row.get('height')) for row in groups]
    max_height = max(heights, default=0)

    def keep(row: dict[str, Any]) -> None:
        code = normalize_stock_code(row.get('code'))
        name = str(row.get('name') or '').strip()
        role = str(row.get('role') or '')
        if not code or not name or role not in priority:
            malformed.append(dict(row))
            return
        existing = candidates.get(code)
        if existing is None or priority[role] > priority[str(existing.get('role') or '')]:
            candidates[code] = {**row, 'code': code, 'name': name}

    for group in groups:
        group_height = _height(group.get('height'))
        group_sector = str(group.get('primary') or group.get('mainline') or '').strip()
        for stock in list(group.get('stock_details') or []):
            if not isinstance(stock, dict):
                continue
            height = _height(stock.get('height')) or group_height
            if height == max_height and max_height >= 6:
                role = 'risk'
            elif height == 2:
                role = 'attack'
            elif 3 <= height <= 5:
                role = 'confirm'
            else:
                role = 'observation'
            keep({
                **stock,
                'name': stock.get('name'),
                'code': stock.get('code'),
                'height': height,
                'sector': str(
                    stock.get('ml') or stock.get('primary') or stock.get('sub')
                    or '题材待确认'
                ).strip(),
                'role': role,
                'group_theme_observation': group_sector,
            })

    progression = ctx.get('progression_chain')
    progression_rows = progression.get('rows') if isinstance(progression, dict) else []
    negative_statuses = {'broken_negative', 'limit_down'}
    for stock in list(progression_rows or []):
        if not isinstance(stock, dict) or str(stock.get('status') or '') not in negative_statuses:
            continue
        keep({
            'name': stock.get('name'),
            'code': stock.get('code'),
            'height': _height(stock.get('previous_height') or stock.get('current_height')),
            'sector': str(stock.get('sector') or stock.get('mainline') or '高位风险').strip(),
            'role': 'risk',
        })

    return sorted(
        [*candidates.values(), *malformed],
        key=lambda row: (-priority.get(row['role'], 0), -int(row.get('height') or 0), row['name']),
    )


def build_candidate_funnel(*, echelon=None, progression_chain=None, mainline=None,
                           security_master=None, report_date=None, rows=None) -> dict:
    """Keep every observation; a rejection is not a missing data point.

    Eligibility is structural, never a trading permission. Publication, scenario
    and real-time gates must still pass. Sorting is explicit, not a win-rate.
    """
    source_rows = list(rows) if rows is not None else _extract_candidates({
        "echelon": echelon, "progression_chain": progression_chain,
    })
    observations, rejected, eligible, risks = [], [], [], []
    reasons = Counter()
    for source in source_rows:
        if not isinstance(source, dict):
            continue
        row = dict(source)
        checked = filter_tradeable_pool([row], security_master=security_master) if str(row.get("name") or "").strip() else []
        if not checked:
            rejected.append({**row, "rejection_reason": "not_tradeable"})
            reasons["not_tradeable"] += 1
            continue
        row = {**row, **checked[0]}
        sector = row.get("sector") or row.get("ml") or row.get("mainline") or "题材待确认"
        row["sector"] = sector
        match = matches_mainline(sector, mainline)
        reason = ("risk_anchor" if row.get("role") == "risk" else
                  "mainline_unknown" if not mainline or mainline == "方向待确认" else
                  "attribution_missing" if sector == "题材待确认" else
                  "off_mainline" if not match else
                  "strategy_role" if row.get("role") not in {"attack", "confirm"} else "")
        row.update(mainline_match=match, candidate_eligible=not reason,
                   rejection_reason=reason, candidate_source="candidate_funnel/v1")
        observations.append(row)
        if reason:
            rejected.append(row)
            reasons[reason] += 1
        else:
            eligible.append(row)
        if row.get("role") == "risk":
            risks.append(row)
    def order(row):
        return (not row["mainline_match"], {"attack": 0, "confirm": 1, "risk": 2}.get(row.get("role"), 3),
                -_height(row.get("height")), str(row.get("code")))
    observations.sort(key=order)
    eligible.sort(key=order)
    risks.sort(key=lambda r: (-_height(r.get("height")), r["code"]))
    payload = {
        "schema_version": "candidate-funnel/v1", "report_date": str(report_date or ""),
        "mainline": str(mainline or "方向待确认"), "input_count": len(source_rows),
        "observations": observations, "eligible_candidates": eligible,
        "risk_anchors": risks, "rejected": rejected, "rejected_counts": dict(reasons),
        "ranking_basis": "同主线优先；沿用二板/三至五板角色，按高度和代码稳定排序；非收益预测",
    }
    payload["fingerprint"] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:20]
    return payload
