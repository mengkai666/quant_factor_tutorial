"""Resolve current stock names across caches without using industry as truth."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from .models import normalize_code


@dataclass(frozen=True)
class NameResolution:
    names: dict[str, str]
    sources: dict[str, str]
    conflicts: list[dict[str, object]]


_SOURCE_PRIORITY = ("industry", "classified", "universe", "limit_pool")


def _column(frame: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    return next((name for name in candidates if name in frame.columns), None)


def _rows(frame, source: str):
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    code_col = _column(frame, ("code", "代码", "证券代码", "raw_code"))
    name_col = _column(frame, ("name", "名称", "证券简称"))
    if not code_col or not name_col:
        return []

    selected = frame
    date_col = _column(selected, ("date", "日期"))
    if source == "classified" and date_col:
        selected = frame.copy()
        selected["_name_date"] = selected[date_col].astype(str).str.replace("-", "", regex=False)
        selected = selected.sort_values("_name_date").drop_duplicates(code_col, keep="last")

    # ⚠️ 用两列的 tolist() 而不是 iterrows(): 后者每行都要造一个 Series,
    #    实测 7.5 万行 × 24 次调用占整轮 9.5s; 取值与顺序完全一致。
    result = []
    for raw_code, raw_name in zip(selected[code_col].tolist(), selected[name_col].tolist()):
        try:
            code = normalize_code(raw_code)
        except ValueError:
            continue
        name = str(raw_name).strip() if pd.notna(raw_name) else ""
        if name:
            result.append((code, name, source))
    return result


def resolve_names(*, universe=None, classified=None, latest_limit=None,
                  industry=None) -> NameResolution:
    """Return names using current data first and preserve source conflicts."""
    candidates: dict[str, list[tuple[str, str]]] = {}
    frames = (
        (industry, "industry"),
        (universe, "universe"),
        (classified, "classified"),
        (latest_limit, "limit_pool"),
    )
    for frame, source in frames:
        for code, name, origin in _rows(frame, source):
            candidates.setdefault(code, []).append((name, origin))

    names = {}
    sources = {}
    conflicts = []
    for code, values in candidates.items():
        unique_names = []
        origins = []
        for name, source in values:
            if name not in unique_names:
                unique_names.append(name)
            if source not in origins:
                origins.append(source)
        if len(unique_names) > 1:
            conflicts.append({"code": code, "names": unique_names, "sources": origins})
        for source in reversed(_SOURCE_PRIORITY):
            matches = [name for name, origin in values if origin == source]
            if matches:
                names[code] = matches[-1]
                sources[code] = source
                break

    conflicts.sort(key=lambda item: str(item["code"]))
    return NameResolution(names=names, sources=sources, conflicts=conflicts)
