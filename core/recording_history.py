"""Lightweight JSONL history of closed recording parts."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Any, Optional

from core.config import APP_DIR

HISTORY_FILE = os.path.join(APP_DIR, "recording_history.jsonl")
_LOCK = threading.Lock()
DEFAULT_LIMIT = 100


def history_path() -> str:
    return HISTORY_FILE


def append_recording(entry: dict[str, Any]) -> dict[str, Any]:
    """Append one history row. Missing fields are filled with safe defaults."""
    record = {
        "time": entry.get("time") or datetime.now().astimezone().isoformat(timespec="seconds"),
        "ts": float(entry.get("ts") or time.time()),
        "room_id": str(entry.get("room_id") or ""),
        "uname": str(entry.get("uname") or ""),
        "title": str(entry.get("title") or ""),
        "path": str(entry.get("path") or ""),
        "source_path": str(entry.get("source_path") or ""),
        "size": int(entry.get("size") or 0),
        "duration": float(entry.get("duration") or 0.0),
        "health": str(entry.get("health") or ""),
        "status": str(entry.get("status") or ""),
        "close_reason": str(entry.get("close_reason") or ""),
        "session_id": str(entry.get("session_id") or ""),
    }
    line = json.dumps(record, ensure_ascii=False)
    with _LOCK:
        os.makedirs(os.path.dirname(HISTORY_FILE) or ".", exist_ok=True)
        with open(HISTORY_FILE, "a", encoding="utf-8") as fp:
            fp.write(line + "\n")
    return record


def read_recent(limit: int = DEFAULT_LIMIT, room_id: Optional[str] = None) -> list[dict]:
    """Read newest-first history rows, optionally filtered by room_id."""
    limit = max(1, int(limit))
    path = HISTORY_FILE
    if not os.path.isfile(path):
        return []
    with _LOCK:
        with open(path, "r", encoding="utf-8", errors="replace") as fp:
            lines = fp.readlines()
    rows: list[dict] = []
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        if room_id is not None and str(item.get("room_id") or "") != str(room_id):
            continue
        rows.append(item)
        if len(rows) >= limit:
            break
    return rows


def group_by_date(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group newest-first rows into (YYYY-MM-DD, items) preserving order."""
    groups: list[tuple[str, list[dict]]] = []
    index: dict[str, list[dict]] = {}
    for row in rows:
        day = str(row.get("time") or "")[:10] or "unknown"
        if day not in index:
            bucket: list[dict] = []
            index[day] = bucket
            groups.append((day, bucket))
        index[day].append(row)
    return groups
