"""감사 로그 (SQLite). 원문 개인정보는 저장하지 않고 마스킹된 글만 남긴다.

각 행은 앞 행의 해시를 물고 있어, 중간 행을 고치거나 지우면 verify()에서 드러난다.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    request_id TEXT NOT NULL,
    client TEXT NOT NULL,
    model TEXT NOT NULL,
    decision TEXT NOT NULL,
    score REAL NOT NULL,
    detail TEXT NOT NULL,
    masked_prompt TEXT NOT NULL,
    masked_response TEXT NOT NULL,
    guard_ms REAL NOT NULL,
    upstream_ms REAL NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
)
"""
_FIELDS = ("ts", "request_id", "client", "model", "decision", "score", "detail",
           "masked_prompt", "masked_response", "guard_ms", "upstream_ms")
GENESIS = "0" * 64


def _digest(prev_hash: str, row: dict[str, Any]) -> str:
    body = json.dumps([row[f] for f in _FIELDS], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute(_SCHEMA)
        self._db.commit()
        self._lock = threading.Lock()

    def write(self, *, request_id: str, client: str, model: str, decision: str, score: float,
              detail: dict[str, Any], masked_prompt: str, masked_response: str,
              guard_ms: float, upstream_ms: float) -> None:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "request_id": request_id, "client": client, "model": model,
            "decision": decision, "score": score,
            "detail": json.dumps(detail, ensure_ascii=False),
            "masked_prompt": masked_prompt, "masked_response": masked_response,
            "guard_ms": guard_ms, "upstream_ms": upstream_ms,
        }
        with self._lock:
            last = self._db.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            row["prev_hash"] = last["hash"] if last else GENESIS
            row["hash"] = _digest(row["prev_hash"], row)
            cols = ", ".join(row)
            self._db.execute(f"INSERT INTO audit ({cols}) VALUES ({', '.join('?' * len(row))})",
                             tuple(row.values()))
            self._db.commit()

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    def stats(self) -> dict[str, Any]:
        by_decision = {r["decision"]: r["n"] for r in self._db.execute(
            "SELECT decision, COUNT(*) AS n FROM audit GROUP BY decision")}
        avg = self._db.execute("SELECT AVG(guard_ms) AS g FROM audit").fetchone()["g"]
        return {
            "total": sum(by_decision.values()),
            "allow": by_decision.get("allow", 0),
            "flag": by_decision.get("flag", 0),
            "block": by_decision.get("block", 0),
            "avg_guard_ms": round(avg, 2) if avg is not None else None,
        }

    def verify(self) -> dict[str, Any]:
        prev = GENESIS
        count = 0
        for r in self._db.execute("SELECT * FROM audit ORDER BY id"):
            row = dict(r)
            if row["prev_hash"] != prev or row["hash"] != _digest(prev, row):
                return {"ok": False, "checked": count, "broken_at_id": row["id"]}
            prev = row["hash"]
            count += 1
        return {"ok": True, "checked": count, "broken_at_id": None}

    def close(self) -> None:
        self._db.close()
