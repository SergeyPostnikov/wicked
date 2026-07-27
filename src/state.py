"""StateStore — локальное состояние в SQLite (specs.yaml components.state_store).

Хранит реестр объектов с чексуммами; сравнение прогонов даёт инкрементальность
(US-007): изменённые объекты → PENDING, неизменные → SKIPPED, исчезнувшие
из источников удаляются из реестра.
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.scanner import CodeObject

log = logging.getLogger("autodocs.state")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    id         TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    name       TEXT NOT NULL,
    type       TEXT NOT NULL,
    path       TEXT NOT NULL,
    checksum   TEXT NOT NULL,
    status     TEXT NOT NULL,   -- машина состояний из specs.yaml state_machine
    last_error TEXT,
    updated_at TEXT NOT NULL
);
"""


@dataclass
class ScanDiff:
    changed: list[CodeObject]    # новые или с изменившейся чексуммой → PENDING
    unchanged: list[CodeObject]  # чексумма совпала → SKIPPED, LLM не нужна
    removed: list[str]           # исчезли из источников — удалены из реестра


class StateStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def apply_scan(self, objects: list[CodeObject]) -> ScanDiff:
        """Сравнивает результат скана с реестром и обновляет его."""
        now = datetime.now(UTC).isoformat()
        known = dict(self._conn.execute("SELECT id, checksum FROM objects"))
        seen_ids = {o.id for o in objects}

        diff = ScanDiff(changed=[], unchanged=[], removed=[])
        for o in objects:
            if known.get(o.id) == o.checksum:
                diff.unchanged.append(o)
                continue
            diff.changed.append(o)
            self._conn.execute(
                """INSERT INTO objects (id, source, name, type, path, checksum, status, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)
                   ON CONFLICT(id) DO UPDATE SET
                     checksum=excluded.checksum, path=excluded.path,
                     status='PENDING', last_error=NULL, updated_at=excluded.updated_at""",
                (o.id, o.source, o.name, o.type, o.path, o.checksum, now),
            )

        diff.removed = [oid for oid in known if oid not in seen_ids]
        if diff.removed:
            self._conn.executemany("DELETE FROM objects WHERE id = ?",
                                   [(oid,) for oid in diff.removed])

        self._conn.commit()
        log.info("Скан применён: changed=%d unchanged=%d removed=%d",
                 len(diff.changed), len(diff.unchanged), len(diff.removed))
        return diff

    def set_status(self, object_id: str, status: str, error: str | None = None) -> None:
        self._conn.execute(
            "UPDATE objects SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (status, error, datetime.now(UTC).isoformat(), object_id),
        )
        self._conn.commit()

    def counts_by_status(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) FROM objects GROUP BY status")
        return dict(rows)
