"""SQLite persistence for cases, audit log and a VASP-contact cache.
(PostgreSQL in production; SQLite keeps the prototype zero-setup.)"""
from __future__ import annotations

import json
import sqlite3
import threading
import time

from .config import settings

_lock = threading.Lock()
_conn = sqlite3.connect(settings.db_path, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript("""
CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, created_at INTEGER, status TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER,
                                  case_id TEXT, actor TEXT, action TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS vasp_cache (entity TEXT PRIMARY KEY, ts INTEGER, data TEXT);
""")


def save_case(case: dict) -> None:
    with _lock:
        _conn.execute("INSERT OR REPLACE INTO cases VALUES (?,?,?,?)",
                      (case["id"], case["created_at"], case["status"], json.dumps(case)))
        _conn.commit()


def get_case(case_id: str) -> dict | None:
    row = _conn.execute("SELECT data FROM cases WHERE id=?", (case_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def list_cases(limit: int = 50) -> list[dict]:
    rows = _conn.execute("SELECT data FROM cases ORDER BY created_at DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        c = json.loads(r["data"])
        out.append({k: c.get(k) for k in ("id", "created_at", "status", "chain", "address",
                                          "complaint_ref", "demo")}
                   | {"risk": (c.get("risk") or {}).get("level"),
                      "nearest_vasp": ((c.get("vasps") or [{}])[0]).get("name")})
    return out


def audit(case_id: str | None, action: str, detail: str = "", actor: str = "system") -> None:
    with _lock:
        _conn.execute("INSERT INTO audit (ts, case_id, actor, action, detail) VALUES (?,?,?,?,?)",
                      (int(time.time()), case_id, actor, action, detail))
        _conn.commit()


def audit_log(case_id: str) -> list[dict]:
    rows = _conn.execute("SELECT ts, actor, action, detail FROM audit WHERE case_id=? ORDER BY id",
                         (case_id,))
    return [dict(r) for r in rows]


def vasp_cache_get(entity: str, max_age_s: int = 7 * 86400) -> dict | None:
    row = _conn.execute("SELECT ts, data FROM vasp_cache WHERE entity=?", (entity,)).fetchone()
    if row and time.time() - row["ts"] < max_age_s:
        return json.loads(row["data"])
    return None


def vasp_cache_put(entity: str, data: dict) -> None:
    with _lock:
        _conn.execute("INSERT OR REPLACE INTO vasp_cache VALUES (?,?,?)",
                      (entity, int(time.time()), json.dumps(data)))
        _conn.commit()
