"""Журнал вопросов в SQLite: что спросили, нашлось ли в базе, сколько занял ответ (как в уроке PEcf09).

Обезличен: вместо посетителя — HMAC идентификатора диалога с солью, текст вопроса и ответа — после вычистки
контактов (privacy.redact). Записи старше retention_days удаляются: таймер faq-assistant-cleanup раз в сутки
и сама служба не чаще раза в час, когда приходят вопросы.
"""
from __future__ import annotations

import sqlite3
import statistics
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    session TEXT NOT NULL,
    status TEXT NOT NULL,          -- answered | no_context | rejected | error
    reason TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL DEFAULT '',
    answer TEXT NOT NULL DEFAULT '',
    sources TEXT NOT NULL DEFAULT '',
    top_score REAL,
    history_turns INTEGER NOT NULL DEFAULT 0,
    retrieval_ms INTEGER,
    llm_ms INTEGER,
    total_ms INTEGER,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL DEFAULT '',
    kb_version TEXT NOT NULL DEFAULT '',
    search_query TEXT NOT NULL DEFAULT '',  -- переписанное уточнение, по которому шёл поиск
    rewrite_ms INTEGER                     -- сколько заняло переписывание уточнения
);
CREATE INDEX IF NOT EXISTS ix_interactions_ts ON interactions(ts);
"""
# Столбцы, добавленные после первой выкладки: в старый файл журнала они дописываются при запуске.
ADDED_COLUMNS = {"search_query": "TEXT NOT NULL DEFAULT ''", "rewrite_ms": "INTEGER"}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class Record:
    session: str
    status: str
    reason: str = ""
    query: str = ""
    answer: str = ""
    sources: str = ""
    top_score: float | None = None
    history_turns: int = 0
    retrieval_ms: int | None = None
    llm_ms: int | None = None
    total_ms: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    kb_version: str = ""
    search_query: str = ""
    rewrite_ms: int | None = None
    ts: str = field(default_factory=utcnow)


class Journal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(SCHEMA)
            have = {row[1] for row in con.execute("PRAGMA table_info(interactions)")}
            for name, ddl in ADDED_COLUMNS.items():
                if name not in have:
                    con.execute(f"ALTER TABLE interactions ADD COLUMN {name} {ddl}")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def write(self, record: Record) -> None:
        row = asdict(record)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._lock, self._connect() as con:
            con.execute(f"INSERT INTO interactions ({cols}) VALUES ({marks})", list(row.values()))

    def cleanup(self, days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock, self._connect() as con:
            return con.execute("DELETE FROM interactions WHERE ts < ?", (cutoff,)).rowcount

    def stats(self) -> dict[str, Any]:
        with self._lock, self._connect() as con:
            rows = con.execute("SELECT status, total_ms, history_turns FROM interactions").fetchall()
        by_status: dict[str, int] = {}
        for status, _, _ in rows:
            by_status[status] = by_status.get(status, 0) + 1
        times = [ms for status, ms, _ in rows if ms is not None and status in ("answered", "no_context")]
        return {
            "total": len(rows),
            "by_status": by_status,
            "with_history": sum(1 for _, _, h in rows if h),
            "median_ms": statistics.median(times) if times else None,
        }
