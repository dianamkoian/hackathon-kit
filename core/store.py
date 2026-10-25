"""Хранилище проверок: SQLite из стандартной библиотеки.

Почему SQLite, а не PostgreSQL. Ставить ничего не надо, работает на Windows и
на Mac одинаково, файл можно показать жюри. PostgreSQL на восьмичасовом
хакатоне — это час на поднятие и ещё один на то, чтобы он завёлся у пятерых.

Почему вообще нужна база, а не «для галочки». Две причины, обе продуктовые:

1. «Вы не первый, кому это прислали» — если такое же сообщение уже проверяли,
   это сильный сигнал для человека и сильный момент для демо.
2. Корпус реальных сообщений растёт сам. Каждая проверка — настоящее
   сообщение, включая те, что введёт жюри на защите. Размеченных примеров нам
   не хватает, и это единственный источник, который пополняется без нас.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(os.getenv("STORE_PATH", "data/checks.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL    NOT NULL,
    text_hash   TEXT    NOT NULL,
    text        TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    assessment  TEXT,
    markers     TEXT    NOT NULL DEFAULT '[]',
    confidence  REAL,
    backend     TEXT,
    cost_usd    REAL,
    latency_ms  REAL
);
CREATE INDEX IF NOT EXISTS checks_by_hash ON checks (text_hash);
CREATE INDEX IF NOT EXISTS checks_by_time ON checks (created_at);
"""


def text_hash(text: str) -> str:
    """Хэш нормализованного текста: пробелы и регистр не должны мешать совпадению."""
    normalized = " ".join(text.split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


@dataclass
class Stats:
    total: int
    abstained: int
    by_assessment: dict[str, int]
    repeated: int          # сколько сообщений присылали больше одного раза
    avg_cost_usd: float

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "abstained": self.abstained,
            "by_assessment": self.by_assessment,
            "repeated": self.repeated,
            "avg_cost_usd": round(self.avg_cost_usd, 6),
        }


class Store:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: uvicorn обслуживает запросы в разных потоках,
        # а запись у нас короткая и под замком самого SQLite.
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def seen_count(self, text: str) -> int:
        """Сколько раз это же сообщение проверяли раньше."""
        row = self._db.execute(
            "SELECT COUNT(*) AS n FROM checks WHERE text_hash = ?", (text_hash(text),)
        ).fetchone()
        return int(row["n"])

    def save(self, *, text: str, status: str, assessment: str | None,
             markers: list | None, confidence: float | None,
             backend: str | None, cost_usd: float | None,
             latency_ms: float | None) -> int:
        cur = self._db.execute(
            "INSERT INTO checks (created_at, text_hash, text, status, assessment,"
            " markers, confidence, backend, cost_usd, latency_ms)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (time.time(), text_hash(text), text, status, assessment,
             json.dumps(markers or [], ensure_ascii=False), confidence,
             backend, cost_usd, latency_ms),
        )
        self._db.commit()
        return int(cur.lastrowid)

    def stats(self) -> Stats:
        total = self._db.execute("SELECT COUNT(*) AS n FROM checks").fetchone()["n"]
        abstained = self._db.execute(
            "SELECT COUNT(*) AS n FROM checks WHERE status = 'abstained'"
        ).fetchone()["n"]
        by_assessment = {
            row["assessment"]: row["n"] for row in self._db.execute(
                "SELECT assessment, COUNT(*) AS n FROM checks"
                " WHERE assessment IS NOT NULL GROUP BY assessment")
        }
        repeated = self._db.execute(
            "SELECT COUNT(*) AS n FROM (SELECT text_hash FROM checks"
            " GROUP BY text_hash HAVING COUNT(*) > 1)"
        ).fetchone()["n"]
        avg = self._db.execute(
            "SELECT AVG(cost_usd) AS c FROM checks WHERE cost_usd IS NOT NULL"
        ).fetchone()["c"]
        return Stats(int(total), int(abstained), by_assessment,
                     int(repeated), float(avg or 0.0))

    def export_jsonl(self, path: Path | str, *, only_new: bool = True) -> int:
        """Выгружает проверки в формат набора примеров — без разметки.

        Это и есть пополнение корпуса: сообщения настоящие, разметку добавляет
        человек. Поле `source` ставится в "real-collected", чтобы в отчёте они
        не смешивались ни с синтетикой, ни с присланными вручную.
        """
        rows = self._db.execute(
            "SELECT text, MIN(created_at) AS t, COUNT(*) AS seen FROM checks"
            " GROUP BY text_hash ORDER BY t"
        ).fetchall()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with open(path, "w", encoding="utf-8") as fh:
            for i, row in enumerate(rows, 1):
                fh.write(json.dumps({
                    "id": f"c-{i:03d}",
                    "source": "real-collected",
                    "seen_times": row["seen"],
                    "text": row["text"],
                }, ensure_ascii=False) + "\n")
                written += 1
        return written
