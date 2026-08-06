# -*- coding: utf-8 -*-
"""报告审计 JSON 原子落盘。"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def write_report_audit(path: str | Path, *, report_date: str, context: dict[str, Any], lineage: dict[str, Any], generated_at: str | None = None) -> dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = {"schema_version": "report-audit/v1", "report_date": report_date, "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"), "context": context, "lineage": lineage}
    body["fingerprint"] = hashlib.sha256(_canonical({"report_date": report_date, "context": context, "lineage": lineage}).encode("utf-8")).hexdigest()
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(body, handle, ensure_ascii=False, indent=2, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)
    return body
